"""Durable run orchestration, capacity gates, and restart recovery."""

from datetime import datetime, timedelta, timezone
import os

import pytest
from sqlalchemy import select

from task_server.api_testing import access
from task_server.api_testing.models.environment import ApiEnvironment, ApiEnvironmentRevision
from task_server.api_testing.models.load_testing import (
    ApiLoadAgent,
    ApiLoadRun,
    ApiLoadRunShard,
    ApiLoadScenario,
    ApiLoadScenarioVersion,
)
from task_server.api_testing.models.project import ApiProject
from task_server.api_testing.services.load_preflight_service import PreflightResult
from task_server.api_testing.services.load_run_service import LoadRunError, LoadRunService
from tests.api_testing.test_load_testing_repository import load_factory


NOW = datetime(2026, 9, 3, 14, 0, tzinfo=timezone.utc)


def _audit(actor="owner"):
    return {"owner_id": actor, "created_by": actor, "updated_by": actor}


def _definition():
    return {
        "name": "模型搜索核心链路",
        "description": "查询、结果和详情",
        "mode": "single_interface",
        "steps": [{
            "id": "search",
            "name": "搜索模型",
            "scope": "iteration",
            "action": "http_request",
            "request": {"method": "GET", "path": "/search", "service": "default", "path_params": {}, "query": {}, "headers": {}, "cookies": {}, "body": None},
            "assertions": [{"type": "status_code", "operator": "equals", "expected": 200, "enabled": True}],
            "extractions": [],
            "sleep_ms": 0,
            "side_effect": "readonly",
        }],
        "dataset_contract": {"dataset_id": None, "usage_mode": "cycle", "variables": []},
        "risk": {"level": "low", "ownership_variable": None, "notes": ""},
        "source_snapshot": {"type": "manual", "version_ids": [], "items": []},
    }


def _calibration(agent_version="1.0.0", k6_version="0.52.0", *, valid_until="2099-09-10T00:00:00+00:00"):
    return {
        "id": "calibration-20260903",
        "state": "valid",
        "calibrated_at": "2026-09-03T12:00:00+00:00",
        "valid_until": valid_until,
        "agent_version": agent_version,
        "k6_version": k6_version,
        "hardware_signature": "4c-8g-linux",
        "max_vus": 100,
        "max_iterations_per_second": 200,
    }


@pytest.fixture()
def run_records(load_factory, monkeypatch):
    monkeypatch.setattr(access, "get_access_profile", lambda _actor: None)
    suffix = os.urandom(5).hex()
    with load_factory.begin() as session:
        project = ApiProject(name="load " + suffix, slug="load-" + suffix, **_audit())
        session.add(project)
        session.flush()
        environment = ApiEnvironment(project_id=project.id, name="性能环境", **_audit())
        session.add(environment)
        session.flush()
        revision = ApiEnvironmentRevision(environment_id=environment.id, revision_number=3, name="性能环境 v3", **_audit())
        session.add(revision)
        session.flush()
        environment.active_revision_id = revision.id
        scenario = ApiLoadScenario(project_id=project.id, name="模型搜索核心链路", scenario_type="single_interface", **_audit())
        session.add(scenario)
        session.flush()
        version = ApiLoadScenarioVersion(
            scenario_id=scenario.id,
            version_number=2,
            definition=_definition(),
            source_snapshot={"type": "manual"},
            validation_summary={"accepted": True},
            compiler_version="k6-safe-v1",
            content_hash="scenario-content-hash",
            **_audit(),
        )
        session.add(version)
        session.flush()
        scenario.active_version_id = version.id
        agents = []
        for index, tier in enumerate(("preferred", "normal")):
            agent = ApiLoadAgent(
                name=f"专用压测节点{index + 1}-{suffix}",
                status="online",
                scheduling_tier=tier,
                agent_version="1.0.0",
                k6_version="0.52.0",
                credential_hash=(suffix + str(index + 1))[:64].ljust(64, str(index + 1)),
                hard_limits={"max_processes": 2, "max_vus": 100, "max_iterations_per_second": 200},
                soft_limits={"max_processes": 2, "max_vus": 80, "max_iterations_per_second": 120},
                current_usage={"processes": 0, "vus": 0, "iterations_per_second": 0},
                health={"schedulable": True, "calibration": _calibration()},
                **_audit("admin"),
            )
            session.add(agent)
            agents.append(agent)
        session.flush()
        return {"project": project, "environment": environment, "revision": revision, "scenario": scenario, "version": version, "agents": agents}


class _Preflight:
    def __init__(self, passed=True):
        self.passed = passed
        self.calls = []

    def run_once(self, definition, revision_id, agents):
        self.calls.append((definition["name"], revision_id, tuple(item.id for item in agents)))
        return PreflightResult(
            passed=self.passed,
            failure_code="" if self.passed else "functional_preflight_failed",
            message="预检通过" if self.passed else "业务断言失败",
            iteration_count=1,
            observed_duration_ms=250,
            cleanup_status="passed",
            steps=(),
            connectivity=tuple({"agent_id": item.id, "agent_name": item.name, "reachable": True} for item in agents),
        )


def _payload(records, **overrides):
    payload = {
        "scenario_version_id": records["version"].id,
        "environment_revision_id": records["revision"].id,
        "workload": {"executor": "constant-vus", "vus": 120, "duration_seconds": 60},
        "thresholds": {"http_error_rate": {"operator": "less_than", "value": 0.01, "required": True}},
        "priority": "high",
        "allocation_policy": {
            "allow_fallback": False,
            "allow_run_anyway": False,
            "agent_ids": [item.id for item in records["agents"]],
        },
    }
    payload.update(overrides)
    return payload


def _service(load_factory, preflight=None):
    return LoadRunService(load_factory, preflight_service=preflight or _Preflight(), now=lambda: NOW)


def test_create_freezes_versions_compiler_allocation_and_agent_calibration(load_factory, run_records):
    run = _service(load_factory).create(_payload(run_records), "owner")

    assert run.state == "draft"
    snapshot = run.configuration
    assert snapshot["scenario"]["version_id"] == run_records["version"].id
    assert snapshot["scenario"]["content_hash"] == "scenario-content-hash"
    assert snapshot["environment"] == {"revision_id": run_records["revision"].id, "name": "性能环境 v3"}
    assert snapshot["compiler"]["version"] == "k6-safe-v1"
    assert len(snapshot["agents"]) == 2
    assert snapshot["agents"][0]["calibration"]["id"] == "calibration-20260903"
    assert sum(item["allocation"]["vus"] for item in snapshot["agents"]) == 120
    with load_factory() as session:
        shards = tuple(session.scalars(select(ApiLoadRunShard).where(ApiLoadRunShard.run_id == run.id)))
    assert len(shards) == 2


@pytest.mark.parametrize("mutation,code", [
    (lambda agent: setattr(agent, "health", {**agent.health, "calibration": {"state": "missing"}}), "agent_calibration_invalid"),
    (lambda agent: setattr(agent, "health", {**agent.health, "calibration": _calibration(valid_until="2026-09-02T00:00:00+00:00")}), "agent_calibration_invalid"),
    (lambda agent: setattr(agent, "k6_version", "0.53.0"), "agent_calibration_invalid"),
])
def test_invalid_expired_or_version_mismatched_calibration_hard_blocks(load_factory, run_records, mutation, code):
    with load_factory.begin() as session:
        agent = session.get(ApiLoadAgent, run_records["agents"][0].id)
        mutation(agent)
    with pytest.raises(LoadRunError) as blocked:
        _service(load_factory).create(_payload(run_records), "owner")
    assert blocked.value.code == code
    assert "校准" in str(blocked.value)


def test_capacity_shortfall_requires_explicit_override_and_marks_verdict_inconclusive(load_factory, run_records):
    too_large = _payload(run_records, workload={"executor": "constant-vus", "vus": 250, "duration_seconds": 60})
    with pytest.raises(LoadRunError) as blocked:
        _service(load_factory).create(too_large, "owner")
    assert blocked.value.code == "capacity_shortfall"

    too_large["allocation_policy"]["allow_run_anyway"] = True
    run = _service(load_factory).create(too_large, "owner")
    assert run.verdict == "inconclusive"
    assert run.configuration["capacity"]["shortfall"] == 90


def test_preflight_failure_ends_run_and_success_queues_it(load_factory, run_records):
    failed_service = _service(load_factory, _Preflight(passed=False))
    failed = failed_service.create(_payload(run_records), "owner")
    failed = failed_service.preflight(failed.id, "owner")
    assert failed.state == "failed"
    assert failed.summary["preflight"]["message"] == "业务断言失败"

    good_service = _service(load_factory, _Preflight())
    good = good_service.create(_payload(run_records), "owner")
    good = good_service.preflight(good.id, "owner")
    assert good.state == "queued"
    assert good.configuration["capacity"]["estimated_vus_from_preflight"] == 1


def test_arrival_rate_preflight_blocks_when_observed_flow_needs_more_vus(load_factory, run_records):
    payload = _payload(
        run_records,
        workload={
            "executor": "constant-arrival-rate",
            "rate": 100,
            "time_unit": "1s",
            "duration_seconds": 60,
            "pre_allocated_vus": 10,
            "max_vus": 20,
        },
    )
    service = _service(load_factory)
    run = service.create(payload, "owner")
    checked = service.preflight(run.id, "owner")

    assert checked.state == "failed"
    assert checked.verdict == "inconclusive"
    assert checked.summary["preflight"]["failure_code"] == "preflight_capacity_shortfall"
    assert checked.configuration["capacity"]["estimated_vus_from_preflight"] == 25

    payload["allocation_policy"]["allow_run_anyway"] = True
    overridden = service.create(payload, "owner")
    overridden = service.preflight(overridden.id, "owner")
    assert overridden.state == "queued"
    assert overridden.verdict == "inconclusive"
    assert overridden.summary["preflight"]["warning_code"] == "preflight_capacity_shortfall"


def test_project_scope_and_production_permission_are_enforced(load_factory, run_records, monkeypatch):
    profiles = {
        "member": {
            "status": "active",
            "must_change_password": False,
            "is_superuser": False,
            "permissions": ["api.view", "api.loadtest.execute", "api.execute"],
            "scope": {
                "api_projects": [run_records["project"].id],
                "api_environments": [run_records["environment"].id],
            },
        },
        "outsider": {
            "status": "active",
            "must_change_password": False,
            "is_superuser": False,
            "permissions": ["api.view", "api.loadtest.execute", "api.execute", "api.production"],
            "scope": {"api_projects": [], "api_environments": []},
        },
    }
    monkeypatch.setattr(access, "get_access_profile", lambda actor: profiles.get(actor))
    with pytest.raises(access.AccessDeniedError):
        _service(load_factory).create(_payload(run_records), "outsider")

    with load_factory.begin() as session:
        session.get(ApiEnvironment, run_records["environment"].id).name = "生产环境"
    with pytest.raises(access.AccessDeniedError) as denied:
        _service(load_factory).create(_payload(run_records), "member")
    assert denied.value.permission == "api.production"

    profiles["member"]["permissions"].append("api.production")
    assert _service(load_factory).create(_payload(run_records), "member").state == "draft"


def test_all_agent_barrier_prevents_partial_start_and_duplicate_start(load_factory, run_records):
    service = _service(load_factory)
    run = service.preflight(service.create(_payload(run_records), "owner").id, "owner")

    pending = service.start(run.id, "owner")
    assert pending.state == "starting"
    first = service.claim_shard(run_records["agents"][0].id)
    assert first.state == "ready"
    assert service.start(run.id, "owner").state == "starting"
    service.claim_shard(run_records["agents"][1].id)
    with pytest.raises(LoadRunError) as duplicate:
        service.start(run.id, "owner")
    assert duplicate.value.code == "duplicate_start"


def test_stop_before_start_and_during_run_is_durable_and_idempotent(load_factory, run_records):
    service = _service(load_factory)
    before = service.create(_payload(run_records), "owner")
    cancelled = service.stop(before.id, "用户取消", "owner")
    assert cancelled.state == "cancelled"
    assert service.stop(before.id, "重复点击", "owner").state == "cancelled"

    running = service.preflight(service.create(_payload(run_records), "owner").id, "owner")
    service.start(running.id, "owner")
    service.claim_shard(run_records["agents"][0].id)
    service.claim_shard(run_records["agents"][1].id)
    stopping = service.stop(running.id, "人工停止", "owner")
    assert stopping.state == "stopping"
    assert stopping.stop_reason == "人工停止"
    with load_factory() as session:
        persisted = session.get(ApiLoadRun, running.id)
    assert persisted.state == "stopping"


def test_lost_shard_and_restart_recovery_do_not_silently_reallocate_pressure(load_factory, run_records):
    service = _service(load_factory)
    run = service.preflight(service.create(_payload(run_records), "owner").id, "owner")
    service.start(run.id, "owner")
    first = service.claim_shard(run_records["agents"][0].id)
    second = service.claim_shard(run_records["agents"][1].id)
    with load_factory.begin() as session:
        session.get(ApiLoadRunShard, first.id).state = "running"
        session.get(ApiLoadRunShard, first.id).last_heartbeat_at = NOW - timedelta(seconds=121)
        session.get(ApiLoadRunShard, second.id).state = "running"
        session.get(ApiLoadRunShard, second.id).last_heartbeat_at = NOW

    recovered = LoadRunService(load_factory, preflight_service=_Preflight(), now=lambda: NOW).recover_stale_runs(stale_after_seconds=120)
    assert recovered == (run.id,)
    with load_factory() as session:
        persisted = session.get(ApiLoadRun, run.id)
        shards = tuple(session.scalars(select(ApiLoadRunShard).where(ApiLoadRunShard.run_id == run.id)))
    assert persisted.state == "failed"
    assert persisted.verdict == "inconclusive"
    assert any(item.state == "lost" for item in shards)
    assert len(shards) == 2


def test_start_barrier_timeout_recovers_even_when_an_agent_never_claimed(load_factory, run_records):
    service = _service(load_factory)
    run = service.preflight(service.create(_payload(run_records), "owner").id, "owner")
    service.start(run.id, "owner")
    service.claim_shard(run_records["agents"][0].id)
    with load_factory.begin() as session:
        persisted = session.get(ApiLoadRun, run.id)
        persisted.updated_at = NOW - timedelta(seconds=121)

    recovered = service.recover_stale_runs(stale_after_seconds=120)

    assert recovered == (run.id,)
    with load_factory() as session:
        states = tuple(
            session.scalars(
                select(ApiLoadRunShard.state)
                .where(ApiLoadRunShard.run_id == run.id)
                .order_by(ApiLoadRunShard.sequence)
            )
        )
    assert states == ("lost", "lost")
