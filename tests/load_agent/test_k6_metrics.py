from datetime import datetime, timezone

from load_agent.k6_metrics import MetricAggregator


def _point(metric, value, second, *, step="search", tags=None):
    return {
        "type": "Point",
        "metric": metric,
        "data": {
            "time": f"2026-09-03T08:00:{second:02d}+00:00",
            "value": value,
            "tags": {"step_id": step, **(tags or {})},
        },
    }


def test_five_second_buckets_include_exact_percentiles_and_counts():
    aggregator = MetricAggregator(window_seconds=5, max_latency_samples=100)
    for index, duration in enumerate((10, 20, 30, 40, 50)):
        aggregator.accept(_point("http_req_duration", duration, index))
        aggregator.accept(_point("http_reqs", 1, index))
    aggregator.accept(_point("http_req_failed", 1, 4))

    buckets = aggregator.flush_all()

    assert len(buckets) == 1
    metrics = buckets[0]["metrics"]
    assert metrics["requests"] == 5
    assert metrics["http_failures"] == 1
    assert metrics["latency_ms"] == {"count": 5, "p50": 30.0, "p90": 50.0, "p95": 50.0, "p99": 50.0, "max": 50.0}
    assert buckets[0]["started_at"] == datetime(2026, 9, 3, 8, 0, tzinfo=timezone.utc).isoformat()


def test_latency_memory_and_error_samples_are_bounded():
    aggregator = MetricAggregator(window_seconds=5, max_latency_samples=4, max_samples=2)
    for index in range(20):
        aggregator.accept(_point("http_req_duration", index + 1, 1))
        aggregator.accept(_point("checks", 0, 1, tags={"check": f"error-{index}"}))

    bucket = aggregator.flush_all()[0]

    assert bucket["metrics"]["latency_ms"]["count"] == 20
    assert len(bucket["samples"]) == 2
    assert aggregator.retained_latency_values <= 4
