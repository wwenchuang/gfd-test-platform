"""Narrow HTTP protocol used only by outbound load Agents."""

import copy
import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import case, select

from .config import ApiTestingSettings
from .db import _session_factory
from .http import ApiHttpError, _domain_error, _failure, _read_json_body, _request_id, _success
from .models.load_testing import ApiLoadRun, ApiLoadRunShard, ApiLoadScenarioVersion
from .repositories.load_testing_repository import LoadTestingRepository
from .services.load_agent_service import LoadAgentError, LoadAgentService
from .services.load_metric_service import LoadMetricError, LoadMetricService


AGENT_API_PREFIX = "/api/api-testing/load-agent/v1"
MAX_SAMPLES_PER_REQUEST = 100
MAX_EVENTS_PER_REQUEST = 100
TERMINAL_SHARD_STATES = frozenset({"finished", "failed", "cancelled"})
logger = logging.getLogger(__name__)


def _factory():
    return _session_factory()


def dispatch_get(handler, qs, path):
    return dispatch_load_agent_request(handler, "GET", path, qs)


def dispatch_post(handler, qs, path):
    return dispatch_load_agent_request(handler, "POST", path, qs)


def dispatch_load_agent_request(handler, method, path, query):
    """Dispatch a matched Agent route without accepting browser authentication."""
    request_id = _request_id(handler)
    try:
        settings = ApiTestingSettings.from_env()
        if not settings.enabled:
            raise ApiHttpError(503, "api_testing_disabled", "API testing is unavailable")
        segments = _segments(path)
        if method == "POST" and segments == ("register",):
            payload = _read_json_body(handler)
            result = _register(payload)
            return _success(handler, result, request_id, 201)
        secret = _agent_secret(handler)
        service = LoadAgentService(_factory())
        agent = service.authenticate(secret)
        if method == "POST":
            payload = _read_json_body(handler)
            result = _post(service, agent, secret, segments, payload)
            return _success(handler, result, request_id, 200)
        if method == "GET":
            result = _get(agent, segments, query)
            return _success(handler, result, request_id, 200)
        raise ApiHttpError(405, "method_not_allowed", "Method is not allowed")
    except LoadAgentError as error:
        return _failure(
            handler,
            ApiHttpError(error.status, error.code, str(error)),
            request_id,
        )
    except ApiHttpError as error:
        return _failure(handler, error, request_id)
    except Exception as error:
        mapped = _domain_error(error)
        logger.exception(
            "Load Agent request failed request_id=%s method=%s route=%s error_code=%s exception_type=%s",
            request_id,
            method,
            path,
            mapped.code,
            type(error).__name__,
        )
        return _failure(handler, mapped, request_id)


def _segments(path):
    if not path.startswith(AGENT_API_PREFIX):
        raise ApiHttpError(404, "not_found", "Resource was not found")
    suffix = path[len(AGENT_API_PREFIX):]
    if suffix and not suffix.startswith("/"):
        raise ApiHttpError(404, "not_found", "Resource was not found")
    return tuple(part for part in suffix.strip("/").split("/") if part)


def _agent_secret(handler):
    value = str(handler.headers.get("Authorization") or "")
    if not value.startswith("Agent "):
        raise LoadAgentError("需要Agent凭据", status=401, code="agent_unauthorized")
    secret = value[6:].strip()
    if not secret or len(secret) > 256:
        raise LoadAgentError("Agent凭据无效", status=401, code="agent_unauthorized")
    return secret


def _uuid(value):
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise ApiHttpError(400, "invalid_identifier", "Identifier must be a UUID")


def _register(payload):
    token = payload.get("enrollment_token")
    capabilities = payload.get("capabilities")
    result = LoadAgentService(_factory()).register(token, capabilities)
    return {
        "agent": _agent_view(result.agent),
        "secret": result.secret,
        "credential_notice": "凭据仅显示一次，请保存到Agent私有环境文件",
    }


def _post(service, agent, secret, segments, payload):
    if segments == ("heartbeat",):
        updated = service.heartbeat(secret, payload)
        pending = (updated.health or {}).get("pending_command") if isinstance(updated.health, dict) else None
        commands = [copy.deepcopy(pending)] if isinstance(pending, dict) else []
        return {"agent": _agent_view(updated), "commands": commands}
    if segments == ("claim",):
        return {"shard": _claim_shard(agent.id)}
    if len(segments) == 3 and segments[0] == "shards":
        shard_id = _uuid(segments[1])
        action = segments[2]
        if action == "started":
            return {"shard": _start_shard(agent.id, shard_id, payload)}
        if action == "metrics":
            return _ingest_metrics(agent.id, shard_id, payload)
        if action == "samples":
            return _ingest_samples(agent.id, shard_id, payload)
        if action == "events":
            return _ingest_events(agent.id, shard_id, payload)
        if action == "finish":
            return {"shard": _finish_shard(agent.id, shard_id, payload)}
    if segments and segments[0] in {"heartbeat", "claim"}:
        raise ApiHttpError(404, "not_found", "Resource was not found")
    if segments and segments[0] == "shards":
        raise ApiHttpError(404, "not_found", "Resource was not found")
    raise ApiHttpError(404, "not_found", "Resource was not found")


def _get(agent, segments, query):
    if segments == ("heartbeat",) or segments == ("claim",):
        raise ApiHttpError(405, "method_not_allowed", "Method is not allowed")
    if len(segments) == 3 and segments[0] == "shards" and segments[2] == "commands":
        shard_id = _uuid(segments[1])
        factory = _factory()
        with factory.begin() as session:
            shard = _owned_shard(session, agent.id, shard_id, for_update=True)
            run = session.get(ApiLoadRun, shard.run_id)
            stop = run is None or run.state in {"stopping", "cancelled", "failed"}
            start = run is not None and run.state == "running" and shard.state == "ready"
            now = datetime.now(timezone.utc)
            previous = shard.last_heartbeat_at
            if previous is not None and previous.tzinfo is None:
                previous = previous.replace(tzinfo=timezone.utc)
            if (
                shard.state in {"ready", "running", "stopping"}
                and (previous is None or now - previous >= timedelta(seconds=5))
            ):
                shard.last_heartbeat_at = now
            return {
                "commands": (
                    [{"type": "stop", "reason": run.stop_reason if run else "运行不存在"}]
                    if stop
                    else [{"type": "start"}] if start else []
                ),
                "poll_after_ms": 1000,
            }
    if segments and segments[0] == "shards":
        raise ApiHttpError(404, "not_found", "Resource was not found")
    raise ApiHttpError(404, "not_found", "Resource was not found")


def _owned_shard(session, agent_id, shard_id, *, for_update=False):
    query = select(ApiLoadRunShard).where(ApiLoadRunShard.id == shard_id)
    if for_update:
        query = query.with_for_update()
    shard = session.scalar(query)
    if shard is None:
        raise ApiHttpError(404, "shard_not_found", "分片不存在")
    if shard.agent_id != agent_id:
        raise ApiHttpError(403, "shard_not_owned", "该分片不属于当前压测节点")
    return shard


def _claim_shard(agent_id):
    factory = _factory()
    with factory.begin() as session:
        priority_order = case(
            (ApiLoadRun.queue_priority == "urgent", 0),
            (ApiLoadRun.queue_priority == "high", 1),
            (ApiLoadRun.queue_priority == "normal", 2),
            else_=3,
        )
        run = session.scalar(
            select(ApiLoadRun)
            .join(ApiLoadRunShard, ApiLoadRunShard.run_id == ApiLoadRun.id)
            .where(
                ApiLoadRunShard.agent_id == agent_id,
                ApiLoadRunShard.state == "assigned",
                ApiLoadRun.state == "starting",
            )
            .order_by(priority_order, ApiLoadRun.created_at)
            .with_for_update(of=ApiLoadRun, skip_locked=True)
            .limit(1)
        )
        if run is None:
            return None
        shard = session.scalar(
            select(ApiLoadRunShard)
            .where(
                ApiLoadRunShard.run_id == run.id,
                ApiLoadRunShard.agent_id == agent_id,
                ApiLoadRunShard.state == "assigned",
            )
            .order_by(ApiLoadRunShard.sequence)
            .with_for_update()
            .limit(1)
        )
        if shard is None:
            return None
        version = session.get(ApiLoadScenarioVersion, run.scenario_version_id) if run else None
        if run is None or version is None:
            raise ApiHttpError(409, "shard_configuration_missing", "分片运行配置不完整")
        shard.state = "ready"
        shard.last_heartbeat_at = datetime.now(timezone.utc)
        session.flush()
        shard_states = tuple(
            session.scalars(
                select(ApiLoadRunShard.state).where(ApiLoadRunShard.run_id == run.id)
            )
        )
        if shard_states and all(state == "ready" for state in shard_states):
            run.state = "running"
            run.started_at = datetime.now(timezone.utc)
            session.flush()
        return {
            **_shard_view(shard),
            "run": {"id": run.id, "configuration": copy.deepcopy(run.configuration)},
            "scenario": {
                "version_id": version.id,
                "definition": copy.deepcopy(version.definition),
                "compiler_version": version.compiler_version,
                "content_hash": version.content_hash,
            },
        }


def _start_shard(agent_id, shard_id, payload):
    process_info = payload.get("process_info", {})
    if not isinstance(process_info, dict):
        raise ApiHttpError(422, "invalid_request", "process_info must be an object")
    factory = _factory()
    with factory.begin() as session:
        shard = _owned_shard(session, agent_id, shard_id, for_update=True)
        run = session.get(ApiLoadRun, shard.run_id)
        if run is None or run.state != "running":
            raise ApiHttpError(409, "start_barrier_pending", "正在等待全部压测节点就绪，尚未下发开始指令")
        if shard.state == "ready":
            shard.state = "running"
            shard.process_info = copy.deepcopy(process_info)
            shard.last_heartbeat_at = datetime.now(timezone.utc)
        elif shard.state != "running":
            raise ApiHttpError(409, "shard_state_conflict", "分片当前状态不能启动")
        session.flush()
        return _shard_view(shard)


def _items(payload, key, limit):
    value = payload.get(key)
    if not isinstance(value, list):
        raise ApiHttpError(422, "invalid_request", f"{key} must be an array")
    if len(value) > limit:
        raise ApiHttpError(413, "payload_too_large", f"{key} exceeds its item limit")
    if any(not isinstance(item, dict) for item in value):
        raise ApiHttpError(422, "invalid_request", f"{key} items must be objects")
    return value


def _assert_owned(agent_id, shard_id):
    factory = _factory()
    with factory() as session:
        return _owned_shard(session, agent_id, shard_id)


def _touch_shard(agent_id, shard_id):
    factory = _factory()
    with factory.begin() as session:
        shard = _owned_shard(session, agent_id, shard_id, for_update=True)
        if shard.state in {"ready", "running", "stopping"}:
            shard.last_heartbeat_at = datetime.now(timezone.utc)


def _ingest_metrics(agent_id, shard_id, payload):
    try:
        return LoadMetricService(_factory()).ingest(agent_id, shard_id, payload)
    except LoadMetricError as error:
        raise ApiHttpError(error.status, error.code, str(error)) from error


def _ingest_samples(agent_id, shard_id, payload):
    shard = _assert_owned(agent_id, shard_id)
    repository = LoadTestingRepository.from_factory(_factory())
    samples = _items(payload, "samples", MAX_SAMPLES_PER_REQUEST)
    parsed = []
    for sample in samples:
        step_id = sample.get("step_id")
        kind = sample.get("kind")
        body = sample.get("payload", {})
        if not isinstance(step_id, str) or not step_id or len(step_id) > 120:
            raise ApiHttpError(422, "invalid_request", "step_id is invalid")
        if not isinstance(kind, str) or not kind or len(kind) > 48:
            raise ApiHttpError(422, "invalid_request", "sample kind is invalid")
        if not isinstance(body, dict):
            raise ApiHttpError(422, "invalid_request", "sample payload must be an object")
        parsed.append((step_id, kind, body))
    for step_id, kind, body in parsed:
        repository.append_bounded_sample(shard.run_id, shard.id, step_id, kind, body)
    _touch_shard(agent_id, shard.id)
    return {"accepted": len(samples)}


def _ingest_events(agent_id, shard_id, payload):
    shard = _assert_owned(agent_id, shard_id)
    repository = LoadTestingRepository.from_factory(_factory())
    events = _items(payload, "events", MAX_EVENTS_PER_REQUEST)
    parsed = []
    for event in events:
        event_type = event.get("type")
        body = event.get("payload", {})
        if not isinstance(event_type, str) or not event_type or len(event_type) > 80:
            raise ApiHttpError(422, "invalid_request", "event type is invalid")
        if not isinstance(body, dict):
            raise ApiHttpError(422, "invalid_request", "event payload must be an object")
        parsed.append((event_type, body))
    for event_type, body in parsed:
        repository.append_event(
            shard.run_id,
            "agent." + event_type,
            {"shard_id": shard.id, **copy.deepcopy(body)},
        )
    _touch_shard(agent_id, shard.id)
    return {"accepted": len(events)}


def _finish_shard(agent_id, shard_id, payload):
    state = payload.get("state")
    summary = payload.get("summary", {})
    error = payload.get("error", {})
    if state not in TERMINAL_SHARD_STATES:
        raise ApiHttpError(422, "invalid_request", "分片结束状态无效")
    if not isinstance(summary, dict) or not isinstance(error, dict):
        raise ApiHttpError(422, "invalid_request", "summary and error must be objects")
    factory = _factory()
    completed_run_id = None
    with factory.begin() as session:
        snapshot = _owned_shard(session, agent_id, shard_id)
        run = session.scalar(
            select(ApiLoadRun).where(ApiLoadRun.id == snapshot.run_id).with_for_update()
        )
        if run is None:
            raise ApiHttpError(409, "shard_configuration_missing", "分片运行配置不完整")
        shard = _owned_shard(session, agent_id, shard_id, for_update=True)
        if shard.state in TERMINAL_SHARD_STATES:
            if shard.state != state:
                raise ApiHttpError(409, "shard_state_conflict", "分片已经以其他状态结束")
            return _shard_view(shard)
        if shard.state not in {"ready", "running", "stopping"}:
            raise ApiHttpError(409, "shard_state_conflict", "分片当前状态不能结束")
        shard.state = state
        shard.summary = copy.deepcopy(summary)
        shard.error = copy.deepcopy(error)
        shard.last_heartbeat_at = datetime.now(timezone.utc)
        session.flush()
        states = tuple(
            session.scalars(
                select(ApiLoadRunShard.state).where(ApiLoadRunShard.run_id == run.id)
            )
        )
        if states and all(item in TERMINAL_SHARD_STATES for item in states):
            run.finished_at = datetime.now(timezone.utc)
            if run.state == "stopping":
                run.state = "cancelled"
                run.verdict = "inconclusive"
            elif any(item == "failed" for item in states):
                run.state = "failed"
                run.verdict = "inconclusive"
            else:
                run.state = "finished"
            session.flush()
            completed_run_id = run.id
        view = _shard_view(shard)
    if completed_run_id:
        _dispatch_load_completion(completed_run_id)
    return view


def _dispatch_load_completion(run_id):
    from .tasks import finalize_load_run
    finalize_load_run.delay(run_id)


def _agent_view(agent):
    return {
        "id": agent.id,
        "name": agent.name,
        "status": agent.status,
        "scheduling_tier": agent.scheduling_tier,
        "node_group": agent.node_group,
        "labels": copy.deepcopy(agent.labels),
        "agent_version": agent.agent_version,
        "k6_version": agent.k6_version,
        "hard_limits": copy.deepcopy(agent.hard_limits),
        "soft_limits": copy.deepcopy(agent.soft_limits),
        "current_usage": copy.deepcopy(agent.current_usage),
        "health": copy.deepcopy(agent.health),
        "last_heartbeat_at": agent.last_heartbeat_at,
    }


def _shard_view(shard):
    return {
        "id": shard.id,
        "run_id": shard.run_id,
        "agent_id": shard.agent_id,
        "sequence": shard.sequence,
        "global_sequence": shard.global_sequence,
        "allocation": copy.deepcopy(shard.allocation),
        "state": shard.state,
        "process_info": copy.deepcopy(shard.process_info),
        "summary": copy.deepcopy(shard.summary),
        "error": copy.deepcopy(shard.error),
    }
