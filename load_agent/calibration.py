"""Bounded local-only k6 calibration and validity checks."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import time
import uuid


CALIBRATION_SCRIPT = """import { Counter } from 'k6/metrics';
export const options = { scenarios: { local_capacity: {
  executor: 'ramping-vus', startVUs: 1, stages: __STAGES__
} }, thresholds: {} };
const localIterations = new Counter('local_calibration_iterations');
export default function () {
  let value = 1;
  for (let i = 0; i < 200; i += 1) value = (value * 17 + i) % 104729;
  localIterations.add(1 + (value === -1 ? 1 : 0));
}
"""


def _utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def hardware_signature():
    raw = "|".join(
        [
            platform.system(),
            platform.machine(),
            platform.release(),
            str(os.cpu_count() or 0),
            _memory_limit_marker(),
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()


def _memory_limit_marker():
    for name in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            value = Path(name).read_text(encoding="utf-8").strip()
            if value:
                return value
        except OSError:
            continue
    return "unknown"


class _ProcessSampler:
    def __init__(self, pid):
        self.pid = pid
        self.previous_ticks = None
        self.previous_time = None
        try:
            self.clock_ticks = os.sysconf("SC_CLK_TCK")
        except (ValueError, OSError, AttributeError):
            self.clock_ticks = 100

    def sample(self):
        now = time.monotonic()
        cpu = 0.0
        memory_mb = 0.0
        try:
            text = Path(f"/proc/{self.pid}/stat").read_text(encoding="utf-8")
            fields = text.rsplit(")", 1)[1].split()
            ticks = int(fields[11]) + int(fields[12])
            if self.previous_ticks is not None and now > self.previous_time:
                cpu = (ticks - self.previous_ticks) / self.clock_ticks / (now - self.previous_time) * 100
            self.previous_ticks = ticks
            self.previous_time = now
        except (OSError, ValueError, IndexError):
            pass
        try:
            for row in Path(f"/proc/{self.pid}/status").read_text(encoding="utf-8").splitlines():
                if row.startswith("VmRSS:"):
                    memory_mb = int(row.split()[1]) / 1024
                    break
        except (OSError, ValueError, IndexError):
            pass
        return max(0.0, cpu), max(0.0, memory_mb)


def calibration_state(record, now, agent_version, k6_version, signature):
    if not isinstance(record, dict) or record.get("state") != "valid":
        return "missing" if not record else str(record.get("state") or "invalidated")
    if (
        record.get("agent_version") != agent_version
        or record.get("k6_version") != k6_version
        or record.get("hardware_signature") != signature
    ):
        return "invalidated"
    try:
        valid_until = datetime.fromisoformat(str(record.get("valid_until") or "").replace("Z", "+00:00"))
    except ValueError:
        return "invalidated"
    if valid_until.tzinfo is None:
        return "invalidated"
    return "expired" if valid_until <= _utc(now) else "valid"


class CalibrationRunner:
    def __init__(
        self,
        *,
        k6_binary,
        data_dir,
        popen=None,
        now=None,
        hardware_signature=hardware_signature,
        agent_version,
        k6_version,
        hard_max_vus=500,
        hard_max_iterations_per_second=2000,
        process_sampler=None,
        sleeper=None,
    ):
        self.k6_binary = k6_binary
        self.data_dir = Path(data_dir)
        self.popen = popen or subprocess.Popen
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.hardware_signature = hardware_signature
        self.agent_version = agent_version
        self.k6_version = k6_version
        self.hard_max_vus = max(1, int(hard_max_vus))
        self.hard_max_iterations_per_second = max(1, int(hard_max_iterations_per_second))
        self.process_sampler = process_sampler
        self.sleeper = sleeper or time.sleep

    def run(self):
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.data_dir, 0o700)
        work = Path(tempfile.mkdtemp(prefix="calibration-", dir=self.data_dir))
        os.chmod(work, 0o700)
        script = work / "local-calibration.js"
        quarter = max(1, self.hard_max_vus // 4)
        half = max(1, self.hard_max_vus // 2)
        stages = [
            {"duration": "5s", "target": quarter},
            {"duration": "5s", "target": half},
            {"duration": "10s", "target": self.hard_max_vus},
            {"duration": "2s", "target": 0},
        ]
        script.write_text(
            CALIBRATION_SCRIPT.replace("__STAGES__", json.dumps(stages, separators=(",", ":"))),
            encoding="utf-8",
        )
        os.chmod(script, 0o600)
        summary_path = work / "summary.json"
        started = _utc(self.now())
        try:
            process = self.popen(
                [self.k6_binary, "run", f"--summary-export={summary_path}", str(script)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={"PATH": os.environ.get("PATH", "")},
            )
            sampler = self.process_sampler or _ProcessSampler(getattr(process, "pid", -1)).sample
            cpu_peak = 0.0
            memory_peak = 0.0
            deadline = time.monotonic() + 30
            while hasattr(process, "poll") and process.poll() is None:
                cpu, memory = sampler()
                cpu_peak = max(cpu_peak, float(cpu or 0))
                memory_peak = max(memory_peak, float(memory or 0))
                if time.monotonic() >= deadline:
                    process.kill()
                    raise subprocess.TimeoutExpired([self.k6_binary, "run"], 30)
                self.sleeper(0.2)
            stdout, stderr = process.communicate(timeout=2)
            if process.returncode != 0:
                return {
                    "id": str(uuid.uuid4()),
                    "state": "failed",
                    "calibrated_at": started.isoformat(),
                    "message": (stderr or "k6本地校准失败")[:1000],
                    "agent_version": self.agent_version,
                    "k6_version": self.k6_version,
                    "hardware_signature": self.hardware_signature(),
                }
            if summary_path.is_file():
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
            else:
                summary = json.loads(stdout)
            metrics = summary.get("metrics", {})
            iterations = metrics.get("iterations", {})
            vus_max = metrics.get("vus_max", {})
            # k6 0.52 --summary-export writes values directly on the metric;
            # handleSummary and newer machine-readable summaries nest them in
            # `values`. Accept both because deployed Agents currently use 0.52.
            iteration_values = iterations.get("values", iterations)
            vu_values = vus_max.get("values", vus_max)
            rate = float(iteration_values.get("rate") or 0)
            vus = float(vu_values.get("max") or vu_values.get("value") or 0)
            if rate <= 0 or vus <= 0:
                raise ValueError(
                    "校准结果缺少迭代率或虚拟用户数"
                    f"（iterations.rate={rate:g}，vus_max={vus:g}）"
                )
            return {
                "id": str(uuid.uuid4()),
                "state": "valid",
                "calibrated_at": started.isoformat(),
                "valid_until": (started + timedelta(days=7)).isoformat(),
                "agent_version": self.agent_version,
                "k6_version": self.k6_version,
                "hardware_signature": self.hardware_signature(),
                "max_iterations_per_second": min(
                    self.hard_max_iterations_per_second,
                    max(1, int(rate * 0.8)),
                ),
                "max_vus": min(self.hard_max_vus, max(1, int(vus * 0.8))),
                "cpu_peak_percent": round(cpu_peak, 2),
                "memory_peak_mb": round(memory_peak, 2),
                "safety_factor": 0.8,
            }
        except (OSError, ValueError, json.JSONDecodeError, subprocess.TimeoutExpired) as error:
            return {
                "id": str(uuid.uuid4()),
                "state": "failed",
                "calibrated_at": started.isoformat(),
                "message": str(error)[:1000],
                "agent_version": self.agent_version,
                "k6_version": self.k6_version,
                "hardware_signature": self.hardware_signature(),
            }
        finally:
            try:
                script.write_bytes(b"\x00" * script.stat().st_size)
            except OSError:
                pass
            for item in work.iterdir():
                item.unlink(missing_ok=True)
            work.rmdir()
