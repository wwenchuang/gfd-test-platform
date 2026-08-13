"""Authenticated HTTP and SSE adapter for the API testing module."""

from dataclasses import fields, is_dataclass
from datetime import date, datetime
from functools import lru_cache
import json
import logging
import re
import secrets
from types import MappingProxyType
from urllib.parse import unquote
from uuid import UUID, uuid4

import redis
from sqlalchemy import select
from sqlalchemy.exc import InterfaceError, OperationalError

from task_server.auth import bearer_token, verify_session_token

from .config import ApiTestingSettings
from .contracts.case import CasePayloadError
from .adapters.apifox_discovery import ApifoxDiscoveryAdapter, ApifoxDiscoveryError
from .adapters.apifox_openapi import ApifoxOpenApiAdapter, ApifoxOpenApiError
from .adapters.openapi import OpenApiValidationError
from .db import _session_factory
from .events import EventStream
from .models.case import ApiAiJob, ApiCase, ApiCaseVersion
from .models.environment import ApiEnvironment, ApiEnvironmentRevision
from .models.execution import ApiExecution, ApiExecutionCase
from .models.project import ApiProject, ApiWorkspace
from .models.source import ApiSource, ApiSourceDiff, ApiSourceEndpoint, ApiSourceRevision
from .repositories.context_repository import ContextRepository
from .repositories.source_repository import audit_fields
from .services.ai_service import AiCaseService, AiJobInputError, AiJobNotFoundError
from .services.apifox_service import ApifoxInputError, ApifoxService
from .services.case_service import BaselineGateError, CaseNotFoundError, CaseService, EndpointNotFoundError
from .services.environment_service import EnvironmentInputError, EnvironmentNotFoundError, EnvironmentService
from .services.execution_service import ExecutionConflictError, ExecutionNotFoundError, ExecutionService
from .services.notification_service import (
    NotificationInputError,
    NotificationNotConfiguredError,
    NotificationService,
)
from .services.provider_service import (
    ProviderCredentialInputError,
    ProviderCredentialNotFoundError,
    ProviderService,
)
from .services.readiness_service import ReadinessService
from .services.source_service import (
    SourceNotFoundError,
    SourcePreviewExpiredError,
    SourcePreviewNotFoundError,
    SourcePreviewStateError,
    SourceService,
    StaleSourcePreviewError,
)
from .services.test_task_service import (
    TestTaskInputError,
    TestTaskNotFoundError,
    TestTaskScopeError,
    TestTaskService,
)


API_PREFIX = "/api/api-testing/v1"
MAX_JSON_BODY_BYTES = 1_000_000
SSE_HEARTBEAT_SECONDS = 15
# EventSource reconnects reuse its URL, so a successful event-stream exchange
# renews this narrow, opaque ticket for only a short period.
SSE_TICKET_TTL_SECONDS = 60
TERMINAL_EXECUTION_STATES = frozenset({"DONE", "CANCELLED", "PASSED", "FAILED", "BROKEN"})
_REQUEST_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_SSE_TICKET_REDEEM_LUA = """
local payload = redis.call('GET', KEYS[1])
if not payload then return {0} end
local ok, value = pcall(cjson.decode, payload)
if not ok or type(value) ~= 'table' then return {0} end
if type(value['owner_id']) ~= 'string' or value['execution_id'] ~= ARGV[1] then return {0} end
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]))
return {1, payload}
"""
logger = logging.getLogger(__name__)


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


def dispatch_put(handler, qs, path):
    return _dispatch(handler, "PUT", qs, path)


def dispatch_delete(handler, qs, path):
    return _dispatch(handler, "DELETE", qs, path)


def _dispatch(handler, method, qs, path):
    request_id = _request_id(handler)
    actor = None
    try:
        segments = _segments(path)
        ticket = str(qs.get("ticket") or "")
        if not ticket:
            actor = _authenticate(handler, qs, segments, None)
        settings = ApiTestingSettings.from_env()
        if not settings.enabled:
            raise ApiHttpError(503, "api_testing_disabled", "API testing is unavailable")
        if ticket:
            actor = _authenticate(handler, qs, segments, settings)
        if method == "GET" and _is_execution_events(segments):
            return _stream_events(
                handler,
                _uuid(segments[1]),
                request_id,
                actor,
                after=qs.get("after"),
            )
        payload = _read_json_body(handler) if method in {"POST", "PUT"} else None
        result, status = _route(method, segments, qs, payload, actor, settings)
        return _success(handler, result, request_id, status)
    except ApiHttpError as error:
        return _failure(handler, error, request_id)
    except Exception as error:
        mapped_error = _domain_error(error)
        _log_dispatch_error(error, mapped_error, request_id, method, path, actor)
        return _failure(handler, mapped_error, request_id)


def _log_dispatch_error(error, mapped_error, request_id, method, path, actor):
    logger.error(
        "API testing request failed request_id=%s method=%s route=%s actor=%s "
        "error_code=%s exception_type=%s",
        request_id,
        method,
        path,
        actor or "unknown",
        mapped_error.code,
        type(error).__name__,
    )


def _authenticate(handler, qs, segments, settings):
    ticket = str(qs.get("ticket") or "")
    if ticket:
        if not _is_execution_events(segments):
            raise ApiHttpError(401, "unauthorized", "Authentication is required")
        return _consume_sse_ticket(settings, ticket, _uuid(segments[1]))
    payload = verify_session_token(bearer_token(handler.headers))
    if not payload or not isinstance(payload.get("user"), str) or not payload["user"]:
        raise ApiHttpError(401, "unauthorized", "Authentication is required")
    return payload["user"]


def _request_id(handler):
    value = str(handler.headers.get("X-Request-Id", "")).strip()
    return value if _REQUEST_ID.fullmatch(value) else str(uuid4())


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


def _uuid_array(value, field):
    if not isinstance(value, list):
        raise ApiHttpError(422, "invalid_request", f"{field} must be an array")
    return [_uuid(item) for item in value]


def _read_json_body(handler):
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


def _route(method, segments, qs, payload, actor, settings):
    if method == "GET":
        return _get(segments, qs, actor, settings), 200
    if method == "POST":
        asynchronous = segments in {("executions",), ("regressions",)} or (
            len(segments) == 3
            and segments[0] == "tasks"
            and segments[2] == "run"
        )
        return _post(segments, payload, actor, settings), 202 if asynchronous else 200
    if method == "PUT":
        return _put(segments, payload, actor), 200
    if method == "DELETE":
        return _delete(segments, actor), 200
    raise ApiHttpError(405, "method_not_allowed", "Method is not allowed")


def _factory():
    return _session_factory()


def _readiness(settings):
    return ReadinessService(settings).check()


def _event_stream(factory):
    settings = ApiTestingSettings.from_env()
    return EventStream(factory, _shared_redis_client(settings.redis_url))


def _apifox_service(factory):
    return ApifoxService(
        ProviderService(factory),
        ApifoxDiscoveryAdapter(),
        ApifoxOpenApiAdapter(),
        SourceService(factory),
        session_factory=factory,
        environment_service=EnvironmentService(factory),
    )


@lru_cache(maxsize=4)
def _shared_redis_client(redis_url):
    return redis.Redis.from_url(redis_url, decode_responses=True)


def _get(segments, qs, actor, settings):
    if segments == ("readiness",):
        return _readiness(settings)
    factory = _factory()
    if segments == ("providers", "apifox", "credential"):
        return {
            "credential": _view(
                ProviderService(factory).get_apifox_credential(actor)
            )
        }
    if segments == ("projects",):
        with factory() as session:
            projects = session.scalars(
                select(ApiProject).where(ApiProject.owner_id == actor).order_by(ApiProject.created_at)
            ).all()
        return {"projects": [_project_view(item) for item in projects]}
    if segments == ("workspace",):
        with factory() as session:
            workspace = session.scalar(select(ApiWorkspace).where(ApiWorkspace.owner_id == actor))
        return {"workspace": _workspace_view(workspace) if workspace else None}
    if segments == ("context-options",):
        with factory() as session:
            return ContextRepository(session).list_options(actor)
    if segments == ("tasks", "active"):
        project_id = _uuid(qs.get("project_id", ""))
        _scope_project(factory, project_id, actor)
        return {"task": _view(TestTaskService(factory).get_active(project_id, actor))}
    if segments == ("tasks",):
        project_id = _uuid(qs.get("project_id", ""))
        _scope_project(factory, project_id, actor)
        return {"tasks": _view(TestTaskService(factory).list(project_id, actor))}
    if segments == ("environments",):
        project_id = _uuid(qs.get("project_id", ""))
        _scope_project(factory, project_id, actor)
        return {
            "environments": [
                _view(item)
                for item in EnvironmentService(factory).list_assets(
                    project_id, actor, qs.get("status", "active")
                )
            ]
        }
    if segments == ("executions",):
        project_id = _uuid(qs.get("project_id", ""))
        _scope_project(factory, project_id, actor)
        try:
            limit = int(qs.get("limit", "50"))
        except (TypeError, ValueError):
            raise ApiHttpError(400, "invalid_request", "Execution list limit is invalid")
        return {
            "executions": [
                _view(item)
                for item in ExecutionService(
                    factory, event_stream=_event_stream(factory)
                ).list(project_id, actor, limit)
            ]
        }
    if segments == ("ai-jobs", "latest"):
        project_id = _uuid(qs.get("project_id", ""))
        _scope_project(factory, project_id, actor)
        return {
            "job": _view(
                AiCaseService(factory).get_latest_incomplete_job(project_id)
            )
        }
    if len(segments) == 2 and segments[0] == "executions":
        _scope_execution(factory, _uuid(segments[1]), actor)
        return {"execution": _view(ExecutionService(factory, event_stream=_event_stream(factory)).get(_uuid(segments[1])))}
    if len(segments) == 2 and segments[0] == "cases":
        _scope_case(factory, _uuid(segments[1]), actor)
        return {"case": _view(CaseService(factory).get_case(_uuid(segments[1])))}
    if len(segments) == 2 and segments[0] == "case-versions":
        _scope_case_version(factory, _uuid(segments[1]), actor)
        return {"case_version": _view(CaseService(factory).get_version(_uuid(segments[1])))}
    if len(segments) == 2 and segments[0] == "ai-jobs":
        _scope_ai_job_record(factory, _uuid(segments[1]), actor)
        return {"job": _view(AiCaseService(factory).get_job(_uuid(segments[1])))}
    if len(segments) == 2 and segments[0] == "environments":
        _scope_environment(factory, _uuid(segments[1]), actor)
        return {"environment": _view(EnvironmentService(factory).get_environment(_uuid(segments[1])))}
    if len(segments) == 3 and segments[0] == "environments" and segments[2] == "revisions":
        environment_id = _uuid(segments[1])
        _scope_environment(factory, environment_id, actor)
        return {
            "revisions": [
                _view(item)
                for item in EnvironmentService(factory).list_revisions(
                    environment_id, actor
                )
            ]
        }
    if len(segments) == 2 and segments[0] == "environment-revisions":
        _scope_environment_revision(factory, _uuid(segments[1]), actor)
        return {"environment_revision": _view(EnvironmentService(factory).get_revision(_uuid(segments[1])))}
    if len(segments) == 2 and segments[0] == "source-revisions":
        _scope_source_revision(factory, _uuid(segments[1]), actor)
        return {"source_revision": _view(SourceService(factory).get_revision(_uuid(segments[1])))}
    if segments == ("cases",):
        revision_id = _uuid(qs.get("source_revision_id", ""))
        _scope_source_revision(factory, revision_id, actor)
        versions = CaseService(factory).list_active_versions_for_source_revision(
            revision_id, actor
        )
        return {"case_versions": [_view(item) for item in versions]}
    if segments == ("baselines",):
        project_id = _uuid(qs.get("project_id", ""))
        _scope_project(factory, project_id, actor)
        return {
            "baselines": [
                _view(item)
                for item in CaseService(factory).list_active_baselines(
                    project_id,
                    actor,
                )
            ]
        }
    if segments == ("notifications", "feishu"):
        project_id = _uuid(qs.get("project_id", ""))
        _scope_project(factory, project_id, actor)
        return {
            "notification": _view(
                NotificationService(factory).get_feishu(project_id, actor)
            )
        }
    if segments == ("endpoints",):
        revision_id = _uuid(qs.get("source_revision_id", ""))
        _scope_source_revision(factory, revision_id, actor)
        with factory() as session:
            endpoints = session.scalars(select(ApiSourceEndpoint).where(ApiSourceEndpoint.revision_id == revision_id)).all()
        return {"endpoints": [_endpoint_view(item) for item in endpoints]}
    raise ApiHttpError(404, "not_found", "Resource was not found")


def _post(segments, payload, actor, settings):
    factory = _factory()
    if segments == ("tasks",):
        return {"task": _view(TestTaskService(factory).create_context(actor, payload, actor))}
    if len(segments) == 3 and segments[0] == "tasks" and segments[2] == "run":
        task_id = _uuid(segments[1])
        task_service = TestTaskService(factory)
        task = task_service.get(task_id, actor)
        execution = ExecutionService(
            factory, event_stream=_event_stream(factory)
        ).submit_active_baselines(
            {
                "project_id": task.project_id,
                "source_revision_id": task.source_revision_id,
                "environment_revision_id": task.environment_revision_id,
                "endpoint_ids": list(task.selected_endpoint_ids),
            },
            actor,
            _string(payload.get("idempotency_key"), "idempotency_key", 200),
            task=task,
        )
        task = task_service.attach_execution(task.id, execution.id, actor)
        _enqueue_execution(execution.id)
        return {"execution": _view(execution), "task": _view(task)}
    if segments == ("providers", "apifox", "projects"):
        return {"projects": _view(_apifox_service(factory).list_projects(actor))}
    if segments == ("providers", "apifox", "context"):
        project_id = _string(payload.get("project_id"), "project_id", 100)
        environment_id = str(payload.get("environment_id") or "").strip()
        return {
            "context": _view(
                _apifox_service(factory).get_context(
                    actor,
                    project_id,
                    preferred_environment_id=environment_id,
                )
            )
        }
    if segments == ("sources", "apifox", "preview"):
        project_id = _uuid(payload.get("project_id"))
        _scope_project(factory, project_id, actor)
        source_id = _optional_uuid(payload.get("source_id"))
        if source_id:
            source = _scope_source(factory, source_id, actor)
            if source.project_id != project_id:
                raise _not_found()
        request = {**payload, "project_id": project_id, "source_id": source_id}
        return {
            "preview": _view(
                _apifox_service(factory).preview_refresh(actor, request, actor)
            )
        }
    if (
        len(segments) == 4
        and segments[0] == "sources"
        and segments[1] == "apifox"
        and segments[3] == "activate"
    ):
        preview_id = _uuid(segments[2])
        _scope_source_preview(factory, preview_id, actor)
        return _view(
            _apifox_service(factory).activate_preview(actor, preview_id, actor)
        )
    if segments == ("projects",):
        name = _string(payload.get("name"), "name", 200)
        slug = _string(payload.get("slug"), "slug", 120)
        with factory.begin() as session:
            project = ApiProject(name=name, slug=slug, description=str(payload.get("description") or ""), **audit_fields(actor))
            session.add(project)
            session.flush()
            return {"project": _project_view(project)}
    if segments == ("sources", "preview"):
        project_id = _uuid(payload.get("project_id"))
        _scope_project(factory, project_id, actor)
        source_id = _optional_uuid(payload.get("source_id"))
        if source_id:
            source = _scope_source(factory, source_id, actor)
            if source.project_id != project_id:
                raise _not_found()
        return {"preview": _view(SourceService(factory).preview_refresh(project_id, source_id, _required_object(payload, "document"), actor))}
    if len(segments) == 3 and segments[0] == "sources" and segments[2] == "activate":
        _scope_source_preview(factory, _uuid(segments[1]), actor)
        return {"source_revision": _view(SourceService(factory).activate_preview(_uuid(segments[1]), actor))}
    if segments == ("environments", "import"):
        _scope_environment_import(factory, payload, actor)
        return {"environment": _view(EnvironmentService(factory).import_from_source(payload, actor))}
    if len(segments) == 3 and segments[0] == "environments" and segments[2] == "revisions":
        _scope_environment(factory, _uuid(segments[1]), actor)
        changes = payload["environment"] if "environment" in payload else payload
        return {"environment": _view(EnvironmentService(factory).create_revision(_uuid(segments[1]), changes, payload.get("secret_updates") or {}, actor))}
    if len(segments) == 3 and segments[0] == "environments" and segments[2] == "restore":
        environment_id = _uuid(segments[1])
        _scope_environment(factory, environment_id, actor)
        return {
            "environment": _view(
                EnvironmentService(factory).restore(environment_id, actor)
            )
        }
    if (
        len(segments) == 3
        and segments[0] == "environment-revisions"
        and segments[2] == "restore"
    ):
        revision_id = _uuid(segments[1])
        _scope_environment_revision(factory, revision_id, actor)
        return {
            "environment_revision": _view(
                EnvironmentService(factory).restore_revision(revision_id, actor)
            )
        }
    if segments == ("cases",):
        _scope_endpoint(factory, _uuid(payload.get("endpoint_id")), actor)
        return {"case_version": _view(CaseService(factory).create_draft(_uuid(payload.get("endpoint_id")), _required_object(payload, "case"), payload.get("origin", "manual"), actor))}
    if len(segments) == 3 and segments[0] == "cases" and segments[2] == "versions":
        _scope_case(factory, _uuid(segments[1]), actor)
        return {"case_version": _view(CaseService(factory).create_version(_uuid(segments[1]), _required_object(payload, "case"), actor))}
    if len(segments) == 3 and segments[0] == "case-versions" and segments[2] == "validate":
        _scope_case_version(factory, _uuid(segments[1]), actor)
        return {"validation": _view(CaseService(factory).validate_case(_uuid(segments[1]), payload.get("environment_metadata") or {}))}
    if len(segments) == 3 and segments[0] == "case-versions" and segments[2] == "baseline":
        _scope_case_version(factory, _uuid(segments[1]), actor)
        _scope_execution_case(factory, _uuid(payload.get("debug_execution_case_id")), actor)
        return {"baseline": _view(CaseService(factory).adopt_baseline(_uuid(segments[1]), _uuid(payload.get("debug_execution_case_id")), actor))}
    if segments == ("baselines", "bulk-group"):
        return {
            "baselines": [
                _view(item)
                for item in CaseService(factory).update_baseline_group(
                    _uuid_array(payload.get("baseline_ids"), "baseline_ids"),
                    _string(payload.get("group_name"), "group_name", 120),
                    actor,
                )
            ]
        }
    if segments == ("executions",):
        task_id = _optional_uuid(payload.get("task_id"))
        task_service = TestTaskService(factory)
        if task_id:
            task = task_service.get(task_id, actor)
            if (
                task.project_id != payload.get("project_id")
                or task.source_revision_id != payload.get("source_revision_id")
                or task.environment_revision_id
                != payload.get("environment_revision_id")
            ):
                raise TestTaskScopeError("execution context does not match this task")
        request = _execution_request(payload)
        _scope_execution_request(factory, request, actor)
        execution = ExecutionService(factory, event_stream=_event_stream(factory)).submit(
            request,
            actor,
            _string(payload.get("idempotency_key"), "idempotency_key", 200),
            task=task if task_id else None,
        )
        if task_id:
            task_service.attach_execution(task_id, execution.id, actor)
        _enqueue_execution(execution.id)
        return {"execution": _view(execution)}
    if segments == ("executions", "archive"):
        execution_ids = _uuid_array(payload.get("execution_ids"), "execution_ids")
        for execution_id in execution_ids:
            _scope_execution(factory, execution_id, actor)
        return {
            "executions": [
                _view(item)
                for item in ExecutionService(
                    factory, event_stream=_event_stream(factory)
                ).archive_many(execution_ids, actor)
            ]
        }
    if segments == ("regressions",):
        regression = {
            "project_id": _uuid(payload.get("project_id")),
            "source_revision_id": _uuid(payload.get("source_revision_id")),
            "environment_revision_id": _uuid(payload.get("environment_revision_id")),
        }
        if payload.get("baseline_ids") is not None:
            regression["baseline_ids"] = _uuid_array(payload.get("baseline_ids"), "baseline_ids")
        _scope_execution_request(
            factory,
            {**regression, "case_version_ids": [], "execution_type": "baseline_regression", "overrides": {}},
            actor,
            validate_cases=False,
        )
        execution = ExecutionService(
            factory, event_stream=_event_stream(factory)
        ).submit_active_baselines(
            regression,
            actor,
            _string(payload.get("idempotency_key"), "idempotency_key", 200),
        )
        _enqueue_execution(execution.id)
        return {"execution": _view(execution)}
    if len(segments) == 3 and segments[0] == "executions" and segments[2] == "cancel":
        _scope_execution(factory, _uuid(segments[1]), actor)
        return {"execution": _view(ExecutionService(factory, event_stream=_event_stream(factory)).cancel(_uuid(segments[1]), actor))}
    if len(segments) == 3 and segments[0] == "executions" and segments[2] == "sse-ticket":
        _scope_execution(factory, _uuid(segments[1]), actor)
        return {"ticket": _issue_sse_ticket(settings, actor, _uuid(segments[1]))}
    if len(segments) == 3 and segments[0] == "executions" and segments[2] == "notify":
        execution_id = _uuid(segments[1])
        _scope_execution(factory, execution_id, actor)
        channel_type = str(payload.get("channel_type") or "feishu").strip()
        if channel_type != "feishu":
            raise ApiHttpError(422, "invalid_request", "Notification channel is not supported")
        notification = NotificationService(factory).send_execution_report(
            execution_id, actor
        )
        _event_stream(factory).append(
            execution_id,
            "notification_sent",
            {
                "channel_type": notification.channel_type,
                "message": notification.message,
            },
        )
        return {
            "notification": _view(
                notification
            )
        }
    if segments == ("ai-jobs",):
        endpoint_ids = _uuid_array(payload.get("endpoint_ids"), "endpoint_ids")
        environment_revision_id = _uuid(payload.get("environment_revision_id"))
        task_id = _optional_uuid(payload.get("task_id"))
        task_service = TestTaskService(factory)
        if task_id:
            task = task_service.get(task_id, actor)
            if (
                task.environment_revision_id != environment_revision_id
                or not set(endpoint_ids).issubset(set(task.selected_endpoint_ids))
            ):
                raise TestTaskScopeError("AI request does not match this task")
        project_id = _scope_ai_job(factory, endpoint_ids, environment_revision_id, actor)
        _scope_project(factory, project_id, actor)
        service = AiCaseService(factory)
        job = service.submit(endpoint_ids, environment_revision_id, actor, payload.get("model_config"), payload.get("intent", ""))
        if task_id:
            task_service.attach_ai_job(task_id, job.id, actor)
        try:
            _enqueue_ai_job(job.id)
        except Exception:
            service.mark_enqueue_failure(job.id, actor)
            raise ApiHttpError(
                503,
                "ai_enqueue_unavailable",
                "AI generation queue is unavailable",
            )
        return {"job": _view(job)}
    raise ApiHttpError(404, "not_found", "Resource was not found")


def _put(segments, payload, actor):
    if segments == ("providers", "apifox", "credential"):
        return {
            "credential": _view(
                ProviderService(_factory()).save_apifox_credential(
                    actor, payload.get("token"), actor
                )
            )
        }
    if segments == ("notifications", "feishu"):
        project_id = _uuid(payload.get("project_id"))
        factory = _factory()
        _scope_project(factory, project_id, actor)
        return {
            "notification": _view(
                NotificationService(factory).save_feishu(project_id, payload, actor)
            )
        }
    if len(segments) == 2 and segments[0] == "projects":
        project_id = _uuid(segments[1])
        name = _string(payload.get("name"), "name", 200)
        description = str(payload.get("description") or "").strip()
        factory = _factory()
        _scope_project(factory, project_id, actor)
        with factory.begin() as session:
            project = session.scalar(
                select(ApiProject)
                .where(ApiProject.id == project_id, ApiProject.owner_id == actor)
                .with_for_update()
            )
            if project is None:
                raise _not_found()
            project.name = name
            project.description = description
            project.updated_by = actor
            session.flush()
            return {"project": _project_view(project)}
    if len(segments) == 2 and segments[0] == "baselines":
        return {
            "baseline": _view(
                CaseService(_factory()).update_baseline_group(
                    [_uuid(segments[1])],
                    _string(payload.get("group_name"), "group_name", 120),
                    actor,
                )[0]
            )
        }
    if len(segments) == 2 and segments[0] == "tasks":
        return {
            "task": _view(
                TestTaskService(_factory()).update_context(
                    _uuid(segments[1]), actor, payload, actor
                )
            )
        }
    if len(segments) == 3 and segments[0] == "tasks" and segments[2] == "name":
        return {
            "task": _view(
                TestTaskService(_factory()).rename(
                    _uuid(segments[1]),
                    payload.get("name"),
                    actor,
                )
            )
        }
    if segments != ("workspace",):
        raise ApiHttpError(404, "not_found", "Resource was not found")
    context = _workspace_input(payload)
    factory = _factory()
    _scope_workspace_context(factory, context, actor)
    with factory.begin() as session:
        workspace = session.scalar(select(ApiWorkspace).where(ApiWorkspace.owner_id == actor).with_for_update())
        if workspace is None:
            workspace = ApiWorkspace(**context, **audit_fields(actor))
            session.add(workspace)
        else:
            for field, value in context.items():
                setattr(workspace, field, value)
            workspace.updated_by = actor
        session.flush()
        return {"workspace": _workspace_view(workspace)}


def _delete(segments, actor):
    factory = _factory()
    if len(segments) == 2 and segments[0] == "cases":
        case_id = _uuid(segments[1])
        _scope_case(factory, case_id, actor)
        return {"case": _view(CaseService(factory).archive_case(case_id, actor))}
    if len(segments) == 2 and segments[0] == "executions":
        execution_id = _uuid(segments[1])
        _scope_execution(factory, execution_id, actor)
        return {"execution": _view(ExecutionService(factory, event_stream=_event_stream(factory)).archive(execution_id, actor))}
    if len(segments) == 2 and segments[0] == "baselines":
        return {"baseline": _view(CaseService(factory).archive_baseline(_uuid(segments[1]), actor))}
    if len(segments) == 2 and segments[0] == "environments":
        environment_id = _uuid(segments[1])
        _scope_environment(factory, environment_id, actor)
        return {
            "environment": _view(
                EnvironmentService(factory).archive(environment_id, actor)
            )
        }
    if len(segments) == 2 and segments[0] == "projects":
        project_id = _uuid(segments[1])
        _scope_project(factory, project_id, actor)
        with factory.begin() as session:
            project = session.scalar(
                select(ApiProject)
                .where(ApiProject.id == project_id, ApiProject.owner_id == actor)
                .with_for_update()
            )
            if project is None:
                raise _not_found()
            project.status = "archived"
            project.updated_by = actor
            session.flush()
            return {"project": _project_view(project)}
    raise ApiHttpError(404, "not_found", "Resource was not found")


def _scope_project(factory, project_id, actor):
    with factory() as session:
        project = session.scalar(select(ApiProject).where(ApiProject.id == project_id, ApiProject.owner_id == actor))
    if project is None:
        raise _not_found()
    return project


def _scope_source(factory, source_id, actor):
    with factory() as session:
        source = session.scalar(select(ApiSource).join(ApiProject, ApiSource.project_id == ApiProject.id).where(ApiSource.id == source_id, ApiProject.owner_id == actor))
    if source is None:
        raise _not_found()
    return source


def _scope_source_revision(factory, revision_id, actor):
    with factory() as session:
        revision = session.scalar(select(ApiSourceRevision).join(ApiSource, ApiSourceRevision.source_id == ApiSource.id).join(ApiProject, ApiSource.project_id == ApiProject.id).where(ApiSourceRevision.id == revision_id, ApiProject.owner_id == actor))
    if revision is None:
        raise _not_found()
    return revision


def _scope_source_preview(factory, preview_id, actor):
    with factory() as session:
        preview = session.scalar(select(ApiSourceDiff).join(ApiSource, ApiSourceDiff.source_id == ApiSource.id).join(ApiProject, ApiSource.project_id == ApiProject.id).where(ApiSourceDiff.id == preview_id, ApiProject.owner_id == actor))
    if preview is None:
        raise _not_found()
    return preview


def _scope_endpoint(factory, endpoint_id, actor):
    with factory() as session:
        endpoint = session.scalar(select(ApiSourceEndpoint).join(ApiSourceRevision, ApiSourceEndpoint.revision_id == ApiSourceRevision.id).join(ApiSource, ApiSourceRevision.source_id == ApiSource.id).join(ApiProject, ApiSource.project_id == ApiProject.id).where(ApiSourceEndpoint.id == endpoint_id, ApiProject.owner_id == actor))
    if endpoint is None:
        raise _not_found()
    return endpoint


def _scope_environment(factory, environment_id, actor):
    with factory() as session:
        environment = session.scalar(select(ApiEnvironment).join(ApiProject, ApiEnvironment.project_id == ApiProject.id).where(ApiEnvironment.id == environment_id, ApiProject.owner_id == actor))
    if environment is None:
        raise _not_found()
    return environment


def _scope_ai_job_record(factory, job_id, actor):
    with factory() as session:
        job = session.scalar(
            select(ApiAiJob)
            .join(ApiProject, ApiAiJob.project_id == ApiProject.id)
            .where(ApiAiJob.id == job_id, ApiProject.owner_id == actor)
        )
    if job is None:
        raise _not_found()
    return job


def _scope_environment_revision(factory, revision_id, actor):
    with factory() as session:
        revision = session.scalar(select(ApiEnvironmentRevision).join(ApiEnvironment, ApiEnvironmentRevision.environment_id == ApiEnvironment.id).join(ApiProject, ApiEnvironment.project_id == ApiProject.id).where(ApiEnvironmentRevision.id == revision_id, ApiProject.owner_id == actor))
    if revision is None:
        raise _not_found()
    return revision


def _scope_case(factory, case_id, actor):
    with factory() as session:
        case = session.scalar(select(ApiCase).join(ApiProject, ApiCase.project_id == ApiProject.id).where(ApiCase.id == case_id, ApiProject.owner_id == actor))
    if case is None:
        raise _not_found()
    return case


def _scope_case_version(factory, version_id, actor):
    with factory() as session:
        version = session.scalar(select(ApiCaseVersion).join(ApiCase, ApiCaseVersion.case_id == ApiCase.id).join(ApiProject, ApiCase.project_id == ApiProject.id).where(ApiCaseVersion.id == version_id, ApiProject.owner_id == actor))
    if version is None:
        raise _not_found()
    return version


def _scope_execution(factory, execution_id, actor):
    with factory() as session:
        execution = session.scalar(select(ApiExecution).join(ApiProject, ApiExecution.project_id == ApiProject.id).where(ApiExecution.id == execution_id, ApiProject.owner_id == actor))
    if execution is None:
        raise _not_found()
    return execution


def _scope_execution_case(factory, execution_case_id, actor):
    with factory() as session:
        execution_case = session.scalar(
            select(ApiExecutionCase)
            .join(ApiExecution, ApiExecutionCase.execution_id == ApiExecution.id)
            .join(ApiProject, ApiExecution.project_id == ApiProject.id)
            .where(ApiExecutionCase.id == execution_case_id, ApiProject.owner_id == actor)
        )
    if execution_case is None:
        raise _not_found()
    return execution_case


def _scope_environment_import(factory, payload, actor):
    project_id = _uuid(payload.get("project_id"))
    _scope_project(factory, project_id, actor)
    source_id = _optional_uuid(payload.get("source_id"))
    source_revision_id = _optional_uuid(payload.get("source_revision_id"))
    if source_id:
        source = _scope_source(factory, source_id, actor)
        if source.project_id != project_id:
            raise _not_found()
    if source_revision_id:
        revision = _scope_source_revision(factory, source_revision_id, actor)
        if source_id and revision.source_id != source_id:
            raise _not_found()


def _scope_execution_request(factory, request, actor, *, validate_cases=True):
    project = _scope_project(factory, request["project_id"], actor)
    source_revision = _scope_source_revision(factory, request["source_revision_id"], actor)
    environment_revision = _scope_environment_revision(factory, request["environment_revision_id"], actor)
    if _scope_source(factory, source_revision.source_id, actor).project_id != project.id:
        raise _not_found()
    if _scope_environment(factory, environment_revision.environment_id, actor).project_id != project.id:
        raise _not_found()
    if not validate_cases:
        return
    for version_id in request["case_version_ids"]:
        version = _scope_case_version(factory, version_id, actor)
        case = _scope_case(factory, version.case_id, actor)
        if case.project_id != project.id:
            raise _not_found()


def _scope_ai_job(factory, endpoint_ids, environment_revision_id, actor):
    endpoints = [_scope_endpoint(factory, item, actor) for item in endpoint_ids]
    environment_revision = _scope_environment_revision(factory, environment_revision_id, actor)
    project_id = _scope_environment(factory, environment_revision.environment_id, actor).project_id
    for endpoint in endpoints:
        revision = _scope_source_revision(factory, endpoint.revision_id, actor)
        if _scope_source(factory, revision.source_id, actor).project_id != project_id:
            raise _not_found()
    return project_id


def _workspace_input(payload):
    fields = {"project_id", "source_revision_id", "environment_revision_id"}
    if set(payload) != fields:
        raise ApiHttpError(422, "invalid_request", "Workspace context fields are invalid")
    return {field: _optional_uuid(payload.get(field)) for field in fields}


def _scope_workspace_context(factory, context, actor):
    project_id = context["project_id"]
    if project_id is None:
        if context["source_revision_id"] or context["environment_revision_id"]:
            raise ApiHttpError(422, "invalid_request", "Workspace project is required")
        return
    _scope_project(factory, project_id, actor)
    if context["source_revision_id"]:
        source = _scope_source(factory, _scope_source_revision(factory, context["source_revision_id"], actor).source_id, actor)
        if source.project_id != project_id:
            raise _not_found()
    if context["environment_revision_id"]:
        environment = _scope_environment(factory, _scope_environment_revision(factory, context["environment_revision_id"], actor).environment_id, actor)
        if environment.project_id != project_id:
            raise _not_found()


def _ticket_client(settings):
    try:
        client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
        client.ping()
        return client
    except redis.RedisError:
        raise ApiHttpError(503, "sse_unavailable", "SSE ticket service is unavailable")


def _ticket_key(ticket):
    return f"api-testing:sse-ticket:{ticket}"


def _issue_sse_ticket(settings, actor, execution_id):
    ticket = secrets.token_urlsafe(32)
    try:
        stored = _ticket_client(settings).set(_ticket_key(ticket), json.dumps({"owner_id": actor, "execution_id": execution_id}), ex=SSE_TICKET_TTL_SECONDS, nx=True)
    except redis.RedisError:
        raise ApiHttpError(503, "sse_unavailable", "SSE ticket service is unavailable")
    if not stored:
        raise ApiHttpError(503, "sse_unavailable", "SSE ticket service is unavailable")
    return ticket


def _consume_sse_ticket(settings, ticket, execution_id):
    if not isinstance(ticket, str) or not 32 <= len(ticket) <= 256:
        raise ApiHttpError(401, "unauthorized", "Authentication is required")
    try:
        redeemed = _ticket_client(settings).eval(
            _SSE_TICKET_REDEEM_LUA,
            1,
            _ticket_key(ticket),
            execution_id,
            SSE_TICKET_TTL_SECONDS,
        )
    except redis.RedisError:
        raise ApiHttpError(503, "sse_unavailable", "SSE ticket service is unavailable")
    if not isinstance(redeemed, (list, tuple)) or len(redeemed) != 2 or redeemed[0] not in (1, "1"):
        raise ApiHttpError(401, "unauthorized", "Authentication is required")
    try:
        value = json.loads(redeemed[1])
    except (TypeError, ValueError):
        raise ApiHttpError(401, "unauthorized", "Authentication is required")
    if value.get("execution_id") != execution_id or not isinstance(value.get("owner_id"), str):
        raise ApiHttpError(401, "unauthorized", "Authentication is required")
    return value["owner_id"]


def _stream_events(handler, execution_id, request_id, actor, *, after=None):
    last_event_id = str(
        after if after is not None else handler.headers.get("Last-Event-ID", "0")
    ).strip() or "0"
    try:
        after_id = int(last_event_id)
    except ValueError:
        raise ApiHttpError(400, "invalid_last_event_id", "Last-Event-ID must be a non-negative integer")
    if after_id < 0:
        raise ApiHttpError(400, "invalid_last_event_id", "Last-Event-ID must be a non-negative integer")
    factory = _factory()
    execution = _scope_execution(factory, execution_id, actor)
    handler.send_response(200)
    handler._cors()
    handler.send_header("Content-Type", "text/event-stream; charset=utf-8")
    handler.send_header("Cache-Control", "no-cache")
    handler.send_header("X-Request-Id", request_id)
    handler.end_headers()
    stream = _event_stream(factory)
    if execution.state in TERMINAL_EXECUTION_STATES:
        # A reconnect can race the terminal transition. Drain only durable
        # backlog after Last-Event-ID, without waiting, then close the stream.
        for event in stream.read(execution_id, after_id, 0):
            _write_sse(
                handler, event.sequence, event.type, event.payload, event.created_at
            )
        return
    while True:
        events = stream.read(execution_id, after_id, SSE_HEARTBEAT_SECONDS * 1000)
        if events:
            for event in events:
                _write_sse(
                    handler, event.sequence, event.type, event.payload, event.created_at
                )
                after_id = event.sequence
                if event.type == "execution_finished":
                    return
        else:
            _write_sse(handler, None, "heartbeat", {"request_id": request_id})
        if _scope_execution(factory, execution_id, actor).state in TERMINAL_EXECUTION_STATES:
            return


def _write_sse(handler, sequence, event_type, payload, created_at=None):
    lines = []
    if sequence is not None:
        lines.append(f"id: {sequence}")
    lines.append(f"event: {event_type}")
    event_payload = dict(payload)
    if created_at is not None:
        event_payload["_event_created_at"] = created_at.isoformat()
    lines.append("data: " + json.dumps(_json_value(event_payload), ensure_ascii=False, separators=(",", ":")))
    handler.wfile.write(("\n".join(lines) + "\n\n").encode("utf-8"))
    handler.wfile.flush()


def _enqueue_execution(execution_id):
    from .tasks import execute_api_testing
    execute_api_testing.delay(execution_id)


def _enqueue_ai_job(job_id):
    from .tasks import generate_api_cases
    generate_api_cases.delay(job_id)


def _is_execution_events(segments):
    return len(segments) == 3 and segments[0] == "executions" and segments[2] == "events"


def _success(handler, data, request_id, status):
    _send_json(handler, status, {"ok": True, "data": _json_value(data), "request_id": request_id}, request_id)


def _failure(handler, error, request_id):
    _send_json(handler, error.status, {"ok": False, "error": {"code": error.code, "message": error.message, "details": _json_value(error.details)}, "request_id": request_id}, request_id)


def _send_json(handler, status, payload, request_id):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
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


def _domain_error(error):
    if isinstance(error, ApiHttpError):
        return error
    if isinstance(error, (EndpointNotFoundError, CaseNotFoundError, EnvironmentNotFoundError, SourceNotFoundError, SourcePreviewNotFoundError, ExecutionNotFoundError, AiJobNotFoundError, TestTaskNotFoundError)):
        return _not_found()
    if isinstance(error, NotificationNotConfiguredError):
        return ApiHttpError(422, "notification_not_configured", "请先配置并启用飞书群机器人 Webhook")
    if isinstance(error, ExecutionConflictError) and str(error).startswith(
        "no active baselines"
    ):
        return ApiHttpError(
            409,
            "baseline_required",
            "请先调试通过并采纳至少一条用例为基线",
        )
    if isinstance(error, (ExecutionConflictError, BaselineGateError, SourcePreviewExpiredError, SourcePreviewStateError, StaleSourcePreviewError)):
        return ApiHttpError(409, "conflict", "Resource state conflicts with this request")
    if isinstance(error, ProviderCredentialNotFoundError):
        return ApiHttpError(
            422, "apifox_token_required", "请先保存 Apifox 访问令牌"
        )
    if isinstance(error, ApifoxDiscoveryError):
        return ApiHttpError(
            error.http_status,
            "apifox_" + str(error.code).lower(),
            str(error),
            {"manual_fallback": True},
        )
    if isinstance(error, ApifoxOpenApiError):
        return ApiHttpError(502, "apifox_export_failed", str(error))
    if isinstance(error, ApifoxInputError):
        return ApiHttpError(422, "apifox_validation_failed", str(error))
    if isinstance(error, OpenApiValidationError):
        return ApiHttpError(
            422,
            "openapi_validation_failed",
            "接口定义校验失败：%s" % str(error),
        )
    if isinstance(error, CasePayloadError):
        message = str(error)
        if "valid HTTP status code values" in message:
            message = "HTTP 状态码只能是 100 到 599；业务码请改用响应 JSON 字段断言（例如 $.code）"
        else:
            message = "用例内容校验失败：%s" % message
        return ApiHttpError(422, "case_validation_failed", message)
    if isinstance(error, (OperationalError, InterfaceError)):
        return ApiHttpError(
            503,
            "database_unavailable",
            "API 测试数据库暂时不可用，请稍后重试",
        )
    if isinstance(error, redis.RedisError):
        return ApiHttpError(
            503,
            "redis_unavailable",
            "API 测试任务队列暂时不可用，请稍后重试",
        )
    if isinstance(error, TestTaskScopeError):
        return ApiHttpError(409, "task_scope_conflict", "测试任务范围与当前请求不一致")
    if isinstance(error, (ValueError, EnvironmentInputError, AiJobInputError, ProviderCredentialInputError, TestTaskInputError, NotificationInputError)):
        return ApiHttpError(422, "invalid_request", "Request validation failed")
    return ApiHttpError(500, "internal_error", "Internal server error")


def _not_found():
    return ApiHttpError(404, "not_found", "Resource was not found")


def _execution_request(payload):
    return {"project_id": _uuid(payload.get("project_id")), "source_revision_id": _uuid(payload.get("source_revision_id")), "environment_revision_id": _uuid(payload.get("environment_revision_id")), "case_version_ids": _uuid_array(payload.get("case_version_ids"), "case_version_ids"), "execution_type": _string(payload.get("execution_type", "debug"), "execution_type", 32), "overrides": payload.get("overrides") or {}}


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
    return {"id": endpoint.id, "revision_id": endpoint.revision_id, "operation_id": endpoint.operation_id, "method": endpoint.method, "path": endpoint.path, "summary": endpoint.summary, "tags": endpoint.tags, "operation": endpoint.operation}


def _workspace_view(workspace):
    return {"project_id": workspace.project_id, "source_revision_id": workspace.source_revision_id, "environment_revision_id": workspace.environment_revision_id}


def _view(value):
    return _json_value(value)


def _json_value(value):
    if isinstance(value, MappingProxyType):
        return {key: _json_value(item) for key, item in value.items()}
    if is_dataclass(value):
        return {field.name: _json_value(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_value(item) for item in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
