"""Enrollment, authentication, and capacity policy for load Agents."""

import copy
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from .. import access
from ..models.load_testing import ApiLoadAgent, ApiLoadAgentEnrollment


SCHEDULING_TIERS = frozenset({"preferred", "normal", "fallback", "disabled"})
POSITIVE_LIMIT_FIELDS = (
    "max_processes",
    "max_vus",
    "max_iterations_per_second",
    "max_duration_seconds",
    "cpu_cores",
    "memory_mb",
)


class LoadAgentError(ValueError):
    def __init__(self, message, *, status=400, code="invalid_request"):
        super().__init__(message)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class EnrollmentResult:
    id: str
    token: str
    expires_at: object


@dataclass(frozen=True)
class RegistrationResult:
    agent: ApiLoadAgent
    secret: str


def _digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _now_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _clean_name(value):
    value = value.strip() if isinstance(value, str) else ""
    if not value or len(value) > 160:
        raise LoadAgentError("节点名称必须为1到160个字符", code="invalid_agent_name")
    return value


def _clean_tier(value):
    if value not in SCHEDULING_TIERS:
        raise LoadAgentError(
            "调度级别必须是首选、普通、备用或停用",
            code="invalid_scheduling_tier",
        )
    return value


def _positive_limits(value, *, code):
    if not isinstance(value, dict):
        raise LoadAgentError("节点容量必须是对象", code=code)
    result = {}
    for field in POSITIVE_LIMIT_FIELDS:
        item = value.get(field)
        if isinstance(item, bool) or not isinstance(item, (int, float)) or item <= 0:
            raise LoadAgentError(f"节点容量 {field} 必须大于0", code=code)
        result[field] = item
    return result


def _soft_limits(value, hard_limits):
    result = _positive_limits(value, code="invalid_soft_limits")
    if any(result[field] > hard_limits[field] for field in POSITIVE_LIMIT_FIELDS):
        raise LoadAgentError(
            "平台软上限不能超过节点上报的本机硬上限",
            code="soft_limit_exceeds_hard_limit",
        )
    return result


def _plain_dict(value, field):
    if not isinstance(value, dict):
        raise LoadAgentError(f"{field} 必须是对象", code="invalid_agent_payload")
    return copy.deepcopy(value)


class LoadAgentService:
    def __init__(self, session_factory, *, now=None):
        self.session_factory = session_factory
        self.now = now or (lambda: datetime.now(timezone.utc))

    def create_enrollment(self, payload, actor_id):
        access.require_permission(actor_id, "api.loadtest.manage_agents")
        if not isinstance(payload, dict):
            raise LoadAgentError("注册配置必须是对象")
        unknown = set(payload) - {
            "name",
            "node_group",
            "scheduling_tier",
            "soft_limits",
            "labels",
            "expires_in_seconds",
        }
        if unknown:
            raise LoadAgentError("注册配置包含未知字段: " + ", ".join(sorted(unknown)))
        name = _clean_name(payload.get("name"))
        tier = _clean_tier(payload.get("scheduling_tier", "normal"))
        ttl = payload.get("expires_in_seconds", 900)
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 60 <= ttl <= 86400:
            raise LoadAgentError("注册令牌有效期必须为60到86400秒", code="invalid_enrollment_ttl")
        preset = {
            "name": name,
            "node_group": str(payload.get("node_group") or "").strip()[:120],
            "scheduling_tier": tier,
            "labels": _plain_dict(payload.get("labels", {}), "节点标签"),
        }
        if "soft_limits" in payload:
            preset["soft_limits"] = _positive_limits(
                payload["soft_limits"], code="invalid_soft_limits"
            )
        token = secrets.token_urlsafe(32)
        now = _now_utc(self.now())
        with self.session_factory.begin() as session:
            enrollment = ApiLoadAgentEnrollment(
                token_hash=_digest(token),
                expires_at=now + timedelta(seconds=ttl),
                preset=preset,
                owner_id=actor_id,
                created_by=actor_id,
                updated_by=actor_id,
            )
            session.add(enrollment)
            session.flush()
            return EnrollmentResult(enrollment.id, token, enrollment.expires_at)

    def register(self, enrollment_token, capabilities):
        if not isinstance(enrollment_token, str) or not enrollment_token:
            raise LoadAgentError("注册令牌无效", status=401, code="enrollment_invalid")
        if not isinstance(capabilities, dict):
            raise LoadAgentError("节点能力必须是对象", code="invalid_agent_payload")
        unknown = set(capabilities) - {"agent_version", "k6_version", "hard_limits", "labels"}
        if unknown:
            raise LoadAgentError("节点能力包含未知字段: " + ", ".join(sorted(unknown)))
        hard_limits = _positive_limits(
            capabilities.get("hard_limits"), code="invalid_hard_limits"
        )
        now = _now_utc(self.now())
        secret = secrets.token_urlsafe(32)
        with self.session_factory.begin() as session:
            enrollment = session.scalar(
                select(ApiLoadAgentEnrollment)
                .where(ApiLoadAgentEnrollment.token_hash == _digest(enrollment_token))
                .with_for_update()
            )
            if enrollment is None or enrollment.revoked_at is not None:
                raise LoadAgentError("注册令牌无效", status=401, code="enrollment_invalid")
            if enrollment.used_at is not None:
                raise LoadAgentError("注册令牌已经使用", status=409, code="enrollment_used")
            if _now_utc(enrollment.expires_at) <= now:
                raise LoadAgentError("注册令牌已经过期", status=410, code="enrollment_expired")
            preset = copy.deepcopy(enrollment.preset or {})
            soft_limits = (
                _soft_limits(preset["soft_limits"], hard_limits)
                if "soft_limits" in preset
                else copy.deepcopy(hard_limits)
            )
            labels = {**_plain_dict(capabilities.get("labels", {}), "节点标签"), **preset.get("labels", {})}
            agent = ApiLoadAgent(
                name=_clean_name(preset.get("name")),
                status="offline",
                scheduling_tier=_clean_tier(preset.get("scheduling_tier", "normal")),
                node_group=str(preset.get("node_group") or "")[:120],
                labels=labels,
                agent_version=str(capabilities.get("agent_version") or "")[:80],
                k6_version=str(capabilities.get("k6_version") or "")[:80],
                credential_hash=_digest(secret),
                hard_limits=hard_limits,
                soft_limits=soft_limits,
                owner_id=enrollment.owner_id,
                created_by=enrollment.created_by,
                updated_by=enrollment.updated_by,
            )
            session.add(agent)
            try:
                session.flush()
            except IntegrityError as error:
                raise LoadAgentError("节点名称已存在", status=409, code="agent_name_exists") from error
            enrollment.used_at = now
            enrollment.updated_by = enrollment.created_by
            session.flush()
            return RegistrationResult(agent, secret)

    def _authenticated(self, session, secret, *, for_update=False):
        if not isinstance(secret, str) or not secret:
            raise LoadAgentError("Agent凭据无效", status=401, code="agent_unauthorized")
        query = select(ApiLoadAgent).where(ApiLoadAgent.credential_hash == _digest(secret))
        if for_update:
            query = query.with_for_update()
        agent = session.scalar(query)
        if agent is None:
            raise LoadAgentError("Agent凭据无效", status=401, code="agent_unauthorized")
        if agent.status == "disabled" or agent.scheduling_tier == "disabled":
            raise LoadAgentError("压测节点已经停用", status=403, code="agent_disabled")
        return agent

    def authenticate(self, secret):
        with self.session_factory() as session:
            return self._authenticated(session, secret)

    def heartbeat(self, secret, payload):
        if not isinstance(payload, dict):
            raise LoadAgentError("心跳内容必须是对象", code="invalid_agent_payload")
        unknown = set(payload) - {
            "agent_version",
            "k6_version",
            "hard_limits",
            "current_usage",
            "health",
            "egress_ip",
        }
        if unknown:
            raise LoadAgentError("心跳包含未知字段: " + ", ".join(sorted(unknown)))
        hard_limits = _positive_limits(payload.get("hard_limits"), code="invalid_hard_limits")
        current_usage = _plain_dict(payload.get("current_usage", {}), "当前占用")
        for key, value in current_usage.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise LoadAgentError(f"当前占用 {key} 不能为负数", code="invalid_current_usage")
        health = _plain_dict(payload.get("health", {}), "健康状态")
        with self.session_factory.begin() as session:
            agent = self._authenticated(session, secret, for_update=True)
            previous_health = agent.health if isinstance(agent.health, dict) else {}
            pending_command = previous_health.get("pending_command")
            calibration = health.get("calibration") if isinstance(health.get("calibration"), dict) else {}
            command_completed = False
            if isinstance(pending_command, dict) and pending_command.get("type") == "calibrate":
                command_completed = calibration.get("command_id") == pending_command.get("id") and calibration.get("state") in {"valid", "failed"}
            elif isinstance(pending_command, dict) and pending_command.get("type") == "target_connectivity":
                connectivity = health.get("target_connectivity") if isinstance(health.get("target_connectivity"), dict) else {}
                result = connectivity.get(pending_command.get("environment_revision_id"))
                command_completed = isinstance(result, dict) and result.get("command_id") == pending_command.get("id")
            if isinstance(pending_command, dict) and not command_completed:
                health["pending_command"] = copy.deepcopy(pending_command)
                if pending_command.get("type") == "calibrate":
                    health["schedulable"] = False
                    health["calibration"] = {
                        **copy.deepcopy(calibration),
                        "state": "calibrating",
                        "requested_at": pending_command.get("requested_at"),
                    }
            _soft_limits(agent.soft_limits, hard_limits)
            agent.hard_limits = hard_limits
            agent.current_usage = current_usage
            agent.health = health
            agent.agent_version = str(payload.get("agent_version") or "")[:80]
            agent.k6_version = str(payload.get("k6_version") or "")[:80]
            agent.egress_ip = str(payload.get("egress_ip") or "")[:64]
            agent.last_heartbeat_at = _now_utc(self.now())
            agent.status = "online"
            agent.offline_reason = ""
            session.flush()
            return agent

    def request_calibration(self, agent_id, actor_id):
        """Queue one durable local calibration command for an idle online Agent."""
        access.require_permission(actor_id, "api.loadtest.manage_agents")
        with self.session_factory.begin() as session:
            agent = session.scalar(
                select(ApiLoadAgent).where(ApiLoadAgent.id == agent_id).with_for_update()
            )
            if agent is None:
                raise LoadAgentError("压测节点不存在", status=404, code="agent_not_found")
            if agent.status != "online":
                raise LoadAgentError(
                    "节点当前离线，请先启动Agent并等待心跳恢复后再校准",
                    status=409,
                    code="agent_offline",
                )
            usage = agent.current_usage if isinstance(agent.current_usage, dict) else {}
            if float(usage.get("processes") or 0) > 0:
                raise LoadAgentError(
                    "节点正在执行压测，请等待任务结束后再校准",
                    status=409,
                    code="agent_busy",
                )
            health = copy.deepcopy(agent.health if isinstance(agent.health, dict) else {})
            pending = health.get("pending_command")
            if not isinstance(pending, dict) or pending.get("type") != "calibrate":
                requested_at = _now_utc(self.now()).isoformat()
                pending = {
                    "type": "calibrate",
                    "id": str(uuid.uuid4()),
                    "requested_at": requested_at,
                }
            health["pending_command"] = pending
            health["schedulable"] = False
            health["calibration"] = {
                **copy.deepcopy(health.get("calibration") if isinstance(health.get("calibration"), dict) else {}),
                "state": "calibrating",
                "requested_at": pending["requested_at"],
            }
            agent.health = health
            agent.updated_by = actor_id
            session.flush()
            return agent

    def request_target_connectivity(self, agent_id, environment_revision_id, targets, actor_id):
        access.require_permission(actor_id, "api.loadtest.execute")
        if not isinstance(environment_revision_id, str) or not environment_revision_id:
            raise LoadAgentError("环境版本不能为空")
        if not isinstance(targets, list) or not targets:
            raise LoadAgentError("目标环境没有可检查的服务地址", code="target_missing")
        with self.session_factory.begin() as session:
            agent = session.scalar(select(ApiLoadAgent).where(ApiLoadAgent.id == agent_id).with_for_update())
            if agent is None or agent.status != "online":
                raise LoadAgentError("压测节点离线，无法检查目标环境", status=409, code="agent_offline")
            usage = agent.current_usage if isinstance(agent.current_usage, dict) else {}
            if float(usage.get("processes") or 0) > 0:
                raise LoadAgentError("压测节点正在执行任务，无法检查目标环境", status=409, code="agent_busy")
            health = copy.deepcopy(agent.health if isinstance(agent.health, dict) else {})
            existing = health.get("pending_command")
            if isinstance(existing, dict) and (
                existing.get("type") != "target_connectivity"
                or existing.get("environment_revision_id") != environment_revision_id
            ):
                raise LoadAgentError("节点还有待执行的管理命令，请稍后重试", status=409, code="agent_command_pending")
            command = existing if isinstance(existing, dict) else {
                "type": "target_connectivity", "id": str(uuid.uuid4()),
                "environment_revision_id": environment_revision_id,
                "targets": copy.deepcopy(targets), "requested_at": _now_utc(self.now()).isoformat(),
            }
            health["pending_command"] = command
            agent.health = health
            agent.updated_by = actor_id
            session.flush()
            return agent

    def update_agent(self, agent_id, payload, actor_id):
        access.require_permission(actor_id, "api.loadtest.manage_agents")
        if not isinstance(payload, dict):
            raise LoadAgentError("节点配置必须是对象")
        unknown = set(payload) - {"scheduling_tier", "node_group", "labels", "soft_limits"}
        if unknown:
            raise LoadAgentError("节点配置包含未知字段: " + ", ".join(sorted(unknown)))
        with self.session_factory.begin() as session:
            agent = session.scalar(
                select(ApiLoadAgent).where(ApiLoadAgent.id == agent_id).with_for_update()
            )
            if agent is None:
                raise LoadAgentError("压测节点不存在", status=404, code="agent_not_found")
            if agent.status == "disabled":
                raise LoadAgentError("已停用节点不能重新启用，请重新注册", status=409, code="agent_disabled")
            if "scheduling_tier" in payload:
                agent.scheduling_tier = _clean_tier(payload["scheduling_tier"])
                if agent.scheduling_tier == "disabled":
                    agent.status = "disabled"
                    agent.offline_reason = "管理员停用"
            if "soft_limits" in payload:
                agent.soft_limits = _soft_limits(payload["soft_limits"], agent.hard_limits)
            if "node_group" in payload:
                agent.node_group = str(payload["node_group"] or "").strip()[:120]
            if "labels" in payload:
                agent.labels = _plain_dict(payload["labels"], "节点标签")
            agent.updated_by = actor_id
            session.flush()
            return agent
