"""Lightweight process memory diagnostics without third-party dependencies."""

import os
import resource
import sys
import threading
import time

_MONITOR_LOCK = threading.Lock()
_MONITOR_STARTED = False


def _current_rss_bytes():
    try:
        with open("/proc/self/status", encoding="ascii") as status_file:
            for line in status_file:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def _peak_rss_bytes():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(peak if sys.platform == "darwin" else peak * 1024)


def process_runtime_metrics():
    rss = _current_rss_bytes()
    peak = max(rss, _peak_rss_bytes())
    return {
        "pid": os.getpid(),
        "rss_mb": round(rss / 1024 / 1024, 1),
        "peak_rss_mb": round(peak / 1024 / 1024, 1),
        "threads": threading.active_count(),
    }


def _memory_monitor_loop(warn_mb, interval_seconds):
    while True:
        time.sleep(interval_seconds)
        metrics = process_runtime_metrics()
        if metrics["rss_mb"] >= warn_mb:
            print(
                "运行内存告警："
                f"RSS={metrics['rss_mb']}MB，峰值={metrics['peak_rss_mb']}MB，"
                f"线程={metrics['threads']}，告警线={warn_mb}MB",
                flush=True,
            )


def start_runtime_memory_monitor():
    global _MONITOR_STARTED
    with _MONITOR_LOCK:
        if _MONITOR_STARTED:
            return False
        _MONITOR_STARTED = True
    warn_mb = max(256, int(os.getenv("TASK_MEMORY_WARN_MB", "1536") or 1536))
    interval_seconds = max(15, int(os.getenv("TASK_MEMORY_MONITOR_INTERVAL_SECONDS", "60") or 60))
    threading.Thread(
        target=_memory_monitor_loop,
        args=(warn_mb, interval_seconds),
        name="runtime-memory-monitor",
        daemon=True,
    ).start()
    return True
