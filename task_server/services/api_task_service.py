"""Task-oriented API testing view.

This service derives a single API test task from the existing source, asset,
plan, execution, and report services. It does not own persistence or execution
semantics; it gives the product UI one workflow object to render.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _environment_snapshot(source: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = source.get("environment_snapshot")
    return snapshot if isinstance(snapshot, dict) else {}


def _first_base_url(source: Dict[str, Any]) -> Dict[str, Any]:
    snapshot = _environment_snapshot(source)
    base_urls = snapshot.get("base_urls") if isinstance(snapshot.get("base_urls"), list) else []
    for item in base_urls:
        if isinstance(item, dict) and str(item.get("url") or "").strip():
            return item
    return {}


def _environment_name(source: Dict[str, Any]) -> str:
    metadata = source.get("provider_metadata") if isinstance(source.get("provider_metadata"), dict) else {}
    return (
        str(metadata.get("environment_name") or "").strip()
        or str(source.get("environment_name") or "").strip()
        or str(source.get("environment_id") or "").strip()
        or "未选择环境"
    )


def _project_name(source: Dict[str, Any]) -> str:
    metadata = source.get("provider_metadata") if isinstance(source.get("provider_metadata"), dict) else {}
    return (
        str(metadata.get("project_name") or "").strip()
        or str(source.get("project_name") or "").strip()
        or str(source.get("name") or "").strip()
        or str(source.get("project_id") or "").strip()
        or "API 项目"
    )


def _latest_run(execution: Dict[str, Any]) -> Dict[str, Any]:
    active_rows = execution.get("active_runs") if isinstance(execution.get("active_runs"), list) else []
    active = active_rows[0] if active_rows else {}
    if isinstance(active, dict) and active.get("execution_id"):
        return active
    recent_rows = execution.get("recent_runs") if isinstance(execution.get("recent_runs"), list) else []
    recent = recent_rows[0] if recent_rows else {}
    return recent if isinstance(recent, dict) else {}


def _task_status(
    snapshot: Dict[str, Any],
    plans: Dict[str, Any],
    execution: Dict[str, Any],
    reports: List[Dict[str, Any]],
) -> str:
    active_rows = execution.get("active_runs") if isinstance(execution.get("active_runs"), list) else []
    active = active_rows[0] if active_rows else {}
    if isinstance(active, dict) and active.get("execution_id"):
        return "running"
    latest_report = reports[0] if reports else {}
    if _safe_int(latest_report.get("failed"), 0) > 0:
        return "failed"
    if (plans.get("latest_baseline") or {}).get("plan_id"):
        return "ready"
    if (plans.get("latest_draft") or {}).get("plan_id"):
        return "draft"
    if str(snapshot.get("state") or "") == "ready":
        return "selecting"
    return "update_needed"


def _step(id_: str, title: str, state: str, action: str, summary: str) -> Dict[str, str]:
    return {
        "id": id_,
        "title": title,
        "state": state,
        "action": action,
        "summary": summary,
    }


def build_api_test_task(
    *,
    source: Dict[str, Any],
    snapshot: Dict[str, Any],
    plans: Dict[str, Any],
    execution: Dict[str, Any],
    reports: List[Dict[str, Any]],
    sync_state: Dict[str, Any],
    metrics: Dict[str, Any],
    pending_changes: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Build the task object displayed on the API workbench."""
    source = source or {}
    snapshot = snapshot or {}
    plans = plans or {}
    execution = execution or {}
    reports = reports or []
    sync_state = sync_state or {}
    metrics = metrics or {}
    pending_changes = pending_changes or []
    latest_draft = plans.get("latest_draft") or {}
    latest_baseline = plans.get("latest_baseline") or {}
    latest_run = _latest_run(execution)
    latest_report = reports[0] if reports else {}
    base_url = _first_base_url(source)
    interface_count = _safe_int(snapshot.get("endpoint_count"), 0)
    draft_count = _safe_int(latest_draft.get("case_count"), 0)
    baseline_count = _safe_int(latest_baseline.get("case_count"), 0)
    executable_count = _safe_int(latest_baseline.get("executable_case_count"), 0)
    running = bool(latest_run.get("execution_id") and str(latest_run.get("status") or "").lower() in {"queued", "running"})
    return {
        "kind": "API测试任务",
        "task_id": f"api_task_{source.get('source_id') or source.get('project_id') or 'default'}",
        "name": f"{_project_name(source)} 接口测试任务",
        "description": "选择接口 → AI分析 → 调试参数 → 执行 → 报告",
        "status": _task_status(snapshot, plans, execution, reports),
        "project": {
            "id": str(source.get("project_id") or ""),
            "name": _project_name(source),
            "source_id": str(source.get("source_id") or ""),
        },
        "environment": {
            "id": str(source.get("environment_id") or ""),
            "name": _environment_name(source),
            "base_url": str(base_url.get("url") or ""),
            "base_url_name": str(base_url.get("name") or "default"),
        },
        "summary": {
            "interface_count": interface_count,
            "selected_interface_count": _safe_int(latest_baseline.get("endpoint_count") or latest_draft.get("endpoint_count"), 0),
            "draft_case_count": draft_count,
            "baseline_case_count": baseline_count,
            "executable_case_count": executable_count,
            "pending_change_count": _safe_int(metrics.get("pending_changes"), 0) or len(pending_changes),
            "latest_run_status": str(latest_run.get("status") or ""),
            "latest_report_status": str(latest_report.get("status") or ""),
        },
        "sync": {
            "status": str(sync_state.get("status") or ""),
            "last_sync_at": str(sync_state.get("last_sync_at") or ""),
            "pending_changes": _safe_int(sync_state.get("pending_changes"), 0),
        },
        "steps": [
            _step(
                "select",
                "选择接口",
                "done" if interface_count else "todo",
                "showApiAssetsPage()",
                f"{interface_count} 个接口可选" if interface_count else "先手动更新 Apifox 接口",
            ),
            _step(
                "ai_design",
                "AI分析与测试设计",
                "done" if (draft_count or baseline_count) else "todo",
                "showApiPlanPage()",
                f"{draft_count or baseline_count} 条测试用例" if (draft_count or baseline_count) else "从接口详情或模块发起 AI 生成",
            ),
            _step(
                "debug",
                "调试参数",
                "done" if executable_count else ("running" if draft_count else "todo"),
                "showApiDebugPage()",
                f"{executable_count} 条可执行" if executable_count else "先补齐环境变量、Token 和测试数据",
            ),
            _step(
                "execute",
                "自动回归",
                "running" if running else ("done" if latest_run.get("execution_id") else ("todo" if not executable_count else "ready")),
                "showApiRegressionPage()",
                "正在执行" if running else ("查看最近执行" if latest_run.get("execution_id") else "发版后一键执行基线接口测试"),
            ),
            _step(
                "report",
                "查看报告",
                "done" if latest_report.get("report_id") else "todo",
                "showApiReportsPage()",
                "查看失败原因和 AI 分析" if latest_report.get("report_id") else "执行完成后自动生成报告",
            ),
        ],
    }
