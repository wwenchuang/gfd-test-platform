"""Deterministic capacity and dataset sharding tests."""

from types import SimpleNamespace

import pytest

from task_server.api_testing.services.load_allocator import (
    LoadAllocationError,
    allocate_run,
)


def _agent(
    identifier,
    *,
    tier="preferred",
    rate=4_000,
    vus=400,
    used_rate=0,
    used_vus=0,
    status="online",
    schedulable=True,
):
    return SimpleNamespace(
        id=identifier,
        name=f"节点 {identifier}",
        status=status,
        scheduling_tier=tier,
        hard_limits={"max_iterations_per_second": rate, "max_vus": vus},
        soft_limits={"max_iterations_per_second": rate, "max_vus": vus},
        current_usage={"iterations_per_second": used_rate, "vus": used_vus},
        health={
            "schedulable": schedulable,
            "calibration": {
                "state": "valid",
                "valid_until": "2099-01-01T00:00:00+00:00",
                "max_iterations_per_second": rate,
                "max_vus": vus,
            },
        },
    )


PREFERRED_4000 = _agent("preferred-4000")
FALLBACK_2000 = _agent("fallback-2000", tier="fallback", rate=2_000, vus=200)


def _rate_workload(rate=5_000, *, duration=10, dataset_mode="cycle", rows=100):
    return {
        "executor": "constant-arrival-rate",
        "rate": rate,
        "time_unit": "1s",
        "duration_seconds": duration,
        "pre_allocated_vus": 100,
        "max_vus": 500,
        "dataset_mode": dataset_mode,
        "dataset_row_count": rows,
        "estimated_iterations": rate * duration,
    }


def test_allocator_never_uses_fallback_without_opt_in():
    allocations = allocate_run(
        _rate_workload(), [PREFERRED_4000, FALLBACK_2000], False
    )

    assert [item.agent_id for item in allocations] == [PREFERRED_4000.id]
    assert allocations[0].rate == 4_000
    assert allocations[0].capacity_shortfall == 1_000


def test_allocator_uses_fallback_only_after_preferred_capacity_with_opt_in():
    allocations = allocate_run(
        _rate_workload(), [PREFERRED_4000, FALLBACK_2000], True
    )

    assert [(item.agent_id, item.rate) for item in allocations] == [
        ("preferred-4000", 4_000),
        ("fallback-2000", 1_000),
    ]
    assert sum(item.rate for item in allocations) == 5_000
    assert all(item.capacity_shortfall == 0 for item in allocations)


def test_same_tier_weighted_rounding_is_deterministic_and_exact():
    agents = [
        _agent("large", rate=3, vus=30),
        _agent("small", rate=1, vus=10),
    ]
    workload = _rate_workload(7, rows=10)
    workload["max_vus"] = 7

    allocations = allocate_run(workload, agents, False)

    assert [(item.agent_id, item.rate) for item in allocations] == [
        ("large", 3),
        ("small", 1),
    ]
    assert sum(item.rate for item in allocations) == 4
    assert allocations[0].capacity_shortfall == 3


def test_current_usage_and_health_reduce_or_remove_capacity():
    busy = _agent("busy", rate=100, used_rate=70, vus=100, used_vus=20)
    unhealthy = _agent("unhealthy", rate=1_000, schedulable=False)
    workload = _rate_workload(50, rows=10)
    workload["max_vus"] = 50

    allocations = allocate_run(workload, [busy, unhealthy], False)

    assert [(item.agent_id, item.rate) for item in allocations] == [("busy", 30)]
    assert allocations[0].capacity_shortfall == 20


def test_vu_workload_splits_exactly_across_nodes():
    workload = {
        "executor": "constant-vus",
        "vus": 7,
        "duration_seconds": 30,
        "dataset_mode": "cycle",
        "dataset_row_count": 2,
    }
    agents = [_agent("a", vus=6, rate=100), _agent("b", vus=4, rate=100)]

    allocations = allocate_run(workload, agents, False)

    assert sum(item.vus for item in allocations) == 7
    assert [(item.agent_id, item.vus) for item in allocations] == [("a", 4), ("b", 3)]


def test_five_nodes_split_non_uniform_capacity_without_changing_global_target():
    workload = {
        "executor": "constant-vus",
        "vus": 137,
        "duration_seconds": 30,
        "dataset_mode": "fixed_per_vu",
        "dataset_row_count": 137,
    }
    agents = [
        _agent("node-a", vus=10),
        _agent("node-b", vus=20),
        _agent("node-c", vus=30),
        _agent("node-d", vus=40),
        _agent("node-e", vus=50),
    ]

    allocations = allocate_run(workload, agents, False)

    assert len(allocations) == 5
    assert sum(item.vus for item in allocations) == 137
    assert [(item.dataset_start, item.dataset_end) for item in allocations] == [
        (0, 9),
        (9, 27),
        (27, 54),
        (54, 91),
        (91, 137),
    ]
    assert allocations[-1].dataset_end == 137


def test_fixed_per_vu_requires_one_row_for_every_allocated_vu():
    workload = {
        "executor": "constant-vus",
        "vus": 5,
        "duration_seconds": 30,
        "dataset_mode": "fixed_per_vu",
        "dataset_row_count": 4,
    }

    with pytest.raises(LoadAllocationError, match="固定数据行不足"):
        allocate_run(workload, [_agent("a", vus=10)], False)


def test_exclusive_rows_are_partitioned_without_overlap():
    workload = _rate_workload(7, duration=10, dataset_mode="exclusive_per_iteration", rows=70)
    agents = [_agent("a", rate=4, vus=40), _agent("b", rate=3, vus=30)]
    workload["max_vus"] = 7

    allocations = allocate_run(workload, agents, False)

    assert [(item.dataset_start, item.dataset_end) for item in allocations] == [(0, 40), (40, 70)]
    assert allocations[0].dataset_end == allocations[1].dataset_start
    assert all(item.dataset_repeats is False for item in allocations)


def test_exclusive_ranges_follow_cross_tier_load_instead_of_tier_priority():
    workload = _rate_workload(5, duration=10, dataset_mode="exclusive_per_iteration", rows=50)
    workload["max_vus"] = 5
    agents = [
        _agent("preferred", rate=4, vus=4),
        _agent("fallback", tier="fallback", rate=2, vus=2),
    ]

    allocations = allocate_run(workload, agents, True)

    assert [(item.rate, item.dataset_end - item.dataset_start) for item in allocations] == [
        (4, 40),
        (1, 10),
    ]


def test_arrival_rate_does_not_assign_load_to_agent_without_vu_capacity():
    full = _agent("full-vus", rate=100, vus=10, used_vus=10)
    available = _agent("available", rate=100, vus=10)
    workload = _rate_workload(50, rows=10)
    workload["max_vus"] = 10

    allocations = allocate_run(workload, [full, available], False)

    assert [(item.agent_id, item.rate, item.vus) for item in allocations] == [
        ("available", 50, 10)
    ]


def test_exclusive_mode_rejects_insufficient_rows_and_cycle_records_reuse():
    workload = _rate_workload(5, duration=10, dataset_mode="exclusive_per_iteration", rows=49)
    workload["max_vus"] = 5
    with pytest.raises(LoadAllocationError, match="独占数据行不足"):
        allocate_run(workload, [_agent("a", rate=10, vus=10)], False)

    cycle = _rate_workload(5, dataset_mode="cycle", rows=3)
    cycle["max_vus"] = 5
    allocations = allocate_run(cycle, [_agent("a", rate=3), _agent("b", rate=2)], False)
    assert [(item.dataset_start, item.dataset_end) for item in allocations] == [(0, 3), (0, 3)]
    assert all(item.dataset_repeats is True for item in allocations)


def test_no_schedulable_agent_returns_a_clear_error():
    with pytest.raises(LoadAllocationError, match="没有可调度的压测节点"):
        allocate_run(_rate_workload(10), [_agent("off", status="offline")], False)


def test_duplicate_agent_ids_and_exhausted_process_slots_are_rejected():
    duplicate = _agent("same")
    with pytest.raises(LoadAllocationError, match="节点 ID 不能重复"):
        allocate_run(_rate_workload(10), [duplicate, duplicate], False)

    full = _agent("full")
    full.hard_limits["max_processes"] = 1
    full.soft_limits["max_processes"] = 1
    full.current_usage["processes"] = 1
    with pytest.raises(LoadAllocationError, match="没有可调度的压测节点"):
        allocate_run(_rate_workload(10), [full], False)


def test_uncalibrated_or_expired_agents_cannot_run_and_calibration_caps_capacity():
    uncalibrated = _agent("uncalibrated", rate=100, vus=100)
    uncalibrated.health.pop("calibration")
    expired = _agent("expired", rate=100, vus=100)
    expired.health["calibration"]["valid_until"] = "2020-01-01T00:00:00+00:00"
    with pytest.raises(LoadAllocationError, match="校准"):
        allocate_run(_rate_workload(50), [uncalibrated, expired], False)

    calibrated = _agent("calibrated", rate=100, vus=100)
    calibrated.health["calibration"].update(
        max_iterations_per_second=20,
        max_vus=20,
    )
    workload = _rate_workload(50)
    workload["max_vus"] = 50
    allocations = allocate_run(workload, [calibrated], False)
    assert [(item.rate, item.vus) for item in allocations] == [(20, 20)]
    assert allocations[0].capacity_shortfall == 30
