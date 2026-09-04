"""Transactional orchestration for preflighted distributed k6 runs."""

import copy
from dataclasses import asdict
from datetime import datetime, timezone
import json

from sqlalchemy import case, select

from .. import access
from ..models.environment import ApiEnvironment, ApiEnvironmentRevision
from ..models.load_testing import (
    ApiLoadAgent,
    ApiLoadDataset,
    ApiLoadRun,
    ApiLoadRunShard,
    ApiLoadScenario,
    ApiLoadScenarioVersion,
)
from .load_allocator import LoadAllocationError, allocate_run, calibration_state
from .load_agent_service import agent_heartbeat_is_fresh
from .load_scenario_compiler import LoadScenarioCompileError, compile_scenario


PRIORITIES = frozenset({"urgent", "high", "normal", "low"})
TERMINAL_RUN_STATES = frozenset({"finished", "cancelled", "failed"})
TERMINAL_SHARD_STATES = frozenset({"finished", "cancelled", "failed", "lost"})
CALIBRATION_STATE_LABELS = {
    "missing": "未校准",
    "calibrating": "校准中",
    "valid": "校准有效",
    "expired": "已过期",
    "failed": "校准失败",
    "invalidated": "版本或硬件变化，校准已失效",
}


class LoadRunError(ValueError):
    def __init__(self, message, *, status=400, code="invalid_request"):
        super().__init__(message)
        self.status = status
        self.code = code


def _audit(actor):
    return {"owner_id": actor, "created_by": actor, "updated_by": actor}


def _utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _json_object(value, field):
    if not isinstance(value, dict):
        raise LoadRunError(f"{field}必须是对象")
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as error:
        raise LoadRunError(f"{field}必须是 JSON 数据") from error
    if len(encoded.encode("utf-8")) > 1_000_000:
        raise LoadRunError(f"{field}超过 1 MB 限制")
    return copy.deepcopy(value)


class LoadRunService:
    def __init__(self, session_factory, *, preflight_service, now=None):
        self.session_factory = session_factory
        self.preflight_service = preflight_service
        self.now = now or (lambda: datetime.now(timezone.utc))

    def create(self, payload, actor_id):
        access.require_permission(actor_id, "api.loadtest.execute")
        parsed = self._parse_create(payload)
        now = _utc(self.now())
        with self.session_factory.begin() as session:
            version = session.get(ApiLoadScenarioVersion, parsed["scenario_version_id"])
            scenario = session.get(ApiLoadScenario, version.scenario_id) if version else None
            revision = session.get(ApiEnvironmentRevision, parsed["environment_revision_id"])
            environment = session.get(ApiEnvironment, revision.environment_id) if revision else None
            if version is None or scenario is None:
                raise LoadRunError("压测场景版本不存在", status=404, code="scenario_not_found")
            access.require_resource(session, scenario, actor_id, "api.loadtest.execute")
            if revision is None or environment is None or environment.project_id != scenario.project_id:
                raise LoadRunError("环境版本不存在或不属于当前项目", status=404, code="environment_not_found")
            access.require_execution_environment(session, revision.id, actor_id, scenario.project_id)

            selected_agents = self._selected_agents(session, parsed["allocation_policy"])
            explicitly_selected = bool(
                parsed["allocation_policy"]["agent_ids"]
                or parsed["allocation_policy"].get("node_group")
            )
            if not explicitly_selected:
                calibrated = tuple(
                    item for item in selected_agents
                    if calibration_state(item, now=now) == "valid"
                )
                if calibrated:
                    selected_agents = calibrated
            for agent in selected_agents:
                state = calibration_state(agent, now=now)
                if state != "valid":
                    raise LoadRunError(
                        f"压测节点“{agent.name}”{CALIBRATION_STATE_LABELS[state]}，请在节点页完成本地校准后重试",
                        status=409,
                        code="agent_calibration_invalid",
                    )

            definition = copy.deepcopy(version.definition)
            try:
                compiled = compile_scenario(definition, parsed["workload"])
            except LoadScenarioCompileError as error:
                raise LoadRunError(str(error), code="scenario_compile_failed") from error

            allocation_workload = copy.deepcopy(parsed["workload"])
            dataset_snapshot = self._dataset_snapshot(session, scenario.project_id, definition)
            allocation_workload.update(
                dataset_mode=definition["dataset_contract"]["usage_mode"],
                dataset_row_count=dataset_snapshot.get("row_count", 0),
                estimated_iterations=self._estimated_iterations(parsed["workload"]),
            )
            try:
                allocations = allocate_run(
                    allocation_workload,
                    selected_agents,
                    parsed["allocation_policy"]["allow_fallback"],
                )
            except LoadAllocationError as error:
                raise LoadRunError(str(error), status=409, code="capacity_unavailable") from error
            shortfall = sum(item.capacity_shortfall for item in allocations)
            vu_shortfall = sum(item.vu_shortfall for item in allocations)
            if (shortfall or vu_shortfall) and not parsed["allocation_policy"]["allow_run_anyway"]:
                raise LoadRunError(
                    self._shortfall_message(parsed["workload"], shortfall, vu_shortfall),
                    status=409,
                    code="capacity_shortfall",
                )

            agent_by_id = {str(item.id): item for item in selected_agents}
            agent_snapshots = []
            for allocation in allocations:
                agent = agent_by_id[allocation.agent_id]
                calibration = copy.deepcopy((agent.health or {}).get("calibration") or {})
                agent_snapshots.append(
                    {
                        "id": agent.id,
                        "name": agent.name,
                        "scheduling_tier": agent.scheduling_tier,
                        "agent_version": agent.agent_version,
                        "k6_version": agent.k6_version,
                        "hard_limits": copy.deepcopy(agent.hard_limits),
                        "soft_limits": copy.deepcopy(agent.soft_limits),
                        "calibration": calibration,
                        "allocation": asdict(allocation),
                    }
                )
            snapshot = {
                "scenario": {
                    "id": scenario.id,
                    "name": scenario.name,
                    "version_id": version.id,
                    "version_number": version.version_number,
                    "content_hash": version.content_hash,
                },
                "environment": {"revision_id": revision.id, "name": revision.name},
                "workload": parsed["workload"],
                "thresholds": parsed["thresholds"],
                "priority": parsed["priority"],
                "allocation_policy": parsed["allocation_policy"],
                "capacity": {
                    "shortfall": shortfall,
                    "vu_shortfall": vu_shortfall,
                    "estimated_vus_from_preflight": None,
                },
                "compiler": {
                    "version": compiled.compiler_version,
                    "content_hash": compiled.content_hash,
                },
                "dataset": dataset_snapshot,
                "agents": agent_snapshots,
                "created_at": now.isoformat(),
            }
            run = ApiLoadRun(
                project_id=scenario.project_id,
                scenario_version_id=version.id,
                environment_revision_id=revision.id,
                load_model=parsed["workload"]["executor"],
                queue_priority=parsed["priority"],
                configuration=snapshot,
                verdict="inconclusive" if shortfall or vu_shortfall else "pending",
                **_audit(actor_id),
            )
            session.add(run)
            session.flush()
            for allocation in allocations:
                session.add(
                    ApiLoadRunShard(
                        run_id=run.id,
                        agent_id=allocation.agent_id,
                        sequence=allocation.sequence,
                        global_sequence=allocation.sequence,
                        allocation=asdict(allocation),
                        **_audit(actor_id),
                    )
                )
            session.flush()
            return run

    def preflight(self, run_id, actor_id):
        access.require_permission(actor_id, "api.loadtest.execute")
        with self.session_factory.begin() as session:
            run = self._owned_run(session, run_id, actor_id, for_update=True)
            if run.state != "draft":
                raise LoadRunError("当前任务不能重复预检", status=409, code="preflight_state_conflict")
            run.state = "preflighting"
            version = session.get(ApiLoadScenarioVersion, run.scenario_version_id)
            shards = self._run_shards(session, run.id)
            agents = [session.get(ApiLoadAgent, item.agent_id) for item in shards]
            if version is None or any(item is None for item in agents):
                run.state = "failed"
                run.verdict = "inconclusive"
                run.finished_at = _utc(self.now())
                run.summary = {"preflight": {"passed": False, "message": "任务快照依赖已丢失"}}
                return run
            # Re-check the live binary versions immediately before probing. The
            # immutable run snapshot remains the evidence of what was selected.
            for agent in agents:
                if calibration_state(agent, now=_utc(self.now())) != "valid":
                    run.state = "failed"
                    run.verdict = "inconclusive"
                    run.finished_at = _utc(self.now())
                    run.summary = {"preflight": {"passed": False, "failure_code": "agent_calibration_invalid", "message": f"压测节点“{agent.name}”校准已失效"}}
                    return run
            definition = copy.deepcopy(version.definition)
            revision_id = run.environment_revision_id

        result = self.preflight_service.run_once(definition, revision_id, agents)
        with self.session_factory.begin() as session:
            run = session.scalar(select(ApiLoadRun).where(ApiLoadRun.id == run_id).with_for_update())
            if run is None or run.state != "preflighting":
                raise LoadRunError("预检结果已过期，任务状态已经变化", status=409, code="preflight_state_conflict")
            preflight_summary = result.to_dict()
            run.summary = {**copy.deepcopy(run.summary or {}), "preflight": preflight_summary}
            configuration = copy.deepcopy(run.configuration)
            target_rate = self._target_rate(configuration["workload"])
            estimated_vus = result.estimated_vus_for_rate(target_rate) if target_rate else 1
            configuration["capacity"]["estimated_vus_from_preflight"] = estimated_vus
            configuration["preflight"] = preflight_summary
            run.configuration = configuration
            allocated_vus = sum(
                int((item.get("allocation") or {}).get("vus") or 0)
                for item in configuration["agents"]
            )
            preflight_capacity_shortfall = bool(target_rate and estimated_vus > allocated_vus)
            allow_run_anyway = configuration["allocation_policy"]["allow_run_anyway"]
            if preflight_capacity_shortfall:
                preflight_summary = {
                    **preflight_summary,
                    "passed": bool(result.passed and allow_run_anyway),
                    "failure_code": "" if allow_run_anyway else "preflight_capacity_shortfall",
                    "warning_code": "preflight_capacity_shortfall" if allow_run_anyway else "",
                    "message": (
                        f"按单链路实测 {result.observed_duration_ms} 毫秒估算，目标吞吐至少需要 "
                        f"{estimated_vus} 个虚拟用户，当前分配 {allocated_vus} 个"
                    ),
                }
                run.summary = {**copy.deepcopy(run.summary or {}), "preflight": preflight_summary}
                configuration["preflight"] = preflight_summary
                run.configuration = configuration
            if result.passed and (not preflight_capacity_shortfall or allow_run_anyway):
                run.state = "queued"
                if preflight_capacity_shortfall:
                    run.verdict = "inconclusive"
            else:
                run.state = "failed"
                run.verdict = "inconclusive"
                run.finished_at = _utc(self.now())
            session.flush()
            return run

    def start(self, run_id, actor_id):
        access.require_permission(actor_id, "api.loadtest.execute")
        with self.session_factory.begin() as session:
            run = self._owned_run(session, run_id, actor_id, for_update=True)
            shards = self._run_shards(session, run.id)
            if run.state == "queued":
                run.state = "starting"
            elif run.state == "starting":
                if shards and all(item.state == "ready" for item in shards):
                    run.state = "running"
                    run.started_at = _utc(self.now())
                return run
            elif run.state == "running":
                raise LoadRunError("任务已经启动，请勿重复点击", status=409, code="duplicate_start")
            else:
                raise LoadRunError("当前任务状态不能启动", status=409, code="start_state_conflict")
            return run

    def stop(self, run_id, reason, actor_id):
        access.require_permission(actor_id, "api.loadtest.execute")
        reason = str(reason or "人工停止").strip()[:1000] or "人工停止"
        with self.session_factory.begin() as session:
            run = self._owned_run(session, run_id, actor_id, for_update=True)
            shards = self._run_shards(session, run.id, for_update=True)
            if run.state in TERMINAL_RUN_STATES:
                return run
            run.stop_reason = reason
            if run.state in {"draft", "preflighting", "queued"}:
                run.state = "cancelled"
                run.finished_at = _utc(self.now())
                run.verdict = "inconclusive"
                for shard in shards:
                    if shard.state not in TERMINAL_SHARD_STATES:
                        shard.state = "cancelled"
            elif run.state in {"starting", "running", "stopping"}:
                run.state = "stopping"
                for shard in shards:
                    if shard.state == "assigned":
                        shard.state = "cancelled"
                    elif shard.state not in TERMINAL_SHARD_STATES:
                        shard.state = "stopping"
                if all(item.state in TERMINAL_SHARD_STATES for item in shards):
                    run.state = "cancelled"
                    run.finished_at = _utc(self.now())
                    run.verdict = "inconclusive"
            else:
                raise LoadRunError("当前任务状态不能停止", status=409, code="stop_state_conflict")
            return run

    def claim_shard(self, agent_id):
        with self.session_factory.begin() as session:
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
                .order_by(
                    priority_order,
                    ApiLoadRun.created_at,
                )
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
            shard.state = "ready"
            shard.last_heartbeat_at = _utc(self.now())
            session.flush()
            if all(item.state == "ready" for item in self._run_shards(session, run.id)):
                run.state = "running"
                run.started_at = _utc(self.now())
            return shard

    def finish_shard(self, agent_id, shard_id, state, *, summary=None, error=None):
        if state not in {"finished", "failed", "cancelled"}:
            raise LoadRunError("分片结束状态无效")
        with self.session_factory.begin() as session:
            shard = session.scalar(select(ApiLoadRunShard).where(ApiLoadRunShard.id == shard_id).with_for_update())
            if shard is None or shard.agent_id != agent_id:
                raise LoadRunError("分片不存在或不属于当前节点", status=404, code="shard_not_found")
            if shard.state in TERMINAL_SHARD_STATES:
                if shard.state != state:
                    raise LoadRunError("分片已经以其他状态结束", status=409, code="shard_state_conflict")
                return shard
            shard.state = state
            shard.summary = _json_object(summary or {}, "分片汇总")
            shard.error = _json_object(error or {}, "分片错误")
            shard.last_heartbeat_at = _utc(self.now())
            run = session.scalar(select(ApiLoadRun).where(ApiLoadRun.id == shard.run_id).with_for_update())
            shards = self._run_shards(session, run.id, for_update=True)
            if all(item.state in TERMINAL_SHARD_STATES for item in shards):
                run.finished_at = _utc(self.now())
                if run.state == "stopping":
                    run.state = "cancelled"
                    run.verdict = "inconclusive"
                elif any(item.state in {"failed", "lost"} for item in shards):
                    run.state = "failed"
                    run.verdict = "inconclusive"
                else:
                    run.state = "finished"
            return shard

    def recover_stale_runs(self, *, stale_after_seconds=120):
        if not isinstance(stale_after_seconds, int) or isinstance(stale_after_seconds, bool) or not 15 <= stale_after_seconds <= 3600:
            raise LoadRunError("分片失联判定时间必须在15到3600秒之间")
        cutoff = _utc(self.now()).timestamp() - stale_after_seconds
        recovered = []
        with self.session_factory.begin() as session:
            runs = tuple(
                session.scalars(
                    select(ApiLoadRun)
                    .where(ApiLoadRun.state.in_(("starting", "running", "stopping")))
                    .order_by(ApiLoadRun.created_at)
                    .with_for_update(skip_locked=True)
                )
            )
            for run in runs:
                shards = self._run_shards(session, run.id, for_update=True)
                barrier_expired = (
                    run.state == "starting"
                    and run.updated_at is not None
                    and _utc(run.updated_at).timestamp() < cutoff
                )
                stale = [
                    item for item in shards
                    if item.state not in TERMINAL_SHARD_STATES
                    and (
                        barrier_expired
                        or (
                            item.last_heartbeat_at is not None
                            and _utc(item.last_heartbeat_at).timestamp() < cutoff
                        )
                    )
                ]
                if not stale:
                    continue
                for shard in stale:
                    shard.state = "lost"
                    shard.error = {"code": "agent_lost", "message": "压测节点心跳超时，未自动迁移剩余压力"}
                run.state = "cancelled" if run.state == "stopping" else "failed"
                run.verdict = "inconclusive"
                run.finished_at = _utc(self.now())
                run.summary = {**copy.deepcopy(run.summary or {}), "recovery": {"lost_shard_ids": [item.id for item in stale]}}
                recovered.append(run.id)
        return tuple(recovered)

    def _parse_create(self, payload):
        if not isinstance(payload, dict):
            raise LoadRunError("压测任务必须是对象")
        allowed = {"scenario_version_id", "environment_revision_id", "workload", "thresholds", "priority", "allocation_policy"}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise LoadRunError(f"压测任务包含不支持字段：{unknown[0]}")
        for field in ("scenario_version_id", "environment_revision_id"):
            if not isinstance(payload.get(field), str) or not payload[field]:
                raise LoadRunError(f"{field}不能为空")
        priority = payload.get("priority", "normal")
        if priority not in PRIORITIES:
            raise LoadRunError("任务优先级必须是紧急、高、普通或低")
        policy = _json_object(payload.get("allocation_policy", {}), "节点策略")
        unknown_policy = sorted(set(policy) - {"allow_fallback", "allow_run_anyway", "agent_ids", "node_group"})
        if unknown_policy:
            raise LoadRunError(f"节点策略包含不支持字段：{unknown_policy[0]}")
        for field in ("allow_fallback", "allow_run_anyway"):
            if not isinstance(policy.get(field, False), bool):
                raise LoadRunError(f"{field}必须是布尔值")
            policy[field] = policy.get(field, False)
        agent_ids = policy.get("agent_ids", [])
        if not isinstance(agent_ids, list) or any(not isinstance(item, str) or not item for item in agent_ids):
            raise LoadRunError("指定节点必须是节点 ID 数组")
        if len(agent_ids) != len(set(agent_ids)):
            raise LoadRunError("指定节点不能重复")
        policy["agent_ids"] = agent_ids
        node_group = policy.get("node_group")
        if node_group is not None and (not isinstance(node_group, str) or not node_group.strip()):
            raise LoadRunError("节点组名称无效")
        return {
            "scenario_version_id": payload["scenario_version_id"],
            "environment_revision_id": payload["environment_revision_id"],
            "workload": _json_object(payload.get("workload"), "负载配置"),
            "thresholds": _json_object(payload.get("thresholds", {}), "性能阈值"),
            "priority": priority,
            "allocation_policy": policy,
        }

    def _selected_agents(self, session, policy):
        query = select(ApiLoadAgent).where(
            ApiLoadAgent.status == "online",
            ApiLoadAgent.scheduling_tier != "disabled",
        )
        if not policy["allow_fallback"]:
            query = query.where(ApiLoadAgent.scheduling_tier != "fallback")
        if policy["agent_ids"]:
            query = query.where(ApiLoadAgent.id.in_(policy["agent_ids"]))
        if policy.get("node_group"):
            query = query.where(ApiLoadAgent.node_group == policy["node_group"])
        agents = tuple(session.scalars(query.order_by(ApiLoadAgent.scheduling_tier, ApiLoadAgent.id)))
        agents = tuple(item for item in agents if agent_heartbeat_is_fresh(item, now=self.now()))
        if policy["agent_ids"] and {item.id for item in agents} != set(policy["agent_ids"]):
            raise LoadRunError("指定节点不存在或当前不在线", status=409, code="agent_unavailable")
        if not agents:
            raise LoadRunError("没有在线压测节点", status=409, code="agent_unavailable")
        return agents

    @staticmethod
    def _dataset_snapshot(session, project_id, definition):
        dataset_id = definition["dataset_contract"].get("dataset_id")
        if not dataset_id:
            return {}
        dataset = session.get(ApiLoadDataset, dataset_id)
        if dataset is None or dataset.project_id != project_id or dataset.status != "active":
            raise LoadRunError("场景数据集不存在或已停用", status=409, code="dataset_unavailable")
        return {"id": dataset.id, "name": dataset.name, "content_hash": dataset.content_hash, "row_count": dataset.row_count, "usage_mode": dataset.usage_mode}

    @staticmethod
    def _estimated_iterations(workload):
        if workload.get("executor") == "constant-vus":
            return int(workload.get("vus") or 0) * int(workload.get("duration_seconds") or 0)
        if workload.get("executor") == "constant-arrival-rate":
            multiplier = 60 if workload.get("time_unit") == "1m" else 1
            return max(1, int(workload.get("rate") or 0) * int(workload.get("duration_seconds") or 0) // multiplier)
        if workload.get("executor") == "ramping-vus":
            return max(
                1,
                sum(
                    int(item.get("duration_seconds") or 0) * int(item.get("target") or 0)
                    for item in workload.get("stages", [])
                    if isinstance(item, dict)
                ),
            )
        if workload.get("executor") == "ramping-arrival-rate":
            multiplier = 60 if workload.get("time_unit") == "1m" else 1
            return max(
                1,
                sum(
                    int(item.get("duration_seconds") or 0) * int(item.get("target") or 0) // multiplier
                    for item in workload.get("stages", [])
                    if isinstance(item, dict)
                ),
            )
        return max(1, int(workload.get("estimated_iterations") or 1))

    @staticmethod
    def _target_rate(workload):
        executor = workload.get("executor")
        if executor == "constant-arrival-rate":
            rate = int(workload.get("rate") or 0)
            return rate / (60 if workload.get("time_unit") == "1m" else 1)
        if executor == "ramping-arrival-rate":
            targets = [int(workload.get("start_rate") or 0)] + [int(item.get("target") or 0) for item in workload.get("stages", []) if isinstance(item, dict)]
            return max(targets) / (60 if workload.get("time_unit") == "1m" else 1)
        return 0

    @staticmethod
    def _shortfall_message(workload, shortfall, vu_shortfall):
        if workload.get("executor") in {"constant-arrival-rate", "ramping-arrival-rate"}:
            return f"压测节点容量不足：目标吞吐还差 {shortfall}，虚拟用户还差 {vu_shortfall}；可降低目标、增加节点或明确选择仍然执行"
        return f"压测节点容量不足：还差 {vu_shortfall or shortfall} 个虚拟用户；可降低目标、增加节点或明确选择仍然执行"

    @staticmethod
    def _run_shards(session, run_id, *, for_update=False):
        query = select(ApiLoadRunShard).where(ApiLoadRunShard.run_id == run_id).order_by(ApiLoadRunShard.sequence)
        if for_update:
            query = query.with_for_update()
        return tuple(session.scalars(query))

    @staticmethod
    def _owned_run(session, run_id, actor_id, *, for_update=False):
        query = select(ApiLoadRun).where(ApiLoadRun.id == run_id)
        if for_update:
            query = query.with_for_update()
        run = session.scalar(query)
        if run is None:
            raise LoadRunError("压测任务不存在", status=404, code="run_not_found")
        access.require_resource(session, run, actor_id, "api.loadtest.execute")
        return run
