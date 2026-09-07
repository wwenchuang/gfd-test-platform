"""Evidence-only load statistics; configured pressure is never measured pressure."""
from datetime import datetime


def _time(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("VU sampling time must include timezone")
    return parsed.timestamp()


def measured_vus(buckets, shards, target, duration):
    """Require simultaneous sustained per-shard gauges, allowing one sample tick.

    k6 emits VU gauges about once per second. Never add peaks at different times
    or multiply planned VUs by completed iterations. Missing evidence fails shut.
    """
    result = {"available": False, "reached": False, "sustained_seconds": 0.0,
              "target_vus": target, "sampling_tolerance_seconds": 1.1,
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
    reached = span > 0 and span + 1.1 >= duration
    return {**result, "available": True, "reached": reached,
            "sustained_seconds": round(span, 3),
            "reason": "各节点实际并发在共同时间段内达到目标。" if reached else "实际并发不足、持续时间不足或采样不连续，不能证明达到目标。"}
