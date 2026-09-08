"""Bounded aggregation of incremental k6 JSON point output."""

from datetime import datetime, timezone
from bisect import bisect_left
import math


COUNTERS = {
    "http_reqs": "requests",
    "iterations": "iterations",
    "workflow_iteration_started": "workflow_starts",
    "dropped_iterations": "dropped_iterations",
    "data_sent": "bytes_sent",
    "data_received": "bytes_received",
}
SUPPORTED_METRICS = frozenset(COUNTERS) | {
    "http_req_duration", "http_req_failed", "checks", "workflow_iteration_success", "vus",
}
LATENCY_BOUNDS_MS = (10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000)


def _utc(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _percentile(values, percentile):
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, math.ceil(percentile * len(ordered)) - 1)
    return round(float(ordered[index]), 3)


class _Bucket:
    def __init__(self, started_at, step_id, max_latency_samples, max_samples):
        self.started_at = started_at
        self.step_id = step_id
        self.max_latency_samples = max_latency_samples
        self.max_samples = max_samples
        self.counters = {name: 0.0 for name in COUNTERS.values()}
        self.http_failures = 0
        self.business_assertions = 0
        self.business_failures = 0
        self.workflow_iterations = 0
        self.workflow_failures = 0
        self.latencies = []
        self.latency_count = 0
        self.latency_sum = 0.0
        self.latency_max = 0.0
        self.latency_histogram = [0 for _ in range(len(LATENCY_BOUNDS_MS) + 1)]
        self.samples = []
        self.vu_gauge = None

    def accept(self, metric, value, tags, timestamp):
        if metric == "vus" and self.step_id == "all":
            vus = int(value)
            if self.vu_gauge is None:
                self.vu_gauge = {"count": 1, "min": vus, "max": vus, "sum": vus,
                                 "first_at": timestamp, "last_at": timestamp, "max_gap_seconds": 0.0}
            else:
                gauge = self.vu_gauge
                gauge["count"] += 1
                gauge["min"] = min(gauge["min"], vus)
                gauge["max"] = max(gauge["max"], vus)
                gauge["sum"] += vus
                # Keep a conservative upper bound when points arrive late: a
                # later sample must never erase a previously observed gap.
                gauge["max_gap_seconds"] = max(
                    gauge["max_gap_seconds"], abs((timestamp - gauge["last_at"]).total_seconds())
                )
                gauge["first_at"] = min(gauge["first_at"], timestamp)
                gauge["last_at"] = max(gauge["last_at"], timestamp)
        elif metric in COUNTERS:
            self.counters[COUNTERS[metric]] += float(value)
        elif metric == "http_req_duration":
            latency = float(value)
            self.latency_count += 1
            self.latency_sum += latency
            self.latency_max = max(self.latency_max, latency)
            self.latency_histogram[bisect_left(LATENCY_BOUNDS_MS, latency)] += 1
            if len(self.latencies) < self.max_latency_samples:
                self.latencies.append(latency)
            else:
                # Deterministic bounded reservoir: retain evenly distributed
                # positions without keeping the complete point stream.
                position = self.latency_count % self.max_latency_samples
                self.latencies[position] = latency
        elif metric == "http_req_failed" and float(value) > 0:
            self.http_failures += 1
            self._sample("http_error", tags)
        elif metric == "checks":
            self.business_assertions += 1
            if float(value) <= 0:
                self.business_failures += 1
                self._sample("business_assertion", tags)
        elif metric == "workflow_iteration_success":
            self.workflow_iterations += 1
            if float(value) <= 0:
                self.workflow_failures += 1
                self._sample("workflow_failure", tags)

    def _sample(self, kind, tags):
        if len(self.samples) >= self.max_samples:
            return
        self.samples.append(
            {
                "step_id": self.step_id,
                "kind": kind,
                "payload": {"check": str(tags.get("check") or "")[:300]},
            }
        )

    def view(self, window_seconds):
        metrics = {key: int(value) if value.is_integer() else value for key, value in self.counters.items()}
        metrics.update(
            {
                "http_failures": self.http_failures,
                "business_assertions": self.business_assertions,
                "business_failures": self.business_failures,
                "workflow_iterations": self.workflow_iterations,
                "workflow_failures": self.workflow_failures,
                "latency_ms": {
                    "count": self.latency_count,
                    "p50": _percentile(self.latencies, 0.50),
                    "p90": _percentile(self.latencies, 0.90),
                    "p95": _percentile(self.latencies, 0.95),
                    "p99": _percentile(self.latencies, 0.99),
                    "max": round(max(self.latencies), 3) if self.latencies else 0.0,
                },
                "latency_histogram": {
                    "bounds_ms": list(LATENCY_BOUNDS_MS),
                    "counts": list(self.latency_histogram),
                    "count": self.latency_count,
                    "sum_ms": round(self.latency_sum, 3),
                    "max_ms": round(self.latency_max, 3),
                },
            }
        )
        if self.vu_gauge is not None:
            metrics["vu_gauge"] = {
                **self.vu_gauge,
                "first_at": self.vu_gauge["first_at"].isoformat(),
                "last_at": self.vu_gauge["last_at"].isoformat(),
            }
        return {
            "step_id": self.step_id,
            "started_at": self.started_at.isoformat(),
            "bucket_seconds": window_seconds,
            "metrics": metrics,
            "samples": list(self.samples),
        }


class MetricAggregator:
    def __init__(self, *, window_seconds=5, max_latency_samples=4096, max_samples=20):
        if not 1 <= window_seconds <= 60:
            raise ValueError("指标窗口必须在1到60秒之间")
        if not 1 <= max_latency_samples <= 100_000:
            raise ValueError("延迟样本上限必须在1到100000之间")
        if not 1 <= max_samples <= 100:
            raise ValueError("错误样本上限必须在1到100之间")
        self.window_seconds = window_seconds
        self.max_latency_samples = max_latency_samples
        self.max_samples = max_samples
        self._buckets = {}
        self._high_water_window = None
        self._finalized_before = None

    @property
    def retained_latency_values(self):
        return sum(len(item.latencies) for item in self._buckets.values())

    def accept(self, point):
        if not isinstance(point, dict) or point.get("type") != "Point":
            return ()
        data = point.get("data") if isinstance(point.get("data"), dict) else {}
        try:
            timestamp = _utc(data.get("time"))
            value = float(data.get("value"))
        except (TypeError, ValueError, OverflowError):
            return ()
        if not math.isfinite(value) or isinstance(data.get("value"), bool):
            return ()
        metric = str(point.get("metric") or "")
        if metric not in SUPPORTED_METRICS:
            return ()
        if metric == "vus" and (value < 0 or not value.is_integer()):
            return ()
        tags = data.get("tags") if isinstance(data.get("tags"), dict) else {}
        step_id = str(tags.get("step_id") or "all")[:120]
        if metric == "vus" and step_id != "all":
            return ()
        epoch = int(timestamp.timestamp())
        window_epoch = epoch - epoch % self.window_seconds
        if self._finalized_before is not None and window_epoch < self._finalized_before:
            raise ValueError("已完成的指标窗口收到超出缓冲范围的乱序点，统计证据不完整")
        self._high_water_window = max(
            self._high_water_window if self._high_water_window is not None else window_epoch,
            window_epoch,
        )
        # Retain current and previous windows so boundary-adjacent points can
        # merge before upload. One scalar watermark prevents partial re-uploads.
        cutoff = self._high_water_window - self.window_seconds
        self._finalized_before = max(
            self._finalized_before if self._finalized_before is not None else cutoff, cutoff,
        )
        key = (window_epoch, step_id)
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = _Bucket(
                datetime.fromtimestamp(window_epoch, tz=timezone.utc),
                step_id,
                self.max_latency_samples,
                self.max_samples,
            )
            self._buckets[key] = bucket
        bucket.accept(metric, value, tags, timestamp)
        ready_keys = [item for item in self._buckets if item[0] < self._finalized_before]
        return self._flush_keys(ready_keys)

    def flush_all(self):
        if self._high_water_window is not None:
            self._finalized_before = self._high_water_window + self.window_seconds
        return self._flush_keys(list(self._buckets))

    def _flush_keys(self, keys):
        result = []
        for key in sorted(keys):
            bucket = self._buckets.pop(key, None)
            if bucket is not None:
                result.append(bucket.view(self.window_seconds))
        return tuple(result)
