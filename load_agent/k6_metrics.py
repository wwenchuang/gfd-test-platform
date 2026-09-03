"""Bounded aggregation of incremental k6 JSON point output."""

from datetime import datetime, timezone
from bisect import bisect_left
import math


COUNTERS = {
    "http_reqs": "requests",
    "iterations": "iterations",
    "dropped_iterations": "dropped_iterations",
    "data_sent": "bytes_sent",
    "data_received": "bytes_received",
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

    def accept(self, metric, value, tags):
        if metric in COUNTERS:
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
        except (TypeError, ValueError):
            return ()
        tags = data.get("tags") if isinstance(data.get("tags"), dict) else {}
        step_id = str(tags.get("step_id") or "all")[:120]
        epoch = int(timestamp.timestamp())
        window_epoch = epoch - epoch % self.window_seconds
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
        bucket.accept(str(point.get("metric") or ""), value, tags)
        ready_keys = [item for item in self._buckets if item[0] < window_epoch]
        return self._flush_keys(ready_keys)

    def flush_all(self):
        return self._flush_keys(list(self._buckets))

    def _flush_keys(self, keys):
        result = []
        for key in sorted(keys):
            bucket = self._buckets.pop(key, None)
            if bucket is not None:
                result.append(bucket.view(self.window_seconds))
        return tuple(result)
