"""VU evidence validation without PostgreSQL or Redis fixtures."""

import pytest

from task_server.api_testing.services.load_metric_service import LoadMetricError, LoadMetricService
from tests.api_testing.test_load_metric_service import _payload


def _gauge(**overrides):
    return {"count": 3, "min": 2, "max": 4, "sum": 9,
            "first_at": "2026-09-03T10:00:01+00:00",
            "last_at": "2026-09-03T10:00:04+00:00", **overrides}


def _validate(gauge, step="all"):
    bucket = _payload(vu_gauge=gauge)["buckets"][0]
    return LoadMetricService._bucket({**bucket, "step_id": step}, {"all", "search"})


def test_legacy_metric_payload_remains_accepted_without_vu_evidence():
    assert "vu_gauge" not in LoadMetricService._bucket(_payload()["buckets"][0], {"search"})["metrics"]


def test_valid_gauge_preserves_all_samples_and_normalizes_utc_times():
    result = _validate(_gauge(first_at="2026-09-03T18:00:01+08:00"))
    assert result["metrics"]["vu_gauge"] == _gauge()


@pytest.mark.parametrize("overrides", [
    {"count": 0}, {"count": -1}, {"count": 1.5}, {"count": True},
    {"min": -1}, {"min": 0.5}, {"max": float("nan")}, {"sum": float("inf")},
    {"sum": 9.5}, {"min": 5}, {"sum": 5}, {"sum": 13},
    {"first_at": "2026-09-03T10:00:05+00:00"},
    {"first_at": "2026-09-03T09:59:59+00:00"},
    {"last_at": "2026-09-03T10:00:05+00:00"},
    {"first_at": "2026-09-03T10:00:04.500+00:00"},
    {"last_at": "2026-09-03T10:00:04"}, {"last_at": None},
    {"values": [2, 3, 4]},
])
def test_invalid_gauge_is_rejected(overrides):
    with pytest.raises(LoadMetricError):
        _validate(_gauge(**overrides))


def test_vu_gauge_is_rejected_on_individual_steps():
    with pytest.raises(LoadMetricError):
        _validate(_gauge(), step="search")


def test_zero_vus_is_valid_evidence():
    assert _validate(_gauge(min=0, max=0, sum=0))["metrics"]["vu_gauge"]["sum"] == 0


def test_agent_vu_bucket_passes_server_validation():
    from load_agent.k6_metrics import MetricAggregator
    from tests.load_agent.test_k6_metrics import _point

    aggregator = MetricAggregator()
    aggregator.accept(_point("vus", 3, 1, step="all"))
    aggregator.accept(_point("vus", 5, 4, step="all"))
    bucket = aggregator.flush_all()[0]
    normalized = LoadMetricService._bucket(bucket, {"all"})
    assert normalized["metrics"]["vu_gauge"] == bucket["metrics"]["vu_gauge"]


def test_vu_cadence_evidence_is_optional_but_preserved_when_present():
    assert "max_gap_seconds" not in _validate(_gauge())["metrics"]["vu_gauge"]
    assert _validate(_gauge(max_gap_seconds=1.5))["metrics"]["vu_gauge"]["max_gap_seconds"] == 1.5


@pytest.mark.parametrize("gap", [-1, float("nan"), float("inf"), True, "1", 5.1])
def test_invalid_vu_cadence_is_rejected(gap):
    with pytest.raises(LoadMetricError):
        _validate(_gauge(max_gap_seconds=gap))
