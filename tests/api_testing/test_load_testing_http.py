"""User-facing HTTP contracts for performance scenarios, runs, reports and Agents."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from task_server.api_testing import access
from task_server.api_testing.load_testing_http import _agent_view, _prepare_run_connectivity, handle_load_testing_request
from task_server.api_testing.models.environment import ApiEnvironment, ApiEnvironmentRevision, ApiEnvironmentService
from task_server.api_testing.models.load_testing import (
    ApiLoadAgent,
    ApiLoadEvent,
    ApiLoadRun,
    ApiLoadRunShard,
    ApiLoadScenario,
    ApiLoadScenarioVersion,
)
from task_server.api_testing.models.project import ApiProject
from task_server.api_testing.services.load_run_service import LoadRunError
from tests.api_testing.test_load_testing_repository import load_factory


def _audit(actor="owner"):
    return {"owner_id": actor, "created_by": actor, "updated_by": actor}


def _definition(name="搜索模型"):
    return {
        "name": name,
        "description": "核心查询",
        "mode": "single_interface",
        "steps": [{
            "id": "search", "name": "搜索", "scope": "iteration", "action": "http_request",
            "request": {"method": "GET", "path": "/search", "service": "default", "path_params": {}, "query": {}, "headers": {}, "cookies": {}, "body": None},
            "assertions": [{"type": "status_code", "operator": "equals", "expected": 200, "enabled": True}],
            "extractions": [], "sleep_ms": 0, "side_effect": "readonly",
        }],
        "dataset_contract": {"dataset_id": None, "usage_mode": "cycle", "variables": []},
        "risk": {"level": "low", "ownership_variable": None, "notes": ""},
        "source_snapshot": {"type": "manual", "version_ids": [], "items": []},
    }


@pytest.fixture()
def users(monkeypatch):
    common = {"status": "active", "must_change_password": False, "is_superuser": False, "scope": {"api_projects": "*", "api_environments": "*"}}
    profiles = {
        "viewer": {**common, "permissions": ["api.view", "api.loadtest.view"]},
        "editor": {**common, "permissions": ["api.view", "api.loadtest.view", "api.loadtest.edit"]},
        "runner": {**common, "permissions": ["api.view", "api.execute", "api.loadtest.view", "api.loadtest.execute"]},
        "manager": {**common, "permissions": ["api.view", "api.loadtest.view", "api.loadtest.manage_agents"]},
        "notifier": {**common, "permissions": ["api.view", "api.loadtest.view", "platform.notify"]},
        "other": {**common, "permissions": ["api.view", "api.loadtest.view"], "scope": {"api_projects": [], "api_environments": []}},
    }
    monkeypatch.setattr(access, "get_access_profile", lambda actor: profiles.get(actor))
    return profiles


@pytest.fixture()
def catalog(load_factory):
    suffix = uuid4().hex[:10]
    with load_factory.begin() as session:
        project = ApiProject(name="性能项目", slug=f"performance-{suffix}", **_audit())
        session.add(project)
        session.flush()
        environment = ApiEnvironment(project_id=project.id, name="测试环境", **_audit())
        session.add(environment)
        session.flush()
        revision = ApiEnvironmentRevision(environment_id=environment.id, revision_number=1, name="测试环境 v1", **_audit())
        session.add(revision)
        session.flush()
        environment.active_revision_id = revision.id
        return {"project": project, "environment": environment, "revision": revision}


def _call(load_factory, method, path, actor, payload=None, query=None):
    segments = tuple(part for part in path.strip("/").split("/") if part)
    return handle_load_testing_request(method, segments, query or {}, payload or {}, actor, load_factory)


def test_scenario_version_lifecycle_and_direct_reads_enforce_scope(load_factory, catalog, users):
    with pytest.raises(access.AccessDeniedError):
        _call(load_factory, "POST", "/load-scenarios", "viewer", {"project_id": catalog["project"].id, "name": "越权创建", "scenario_type": "single_interface"})

    created, status = _call(load_factory, "POST", "/load-scenarios", "editor", {
        "project_id": catalog["project"].id,
        "name": "模型搜索核心链路",
        "scenario_type": "single_interface",
        "description": "新人可直接复用",
    })
    scenario_id = created["scenario"]["id"]
    assert status == 201
    versioned, status = _call(load_factory, "POST", f"/load-scenarios/{scenario_id}/versions", "editor", {"definition": _definition()})
    assert status == 201
    version_id = versioned["version"]["id"]
    assert versioned["version"]["validation_summary"]["accepted"] is True

    listing, _ = _call(load_factory, "GET", "/load-scenarios", "viewer", query={"project_id": catalog["project"].id})
    assert listing["scenarios"][0]["active_version_id"] == version_id
    direct, _ = _call(load_factory, "GET", f"/load-scenarios/{scenario_id}", "viewer")
    assert direct["scenario"]["name"] == "模型搜索核心链路"
    with pytest.raises(access.AccessDeniedError):
        _call(load_factory, "GET", f"/load-scenarios/{scenario_id}", "other")


def test_agent_management_separates_view_and_enrollment_secret(load_factory, users):
    with pytest.raises(access.AccessDeniedError):
        _call(load_factory, "POST", "/load-agent-enrollments", "viewer", {"name": "专用节点", "scheduling_tier": "preferred"})
    enrolled, status = _call(load_factory, "POST", "/load-agent-enrollments", "manager", {
        "name": "专用节点", "node_group": "腾讯云", "scheduling_tier": "preferred", "expires_in_seconds": 600,
    })
    assert status == 201
    assert enrolled["enrollment"]["token"]
    assert enrolled["enrollment"]["credential_notice"] == "注册令牌仅显示一次，请立即保存"

    listing, _ = _call(load_factory, "GET", "/load-agent-enrollments", "manager")
    assert listing["enrollments"][0]["id"] == enrolled["enrollment"]["id"]
    assert "token" not in listing["enrollments"][0]


def test_agent_calibration_action_requires_management_permission(load_factory, users, monkeypatch):
    agent_id = str(uuid4())
    requested = SimpleNamespace(
        id=agent_id, name="专用节点", status="online", scheduling_tier="preferred",
        node_group="腾讯云", labels={}, agent_version="1.0.0", k6_version="0.52.0",
        hard_limits={}, soft_limits={}, current_usage={},
        health={"calibration": {"state": "calibrating"}, "pending_command": {"type": "calibrate", "id": "command-1"}},
        egress_ip="", last_heartbeat_at=None, offline_reason="",
    )
    monkeypatch.setattr(
        "task_server.api_testing.load_testing_http.LoadAgentService.request_calibration",
        lambda _self, requested_id, actor: requested if requested_id == agent_id and actor == "manager" else None,
    )

    with pytest.raises(access.AccessDeniedError):
        _call(load_factory, "POST", f"/load-agents/{agent_id}/calibrate", "viewer")
    result, status = _call(load_factory, "POST", f"/load-agents/{agent_id}/calibrate", "manager")

    assert status == 202
    assert result["agent"]["calibration_state"] == "calibrating"


def test_stale_agent_view_ends_false_online_and_calibrating_states():
    item = SimpleNamespace(
        id="stale-agent", name="离线专用节点", status="online", scheduling_tier="preferred",
        node_group="腾讯云", labels={}, agent_version="0.1.1", k6_version="0.52.0",
        hard_limits={}, soft_limits={}, current_usage={}, egress_ip="",
        last_heartbeat_at=datetime.now(timezone.utc) - timedelta(seconds=60), offline_reason="",
        health={"schedulable": False, "calibration": {"state": "calibrating"}, "pending_command": {"type": "calibrate"}},
    )

    view = _agent_view(item)

    assert view["status"] == "offline"
    assert view["offline_reason"] == "heartbeat_timeout"
    assert view["calibration_state"] == "failed"
    assert "心跳超时" in view["health"]["calibration"]["message"]


def test_run_connectivity_uses_selected_shards_and_environment_service_targets(load_factory, catalog, users, monkeypatch):
    with load_factory.begin() as session:
        scenario = ApiLoadScenario(project_id=catalog["project"].id, name="目标检查", scenario_type="single_interface", **_audit())
        session.add(scenario)
        session.flush()
        version = ApiLoadScenarioVersion(scenario_id=scenario.id, version_number=1, definition=_definition(), source_snapshot={}, validation_summary={"accepted": True}, compiler_version="k6-safe-v1", content_hash="c" * 64, **_audit())
        session.add(version)
        agent = ApiLoadAgent(name="北京专用节点", status="online", scheduling_tier="preferred", node_group="北京", credential_hash="secret", agent_version="1", k6_version="1", hard_limits={}, soft_limits={}, current_usage={}, health={}, **_audit())
        session.add(agent)
        session.flush()
        run = ApiLoadRun(project_id=catalog["project"].id, scenario_version_id=version.id, environment_revision_id=catalog["revision"].id, load_model="constant-vus", queue_priority="normal", configuration={}, state="draft", **_audit())
        session.add(run)
        session.flush()
        session.add(ApiLoadRunShard(run_id=run.id, agent_id=agent.id, sequence=1, global_sequence=1, allocation={}, state="assigned", **_audit()))
        session.add(ApiEnvironmentService(revision_id=catalog["revision"].id, service_name="default", module_name="主服务", base_url="https://api.example.test:8443/base", metadata_json={}, **_audit()))
        session.flush()
        run_id, agent_id = run.id, agent.id

    captured = []
    monkeypatch.setattr(
        "task_server.api_testing.load_testing_http.LoadAgentService.request_target_connectivity",
        lambda _self, requested_agent, revision_id, targets, actor: captured.append((requested_agent, revision_id, targets, actor)) or SimpleNamespace(id=requested_agent),
    )
    agents = _prepare_run_connectivity(load_factory, run_id, "runner")

    assert agents[0].id == agent_id
    assert captured == [(agent_id, catalog["revision"].id, [{"name": "主服务", "host": "api.example.test", "port": 8443, "tls": True}], "runner")]


def test_run_read_events_report_ai_and_actions_use_separate_permissions(load_factory, catalog, users, monkeypatch):
    now = datetime(2026, 9, 3, 16, 0, tzinfo=timezone.utc)
    with load_factory.begin() as session:
        scenario = ApiLoadScenario(project_id=catalog["project"].id, name="搜索链路", scenario_type="single_interface", **_audit())
        session.add(scenario)
        session.flush()
        version = ApiLoadScenarioVersion(scenario_id=scenario.id, version_number=1, definition=_definition(), source_snapshot={}, validation_summary={"accepted": True}, compiler_version="k6-safe-v1", content_hash="x" * 64, **_audit())
        session.add(version)
        session.flush()
        scenario.active_version_id = version.id
        run = ApiLoadRun(project_id=catalog["project"].id, scenario_version_id=version.id, environment_revision_id=catalog["revision"].id, load_model="constant-vus", queue_priority="normal", configuration={"scenario": {"name": "搜索链路"}}, state="queued", **_audit())
        session.add(run)
        session.flush()
        event = ApiLoadEvent(run_id=run.id, sequence=1, event_type="run.queued", payload={"message": "等待节点"}, **_audit())
        session.add(event)
        session.flush()
        run_id = run.id

    class FakeRunService:
        def start(self, requested_id, actor):
            assert actor == "runner" and requested_id == run_id
            with load_factory.begin() as session:
                row = session.get(ApiLoadRun, run_id)
                row.state = "starting"
                return row

        def stop(self, requested_id, reason, actor):
            assert actor == "runner" and reason == "人工停止"
            with load_factory.begin() as session:
                row = session.get(ApiLoadRun, run_id)
                row.state = "cancelled"
                row.verdict = "inconclusive"
                return row

    monkeypatch.setattr("task_server.api_testing.load_testing_http._run_service", lambda _factory: FakeRunService())
    monkeypatch.setattr("task_server.api_testing.load_testing_http.LoadReportService.build", lambda _self, requested_id, actor: {"run_id": requested_id, "verdict": "inconclusive", "actor": actor})
    monkeypatch.setattr("task_server.api_testing.load_testing_http.LoadAiAnalysisService.request", lambda _self, requested_id, actor, force=False: SimpleNamespace(id="analysis-1", run_id=requested_id, state="queued", evidence_hash="e" * 64, model="平台自动路由", prompt_version="api-load-analysis.v1", result={}, error=""))
    monkeypatch.setattr("task_server.api_testing.load_testing_http.NotificationService.send_load_test_report", lambda _self, requested_id, actor: SimpleNamespace(run_id=requested_id, channel_type="feishu", sent=True, message="性能测试飞书报告已发"))
    connectivity_agent = SimpleNamespace(
        id="agent-connectivity", name="专用节点", status="online", scheduling_tier="preferred", node_group="腾讯云",
        labels={}, agent_version="1.0.0", k6_version="0.52.0", hard_limits={}, soft_limits={}, current_usage={},
        health={"pending_command": {"type": "target_connectivity"}, "calibration": {"state": "valid"}},
        egress_ip="", last_heartbeat_at=None, offline_reason="",
    )
    monkeypatch.setattr(
        "task_server.api_testing.load_testing_http._prepare_run_connectivity",
        lambda factory, requested_id, actor: [connectivity_agent] if requested_id == run_id and actor == "runner" else [],
    )

    detail, _ = _call(load_factory, "GET", f"/load-runs/{run_id}", "viewer")
    assert detail["run"]["id"] == run_id
    events, _ = _call(load_factory, "GET", f"/load-runs/{run_id}/events", "viewer", query={"after": "0"})
    assert events["events"][0]["type"] == "run.queued"
    report, _ = _call(load_factory, "GET", f"/load-runs/{run_id}/report", "viewer")
    assert report["report"]["verdict"] == "inconclusive"
    analysis, status = _call(load_factory, "POST", f"/load-runs/{run_id}/ai-analysis", "viewer", {"force": True})
    assert status == 202 and analysis["analysis"]["state"] == "queued"
    notified, status = _call(load_factory, "POST", f"/load-runs/{run_id}/notify", "notifier")
    assert status == 200 and notified["notification"]["message"] == "性能测试飞书报告已发"
    with pytest.raises(access.AccessDeniedError):
        _call(load_factory, "POST", f"/load-runs/{run_id}/start", "viewer")
    connectivity, status = _call(load_factory, "POST", f"/load-runs/{run_id}/connectivity", "runner")
    assert status == 202
    assert connectivity["agents"][0]["health"]["pending_command"]["type"] == "target_connectivity"
    started, _ = _call(load_factory, "POST", f"/load-runs/{run_id}/start", "runner")
    assert started["run"]["state"] == "starting"
    stopped, _ = _call(load_factory, "POST", f"/load-runs/{run_id}/stop", "runner", {"reason": "人工停止"})
    assert stopped["run"]["state"] == "cancelled"


def test_load_route_is_mounted_once_before_generic_not_found():
    source = open("task_server/api_testing/http.py", encoding="utf-8").read()
    assert "dispatch_load_testing_request" in source
    assert source.index("dispatch_load_testing_request") < source.index("def _route(")


def test_completed_run_persists_deterministic_verdict_and_queues_ai(load_factory, catalog, users, monkeypatch):
    from task_server.api_testing import tasks

    with load_factory.begin() as session:
        scenario = ApiLoadScenario(project_id=catalog["project"].id, name="完成链路", scenario_type="single_interface", **_audit())
        session.add(scenario)
        session.flush()
        version = ApiLoadScenarioVersion(scenario_id=scenario.id, version_number=1, definition=_definition(), source_snapshot={}, validation_summary={"accepted": True}, compiler_version="k6-safe-v1", content_hash="z" * 64, **_audit())
        session.add(version)
        session.flush()
        run = ApiLoadRun(project_id=catalog["project"].id, scenario_version_id=version.id, environment_revision_id=catalog["revision"].id, load_model="constant-vus", queue_priority="normal", configuration={"scenario": {"name": "完成链路"}}, state="finished", **_audit())
        session.add(run)
        session.flush()
        run_id = run.id

    report = {"run_id": run_id, "verdict": "passed", "verdict_label": "通过", "evidence": {"complete": True}}
    queued = []
    monkeypatch.setattr(tasks, "_session_factory", lambda: load_factory)
    monkeypatch.setattr(tasks.LoadReportService, "build", lambda _self, requested_id, actor: report)
    monkeypatch.setattr(tasks.LoadAiAnalysisService, "request", lambda _self, requested_id, actor, force=False: SimpleNamespace(id="analysis-auto", state="queued"))
    monkeypatch.setattr(tasks.analyze_load_report, "delay", lambda analysis_id: queued.append(analysis_id))

    assert tasks.finalize_load_run.run(run_id) == "queued"
    assert queued == ["analysis-auto"]
    with load_factory() as session:
        persisted = session.get(ApiLoadRun, run_id)
        assert persisted.verdict == "passed"
        assert persisted.summary["deterministic_report"] == {"verdict": "passed", "evidence_complete": True}


def test_repeated_http_start_returns_the_existing_running_task(load_factory, catalog, users, monkeypatch):
    with load_factory.begin() as session:
        scenario = ApiLoadScenario(project_id=catalog["project"].id, name="幂等链路", scenario_type="single_interface", **_audit())
        session.add(scenario)
        session.flush()
        version = ApiLoadScenarioVersion(scenario_id=scenario.id, version_number=1, definition=_definition(), source_snapshot={}, validation_summary={"accepted": True}, compiler_version="k6-safe-v1", content_hash="i" * 64, **_audit())
        session.add(version)
        session.flush()
        run = ApiLoadRun(project_id=catalog["project"].id, scenario_version_id=version.id, environment_revision_id=catalog["revision"].id, load_model="constant-vus", queue_priority="normal", configuration={}, state="running", **_audit())
        session.add(run)
        session.flush()
        run_id = run.id

    class DuplicateStart:
        def start(self, _run_id, _actor):
            raise LoadRunError("任务已经启动，请勿重复点击", status=409, code="duplicate_start")

    monkeypatch.setattr("task_server.api_testing.load_testing_http._run_service", lambda _factory: DuplicateStart())

    result, status = _call(load_factory, "POST", f"/load-runs/{run_id}/start", "runner")
    assert status == 200
    assert result["run"]["state"] == "running"
