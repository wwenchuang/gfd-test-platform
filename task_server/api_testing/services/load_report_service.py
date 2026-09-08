"""Build deterministic performance reports from durable metric evidence."""

import copy
from datetime import timezone
import math

from sqlalchemy import select

from .. import access
from .load_statistics import measured_vus
from ..models.load_testing import (
    ApiLoadAgent,
    ApiLoadMetricBucket,
    ApiLoadRun,
    ApiLoadRunShard,
    ApiLoadSample,
    ApiLoadScenarioVersion,
)


TERMINAL_STATES = frozenset({"finished", "failed", "cancelled"})
THRESHOLDS = {
    "http_error_rate": ("HTTP错误率", "transport.http_error_rate"),
    "business_failure_rate": ("业务断言失败率", "business.failure_rate"),
    "workflow_failure_rate": ("完整链路失败率", "workflow.failure_rate"),
    "dropped_iteration_rate": ("丢弃迭代率", "dropped_iterations.rate"),
    "p95_ms": ("P95响应时间", "latency.p95_ms"),
    "p99_ms": ("P99响应时间", "latency.p99_ms"),
    "max_latency_ms": ("最大响应时间", "latency.max_ms"),
    "min_iterations_per_second": ("最低每秒迭代数", "load_goal.actual_iterations_per_second"),
    "min_requests_per_second": ("最低每秒请求数", "transport.requests_per_second"),
}
OPERATORS = {
    "less_than": ("小于", lambda actual, expected: actual < expected),
    "less_than_or_equal": ("小于等于", lambda actual, expected: actual <= expected),
    "greater_than": ("大于", lambda actual, expected: actual > expected),
    "greater_than_or_equal": ("大于等于", lambda actual, expected: actual >= expected),
}


class LoadReportError(ValueError):
    pass


def _utc(value):
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _rate(numerator, denominator):
    return round(numerator / denominator, 6) if denominator else 0


def _duration(run, buckets):
    started = _utc(run.started_at)
    finished = _utc(run.finished_at)
    if started is not None and finished is not None and finished > started:
        return (finished - started).total_seconds()
    if buckets:
        starts = [_utc(item.bucket_started_at) for item in buckets]
        return max(5.0, (max(starts) - min(starts)).total_seconds() + 5.0)
    return 0.0


def _configured_load_duration(configuration):
    workload = (configuration or {}).get("workload") or configuration or {}
    duration = workload.get("duration_seconds")
    if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration > 0:
        return float(duration)
    stages = workload.get("stages") if isinstance(workload.get("stages"), list) else []
    durations = [
        item.get("duration_seconds")
        for item in stages
        if isinstance(item, dict)
    ]
    if durations and all(
        isinstance(item, (int, float)) and not isinstance(item, bool) and item > 0
        for item in durations
    ):
        return float(sum(durations))
    return 0.0


def _missing_windows(buckets):
    groups = {}
    for item in buckets:
        groups.setdefault((item.shard_id, item.scenario_step_id), set()).add(
            int(_utc(item.bucket_started_at).timestamp())
        )
    missing = 0
    for values in groups.values():
        ordered = sorted(values)
        missing += sum(max(0, (right - left) // 5 - 1) for left, right in zip(ordered, ordered[1:]))
    return missing


def _http_sample_integrity(buckets):
    """Reconcile full-stream HTTP counters, never unlike workflow counters.

    k6 emits one http_req_duration point for each http_reqs sample. Compare
    whole-run step/shard totals: points may straddle individual bucket edges.
    Keeping groups separate prevents two nodes' opposite errors cancelling out.
    """
    groups = {}
    for bucket in buckets:
        key = (bucket.shard_id, bucket.scenario_step_id)
        item = groups.setdefault(key, {"shard_id": key[0], "step_id": key[1], "requests": 0, "latency_samples": 0})
        metrics = bucket.metrics or {}
        item["requests"] += int(metrics.get("requests") or 0)
        item["latency_samples"] += int((metrics.get("latency_histogram") or {}).get("count") or 0)
    mismatches = [item for item in groups.values() if item["requests"] != item["latency_samples"]]
    acceptable = all(
        abs(item["requests"] - item["latency_samples"]) <= 2
        and abs(item["requests"] - item["latency_samples"]) * 10 <= max(item["requests"], item["latency_samples"])
        for item in mismatches
    )
    return {"consistent": not mismatches, "acceptable": acceptable,
            "tolerance": {"max_count": 2, "max_ratio": 0.1}, "mismatches": mismatches,
            "scope": "同一节点、同一步骤、完整运行的 HTTP 请求数与耗时采样数"}


def _percentile(histogram, percentile):
    total = histogram["count"]
    if total <= 0:
        return 0.0
    rank = max(1, math.ceil(total * percentile))
    cumulative = 0
    for index, count in enumerate(histogram["counts"]):
        cumulative += count
        if cumulative >= rank:
            if index < len(histogram["bounds_ms"]):
                # A bucket upper bound is conservative, but the observed maximum
                # is a tighter bound and must also bound every percentile.
                return min(float(histogram["bounds_ms"][index]), float(histogram["max_ms"]))
            return float(histogram["max_ms"])
    return float(histogram["max_ms"])


def _path(value, dotted):
    current = value
    for part in dotted.split("."):
        current = current.get(part, {}) if isinstance(current, dict) else {}
    return current if isinstance(current, (int, float)) and not isinstance(current, bool) else 0


def _monitoring_evidence(run):
    selected = ((run.configuration or {}).get("monitoring") or {}).get("services", [])
    if not selected:
        return {"state": "not_selected", "terminal": True, "services": []}, True
    result = copy.deepcopy((run.summary or {}).get("monitoring") or {
        "state": "collecting", "terminal": False, "services": [
            {"revision_id": s["revision_id"], "name": s["name"], "required": s["required"], "state": "collecting", "metrics": []}
            for s in selected]})
    by_revision = {s.get("revision_id"): s for s in result.get("services", [])}
    required_ok = all(result.get("terminal") and by_revision.get(s["revision_id"], {}).get("state") == "completed"
                      for s in selected if s.get("required"))
    return result, required_ok


class LoadReportService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def build(self, run_id, actor_id):
        access.require_permission(actor_id, "api.loadtest.view")
        with self.session_factory() as session:
            run = session.get(ApiLoadRun, run_id)
            if run is None:
                raise LoadReportError("压测任务不存在")
            access.require_resource(session, run, actor_id, "api.loadtest.view")
            buckets = tuple(
                session.scalars(
                    select(ApiLoadMetricBucket)
                    .where(ApiLoadMetricBucket.run_id == run.id)
                    .order_by(ApiLoadMetricBucket.bucket_started_at, ApiLoadMetricBucket.shard_id, ApiLoadMetricBucket.scenario_step_id)
                )
            )
            shards = tuple(
                session.scalars(
                    select(ApiLoadRunShard)
                    .where(ApiLoadRunShard.run_id == run.id)
                    .order_by(ApiLoadRunShard.sequence)
                )
            )
            agent_ids = [item.agent_id for item in shards if item.agent_id]
            agents = {
                item.id: item
                for item in session.scalars(select(ApiLoadAgent).where(ApiLoadAgent.id.in_(agent_ids)))
            } if agent_ids else {}
            samples = tuple(
                session.scalars(
                    select(ApiLoadSample)
                    .where(ApiLoadSample.run_id == run.id)
                    .order_by(ApiLoadSample.created_at, ApiLoadSample.id)
                )
            )
            shard_workloads = {}
            if (run.configuration.get("workload") or {}).get("executor") == "ramping-arrival-rate":
                from ..load_agent_http import _shard_workload
                shard_workloads = {shard.id: _shard_workload(session, run, shard, run.configuration["workload"]) for shard in shards}
            version = session.get(ApiLoadScenarioVersion, run.scenario_version_id)
            previous, comparison_reason = self._previous_run(session, run)
            previous_buckets = ()
            if previous is not None and not comparison_reason:
                previous_buckets = tuple(
                    session.scalars(
                        select(ApiLoadMetricBucket).where(ApiLoadMetricBucket.run_id == previous.id)
                    )
                )

        aggregate = self._aggregate(
            run,
            buckets,
            duration_seconds=_configured_load_duration(run.configuration),
        )
        missing_windows = _missing_windows(buckets)
        sample_integrity = _http_sample_integrity(buckets)
        evidence_complete = (
            run.state == "finished"
            and bool(shards)
            and all(item.state == "finished" for item in shards)
            and bool(buckets)
            and missing_windows == 0
            and sample_integrity["acceptable"]
        )
        workload = run.configuration.get("workload") or run.configuration
        if workload.get("executor") == "constant-vus":
            aggregate["vu_evidence"] = measured_vus(buckets, shards, int(workload.get("vus") or 0), _configured_load_duration(run.configuration))
        aggregate["stage_starts"] = {
            str(index): sum((bucket.metrics or {}).get("workflow_starts", 0) for bucket in buckets if bucket.scenario_step_id == f"__load_stage_{index}")
            for index in range(len(workload.get("stages") or []))
        } if any((bucket.metrics or {}).get("workflow_starts", 0) for bucket in buckets) else None
        load_goal = self._load_goal(run.configuration, aggregate)
        if shard_workloads:
            shard_goals = []
            for shard in shards:
                observed = [bucket for bucket in buckets if bucket.shard_id == shard.id and bucket.scenario_step_id.startswith("__load_stage_") and "workflow_starts" in (bucket.metrics or {})]
                starts = {str(i): sum((bucket.metrics or {}).get("workflow_starts", 0) for bucket in observed if bucket.scenario_step_id == f"__load_stage_{i}") for i in range(len(shard_workloads[shard.id].get("stages") or []))} if observed else None
                goal = self._load_goal(shard_workloads[shard.id], {**aggregate, "stage_starts": starts})
                shard_goals.append({"shard_id": shard.id, "reached": goal["reached"], "requires_stage_evidence": goal["requires_stage_evidence"], "stages": goal["stages"]})
            load_goal["outside_stage_starts"] = sum((b.metrics or {}).get("workflow_starts", 0) for b in buckets if b.scenario_step_id == "__load_stage_outside")
            load_goal["shards"] = shard_goals
            load_goal["requires_stage_evidence"] = any(goal["requires_stage_evidence"] for goal in shard_goals)
            load_goal["reached"] = load_goal["reached"] and all(goal["reached"] for goal in shard_goals)
            load_goal["explanation"] += " 每个节点也必须逐阶段达到其分配目标，不能用一个节点的多发请求抵消另一个节点的缺失。"

        sections = self._sections(aggregate)
        report_basis = {"load_goal": load_goal, **sections}
        thresholds = self._thresholds(run.configuration.get("thresholds") or {}, report_basis)
        required_failures = [item for item in thresholds if item["required"] and not item["passed"]]
        missing_threshold_evidence = any(item["required"] and item["actual"] is None for item in thresholds)
        # The stored verdict is a dispatch-time/finalization snapshot. Rebuild
        # the report from durable metrics so a reporting fix cannot leave an
        # otherwise complete historical run permanently marked inconclusive.
        monitoring, monitoring_ok = _monitoring_evidence(run)
        forced_inconclusive = not evidence_complete or not load_goal["reached"] or missing_threshold_evidence or not monitoring_ok
        verdict = "inconclusive" if forced_inconclusive else "failed" if required_failures else "passed"
        if not sample_integrity["acceptable"]:
            explanation = "HTTP 请求与耗时采样计数不一致，指标证据不完整；请检查采集链路，不能据此判定性能通过。"
        elif not evidence_complete:
            explanation = "运行证据不完整：指标窗口缺失、没有有效采样或节点未正常完成，请查看证据明细。"
        elif not load_goal["reached"]:
            explanation = "未达到目标负载，当前数据不能证明系统满足性能要求。"
        elif missing_threshold_evidence:
            explanation = "必选性能标准缺少对应样本，不能把无采样当成通过。"
        elif not monitoring_ok:
            explanation = "必选资源监控尚未收齐或存在采样缺失，暂不能形成完整性能结论。"
        elif required_failures:
            explanation = "目标负载已达到，但有必选性能阈值未通过。"
        else:
            explanation = "目标负载和全部必选性能阈值均已通过。"

        if sample_integrity["acceptable"] and not sample_integrity["consistent"]:
            explanation += " 存在容差内采样计数偏差；耗时指标仅基于已收到的样本，尾部耗时可能受影响。"

        comparison = self._comparison(previous, previous_buckets, aggregate, comparison_reason)
        return {
            "test_context": copy.deepcopy((run.configuration or {}).get("test_context") or {}),
            "stop_policy": copy.deepcopy((run.configuration or {}).get("stop_policy")),
            "run_id": run.id,
            "state": run.state,
            "verdict": verdict,
            "verdict_label": {"passed": "通过", "failed": "未通过", "inconclusive": "证据不足"}[verdict],
            "verdict_explanation": explanation,
            "statistics_schema_version": 2,
            "statistical_basis": {
                "rate_duration_seconds": aggregate["duration_seconds"],
                "planned_duration_seconds": _configured_load_duration(run.configuration),
                "observed_wall_seconds": _duration(run, buckets),
                "rate_duration_basis": "配置负载时长" if _configured_load_duration(run.configuration) else "观测时间范围",
                "percentile_method": "合并耗时分桶后取累计分位数，不平均节点或时段分位数。",
                "request_rate_label": "请求吞吐 RPS，不等同于成功业务事务 TPS",
            },
            "labels": {
                "load_goal": "负载目标",
                "thresholds": "性能阈值",
                "transport": "HTTP传输",
                "business": "业务断言",
                "workflow": "完整链路",
                "dropped_iterations": "丢弃迭代",
                "latency": "响应时间",
                "steps": "接口与步骤",
                "agents": "压测节点",
                "samples": "脱敏失败样本",
                "comparison": "历史对比",
                "evidence": "证据完整性",
            },
            "load_goal": load_goal,
            "thresholds": thresholds,
            **sections,
            "series": self._series(buckets),
            "steps": self._steps(buckets, version),
            "agents": self._agents(shards, agents),
            "samples": self._samples(samples),
            "comparison": comparison,
            "monitoring": monitoring,
            "evidence": {
                "monitoring_required_complete": monitoring_ok,
                "label": "证据完整性",
                "complete": evidence_complete,
                "sample_integrity": sample_integrity,
                "bucket_count": len(buckets),
                "missing_windows": missing_windows,
                "finished_shards": sum(item.state == "finished" for item in shards),
                "total_shards": len(shards),
                "scenario_snapshot": copy.deepcopy(run.configuration.get("scenario") or {}),
                "environment_snapshot": copy.deepcopy(run.configuration.get("environment") or {}),
                "workload_snapshot": copy.deepcopy(run.configuration.get("workload") or {}),
                "agent_snapshot": copy.deepcopy(run.configuration.get("agents") or []),
            },
        }

    @staticmethod
    def _aggregate(run, buckets, *, duration_seconds=0.0):
        totals = {
            key: 0
            for key in (
                "requests", "iterations", "dropped_iterations", "bytes_sent", "bytes_received",
                "http_failures", "business_assertions", "business_failures",
                "workflow_iterations", "workflow_failures",
            )
        }
        histogram = {"bounds_ms": [], "counts": [], "count": 0, "sum_ms": 0.0, "max_ms": 0.0}
        for bucket in buckets:
            metrics = bucket.metrics or {}
            for key in totals:
                totals[key] += int(metrics.get(key) or 0)
            current = metrics.get("latency_histogram") or {}
            bounds = current.get("bounds_ms") or []
            counts = current.get("counts") or []
            if not histogram["bounds_ms"]:
                histogram["bounds_ms"] = list(bounds)
                histogram["counts"] = [0 for _ in counts]
            if bounds != histogram["bounds_ms"] or len(counts) != len(histogram["counts"]):
                continue
            histogram["counts"] = [left + int(right or 0) for left, right in zip(histogram["counts"], counts)]
            histogram["count"] += int(current.get("count") or 0)
            histogram["sum_ms"] += float(current.get("sum_ms") or 0)
            histogram["max_ms"] = max(histogram["max_ms"], float(current.get("max_ms") or 0))
        duration = float(duration_seconds) if duration_seconds > 0 else _duration(run, buckets)
        return {"totals": totals, "histogram": histogram, "duration_seconds": duration}

    @staticmethod
    def _sections(aggregate):
        total = aggregate["totals"]
        duration = aggregate["duration_seconds"]
        histogram = aggregate["histogram"]
        return {
            "transport": {
                "label": "HTTP传输",
                "requests": total["requests"],
                "requests_per_second": round(total["requests"] / duration, 3) if duration else 0,
                "http_failures": total["http_failures"],
                "http_error_rate": _rate(total["http_failures"], total["requests"]),
                "bytes_sent": total["bytes_sent"],
                "bytes_received": total["bytes_received"],
            },
            "business": {
                "label": "业务断言",
                "assertions": total["business_assertions"],
                "failures": total["business_failures"],
                "failure_rate": _rate(total["business_failures"], total["business_assertions"]),
            },
            "workflow": {
                "label": "完整链路",
                "iterations": total["workflow_iterations"],
                "failures": total["workflow_failures"],
                "failure_rate": _rate(total["workflow_failures"], total["workflow_iterations"]),
            },
            "dropped_iterations": {
                "label": "丢弃迭代",
                "count": total["dropped_iterations"],
                "rate": _rate(total["dropped_iterations"], total["iterations"] + total["dropped_iterations"]),
            },
            "latency": {
                "label": "响应时间",
                "sample_count": histogram["count"],
                "average_ms": round(histogram["sum_ms"] / histogram["count"], 3) if histogram["count"] else 0,
                "p50_ms": _percentile(histogram, 0.50),
                "p90_ms": _percentile(histogram, 0.90),
                "p95_ms": _percentile(histogram, 0.95),
                "p99_ms": _percentile(histogram, 0.99),
                "max_ms": round(histogram["max_ms"], 3),
            },
        }

    @staticmethod
    def _load_goal(configuration, aggregate):
        workload = configuration.get("workload") or configuration
        executor = workload.get("executor") or ""
        actual_rate = round(aggregate["totals"]["iterations"] / aggregate["duration_seconds"], 3) if aggregate["duration_seconds"] else 0
        if executor == "ramping-arrival-rate":
            unit_seconds = 60 if workload.get("time_unit") == "1m" else 1
            previous = float(workload.get("start_rate") or 0) / unit_seconds
            stages = []
            expected = 0.0
            elapsed = 0.0
            for index, stage in enumerate(workload.get("stages") or []):
                duration = float(stage.get("duration_seconds") or 0)
                target = float(stage.get("target") or 0) / unit_seconds
                count = (previous + target) / 2 * duration
                stages.append({"index": index + 1, "start_seconds": elapsed, "duration_seconds": duration,
                               "start_rate": previous, "target_rate": target, "expected_iterations": count})
                elapsed += duration
                expected += count
                previous = target
            target_rate = expected / elapsed if elapsed else 0
            actual_stages = aggregate.get("stage_starts")
            if actual_stages is not None:
                for stage in stages:
                    stage["actual_started_iterations"] = actual_stages.get(str(stage["index"] - 1), 0)
                    stage["reached"] = stage["actual_started_iterations"] >= stage["expected_iterations"] * .99
            return {"label": "负载目标", "model": executor, "model_label": "阶梯到达率",
                    "target_iterations_per_second": target_rate, "actual_iterations_per_second": actual_rate,
                    "expected_iterations": expected, "stages": stages, "requires_stage_evidence": actual_stages is None,
                    "attainment_rate": round(aggregate["totals"]["iterations"] / expected, 4) if expected else None,
                    "reached": actual_stages is not None and all(s.get("reached") for s in stages),
                    "explanation": "按各节点 k6 场景自身起点标记实际发起阶段，逐阶段对照计划迭代量的99%；业务完成和采样完整性另行判断。" if actual_stages is not None else "已按各阶段起止到达率计算计划迭代量；缺少与阶段起点对齐的实际发起证据，不能用全程平均判定阶梯达标。"}
        if executor == "constant-arrival-rate":
            target = float(workload.get("rate") or 0) / (60 if workload.get("time_unit") == "1m" else 1)
            if workload.get("time_unit") not in {None, "1s", "1m"}:
                target = 0
            reached = target > 0 and actual_rate >= target * 0.99
            return {
                "label": "负载目标", "model": executor, "model_label": "固定到达率",
                "target_iterations_per_second": target, "actual_iterations_per_second": actual_rate,
                "attainment_rate": round(actual_rate / target, 4) if target else 0,
                "reached": reached,
                "explanation": (
                    f"按配置负载时长计算，实际完成迭代率 {actual_rate:g} 次/秒，"
                    f"目标 {target:g} 次/秒，完成比例 {actual_rate / target * 100 if target else 0:.2f}%；"
                    f"{'已达到' if reached else '未达到'} 99% 的达标门槛。完整节点证据另行校验。"
                ),
            }
        target_vus = int(workload.get("vus") or max((item.get("target", 0) for item in workload.get("stages", [])), default=0))
        return {
            "label": "负载目标",
            "model": executor,
            "model_label": "固定并发用户" if executor == "constant-vus" else "阶梯并发用户",
            "target_vus": target_vus,
            "actual_iterations_per_second": actual_rate,
            "reached": executor == "constant-vus" and bool((aggregate.get("vu_evidence") or {}).get("reached")) and aggregate["totals"]["iterations"] > 0,
            "vu_evidence": aggregate.get("vu_evidence") or {},
            "explanation": (aggregate.get("vu_evidence") or {}).get("reason") or "缺少实际并发与阶段对齐采样，不能仅凭配置并发或完成迭代认定达标。",
        }

    @staticmethod
    def _thresholds(configuration, report):
        result = []
        for key in sorted(configuration):
            rule = configuration[key]
            if not isinstance(rule, dict):
                continue
            label, path = THRESHOLDS.get(key, (key, ""))
            operator = str(rule.get("operator") or "")
            operator_label, predicate = OPERATORS.get(operator, (operator or "未知比较", lambda _a, _b: False))
            expected = rule.get("value")
            actual = _path(report, path) if path else 0
            sample_paths = {
                "http_error_rate": "transport.requests", "business_failure_rate": "business.assertions",
                "workflow_failure_rate": "workflow.iterations", "p95_ms": "latency.sample_count",
                "p99_ms": "latency.sample_count", "max_latency_ms": "latency.sample_count",
            }
            if key in sample_paths and _path(report, sample_paths[key]) <= 0:
                actual = None
            valid_expected = isinstance(expected, (int, float)) and not isinstance(expected, bool) and math.isfinite(float(expected))
            passed = bool(path and actual is not None and valid_expected and predicate(float(actual), float(expected)))
            result.append({
                "key": key,
                "label": label,
                "operator": operator,
                "operator_label": operator_label,
                "expected": expected,
                "actual": actual,
                "required": rule.get("required") is not False,
                "passed": passed,
                "status_label": "无样本，无法判断" if actual is None else "通过" if passed else "未通过",
            })
        return result

    @classmethod
    def _series(cls, buckets):
        rows = []
        grouped = {}
        for item in buckets:
            grouped.setdefault(_utc(item.bucket_started_at), []).append(item)
        for started_at in sorted(grouped):
            selected = grouped[started_at]
            aggregate = cls._aggregate(
                type("RunWindow", (), {"started_at": None, "finished_at": None})(),
                selected,
            )
            sections = cls._sections(aggregate)
            totals = aggregate["totals"]
            rows.append({
                "started_at": started_at.isoformat(),
                "requests": totals["requests"],
                "iterations": totals["iterations"],
                "http_failures": totals["http_failures"],
                "business_failures": totals["business_failures"],
                "p95_ms": sections["latency"]["p95_ms"],
            })
        return rows

    @classmethod
    def _steps(cls, buckets, version):
        names = {
            str(item.get("id")): str(item.get("name") or item.get("id"))
            for item in ((version.definition.get("steps") if version else None) or [])
            if isinstance(item, dict) and item.get("id")
        }
        result = []
        for step_id in sorted({item.scenario_step_id for item in buckets if not item.scenario_step_id.startswith("__load_stage_")}):
            selected = [item for item in buckets if item.scenario_step_id == step_id]
            aggregate = cls._aggregate(type("RunWindow", (), {"started_at": None, "finished_at": None})(), selected)
            sections = cls._sections(aggregate)
            result.append({
                "id": step_id,
                "name": names.get(step_id, "全部步骤" if step_id == "all" else step_id),
                "requests": sections["transport"]["requests"],
                "http_error_rate": sections["transport"]["http_error_rate"],
                "business_failure_rate": sections["business"]["failure_rate"],
                "p95_ms": sections["latency"]["p95_ms"],
            })
        return sorted(result, key=lambda item: (-item["p95_ms"], item["id"]))

    @staticmethod
    def _agents(shards, agents):
        return [
            {
                "id": item.agent_id,
                "shard_id": item.id,
                "load_generator_resources": copy.deepcopy((item.summary or {}).get("load_generator_resources")),
                "name": agents[item.agent_id].name if item.agent_id in agents else "节点已删除",
                "state": item.state,
                "state_label": {
                    "assigned": "已分配", "ready": "已就绪", "running": "运行中", "stopping": "停止中",
                    "finished": "已完成", "failed": "失败", "cancelled": "已取消",
                }.get(item.state, item.state),
                "allocation": copy.deepcopy(item.allocation),
                "summary": copy.deepcopy(item.summary),
                "error": {"message": str((item.error or {}).get("message") or "")[:500]},
            }
            for item in shards
        ]

    @staticmethod
    def _samples(samples):
        return [
            {
                "step_id": item.scenario_step_id,
                "kind": item.kind,
                "elapsed_ms": item.elapsed_ms,
                "status_code": item.status_code,
                "business_code": item.business_code,
                "occurrence_count": item.occurrence_count,
                "summary": str((item.payload or {}).get("check") or (item.payload or {}).get("summary") or "")[:300],
            }
            for item in samples
        ]

    @staticmethod
    def _previous_run(session, run):
        workload = (run.configuration or {}).get("workload") or {}
        candidates = tuple(
            session.scalars(
                select(ApiLoadRun)
                .where(
                    ApiLoadRun.id != run.id,
                    ApiLoadRun.scenario_version_id == run.scenario_version_id,
                    ApiLoadRun.state == "finished",
                    ApiLoadRun.created_at < run.created_at,
                )
                .order_by(ApiLoadRun.created_at.desc())
                .limit(10)
            )
        )
        if not candidates:
            return None, "没有可比历史运行"
        for item in candidates:
            if (
                item.environment_revision_id == run.environment_revision_id
                and item.load_model == run.load_model
                and ((item.configuration or {}).get("workload") or {}) == workload
                and ((item.configuration or {}).get("test_context") or {}) == ((run.configuration or {}).get("test_context") or {})
                and ((item.configuration or {}).get("monitoring") or {}) == ((run.configuration or {}).get("monitoring") or {})
                and ((item.configuration or {}).get("dataset") or {}) == ((run.configuration or {}).get("dataset") or {})
            ):
                return item, ""
        latest = candidates[0]
        if latest.environment_revision_id != run.environment_revision_id:
            reason = "最近历史运行使用了不同的环境版本"
        elif latest.load_model != run.load_model:
            reason = "最近历史运行使用了不同的负载模型"
        elif ((latest.configuration or {}).get("workload") or {}) != workload:
            reason = "最近历史运行使用了不同的负载参数"
        else:
            reason = "测试条件、数据快照或监控配置不同，不能直接比较性能改善"
        return latest, reason

    @classmethod
    def _comparison(cls, previous, previous_buckets, current, reason=""):
        if reason:
            result = {"label": "历史对比", "compatible": False, "reason": reason}
            if previous is not None:
                result["previous_run_id"] = previous.id
            return result
        if previous is None or not previous_buckets:
            return {"label": "历史对比", "compatible": False, "reason": "没有可比历史运行"}
        baseline = cls._aggregate(
            previous,
            previous_buckets,
            duration_seconds=_configured_load_duration(previous.configuration),
        )
        current_sections = cls._sections(current)
        previous_sections = cls._sections(baseline)
        current_p95 = current_sections["latency"]["p95_ms"]
        previous_p95 = previous_sections["latency"]["p95_ms"]
        return {
            "label": "历史对比",
            "compatible": True,
            "previous_run_id": previous.id,
            "p95_ms": {"current": current_p95, "previous": previous_p95, "change_rate": _rate(current_p95 - previous_p95, previous_p95)},
            "http_error_rate": {
                "current": current_sections["transport"]["http_error_rate"],
                "previous": previous_sections["transport"]["http_error_rate"],
            },
        }
