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
    assert metrics["latency_histogram"]["count"] == 5
    assert sum(metrics["latency_histogram"]["counts"]) == 5
    assert metrics["latency_histogram"]["sum_ms"] == 150.0
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


def test_business_and_workflow_assertion_totals_are_kept_separate():
    aggregator = MetricAggregator(window_seconds=5)
    aggregator.accept(_point("checks", 1, 1))
    aggregator.accept(_point("checks", 0, 1, tags={"check": "业务码为0"}))
    aggregator.accept(_point("workflow_iteration_success", 1, 1))
    aggregator.accept(_point("workflow_iteration_success", 0, 1))

    metrics = aggregator.flush_all()[0]["metrics"]

    assert metrics["business_assertions"] == 2
    assert metrics["business_failures"] == 1
    assert metrics["workflow_iterations"] == 2
    assert metrics["workflow_failures"] == 1


def test_vu_gauge_preserves_bounded_actual_sample_summary_and_times():
    aggregator = MetricAggregator()
    for second, value in ((3, 4), (1, 0), (4, 8), (2, 4)):
        aggregator.accept(_point("vus", value, second, step="all"))
    metrics = aggregator.flush_all()[0]["metrics"]
    assert metrics["vu_gauge"] == {
        "count": 4, "min": 0, "max": 8, "sum": 16,
        "first_at": "2026-09-03T08:00:01+00:00",
        "last_at": "2026-09-03T08:00:04+00:00",
        "max_gap_seconds": 2.0,
    }
    assert "values" not in metrics["vu_gauge"]


def test_vu_gauge_is_only_emitted_for_global_actual_samples():
    aggregator = MetricAggregator()
    aggregator.accept(_point("vus", 4, 1))
    aggregator.accept(_point("vus_max", 100, 1, step="all"))
    aggregator.accept(_point("http_reqs", 1, 1, step="all"))
    assert all("vu_gauge" not in item["metrics"] for item in aggregator.flush_all())


def test_invalid_vu_and_nonfinite_points_do_not_poison_valid_metrics():
    aggregator = MetricAggregator()
    for value in (float("nan"), float("inf"), -1, 1.5, True):
        aggregator.accept(_point("vus", value, 1, step="all"))
    aggregator.accept(_point("http_reqs", float("nan"), 1, step="all"))
    aggregator.accept(_point("http_req_duration", float("inf"), 1, step="all"))
    aggregator.accept(_point("vus", 2, 2, step="all"))
    metrics = aggregator.flush_all()[0]["metrics"]
    assert metrics["vu_gauge"]["count"] == 1
    assert metrics["vu_gauge"]["sum"] == 2
    assert metrics["requests"] == 0
    assert metrics["latency_histogram"]["count"] == 0


def test_vu_windows_keep_counts_independent_without_retaining_raw_samples():
    aggregator = MetricAggregator()
    for _ in range(10000):
        aggregator.accept(_point("vus", 3, 1, step="all"))
    assert aggregator.accept(_point("vus", 7, 5, step="all")) == ()
    first = aggregator.accept(_point("vus", 9, 10, step="all"))[0]
    last = aggregator.flush_all()[0]
    assert first["metrics"]["vu_gauge"]["count"] == 10000
    assert first["metrics"]["vu_gauge"]["sum"] == 30000
    assert len(first["metrics"]["vu_gauge"]) == 7
    assert first["samples"] == []
    assert last["metrics"]["vu_gauge"]["count"] == 1
    assert last["metrics"]["vu_gauge"]["min"] == 7


def test_sparse_vu_samples_expose_internal_cadence_gaps():
    from datetime import timedelta
    aggregator = MetricAggregator()
    buckets = []
    origin = datetime(2026, 9, 3, 8, tzinfo=timezone.utc)
    for second in (0, .01, 4, 5, 5.01, 9):
        point = _point("vus", 10, 0, step="all")
        point["data"]["time"] = (origin + timedelta(seconds=second)).isoformat()
        buckets.extend(aggregator.accept(point))
    buckets.extend(aggregator.flush_all())
    assert len(buckets) == 2
    for bucket in buckets:
        assert bucket["metrics"]["vu_gauge"]["count"] == 3
        assert bucket["metrics"]["vu_gauge"]["max_gap_seconds"] == 3.99


def test_late_vu_sample_cannot_erase_an_observed_cadence_gap():
    aggregator = MetricAggregator()
    for second in (0, 4, 1, 2, 3):
        aggregator.accept(_point("vus", 10, second, step="all"))
    assert aggregator.flush_all()[0]["metrics"]["vu_gauge"]["max_gap_seconds"] == 4


def test_previous_window_accepts_late_points_before_finalization():
    aggregator = MetricAggregator()
    assert aggregator.accept(_point("http_reqs", 1, 4)) == ()
    assert aggregator.accept(_point("http_reqs", 1, 5)) == ()
    assert aggregator.accept(_point("http_reqs", 1, 4)) == ()
    first = aggregator.accept(_point("http_reqs", 1, 10))[0]
    assert first["metrics"]["requests"] == 2
    assert [item["metrics"]["requests"] for item in aggregator.flush_all()] == [1, 1]


def test_points_for_finalized_windows_raise_instead_of_recreating_partial_buckets():
    import pytest
    aggregator = MetricAggregator()
    aggregator.accept(_point("http_reqs", 1, 0))
    assert len(aggregator.accept(_point("http_reqs", 1, 10))) == 1
    with pytest.raises(ValueError, match="已完成.*乱序"):
        aggregator.accept(_point("http_reqs", 1, 0))


def test_unsupported_metrics_cannot_advance_watermark_or_create_empty_buckets():
    aggregator = MetricAggregator()
    aggregator.accept(_point("http_reqs", 1, 0))
    assert aggregator.accept(_point("unsupported", 1, 30)) == ()
    assert aggregator.accept(_point("vus", 10, 30, step="search")) == ()
    aggregator.accept(_point("http_reqs", 1, 0))
    buckets = aggregator.flush_all()
    assert len(buckets) == 1
    assert buckets[0]["metrics"]["requests"] == 2


def test_final_flush_watermark_cannot_move_backward_if_collection_resumes():
    import pytest
    aggregator = MetricAggregator()
    aggregator.accept(_point("http_reqs", 1, 0))
    aggregator.flush_all()
    aggregator.accept(_point("http_reqs", 1, 5))
    with pytest.raises(ValueError, match="乱序"):
        aggregator.accept(_point("http_reqs", 1, 0))
