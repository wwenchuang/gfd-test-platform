"""Run the frozen Week 15 report-read workload on one isolated loopback listener."""
import argparse
import csv
import json
import math
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
STAGES = (("negative", 1, 1), ("smoke", 1, 2), ("baseline", 1, None),
          ("medium", 5, None), ("high", 10, None), ("retest", 1, None))
WARMUP, STEADY, RECOVERY = 60, 180, 20
INTERVAL = 2


def now_ms():
    return time.time_ns() // 1_000_000


def write_meta(folder, data):
    temp = folder / "metadata.json.tmp"
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    temp.replace(folder / "metadata.json")


def process_snapshot():
    # Never request full argv or environment. Capture cumulative CPU to calculate
    # interval CPU percentage (100% = one logical core) independently of ps %CPU.
    fields = subprocess.check_output(["ps", "-A", "-o", "pid=,ppid=,time=,rss=,comm="], text=True)
    result = {}
    for line in fields.splitlines():
        values = line.split(None, 4)
        if len(values) != 5:
            continue
        pid, parent, duration, rss, command = values
        days, _, clock = duration.rpartition("-")
        seconds = 0.0
        for part in clock.split(":"):
            seconds = seconds * 60 + float(part)
        result[int(pid)] = (int(parent), seconds + int(days or 0) * 86400, int(rss), command)
    return result


def java_process(processes, root):
    if root is None:
        return None
    for pid, (_, _, _, name) in processes.items():
        if Path(name).name not in ("java", "java.exe"):
            continue
        ancestor = pid
        while ancestor in processes and ancestor > 1:
            if ancestor == root:
                return pid
            ancestor = processes[ancestor][0]
    return None


def monitor(writer, previous, target, generator, db):
    stamp, monotonic = now_ms(), time.monotonic()
    processes = process_snapshot()
    jvm = java_process(processes, generator)
    for who, pid in (("server", target), ("generator", jvm)):
        if pid not in processes:
            continue
        _, cpu, rss, _ = processes[pid]
        writer.writerow([stamp, who, "rss_kib", "KiB", rss])
        if pid in previous:
            last_clock, last_cpu = previous[pid]
            percent = max(0.0, 100 * (cpu - last_cpu) / (monotonic - last_clock))
            writer.writerow([stamp, who, "cpu_percent", "%", round(percent, 2)])
        previous[pid] = (monotonic, cpu)
    if db:
        writer.writerow([stamp, "database", "db_bytes", "bytes", db.stat().st_size])
        wal = Path(str(db) + "-wal")
        try:
            size = wal.stat().st_size
        except FileNotFoundError:
            size = 0
        writer.writerow([stamp, "database", "wal_bytes", "bytes", size])
    return processes.get(target)


def samples(path):
    if not path.exists():
        return []
    with path.open(newline="") as source:
        # A JTL append can be in progress. A partial final row will be reread.
        return [row for row in csv.DictReader(source)
                if row.get("success") in {"true", "false"} and row.get("elapsed", "").isdigit()
                and None not in row.values()]


def gate(rows):
    if len(rows) >= 3 and all(r["success"] == "false" for r in rows[-3:]):
        return "three_consecutive_errors"
    recent = rows[-30:]
    if len(recent) >= 20:
        if sum(r["success"] == "false" for r in recent) / len(recent) >= 0.05:
            return "rolling_error_rate_ge_5pct"
        latency = sorted(int(r["elapsed"]) for r in recent)
        if latency[math.ceil(0.95 * len(latency)) - 1] > 2000:
            return "rolling_p95_gt_2000ms"
    return None


def stop_group(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGTERM)
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        except ProcessLookupError:
            process.wait()


def run_stage(args, name, users, count):
    folder = args.output / name
    folder.mkdir()
    rows_path = folder / "requests.jtl"
    marker = folder / "stage_start_ms"
    budget = WARMUP + STEADY if count is None else 30
    args_jmeter = [str(args.jmeter), "-n", "-t", str(HERE / "knowledge_evaluation.jmx"),
                   "-q", str(HERE / "knowledge_jtl.properties"), "-l", str(rows_path),
                   "-j", str(folder / "jmeter.log"), f"-Jport={args.port}",
                   f"-Jusers={users}", f"-Jbudget_ms={budget * 1000}",
                   f"-Jsample_limit={count or 100000}", f"-Jtoken_file={args.token_file}",
                   f"-Jmarker={marker}", f"-Jexpected_questions={31 if name == 'negative' else 30}"]
    meta = {"stage": name, "threads": users, "think_ms": 1000, "warmup_seconds": WARMUP if count is None else 0,
            "steady_seconds": STEADY if count is None else 0, "stage_start_ms": None, "stage_end_ms": None,
            "steady_start_ms": None, "steady_end_ms": None, "recovery_start_ms": None, "recovery_end_ms": None,
            "target_pid": args.target_pid, "db_monitor": "SQLite file sizes only" if args.sqlite_path else "unavailable",
            "status": "running", "abort_reason": None}
    write_meta(folder, meta)
    previous = {}
    with (folder / "monitor.csv").open("w", newline="") as output, (folder / "jmeter-cli.log").open("w") as log:
        writer = csv.writer(output)
        writer.writerow(["timeStamp", "source", "metric", "unit", "value"])
        with subprocess.Popen(args_jmeter, stdout=log, stderr=subprocess.STDOUT, start_new_session=True) as process:
            deadline = time.monotonic() + budget + 45
            try:
                while process.poll() is None:
                    target = monitor(writer, previous, args.target_pid, process.pid, args.sqlite_path)
                    output.flush()
                    if marker.exists() and meta["stage_start_ms"] is None:
                        value = marker.read_text().strip()
                        if value.isdigit():
                            meta["stage_start_ms"] = int(value)
                            if count is None:
                                meta["steady_start_ms"] = int(value) + WARMUP * 1000
                                meta["steady_end_ms"] = int(value) + budget * 1000
                            write_meta(folder, meta)
                    reason = None
                    if not target:
                        reason = "target_process_missing"
                    elif target[2] > 1_572_864:
                        reason = "target_rss_gt_1536MiB"
                    elif time.monotonic() > deadline:
                        reason = "stage_timeout"
                    elif name != "negative":
                        reason = gate(samples(rows_path))
                    if reason:
                        meta["abort_reason"] = reason
                        break
                    time.sleep(INTERVAL)
            except KeyboardInterrupt:
                meta["abort_reason"] = "interrupted"
            finally:
                stop_group(process)
                meta["stage_end_ms"] = now_ms()
                meta["exit_code"] = process.returncode
                meta["status"] = "aborted" if meta["abort_reason"] else "failed"
                write_meta(folder, meta)
        rows = samples(rows_path)
        if marker.exists() and meta["stage_start_ms"] is None:
            meta["stage_start_ms"] = int(marker.read_text())
        if not meta["abort_reason"] and process.returncode == 0 and meta["stage_start_ms"]:
            if name == "negative":
                passed = (len(rows) == 1 and rows[0]["responseCode"] == "200"
                          and rows[0]["success"] == "false" and rows[0]["failureMessage"] == "question_count")
            elif count:
                passed = len(rows) == count and all(r["success"] == "true" for r in rows)
            else:
                end = meta["steady_end_ms"]
                # Demand actual traffic across the intended interval, including its
                # final five seconds, before qualifying this level for the next one.
                passed = (end is not None and all(r["success"] == "true" for r in rows)
                          and any(end - 5000 <= int(r["timeStamp"]) < end for r in rows)
                          and max((int(r["grpThreads"]) for r in rows), default=0) == users)
            meta["status"] = "passed" if passed else "failed"
        meta["samples"] = len(rows)
        meta["recovery_start_ms"] = now_ms()
        write_meta(folder, meta)
        if count is None:
            recovery_end = time.monotonic() + RECOVERY
            while time.monotonic() < recovery_end:
                monitor(writer, previous, args.target_pid, None, args.sqlite_path)
                output.flush()
                time.sleep(min(INTERVAL, max(0, recovery_end - time.monotonic())))
        meta["recovery_end_ms"] = now_ms()
        write_meta(folder, meta)
    print(f"{name}: {meta['status']}, {len(rows)} samples, {meta['abort_reason'] or '-'}", flush=True)
    return meta["status"] == "passed"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jmeter", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--target-pid", type=int, required=True)
    parser.add_argument("--token-file", type=Path, required=True)
    parser.add_argument("--sqlite-path", type=Path)
    parser.add_argument("--output", type=Path, required=True, help="new, private run directory")
    args = parser.parse_args()
    os.umask(0o077)
    args.jmeter = args.jmeter.absolute()
    args.output = args.output.absolute()
    args.token_file = args.token_file.absolute()
    if not (1 <= args.port <= 65535 and args.target_pid > 1 and args.jmeter.is_file()
            and os.access(args.jmeter, os.X_OK)):
        parser.error("provide the JMeter executable and isolated listener PID/port")
    attrs = args.token_file.lstat()
    if (not stat.S_ISREG(attrs.st_mode) or attrs.st_uid != os.getuid()
            or attrs.st_mode & 0o077 or not (1 <= attrs.st_size <= 8192)):
        parser.error("token-file must be a private single-user regular file containing one raw token")
    token = args.token_file.read_text().strip()
    if not token or any(char.isspace() for char in token):
        parser.error("token-file must contain one raw token")
    if args.sqlite_path and not args.sqlite_path.is_file():
        parser.error("SQLite database file does not exist")
    sockets = subprocess.check_output(["lsof", "-nP", "-a", "-p", str(args.target_pid),
                                       "-iTCP:" + str(args.port), "-sTCP:LISTEN", "-Fn"], text=True)
    if f"n127.0.0.1:{args.port}" not in sockets.splitlines():
        parser.error("target PID must listen on the requested IPv4 loopback port")
    args.output.mkdir(parents=True, exist_ok=False)
    for stage in STAGES:
        if not run_stage(args, *stage):
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
