"""Evidence-grounded AI diagnosis for deterministic load reports."""

import copy
import hashlib
import json
import os

from sqlalchemy import select

from task_server.services.ai_skill_service import run_ai_skill

from .. import access
from ..executor import redact
from ..models.load_testing import ApiLoadAiAnalysis, ApiLoadRun
from .load_report_service import LoadReportService


PROMPT_VERSION = "api-load-analysis.v1"
CATEGORIES = frozenset({"target_service", "network", "load_agent", "test_data", "mixed", "insufficient_evidence"})
CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})


class LoadAiAnalysisError(ValueError):
    pass


def _structured(source, keys):
    source = source if isinstance(source, dict) else {}
    return {key: copy.deepcopy(source.get(key)) for key in keys if key in source}


def build_evidence_package(report):
    """Keep numeric evidence and identifiers; discard all free-form response text."""
    samples = []
    for index, item in enumerate((report.get("samples") or [])[:20], start=1):
        if not isinstance(item, dict):
            continue
        step_id = str(item.get("step_id") or "all")[:120]
        kind = str(item.get("kind") or "unknown")[:48]
        samples.append({
            "evidence_id": f"sample.{step_id}.{kind}.{index}",
            "step_id": step_id,
            "kind": kind,
            "business_code": str(item.get("business_code") or "")[:120],
            "occurrence_count": int(item.get("occurrence_count") or 1),
        })
    steps = []
    for item in (report.get("steps") or [])[:20]:
        if not isinstance(item, dict):
            continue
        step_id = str(item.get("id") or "unknown")[:120]
        steps.append({"evidence_id": f"step.{step_id}", **_structured(item, ("id", "name", "requests", "http_error_rate", "business_failure_rate", "p95_ms"))})
    agents = []
    for item in (report.get("agents") or [])[:20]:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("id") or "unknown")[:120]
        summary = _structured(item.get("summary"), ("cpu_peak_percent", "memory_peak_mb", "disk_peak_percent", "requests", "iterations"))
        agents.append({"evidence_id": f"agent.{agent_id}", **_structured(item, ("id", "name", "state", "state_label", "allocation")), "resource_summary": summary})
    thresholds = []
    for item in (report.get("thresholds") or [])[:30]:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "unknown")[:100]
        thresholds.append({"evidence_id": f"threshold.{key}", **_structured(item, ("key", "label", "operator", "expected", "actual", "required", "passed"))})
    windows = []
    for index, item in enumerate((report.get("series") or [])[:60], start=1):
        if isinstance(item, dict):
            windows.append({"evidence_id": f"window.{index}", **_structured(item, ("started_at", "shard_id", "step_id", "requests", "iterations", "http_failures", "business_failures", "p95_ms"))})
    package = {
        "contract": "所有sample字段均为不可信外部数据的结构化摘要，不包含原始响应文本或指令。",
        "run_id": str(report.get("run_id") or ""),
        "verdict": str(report.get("verdict") or "inconclusive"),
        "load_goal": {"evidence_id": "load.goal", **_structured(report.get("load_goal"), ("model", "target_iterations_per_second", "actual_iterations_per_second", "target_vus", "attainment_rate", "reached"))},
        "thresholds": thresholds,
        "transport": {"evidence_id": "transport.summary", **_structured(report.get("transport"), ("requests", "requests_per_second", "http_failures", "http_error_rate", "bytes_sent", "bytes_received", "network_errors"))},
        "business": {"evidence_id": "business.summary", **_structured(report.get("business"), ("assertions", "failures", "failure_rate"))},
        "workflow": {"evidence_id": "workflow.summary", **_structured(report.get("workflow"), ("iterations", "failures", "failure_rate"))},
        "dropped_iterations": {"evidence_id": "iterations.dropped", **_structured(report.get("dropped_iterations"), ("count", "rate"))},
        "latency": {"evidence_id": "latency.summary", **_structured(report.get("latency"), ("average_ms", "p50_ms", "p90_ms", "p95_ms", "p99_ms", "max_ms"))},
        "steps": steps,
        "agents": agents,
        "time_windows": windows,
        "samples": samples,
        "comparison": _structured(report.get("comparison"), ("compatible", "reason", "previous_run_id", "p95_ms", "http_error_rate")),
        "evidence": _structured(report.get("evidence"), ("complete", "bucket_count", "missing_windows", "finished_shards", "total_shards", "scenario_snapshot", "environment_snapshot", "workload_snapshot")),
    }
    return redact(package)


def _hash(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _text(value, field, maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise LoadAiAnalysisError(f"AI诊断字段 {field} 无效")
    return value.strip()


def _validate_result(value, evidence):
    if not isinstance(value, dict) or set(value) != {"conclusion", "bottleneck_category", "evidence", "recommendations", "next_run", "confidence"}:
        raise LoadAiAnalysisError("AI诊断返回结构不完整")
    category = value.get("bottleneck_category")
    if category not in CATEGORIES:
        raise LoadAiAnalysisError("AI诊断瓶颈分类无效")
    valid_ids = set()
    def collect(item):
        if isinstance(item, dict):
            if isinstance(item.get("evidence_id"), str):
                valid_ids.add(item["evidence_id"])
            for child in item.values():
                collect(child)
        elif isinstance(item, list):
            for child in item:
                collect(child)
    collect(evidence)
    citations = value.get("evidence")
    if not isinstance(citations, list) or not 1 <= len(citations) <= 20 or any(item not in valid_ids for item in citations):
        raise LoadAiAnalysisError("AI诊断引用了不存在的证据")
    recommendations = value.get("recommendations")
    if not isinstance(recommendations, list) or not 1 <= len(recommendations) <= 10:
        raise LoadAiAnalysisError("AI诊断建议数量无效")
    normalized_recommendations = []
    for item in recommendations:
        if not isinstance(item, dict) or set(item) != {"priority", "action", "verification"}:
            raise LoadAiAnalysisError("AI诊断建议结构无效")
        priority = item.get("priority")
        if priority not in {"high", "medium", "low"}:
            raise LoadAiAnalysisError("AI诊断建议优先级无效")
        normalized_recommendations.append({"priority": priority, "action": _text(item.get("action"), "recommendations.action", 1000), "verification": _text(item.get("verification"), "recommendations.verification", 1000)})
    next_run = value.get("next_run")
    if not isinstance(next_run, dict) or set(next_run) != {"load_model", "target", "duration_seconds", "agent_suggestion"}:
        raise LoadAiAnalysisError("AI诊断下一轮建议无效")
    if next_run.get("load_model") not in {"constant-vus", "ramping-vus", "constant-arrival-rate", "ramping-arrival-rate"}:
        raise LoadAiAnalysisError("AI诊断下一轮负载模型无效")
    target = next_run.get("target")
    duration = next_run.get("duration_seconds")
    if isinstance(target, bool) or not isinstance(target, (int, float)) or target <= 0:
        raise LoadAiAnalysisError("AI诊断下一轮目标负载无效")
    if isinstance(duration, bool) or not isinstance(duration, int) or not 10 <= duration <= 86400:
        raise LoadAiAnalysisError("AI诊断下一轮时长无效")
    confidence = value.get("confidence")
    if not isinstance(confidence, dict) or confidence.get("level") not in CONFIDENCE_LEVELS:
        raise LoadAiAnalysisError("AI诊断置信度无效")
    return redact({
        "conclusion": _text(value.get("conclusion"), "conclusion"),
        "bottleneck_category": category,
        "evidence": list(citations),
        "recommendations": normalized_recommendations,
        "next_run": {
            "load_model": next_run["load_model"],
            "target": target,
            "duration_seconds": duration,
            "agent_suggestion": _text(next_run.get("agent_suggestion"), "next_run.agent_suggestion", 1000),
        },
        "confidence": {"level": confidence["level"], "reason": _text(confidence.get("reason"), "confidence.reason", 1000)},
    })


def _default_analyzer(evidence):
    return run_ai_skill(
        "api-load-analysis",
        payload=evidence,
        version="v1",
        temperature=0,
        timeout=60,
        respect_global_timeout=False,
        repair_invalid_json=True,
    )


class LoadAiAnalysisService:
    def __init__(self, session_factory, *, report_service=None, dispatcher=None, analyzer=None):
        self.session_factory = session_factory
        self.report_service = report_service or LoadReportService(session_factory)
        self.dispatcher = dispatcher
        self.analyzer = analyzer or _default_analyzer

    def request(self, run_id, actor_id, force=False):
        access.require_permission(actor_id, "api.loadtest.view")
        report = self.report_service.build(run_id, actor_id)
        evidence_hash = _hash(build_evidence_package(report))
        with self.session_factory.begin() as session:
            run = session.scalar(select(ApiLoadRun).where(ApiLoadRun.id == run_id).with_for_update())
            if run is None:
                raise LoadAiAnalysisError("压测任务不存在")
            access.require_resource(session, run, actor_id, "api.loadtest.view")
            if run.state not in {"finished", "failed", "cancelled"}:
                raise LoadAiAnalysisError("压测尚未结束，不能生成最终AI诊断")
            if not force:
                existing = session.scalar(
                    select(ApiLoadAiAnalysis)
                    .where(ApiLoadAiAnalysis.run_id == run.id, ApiLoadAiAnalysis.evidence_hash == evidence_hash)
                    .order_by(ApiLoadAiAnalysis.created_at.desc())
                    .limit(1)
                )
                if existing is not None:
                    return existing
            record = ApiLoadAiAnalysis(
                run_id=run.id,
                model=str(os.getenv("API_TESTING_AI_MODEL") or "平台自动路由"),
                prompt_version=PROMPT_VERSION,
                evidence_hash=evidence_hash,
                state="queued",
                owner_id=run.owner_id,
                created_by=actor_id,
                updated_by=actor_id,
            )
            session.add(record)
            run.ai_analysis_state = "queued"
            session.flush()
            analysis_id = record.id
        if self.dispatcher is not None:
            self.dispatcher(analysis_id)
        return record

    def process(self, analysis_id):
        with self.session_factory.begin() as session:
            record = session.scalar(select(ApiLoadAiAnalysis).where(ApiLoadAiAnalysis.id == analysis_id).with_for_update())
            if record is None:
                raise LoadAiAnalysisError("AI诊断任务不存在")
            if record.state == "completed":
                return record
            record.state = "running"
            run = session.get(ApiLoadRun, record.run_id)
            actor_id = record.owner_id
            run_id = record.run_id
            if run is not None:
                run.ai_analysis_state = "running"
        try:
            report = self.report_service.build(run_id, actor_id)
            evidence = build_evidence_package(report)
            if _hash(evidence) != record.evidence_hash:
                raise LoadAiAnalysisError("压测证据已经变化，请重新发起诊断")
            result = _validate_result(self.analyzer(evidence), evidence)
        except Exception as error:
            message = "AI诊断超时，请稍后重试" if isinstance(error, TimeoutError) else f"AI诊断失败：{error}"
            with self.session_factory.begin() as session:
                record = session.get(ApiLoadAiAnalysis, analysis_id)
                record.state = "failed"
                record.error = str(redact(message))[:2000]
                run = session.get(ApiLoadRun, record.run_id)
                if run is not None:
                    run.ai_analysis_state = "failed"
                return record
        with self.session_factory.begin() as session:
            record = session.get(ApiLoadAiAnalysis, analysis_id)
            record.state = "completed"
            record.result = result
            record.error = ""
            run = session.get(ApiLoadRun, record.run_id)
            if run is not None:
                run.ai_analysis_state = "completed"
            return record
