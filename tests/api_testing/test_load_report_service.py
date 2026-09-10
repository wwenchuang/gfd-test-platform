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


@pytest.mark.parametrize("requests,verdict,complete", [(21,"passed",True),(22,"inconclusive",False)])
def test_missing_latency_points_respect_count_tolerance(load_factory, load_run_with_shard, requests, verdict, complete):
    run, shard, _ = _prepare(load_factory, load_run_with_shard, target_rate=1)
    payload = _metric_payload("partial-stream", requests=19, iterations=20)
    payload["buckets"][0]["metrics"]["requests"] = requests
    LoadMetricService(load_factory).ingest(shard.agent_id, shard.id, payload)
    _finish(load_factory, run, shard)
    report = LoadReportService(load_factory).build(run.id, "load-owner")
    assert report["verdict"] == verdict
    assert report["evidence"]["complete"] is complete
    assert report["transport"]["requests"] == requests
    assert report["latency"]["sample_count"] == 19
    assert "采样计数" in report["verdict_explanation"]


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


def test_report_recomputes_a_stale_persisted_verdict_from_current_evidence(
    load_factory, load_run_with_shard
):
    run, shard, _ = _prepare(load_factory, load_run_with_shard, target_rate=1)
    LoadMetricService(load_factory).ingest(
        shard.agent_id,
        shard.id,
        _metric_payload("complete-after-fix", requests=10, iterations=10),
    )
    _finish(load_factory, run, shard)
    with load_factory.begin() as session:
        session.get(ApiLoadRun, run.id).verdict = "inconclusive"

    report = LoadReportService(load_factory).build(run.id, "load-owner")

    assert report["evidence"]["complete"] is True
    assert report["load_goal"]["reached"] is True
    assert report["verdict"] == "passed"
    assert report["verdict_label"] == "通过"


def test_series_combines_step_and_workflow_metrics_in_the_same_five_second_window(
    load_factory, load_run_with_shard
):
    run, shard, _ = _prepare(load_factory, load_run_with_shard, target_rate=1)
    request_bucket = _metric_payload("request-window", requests=3, iterations=0)
    workflow_bucket = _metric_payload("workflow-window", requests=0, iterations=3)
    workflow_bucket["buckets"][0]["step_id"] = "all"
    LoadMetricService(load_factory).ingest(shard.agent_id, shard.id, request_bucket)
    LoadMetricService(load_factory).ingest(shard.agent_id, shard.id, workflow_bucket)
    _finish(load_factory, run, shard)

    report = LoadReportService(load_factory).build(run.id, "load-owner")

    assert len(report["series"]) == 1
    assert report["series"][0]["requests"] == 3
    assert report["series"][0]["iterations"] == 3


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
    assert report["latency"]["p95_ms"] == 499.0
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


@pytest.mark.parametrize('with_gauges', [False, True])
def test_constant_vus_requires_persisted_sustained_actual_samples(load_factory, load_run_with_shard, with_gauges):
    run, shard, _ = _prepare(load_factory, load_run_with_shard)
    with load_factory.begin() as session:
        row = session.get(ApiLoadRun, run.id)
        row.configuration = {**row.configuration, 'workload': {'executor': 'constant-vus', 'vus': 5, 'duration_seconds': 10}}
        session.get(ApiLoadRunShard, shard.id).allocation = {'vus': 5}
    service = LoadMetricService(load_factory)
    service.ingest(shard.agent_id, shard.id, _metric_payload('requests', requests=50, iterations=50))
    if with_gauges:
        for seconds in (0, 5):
            start = START + timedelta(seconds=seconds)
            payload = _metric_payload('vu-' + str(seconds), requests=0, iterations=0, start=start)
            bucket = payload['buckets'][0]
            bucket['step_id'] = 'all'
            bucket['metrics']['vu_gauge'] = {'count': 5, 'min': 5, 'max': 5, 'sum': 25,
                'first_at': start.isoformat(), 'last_at': (start + timedelta(seconds=4)).isoformat(), 'max_gap_seconds': 1}
            service.ingest(shard.agent_id, shard.id, payload)
    _finish(load_factory, run, shard)
    report = LoadReportService(load_factory).build(run.id, 'load-owner')
    assert report['load_goal']['reached'] is with_gauges
    assert report['verdict'] == ('passed' if with_gauges else 'inconclusive')
    assert report['transport']['requests'] == 50
    assert report['statistics_schema_version'] == 2


def test_one_shard_extra_stage_requests_cannot_hide_other_shard_missing(load_factory, load_run_with_shard):
    from task_server.api_testing.models.load_testing import ApiLoadMetricBucket
    from tests.api_testing.test_load_testing_repository import _audit
    run, shard, _ = _prepare(load_factory, load_run_with_shard)
    with load_factory.begin() as session:
        saved = session.get(ApiLoadRun,run.id)
        workload={'executor':'ramping-arrival-rate','start_rate':10,'time_unit':'1s','max_vus':20,'pre_allocated_vus':10,'stages':[{'duration_seconds':10,'target':10}]}
        saved.configuration={**saved.configuration,'workload':workload};saved.state='finished'
        first=session.get(ApiLoadRunShard,shard.id);first.state='finished';first.allocation={'rate':5,'vus':10}
        other=ApiLoadRunShard(run_id=run.id,agent_id=shard.agent_id,sequence=shard.sequence+1,global_sequence=shard.global_sequence+1,allocation={'rate':5,'vus':10},state='finished',**_audit())
        session.add(other);session.flush();other_id=other.id
        session.add(ApiLoadMetricBucket(run_id=run.id,shard_id=shard.id,scenario_step_id='__load_stage_0',bucket_started_at=START,bucket_seconds=5,metrics={'workflow_starts':100},**_audit()))
    report=LoadReportService(load_factory).build(run.id,'load-owner')
    assert report['load_goal']['stages'][0]['reached']
    assert not report['load_goal']['reached'] and report['load_goal']['requires_stage_evidence']
    assert next(s for s in report['load_goal']['shards'] if s['shard_id']==other_id)['requires_stage_evidence']
    with load_factory.begin() as session:
        session.add(ApiLoadMetricBucket(run_id=run.id,shard_id=other_id,scenario_step_id='__load_stage_0',bucket_started_at=START,bucket_seconds=5,metrics={'workflow_starts':50},**_audit()))
    assert LoadReportService(load_factory).build(run.id,'load-owner')['load_goal']['reached']


@pytest.mark.parametrize('starts,reached', [((1, 1), True), ((2, 0), False)])
def test_independent_shards_sum_integer_schedules_without_cross_node_compensation(load_factory, load_run_with_shard, starts, reached):
    from task_server.api_testing.models.load_testing import ApiLoadMetricBucket
    from tests.api_testing.test_load_testing_repository import _audit
    run, shard, _ = _prepare(load_factory, load_run_with_shard)
    with load_factory.begin() as session:
        saved = session.get(ApiLoadRun, run.id)
        saved.configuration = {**saved.configuration, 'workload': {
            'executor': 'ramping-arrival-rate', 'start_rate': 2, 'time_unit': '1s',
            'max_vus': 20, 'pre_allocated_vus': 10, 'stages': [{'duration_seconds': 1, 'target': 4}]}}
        saved.state = 'finished'
        first = session.get(ApiLoadRunShard, shard.id)
        first.state = 'finished'; first.allocation = {'rate': 2, 'vus': 10}
        second = ApiLoadRunShard(run_id=run.id, agent_id=shard.agent_id, sequence=shard.sequence+1,
            global_sequence=shard.global_sequence+1, allocation={'rate': 2, 'vus': 10}, state='finished', **_audit())
        session.add(second); session.flush()
        for item, count in zip((first, second), starts):
            session.add(ApiLoadMetricBucket(run_id=run.id, shard_id=item.id, scenario_step_id='__load_stage_0',
                bucket_started_at=START, bucket_seconds=5, metrics={'workflow_starts': count}, **_audit()))
    goal = LoadReportService(load_factory).build(run.id, 'load-owner')['load_goal']
    assert goal['expected_iterations'] == 3
    assert goal['scheduled_iterations'] == 2
    assert goal['reached'] is reached


def test_step_rows_omit_global_iteration_only_bucket_but_keep_real_zero_steps():
    from types import SimpleNamespace
    def bucket(step, requests):
        return SimpleNamespace(scenario_step_id=step, metrics={"requests": requests, "iterations": 1}, bucket_started_at=START, bucket_seconds=5)
    version = SimpleNamespace(definition={"steps": [{"id": "read", "name": "读取配置"}, {"id": "cleanup", "name": "清理"}]})
    buckets = [bucket("all", 0), bucket("read", 2), bucket("cleanup", 0)]
    rows = LoadReportService._steps(buckets, version)
    assert {row["id"] for row in rows} == {"read", "cleanup"}
    # Legacy agents may place real HTTP evidence in the global bucket.
    assert LoadReportService._steps([bucket("all", 2)], version)[0]["requests"] == 2
