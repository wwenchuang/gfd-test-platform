"""HTTP boundary contracts for remote load Agents."""

import io
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from task_server.api_testing import access
from task_server.api_testing.models.load_testing import ApiLoadMetricBucket, ApiLoadRun, ApiLoadRunShard
from task_server.api_testing.repositories.load_testing_repository import LoadTestingRepository
from task_server.api_testing.services.load_agent_service import LoadAgentService
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
    scenario = repository.create_scenario(
        load_records["project"].id, "HTTP协议场景", "single_interface", "load-owner"
    )
    version = repository.create_scenario_version(
        scenario.id,
        {"steps": [{"id": "detail", "request": {"method": "GET", "path": "/detail"}}]},
        "compiler-v1",
        "load-owner",
    )
    run = repository.create_run(
        version.id,
        load_records["environment_revision"].id,
        {"executor": "constant-vus", "vus": 2},
        "load-owner",
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
    scenario = repository.create_scenario(
        load_records["project"].id, "HTTP指标场景", "single_interface", "load-owner"
    )
    version = repository.create_scenario_version(
        scenario.id,
        {"steps": [{"id": "search", "request": {"method": "GET", "path": "/search"}}]},
        "compiler-v1",
        "load-owner",
    )
    run = repository.create_run(
        version.id,
        load_records["environment_revision"].id,
        {"executor": "constant-vus", "vus": 1},
        "load-owner",
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
