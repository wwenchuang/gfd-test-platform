"""Execution planning policy for Agent-generated Midscene YAML.

This module is intentionally local-rule only.  AI may generate or repair YAML,
but dispatch decisions must be deterministic and visible: what can run first,
what is deferred, what is blocked, and whether smoke failures prove the YAML is
not executable.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


BLOCKING_BUCKETS = {"YAML 可执行性不足", "元素定位失败", "Runner 超时", "Runner 未下发", "模型/环境失败"}
RESULT_FAILURE_BUCKETS = {"页面状态不匹配", "产品断言失败"}
SMOKE_MIN_PASS_RATE = 0.5


def _safe_list(value: Any) -> List[Any]:
    return value if isinstance(value, list) else []


def _score_of(item: Dict[str, Any]) -> Dict[str, Any]:
    score = item.get("executableScore")
    return score if isinstance(score, dict) else {}


def _ref_label(item: Dict[str, Any]) -> str:
    score = _score_of(item)
    task_scores = _safe_list(score.get("taskScores"))
    task_name = ""
    if task_scores and isinstance(task_scores[0], dict):
        task_name = str(task_scores[0].get("name") or "")
    return str(item.get("name") or item.get("title") or task_name or item.get("file") or "未命名用例")


def _compact_ref(item: Dict[str, Any]) -> Dict[str, Any]:
    score = _score_of(item)
    reasons = score.get("reasons") if isinstance(score.get("reasons"), list) else []
    task_scores = [row for row in _safe_list(score.get("taskScores")) if isinstance(row, dict)]
    return {
        "name": _ref_label(item),
        "module": item.get("module") or "",
        "file": item.get("file") or "",
        "path": item.get("path") or "",
        "score": score.get("score") or item.get("score") or 0,
        "executionLevel": score.get("executionLevel") or item.get("executionLevel") or item.get("level") or "",
        "smokeCandidate": bool(item.get("smokeCandidate") or item.get("runnerCandidate") or score.get("smokeCandidate")),
        "gateReason": item.get("gateReason") or "",
        "reasons": reasons[:5],
        "priority": next((str(row.get("priority") or "") for row in task_scores if row.get("priority")), ""),
        "mainBusinessChain": any(bool(row.get("mainBusinessChain")) for row in task_scores),
        "baselineEvidence": bool(score.get("baselineEvidence") or any(row.get("baselineEvidence") for row in task_scores)),
    }


def _count_levels(scored_refs: Iterable[Dict[str, Any]]) -> Dict[str, int]:
    counts = {"executable": 0, "needs_review": 0, "draft": 0, "manual": 0}
    for item in scored_refs or []:
        level = str(_score_of(item).get("executionLevel") or item.get("executionLevel") or item.get("level") or "draft")
        if level not in counts:
            level = "draft"
        counts[level] += 1
    return counts


def build_generated_yaml_execution_plan(
    scored_refs: List[Dict[str, Any]],
    selected_refs: List[Dict[str, Any]],
    deferred_refs: List[Dict[str, Any]],
    blocking_refs: List[Dict[str, Any]],
    *,
    smoke_limit: int,
    first_smoke_upper: int,
    expand_limit: int,
    expand_batch_limit: int,
    repairs: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Build the visible execution plan for generated YAML.

    The plan is the contract between generation, validation, Runner dispatch,
    and the frontend.  It avoids scattering policy decisions across unrelated
    call sites.
    """
    scored_refs = [item for item in scored_refs or [] if isinstance(item, dict)]
    selected_refs = [item for item in selected_refs or [] if isinstance(item, dict)]
    deferred_refs = [item for item in deferred_refs or [] if isinstance(item, dict)]
    blocking_refs = [item for item in blocking_refs or [] if isinstance(item, dict)]
    level_counts = _count_levels(scored_refs)
    blocking_reasons = []
    seen = set()
    for item in blocking_refs:
        reason = str(item.get("gateReason") or "")
        if not reason:
            score = _score_of(item)
            reasons = score.get("reasons") if isinstance(score.get("reasons"), list) else []
            reason = str(reasons[0]) if reasons else "未达到自动执行准入"
        if reason and reason not in seen:
            seen.add(reason)
            blocking_reasons.append(reason)

    can_dispatch = bool(selected_refs) and not blocking_refs
    if selected_refs:
        can_dispatch = True

    return {
        "version": "generated-yaml-execution-plan-v1",
        "policy": {
            "modelCalls": "AI 参与需求规划、可信基线语义重排、YAML 路径规划、冒烟优先级和失败定向修复；平台以确定性规则执行静态校验、设备约束、dry-run 和继续阈值。",
            "smokePurpose": "首批冒烟用于证明 YAML 能下发、能运行、能产生日志；产品断言失败记录为测试结果，不等同于不可执行。",
            "expansion": "首批没有脚本/YAML/定位/超时类阻断且真实通过率不低于 50% 时，按批继续执行剩余可执行用例。",
        },
        "counts": {
            "total": len(scored_refs),
            "selectedSmoke": len(selected_refs),
            "deferredExecutable": len(deferred_refs),
            "blocking": len(blocking_refs),
            "autoRepair": len(repairs or []),
            **level_counts,
        },
        "limits": {
            "smokeLimit": int(smoke_limit or 0),
            "firstSmokeUpper": int(first_smoke_upper or 0),
            "expandLimit": int(expand_limit or 0),
            "expandBatchLimit": int(expand_batch_limit or 0),
        },
        "readiness": {
            "canDispatch": can_dispatch,
            "canExpandAfterSmoke": bool(deferred_refs),
            "requiresRepair": bool(blocking_refs),
            "blockingReasons": blocking_reasons[:8],
        },
        "phases": [
            {
                "name": "preflight",
                "label": "执行前准入",
                "status": "blocked" if blocking_refs and not selected_refs else "ready",
                "summary": "本地评分 + dry-run + Runner 能力检查，拦截明显不可执行 YAML。",
            },
            {
                "name": "smoke",
                "label": "首批冒烟执行",
                "status": "ready" if selected_refs else "blocked",
                "count": len(selected_refs),
                "summary": "只下发首批可执行候选，先验证脚本能真实跑起来。",
            },
            {
                "name": "remaining",
                "label": "剩余可执行用例",
                "status": "pending" if deferred_refs else "empty",
                "count": len(deferred_refs),
                "summary": "首批真实通过率不低于 50% 且没有执行阻断后分批继续，支持人工修改后继续执行。",
            },
        ],
        "selected": [_compact_ref(item) for item in selected_refs[:20]],
        "deferred": [_compact_ref(item) for item in deferred_refs[:30]],
        "blocking": [_compact_ref(item) for item in blocking_refs[:30]],
        "repairs": repairs or [],
    }


def _failure_text(failure_reasons: Any, dry_run_blocked: Any) -> str:
    parts: List[str] = []
    for item in _safe_list(failure_reasons):
        if isinstance(item, dict):
            parts.append(str(item.get("failureType") or "") + " " + str(item.get("reason") or ""))
    for item in _safe_list(dry_run_blocked):
        if isinstance(item, dict):
            parts.append(str(item.get("reason") or "") + " " + " ".join(str(err) for err in _safe_list(item.get("errors"))))
    return "\n".join(parts)


def classify_generated_yaml_failure_bucket(failure_reasons: Any, dry_run_blocked: Any = None) -> str:
    text = _failure_text(failure_reasons, dry_run_blocked)
    lowered = text.lower()
    if "env_issue" in lowered or any(term in lowered for term in (
        "model service", "model request", "模型服务", "device offline", "设备离线",
        "econnreset", "etimedout", "gateway timeout",
    )):
        return "模型/环境失败"
    if "执行等级" in text or "executable" in lowered or "dry-run" in lowered or "yaml" in lowered:
        return "YAML 可执行性不足"
    if "failed to locate" in lowered or "元素定位" in text or "找不到" in text or "未找到" in text or "locate element" in lowered:
        return "元素定位失败"
    if "assertion failed" in lowered or "断言" in text:
        return "产品断言失败"
    if "页面状态" in text or ("页面" in text and ("不匹配" in text or "未出现" in text or "超时" in text)) or "waitfor timeout" in lowered:
        return "页面状态不匹配"
    if text.strip():
        return "Runner 失败"
    return "Runner 失败"


def classify_generated_yaml_smoke_blocker(
    failure_reasons: Any,
    dry_run_blocked: Any = None,
    *,
    smoke_total: int = 0,
    smoke_failed: int = 0,
    timeout_count: int = 0,
) -> Dict[str, Any]:
    """Classify whether smoke results should block full execution.

    Smoke outcomes keep their real result bucket, but expansion is a separate
    deterministic decision. Static execution blockers always stop expansion;
    otherwise at least half of the smoke jobs must pass before remaining cases
    can run.
    """
    dry_blocked = [item for item in _safe_list(dry_run_blocked) if isinstance(item, dict)]
    if dry_blocked:
        return {
            "block": True,
            "reason": "YAML dry-run 未通过",
            "bucket": "YAML 可执行性不足",
            "rule": "冒烟必须先通过本地/Runner dry-run，静态不可执行 YAML 不下发。",
        }
    if timeout_count:
        return {
            "block": True,
            "reason": f"首批冒烟有 {timeout_count} 个任务超时",
            "bucket": "Runner 超时",
            "rule": "冒烟必须能在等待窗口内产出明确结果，超时会暂停扩展避免批量卡死。",
        }
    if not smoke_total:
        return {
            "block": True,
            "reason": "首批冒烟没有创建 Runner 任务",
            "bucket": "Runner 未下发",
            "rule": "冒烟必须真实创建 Runner 任务并进入执行链路。",
        }

    bucket = classify_generated_yaml_failure_bucket(failure_reasons, dry_blocked)
    text = _failure_text(failure_reasons, dry_blocked)
    lowered = text.lower()
    hard_failure = (
        bucket in BLOCKING_BUCKETS
        or "failed to locate element" in lowered
        or "locate element" in lowered
        or "未找到用例" in text
        or "找不到" in text
        or "工具调用失败" in text
        or "yaml" in lowered
        or "dry-run" in lowered
    )
    if smoke_failed and hard_failure:
        return {
            "block": True,
            "reason": bucket,
            "bucket": bucket,
            "rule": "冒烟已下发但失败归因为脚本/YAML/元素定位/超时问题，先修复生成脚本或环境再扩展。",
        }
    if smoke_failed >= smoke_total and bucket == "Runner 失败":
        return {
            "block": True,
            "reason": "首批冒烟均为 Runner 失败且未能归因",
            "bucket": bucket,
            "rule": "没有任何冒烟任务产出有效结果时暂停扩展，避免批量制造未知失败。",
        }
    smoke_passed = max(0, smoke_total - smoke_failed)
    smoke_pass_rate = smoke_passed / smoke_total
    if smoke_pass_rate < SMOKE_MIN_PASS_RATE:
        return {
            "block": True,
            "executable": True,
            "thresholdPassed": False,
            "passRate": round(smoke_pass_rate, 4),
            "reason": f"首批冒烟通过率 {smoke_pass_rate:.1%} 低于 50%",
            "bucket": bucket if smoke_failed else "冒烟通过率不足",
            "rule": "保留每条冒烟的真实通过/失败结果；通过率低于 50% 时暂停 remaining，先进入失败诊断和修复。",
        }
    return {
        "block": False,
        "executable": True,
        "thresholdPassed": True,
        "passRate": round(smoke_pass_rate, 4),
        "reason": bucket if smoke_failed else "",
        "bucket": bucket if smoke_failed else "",
        "rule": "冒烟必须能执行且真实通过率不低于 50%；产品断言失败或页面状态不匹配仍保留为真实测试结果。",
    }


def update_execution_plan_after_smoke(
    plan: Dict[str, Any],
    smoke_blocker: Dict[str, Any],
    *,
    smoke_total: int,
    smoke_passed: int,
    smoke_failed: int,
    timeout_count: int,
) -> Dict[str, Any]:
    plan = dict(plan or {})
    readiness = dict(plan.get("readiness") if isinstance(plan.get("readiness"), dict) else {})
    readiness.update({
        "smokeExecutable": bool((smoke_blocker or {}).get("executable", not bool((smoke_blocker or {}).get("block")))),
        "smokePassThresholdMet": bool((smoke_blocker or {}).get("thresholdPassed", not bool((smoke_blocker or {}).get("block")))),
        "smokePassRate": round(smoke_passed / smoke_total, 4) if smoke_total else 0,
        "smokeFailureBucket": (smoke_blocker or {}).get("bucket") or "",
        "smokeFailurePolicy": (smoke_blocker or {}).get("rule") or "",
        "stopFurtherExecution": bool((smoke_blocker or {}).get("block")),
    })
    plan["readiness"] = readiness
    plan["smokeResult"] = {
        "total": smoke_total,
        "passed": smoke_passed,
        "failed": smoke_failed,
        "timeout": timeout_count,
        "passRate": round(smoke_passed / smoke_total, 4) if smoke_total else 0,
        "threshold": SMOKE_MIN_PASS_RATE,
        "thresholdPassed": bool((smoke_blocker or {}).get("thresholdPassed", not bool((smoke_blocker or {}).get("block")))),
        "executable": bool((smoke_blocker or {}).get("executable", not bool((smoke_blocker or {}).get("block")))),
        "block": bool((smoke_blocker or {}).get("block")),
        "reason": (smoke_blocker or {}).get("reason") or "",
        "bucket": (smoke_blocker or {}).get("bucket") or "",
        "rule": (smoke_blocker or {}).get("rule") or "",
    }
    phases = []
    for phase in _safe_list(plan.get("phases")):
        if not isinstance(phase, dict):
            continue
        phase = dict(phase)
        if phase.get("name") == "smoke":
            phase["status"] = "blocked" if (smoke_blocker or {}).get("block") else "complete"
        elif phase.get("name") == "remaining" and (smoke_blocker or {}).get("block"):
            phase["status"] = "blocked"
        phases.append(phase)
    if phases:
        plan["phases"] = phases
    return plan
