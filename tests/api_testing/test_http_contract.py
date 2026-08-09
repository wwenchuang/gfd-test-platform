import io
import json
import os
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest
import redis
from sqlalchemy.orm import sessionmaker

from task_server.api_testing import http
from task_server.api_testing.db import engine_for_url
from task_server.api_testing.events import ExecutionEvent, EventStream
from task_server.api_testing.models.case import ApiAiJob, ApiAiJobBatch, ApiCase, ApiCaseVersion
from task_server.api_testing.models.environment import ApiEnvironment, ApiEnvironmentRevision
from task_server.api_testing.models.execution import ApiExecution
from task_server.api_testing.models.project import ApiProject
from task_server.api_testing.models.source import ApiSource, ApiSourceEndpoint, ApiSourceRevision
from task_server.app import TaskHTTPHandler, ThreadingHTTPServer
from tests.api_testing.test_migrations import (
    _alembic_config,
    _create_test_schema,
    _database_url,
    _drop_test_schema,
    _without_database_environment,
)


def _audit(owner):
    return {"owner_id": owner, "created_by": owner, "updated_by": owner}


class HttpResponse:
    def __init__(self, response):
        self.status = response.status
        self.headers = dict(response.getheaders())
        raw = response.read()
        self.body = json.loads(raw.decode("utf-8")) if raw else None


class HttpClient:
    def __init__(self, port):
        self.port = port

    def get(self, path, headers=None):
        return self.request("GET", path, headers=headers)

    def post(self, path, payload=None, headers=None):
        return self.request("POST", path, json.dumps(payload or {}).encode("utf-8"), {"Content-Type": "application/json", **(headers or {})})

    def put(self, path, payload=None, headers=None):
        return self.request("PUT", path, json.dumps(payload or {}).encode("utf-8"), {"Content-Type": "application/json", **(headers or {})})

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.request(method, path, body=body, headers=headers or {})
        return HttpResponse(connection.getresponse())

    def open_stream(self, path, headers=None):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=3)
        connection.request("GET", path, headers=headers or {})
        return connection, connection.getresponse()


@pytest.fixture()
def http_client():
    server = ThreadingHTTPServer(("127.0.0.1", 0), TaskHTTPHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield HttpClient(server.server_port)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


@pytest.fixture()
def api_context(monkeypatch):
    database_url = _database_url()
    redis_url = os.getenv("TEST_REDIS_URL", "").strip()
    if not redis_url:
        pytest.skip("TEST_REDIS_URL is required for HTTP boundary tests")
    created_schemas = set()
    schema_name, schema_url = _create_test_schema(database_url, created_schemas)
    try:
        from alembic import command

        with _without_database_environment():
            command.upgrade(_alembic_config(schema_url), "head")
        factory = sessionmaker(bind=engine_for_url(schema_url), expire_on_commit=False)
        redis_client = redis.Redis.from_url(redis_url, decode_responses=True)
        redis_client.ping()
        redis_client.flushdb()
        monkeypatch.setattr(http, "_factory", lambda: factory)
        monkeypatch.setattr(http, "_event_stream", lambda _factory: EventStream(factory, redis_client))
        monkeypatch.setattr(http.ApiTestingSettings, "from_env", staticmethod(lambda: SimpleNamespace(enabled=True, redis_url=redis_url)))
        monkeypatch.setattr(http, "verify_session_token", lambda token: {"user": token} if token in {"owner-a", "owner-b"} else None)
        monkeypatch.setattr(http, "_enqueue_execution", lambda _execution_id: None)
        yield {"factory": factory, "redis": redis_client}
    finally:
        if "redis_client" in locals():
            redis_client.flushdb()
        _drop_test_schema(database_url, schema_name, created_schemas)


@pytest.fixture()
def owned_records(api_context):
    factory = api_context["factory"]
    with factory.begin() as session:
        first = ApiProject(name="owner a", slug="owner-a-project", **_audit("owner-a"))
        second = ApiProject(name="owner b", slug="owner-b-project", **_audit("owner-b"))
        session.add_all((first, second))
        session.flush()
        source = ApiSource(project_id=first.id, name="source", source_type="openapi", **_audit("owner-a"))
        session.add(source)
        session.flush()
        revision = ApiSourceRevision(source_id=source.id, revision_number=1, status="active", document_hash="a" * 64, normalized_document={"openapi": "3.0.0"}, **_audit("owner-a"))
        session.add(revision)
        session.flush()
        source.active_revision_id = revision.id
        endpoint = ApiSourceEndpoint(revision_id=revision.id, stable_key="b" * 64, operation_id="favoriteList", method="GET", path="/favorites", normalized_path="/favorites", operation={}, **_audit("owner-a"))
        environment = ApiEnvironment(project_id=first.id, source_id=source.id, name="env", **_audit("owner-a"))
        session.add_all((endpoint, environment))
        session.flush()
        environment_revision = ApiEnvironmentRevision(environment_id=environment.id, source_revision_id=revision.id, revision_number=1, name="env", **_audit("owner-a"))
        session.add(environment_revision)
        session.flush()
        case = ApiCase(project_id=first.id, endpoint_id=endpoint.id, name="case", origin="manual", **_audit("owner-a"))
        session.add(case)
        session.flush()
        version = ApiCaseVersion(case_id=case.id, endpoint_id=endpoint.id, version_number=1, purpose="case", request_template={"name": "case", "request": {}}, **_audit("owner-a"))
        session.add(version)
        session.flush()
        execution = ApiExecution(project_id=first.id, source_revision_id=revision.id, environment_revision_id=environment_revision.id, execution_type="debug", state="DONE", idempotency_key="seed", requested_case_ids=[version.id], request_snapshot={}, **_audit("owner-a"))
        session.add(execution)
        session.flush()
        second_execution = ApiExecution(project_id=first.id, source_revision_id=revision.id, environment_revision_id=environment_revision.id, execution_type="debug", state="DONE", idempotency_key="seed-2", requested_case_ids=[version.id], request_snapshot={}, **_audit("owner-a"))
        session.add(second_execution)
        session.flush()
        return {"project": first, "other_project": second, "source": source, "revision": revision, "endpoint": endpoint, "environment": environment, "environment_revision": environment_revision, "case": case, "version": version, "execution": execution, "second_execution": second_execution}


def _auth(actor="owner-a", **headers):
    return {"Authorization": f"Bearer {actor}", **headers}


def test_api_routes_require_existing_session(http_client):
    response = http_client.get("/api/api-testing/v1/projects")

    assert response.status == 401
    assert response.body["error"] == {"code": "unauthorized", "message": "Authentication is required", "details": {}}
    assert response.body["request_id"] == response.headers["X-Request-Id"]


def test_authenticated_reads_are_owner_scoped(http_client, owned_records):
    response = http_client.get("/api/api-testing/v1/projects", _auth())
    other = http_client.get(f"/api/api-testing/v1/executions/{owned_records['execution'].id}", _auth("owner-b"))
    endpoints = http_client.get(f"/api/api-testing/v1/endpoints?source_revision_id={owned_records['revision'].id}", _auth("owner-b"))

    assert [project["id"] for project in response.body["data"]["projects"]] == [owned_records["project"].id]
    assert other.status == endpoints.status == 404
    assert other.body["error"]["code"] == endpoints.body["error"]["code"] == "not_found"


def test_cross_owner_nested_write_is_hidden_not_validated(http_client, owned_records):
    response = http_client.post(f"/api/api-testing/v1/environments/{owned_records['environment'].id}/revisions", {}, _auth("owner-b"))

    assert response.status == 404
    assert response.body["error"]["code"] == "not_found"


def test_cross_owner_source_preview_is_hidden_before_document_validation(http_client, owned_records):
    response = http_client.post("/api/api-testing/v1/sources/preview", {"project_id": owned_records["project"].id}, _auth("owner-b"))

    assert response.status == 404
    assert response.body["error"]["code"] == "not_found"


def test_api_post_intercepts_invalid_and_oversized_content_length(http_client):
    malformed = http_client.request("POST", "/api/api-testing/v1/executions", b"", _auth(**{"Content-Length": "nope"}))
    oversized = http_client.request("POST", "/api/api-testing/v1/executions", b"", _auth(**{"Content-Length": str(1_000_001)}))

    assert malformed.status == 401
    assert malformed.body["error"]["code"] == "unauthorized"
    assert oversized.status == 401
    assert oversized.body["error"]["code"] == "unauthorized"


def test_authenticated_payload_limit_uuid_and_request_id(http_client, api_context):
    too_large = http_client.request("POST", "/api/api-testing/v1/projects", b"{}", _auth(**{"Content-Length": str(1_000_001)}))
    invalid = http_client.get("/api/api-testing/v1/executions/not-a-uuid", _auth(**{"X-Request-Id": "contract-42"}))

    assert too_large.status == 413
    assert too_large.body["error"]["code"] == "payload_too_large"
    assert invalid.status == 400
    assert invalid.body["error"]["code"] == "invalid_identifier"
    assert invalid.body["request_id"] == invalid.headers["X-Request-Id"] == "contract-42"


def test_environment_import_and_ai_ids_are_canonical_uuids(http_client, api_context):
    environment = http_client.post("/api/api-testing/v1/environments/import", {"project_id": "not-a-uuid"}, _auth())
    ai_job = http_client.post("/api/api-testing/v1/ai-jobs", {"endpoint_ids": ["not-a-uuid"], "environment_revision_id": "not-a-uuid"}, _auth())

    assert environment.status == ai_job.status == 400
    assert environment.body["error"]["code"] == ai_job.body["error"]["code"] == "invalid_identifier"


def test_authenticated_invalid_content_length_and_one_megabyte_boundary(http_client, api_context):
    malformed = http_client.request("POST", "/api/api-testing/v1/projects", b"", _auth(**{"Content-Length": "bad"}))
    exact_limit = b'{"name":"' + (b"x" * (1_000_000 - len(b'{"name":""}'))) + b'"}'
    accepted_size = http_client.request("POST", "/api/api-testing/v1/projects", exact_limit, _auth())

    assert malformed.status == 400
    assert malformed.body["error"]["code"] == "invalid_request"
    assert accepted_size.status != 413


def test_disabled_mode_is_stable_before_database_access(http_client, monkeypatch):
    monkeypatch.setattr(http.ApiTestingSettings, "from_env", staticmethod(lambda: SimpleNamespace(enabled=False, redis_url="redis://unreachable")))
    monkeypatch.setattr(http, "_factory", lambda: (_ for _ in ()).throw(AssertionError("database was opened")))
    monkeypatch.setattr(http, "verify_session_token", lambda token: {"user": "owner-a"} if token == "owner-a" else None)

    response = http_client.get("/api/api-testing/v1/projects", _auth())

    assert response.status == 503
    assert response.body["error"]["code"] == "api_testing_disabled"


def test_workspace_context_is_owner_scoped_and_persistent(http_client, owned_records):
    payload = {"project_id": owned_records["project"].id, "source_revision_id": owned_records["revision"].id, "environment_revision_id": owned_records["environment_revision"].id}
    saved = http_client.put("/api/api-testing/v1/workspace", payload, _auth())
    loaded = http_client.get("/api/api-testing/v1/workspace", _auth())
    denied = http_client.put("/api/api-testing/v1/workspace", payload, _auth("owner-b"))

    assert saved.status == loaded.status == 200
    assert loaded.body["data"]["workspace"] == payload
    assert denied.status == 404


def test_ai_job_status_is_queryable_and_owner_scoped(http_client, api_context, owned_records):
    with api_context["factory"].begin() as session:
        job = ApiAiJob(
            project_id=owned_records["project"].id,
            environment_revision_id=owned_records["environment_revision"].id,
            state="running",
            endpoint_ids=[owned_records["endpoint"].id],
            requested_model="qwen3.6-plus",
            actual_model="qwen3.6-plus",
            summary={"requested_provider_id": "qwen", "generated_drafts": 0},
            **_audit("owner-a"),
        )
        session.add(job)
        session.flush()
        session.add(ApiAiJobBatch(
            job_id=job.id,
            sequence=1,
            state="running",
            endpoint_ids=[owned_records["endpoint"].id],
            requested_model="qwen3.6-plus",
            actual_model="qwen3.6-plus",
            result={"draft_version_ids": [], "validation_errors": []},
            error={},
            **_audit("owner-a"),
        ))
        job_id = job.id

    accepted = http_client.get(f"/api/api-testing/v1/ai-jobs/{job_id}", _auth())
    denied = http_client.get(f"/api/api-testing/v1/ai-jobs/{job_id}", _auth("owner-b"))

    assert accepted.status == 200
    assert accepted.body["data"]["job"]["state"] == "running"
    assert accepted.body["data"]["job"]["batches"][0]["actual_model"] == "qwen3.6-plus"
    assert denied.status == 404


def test_ai_job_submission_enqueues_real_processing(http_client, owned_records, monkeypatch):
    queued = []
    monkeypatch.setattr(http, "_enqueue_ai_job", queued.append)

    response = http_client.post("/api/api-testing/v1/ai-jobs", {
        "endpoint_ids": [owned_records["endpoint"].id],
        "environment_revision_id": owned_records["environment_revision"].id,
        "intent": "覆盖收藏列表正向与鉴权失败",
    }, _auth())

    assert response.status == 200
    assert queued == [response.body["data"]["job"]["id"]]
    assert response.body["data"]["job"]["state"] == "queued"


def test_sse_ticket_is_reusable_for_eventsource_reconnect(http_client, api_context, owned_records):
    issue = http_client.post(f"/api/api-testing/v1/executions/{owned_records['execution'].id}/sse-ticket", {}, _auth())
    ticket = issue.body["data"]["ticket"]
    api_context["redis"].expire(http._ticket_key(ticket), 1)
    accepted = http_client.get(f"/api/api-testing/v1/executions/{owned_records['execution'].id}/events?ticket={ticket}")
    first_ttl = api_context["redis"].ttl(http._ticket_key(ticket))
    api_context["redis"].expire(http._ticket_key(ticket), 1)
    replay = http_client.get(f"/api/api-testing/v1/executions/{owned_records['execution'].id}/events?ticket={ticket}")
    second_ttl = api_context["redis"].ttl(http._ticket_key(ticket))

    assert issue.status == 200
    assert accepted.status == 200
    assert accepted.headers["Content-Type"].startswith("text/event-stream")
    assert replay.status == 200
    assert 1 < first_ttl <= http.SSE_TICKET_TTL_SECONDS
    assert 1 < second_ttl <= http.SSE_TICKET_TTL_SECONDS


def test_sse_ticket_cannot_be_redeemed_for_another_execution(http_client, owned_records):
    issue = http_client.post(f"/api/api-testing/v1/executions/{owned_records['execution'].id}/sse-ticket", {}, _auth())
    ticket = issue.body["data"]["ticket"]
    wrong_execution = http_client.get(f"/api/api-testing/v1/executions/{owned_records['second_execution'].id}/events?ticket={ticket}")

    assert wrong_execution.status == 401
    assert wrong_execution.body["error"]["code"] == "unauthorized"


def test_sse_ticket_reconnect_replays_only_new_durable_events(http_client, api_context, owned_records):
    execution_id = owned_records["execution"].id
    with api_context["factory"].begin() as session:
        session.get(ApiExecution, execution_id).state = "RUNNING"
    ticket = http_client.post(
        f"/api/api-testing/v1/executions/{execution_id}/sse-ticket", {}, _auth()
    ).body["data"]["ticket"]
    stream = EventStream(api_context["factory"], api_context["redis"])
    first_sequence = stream.append(execution_id, "progress", {"step": 1})

    first_connection, first_response = http_client.open_stream(
        f"/api/api-testing/v1/executions/{execution_id}/events?ticket={ticket}"
    )
    first_frame = b"".join(first_response.fp.readline() for _ in range(4))
    first_connection.close()

    second_sequence = stream.append(execution_id, "execution_finished", {"step": 2})
    with api_context["factory"].begin() as session:
        session.get(ApiExecution, execution_id).state = "DONE"
    _, second_response = http_client.open_stream(
        f"/api/api-testing/v1/executions/{execution_id}/events?ticket={ticket}",
        {"Last-Event-ID": str(first_sequence)},
    )
    second_body = second_response.read()

    assert first_response.status == second_response.status == 200
    assert first_frame == b'id: 1\nevent: progress\ndata: {"step":1}\n\n'
    assert first_sequence == 1
    assert second_sequence == 2
    assert second_body == b'id: 2\nevent: execution_finished\ndata: {"step":2}\n\n'


def test_sse_frames_resume_heartbeat_terminal_and_disconnect(monkeypatch):
    class Handler:
        headers = {"Last-Event-ID": "4"}

        def __init__(self):
            self.wfile = io.BytesIO()

        def send_response(self, _status):
            pass

        def _cors(self):
            pass

        def send_header(self, _name, _value):
            pass

        def end_headers(self):
            pass

    class Stream:
        def read(self, execution_id, after_id, block_ms):
            assert execution_id == "00000000-0000-0000-0000-000000000001"
            assert after_id == 4
            assert block_ms == 15_000
            return (ExecutionEvent(5, "execution_finished", {"state": "DONE"}, None),)
    class Service:
        def __init__(self, *_args, **_kwargs):
            pass

        def get(self, _execution_id):
            return SimpleNamespace(state="DONE")

    monkeypatch.setattr(http, "_factory", lambda: object())
    monkeypatch.setattr(http, "_event_stream", lambda _factory: Stream())
    monkeypatch.setattr(http, "ExecutionService", Service)
    monkeypatch.setattr(http, "_scope_execution", lambda *_args: SimpleNamespace(state="RUNNING"))
    handler = Handler()

    http._stream_events(handler, "00000000-0000-0000-0000-000000000001", "request", "owner-a")

    assert handler.wfile.getvalue() == b'id: 5\nevent: execution_finished\ndata: {"state":"DONE"}\n\n'


def test_sse_emits_heartbeat_and_closes_terminal_reconnect_without_blocking(monkeypatch):
    class Handler:
        headers = {"Last-Event-ID": "0"}

        def __init__(self):
            self.wfile = io.BytesIO()

        def send_response(self, _status):
            pass

        def _cors(self):
            pass

        def send_header(self, _name, _value):
            pass

        def end_headers(self):
            pass

    class Stream:
        reads = 0

        def read(self, _execution_id, _after_id, block_ms):
            self.reads += 1
            assert block_ms == 15_000
            return ()

    stream = Stream()
    states = iter(("RUNNING", "DONE"))
    monkeypatch.setattr(http, "_factory", lambda: object())
    monkeypatch.setattr(http, "_event_stream", lambda _factory: stream)
    monkeypatch.setattr(http, "_scope_execution", lambda *_args: SimpleNamespace(state=next(states)))
    handler = Handler()

    http._stream_events(handler, "00000000-0000-0000-0000-000000000001", "request", "owner-a")

    assert stream.reads == 1
    assert handler.wfile.getvalue() == b'event: heartbeat\ndata: {"request_id":"request"}\n\n'


def test_terminal_sse_reconnect_does_not_block(monkeypatch):
    class Handler:
        headers = {"Last-Event-ID": "99"}
        wfile = io.BytesIO()

        def send_response(self, _status):
            pass

        def _cors(self):
            pass

        def send_header(self, _name, _value):
            pass

        def end_headers(self):
            pass

    reads = []
    monkeypatch.setattr(http, "_factory", lambda: object())
    monkeypatch.setattr(http, "_scope_execution", lambda *_args: SimpleNamespace(state="CANCELLED"))
    monkeypatch.setattr(http, "_event_stream", lambda _factory: SimpleNamespace(read=lambda *_args: reads.append(_args[2]) or ()))

    http._stream_events(Handler(), "00000000-0000-0000-0000-000000000001", "request", "owner-a")

    assert reads == [0]


def test_sse_disconnect_stops_stream(monkeypatch):
    class BrokenWriter:
        def write(self, _value):
            raise BrokenPipeError()

        def flush(self):
            raise AssertionError("flush after disconnect")

    class Handler:
        headers = {"Last-Event-ID": "0"}
        wfile = BrokenWriter()

        def send_response(self, _status):
            pass

        def _cors(self):
            pass

        def send_header(self, _name, _value):
            pass

        def end_headers(self):
            pass

    monkeypatch.setattr(http, "_factory", lambda: object())
    monkeypatch.setattr(http, "_scope_execution", lambda *_args: SimpleNamespace(state="RUNNING"))
    monkeypatch.setattr(http, "_event_stream", lambda _factory: SimpleNamespace(read=lambda *_args: (ExecutionEvent(1, "log", {}, None),)))

    with pytest.raises(BrokenPipeError):
        http._stream_events(Handler(), "00000000-0000-0000-0000-000000000001", "request", "owner-a")
