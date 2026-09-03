"""Deterministic, tier-aware load capacity and dataset allocation."""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
import math


TIER_ORDER = {"preferred": 0, "normal": 1, "fallback": 2}
ARRIVAL_EXECUTORS = frozenset({"constant-arrival-rate", "ramping-arrival-rate"})
VU_EXECUTORS = frozenset({"constant-vus", "ramping-vus"})
DATASET_MODES = frozenset({"cycle", "fixed_per_vu", "exclusive_per_iteration"})


class LoadAllocationError(ValueError):
    """Raised when a run cannot be partitioned safely."""


@dataclass(frozen=True)
class ShardAllocation:
    agent_id: str
    agent_name: str
    scheduling_tier: str
    sequence: int
    vus: int
    rate: int
    capacity_shortfall: int
    vu_shortfall: int
    dataset_start: int
    dataset_end: int
    dataset_repeats: bool


@dataclass(frozen=True)
class _Candidate:
    agent: object
    tier: str
    rate_capacity: int
    vu_capacity: int


def _integer(value, field, *, minimum=0):
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise LoadAllocationError(f"{field}必须是不小于 {minimum} 的整数")
    return value


def _limit(agent, field):
    hard = (getattr(agent, "hard_limits", None) or {}).get(field, 0)
    soft = (getattr(agent, "soft_limits", None) or {}).get(field, hard)
    if not isinstance(hard, (int, float)) or not isinstance(soft, (int, float)):
        return 0
    return max(0, int(min(hard, soft)))


def _usage(agent, field):
    value = (getattr(agent, "current_usage", None) or {}).get(field, 0)
    return max(0, int(value)) if isinstance(value, (int, float)) else 0


def _candidates(agents, allow_fallback):
    result = []
    identifiers = [str(getattr(agent, "id", "")) for agent in agents]
    if not all(identifiers) or len(identifiers) != len(set(identifiers)):
        raise LoadAllocationError("节点 ID 不能为空且节点 ID 不能重复")
    for agent in agents:
        tier = str(getattr(agent, "scheduling_tier", "normal"))
        health = getattr(agent, "health", None) or {}
        calibration = health.get("calibration") if isinstance(health, dict) else None
        if (
            getattr(agent, "status", None) != "online"
            or tier not in TIER_ORDER
            or (tier == "fallback" and not allow_fallback)
            or health.get("schedulable", True) is False
            or calibration_state(agent) != "valid"
        ):
            continue
        process_limit = _limit(agent, "max_processes")
        if process_limit and _usage(agent, "processes") >= process_limit:
            continue
        result.append(
            _Candidate(
                agent=agent,
                tier=tier,
                rate_capacity=max(
                    0,
                    min(
                        _limit(agent, "max_iterations_per_second"),
                        int(calibration["max_iterations_per_second"]),
                    )
                    - _usage(agent, "iterations_per_second"),
                ),
                vu_capacity=max(
                    0,
                    min(_limit(agent, "max_vus"), int(calibration["max_vus"]))
                    - _usage(agent, "vus"),
                ),
            )
        )
    return tuple(sorted(result, key=lambda item: (TIER_ORDER[item.tier], str(item.agent.id))))


def calibration_state(agent, *, now=None):
    """Return the stable UI state for an Agent's local capacity calibration."""
    health = getattr(agent, "health", None) or {}
    calibration = health.get("calibration") if isinstance(health, dict) else None
    if not isinstance(calibration, dict) or calibration.get("state") in {None, "missing"}:
        return "missing"
    if calibration.get("state") in {"running", "calibrating"}:
        return "calibrating"
    if calibration.get("state") == "failed":
        return "failed"
    if calibration.get("state") != "valid":
        return "invalidated"
    for field in ("max_iterations_per_second", "max_vus"):
        value = calibration.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
            return "invalidated"
    try:
        valid_until = datetime.fromisoformat(
            str(calibration.get("valid_until") or "").replace("Z", "+00:00")
        )
    except ValueError:
        return "invalidated"
    if valid_until.tzinfo is None:
        return "invalidated"
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    if valid_until.astimezone(timezone.utc) <= current.astimezone(timezone.utc):
        return "expired"
    expected_agent_version = calibration.get("agent_version")
    expected_k6_version = calibration.get("k6_version")
    if expected_agent_version and expected_agent_version != getattr(agent, "agent_version", ""):
        return "invalidated"
    if expected_k6_version and expected_k6_version != getattr(agent, "k6_version", ""):
        return "invalidated"
    return "valid"


def _workload_target(workload):
    executor = workload.get("executor") if isinstance(workload, dict) else None
    if executor == "constant-vus":
        return "vus", _integer(workload.get("vus"), "并发用户数", minimum=1), 0
    if executor == "ramping-vus":
        stages = workload.get("stages")
        if not isinstance(stages, list) or not stages:
            raise LoadAllocationError("阶梯并发至少需要一个阶段")
        target = max(
            [_integer(workload.get("start_vus", 0), "初始并发用户数")]
            + [_integer(item.get("target"), "阶段并发用户数") for item in stages if isinstance(item, dict)]
        )
        if target <= 0:
            raise LoadAllocationError("阶梯并发的最大并发用户数必须大于 0")
        return "vus", target, 0
    if executor in ARRIVAL_EXECUTORS:
        if executor == "constant-arrival-rate":
            rate = _integer(workload.get("rate"), "目标吞吐", minimum=1)
        else:
            stages = workload.get("stages")
            if not isinstance(stages, list) or not stages:
                raise LoadAllocationError("阶梯吞吐至少需要一个阶段")
            rate = max(
                [_integer(workload.get("start_rate", 0), "初始吞吐")]
                + [_integer(item.get("target"), "阶段吞吐") for item in stages if isinstance(item, dict)]
            )
            if rate <= 0:
                raise LoadAllocationError("阶梯吞吐的最大目标必须大于 0")
        unit = workload.get("time_unit", "1s")
        if unit not in {"1s", "1m"}:
            raise LoadAllocationError("吞吐时间单位只支持 1s 或 1m")
        requested_vus = _integer(workload.get("max_vus"), "最大虚拟用户数", minimum=1)
        return "rate", rate, requested_vus
    raise LoadAllocationError("不支持的负载模型")


def _weighted_amount(target, candidates, capacity_getter, weight_getter=None):
    remaining = target
    allocations = {str(item.agent.id): 0 for item in candidates}
    for tier in ("preferred", "normal", "fallback"):
        group = [item for item in candidates if item.tier == tier and capacity_getter(item) > 0]
        if not group or remaining <= 0:
            continue
        capacities = {str(item.agent.id): capacity_getter(item) for item in group}
        group_target = min(remaining, sum(capacities.values()))
        weights = {
            str(item.agent.id): (
                weight_getter(item) if weight_getter else capacities[str(item.agent.id)]
            )
            for item in group
        }
        weight_total = sum(max(0, value) for value in weights.values()) or len(group)
        raw = {
            identifier: group_target * max(0, weights[identifier]) / weight_total
            for identifier in capacities
        }
        assigned = {
            identifier: min(capacities[identifier], int(math.floor(raw[identifier])))
            for identifier in capacities
        }
        left = group_target - sum(assigned.values())
        order = sorted(
            capacities,
            key=lambda identifier: (-(raw[identifier] - math.floor(raw[identifier])), identifier),
        )
        while left > 0:
            progressed = False
            for identifier in order:
                if assigned[identifier] < capacities[identifier]:
                    assigned[identifier] += 1
                    left -= 1
                    progressed = True
                    if left == 0:
                        break
            if not progressed:
                break
        for identifier, amount in assigned.items():
            allocations[identifier] += amount
        remaining -= sum(assigned.values())
    return allocations, remaining


def _weighted_flat(target, candidates, capacity_getter, weight_getter, *, minimum_one=False):
    allocations = {str(item.agent.id): 0 for item in candidates}
    if target <= 0 or not candidates:
        return allocations, target
    if minimum_one:
        for item in candidates:
            identifier = str(item.agent.id)
            if capacity_getter(item) > 0 and target > 0:
                allocations[identifier] = 1
                target -= 1
    capacities = {
        str(item.agent.id): max(0, capacity_getter(item) - allocations[str(item.agent.id)])
        for item in candidates
    }
    assignable = min(target, sum(capacities.values()))
    weights = {str(item.agent.id): max(0, weight_getter(item)) for item in candidates}
    weight_total = sum(weights.values()) or len(candidates)
    raw = {
        identifier: assignable * weights[identifier] / weight_total
        for identifier in capacities
    }
    assigned = {
        identifier: min(capacities[identifier], int(math.floor(raw[identifier])))
        for identifier in capacities
    }
    left = assignable - sum(assigned.values())
    order = sorted(
        capacities,
        key=lambda identifier: (-(raw[identifier] - math.floor(raw[identifier])), identifier),
    )
    while left > 0:
        progressed = False
        for identifier in order:
            if assigned[identifier] < capacities[identifier]:
                assigned[identifier] += 1
                left -= 1
                progressed = True
                if left == 0:
                    break
        if not progressed:
            break
    for identifier, amount in assigned.items():
        allocations[identifier] += amount
    return allocations, target - sum(assigned.values())


def _estimated_iterations(workload):
    value = workload.get("estimated_iterations", 0)
    return _integer(value, "预计迭代数", minimum=1)


def allocate_run(workload, agents, allow_fallback):
    """Return stable, bounded shard allocations without exceeding any Agent."""
    if not isinstance(allow_fallback, bool):
        raise LoadAllocationError("是否允许备用节点必须是布尔值")
    kind, target, requested_vus = _workload_target(workload)
    candidates = _candidates(agents, allow_fallback)
    if not candidates:
        raise LoadAllocationError(
            "没有可调度的压测节点；请检查节点心跳、校准状态、级别和容量"
        )

    if kind == "vus":
        primary, shortfall = _weighted_amount(target, candidates, lambda item: item.vu_capacity)
        vu_amounts = primary
        rate_amounts = {identifier: 0 for identifier in primary}
        vu_shortfall = shortfall
    else:
        unit_multiplier = 60 if workload.get("time_unit") == "1m" else 1
        candidates = tuple(item for item in candidates if item.vu_capacity > 0)
        if not candidates:
            raise LoadAllocationError("压测节点没有可用虚拟用户容量")
        if len(candidates) > requested_vus:
            candidates = tuple(
                sorted(
                    candidates,
                    key=lambda item: (
                        TIER_ORDER[item.tier],
                        -min(
                            item.rate_capacity * unit_multiplier,
                            target * item.vu_capacity // requested_vus,
                        ),
                        str(item.agent.id),
                    ),
                )[:requested_vus]
            )
        primary, shortfall = _weighted_amount(
            target,
            candidates,
            lambda item: min(
                item.rate_capacity * unit_multiplier,
                target * item.vu_capacity // requested_vus,
            ),
        )
        rate_amounts = primary
        achieved_rate = target - shortfall
        selected = [item for item in candidates if primary[str(item.agent.id)] > 0]
        achieved_vus = (
            max(len(selected), math.ceil(requested_vus * achieved_rate / target))
            if achieved_rate
            else 0
        )
        vu_amounts, vu_shortfall = _weighted_flat(
            achieved_vus,
            selected,
            lambda item: item.vu_capacity,
            lambda item: primary[str(item.agent.id)],
            minimum_one=True,
        )

    active = [
        item
        for item in candidates
        if rate_amounts.get(str(item.agent.id), 0) > 0
        or vu_amounts.get(str(item.agent.id), 0) > 0
    ]
    if not active:
        raise LoadAllocationError("压测节点没有剩余容量")

    dataset_mode = workload.get("dataset_mode", "cycle")
    if dataset_mode not in DATASET_MODES:
        raise LoadAllocationError("数据取用方式不支持")
    row_count = _integer(workload.get("dataset_row_count", 0), "数据行数")
    total_vus = sum(vu_amounts.get(str(item.agent.id), 0) for item in active)
    if dataset_mode == "fixed_per_vu" and row_count < total_vus:
        raise LoadAllocationError(
            f"固定数据行不足：需要 {total_vus} 行，当前只有 {row_count} 行"
        )
    if dataset_mode == "exclusive_per_iteration":
        needed_rows = _estimated_iterations(workload)
        if row_count < needed_rows:
            raise LoadAllocationError(
                f"独占数据行不足：预计需要 {needed_rows} 行，当前只有 {row_count} 行"
            )
        range_amounts, range_shortfall = _weighted_flat(
            needed_rows,
            active,
            lambda item: needed_rows,
            lambda item: rate_amounts.get(str(item.agent.id), 0)
            or vu_amounts.get(str(item.agent.id), 0),
        )
        if range_shortfall:
            raise LoadAllocationError("独占数据范围无法完整分片")
    else:
        range_amounts = {}

    allocations = []
    cursor = 0
    for sequence, item in enumerate(active):
        identifier = str(item.agent.id)
        if dataset_mode == "cycle":
            start, end, repeats = 0, row_count, True
        elif dataset_mode == "fixed_per_vu":
            start = cursor
            end = start + vu_amounts.get(identifier, 0)
            cursor = end
            repeats = False
        else:
            start = cursor
            end = start + range_amounts[identifier]
            cursor = end
            repeats = False
        allocations.append(
            ShardAllocation(
                agent_id=identifier,
                agent_name=str(getattr(item.agent, "name", identifier)),
                scheduling_tier=item.tier,
                sequence=sequence,
                vus=vu_amounts.get(identifier, 0),
                rate=rate_amounts.get(identifier, 0),
                capacity_shortfall=0,
                vu_shortfall=0,
                dataset_start=start,
                dataset_end=end,
                dataset_repeats=repeats,
            )
        )
    allocations[0] = replace(
        allocations[0], capacity_shortfall=shortfall, vu_shortfall=vu_shortfall
    )
    return tuple(allocations)
