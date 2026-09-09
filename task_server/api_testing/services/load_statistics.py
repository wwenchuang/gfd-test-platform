"""Evidence-only load statistics; configured pressure is never measured pressure."""
from datetime import datetime


def _time(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("VU sampling time must include timezone")
    return parsed.timestamp()


def measured_vus(buckets, shards, target, duration):
    """Require simultaneous sustained per-shard gauges.

    k6 emits VU gauges about once per second. Never add peaks at different times
    or multiply planned VUs by completed iterations. For runs of at least 20
    seconds, allow only the outer five-second upload bucket plus one sample tick;
    internal gaps still fail shut.
    """
    boundary_tolerance = 6.0 if duration >= 20 else 1.1
    result = {"available": False, "reached": False, "sustained_seconds": 0.0,
              "target_vus": target, "sampling_tolerance_seconds": boundary_tolerance,
              "reason": "缺少实际并发采样，请升级压测节点后重新执行。"}
    if not shards or target <= 0 or duration <= 0:
        return result
    allocated = [int((shard.allocation or {}).get("vus") or 0) for shard in shards]
    if any(value <= 0 for value in allocated) or sum(allocated) != target:
        result["reason"] = "节点并发分配与本次目标不一致，无法确认实际压力。"
        return result
    common = None
    for shard, required in zip(shards, allocated):
        evidence = sorted(
            [item for item in buckets if item.shard_id == shard.id and item.scenario_step_id == "all" and (item.metrics or {}).get("vu_gauge")],
            key=lambda item: item.bucket_started_at,
        )
        if not evidence:
            return result
        intervals = []
        for item in evidence:
            gauge = item.metrics["vu_gauge"]
            try:
                first, last = _time(gauge["first_at"]), _time(gauge["last_at"])
                # At least roughly one sample per second within the window.
                valid = (gauge["min"] >= required and last >= first
                         and gauge.get("max_gap_seconds", float("inf")) <= 1.1
                         and gauge["count"] >= max(1, int((last - first) / 1.5) + 1))
            except (ValueError, KeyError, TypeError):
                valid = False
            if not valid:
                continue
            if intervals and first - intervals[-1][1] <= 1.1:
                intervals[-1] = (intervals[-1][0], max(last, intervals[-1][1]))
            else:
                intervals.append((first, last))
        if common is None:
            common = intervals
        else:
            common = [(max(a, c), min(b, d)) for a, b in common for c, d in intervals if min(b, d) >= max(a, c)]
    span = max((end - start for start, end in (common or [])), default=0.0)
    reached = span > 0 and span + boundary_tolerance >= duration
    return {**result, "available": True, "reached": reached,
            "sustained_seconds": round(span, 3),
            "reason": "各节点实际并发在共同时间段内达到目标。" if reached else "实际并发不足、持续时间不足或采样不连续，不能证明达到目标。"}


def measured_vu_stages(buckets, shards, shard_workloads):
    """Verify each ramping-VU shard against its own stage trajectory.

    A stage ramps linearly from the previous target. The bounded five-second
    gauge gives enough evidence to compare the stage average and end target,
    while continuous one-second sampling prevents a single peak from passing.
    """
    result = {
        "available": False,
        "reached": False,
        "requires_stage_evidence": True,
        "shards": [],
        "reason": "缺少实际并发与阶段对齐采样，不能仅凭配置并发或完成迭代认定达标。",
    }
    if not shards or not shard_workloads:
        return result
    all_available = True
    all_reached = True
    shard_results = []
    for shard in shards:
        workload = shard_workloads.get(shard.id) or {}
        stages = workload.get("stages") if isinstance(workload.get("stages"), list) else []
        evidence = sorted(
            [item for item in buckets if item.shard_id == shard.id and item.scenario_step_id == "all" and (item.metrics or {}).get("vu_gauge")],
            key=lambda item: item.bucket_started_at,
        )
        parsed = []
        for item in evidence:
            gauge = item.metrics["vu_gauge"]
            try:
                first, last = _time(gauge["first_at"]), _time(gauge["last_at"])
                count = int(gauge["count"])
                valid = (
                    count > 0
                    and last >= first
                    and gauge.get("max_gap_seconds", float("inf")) <= 1.1
                    and count >= max(1, int((last - first) / 1.5) + 1)
                )
                if valid:
                    parsed.append({
                        "first": first,
                        "last": last,
                        "count": count,
                        "min": int(gauge["min"]),
                        "max": int(gauge["max"]),
                        "sum": float(gauge["sum"]),
                    })
            except (ValueError, KeyError, TypeError, OverflowError):
                continue
        available = bool(stages and parsed)
        continuous = available and all(
            current["first"] - previous["last"] <= 1.1
            for previous, current in zip(parsed, parsed[1:])
        )
        total_duration = sum(float(stage.get("duration_seconds") or 0) for stage in stages)
        tolerance = 6.0 if total_duration >= 20 else 1.1
        covered = bool(
            continuous
            and parsed[-1]["last"] - parsed[0]["first"] + tolerance >= total_duration
        )
        origin = parsed[0]["first"] if parsed else 0
        previous_target = int(workload.get("start_vus") or 0)
        elapsed = 0.0
        stage_results = []
        for index, stage in enumerate(stages):
            duration = float(stage.get("duration_seconds") or 0)
            target = int(stage.get("target") or 0)
            end = elapsed + duration
            selected = [
                item for item in parsed
                if elapsed <= ((item["first"] + item["last"]) / 2 - origin) < end
            ]
            count = sum(item["count"] for item in selected)
            actual_average = sum(item["sum"] for item in selected) / count if count else None
            observed_min = min((item["min"] for item in selected), default=None)
            observed_max = max((item["max"] for item in selected), default=None)
            planned_average = (previous_target + target) / 2
            average_tolerance = max(0.75, planned_average * 0.15)
            if target > previous_target:
                endpoint_reached = observed_max is not None and observed_max >= target
                average_reached = actual_average is not None and actual_average >= planned_average - average_tolerance
            elif target < previous_target:
                endpoint_reached = observed_min is not None and observed_min <= target
                average_reached = actual_average is not None and actual_average <= planned_average + average_tolerance
            else:
                endpoint_reached = observed_min is not None and observed_min >= target
                average_reached = actual_average is not None and actual_average >= target - average_tolerance
            reached = bool(covered and endpoint_reached and average_reached)
            stage_results.append({
                "index": index + 1,
                "start_seconds": elapsed,
                "duration_seconds": duration,
                "start_vus": previous_target,
                "target_vus": target,
                "planned_average_vus": round(planned_average, 3),
                "actual_average_vus": round(actual_average, 3) if actual_average is not None else None,
                "observed_min_vus": observed_min,
                "observed_max_vus": observed_max,
                "reached": reached,
            })
            previous_target = target
            elapsed = end
        shard_reached = bool(available and covered and stage_results and all(item["reached"] for item in stage_results))
        all_available = all_available and available
        all_reached = all_reached and shard_reached
        shard_results.append({
            "shard_id": shard.id,
            "available": available,
            "reached": shard_reached,
            "continuous": continuous,
            "covered_seconds": round(parsed[-1]["last"] - parsed[0]["first"], 3) if parsed else 0.0,
            "sampling_tolerance_seconds": tolerance,
            "stages": stage_results,
        })
    return {
        **result,
        "available": all_available,
        "reached": all_available and all_reached,
        "requires_stage_evidence": not all_available,
        "shards": shard_results,
        "reason": (
            "各节点实际并发连续采样，并按各自阶梯配置达到每个阶段。"
            if all_available and all_reached
            else "实际并发未覆盖完整阶梯、采样不连续，或至少一个阶段未达到计划轨迹。"
            if all_available
            else result["reason"]
        ),
    }
