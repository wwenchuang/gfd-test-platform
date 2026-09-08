"""PostgreSQL persistence of runtime resource-only batches and finish payloads."""

import copy

import pytest

from task_server.api_testing import load_agent_http
from task_server.api_testing.models.load_testing import ApiLoadRunShard
from task_server.api_testing.services.load_metric_service import LoadMetricError, LoadMetricService
from tests.api_testing.test_load_metric_service import _running
from tests.api_testing.test_load_testing_repository import load_factory, load_records, load_run_with_shard
from tests.load_agent.test_resource_validation import _payload


def test_live_resource_only_batches_are_durable_idempotent_and_finalized(load_factory, load_run_with_shard, monkeypatch):
    _, run, shard = load_run_with_shard
    _running(load_factory, run, shard)
    service = LoadMetricService(load_factory, publisher=lambda *_args: None)
    payload = {"batch_id": "resources-1", "buckets": [], "load_generator_resources": _payload()}
    assert service.ingest(shard.agent_id, shard.id, payload) == {"accepted": 0, "duplicate": False}
    assert service.ingest(shard.agent_id, shard.id, payload)["duplicate"] is True
    second = copy.deepcopy(_payload())
    second.update(sample_count=2, dropped_samples=1)
    second["samples"][0]["sampled_at"] = "2026-09-08T10:00:05+00:00"
    service.ingest(shard.agent_id, shard.id, {**payload, "batch_id": "resources-2", "load_generator_resources": second})
    with load_factory() as session:
        persisted = session.get(ApiLoadRunShard, shard.id).summary["load_generator_resources"]
    assert persisted["dropped_samples"] == 0
    assert len(persisted["samples"]) == 2
    monkeypatch.setattr(load_agent_http, "_factory", lambda: load_factory)
    monkeypatch.setattr(load_agent_http, "_dispatch_load_completion", lambda *_args: None)
    result = load_agent_http._finish_shard(shard.agent_id, shard.id, {
        "state": "finished", "summary": {"exit_code": 0, "load_generator_resources": persisted}})
    assert result["summary"]["load_generator_resources"] == persisted


def test_foreign_empty_or_invalid_resource_batches_do_not_write(load_factory, load_run_with_shard):
    _, run, shard = load_run_with_shard
    _running(load_factory, run, shard)
    service = LoadMetricService(load_factory, publisher=lambda *_args: None)
    payload = {"batch_id": "resources-invalid", "buckets": [], "load_generator_resources": _payload()}
    with pytest.raises(LoadMetricError, match="不属于"):
        service.ingest("another-agent", shard.id, payload)
    with pytest.raises(LoadMetricError):
        service.ingest(shard.agent_id, shard.id, {"batch_id": "empty", "buckets": []})
    payload["load_generator_resources"]["samples"][0]["cpu_percent"] = 0
    with pytest.raises(LoadMetricError):
        service.ingest(shard.agent_id, shard.id, payload)
    with load_factory() as session:
        assert "load_generator_resources" not in (session.get(ApiLoadRunShard, shard.id).summary or {})


def test_finish_rejects_invalid_resource_contract_before_writing(monkeypatch):
    from task_server.api_testing.http import ApiHttpError
    payload = _payload()
    payload["role"] = "target"
    with pytest.raises(ApiHttpError) as caught:
        load_agent_http._finish_shard("irrelevant", "irrelevant", {
            "state": "finished", "summary": {"load_generator_resources": payload}})
    assert caught.value.status == 422
