"""Public operational summary of the workbench monitor's private state file."""
import json
import math
import sqlite3
from contextlib import closing
import time
from pathlib import Path

STATE_PATH = Path("/var/lib/midscene-log-monitor/state.json")
UNITS = ("midscene-task.service", "midscene-api-worker.service",
         "midscene-api-thinking-worker.service", "midscene-api-scheduler.service")
REASONS = ("no_visible_history", "journalctl_missing", "journal_access", "journal_timeout",
           "journal_query_failed", "journal_invalid", "entry_limit", "unreadable_fields")
DELIVERIES = ("pending", "sent", "unknown", "rejected", "disabled", "channel_unavailable")


def _load():
    try:
        with STATE_PATH.open("rb") as stream:
            data = stream.read(65537)
        if len(data) <= 65536:
            state = json.loads(data)
            if isinstance(state, dict):
                return state
    except (OSError, ValueError, RecursionError):
        pass
    return {}


def read_status():
    now, state = int(time.time()), _load()
    sources = []
    for unit in UNITS:
        record = state.get(unit)
        if not isinstance(record, dict):
            record = {}
        end = record.get("window_end")
        coverage = record.get("coverage")
        if (type(end) is not int or not 0 < end <= now or end % 300
                or coverage not in ("complete", "partial", "source_unavailable")):
            record, end, coverage = {}, None, "unobserved"
        matches = record.get("matches")
        if not isinstance(matches, dict):
            matches = {}
        counts = {name: matches[name] for name in ("http_5xx", "traceback", "error")
                  if type(matches.get(name)) is int and 0 < matches[name] <= 500}
        reason, delivery = record.get("reason"), record.get("delivery")
        active, gap = record.get("error_active"), record.get("gap_seconds")
        sources.append({"unit": unit, "coverage": coverage, "window_end": end,
                        "fresh": end is not None and now - end <= 360,
                        "matches": counts, "reason": reason if reason in REASONS else None,
                        "error_active": active if type(active) is bool else None,
                        "delivery": delivery if delivery in DELIVERIES else None,
                        "gap_seconds": gap if type(gap) is int and 0 <= gap <= now else 0})
    status = "unavailable"
    if any(item["window_end"] for item in sources):
        status = "current" if all(item["fresh"] for item in sources) else "stale"
    return {"ok": True, "scope": "workbench_systemd", "window_seconds": 300, "status": status, "sources": sources}


def require_investigation_access(actor):
    from task_server.access_control import MainAccess
    from .failure_analysis_tasks import _profile
    MainAccess(_profile(actor), catalog=()).check("GET", "/api/platform/log-monitor/status")


def has_monitor_evidence(analysis):
    return any(item.get("kind") == "workbench_log_window" for item in analysis.get("evidence", []))

def overlapping_windows(start, finish):
    """Return up to eight complete windows, prioritizing recorded errors."""
    first = (math.floor(start / 300) + 1) * 300
    last = max(first, math.ceil(finish / 300) * 300)
    expected = ((last - first) // 300 + 1) * len(UNITS)
    now = int(time.time()) // 300 * 300
    lo, hi = max(first, now - 48 * 3600 + 300), min(last, now)
    if lo > hi:
        return [], 0, expected
    rows = {}
    # The latest snapshot also supports existing installations before their first history write.
    for row in read_status()["sources"]:
        end = row["window_end"]
        if end and lo <= end <= hi and row["coverage"] == "complete":
            rows[(row["unit"], end)] = row
    path = STATE_PATH.with_name("history.sqlite3")
    columns = ("http_5xx", "traceback", "error")
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=1)) as db:
            for unit, end, *counts in db.execute("""SELECT unit, window_end, http_5xx, traceback, error
                    FROM windows WHERE unit IN (?,?,?,?) AND coverage='complete'
                    AND window_end BETWEEN ? AND ? AND window_end % 300=0 LIMIT 2304""", (*UNITS, lo, hi)):
                if type(end) is not int or any(type(n) is not int or n < 0 or n > 500 for n in counts):
                    continue
                rows[(unit, end)] = {"unit": unit, "window_end": end,
                                     "matches": {key:n for key, n in zip(columns, counts) if n}}
    except sqlite3.Error:
        pass
    ordered = sorted(rows.values(), key=lambda row: (not bool(row["matches"]), -row["window_end"], row["unit"]))
    return ordered[:8], len(ordered), expected
