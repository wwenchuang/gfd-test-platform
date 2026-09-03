"""Validated and idempotent load metric ingestion."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from task_server.api_testing.events import LoadEventStream
from task_server.api_testing.models.load_testing import ApiLoadMetricBucket, ApiLoadRunShard
from task_server.api_testing.services.load_metric_service import LoadMetricError, LoadMetricService
from tests.api_testing.test_load_testing_repository import load_factory, load_records, load_run_with_shard


START = "2026-09-03T10:00:00+00:00"


def _payload(**metric_overrides):
    metrics = {
        "requests": 10,
        "iterations": 5,
        "dropped_iterations": 0,
        "http_failures": 0,
        "business_assertions": 10,
        "business_failures": 0,
        "workflow_iterations": 5,
        "workflow_failures": 0,
        "latency_histogram": {
            "bounds_ms": [10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000],
            "counts": [0, 1, 4, 5, 0, 0, 0, 0, 0, 0, 0],
            "count": 10,
            "sum_ms": 620,
            "max_ms": 98,
        },
        **metric_overrides,
    }
    return {
        "batch_id": "batch-20260903-1",
        "buckets": [{"step_id": "search", "started_at": START, "bucket_seconds": 5, "metrics": metrics}],
    }


def _running(load_factory, run, shard):
    with load_factory.begin() as session:
        session.get(type(run), run.id).state = "running"
        session.get(ApiLoadRunShard, shard.id).state = "running"


def test_ingestion_validates_and_duplicate_batch_does_not_double_count(load_factory, load_run_with_shard):
    _repository, run, shard = load_run_with_shard
    _running(load_factory, run, shard)
    published = []
    service = LoadMetricService(load_factory, publisher=lambda run_id, kind, payload: published.append((run_id, kind, payload)))

    first = service.ingest(shard.agent_id, shard.id, _payload())
    duplicate = service.ingest(shard.agent_id, shard.id, _payload())

    assert first == {"accepted": 1, "duplicate": False}
    assert duplicate == {"accepted": 1, "duplicate": True}
    assert len(published) == 1
    with load_factory() as session:
        buckets = tuple(session.scalars(select(ApiLoadMetricBucket).where(ApiLoadMetricBucket.run_id == run.id)))
    assert len(buckets) == 1
    assert buckets[0].bucket_seconds == 5


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (_payload(requests=-1), "不能为负数"),
        (_payload(requests=float("nan")), "有限数字"),
        ({**_payload(), "buckets": [{**_payload()["buckets"][0], "bucket_seconds": 10}]}, "5秒"),
        ({**_payload(), "buckets": [{**_payload()["buckets"][0], "step_id": "unknown"}]}, "未知步骤"),
    ],
)
def test_ingestion_rejects_invalid_metric_evidence(load_factory, load_run_with_shard, payload, message):
    _repository, run, shard = load_run_with_shard
    _running(load_factory, run, shard)

    with pytest.raises(LoadMetricError, match=message):
        LoadMetricService(load_factory).ingest(shard.agent_id, shard.id, payload)


def test_terminal_or_foreign_shard_upload_is_rejected(load_factory, load_run_with_shard):
    _repository, run, shard = load_run_with_shard
    _running(load_factory, run, shard)
    with pytest.raises(LoadMetricError, match="不属于"):
        LoadMetricService(load_factory).ingest("other-agent", shard.id, _payload())

    with load_factory.begin() as session:
        session.get(ApiLoadRunShard, shard.id).state = "finished"
    with pytest.raises(LoadMetricError, match="已经结束"):
        LoadMetricService(load_factory).ingest(shard.agent_id, shard.id, _payload())


def test_default_metric_event_is_durable_but_internal_batch_receipt_is_hidden(load_factory, load_run_with_shard):
    _repository, run, shard = load_run_with_shard
    _running(load_factory, run, shard)

    LoadMetricService(load_factory).ingest(shard.agent_id, shard.id, _payload())
    events = LoadEventStream(load_factory).read(run.id, 0, 0)

    assert [(item.type, item.payload["bucket_count"]) for item in events] == [("metrics", 1)]


def test_metric_windows_must_be_ordered_and_unique(load_factory, load_run_with_shard):
    _repository, run, shard = load_run_with_shard
    _running(load_factory, run, shard)
    first = _payload()["buckets"][0]
    later = {**first, "started_at": "2026-09-03T10:00:05+00:00"}
    service = LoadMetricService(load_factory)

    with pytest.raises(LoadMetricError, match="时间顺序"):
        service.ingest(shard.agent_id, shard.id, {"batch_id": "unordered", "buckets": [later, first]})
    with pytest.raises(LoadMetricError, match="重复"):
        service.ingest(shard.agent_id, shard.id, {"batch_id": "duplicate-window", "buckets": [first, first]})
