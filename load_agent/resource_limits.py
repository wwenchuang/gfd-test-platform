"""Container-aware resource limits reported by the load Agent."""

import os
from pathlib import Path


def _read(path):
    return Path(path).read_text(encoding="utf-8").strip()


def cpu_limit_cores(*, read_text=None, host_cpu_count=None):
    """Return the effective CPU quota, capped by the visible host CPU count."""
    reader = read_text or _read
    cpu_count = host_cpu_count or os.cpu_count
    host_cores = max(1.0, float(cpu_count() or 1))
    quota_cores = None

    try:
        quota, period = reader("/sys/fs/cgroup/cpu.max").split()[:2]
        if quota != "max" and float(quota) > 0 and float(period) > 0:
            quota_cores = float(quota) / float(period)
    except (OSError, ValueError, IndexError):
        pass

    if quota_cores is None:
        try:
            quota = float(reader("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"))
            period = float(reader("/sys/fs/cgroup/cpu/cpu.cfs_period_us"))
            if quota > 0 and period > 0:
                quota_cores = quota / period
        except (OSError, ValueError):
            pass

    effective = min(host_cores, quota_cores) if quota_cores is not None else host_cores
    rounded = round(max(0.01, effective), 3)
    return int(rounded) if rounded.is_integer() else rounded
