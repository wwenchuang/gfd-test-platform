"""HTTP boundary contracts for remote load Agents."""

import io
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import delete, select

from task_server.api_testing import access
from task_server.api_testing.models.environment import ApiEnvironmentService
from task_server.api_testing.models.load_testing import ApiLoadMetricBucket, ApiLoadRun, ApiLoadRunShard
from task_server.api_testing.repositories.load_testing_repository import LoadTestingRepository
from task_server.api_testing.services.load_agent_service import LoadAgentService
from task_server.api_testing.services.load_scenario_compiler import compile_scenario
from task_server.api_testing import load_agent_http
from tests.api_testing.test_http_contract import http_client
from tests.api_testing.test_load_testing_repository import load_factory, load_records


AGENT_PREFIX = "/api/api-testing/load-agent/v1"
HARD_LIMITS = {
    "max_processes": 1,
    "max_vus": 100,
    "max_iterations_per_second": 500,
    "max_duration_seconds": 900,
    "cpu_cores": 2,
    "memory_mb": 1024,
}
CAPABILITIES = {
    "agent_version": "1.0.0",
    "k6_version": "0.52.0",
    "hard_limits": HARD_LIMITS,
    "labels": {},
}


@pytest.fixture()
def agent_http_context(load_factory, monkeypatch):
    profiles = {
        "admin": {
            "status": "active",
            "must_change_password": False,
            "is_superuser": False,
            "permissions": ["api.view", "api.loadtest.view", "api.loadtest.manage_agents"],
        }
    }
    monkeypatch.setattr(access, "get_access_profile", lambda actor: profiles.get(actor))
    monkeypatch.setattr(load_agent_http, "_factory", lambda: load_factory)
    monkeypatch.setattr(
        load_agent_http.ApiTestingSettings,
        "from_env",
        staticmethod(lambda: type("Settings", (), {"enabled": True})()),
    )
    return {"factory": load_factory, "service": LoadAgentService(load_factory)}


def _enrollment(context, name):
    return context["service"].create_enrollment(
        {"name": name, "scheduling_tier": "normal"}, "admin"
    )


def _register(http_client, context, name):
    enrollment = _enrollment(context, name)
    response = http_client.post(
        AGENT_PREFIX + "/register",
        {"enrollment_token": enrollment.token, "capabilities": CAPABILITIES},
    )
    assert response.status == 201, response.body
    return response.body["data"]


def _agent_auth(secret, scheme="Agent"):
    return {"Authorization": f"{scheme} {secret}"}


def _begin_start(repository, run):
    repository.transition_run(run.id, ("draft",), "preflighting")
    repository.transition_run(run.id, ("preflighting",), "queued")
    repository.transition_run(run.id, ("queued",), "starting")


def _definition(step_id, path):
    return {
        "name": "HTTP协议场景",
        "description": "Agent协议集成测试",
        "mode": "single_interface",
        "steps": [{
            "id": step_id,
            "name": step_id,
            "scope": "iteration",
            "action": "http_request",
            "request": {"method": "GET", "path": path, "service": "default", "path_params": {}, "query": {}, "headers": {}, "cookies": {}, "body": None},
            "assertions": [{"type": "status_code", "operator": "equals", "expected": 200, "enabled": True}],
            "extractions": [],
            "sleep_ms": 0,
            "side_effect": "readonly",
        }],
        "dataset_contract": {"dataset_id": None, "usage_mode": "cycle", "variables": []},
        "risk": {"level": "low", "ownership_variable": None, "notes": ""},
        "source_snapshot": {"type": "manual", "version_ids": [], "items": []},
    }


def _executable_run(repository, factory, records, scenario_name, step_id, path, workload):
    definition = _definition(step_id, path)
    compiled = compile_scenario(definition, workload)
    with factory.begin() as session:
        session.add(ApiEnvironmentService(
            revision_id=records["environment_revision"].id,
            service_name="default",
            module_name="test",
            base_url="https://api.example.test",
            metadata_json={},
            owner_id="load-owner",
            created_by="load-owner",
            updated_by="load-owner",
        ))
    scenario = repository.create_scenario(records["project"].id, scenario_name, "single_interface", "load-owner")
    version = repository.create_scenario_version(scenario.id, definition, "k6-safe-v1", "load-owner")
    run = repository.create_run(
        version.id,
        records["environment_revision"].id,
        {"workload": workload, "compiler": {"content_hash": compiled.content_hash}, "dataset": {}},
        "load-owner",
    )
    return run


def _metric_batch(batch_id, started_at, requests):
    return {
        "batch_id": batch_id,
        "buckets": [{
            "step_id": "search",
            "started_at": started_at,
            "bucket_seconds": 5,
            "metrics": {
                "requests": requests,
                "latency_histogram": {
                    "bounds_ms": [10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000],
                    "counts": [0, 0, requests, 0, 0, 0, 0, 0, 0, 0, 0],
                    "count": requests,
                    "sum_ms": requests * 40,
                    "max_ms": 45,
                },
            },
        }],
    }


def test_registration_is_one_time_and_browser_bearer_cannot_authenticate_agent(
    http_client, agent_http_context
):
    enrollment = _enrollment(agent_http_context, "HTTP节点一")
    payload = {"enrollment_token": enrollment.token, "capabilities": CAPABILITIES}
    registered = http_client.post(AGENT_PREFIX + "/register", payload)
    replay = http_client.post(AGENT_PREFIX + "/register", payload)

    assert registered.status == 201
    assert replay.status == 409
    assert replay.body["error"]["code"] == "enrollment_used"
    secret = registered.body["data"]["secret"]
    rejected = http_client.post(
        AGENT_PREFIX + "/heartbeat",
        {
            "agent_version": "1.0.0",
            "k6_version": "0.52.0",
            "hard_limits": HARD_LIMITS,
            "current_usage": {},
            "health": {},
        },
        _agent_auth(secret, "Bearer"),
    )
    assert rejected.status == 401
    assert rejected.body["error"]["code"] == "agent_unauthorized"

    missing = http_client.post(AGENT_PREFIX + "/heartbeat", {})
    assert missing.status == 401
    assert missing.body["error"]["code"] == "agent_unauthorized"

    agent_http_context["service"].update_agent(
        registered.body["data"]["agent"]["id"],
        {"scheduling_tier": "disabled"},
        "admin",
    )
    disabled = http_client.post(
        AGENT_PREFIX + "/heartbeat",
        {
            "agent_version": "1.0.0",
            "k6_version": "0.52.0",
            "hard_limits": HARD_LIMITS,
            "current_usage": {},
            "health": {},
        },
        _agent_auth(secret),
    )
    assert disabled.status == 403
    assert disabled.body["error"]["code"] == "agent_disabled"


def test_agent_payload_limit_is_enforced_before_json_decode(http_client, agent_http_context):
    class Handler:
        headers = {"Content-Length": "1000001"}
        wfile = io.BytesIO()
        status = None

        def _body(self):
            raise AssertionError("oversized payload must not be decoded")

        def send_response(self, status):
            self.status = status

        def _cors(self):
            pass

        def send_header(self, *_args):
            pass

        def end_headers(self):
            pass

    handler = Handler()
    load_agent_http.dispatch_load_agent_request(
        handler, "POST", AGENT_PREFIX + "/register", {}
    )
    body = json.loads(handler.wfile.getvalue())
    assert handler.status == 413
    assert body["error"]["code"] == "payload_too_large"


def test_agent_can_claim_and_update_only_its_own_shard(
    http_client, agent_http_context, load_records
):
    first = _register(http_client, agent_http_context, "HTTP分片节点一")
    second = _register(http_client, agent_http_context, "HTTP分片节点二")
    repository = LoadTestingRepository.from_factory(agent_http_context["factory"])
    run = _executable_run(
        repository, agent_http_context["factory"], load_records,
        "HTTP协议场景", "detail", "/detail",
        {"executor": "constant-vus", "vus": 2, "duration_seconds": 10},
    )
    shard = repository.create_shard(
        run.id, first["agent"]["id"], 0, {"vus": 2}, "load-owner"
    )
    repository.create_shard(
        run.id, second["agent"]["id"], 1, {"vus": 1}, "load-owner"
    )

    before_barrier = http_client.post(
        AGENT_PREFIX + "/claim", {}, _agent_auth(first["secret"])
    )
    assert before_barrier.body["data"]["shard"] is None
    _begin_start(repository, run)

    claimed = http_client.post(
        AGENT_PREFIX + "/claim", {}, _agent_auth(first["secret"])
    )
    assert claimed.status == 200
    assert claimed.body["data"]["shard"]["id"] == shard.id
    waiting = http_client.get(
        AGENT_PREFIX + f"/shards/{shard.id}/commands",
        _agent_auth(first["secret"]),
    )
    assert waiting.body["data"]["commands"] == []

    foreign = http_client.post(
        AGENT_PREFIX + f"/shards/{shard.id}/started",
        {"process_info": {"pid": 123}},
        _agent_auth(second["secret"]),
    )
    assert foreign.status == 403
    assert foreign.body["error"]["code"] == "shard_not_owned"
    foreign_finish = http_client.post(
        AGENT_PREFIX + f"/shards/{shard.id}/finish",
        {"state": "failed", "summary": {}, "error": {"message": "越权"}},
        _agent_auth(second["secret"]),
    )
    assert foreign_finish.status == 403
    assert foreign_finish.body["error"]["code"] == "shard_not_owned"

    second_claim = http_client.post(
        AGENT_PREFIX + "/claim", {}, _agent_auth(second["secret"])
    )
    assert second_claim.body["data"]["shard"] is not None
    released = http_client.get(
        AGENT_PREFIX + f"/shards/{shard.id}/commands",
        _agent_auth(first["secret"]),
    )
    assert released.body["data"]["commands"] == [{"type": "start"}]

    started = http_client.post(
        AGENT_PREFIX + f"/shards/{shard.id}/started",
        {"process_info": {"pid": 321}},
        _agent_auth(first["secret"]),
    )
    assert started.status == 200
    previous_heartbeat = datetime.now(timezone.utc) - timedelta(minutes=5)
    with agent_http_context["factory"].begin() as session:
        session.get(ApiLoadRunShard, shard.id).last_heartbeat_at = previous_heartbeat
    sample = http_client.post(
        AGENT_PREFIX + f"/shards/{shard.id}/samples",
        {
            "samples": [
                {
                    "step_id": "detail",
                    "kind": "http_error",
                    "payload": {"status_code": 503, "summary": "服务暂时不可用"},
                }
            ]
        },
        _agent_auth(first["secret"]),
    )
    event = http_client.post(
        AGENT_PREFIX + f"/shards/{shard.id}/events",
        {"events": [{"type": "k6_started", "payload": {"pid": 321}}]},
        _agent_auth(first["secret"]),
    )
    commands = http_client.get(
        AGENT_PREFIX + f"/shards/{shard.id}/commands",
        _agent_auth(first["secret"]),
    )
    assert sample.body["data"]["accepted"] == 1
    assert event.body["data"]["accepted"] == 1
    assert commands.body["data"]["commands"] == []
    with agent_http_context["factory"]() as session:
        persisted = session.get(ApiLoadRunShard, shard.id)
        assert persisted.state == "running"
        assert persisted.process_info == {"pid": 321}
        assert persisted.last_heartbeat_at > previous_heartbeat


def test_claim_rejects_environment_without_the_scenario_service(
    http_client, agent_http_context, load_records
):
    registered = _register(http_client, agent_http_context, "HTTP缺失服务节点")
    repository = LoadTestingRepository.from_factory(agent_http_context["factory"])
    run = _executable_run(
        repository,
        agent_http_context["factory"],
        load_records,
        "HTTP缺失服务场景",
        "detail",
        "/detail",
        {"executor": "constant-vus", "vus": 1, "duration_seconds": 10},
    )
    repository.create_shard(
        run.id, registered["agent"]["id"], 0, {"vus": 1}, "load-owner"
    )
    with agent_http_context["factory"].begin() as session:
        session.execute(
            delete(ApiEnvironmentService).where(
                ApiEnvironmentService.revision_id == load_records["environment_revision"].id
            )
        )
    _begin_start(repository, run)

    response = http_client.post(
        AGENT_PREFIX + "/claim", {}, _agent_auth(registered["secret"])
    )

    assert response.status == 409
    assert response.body["error"]["code"] == "shard_environment_unavailable"
    assert "default" in response.body["error"]["message"]


def test_claim_ignores_unresolved_services_not_used_by_the_scenario(
    http_client, agent_http_context, load_records
):
    registered = _register(http_client, agent_http_context, "HTTP无关服务节点")
    repository = LoadTestingRepository.from_factory(agent_http_context["factory"])
    run = _executable_run(
        repository,
        agent_http_context["factory"],
        load_records,
        "HTTP只使用默认服务场景",
        "detail",
        "/detail",
        {"executor": "constant-vus", "vus": 1, "duration_seconds": 10},
    )
    repository.create_shard(
        run.id, registered["agent"]["id"], 0, {"vus": 1}, "load-owner"
    )
    with agent_http_context["factory"].begin() as session:
        session.add(
            ApiEnvironmentService(
                revision_id=load_records["environment_revision"].id,
                service_name="unused-apifox-service",
                module_name="unused",
                base_url="{{UNUSED_APIFOX_BASE_URL}}",
                metadata_json={"unresolved": True},
                owner_id="load-owner",
                created_by="load-owner",
                updated_by="load-owner",
            )
        )
    _begin_start(repository, run)

    response = http_client.post(
        AGENT_PREFIX + "/claim", {}, _agent_auth(registered["secret"])
    )

    assert response.status == 200
    assert response.body["data"]["shard"]["id"]


def test_duplicate_metrics_replace_the_bucket_and_finish_is_idempotent(
    http_client, agent_http_context, load_records, monkeypatch
):
    completions = []
    monkeypatch.setattr(
        "task_server.api_testing.load_agent_http._dispatch_load_completion",
        lambda run_id: completions.append(run_id),
    )
    registered = _register(http_client, agent_http_context, "HTTP指标节点")
    repository = LoadTestingRepository.from_factory(agent_http_context["factory"])
    run = _executable_run(
        repository, agent_http_context["factory"], load_records,
        "HTTP指标场景", "search", "/search",
        {"executor": "constant-vus", "vus": 1, "duration_seconds": 10},
    )
    shard = repository.create_shard(
        run.id, registered["agent"]["id"], 0, {"vus": 1}, "load-owner"
    )
    auth = _agent_auth(registered["secret"])
    _begin_start(repository, run)
    assert http_client.post(AGENT_PREFIX + "/claim", {}, auth).status == 200
    http_client.post(
        AGENT_PREFIX + f"/shards/{shard.id}/started", {"process_info": {}}, auth
    )
    started_at = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc).isoformat()
    first = _metric_batch("batch-http-1", started_at, 10)
    second = _metric_batch("batch-http-2", started_at, 12)
    assert http_client.post(AGENT_PREFIX + f"/shards/{shard.id}/metrics", first, auth).status == 200
    assert http_client.post(AGENT_PREFIX + f"/shards/{shard.id}/metrics", second, auth).status == 200
    invalid_batch = http_client.post(
        AGENT_PREFIX + f"/shards/{shard.id}/metrics",
        {**_metric_batch("batch-http-invalid", started_at, 3),
            "buckets": [
                _metric_batch(
                    "unused",
                    datetime(2026, 9, 3, 12, 0, 5, tzinfo=timezone.utc).isoformat(),
                    3,
                )["buckets"][0],
                {**_metric_batch("unused-2", started_at, 1)["buckets"][0], "started_at": "not-a-time"},
            ]
        },
        auth,
    )
    assert invalid_batch.status == 422
    finished = http_client.post(
        AGENT_PREFIX + f"/shards/{shard.id}/finish",
        {"state": "finished", "summary": {"requests": 12}},
        auth,
    )
    repeated = http_client.post(
        AGENT_PREFIX + f"/shards/{shard.id}/finish",
        {"state": "finished", "summary": {"requests": 12}},
        auth,
    )

    assert finished.status == repeated.status == 200
    assert completions == [run.id]
    with agent_http_context["factory"]() as session:
        buckets = tuple(
            session.scalars(select(ApiLoadMetricBucket).where(ApiLoadMetricBucket.run_id == run.id))
        )
        persisted = session.get(ApiLoadRunShard, shard.id)
        persisted_run = session.get(ApiLoadRun, run.id)
        assert len(buckets) == 1
        assert buckets[0].metrics["requests"] == 12
        assert persisted.state == "finished"
        assert persisted.summary == {"requests": 12}
        assert persisted_run.state == "finished"


def test_unknown_agent_route_and_method_fail_closed(http_client, agent_http_context):
    registered = _register(http_client, agent_http_context, "HTTP路由节点")
    auth = _agent_auth(registered["secret"])
    missing = http_client.post(AGENT_PREFIX + "/unknown", {}, auth)
    wrong_method = http_client.get(AGENT_PREFIX + "/heartbeat", auth)
    assert missing.status == 404
    assert wrong_method.status == 405


@pytest.mark.parametrize(
    ("workload", "allocations", "expected"),
    [
        (
            {"executor": "constant-vus", "vus": 10, "duration_seconds": 30},
            [{"vus": 4}, {"vus": 6}],
            [{"vus": 4}, {"vus": 6}],
        ),
        (
            {
                "executor": "constant-arrival-rate",
                "rate": 100,
                "pre_allocated_vus": 20,
                "max_vus": 50,
                "duration_seconds": 30,
            },
            [{"rate": 40, "vus": 20}, {"rate": 60, "vus": 30}],
            [
                {"rate": 40, "pre_allocated_vus": 8, "max_vus": 20},
                {"rate": 60, "pre_allocated_vus": 12, "max_vus": 30},
            ],
        ),
        (
            {
                "executor": "constant-arrival-rate",
                "rate": 100,
                "pre_allocated_vus": 20,
                "max_vus": 50,
                "duration_seconds": 30,
            },
            [
                {"rate": 7, "vus": 4},
                {"rate": 13, "vus": 6},
                {"rate": 20, "vus": 10},
                {"rate": 25, "vus": 12},
                {"rate": 35, "vus": 18},
            ],
            [
                {"rate": 7, "pre_allocated_vus": 2, "max_vus": 4},
                {"rate": 13, "pre_allocated_vus": 2, "max_vus": 6},
                {"rate": 20, "pre_allocated_vus": 4, "max_vus": 10},
                {"rate": 25, "pre_allocated_vus": 5, "max_vus": 12},
                {"rate": 35, "pre_allocated_vus": 7, "max_vus": 18},
            ],
        ),
        (
            {
                "executor": "ramping-vus",
                "start_vus": 2,
                "stages": [
                    {"duration_seconds": 10, "target": 6},
                    {"duration_seconds": 20, "target": 10},
                ],
            },
            [{"vus": 4}, {"vus": 6}],
            [
                {"start_vus": 1, "stage_targets": [2, 4]},
                {"start_vus": 1, "stage_targets": [4, 6]},
            ],
        ),
        (
            {
                "executor": "ramping-arrival-rate",
                "start_rate": 20,
                "pre_allocated_vus": 20,
                "max_vus": 50,
                "stages": [
                    {"duration_seconds": 10, "target": 50},
                    {"duration_seconds": 20, "target": 100},
                ],
            },
            [{"rate": 40, "vus": 20}, {"rate": 60, "vus": 30}],
            [
                {
                    "start_rate": 8,
                    "stage_targets": [20, 40],
                    "pre_allocated_vus": 8,
                    "max_vus": 20,
                },
                {
                    "start_rate": 12,
                    "stage_targets": [30, 60],
                    "pre_allocated_vus": 12,
                    "max_vus": 30,
                },
            ],
        ),
    ],
)
def test_shard_workload_preserves_global_load_across_agents(
    workload, allocations, expected
):
    shards = [
        SimpleNamespace(id=f"shard-{index}", sequence=index, allocation=allocation)
        for index, allocation in enumerate(allocations)
    ]

    class Session:
        def scalars(self, _query):
            return shards

    run = SimpleNamespace(id="run-one")
    actual = [
        load_agent_http._shard_workload(Session(), run, shard, workload)
        for shard in shards
    ]

    for item, item_expected in zip(actual, expected):
        for key, value in item_expected.items():
            if key == "stage_targets":
                assert [stage["target"] for stage in item["stages"]] == value
            else:
                assert item[key] == value
