"""Monitor the workbench's fixed systemd units, exposing only bounded counts."""
import argparse
import fcntl
import json
import os
import re
import subprocess
import sqlite3
from contextlib import closing
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError

UNITS = ("midscene-task.service", "midscene-api-worker.service",
         "midscene-api-thinking-worker.service", "midscene-api-scheduler.service")
WINDOW = 300
LIMIT = 500
ACTIONS = {
    "http_5xx": "按时间窗检查工作台 HTTP 5xx 请求及其上游服务",
    "traceback": "按时间窗检查异常堆栈与失败调用",
    "error": "按时间窗检查服务错误与同次任务状态",
}
HTTP_ERROR = re.compile(r'HTTP/\d(?:\.\d+)?["\s]+5\d\d\b')
ERROR = re.compile(r'(?:^|[\s\[])(?:ERROR|CRITICAL|FATAL)(?:$|[:/\s\]])')


COVERAGE_HINTS = {
    "no_visible_history": "确认本机已安装该服务、曾向 journal 写入日志，并检查服务账号读权限",
    "journalctl_missing": "请在已安装 systemd 的工作台服务器运行",
    "journal_access": "检查巡检账号所属的 systemd-journal 组和 journal 读取权限",
    "journal_timeout": "检查服务器 journal 查询延迟",
    "journal_query_failed": "在本机以巡检服务身份复查 journal 命令是否可用",
    "journal_invalid": "检查本机 journal JSON 输出是否可读",
    "entry_limit": "本窗记录超过 500 行，请按时间窗复查更早日志",
    "unreadable_fields": "部分日志字段过大或格式无法读取，请按时间窗复查",
}


class JournalUnavailable(Exception):
    pass


def _records(unit, args, command):
    run = command(["journalctl", "--no-pager", "--unit", unit, "--output=json", *args],
                  capture_output=True, text=True, errors="replace", timeout=15,
                  env={**os.environ, "LC_ALL": "C", "SYSTEMD_COLORS": "0"})
    if run.returncode or run.stderr.strip():
        denied = any(word in run.stderr.lower() for word in ("permission", "not seeing", "access denied"))
        raise JournalUnavailable("journal_access" if denied else "journal_query_failed")
    if run.stdout.strip() == "-- No entries --":
        return []
    return [json.loads(line) for line in run.stdout.splitlines() if line.strip()]


def observe(unit, start, end, command=subprocess.run):
    if unit not in UNITS or end - start != WINDOW:
        raise ValueError("invalid unit or window")
    try:
        # Check visibility separately so no matching rows never proves a missing unit healthy.
        if not _records(unit, ["--lines=1", "--output-fields=PRIORITY"], command):
            raise JournalUnavailable("no_visible_history")
        rows = _records(unit, [f"--since=@{start}", f"--until=@{end - 1}.999999",
                               f"--lines={LIMIT + 1}", "--output-fields=PRIORITY,MESSAGE"], command)
    except (OSError, ValueError, JournalUnavailable, subprocess.SubprocessError) as exc:
        reason = "journal_query_failed"
        if isinstance(exc, JournalUnavailable):
            reason = str(exc)
        elif isinstance(exc, FileNotFoundError):
            reason = "journalctl_missing"
        elif isinstance(exc, PermissionError):
            reason = "journal_access"
        elif isinstance(exc, subprocess.TimeoutExpired):
            reason = "journal_timeout"
        elif isinstance(exc, ValueError):
            reason = "journal_invalid"
        return {"coverage": "source_unavailable", "reason": reason, "matches": {}}
    counts = {}
    reason = "entry_limit" if len(rows) > LIMIT else None
    for row in rows[-LIMIT:]:
        if not isinstance(row, dict):
            reason = reason or "unreadable_fields"
            continue
        message = row.get("MESSAGE")
        # journalctl's default JSON representation omits large fields and encodes
        # binary fields as arrays. Neither proves that the entry was clean.
        if not isinstance(message, str):
            reason = reason or "unreadable_fields"
            message = ""
        if HTTP_ERROR.search(message):
            kind = "http_5xx"
        elif "Traceback (most recent call last):" in message:
            kind = "traceback"
        elif str(row.get("PRIORITY")) in ("0", "1", "2", "3") or ERROR.search(message):
            kind = "error"
        else:
            continue
        counts[kind] = counts.get(kind, 0) + 1
    return {"coverage": "partial" if reason else "complete", "reason": reason, "matches": counts}


def notify(message):
    if os.environ.get("MIDSCENE_LOG_MONITOR_NOTIFY") != "1":
        return "disabled"
    # Import only for enabled delivery, so a read-only preview needs only stdlib.
    from task_server.services.feishu_service import resolve_task_app_feishu_channel, send_feishu_notification
    channel, webhook = resolve_task_app_feishu_channel(None)
    if not channel["ready"]:
        return "channel_unavailable"
    try:
        receipt = send_feishu_notification({"text": message}, webhook=webhook)
    except HTTPError as exc:
        return "rejected" if exc.code in (400, 401, 403, 404, 413, 422, 429) else "unknown"
    except Exception:
        return "unknown"
    code = receipt.get("code", receipt.get("StatusCode")) if isinstance(receipt, dict) else None
    if type(code) is not int:
        return "unknown"
    return "sent" if code == 0 else "rejected"


def _message(unit, start, end, observation, changes):
    counts = observation["matches"]
    detail = "、".join(f"{name} {count} 行" for name, count in counts.items())
    if not detail:
        detail = "未命中错误行" if observation["coverage"] == "complete" else "日志覆盖不足，无法确认本窗错误数"
    hints = [ACTIONS[name] for name in counts]
    if observation["coverage"] != "complete":
        hints.append(COVERAGE_HINTS.get(observation.get("reason"), "检查本窗 journal 采集结果"))
    hints.append(f"journalctl -u {unit} --since '@{start}' --until '@{end - 1}.999999'")
    utc_start = datetime.fromtimestamp(start, timezone.utc).strftime("%Y-%m-%d %H:%M")
    utc_end = datetime.fromtimestamp(end, timezone.utc).strftime("%H:%M")
    return "\n".join([f"测试工作台服务日志｜{unit}", "；".join(changes),
                      f"UTC {utc_start} 至 {utc_end}，{detail}", *hints])


def _write(path, state):
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf8") as out:
        json.dump(state, out, ensure_ascii=False)
        out.flush()
        os.fsync(out.fileno())
    os.replace(out.name, path)


def _remember(root, unit, current):
    end = current["window_end"]
    counts = current["matches"]
    with closing(sqlite3.connect(root / "history.sqlite3", timeout=3)) as db, db:
        db.execute("""CREATE TABLE IF NOT EXISTS windows (
            unit TEXT NOT NULL, window_end INTEGER NOT NULL, coverage TEXT NOT NULL,
            http_5xx INTEGER NOT NULL, traceback INTEGER NOT NULL, error INTEGER NOT NULL,
            PRIMARY KEY(unit, window_end))""")
        db.execute("INSERT OR IGNORE INTO windows VALUES (?, ?, ?, ?, ?, ?)",
                   (unit, end, current["coverage"], *(counts.get(key, 0) for key in ACTIONS)))
        db.execute("DELETE FROM windows WHERE window_end <= ?", (end - 48 * 3600,))


def _advance(unit, previous, observation, end):
    start = end - WINDOW
    changes = []
    active = previous.get("error_active", False)
    if observation["matches"] and not active:
        active = True
        changes.append("发现服务错误")
    elif active and not observation["matches"] and observation["coverage"] == "complete":
        active = False
        changes.append("日志告警恢复，本窗口未再命中错误")
    coverage = observation["coverage"]
    if coverage != previous.get("coverage", "complete"):
        changes.append({"complete": "日志采集恢复", "partial": "日志覆盖不完整",
                        "source_unavailable": "日志源不可用"}[coverage])
    gap = max(0, start - previous.get("window_end", start))
    if gap:
        changes.append(f"监控中断 {gap} 秒，期间日志未检查")
    message = _message(unit, start, end, observation, changes) if changes else previous.get("last_alert")
    return {**observation, "window_end": end, "error_active": active, "gap_seconds": gap,
            "last_alert": message, "delivery": "pending" if changes else previous.get("delivery")}


def scan(units, root, end, *, dry_run=False, observe_unit=observe, deliver=notify):
    start = end - WINDOW
    if dry_run:
        return {unit: {"window_end": end, **observe_unit(unit, start, end)} for unit in units}
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = root / "state.json"
    with (root / "scan.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text()) if path.exists() else {}
        for unit in units:
            prior = state.get(unit, {})
            if prior.get("window_end", 0) >= end:
                continue
            observation = observe_unit(unit, start, end)
            current = _advance(unit, prior, observation, end)
            _remember(root, unit, current)
            state[unit] = current
            _write(path, state)
            if current["last_alert"] and current["delivery"] in (
                    "pending", "rejected", "channel_unavailable", "disabled"):
                # A crash or timeout after the send starts has an unknown outcome.
                # Saving this before sending prevents repeating an unconfirmed delivery.
                current["delivery"] = "unknown"
                _write(path, state)
                current["delivery"] = deliver(current["last_alert"])
                _write(path, state)
        return {unit: state[unit] for unit in units}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--run", action="store_true")
    parser.add_argument("--unit", choices=UNITS, action="append")
    args = parser.parse_args()
    units = args.unit or UNITS
    root = Path(os.getenv("STATE_DIRECTORY", "/var/lib/midscene-log-monitor"))
    end = int(time.time()) // WINDOW * WINDOW
    status = scan(units, root, end, dry_run=args.dry_run)
    print(json.dumps({"scope": "workbench_systemd", "sources": status}, ensure_ascii=False))
    return 2 if any(s["coverage"] != "complete" for s in status.values()) else 0


if __name__ == "__main__":
    sys.exit(main())
