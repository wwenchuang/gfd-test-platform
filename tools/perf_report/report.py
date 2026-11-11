"""Recompute practice load statistics from timestamped JMeter CSV and monitor evidence."""
import argparse
import csv
import hashlib
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


LOAD_STAGES = ("baseline", "medium", "high", "retest")
CHECK_STAGES = ("negative", "smoke")
ASSERTION_NAMES = {"question_count", "http_status", "business_ok", "method_metrics", "invalid_report"}


def read_csv(stream, required):
    reader = csv.DictReader(stream)
    if not required.issubset(reader.fieldnames or ()):
        raise ValueError(f"missing CSV columns: {sorted(required - set(reader.fieldnames or ()))}")
    for row in reader:
        if None in row or any(row[field] is None for field in required):
            raise ValueError(f"incomplete CSV row {reader.line_num}")
        yield row


def read_jtl(stream):
    samples = []
    for row in read_csv(stream, {"timeStamp", "elapsed", "label", "responseCode", "success"}):
        start, elapsed = int(row["timeStamp"]), int(row["elapsed"])
        success = row["success"].lower()
        if (row["label"] != "knowledge-evaluation" or elapsed < 0 or start < 0 or
                success not in ("true", "false") or not row["responseCode"].strip()):
            raise ValueError(f"invalid JTL sample at {start}")
        code = row["responseCode"]
        http_code = code if (len(code) == 3 and code.isascii() and code.isdigit()
                             and 100 <= int(code) <= 599) else None
        if http_code is None:
            error = "transport"
        elif not 200 <= int(code) <= 299:
            error = "http"
        elif success == "false":
            error = "business"
        else:
            error = None
        reason = row.get("failureMessage") or ""
        # Keep private exception or response text out of aggregate evidence.
        reason = reason if reason in ASSERTION_NAMES else ("other" if reason else "")
        threads = int(row["grpThreads"]) if row.get("grpThreads") else None
        samples.append((start, elapsed, error, reason, http_code, threads))
    return samples


def window_stats(samples, start, end):
    if end < start:
        raise ValueError("end of request window precedes start")
    duration = (end - start) / 1000
    latencies = []
    errors = dict(transport=0, http=0, business=0)
    assertion_failures = Counter()
    response_codes = Counter()
    timestamps, thread_counts = [], []
    for timestamp, elapsed, error, reason, http_code, threads in samples:
        if start <= timestamp < end:
            latencies.append(elapsed)
            timestamps.append(timestamp)
            if threads is not None:
                thread_counts.append(threads)
            response_codes[http_code or "non_http"] += 1
            if error:
                errors[error] += 1
                if reason:
                    assertion_failures[reason] += 1
    latencies.sort()
    count = len(latencies)
    failures = sum(errors.values())
    http_ok = count - errors["transport"] - errors["http"]

    def percentage(n):
        return round(100 * n / count, 6) if count else None

    def percentile(q):
        return latencies[math.ceil(count * q) - 1] if count else None

    return dict(start_ms=start, end_ms=end, duration_s=duration, samples=count,
                http_successes=http_ok, business_successes=count - failures, errors=failures,
                http_success_rate_pct=percentage(http_ok), business_success_rate_pct=percentage(count - failures),
                error_rate_pct=percentage(failures), error_counts=errors, assertion_failures=dict(sorted(assertion_failures.items())),
                response_codes=dict(sorted(response_codes.items())), max_threads=max(thread_counts, default=None),
                first_sample_start_ms=min(timestamps, default=None), last_sample_start_ms=max(timestamps, default=None),
                p50_ms=percentile(.50), p95_ms=percentile(.95), p99_ms=percentile(.99),
                throughput_rps=round(count / duration, 6) if duration else None)


def read_monitor(stream):
    samples = []
    for row in read_csv(stream, {"timeStamp", "source", "metric", "unit", "value"}):
        value = float(row["value"])
        if row["source"] not in ("generator", "server", "database") or not math.isfinite(value):
            raise ValueError("invalid monitor metric")
        samples.append((int(row["timeStamp"]), row["source"], row["metric"], row["unit"], value))
    return samples


def monitor_stats(samples, start, end):
    groups = defaultdict(list)
    for timestamp, source, metric, unit, value in samples:
        if start <= timestamp < end:
            groups[source, metric, unit].append(value)
    return [dict(source=source, metric=metric, unit=unit, samples=len(values), min=min(values),
                 max=max(values), mean=round(sum(values) / len(values), 6))
            for (source, metric, unit), values in sorted(groups.items())]


def summarize_stage(directory, metadata):
    start, end = int(metadata["stage_start_ms"]), int(metadata["stage_end_ms"])
    recovery_start = int(metadata["recovery_start_ms"])
    recovery_end = int(metadata["recovery_end_ms"])
    if not (start <= end <= recovery_start <= recovery_end):
        raise ValueError(f"invalid stage or recovery window for {directory.name}")
    with (directory / "requests.jtl").open(encoding="utf-8-sig", newline="") as stream:
        samples = read_jtl(stream)
    if "samples" in metadata and len(samples) != metadata["samples"]:
        raise ValueError(f"JTL sample count differs from metadata for {directory.name}")
    with (directory / "monitor.csv").open(encoding="utf-8-sig", newline="") as stream:
        monitor_rows = read_monitor(stream)
    observed = window_stats(samples, start, end)
    hashes = {name: hashlib.sha256((directory / name).read_bytes()).hexdigest()
              for name in ("requests.jtl", "monitor.csv", "metadata.json")}
    result = dict(id=directory.name, concurrency=int(metadata["threads"]), status=metadata["status"],
                  stage_start_ms=start, stage_end_ms=end, think_ms=metadata["think_ms"],
                  sha256=hashes, jtl_samples=len(samples), outside_stage_samples=len(samples) - observed["samples"],
                  recovery_start_ms=recovery_start, recovery_end_ms=recovery_end)
    monitors = {"recovery": monitor_stats(monitor_rows, recovery_start, recovery_end)}
    if directory.name in CHECK_STAGES:
        if directory.name == "negative":
            expected = (observed["response_codes"] == {"200": 1} and
                        observed["error_counts"]["business"] == 1 and observed["assertion_failures"] == {"question_count": 1})
        else:
            expected = observed["samples"] == 2 and observed["errors"] == 0
        monitors["stage"] = monitor_stats(monitor_rows, start, end)
        result.update(observed=observed, expected_outcome_observed=expected and result["outside_stage_samples"] == 0)
    else:
        planned_start = int(metadata["steady_start_ms"])
        planned_end = int(metadata["steady_end_ms"])
        if not start <= planned_start < planned_end:
            raise ValueError(f"invalid steady measurement window for {directory.name}")
        warm_end = min(planned_start, end)
        actual_end = min(planned_end, end)
        warm = window_stats(samples, start, warm_end)
        steady = window_stats(samples, warm_end, actual_end)
        last_start = steady["last_sample_start_ms"]
        covers_tail = last_start is not None and last_start >= planned_end - 5000
        monitors.update(warm=monitor_stats(monitor_rows, start, warm_end),
                        steady=monitor_stats(monitor_rows, warm_end, actual_end))
        result.update(warm=warm, steady=steady, planned_steady_start_ms=planned_start,
                      planned_steady_end_ms=planned_end, after_steady_samples=observed["samples"] - warm["samples"] - steady["samples"],
                      complete_steady_window=(end >= planned_end and covers_tail and metadata["status"] != "aborted"))
    result["monitor"] = monitors
    return result


def baseline_comparison(baseline, retest):
    compared = dict(baseline=baseline["id"], retest=retest["id"], comparable=False)
    if not all(r["complete_steady_window"] and r["steady"]["samples"] > 0 for r in (baseline, retest)):
        return dict(compared, reason="incomplete_or_empty_steady_window")
    if (baseline["concurrency"] != retest["concurrency"] or baseline["think_ms"] != retest["think_ms"]
            or baseline["warm"]["duration_s"] != retest["warm"]["duration_s"]
            or baseline["steady"]["duration_s"] != retest["steady"]["duration_s"]):
        return dict(compared, reason="configuration_differs")
    old, new = baseline["steady"], retest["steady"]
    compared.update(comparable=True, p95_delta_ms=new["p95_ms"] - old["p95_ms"],
                    throughput_delta_rps=round(new["throughput_rps"] - old["throughput_rps"], 6),
                    error_rate_delta_pp=round(new["error_rate_pct"] - old["error_rate_pct"], 6))
    return compared


def build_report(directory):
    stages, pending, missing = {}, [], []
    for name in (*CHECK_STAGES, *LOAD_STAGES):
        stage_dir = directory / name
        if not stage_dir.is_dir():
            missing.append(name)
            continue
        metadata = json.loads((stage_dir / "metadata.json").read_text(encoding="utf-8"))
        if metadata.get("status") == "running" or metadata.get("recovery_end_ms") is None:
            pending.append(name)
            continue
        stages[name] = summarize_stage(stage_dir, metadata)
    if not stages and not pending:
        raise ValueError("no stage evidence directories found")
    comparisons = []
    if "baseline" in stages and "retest" in stages:
        comparisons.append(baseline_comparison(stages["baseline"], stages["retest"]))
    return dict(schema_version=1, checks=[stages[s] for s in CHECK_STAGES if s in stages],
                runs=[stages[s] for s in LOAD_STAGES if s in stages], comparisons=comparisons,
                pending_stages=pending, missing_stages=missing)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_directory", type=Path)
    parser.add_argument("--output", type=Path, help="write JSON to this file (default: stdout)")
    args = parser.parse_args()
    try:
        report = build_report(args.run_directory)
    except (OSError, KeyError, TypeError, ValueError) as exc:
        parser.exit(1, f"Could not recompute evidence: {exc}\n")
    output = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(output, encoding="utf-8")
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
