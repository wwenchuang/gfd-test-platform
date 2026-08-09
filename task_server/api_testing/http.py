"""Authenticated HTTP and SSE adapter for the API testing module."""

from dataclasses import asdict, is_dataclass
from datetime import date, datetime
import json
import re
import time
from types import MappingProxyType
from urllib.parse import unquote
from uuid import UUID, uuid4

from sqlalchemy import select

from task_server.auth import bearer_token, verify_session_token

from .config import ApiTestingSettings
from .db import _session_factory
from .events import EventStream
from .models.project import ApiProject
from .models.source import ApiSourceEndpoint
from .services.ai_service import AiCaseService, AiJobInputError, AiJobNotFoundError
from .services.case_service import BaselineGateError, CaseNotFoundError, CaseService, EndpointNotFoundError
from .services.environment_service import EnvironmentInputError, EnvironmentNotFoundError, EnvironmentService
from .services.execution_service import ExecutionConflictError, ExecutionNotFoundError, ExecutionService
from .services.source_service import (
    SourceNotFoundError,
    SourcePreviewExpiredError,
    SourcePreviewNotFoundError,
    SourcePreviewStateError,
    SourceService,
    StaleSourcePreviewError,
)


API_PREFIX = "/api/api-testing/v1"
MAX_JSON_BODY_BYTES = 1_000_000
SSE_HEARTBEAT_SECONDS = 15
TERMINAL_EXECUTION_STATES = frozenset({"DONE", "CANCELLED", "PASSED", "FAILED", "BROKEN"})
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class ApiHttpError(Exception):
    def __init__(self, status, code, message, details=None):
        self.status = status
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def dispatch_get(handler, qs, path):
    return _dispatch(handler, "GET", qs, path)


def dispatch_post(handler, qs, path):
    return _dispatch(handler, "POST", qs, path)


def dispatch_delete(handler, qs, path):
    return _dispatch(handler, "DELETE", qs, path)


def _dispatch(handler, method, qs, path):
    request_id = _request_id(handler)
    try:
        actor = _require_session(handler)
        settings = ApiTestingSettings.from_env()
        if not settings.enabled:
            raise ApiHttpError(503, "api_testing_disabled", "API testing is unavailable")
        segments = _segments(path)
        if method == "GET" and _is_execution_events(segments):
            return _stream_events(handler, _uuid(segments[1]), request_id, actor)
        payload = _read_json_body(handler) if method == "POST" else None
        result, status = _route(method, segments, qs, payload, actor)
        return _success(handler, result, request_id, status)
    except ApiHttpError as error:
        return _failure(handler, error, request_id)
    except Exception as error:
        return _failure(handler, _domain_error(error), request_id)


def _request_id(handler):
    value = str(handler.headers.get("X-Request-Id", "")).strip()
    return value if _REQUEST_ID.fullmatch(value) else str(uuid4())


def _require_session(handler):
    payload = verify_session_token(bearer_token(handler.headers))
    if not payload or not isinstance(payload.get("user"), str) or not payload["user"]:
        raise ApiHttpError(401, "unauthorized", "Authentication is required")
    return payload["user"]


def _segments(path):
    if not path.startswith(API_PREFIX):
        raise ApiHttpError(404, "not_found", "Resource was not found")
    suffix = path[len(API_PREFIX):]
    if suffix and not suffix.startswith("/"):
        raise ApiHttpError(404, "not_found", "Resource was not found")
    return tuple(unquote(part) for part in suffix.strip("/").split("/") if part)


def _uuid(value):
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise ApiHttpError(400, "invalid_identifier", "Identifier must be a UUID")


def _read_json_body(handler):
    # Authentication happens in _dispatch before this method is ever reached.
    header = handler.headers.get("Content-Length", "0")
    try:
        size = int(header)
    except (TypeError, ValueError):
        raise ApiHttpError(400, "invalid_request", "Content-Length is invalid")
    if size < 0 or size > MAX_JSON_BODY_BYTES:
        raise ApiHttpError(413, "payload_too_large", "JSON payload exceeds the 1 MB limit")
    try:
        payload = handler._body()
    except Exception:
        raise ApiHttpError(400, "invalid_json", "Request body must be valid JSON")
    if not isinstance(payload, dict):
        raise ApiHttpError(422, "invalid_request", "Request body must be a JSON object")
    return payload


def _route(method, segments, qs, payload, actor):
    if method == "GET":
        return _get(segments, qs), 200
    if method == "POST":
        return _post(segments, payload, actor), 202 if segments == ("executions",) else 200
    if method == "DELETE":
        return _delete(segments, actor), 200
    raise ApiHttpError(405, "method_not_allowed", "Method is not allowed")


def _factory():
    return _session_factory()


def _event_stream(factory):
    return EventStream(factory)


def _get(segments, qs):
    factory = _factory()
    if segments == ("projects",):
        with factory() as session:
            projects = session.scalars(select(ApiProject).order_by(ApiProject.created_at)).all()
        return {"projects": [_project_view(item) for item in projects]}
    if segments == ("workspace",):
        return {"project": None, "source_revision": None, "environment_revision": None}
    if len(segments) == 2 and segments[0] == "executions":
        return {"execution": _view(ExecutionService(factory, event_stream=_event_stream(factory)).get(_uuid(segments[1])))}
    if len(segments) == 2 and segments[0] == "cases":
        return {"case": _view(CaseService(factory).get_case(_uuid(segments[1])))}
    if len(segments) == 2 and segments[0] == "case-versions":
        return {"case_version": _view(CaseService(factory).get_version(_uuid(segments[1])))}
    if len(segments) == 2 and segments[0] == "environments":
        return {"environment": _view(EnvironmentService(factory).get_environment(_uuid(segments[1])))}
    if len(segments) == 2 and segments[0] == "environment-revisions":
        return {"environment_revision": _view(EnvironmentService(factory).get_revision(_uuid(segments[1])))}
    if len(segments) == 2 and segments[0] == "source-revisions":
        return {"source_revision": _view(SourceService(factory).get_revision(_uuid(segments[1])))}
    if segments == ("endpoints",):
        revision_id = _uuid(qs.get("source_revision_id", ""))
        with factory() as session:
            endpoints = session.scalars(
                select(ApiSourceEndpoint).where(ApiSourceEndpoint.revision_id == revision_id)
            ).all()
        return {"endpoints": [_endpoint_view(item) for item in endpoints]}
    raise ApiHttpError(404, "not_found", "Resource was not found")


def _post(segments, payload, actor):
    factory = _factory()
    if segments == ("projects",):
        name = _string(payload.get("name"), "name", 200)
        slug = _string(payload.get("slug"), "slug", 120)
        with factory.begin() as session:
            project = ApiProject(name=name, slug=slug, description=str(payload.get("description") or ""), owner_id=actor, created_by=actor, updated_by=actor)
            session.add(project)
            session.flush()
            return {"project": _project_view(project)}
    if segments == ("sources", "preview"):
        return {"preview": _view(SourceService(factory).preview_refresh(_uuid(payload.get("project_id")), _optional_uuid(payload.get("source_id")), _required_object(payload, "document"), actor))}
    if len(segments) == 3 and segments[0] == "sources" and segments[2] == "activate":
        return {"source_revision": _view(SourceService(factory).activate_preview(_uuid(segments[1]), actor))}
    if segments == ("environments", "import"):
        return {"environment": _view(EnvironmentService(factory).import_from_source(payload, actor))}
    if len(segments) == 3 and segments[0] == "environments" and segments[2] == "revisions":
        return {"environment": _view(EnvironmentService(factory).create_revision(_uuid(segments[1]), payload.get("environment") or payload, payload.get("secret_updates") or {}, actor))}
    if segments == ("cases",):
        return {"case_version": _view(CaseService(factory).create_draft(_uuid(payload.get("endpoint_id")), _required_object(payload, "case"), payload.get("origin", "manual"), actor))}
    if len(segments) == 3 and segments[0] == "cases" and segments[2] == "versions":
        return {"case_version": _view(CaseService(factory).create_version(_uuid(segments[1]), _required_object(payload, "case"), actor))}
    if len(segments) == 3 and segments[0] == "case-versions" and segments[2] == "validate":
        return {"validation": _view(CaseService(factory).validate_case(_uuid(segments[1]), payload.get("environment_metadata") or {}))}
    if len(segments) == 3 and segments[0] == "case-versions" and segments[2] == "baseline":
        return {"baseline": _view(CaseService(factory).adopt_baseline(_uuid(segments[1]), _uuid(payload.get("debug_execution_case_id")), actor))}
    if segments == ("executions",):
        execution = ExecutionService(factory, event_stream=_event_stream(factory)).submit(_execution_request(payload), actor, _string(payload.get("idempotency_key"), "idempotency_key", 200))
        _enqueue_execution(execution.id)
        return {"execution": _view(execution)}
    if len(segments) == 3 and segments[0] == "executions" and segments[2] == "cancel":
        return {"execution": _view(ExecutionService(factory, event_stream=_event_stream(factory)).cancel(_uuid(segments[1]), actor))}
    if segments == ("ai-jobs",):
        return {"job": _view(AiCaseService(factory).submit(payload.get("endpoint_ids"), _uuid(payload.get("environment_revision_id")), actor, payload.get("model_config"), payload.get("intent", "")))}
    raise ApiHttpError(404, "not_found", "Resource was not found")


def _delete(segments, actor):
    if len(segments) == 2 and segments[0] == "executions":
        return {"execution": _view(ExecutionService(_factory(), event_stream=_event_stream(_factory())).cancel(_uuid(segments[1]), actor))}
    raise ApiHttpError(404, "not_found", "Resource was not found")


def _enqueue_execution(execution_id):
    from .tasks import execute_api_testing
    execute_api_testing.delay(execution_id)


def _is_execution_events(segments):
    return len(segments) == 3 and segments[0] == "executions" and segments[2] == "events"


def _stream_events(handler, execution_id, request_id, _actor):
    last_event_id = handler.headers.get("Last-Event-ID", "0").strip() or "0"
    try:
        after_id = int(last_event_id)
    except ValueError:
        raise ApiHttpError(400, "invalid_last_event_id", "Last-Event-ID must be a non-negative integer")
    if after_id < 0:
        raise ApiHttpError(400, "invalid_last_event_id", "Last-Event-ID must be a non-negative integer")
    factory = _factory()
    service = ExecutionService(factory, event_stream=_event_stream(factory))
    execution = service.get(execution_id)
    handler.send_response(200)
    handler._cors()
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("X-Request-Id", request_id)
    handler.end_headers()
    while True:
        events = _event_stream(factory).read(execution_id, after_id, SSE_HEARTBEAT_SECONDS * 1000)
        if events:
            for event in events:
                _write_sse(handler, event.sequence, event.type, event.payload)
                after_id = event.sequence
                if event.type == "execution_finished":
                    return
        else:
            _write_sse(handler, None, "heartbeat", {"request_id": request_id})
        if service.get(execution_id).state in TERMINAL_EXECUTION_STATES:
            return


def _write_sse(handler, sequence, event_type, payload):
    lines = []
    if sequence is not None:
        lines.append(f"id: {sequence}")
    lines.append(f"event: {event_type}")
    lines.append("data: " + json.dumps(_json_value(payload), ensure_ascii=False, separators=(",", ":")))
    try:
        handler.wfile.write(("\n".join(lines) + "\n\n").encode("utf-8"))
        handler.wfile.flush()
    except (BrokenPipeError, ConnectionResetError):
        raise


def _success(handler, data, request_id, status):
    body = json.dumps(
        {"ok": True, "data": _json_value(data), "request_id": request_id},
        ensure_ascii=False,
    ).encode("utf-8")
    handler.send_response(status)
    handler._cors()
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("X-Request-Id", request_id)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        pass


def _failure(handler, error, request_id):
    payload = {"ok": False, "error": {"code": error.code, "message": error.message, "details": _json_value(error.details)}, "request_id": request_id}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(error.status)
    handler._cors()
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("X-Request-Id", request_id)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        pass


def _domain_error(error):
    if isinstance(error, ApiHttpError):
        return error
    if isinstance(error, (EndpointNotFoundError, CaseNotFoundError, EnvironmentNotFoundError, SourceNotFoundError, SourcePreviewNotFoundError, ExecutionNotFoundError, AiJobNotFoundError)):
        return ApiHttpError(404, "not_found", "Resource was not found")
    if isinstance(error, (ExecutionConflictError, BaselineGateError, SourcePreviewExpiredError, SourcePreviewStateError, StaleSourcePreviewError)):
        return ApiHttpError(409, "conflict", "Resource state conflicts with this request")
    if isinstance(error, (ValueError, EnvironmentInputError, AiJobInputError)):
        return ApiHttpError(422, "invalid_request", "Request validation failed")
    return ApiHttpError(500, "internal_error", "Internal server error")


def _execution_request(payload):
    return {
        "project_id": _uuid(payload.get("project_id")),
        "source_revision_id": _uuid(payload.get("source_revision_id")),
        "environment_revision_id": _uuid(payload.get("environment_revision_id")),
        "case_version_ids": [_uuid(value) for value in payload.get("case_version_ids", [])],
        "execution_type": _string(payload.get("execution_type", "debug"), "execution_type", 32),
        "overrides": payload.get("overrides") or {},
    }


def _string(value, name, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ApiHttpError(422, "invalid_request", f"{name} is invalid")
    return value.strip()


def _optional_uuid(value):
    return _uuid(value) if value else None


def _required_object(payload, key):
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ApiHttpError(422, "invalid_request", f"{key} must be an object")
    return value


def _project_view(project):
    return {"id": project.id, "name": project.name, "slug": project.slug, "description": project.description, "status": project.status}


def _endpoint_view(endpoint):
    return {"id": endpoint.id, "revision_id": endpoint.revision_id, "operation_id": endpoint.operation_id, "method": endpoint.method, "path": endpoint.path, "summary": endpoint.summary, "tags": endpoint.tags}


def _view(value):
    return _json_value(asdict(value) if is_dataclass(value) else value)


def _json_value(value):
    if isinstance(value, MappingProxyType):
        return {key: _json_value(item) for key, item in value.items()}
    if is_dataclass(value):
        return _json_value(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
