import io
import json
import os
from datetime import datetime, timezone
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread
from types import SimpleNamespace

import pytest
import redis
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from task_server.api_testing import http
from task_server.api_testing.db import engine_for_url
from task_server.api_testing.events import ExecutionEvent, EventStream
from task_server.api_testing.models.case import ApiAiJob, ApiAiJobBatch, ApiBaseline, ApiCase, ApiCaseVersion
from task_server.api_testing.models.environment import ApiEnvironment, ApiEnvironmentRevision
from task_server.api_testing.models.execution import ApiExecution, ApiExecutionCase
from task_server.api_testing.models.project import ApiProject, ApiWorkspace
from task_server.api_testing.models.source import ApiSource, ApiSourceEndpoint, ApiSourceRevision
from task_server.app import TaskHTTPHandler, ThreadingHTTPServer
from task_server import router as task_router
from tests.api_testing.test_migrations import (
    _alembic_config,
    _create_test_schema,
    _database_url,
    _drop_test_schema,
    _without_database_environment,
)


def test_sse_event_timestamp_is_included_without_mutating_payload():
    class Handler:
        def __init__(self):
            self.wfile = io.BytesIO()

    payload = {"status": "PASSED"}
    handler = Handler()

    http._write_sse(
        handler,
        7,
        "case_finished",
        payload,
        datetime(2026, 8, 12, 7, 9, 38, tzinfo=timezone.utc),
    )

    frame = handler.wfile.getvalue().decode("utf-8")
    data = json.loads(next(line[6:] for line in frame.splitlines() if line.startswith("data: ")))
    assert data == {
        "status": "PASSED",
        "_event_created_at": "2026-08-12T07:09:38+00:00",
    }
    assert payload == {"status": "PASSED"}


def _audit(owner):
    return {"owner_id": owner, "created_by": owner, "updated_by": owner}


def test_default_event_stream_uses_configured_redis_wakeup(monkeypatch):
    redis_client = object()
    monkeypatch.setattr(
        http.ApiTestingSettings,
        "from_env",
        staticmethod(lambda: SimpleNamespace(redis_url="redis://redis.example/7")),
    )
    monkeypatch.setattr(http, "_shared_redis_client", lambda url: redis_client)

    stream = http._event_stream("factory")

    assert stream.redis is redis_client
    assert stream.session_factory == "factory"


def test_apifox_validation_error_keeps_its_actionable_chinese_message():
    from task_server.api_testing.adapters.openapi import OpenApiValidationError
    from task_server.api_testing.services.apifox_service import ApifoxInputError

    error = http._domain_error(ApifoxInputError("选择的 Apifox 环境不存在或已不可访问"))
    openapi_error = http._domain_error(OpenApiValidationError("Unresolved local reference: #/missing"))

    assert error.status == 422
    assert error.code == "apifox_validation_failed"
    assert error.message == "选择的 Apifox 环境不存在或已不可访问"
    assert openapi_error.status == 422
    assert openapi_error.code == "openapi_validation_failed"
    assert openapi_error.message == "接口定义校验失败：Unresolved local reference: #/missing"


def test_health_exposes_the_running_release_revision(http_client, monkeypatch):
    monkeypatch.setattr(task_router, "TASK_RELEASE_REVISION", "release-test-sha")

    response = http_client.get("/api/health")

    assert response.status == 200
    assert response.body["release_revision"] == "release-test-sha"


def test_case_payload_error_keeps_actionable_assertion_feedback():
    from task_server.api_testing.contracts.case import CasePayloadError

    error = http._domain_error(
        CasePayloadError(
            "assertions[0] expected must contain valid HTTP status code values"
        )
    )

    assert error.status == 422
    assert error.code == "case_validation_failed"
    assert "HTTP 状态码" in error.message
    assert "响应 JSON 字段" in error.message


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

    def delete(self, path, headers=None):
        return self.request("DELETE", path, headers=headers)

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
        version = ApiCaseVersion(
            case_id=case.id,
            endpoint_id=endpoint.id,
            version_number=1,
            purpose="case",
            request_template={
                "name": "case",
                "app_package": "com.kfb.model",
                "app_name": "智小白3D",
                "business": "home",
                "request": {},
            },
            **_audit("owner-a"),
        )
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
    assert response.headers["Cache-Control"] == "no-store"


def test_baseline_assertion_audit_is_owner_scoped(
    http_client, owned_records, monkeypatch
):
    calls = []

    def fake_list(_service, project_id, actor_id):
        calls.append((project_id, actor_id))
        return {
            "summary": {
                "total": 1,
                "verified": 0,
                "upgrade_available": 1,
                "http_failure": 0,
                "business_failure": 0,
                "domain_assertion_required": 0,
                "evidence_missing": 0,
                "needs_review": 1,
                "safe_review": 1,
            },
            "items": [],
        }

    monkeypatch.setattr(http.BaselineAssertionAuditService, "list", fake_list)
    project_id = owned_records["project"].id

    response = http_client.get(
        f"/api/api-testing/v1/baselines/assertion-audit?project_id={project_id}",
        headers=_auth(),
    )
    forbidden = http_client.get(
        f"/api/api-testing/v1/baselines/assertion-audit?project_id={project_id}",
        headers=_auth("owner-b"),
    )

    assert response.status == 200
    assert response.body["data"]["summary"]["upgrade_available"] == 1
    assert calls == [(project_id, "owner-a")]
    assert forbidden.status == 404


def test_workflow_step_preview_is_owner_scoped_and_returns_selectable_fields(
    http_client, owned_records, monkeypatch
):
    calls = []

    class Service:
        def __init__(self, _factory):
            pass

        def preview(self, payload):
            calls.append(payload)
            return {
                "status": "PASSED",
                "failure_category": "",
                "error_message": "",
                "trace": [],
                "response": {"body": {"data": {"access_token": "visible-token"}}},
                "fields": [
                    {
                        "id": "json_path:$.data.access_token",
                        "source": "json_path",
                        "path": "$.data.access_token",
                        "name": "access_token",
                        "value": "visible-token",
                        "value_type": "string",
                        "sensitive": True,
                        "suggested_target": "access_token",
                    }
                ],
                "truncated": False,
                "available_variables": ["access_token"],
                "missing_variables": [],
            }

    monkeypatch.setattr(http, "WorkflowStepPreviewService", Service)
    payload = {
        "environment_revision_id": owned_records["environment_revision"].id,
        "setup_steps": [],
        "target_index": 0,
        "initial_variables": {},
        "processing_pre": [],
        "extraction_overrides": {},
    }

    response = http_client.post(
        "/api/api-testing/v1/workflow-steps/preview", payload, _auth()
    )
    foreign = http_client.post(
        "/api/api-testing/v1/workflow-steps/preview", payload, _auth("owner-b")
    )

    assert response.status == 200
    assert response.body["data"]["preview"]["fields"][0]["value"] == "visible-token"
    assert calls == [{**payload, "environment_revision_id": owned_records["environment_revision"].id}]
    assert foreign.status == 404
    assert foreign.body["error"]["code"] == "not_found"


def test_authenticated_reads_are_owner_scoped(http_client, owned_records):
    response = http_client.get("/api/api-testing/v1/projects", _auth())
    other = http_client.get(f"/api/api-testing/v1/executions/{owned_records['execution'].id}", _auth("owner-b"))
    endpoints = http_client.get(f"/api/api-testing/v1/endpoints?source_revision_id={owned_records['revision'].id}", _auth("owner-b"))

    assert [project["id"] for project in response.body["data"]["projects"]] == [owned_records["project"].id]
    assert other.status == endpoints.status == 404
    assert other.body["error"]["code"] == endpoints.body["error"]["code"] == "not_found"


def test_execution_collection_is_owner_scoped_and_uses_display_metadata(
    http_client, api_context, owned_records
):
    with api_context["factory"].begin() as session:
        execution = session.get(ApiExecution, owned_records["execution"].id)
        execution.summary = {
            "total": 1,
            "passed": 0,
            "failed": 1,
            "broken": 0,
            "cancelled": 0,
        }
        endpoint = session.get(ApiSourceEndpoint, owned_records["endpoint"].id)
        endpoint.summary = "查询我的收藏"
        session.add(
            ApiExecutionCase(
                execution_id=execution.id,
                case_version_id=owned_records["version"].id,
                endpoint_id=endpoint.id,
                environment_revision_id=owned_records["environment_revision"].id,
                ordinal=0,
                status="FAILED",
                failure_category="product_assertion",
                duration_ms=86,
                sanitized_result={
                    "sanitized_request": {"method": "GET", "url": "https://example.test/favorites"},
                    "sanitized_response": {"status_code": 200},
                    "assertion_results": [{"passed": False, "message": "列表为空"}],
                },
                **_audit("owner-a"),
            )
        )

    response = http_client.get(
        f"/api/api-testing/v1/executions?project_id={owned_records['project'].id}",
        _auth(),
    )
    denied = http_client.get(
        f"/api/api-testing/v1/executions?project_id={owned_records['project'].id}",
        _auth("owner-b"),
    )

    assert response.status == 200
    records = response.body["data"]["executions"]
    record = next(item for item in records if item["id"] == owned_records["execution"].id)
    assert record["environment_name"] == "env"
    assert record["case_results"][0] == {
        "execution_case_id": record["case_results"][0]["execution_case_id"],
        "case_version_id": owned_records["version"].id,
        "endpoint_id": owned_records["endpoint"].id,
        "case_name": "case",
        "endpoint_summary": "查询我的收藏",
        "method": "GET",
        "path": "/favorites",
        "execution_role": "requested",
        "status": "FAILED",
        "failure_category": "product_assertion",
        "failure_analysis": None,
        "duration_ms": 86,
        "sanitized_result": {
            "sanitized_request": {"method": "GET", "url": "https://example.test/favorites"},
            "sanitized_response": {"status_code": 200},
            "assertion_results": [{"passed": False, "message": "列表为空"}],
        },
    }
    assert denied.status == 404


def test_active_case_versions_are_restored_for_owned_source_revision(
    http_client, api_context, owned_records
):
    factory = api_context["factory"]
    with factory.begin() as session:
        first_case = session.get(ApiCase, owned_records["case"].id)
        first_case.active_version_id = owned_records["version"].id

        second_case = ApiCase(
            project_id=owned_records["project"].id,
            endpoint_id=owned_records["endpoint"].id,
            name="收藏列表异常场景",
            origin="ai",
            **_audit("owner-a"),
        )
        session.add(second_case)
        session.flush()
        second_version = ApiCaseVersion(
            case_id=second_case.id,
            endpoint_id=owned_records["endpoint"].id,
            version_number=1,
            purpose="鉴权失败",
            request_template={"name": second_case.name, "request": {}},
            **_audit("owner-a"),
        )
        session.add(second_version)
        session.flush()
        second_case.active_version_id = second_version.id

        inactive_version = ApiCaseVersion(
            case_id=second_case.id,
            endpoint_id=owned_records["endpoint"].id,
            version_number=2,
            purpose="未激活版本",
            request_template={"name": "未激活版本", "request": {}},
            **_audit("owner-a"),
        )
        session.add(inactive_version)

        mismatched_case = ApiCase(
            project_id=owned_records["other_project"].id,
            endpoint_id=owned_records["endpoint"].id,
            name="跨项目错误数据",
            origin="manual",
            **_audit("owner-b"),
        )
        session.add(mismatched_case)
        session.flush()
        mismatched_version = ApiCaseVersion(
            case_id=mismatched_case.id,
            endpoint_id=owned_records["endpoint"].id,
            version_number=1,
            purpose="不应返回",
            request_template={"name": mismatched_case.name, "request": {}},
            **_audit("owner-b"),
        )
        session.add(mismatched_version)
        session.flush()
        mismatched_case.active_version_id = mismatched_version.id

    response = http_client.get(
        f"/api/api-testing/v1/cases?source_revision_id={owned_records['revision'].id}",
        _auth(),
    )
    denied = http_client.get(
        f"/api/api-testing/v1/cases?source_revision_id={owned_records['revision'].id}",
        _auth("owner-b"),
    )

    assert response.status == 200
    restored = response.body["data"]["case_versions"]
    assert [item["name"] for item in restored] == ["case", "收藏列表异常场景"]
    assert {item["id"] for item in restored} == {
        owned_records["version"].id,
        second_version.id,
    }
    assert all(item["project_id"] == owned_records["project"].id for item in restored)
    assert all(item["endpoint_id"] == owned_records["endpoint"].id for item in restored)
    assert denied.status == 404


def test_case_version_group_update_is_owner_scoped(http_client, api_context, owned_records):
    version_id = owned_records["version"].id
    with api_context["factory"].begin() as session:
        session.get(ApiCase, owned_records["case"].id).active_version_id = version_id

    response = http_client.put(
        f"/api/api-testing/v1/case-versions/{version_id}/group",
        {"group_name": "收藏回归"},
        _auth(),
    )
    denied = http_client.put(
        f"/api/api-testing/v1/case-versions/{version_id}/group",
        {"group_name": "越权分组"},
        _auth("owner-b"),
    )
    restored = http_client.get(
        f"/api/api-testing/v1/cases?source_revision_id={owned_records['revision'].id}",
        _auth(),
    )

    assert response.status == 200
    assert response.body["data"]["case_version"]["group_name"] == "收藏回归"
    assert denied.status == 404
    assert restored.status == 200
    assert restored.body["data"]["case_versions"][0]["group_name"] == "收藏回归"
    with api_context["factory"]() as session:
        assert session.get(ApiCaseVersion, version_id).group_name == "收藏回归"


def test_context_options_return_only_owned_active_display_metadata(
    http_client, api_context, owned_records
):
    factory = api_context["factory"]
    with factory.begin() as session:
        session.get(ApiEnvironment, owned_records["environment"].id).active_revision_id = (
            owned_records["environment_revision"].id
        )
        session.get(ApiProject, owned_records["project"].id).name = "3D 项目"
        session.get(ApiSource, owned_records["source"].id).name = "Apifox 接口"
        session.get(ApiEnvironmentRevision, owned_records["environment_revision"].id).name = (
            "生产环境（新）- 腾讯云"
        )

    response = http_client.get("/api/api-testing/v1/context-options", _auth())

    assert response.status == 200
    options = response.body["data"]
    assert len(options["projects"]) == 1
    project = options["projects"][0]
    assert {key: project[key] for key in ("id", "name")} == {
        "id": owned_records["project"].id,
        "name": "3D 项目",
    }
    assert len(options["source_revisions"]) == 1
    source_revision = options["source_revisions"][0]
    assert {
        key: source_revision[key]
        for key in (
            "id",
            "source_id",
            "project_id",
            "name",
            "revision_number",
            "endpoint_count",
        )
    } == {
        "id": owned_records["revision"].id,
        "source_id": owned_records["source"].id,
        "project_id": owned_records["project"].id,
        "name": "Apifox 接口",
        "revision_number": 1,
        "endpoint_count": 1,
    }
    assert len(options["environment_revisions"]) == 1
    environment_revision = options["environment_revisions"][0]
    assert {
        key: environment_revision[key]
        for key in ("id", "environment_id", "project_id", "name", "revision")
    } == {
        "id": owned_records["environment_revision"].id,
        "environment_id": owned_records["environment"].id,
        "project_id": owned_records["project"].id,
        "name": "生产环境（新）- 腾讯云",
        "revision": 1,
    }
    assert "normalized_document" not in json.dumps(options)
    assert "default_headers" not in json.dumps(options)


def test_context_options_are_empty_without_owned_saved_context(http_client, api_context):
    response = http_client.get("/api/api-testing/v1/context-options", _auth("owner-b"))

    assert response.status == 200
    assert response.body["data"] == {
        "projects": [],
        "source_revisions": [],
        "environment_revisions": [],
    }


def test_context_options_keep_saved_superseded_revisions_selectable(
    http_client, api_context, owned_records
):
    with api_context["factory"].begin() as session:
        source = session.get(ApiSource, owned_records["source"].id)
        environment = session.get(ApiEnvironment, owned_records["environment"].id)
        old_source = session.get(ApiSourceRevision, owned_records["revision"].id)
        old_environment = session.get(
            ApiEnvironmentRevision, owned_records["environment_revision"].id
        )
        old_source.status = "superseded"
        old_environment.status = "superseded"
        current_source = ApiSourceRevision(
            source_id=source.id,
            revision_number=2,
            status="active",
            document_hash="c" * 64,
            normalized_document={"openapi": "3.0.0"},
            **_audit("owner-a"),
        )
        session.add(current_source)
        session.flush()
        source.active_revision_id = current_source.id
        current_environment = ApiEnvironmentRevision(
            environment_id=environment.id,
            source_revision_id=current_source.id,
            revision_number=2,
            name="当前环境",
            status="active",
            **_audit("owner-a"),
        )
        session.add(current_environment)
        session.flush()
        environment.active_revision_id = current_environment.id
        session.add(
            ApiWorkspace(
                project_id=owned_records["project"].id,
                source_revision_id=old_source.id,
                environment_revision_id=old_environment.id,
                **_audit("owner-a"),
            )
        )

    response = http_client.get("/api/api-testing/v1/context-options", _auth())

    assert response.status == 200
    assert {item["id"] for item in response.body["data"]["source_revisions"]} == {
        owned_records["revision"].id,
        current_source.id,
    }
    assert {item["id"] for item in response.body["data"]["environment_revisions"]} == {
        owned_records["environment_revision"].id,
        current_environment.id,
    }


def test_cross_owner_nested_write_is_hidden_not_validated(http_client, owned_records):
    response = http_client.post(f"/api/api-testing/v1/environments/{owned_records['environment'].id}/revisions", {}, _auth("owner-b"))

    assert response.status == 404
    assert response.body["error"]["code"] == "not_found"


def test_environment_asset_routes_list_history_archive_and_restore(
    http_client, api_context, owned_records
):
    with api_context["factory"].begin() as session:
        environment = session.get(ApiEnvironment, owned_records["environment"].id)
        environment.active_revision_id = owned_records["environment_revision"].id

    project_id = owned_records["project"].id
    environment_id = owned_records["environment"].id
    listed = http_client.get(
        f"/api/api-testing/v1/environments?project_id={project_id}", _auth()
    )
    history = http_client.get(
        f"/api/api-testing/v1/environments/{environment_id}/revisions", _auth()
    )

    assert listed.status == 200
    assert listed.body["data"]["environments"][0]["id"] == environment_id
    assert listed.body["data"]["environments"][0]["active_revision_id"] == (
        owned_records["environment_revision"].id
    )
    assert history.status == 200
    assert [item["revision"] for item in history.body["data"]["revisions"]] == [1]

    archived = http_client.delete(
        f"/api/api-testing/v1/environments/{environment_id}", _auth()
    )
    active = http_client.get(
        f"/api/api-testing/v1/environments?project_id={project_id}", _auth()
    )
    archived_list = http_client.get(
        f"/api/api-testing/v1/environments?project_id={project_id}&status=archived",
        _auth(),
    )

    assert archived.status == 200
    assert archived.body["data"]["environment"]["status"] == "archived"
    assert active.body["data"]["environments"] == []
    assert archived_list.body["data"]["environments"][0]["id"] == environment_id

    restored = http_client.post(
        f"/api/api-testing/v1/environments/{environment_id}/restore", {}, _auth()
    )

    assert restored.status == 200
    assert restored.body["data"]["environment"]["status"] == "active"


def test_environment_asset_routes_hide_cross_owner_resources(
    http_client, api_context, owned_records
):
    with api_context["factory"].begin() as session:
        environment = session.get(ApiEnvironment, owned_records["environment"].id)
        environment.active_revision_id = owned_records["environment_revision"].id

    project_id = owned_records["project"].id
    environment_id = owned_records["environment"].id

    assert http_client.get(
        f"/api/api-testing/v1/environments?project_id={project_id}",
        _auth("owner-b"),
    ).status == 404
    assert http_client.get(
        f"/api/api-testing/v1/environments/{environment_id}/revisions",
        _auth("owner-b"),
    ).status == 404
    assert http_client.delete(
        f"/api/api-testing/v1/environments/{environment_id}",
        _auth("owner-b"),
    ).status == 404


def test_secret_only_environment_revision_accepts_explicit_empty_changes(
    http_client, api_context, owned_records, monkeypatch
):
    monkeypatch.setenv(
        "API_TESTING_SECRET_KEY",
        "http-contract-secret-6f971b1ce4264e8492aecf2721689e94",
    )
    with api_context["factory"].begin() as session:
        environment = session.get(ApiEnvironment, owned_records["environment"].id)
        environment.active_revision_id = owned_records["environment_revision"].id

    response = http_client.post(
        f"/api/api-testing/v1/environments/{owned_records['environment'].id}/revisions",
        {
            "environment": {},
            "secret_updates": {"ZXBToken": "synthetic-secret-never-returned"},
        },
        _auth(),
    )

    assert response.status == 200
    descriptor = response.body["data"]["environment"]["variables"]["ZXBToken"]
    assert descriptor["name"] == "ZXBToken"
    assert descriptor["configured"] is True
    assert descriptor["fingerprint"]
    assert "synthetic-secret-never-returned" not in json.dumps(response.body)


def test_cross_owner_source_preview_is_hidden_before_document_validation(http_client, owned_records):
    response = http_client.post("/api/api-testing/v1/sources/preview", {"project_id": owned_records["project"].id}, _auth("owner-b"))

    assert response.status == 404
    assert response.body["error"]["code"] == "not_found"


def test_apifox_credential_http_response_never_contains_plaintext(
    http_client, api_context, monkeypatch
):
    token = "afxp-http-contract-secret"
    monkeypatch.setenv(
        "API_TESTING_SECRET_KEY",
        "http-provider-secret-7f5f2a352ba84ba680c921e88e9f119b",
    )

    saved = http_client.put(
        "/api/api-testing/v1/providers/apifox/credential",
        {"token": token},
        _auth(),
    )
    loaded = http_client.get(
        "/api/api-testing/v1/providers/apifox/credential", _auth()
    )

    assert saved.status == loaded.status == 200
    assert saved.body["data"]["credential"]["configured"] is True
    assert loaded.body["data"]["credential"]["fingerprint"]
    assert token not in json.dumps(saved.body)
    assert token not in json.dumps(loaded.body)


def test_apifox_discovery_and_preview_are_only_called_by_explicit_posts(
    http_client, api_context, owned_records, monkeypatch
):
    calls = []

    class FakeApifoxService:
        def list_projects(self, owner_id):
            calls.append(("projects", owner_id))
            return ({"id": "5904970", "name": "3D"},)

        def get_context(self, owner_id, project_id, preferred_environment_id=""):
            calls.append(
                ("context", owner_id, project_id, preferred_environment_id)
            )
            return {
                "project": {"id": project_id, "name": "3D"},
                "branches": ({"id": "", "name": "主分支（默认）"},),
                "environments": (
                    {"id": "33831678", "name": "生产环境（新）-腾讯云"},
                ),
                "cli_version": "2.2.8",
            }

        def preview_refresh(self, owner_id, payload, actor_id):
            calls.append(("preview", owner_id, dict(payload), actor_id))
            return {
                "source_preview": {
                    "id": "11111111-1111-4111-8111-111111111111",
                    "added_count": 3,
                    "changed_count": 1,
                    "removed_count": 0,
                },
                "environment_candidate": {
                    "name": "生产环境（新）-腾讯云",
                    "secret_placeholders": ["ZXBToken"],
                },
            }

    service = FakeApifoxService()
    monkeypatch.setattr(http, "_apifox_service", lambda _factory: service)

    before = http_client.get("/api/api-testing/v1/context-options", _auth())
    projects = http_client.post(
        "/api/api-testing/v1/providers/apifox/projects", {}, _auth()
    )
    context = http_client.post(
        "/api/api-testing/v1/providers/apifox/context",
        {"project_id": "5904970", "environment_id": "33831678"},
        _auth(),
    )
    preview = http_client.post(
        "/api/api-testing/v1/sources/apifox/preview",
        {
            "project_id": owned_records["project"].id,
            "source_id": owned_records["source"].id,
            "apifox_project_id": "5904970",
            "branch_id": "",
            "environment_id": "33831678",
        },
        _auth(),
    )

    assert before.status == projects.status == context.status == preview.status == 200
    assert calls == [
        ("projects", "owner-a"),
        ("context", "owner-a", "5904970", "33831678"),
        (
            "preview",
            "owner-a",
            {
                "project_id": owned_records["project"].id,
                "source_id": owned_records["source"].id,
                "apifox_project_id": "5904970",
                "branch_id": "",
                "environment_id": "33831678",
            },
            "owner-a",
        ),
    ]
    assert preview.body["data"]["preview"]["source_preview"]["added_count"] == 3
    assert preview.body["data"]["preview"]["environment_candidate"][
        "secret_placeholders"
    ] == ["ZXBToken"]


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


def test_readiness_route_returns_component_state_without_opening_domain_database(
    http_client, monkeypatch
):
    monkeypatch.setattr(
        http.ApiTestingSettings,
        "from_env",
        staticmethod(lambda: SimpleNamespace(enabled=True, redis_url="redis://unused")),
    )
    monkeypatch.setattr(
        http,
        "verify_session_token",
        lambda token: {"user": "owner-a"} if token == "owner-a" else None,
    )
    monkeypatch.setattr(http, "_readiness", lambda settings: {"ready": True})
    monkeypatch.setattr(
        http,
        "_factory",
        lambda: (_ for _ in ()).throw(AssertionError("domain database was opened")),
    )

    response = http_client.get("/api/api-testing/v1/readiness", _auth())

    assert response.status == 200
    assert response.body["data"] == {"ready": True}


def test_database_failure_is_safe_traceable_and_returns_503(
    http_client, monkeypatch, caplog
):
    monkeypatch.setattr(
        http.ApiTestingSettings,
        "from_env",
        staticmethod(lambda: SimpleNamespace(enabled=True, redis_url="redis://unused")),
    )
    monkeypatch.setattr(
        http,
        "verify_session_token",
        lambda token: {"user": "owner-a"} if token == "owner-a" else None,
    )

    def fail_factory():
        raise OperationalError(
            "SELECT 1",
            {},
            Exception("database password secret-value"),
        )

    monkeypatch.setattr(http, "_factory", fail_factory)

    response = http_client.get(
        "/api/api-testing/v1/projects",
        _auth(**{"X-Request-Id": "database-check-42"}),
    )

    assert response.status == 503
    assert response.body["error"]["code"] == "database_unavailable"
    assert response.body["request_id"] == "database-check-42"
    assert "secret-value" not in json.dumps(response.body)
    assert "database-check-42" in caplog.text
    assert "secret-value" not in caplog.text


def test_redis_connection_failure_maps_to_stable_dependency_error():
    error = http._domain_error(redis.ConnectionError("redis password secret-value"))

    assert error.status == 503
    assert error.code == "redis_unavailable"
    assert "secret-value" not in error.message


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


def test_latest_unfinished_ai_job_can_be_restored_after_reload(
    http_client, api_context, owned_records
):
    with api_context["factory"].begin() as session:
        completed = ApiAiJob(
            project_id=owned_records["project"].id,
            environment_revision_id=owned_records["environment_revision"].id,
            state="completed",
            endpoint_ids=[owned_records["endpoint"].id],
            requested_model="qwen",
            actual_model="qwen",
            summary={},
            created_at=datetime(2026, 8, 27, 10, 1, tzinfo=timezone.utc),
            **_audit("owner-a"),
        )
        running = ApiAiJob(
            project_id=owned_records["project"].id,
            environment_revision_id=owned_records["environment_revision"].id,
            state="running",
            endpoint_ids=[owned_records["endpoint"].id],
            requested_model="qwen",
            actual_model="qwen",
            summary={},
            created_at=datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc),
            **_audit("owner-a"),
        )
        session.add_all((completed, running))
        session.flush()
        session.add(
            ApiAiJobBatch(
                job_id=running.id,
                sequence=1,
                state="running",
                endpoint_ids=[owned_records["endpoint"].id],
                requested_model="qwen",
                actual_model="qwen",
                result={"draft_version_ids": [], "validation_errors": []},
                error={},
                **_audit("owner-a"),
            )
        )

    response = http_client.get(
        f"/api/api-testing/v1/ai-jobs/latest?project_id={owned_records['project'].id}",
        _auth(),
    )
    denied = http_client.get(
        f"/api/api-testing/v1/ai-jobs/latest?project_id={owned_records['project'].id}",
        _auth("owner-b"),
    )

    assert response.status == 200
    assert response.body["data"]["job"]["id"] == running.id
    assert denied.status == 404


def test_latest_completed_ai_job_can_be_restored_for_current_source_revision(
    http_client, api_context, owned_records
):
    with api_context["factory"].begin() as session:
        completed = ApiAiJob(
            project_id=owned_records["project"].id,
            environment_revision_id=owned_records["environment_revision"].id,
            state="completed",
            endpoint_ids=[owned_records["endpoint"].id],
            requested_model="qwen",
            actual_model="qwen",
            summary={"generated_count": 1},
            **_audit("owner-a"),
        )
        session.add(completed)
        session.flush()
        session.add(
            ApiAiJobBatch(
                job_id=completed.id,
                sequence=1,
                state="completed",
                endpoint_ids=[owned_records["endpoint"].id],
                requested_model="qwen",
                actual_model="qwen",
                result={"draft_version_ids": [], "validation_errors": []},
                error={},
                **_audit("owner-a"),
            )
        )

    response = http_client.get(
        "/api/api-testing/v1/ai-jobs/latest"
        f"?project_id={owned_records['project'].id}"
        f"&source_revision_id={owned_records['revision'].id}",
        _auth(),
    )

    assert response.status == 200
    assert response.body["data"]["job"]["id"] == completed.id
    assert response.body["data"]["job"]["state"] == "completed"


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


def test_api_task_restores_selection_and_tracks_ai_and_debug_execution(
    http_client, owned_records, monkeypatch
):
    monkeypatch.setattr(http, "_enqueue_ai_job", lambda _job_id: None)
    created = http_client.post(
        "/api/api-testing/v1/tasks",
        {
            "project_id": owned_records["project"].id,
            "source_revision_id": owned_records["revision"].id,
            "environment_revision_id": owned_records["environment_revision"].id,
            "name": "我的收藏接口回归",
            "selected_endpoint_ids": [owned_records["endpoint"].id],
        },
        _auth(),
    )
    task_id = created.body["data"]["task"]["id"]
    ai = http_client.post(
        "/api/api-testing/v1/ai-jobs",
        {
            "endpoint_ids": [owned_records["endpoint"].id],
            "environment_revision_id": owned_records["environment_revision"].id,
            "intent": "覆盖收藏查询",
            "task_id": task_id,
        },
        _auth(),
    )
    debug = http_client.post(
        "/api/api-testing/v1/executions",
        {
            "project_id": owned_records["project"].id,
            "source_revision_id": owned_records["revision"].id,
            "environment_revision_id": owned_records["environment_revision"].id,
            "case_version_ids": [owned_records["version"].id],
            "execution_type": "debug",
            "overrides": {},
            "idempotency_key": "task-debug-http-contract",
            "task_id": task_id,
        },
        _auth(),
    )
    restored = http_client.get(
        f"/api/api-testing/v1/tasks/active?project_id={owned_records['project'].id}",
        _auth(),
    )

    assert created.status == ai.status == restored.status == 200
    assert debug.status == 202
    assert restored.body["data"]["task"]["id"] == task_id
    assert restored.body["data"]["task"]["selected_endpoint_ids"] == [
        owned_records["endpoint"].id
    ]
    assert restored.body["data"]["task"]["latest_ai_job_id"] == ai.body["data"][
        "job"
    ]["id"]
    assert restored.body["data"]["task"]["latest_execution_id"] == debug.body[
        "data"
    ]["execution"]["id"]
    assert restored.body["data"]["task"]["state"] == "debugging"


def test_api_task_is_hidden_from_another_owner(http_client, owned_records):
    created = http_client.post(
        "/api/api-testing/v1/tasks",
        {
            "project_id": owned_records["project"].id,
            "source_revision_id": owned_records["revision"].id,
            "environment_revision_id": owned_records["environment_revision"].id,
            "name": "收藏回归",
            "selected_endpoint_ids": [owned_records["endpoint"].id],
        },
        _auth(),
    )

    denied = http_client.put(
        f"/api/api-testing/v1/tasks/{created.body['data']['task']['id']}",
        {
            "project_id": owned_records["project"].id,
            "source_revision_id": owned_records["revision"].id,
            "environment_revision_id": owned_records["environment_revision"].id,
            "name": "越权修改",
            "selected_endpoint_ids": [owned_records["endpoint"].id],
        },
        _auth("owner-b"),
    )

    assert denied.status == 404
    assert denied.body["error"]["code"] == "not_found"


def test_api_task_can_be_deleted_from_saved_list(http_client, owned_records):
    first = http_client.post(
        "/api/api-testing/v1/tasks",
        {
            "project_id": owned_records["project"].id,
            "source_revision_id": owned_records["revision"].id,
            "environment_revision_id": owned_records["environment_revision"].id,
            "name": "待删除任务",
            "selected_endpoint_ids": [owned_records["endpoint"].id],
        },
        _auth(),
    ).body["data"]["task"]
    second = http_client.post(
        "/api/api-testing/v1/tasks",
        {
            "project_id": owned_records["project"].id,
            "source_revision_id": owned_records["revision"].id,
            "environment_revision_id": owned_records["environment_revision"].id,
            "name": "保留任务",
            "selected_endpoint_ids": [owned_records["endpoint"].id],
        },
        _auth(),
    ).body["data"]["task"]

    deleted = http_client.delete(
        f"/api/api-testing/v1/tasks/{first['id']}",
        _auth(),
    )
    denied = http_client.delete(
        f"/api/api-testing/v1/tasks/{second['id']}",
        _auth("owner-b"),
    )
    listed = http_client.get(
        f"/api/api-testing/v1/tasks?project_id={owned_records['project'].id}",
        _auth(),
    )

    assert deleted.status == 200
    assert deleted.body["data"]["task"]["id"] == first["id"]
    assert denied.status == 404
    assert [item["id"] for item in listed.body["data"]["tasks"]] == [second["id"]]


def test_api_task_run_executes_only_adopted_baselines_in_saved_selection(
    http_client, api_context, owned_records
):
    with api_context["factory"].begin() as session:
        case = session.get(ApiCase, owned_records["case"].id)
        case.active_version_id = owned_records["version"].id
        debug_case = ApiExecutionCase(
            execution_id=owned_records["execution"].id,
            case_version_id=owned_records["version"].id,
            endpoint_id=owned_records["endpoint"].id,
            environment_revision_id=owned_records["environment_revision"].id,
            ordinal=0,
            status="PASSED",
            **_audit("owner-a"),
        )
        session.add(debug_case)
        session.flush()
        session.add(
            ApiBaseline(
                project_id=owned_records["project"].id,
                case_id=owned_records["case"].id,
                case_version_id=owned_records["version"].id,
                environment_revision_id=owned_records["environment_revision"].id,
                debug_execution_case_id=debug_case.id,
                status="active",
                **_audit("owner-a"),
            )
        )
    task = http_client.post(
        "/api/api-testing/v1/tasks",
        {
            "project_id": owned_records["project"].id,
            "source_revision_id": owned_records["revision"].id,
            "environment_revision_id": owned_records["environment_revision"].id,
            "name": "我的收藏接口回归",
            "selected_endpoint_ids": [owned_records["endpoint"].id],
        },
        _auth(),
    ).body["data"]["task"]
    assert task["runnable_baseline_count"] == 1

    response = http_client.post(
        f"/api/api-testing/v1/tasks/{task['id']}/run",
        {"idempotency_key": "task-regression-http-contract"},
        _auth(),
    )

    assert response.status == 202
    assert response.body["data"]["execution"]["execution_type"] == "baseline_regression"
    assert response.body["data"]["execution"]["case_statuses"] == ["QUEUED"]
    assert response.body["data"]["task"]["state"] == "running"
    assert response.body["data"]["task"]["latest_execution_id"] == response.body[
        "data"
    ]["execution"]["id"]


def test_selected_baseline_regression_does_not_use_task_scope(
    http_client, api_context, owned_records
):
    with api_context["factory"].begin() as session:
        first_case = session.get(ApiCase, owned_records["case"].id)
        first_case.active_version_id = owned_records["version"].id
        debug_case = ApiExecutionCase(
            execution_id=owned_records["execution"].id,
            case_version_id=owned_records["version"].id,
            endpoint_id=owned_records["endpoint"].id,
            environment_revision_id=owned_records["environment_revision"].id,
            ordinal=0,
            status="PASSED",
            **_audit("owner-a"),
        )
        session.add(debug_case)
        session.flush()
        first_baseline = ApiBaseline(
            project_id=owned_records["project"].id,
            case_id=owned_records["case"].id,
            case_version_id=owned_records["version"].id,
            environment_revision_id=owned_records["environment_revision"].id,
            debug_execution_case_id=debug_case.id,
            status="active",
            **_audit("owner-a"),
        )
        second_case = ApiCase(
            project_id=owned_records["project"].id,
            endpoint_id=owned_records["endpoint"].id,
            name="case second baseline",
            origin="manual",
            **_audit("owner-a"),
        )
        session.add(second_case)
        session.flush()
        second_version = ApiCaseVersion(
            case_id=second_case.id,
            endpoint_id=owned_records["endpoint"].id,
            version_number=1,
            purpose="case second",
            request_template={
                "name": "case second",
                "app_package": "com.kfb.model",
                "app_name": "智小白3D",
                "business": "home",
                "request": {},
            },
            **_audit("owner-a"),
        )
        session.add(second_version)
        session.flush()
        second_case.active_version_id = second_version.id
        second_debug_case = ApiExecutionCase(
            execution_id=owned_records["second_execution"].id,
            case_version_id=second_version.id,
            endpoint_id=owned_records["endpoint"].id,
            environment_revision_id=owned_records["environment_revision"].id,
            ordinal=0,
            status="PASSED",
            **_audit("owner-a"),
        )
        session.add(second_debug_case)
        session.flush()
        second_baseline = ApiBaseline(
            project_id=owned_records["project"].id,
            case_id=second_case.id,
            case_version_id=second_version.id,
            environment_revision_id=owned_records["environment_revision"].id,
            debug_execution_case_id=second_debug_case.id,
            status="active",
            **_audit("owner-a"),
        )
        session.add_all((first_baseline, second_baseline))
        session.flush()
        selected_baseline_id = second_baseline.id
        selected_case_version_id = second_version.id
        unselected_case_version_id = owned_records["version"].id

    task = http_client.post(
        "/api/api-testing/v1/tasks",
        {
            "project_id": owned_records["project"].id,
            "source_revision_id": owned_records["revision"].id,
            "environment_revision_id": owned_records["environment_revision"].id,
            "name": "同一接口多版本基线",
            "selected_endpoint_ids": [owned_records["endpoint"].id],
        },
        _auth(),
    ).body["data"]["task"]

    assert task["runnable_endpoint_count"] == 1
    assert task["runnable_baseline_count"] == 2

    response = http_client.post(
        "/api/api-testing/v1/regressions",
        {
            "project_id": owned_records["project"].id,
            "source_revision_id": owned_records["revision"].id,
            "environment_revision_id": owned_records["environment_revision"].id,
            "baseline_ids": [selected_baseline_id],
            "idempotency_key": "selected-baseline-http-contract",
        },
        _auth(),
    )

    assert response.status == 202
    execution = response.body["data"]["execution"]
    assert execution["execution_type"] == "baseline_regression"
    assert [item["case_version_id"] for item in execution["case_results"]] == [
        selected_case_version_id
    ]
    assert unselected_case_version_id not in [
        item["case_version_id"] for item in execution["case_results"]
    ]


def test_api_task_without_an_active_baseline_is_not_runnable(
    http_client, owned_records
):
    task = http_client.post(
        "/api/api-testing/v1/tasks",
        {
            "project_id": owned_records["project"].id,
            "source_revision_id": owned_records["revision"].id,
            "environment_revision_id": owned_records["environment_revision"].id,
            "name": "我的收藏接口回归",
            "selected_endpoint_ids": [owned_records["endpoint"].id],
        },
        _auth(),
    ).body["data"]["task"]

    assert task["runnable_baseline_count"] == 0
    response = http_client.post(
        f"/api/api-testing/v1/tasks/{task['id']}/run",
        {"idempotency_key": "task-regression-without-baseline"},
        _auth(),
    )

    assert response.status == 409
    assert response.body["error"] == {
        "code": "baseline_required",
        "message": "请先调试通过并采纳至少一条用例为基线",
        "details": {},
    }


def test_ai_job_enqueue_failure_is_persisted_as_safe_terminal_failure(
    http_client, api_context, owned_records, monkeypatch
):
    secret = "sk-this-must-never-be-persisted"

    def fail_enqueue(_job_id):
        raise RuntimeError(f"Redis unavailable with credential {secret}")

    monkeypatch.setattr(http, "_enqueue_ai_job", fail_enqueue)

    response = http_client.post(
        "/api/api-testing/v1/ai-jobs",
        {
            "endpoint_ids": [owned_records["endpoint"].id],
            "environment_revision_id": owned_records["environment_revision"].id,
            "intent": "覆盖收藏列表",
        },
        _auth(),
    )

    assert response.status == 503
    assert response.body["error"] == {
        "code": "ai_enqueue_unavailable",
        "message": "AI generation queue is unavailable",
        "details": {},
    }
    with api_context["factory"]() as session:
        job = session.query(ApiAiJob).one()
        batches = session.query(ApiAiJobBatch).order_by(ApiAiJobBatch.sequence).all()
        assert job.state == "failed_gateway"
        assert job.summary["infrastructure_failure"] == "enqueue_failed"
        assert job.summary["gateway_failures"] == len(batches) == 1
        assert [item.state for item in batches] == ["failed_gateway"]
        assert [item.error for item in batches] == [{"code": "enqueue_failed"}]
        persisted = json.dumps(
            {"summary": job.summary, "errors": [item.error for item in batches]}
        )
        assert secret not in persisted
        assert "Redis unavailable" not in persisted


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
    assert first_frame.startswith(b'id: 1\nevent: progress\ndata: ')
    first_data = json.loads(first_frame.split(b'data: ', 1)[1])
    assert first_data["step"] == 1
    assert datetime.fromisoformat(first_data["_event_created_at"]).tzinfo is not None
    assert first_sequence == 1
    assert second_sequence == 2
    assert second_body.startswith(b'id: 2\nevent: execution_finished\ndata: ')
    second_data = json.loads(second_body.split(b'data: ', 1)[1])
    assert second_data["step"] == 2
    assert datetime.fromisoformat(second_data["_event_created_at"]).tzinfo is not None


def test_fresh_sse_ticket_can_resume_from_query_event_id(http_client, api_context, owned_records):
    execution_id = owned_records["execution"].id
    stream = EventStream(api_context["factory"], api_context["redis"])
    first_sequence = stream.append(execution_id, "request", {"step": 1})
    second_sequence = stream.append(execution_id, "execution_finished", {"step": 2})
    with api_context["factory"].begin() as session:
        session.get(ApiExecution, execution_id).state = "DONE"
    ticket = http_client.post(
        f"/api/api-testing/v1/executions/{execution_id}/sse-ticket", {}, _auth()
    ).body["data"]["ticket"]

    _, response = http_client.open_stream(
        f"/api/api-testing/v1/executions/{execution_id}/events?ticket={ticket}&after={first_sequence}"
    )
    body = response.read()

    assert response.status == 200
    assert first_sequence == 1
    assert second_sequence == 2
    assert body.startswith(b'id: 2\nevent: execution_finished\ndata: ')
    data = json.loads(body.split(b'data: ', 1)[1])
    assert data["step"] == 2
    assert datetime.fromisoformat(data["_event_created_at"]).tzinfo is not None


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
