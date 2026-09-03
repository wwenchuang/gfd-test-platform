"""Narrow authenticated HTTP boundary for user-facing performance testing."""

import base64
import binascii
import copy
import logging

from sqlalchemy import select

from . import access
from .db import _session_factory
from .http import ApiHttpError, API_PREFIX, _domain_error, _failure, _read_json_body, _request_id, _success
from .models.load_testing import (
    ApiLoadAgent,
    ApiLoadAgentEnrollment,
    ApiLoadAiAnalysis,
    ApiLoadDataset,
    ApiLoadEvent,
    ApiLoadRun,
    ApiLoadRunShard,
    ApiLoadScenario,
    ApiLoadScenarioVersion,
)
from .repositories.load_testing_repository import LoadTestingRepository
from .services.case_service import CaseService
from .services.environment_service import EnvironmentService
from .services.load_agent_service import LoadAgentError, LoadAgentService
from .services.load_ai_analysis_service import LoadAiAnalysisError, LoadAiAnalysisService
from .services.load_allocator import calibration_state
from .services.load_dataset_service import LoadDatasetError, LoadDatasetService
from .services.load_preflight_service import FunctionalLoadStepRunner, LoadPreflightService
from .services.load_report_service import LoadReportError, LoadReportService
from .services.load_run_service import LoadRunError, LoadRunService
from .services.load_scenario_compiler import COMPILER_VERSION
from .services.load_scenario_service import LoadScenarioService


LOAD_ROUTE_HEADS = frozenset({
    "load-scenarios", "load-scenario-versions", "load-datasets", "load-runs", "load-agents", "load-agent-enrollments",
})
logger = logging.getLogger(__name__)


def _factory():
    return _session_factory()


def dispatch_load_testing_request(handler, method, path, query, actor_id):
    """Handle matched user load-testing routes and return whether one was matched."""
    if not path.startswith(API_PREFIX):
        return False
    suffix = path[len(API_PREFIX):].strip("/")
    segments = tuple(part for part in suffix.split("/") if part)
    if not segments or segments[0] not in LOAD_ROUTE_HEADS:
        return False
    request_id = _request_id(handler)
    try:
        payload = _read_json_body(handler) if method in {"POST", "PUT", "DELETE"} else {}
        data, status = handle_load_testing_request(method, segments, query, payload, actor_id, _factory())
        _success(handler, data, request_id, status)
    except ApiHttpError as error:
        _failure(handler, error, request_id)
    except Exception as error:
        mapped = _load_error(error)
        logger.error(
            "Load testing user request failed request_id=%s method=%s route=%s error_code=%s exception_type=%s",
            request_id, method, path, mapped.code, type(error).__name__,
        )
        _failure(handler, mapped, request_id)
    return True


def handle_load_testing_request(method, segments, query, payload, actor_id, factory):
    """Return a JSON-ready payload and status for one authenticated user request."""
    head = segments[0] if segments else ""
    if head not in LOAD_ROUTE_HEADS:
        raise ApiHttpError(404, "not_found", "Resource was not found")
    access.authorize_http(actor_id, method, segments)

    if method == "GET":
        return _get(factory, segments, query, actor_id), 200
    if method == "POST":
        return _post(factory, segments, payload, actor_id)
    if method == "PUT":
        return _put(factory, segments, payload, actor_id), 200
    if method == "DELETE":
        return _delete(factory, segments, payload, actor_id), 200
    raise ApiHttpError(405, "method_not_allowed", "Method is not allowed")


def _get(factory, segments, query, actor):
    if segments == ("load-scenarios",):
        project_id = _required(query, "project_id", "请选择接口项目")
        _project(factory, project_id, actor, "api.loadtest.view")
        with factory() as session:
            rows = tuple(session.scalars(select(ApiLoadScenario).where(
                ApiLoadScenario.project_id == project_id,
                ApiLoadScenario.status != "archived",
                access.resource_predicate(actor, ApiLoadScenario),
            ).order_by(ApiLoadScenario.updated_at.desc(), ApiLoadScenario.id)))
        return {"scenarios": [_scenario_view(item) for item in rows]}
    if len(segments) == 2 and segments[0] == "load-scenarios":
        scenario = _scenario(factory, segments[1], actor)
        return {"scenario": _scenario_view(scenario)}
    if len(segments) == 3 and segments[0] == "load-scenarios" and segments[2] == "versions":
        scenario = _scenario(factory, segments[1], actor)
        with factory() as session:
            rows = tuple(session.scalars(select(ApiLoadScenarioVersion).where(
                ApiLoadScenarioVersion.scenario_id == scenario.id,
            ).order_by(ApiLoadScenarioVersion.version_number.desc())))
        return {"versions": [_version_view(item) for item in rows]}
    if len(segments) == 2 and segments[0] == "load-scenario-versions":
        version, _ = _version(factory, segments[1], actor)
        return {"version": _version_view(version, include_definition=True)}
    if segments == ("load-datasets",):
        project_id = _required(query, "project_id", "请选择接口项目")
        _project(factory, project_id, actor, "api.loadtest.view")
        with factory() as session:
            rows = tuple(session.scalars(select(ApiLoadDataset).where(
                ApiLoadDataset.project_id == project_id,
                ApiLoadDataset.status != "archived",
                access.resource_predicate(actor, ApiLoadDataset),
            ).order_by(ApiLoadDataset.updated_at.desc(), ApiLoadDataset.id)))
        return {"datasets": [_dataset_view(item) for item in rows]}
    if segments == ("load-runs",):
        project_id = _required(query, "project_id", "请选择接口项目")
        _project(factory, project_id, actor, "api.loadtest.view")
        with factory() as session:
            rows = tuple(session.scalars(select(ApiLoadRun).where(
                ApiLoadRun.project_id == project_id,
                access.resource_predicate(actor, ApiLoadRun),
            ).order_by(ApiLoadRun.created_at.desc(), ApiLoadRun.id).limit(_limit(query))))
        return {"runs": [_run_view(item) for item in rows]}
    if len(segments) == 2 and segments[0] == "load-runs":
        run = _run(factory, segments[1], actor)
        with factory() as session:
            shards = tuple(session.scalars(select(ApiLoadRunShard).where(
                ApiLoadRunShard.run_id == run.id,
            ).order_by(ApiLoadRunShard.sequence)))
        return {"run": _run_view(run), "shards": [_shard_view(item) for item in shards]}
    if len(segments) == 3 and segments[0] == "load-runs" and segments[2] == "events":
        run = _run(factory, segments[1], actor)
        after = _nonnegative_int(query.get("after", 0), "after")
        with factory() as session:
            rows = tuple(session.scalars(select(ApiLoadEvent).where(
                ApiLoadEvent.run_id == run.id,
                ApiLoadEvent.sequence > after,
            ).order_by(ApiLoadEvent.sequence).limit(200)))
        return {"events": [_event_view(item) for item in rows], "terminal": run.state in {"finished", "failed", "cancelled"}}
    if len(segments) == 3 and segments[0] == "load-runs" and segments[2] == "report":
        _run(factory, segments[1], actor)
        return {"report": LoadReportService(factory).build(segments[1], actor)}
    if len(segments) == 3 and segments[0] == "load-runs" and segments[2] == "ai-analysis":
        run = _run(factory, segments[1], actor)
        with factory() as session:
            record = session.scalar(select(ApiLoadAiAnalysis).where(
                ApiLoadAiAnalysis.run_id == run.id,
            ).order_by(ApiLoadAiAnalysis.created_at.desc()).limit(1))
        return {"analysis": _analysis_view(record) if record else None}
    if segments == ("load-agents",):
        access.require_permission(actor, "api.loadtest.view")
        with factory() as session:
            rows = tuple(session.scalars(select(ApiLoadAgent).order_by(ApiLoadAgent.name, ApiLoadAgent.id)))
        return {"agents": [_agent_view(item) for item in rows]}
    if segments == ("load-agent-enrollments",):
        access.require_permission(actor, "api.loadtest.manage_agents")
        with factory() as session:
            rows = tuple(session.scalars(select(ApiLoadAgentEnrollment).order_by(
                ApiLoadAgentEnrollment.created_at.desc(), ApiLoadAgentEnrollment.id,
            ).limit(100)))
        return {"enrollments": [_enrollment_view(item) for item in rows]}
    raise ApiHttpError(404, "not_found", "Resource was not found")


def _post(factory, segments, payload, actor):
    if segments == ("load-scenarios",):
        access.require_permission(actor, "api.loadtest.edit")
        project_id = _required(payload, "project_id", "请选择接口项目")
        _project(factory, project_id, actor, "api.loadtest.edit")
        name = _text(payload.get("name"), "场景名称", 200)
        scenario_type = str(payload.get("scenario_type") or "single_interface")
        if scenario_type not in {"single_interface", "workflow"}:
            raise ApiHttpError(422, "invalid_request", "场景类型必须是单接口或业务链路")
        record = LoadTestingRepository(factory).create_scenario(project_id, name, scenario_type, actor)
        description = str(payload.get("description") or "").strip()[:2000]
        if description:
            with factory.begin() as session:
                current = session.get(ApiLoadScenario, record.id)
                current.description = description
                current.updated_by = actor
        return {"scenario": _scenario_view(record, description=description)}, 201
    if len(segments) == 3 and segments[0] == "load-scenarios" and segments[2] == "versions":
        scenario = _scenario(factory, segments[1], actor, "api.loadtest.edit")
        definition = payload.get("definition")
        admission = LoadScenarioService.validate_definition(definition)
        if not admission.accepted:
            raise ApiHttpError(422, "load_scenario_rejected", "场景未通过压测安全校验", {
                "issues": [_issue_view(item) for item in admission.issues],
            })
        record = LoadTestingRepository(factory).create_scenario_version(
            scenario.id, admission.definition, COMPILER_VERSION, actor,
        )
        with factory.begin() as session:
            current = session.get(ApiLoadScenarioVersion, record.id)
            current.source_snapshot = copy.deepcopy(admission.definition.get("source_snapshot") or {})
            current.validation_summary = {"accepted": True, "issues": []}
            session.flush()
            view = _version_view(current, include_definition=True)
        return {"version": view}, 201
    if segments == ("load-datasets",):
        project_id = _required(payload, "project_id", "请选择接口项目")
        _project(factory, project_id, actor, "api.loadtest.edit")
        encoded = payload.get("content_base64")
        if not isinstance(encoded, str) or not encoded:
            raise ApiHttpError(422, "invalid_request", "请选择CSV或JSON数据文件")
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ApiHttpError(422, "invalid_request", "数据文件内容不是有效Base64") from error
        result = LoadDatasetService(factory).import_bytes(
            project_id, payload.get("name"), payload.get("filename"), content,
            payload.get("usage_mode", "cycle"), actor,
        )
        return {"dataset": _simple_view(result)}, 201
    if segments == ("load-runs",):
        run = _run_service(factory).create(payload, actor)
        return {"run": _run_view(run)}, 201
    if len(segments) == 3 and segments[0] == "load-runs" and segments[2] == "preflight":
        run = _run_service(factory).preflight(segments[1], actor)
        return {"run": _run_view(run)}, 200
    if len(segments) == 3 and segments[0] == "load-runs" and segments[2] == "start":
        try:
            run = _run_service(factory).start(segments[1], actor)
        except LoadRunError as error:
            if error.code != "duplicate_start":
                raise
            run = _run(factory, segments[1], actor)
        return {"run": _run_view(run)}, 200
    if len(segments) == 3 and segments[0] == "load-runs" and segments[2] == "stop":
        run = _run_service(factory).stop(segments[1], payload.get("reason"), actor)
        return {"run": _run_view(run)}, 200
    if len(segments) == 3 and segments[0] == "load-runs" and segments[2] == "ai-analysis":
        _run(factory, segments[1], actor)
        force = payload.get("force", False)
        if not isinstance(force, bool):
            raise ApiHttpError(422, "invalid_request", "force必须是布尔值")
        from .tasks import dispatch_load_analysis
        record = LoadAiAnalysisService(factory, dispatcher=dispatch_load_analysis).request(segments[1], actor, force=force)
        return {"analysis": _analysis_view(record)}, 202
    if segments == ("load-agent-enrollments",):
        result = LoadAgentService(factory).create_enrollment(payload, actor)
        return {"enrollment": {
            "id": result.id,
            "token": result.token,
            "expires_at": _json_time(result.expires_at),
            "credential_notice": "注册令牌仅显示一次，请立即保存",
        }}, 201
    raise ApiHttpError(404, "not_found", "Resource was not found")


def _put(factory, segments, payload, actor):
    if len(segments) == 2 and segments[0] == "load-scenarios":
        scenario = _scenario(factory, segments[1], actor, "api.loadtest.edit")
        allowed = {"name", "description", "status"}
        if set(payload) - allowed:
            raise ApiHttpError(422, "invalid_request", "场景包含不支持的修改字段")
        with factory.begin() as session:
            current = session.get(ApiLoadScenario, scenario.id)
            if "name" in payload:
                current.name = _text(payload["name"], "场景名称", 200)
            if "description" in payload:
                current.description = str(payload["description"] or "").strip()[:2000]
            if "status" in payload:
                if payload["status"] not in {"active", "archived"}:
                    raise ApiHttpError(422, "invalid_request", "场景状态无效")
                current.status = payload["status"]
            current.updated_by = actor
            session.flush()
            return {"scenario": _scenario_view(current)}
    if len(segments) == 2 and segments[0] == "load-agents":
        record = LoadAgentService(factory).update_agent(segments[1], payload, actor)
        return {"agent": _agent_view(record)}
    raise ApiHttpError(404, "not_found", "Resource was not found")


def _delete(factory, segments, payload, actor):
    if len(segments) == 2 and segments[0] == "load-scenarios":
        return _put(factory, segments, {"status": "archived"}, actor)
    if len(segments) == 2 and segments[0] == "load-datasets":
        access.require_permission(actor, "api.loadtest.edit")
        with factory.begin() as session:
            record = session.get(ApiLoadDataset, segments[1])
            access.require_resource(session, record, actor, "api.loadtest.edit")
            record.status = "archived"
            record.updated_by = actor
            session.flush()
            return {"dataset": _dataset_view(record)}
    raise ApiHttpError(404, "not_found", "Resource was not found")


def _run_service(factory):
    from .executor import HttpExecutor
    executor = HttpExecutor(CaseService(factory), EnvironmentService(factory))

    def connectivity_probe(agent, revision_id):
        health = agent.health if isinstance(agent.health, dict) else {}
        snapshots = health.get("target_connectivity") if isinstance(health.get("target_connectivity"), dict) else {}
        result = snapshots.get(revision_id)
        if isinstance(result, dict) and result.get("reachable") is True:
            return result
        return {
            "reachable": False,
            "stage": "not_checked",
            "message": "节点尚未上报当前目标环境连通性，请在节点页执行校准和连通性检查",
        }

    preflight = LoadPreflightService(
        FunctionalLoadStepRunner(executor), connectivity_probe=connectivity_probe,
    )
    return LoadRunService(factory, preflight_service=preflight)


def _project(factory, project_id, actor, permission):
    from .models.project import ApiProject
    access.require_permission(actor, permission)
    with factory() as session:
        record = session.get(ApiProject, project_id)
        access.require_resource(session, record, actor, permission)
        return record


def _scenario(factory, scenario_id, actor, permission="api.loadtest.view"):
    access.require_permission(actor, permission)
    with factory() as session:
        record = session.get(ApiLoadScenario, scenario_id)
        access.require_resource(session, record, actor, permission)
        return record


def _version(factory, version_id, actor):
    access.require_permission(actor, "api.loadtest.view")
    with factory() as session:
        version = session.get(ApiLoadScenarioVersion, version_id)
        scenario = session.get(ApiLoadScenario, version.scenario_id) if version else None
        access.require_resource(session, scenario, actor, "api.loadtest.view")
        return version, scenario


def _run(factory, run_id, actor):
    access.require_permission(actor, "api.loadtest.view")
    with factory() as session:
        record = session.get(ApiLoadRun, run_id)
        access.require_resource(session, record, actor, "api.loadtest.view")
        return record


def _scenario_view(item, *, description=None):
    return {
        "id": item.id, "project_id": item.project_id, "name": item.name,
        "description": item.description if description is None else description,
        "scenario_type": item.scenario_type, "active_version_id": item.active_version_id,
        "status": item.status, "created_at": _json_time(vars(item).get("created_at")), "updated_at": _json_time(vars(item).get("updated_at")),
    }


def _version_view(item, include_definition=False):
    result = {
        "id": item.id, "scenario_id": item.scenario_id, "version_number": item.version_number,
        "validation_summary": copy.deepcopy(item.validation_summary),
        "preflight_summary": copy.deepcopy(item.preflight_summary),
        "compiler_version": item.compiler_version, "content_hash": item.content_hash,
        "created_at": _json_time(vars(item).get("created_at")),
    }
    if include_definition:
        result["definition"] = copy.deepcopy(item.definition)
        result["source_snapshot"] = copy.deepcopy(item.source_snapshot)
    return result


def _dataset_view(item):
    return {
        "id": item.id, "project_id": item.project_id, "name": item.name, "filename": item.filename,
        "field_schema": copy.deepcopy(item.field_schema), "row_count": item.row_count,
        "content_hash": item.content_hash, "sensitivity": item.sensitivity,
        "usage_mode": item.usage_mode, "status": item.status,
        "created_at": _json_time(vars(item).get("created_at")), "updated_at": _json_time(vars(item).get("updated_at")),
    }


def _run_view(item):
    return {
        "id": item.id, "project_id": item.project_id, "scenario_version_id": item.scenario_version_id,
        "environment_revision_id": item.environment_revision_id, "load_model": item.load_model,
        "queue_priority": item.queue_priority, "configuration": copy.deepcopy(item.configuration),
        "state": item.state, "verdict": item.verdict, "stop_reason": item.stop_reason,
        "ai_analysis_state": item.ai_analysis_state, "summary": copy.deepcopy(item.summary),
        "created_at": _json_time(vars(item).get("created_at")), "started_at": _json_time(vars(item).get("started_at")),
        "finished_at": _json_time(vars(item).get("finished_at")), "updated_at": _json_time(vars(item).get("updated_at")),
    }


def _shard_view(item):
    return {
        "id": item.id, "run_id": item.run_id, "agent_id": item.agent_id,
        "sequence": item.sequence, "allocation": copy.deepcopy(item.allocation),
        "state": item.state, "process_info": copy.deepcopy(item.process_info),
        "summary": copy.deepcopy(item.summary), "error": copy.deepcopy(item.error),
        "last_heartbeat_at": _json_time(item.last_heartbeat_at),
    }


def _event_view(item):
    return {"id": item.id, "sequence": item.sequence, "type": item.event_type, "payload": copy.deepcopy(item.payload), "created_at": _json_time(vars(item).get("created_at"))}


def _agent_view(item):
    return {
        "id": item.id, "name": item.name, "status": item.status,
        "scheduling_tier": item.scheduling_tier, "node_group": item.node_group,
        "labels": copy.deepcopy(item.labels), "agent_version": item.agent_version,
        "k6_version": item.k6_version, "hard_limits": copy.deepcopy(item.hard_limits),
        "soft_limits": copy.deepcopy(item.soft_limits), "current_usage": copy.deepcopy(item.current_usage),
        "health": copy.deepcopy(item.health), "calibration_state": calibration_state(item),
        "egress_ip": item.egress_ip, "last_heartbeat_at": _json_time(vars(item).get("last_heartbeat_at")),
        "offline_reason": item.offline_reason,
    }


def _enrollment_view(item):
    return {
        "id": item.id, "expires_at": _json_time(item.expires_at), "used_at": _json_time(item.used_at),
        "revoked_at": _json_time(item.revoked_at), "preset": copy.deepcopy(item.preset),
        "created_at": _json_time(vars(item).get("created_at")),
    }


def _analysis_view(item):
    return {
        "id": item.id, "run_id": item.run_id, "model": item.model,
        "prompt_version": item.prompt_version, "evidence_hash": item.evidence_hash,
        "state": item.state, "result": copy.deepcopy(item.result), "error": item.error,
        "created_at": _json_time(getattr(item, "created_at", None)),
    }


def _issue_view(item):
    return {"level": item.level, "code": item.code, "step_id": item.step_id, "message": item.message, "remedy": item.remedy}


def _simple_view(item):
    return {key: _json_time(value) if key.endswith("_at") else copy.deepcopy(value) for key, value in vars(item).items()}


def _required(source, key, message):
    value = source.get(key) if isinstance(source, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise ApiHttpError(422, "invalid_request", message)
    return value.strip()


def _text(value, field, maximum):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ApiHttpError(422, "invalid_request", f"{field}必须是1到{maximum}个字符")
    return value.strip()


def _limit(query):
    value = _nonnegative_int(query.get("limit", 100), "limit")
    return max(1, min(value or 100, 200))


def _nonnegative_int(value, field):
    try:
        parsed = int(value)
    except (TypeError, ValueError) as error:
        raise ApiHttpError(422, "invalid_request", f"{field}必须是非负整数") from error
    if parsed < 0:
        raise ApiHttpError(422, "invalid_request", f"{field}必须是非负整数")
    return parsed


def _json_time(value):
    return value.isoformat() if hasattr(value, "isoformat") else value


def _load_error(error):
    if isinstance(error, ApiHttpError):
        return error
    if isinstance(error, access.AccessDeniedError):
        return ApiHttpError(403, "permission_denied", str(error), {"permission": error.permission})
    if isinstance(error, (LoadAgentError, LoadRunError)):
        return ApiHttpError(error.status, error.code, str(error))
    if isinstance(error, LoadReportError):
        return ApiHttpError(404, "load_report_not_found", str(error))
    if isinstance(error, LoadAiAnalysisError):
        return ApiHttpError(409, "load_ai_analysis_failed", str(error))
    if isinstance(error, LoadDatasetError):
        return ApiHttpError(422, "load_dataset_invalid", str(error))
    return _domain_error(error)
