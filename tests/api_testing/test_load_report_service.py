"""Truthful deterministic performance report contracts."""

from datetime import datetime, timedelta, timezone

import pytest

from task_server.api_testing import access
from task_server.api_testing.models.load_testing import ApiLoadRun, ApiLoadRunShard
from task_server.api_testing.services.load_metric_service import LoadMetricService
from task_server.api_testing.services.load_report_service import LoadReportService
from tests.api_testing.test_load_testing_repository import load_factory, load_records, load_run_with_shard


START = datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc)
BOUNDS = [10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000]


@pytest.fixture(autouse=True)
def standalone_access(monkeypatch):
    monkeypatch.setattr(access, "get_access_profile", lambda _actor: None)


def _prepare(load_factory, fixture, *, target_rate=5, thresholds=None, state="finished"):
    _repository, run, shard = fixture
    configuration = {
        "workload": {
            "executor": "constant-arrival-rate",
            "rate": target_rate,
            "time_unit": "1s",
            "duration_seconds": 10,
        },
        "thresholds": thresholds or {},
        "scenario": {"version_id": run.scenario_version_id, "content_hash": "hash"},
        "environment": {"revision_id": run.environment_revision_id, "name": "性能环境"},
    }
    with load_factory.begin() as session:
        persisted = session.get(ApiLoadRun, run.id)
        persisted.configuration = configuration
        persisted.load_model = "constant-arrival-rate"
        persisted.state = "running"
        persisted.started_at = START
        persisted.finished_at = START + timedelta(seconds=10)
        session.get(ApiLoadRunShard, shard.id).state = "running"
    return run, shard, state


def _metric_payload(batch, *, requests, iterations, http_failures=0, business_failures=0, workflow_failures=0, dropped=0, start=START, slow=False):
    counts = [0, 0, 0, 0, 0, requests, 0, 0, 0, 0, 0] if slow else [0, 0, requests // 2, requests - requests // 2, 0, 0, 0, 0, 0, 0, 0]
    return {
        "batch_id": batch,
        "buckets": [{
            "step_id": "search",
            "started_at": start.isoformat(),
            "bucket_seconds": 5,
            "metrics": {
                "requests": requests,
                "iterations": iterations,
                "dropped_iterations": dropped,
                "http_failures": http_failures,
                "business_assertions": requests,
                "business_failures": business_failures,
                "workflow_iterations": iterations,
                "workflow_failures": workflow_failures,
                "latency_histogram": {
                    "bounds_ms": BOUNDS,
                    "counts": counts,
                    "count": requests,
                    "sum_ms": requests * 70,
                    "max_ms": 499 if slow else 99,
                },
            },
        }],
    }


def _finish(load_factory, run, shard, state="finished"):
    with load_factory.begin() as session:
        session.get(ApiLoadRunShard, shard.id).state = state
        session.get(ApiLoadRun, run.id).state = "finished" if state == "finished" else state


def test_unreached_rate_is_inconclusive_even_when_all_requests_pass(load_factory, load_run_with_shard):
    run, shard, _ = _prepare(load_factory, load_run_with_shard, target_rate=5)
    LoadMetricService(load_factory).ingest(shard.agent_id, shard.id, _metric_payload("slow", requests=42, iterations=42))
    _finish(load_factory, run, shard)

    report = LoadReportService(load_factory).build(run.id, "load-owner")

    assert report["load_goal"]["label"] == "负载目标"
    assert report["load_goal"]["target_iterations_per_second"] == 5
    assert report["load_goal"]["actual_iterations_per_second"] == 4.2
    assert report["load_goal"]["reached"] is False
    assert report["verdict"] == "inconclusive"
    assert "未达到目标负载" in report["verdict_explanation"]


def test_rate_uses_configured_load_window_instead_of_orchestration_wall_clock(
    load_factory, load_run_with_shard
):
    run, shard, _ = _prepare(load_factory, load_run_with_shard, target_rate=1)
    LoadMetricService(load_factory).ingest(
        shard.agent_id,
        shard.id,
        _metric_payload("complete-window", requests=10, iterations=10),
    )
    _finish(load_factory, run, shard)
    with load_factory.begin() as session:
        session.get(ApiLoadRun, run.id).finished_at = START + timedelta(seconds=13)

    report = LoadReportService(load_factory).build(run.id, "load-owner")

    assert report["transport"]["requests_per_second"] == 1.0
    assert report["load_goal"]["actual_iterations_per_second"] == 1.0
    assert report["load_goal"]["reached"] is True
    assert report["verdict"] == "passed"


def test_http_200_business_failure_is_not_transport_success_verdict(load_factory, load_run_with_shard):
    thresholds = {"business_failure_rate": {"operator": "less_than_or_equal", "value": 0, "required": True}}
    run, shard, _ = _prepare(load_factory, load_run_with_shard, target_rate=4, thresholds=thresholds)
    LoadMetricService(load_factory).ingest(
        shard.agent_id,
        shard.id,
        _metric_payload("business", requests=50, iterations=50, business_failures=5, workflow_failures=5),
    )
    _finish(load_factory, run, shard)

    report = LoadReportService(load_factory).build(run.id, "load-owner")

    assert report["transport"]["http_error_rate"] == 0
    assert report["business"]["failure_rate"] == 0.1
    assert report["workflow"]["failure_rate"] == 0.1
    assert report["verdict"] == "failed"
    assert report["thresholds"][0]["label"] == "业务断言失败率"


def test_lost_shard_and_incompatible_comparison_are_explicit(load_factory, load_run_with_shard):
    run, shard, _ = _prepare(load_factory, load_run_with_shard, target_rate=1)
    LoadMetricService(load_factory).ingest(shard.agent_id, shard.id, _metric_payload("lost", requests=20, iterations=20))
    _finish(load_factory, run, shard, state="failed")

    report = LoadReportService(load_factory).build(run.id, "load-owner")

    assert report["verdict"] == "inconclusive"
    assert report["agents"][0]["state"] == "failed"
    assert report["evidence"]["complete"] is False
    assert report["comparison"]["compatible"] is False
    assert report["comparison"]["reason"] == "没有可比历史运行"


def test_two_shards_sum_counts_and_merge_histograms_without_averaging_percentiles(load_factory, load_run_with_shard):
    run, first, _ = _prepare(load_factory, load_run_with_shard, target_rate=9)
    with load_factory.begin() as session:
        second = ApiLoadRunShard(
            run_id=run.id,
            agent_id=first.agent_id,
            sequence=1,
            global_sequence=1,
            allocation={"rate": 5},
            state="running",
            owner_id="load-owner",
            created_by="load-owner",
            updated_by="load-owner",
        )
        session.add(second)
        session.flush()
        second_id = second.id
    LoadMetricService(load_factory).ingest(first.agent_id, first.id, _metric_payload("first", requests=40, iterations=40))
    LoadMetricService(load_factory).ingest(first.agent_id, second_id, _metric_payload("second", requests=60, iterations=60, slow=True))
    with load_factory.begin() as session:
        session.get(ApiLoadRunShard, first.id).state = "finished"
        session.get(ApiLoadRunShard, second_id).state = "finished"
        session.get(ApiLoadRun, run.id).state = "finished"

    report = LoadReportService(load_factory).build(run.id, "load-owner")

    assert report["transport"]["requests"] == 100
    assert report["latency"]["p95_ms"] == 500.0
    assert report["verdict"] == "passed"


def test_missing_five_second_window_and_stopped_run_are_inconclusive(load_factory, load_run_with_shard):
    run, shard, _ = _prepare(load_factory, load_run_with_shard, target_rate=1)
    LoadMetricService(load_factory).ingest(shard.agent_id, shard.id, _metric_payload("window-0", requests=10, iterations=10))
    LoadMetricService(load_factory).ingest(
        shard.agent_id,
        shard.id,
        _metric_payload("window-10", requests=10, iterations=10, start=START + timedelta(seconds=10)),
    )
    with load_factory.begin() as session:
        session.get(ApiLoadRunShard, shard.id).state = "cancelled"
        session.get(ApiLoadRun, run.id).state = "cancelled"

    report = LoadReportService(load_factory).build(run.id, "load-owner")

    assert report["verdict"] == "inconclusive"
    assert report["evidence"]["missing_windows"] == 1


def test_dropped_iteration_threshold_is_evaluated_separately(load_factory, load_run_with_shard):
    thresholds = {"dropped_iteration_rate": {"operator": "less_than", "value": 0.05, "required": True}}
    run, shard, _ = _prepare(load_factory, load_run_with_shard, target_rate=5, thresholds=thresholds)
    LoadMetricService(load_factory).ingest(
        shard.agent_id,
        shard.id,
        _metric_payload("dropped", requests=50, iterations=50, dropped=10),
    )
    _finish(load_factory, run, shard)

    report = LoadReportService(load_factory).build(run.id, "load-owner")

    assert report["dropped_iterations"]["rate"] == 0.166667
    assert report["thresholds"][0]["passed"] is False
    assert report["verdict"] == "failed"


def test_incompatible_history_explains_why_no_regression_claim_is_made(load_factory, load_run_with_shard):
    run, shard, _ = _prepare(load_factory, load_run_with_shard, target_rate=2)
    LoadMetricService(load_factory).ingest(shard.agent_id, shard.id, _metric_payload("current", requests=25, iterations=25))
    _finish(load_factory, run, shard)
    with load_factory.begin() as session:
        current = session.get(ApiLoadRun, run.id)
        previous = ApiLoadRun(
            project_id=current.project_id,
            scenario_version_id=current.scenario_version_id,
            environment_revision_id=current.environment_revision_id,
            load_model="constant-vus",
            queue_priority="normal",
            configuration={"workload": {"executor": "constant-vus", "vus": 10, "duration_seconds": 10}},
            state="finished",
            verdict="passed",
            created_at=current.created_at - timedelta(minutes=5),
            owner_id="load-owner",
            created_by="load-owner",
            updated_by="load-owner",
        )
        session.add(previous)
        session.flush()
        previous_id = previous.id

    report = LoadReportService(load_factory).build(run.id, "load-owner")

    assert report["comparison"]["compatible"] is False
    assert report["comparison"]["previous_run_id"] == previous_id
    assert report["comparison"]["reason"] == "最近历史运行使用了不同的负载模型"
