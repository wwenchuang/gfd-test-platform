"""Bounded, read-only runtime evidence for the generator, never the target host."""

from collections import deque
from datetime import datetime, timezone
import math
import os
from pathlib import Path
import subprocess
import sys
import time


MAX_RESOURCE_SAMPLES = 1500


def resource_interval(shard):
    workload = ((shard.get("run") or {}).get("configuration") or {}).get("workload") or {}
    duration = _finite(workload.get("duration_seconds")) or sum(
        _finite(stage.get("duration_seconds")) or 0 for stage in workload.get("stages", []) if isinstance(stage, dict)
    )
    # Include a minute for graceful shutdown; retain the whole planned run.
    return max(5, min(3600, math.ceil((duration + 60) / (MAX_RESOURCE_SAMPLES - 1))))


def _finite(value):
    try:
        value = float(value)
        return value if math.isfinite(value) and value >= 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


class RuntimeResourceSampler:
    """cgroup usage includes all members; process fallback measures only k6 RSS/CPU.

    No Docker socket, host mount or privilege is required. Missing/unsupported
    counters remain null. A cgroup is not assumed to be a dedicated container.
    """

    def __init__(self, pid, *, interval_seconds=5, max_samples=MAX_RESOURCE_SAMPLES,
                 read_text=None, host_cpu_count=None, affinity_count=None, process_reader=None):
        self.pid = pid
        self.interval = max(5.0, float(interval_seconds))
        self.rows = deque(maxlen=max(1, min(MAX_RESOURCE_SAMPLES, int(max_samples))))
        self.count = 0
        self.previous = None
        self.last_sample = None
        self.read_text = read_text or (lambda path: Path(path).read_text(encoding="utf-8"))
        self.host_cpu_count = host_cpu_count or os.cpu_count
        self.affinity_count = affinity_count or self._affinity_count
        self.process_reader = process_reader or self._process_usage
        self.groups = self._groups()

    def _read(self, path):
        try:
            return self.read_text(str(path)).strip()
        except (OSError, ValueError):
            return ""

    def _groups(self):
        groups = {}
        for line in self._read(f"/proc/{self.pid}/cgroup").splitlines():
            parts = line.split(":", 2)
            if len(parts) != 3 or ".." in Path(parts[2]).parts:
                continue
            controllers, relative = parts[1], parts[2].lstrip("/")
            if not controllers:
                groups["v2"] = Path("/sys/fs/cgroup") / relative
            else:
                for controller in controllers.split(","):
                    groups[controller] = Path("/sys/fs/cgroup") / controllers / relative
        # Resolve nonstandard mounts and cgroup namespaces using the process's
        # own mount table, rather than accidentally sampling an ancestor host.
        for line in self._read(f"/proc/{self.pid}/mountinfo").splitlines():
            before, separator, after = line.partition(" - ")
            fields, tail = before.split(), after.split()
            if not separator or len(fields) < 5 or len(tail) < 3 or tail[0] not in {"cgroup", "cgroup2"}:
                continue
            root, mount = fields[3], fields[4]
            controllers = ["v2"] if tail[0] == "cgroup2" else tail[2].split(",")
            for controller in controllers:
                if controller not in groups:
                    continue
                for entry in self._read(f"/proc/{self.pid}/cgroup").splitlines():
                    parts = entry.split(":", 2)
                    if len(parts) != 3 or not (controller in parts[1].split(",") or controller == "v2" and not parts[1]):
                        continue
                    try:
                        relative = Path(parts[2]).relative_to(root)
                    except ValueError:
                        continue
                    groups[controller] = Path(mount) / relative
        return groups

    def _affinity_count(self):
        try:
            return len(os.sched_getaffinity(self.pid))
        except (AttributeError, OSError, TypeError):
            return None

    def _process_usage(self):
        if not isinstance(self.pid, int) or self.pid <= 0:
            return None, None
        try:
            if sys.platform.startswith("linux"):
                fields = self._read(f"/proc/{self.pid}/stat").rsplit(")", 1)[1].split()
                return ((int(fields[11]) + int(fields[12])) / os.sysconf("SC_CLK_TCK"),
                        int(fields[21]) * os.sysconf("SC_PAGE_SIZE"))
            if sys.platform == "darwin":
                result = subprocess.run(["ps", "-p", str(self.pid), "-o", "time=", "-o", "rss="],
                                        capture_output=True, text=True, timeout=1, check=True)
                cpu, rss = result.stdout.split()
                parts = cpu.split(":")
                seconds = sum(float(value) * 60 ** index for index, value in enumerate(reversed(parts)))
                return seconds, int(rss) * 1024
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            pass
        return None, None

    @staticmethod
    def _ancestors(path):
        # A mount's inaccessible ancestors are skipped; visible parent quotas
        # still cap a nested cgroup's otherwise unlimited local setting.
        return [path, *[p for p in path.parents if str(p).startswith("/sys/fs/cgroup")]]

    def _cpu_limit(self):
        visible = [_finite(self.host_cpu_count()), _finite(self.affinity_count())]
        visible = [value for value in visible if value is not None and value > 0]
        quotas = []
        group = self.groups.get("v2") or self.groups.get("cpu")
        if group:
            for path in self._ancestors(group):
                if "v2" in self.groups:
                    values = self._read(path / "cpu.max").split()
                else:
                    values = [self._read(path / "cpu.cfs_quota_us"), self._read(path / "cpu.cfs_period_us")]
                if len(values) >= 2:
                    quota, period = _finite(values[0]), _finite(values[1])
                    if quota and period:
                        quotas.append(quota / period)
        limits = visible + quotas
        if not limits:
            return None, "unavailable"
        limit = min(limits)
        return limit, "cgroup_quota" if quotas and min(quotas) <= limit else "visible_cpus"

    def _usage(self):
        cpu, memory = None, None
        cpu_scope, memory_scope = "unavailable", "unavailable"
        group = self.groups.get("v2")
        if group:
            stats = dict(line.split() for line in self._read(group / "cpu.stat").splitlines() if len(line.split()) == 2)
            cpu = _finite(stats.get("usage_usec"))
            cpu = cpu / 1e6 if cpu is not None else None
            memory = _finite(self._read(group / "memory.current"))
            cpu_scope = memory_scope = "cgroup_v2"
        else:
            cpu_group = self.groups.get("cpuacct")
            memory_group = self.groups.get("memory")
            if cpu_group:
                cpu = _finite(self._read(cpu_group / "cpuacct.usage"))
                cpu = cpu / 1e9 if cpu is not None else None
                cpu_scope = "cgroup_v1"
            if memory_group:
                memory = _finite(self._read(memory_group / "memory.usage_in_bytes"))
                memory_scope = "cgroup_v1"
        if cpu is None or memory is None:
            process_cpu, process_memory = self.process_reader()
            if cpu is None:
                cpu = _finite(process_cpu)
                cpu_scope = "k6_process" if cpu is not None else "unavailable"
            if memory is None:
                memory = _finite(process_memory)
                memory_scope = "k6_process" if memory is not None else "unavailable"
        limits = []
        if memory_scope in {"cgroup_v1", "cgroup_v2"}:
            memory_group = group or self.groups.get("memory")
            name = "memory.max" if group else "memory.limit_in_bytes"
            for path in self._ancestors(memory_group):
                value = _finite(self._read(path / name))
                if value and value < 2 ** 60:  # Linux v1 unlimited sentinel
                    limits.append(int(value))
        return cpu, cpu_scope, memory, memory_scope, min(limits) if limits else None

    def sample(self, *, now=None, force=False):
        now = time.monotonic() if now is None else now
        if not force and self.last_sample is not None and now - self.last_sample < self.interval:
            return False
        cpu, cpu_scope, memory, memory_scope, memory_limit = self._usage()
        cpu_limit, limit_source = self._cpu_limit()
        used = None
        if cpu is not None and self.previous is not None:
            previous_time, previous_cpu, previous_scope = self.previous
            if cpu_scope == previous_scope and now > previous_time and cpu >= previous_cpu:
                used = (cpu - previous_cpu) / (now - previous_time)
        self.previous = (now, cpu, cpu_scope) if cpu is not None else None
        self.rows.append({
            "sampled_at": datetime.now(timezone.utc).isoformat(),
            "cpu_scope": cpu_scope, "cpu_used_cores": used, "cpu_limit_cores": cpu_limit,
            "cpu_percent": used / cpu_limit * 100 if used is not None and cpu_limit else None,
            "cpu_limit_source": limit_source,
            "memory_scope": memory_scope, "memory_used_bytes": int(memory) if memory is not None else None,
            "memory_limit_bytes": memory_limit,
            "memory_percent": memory / memory_limit * 100 if memory is not None and memory_limit else None,
        })
        self.last_sample = now
        self.count += 1
        return True

    def snapshot(self, *, tail=None):
        rows = list(self.rows)
        if tail is not None:
            rows = rows[-tail:]
        return {"role": "load_generator", "interval_seconds": self.interval,
                "sample_count": self.count, "dropped_samples": self.count - len(rows),
                "samples": rows}
