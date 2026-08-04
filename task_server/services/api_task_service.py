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
    environment_variable_count = _safe_int(_environment_snapshot(source).get("variable_count"), 0)
    has_saved_environment = bool(base_url.get("url")) or environment_variable_count > 0
    running = bool(latest_run.get("execution_id") and str(latest_run.get("status") or "").lower() in {"queued", "running"})
    return {
        "kind": "API测试任务",
        "task_id": f"api_task_{source.get('source_id') or source.get('project_id') or 'default'}",
        "name": f"{_project_name(source)} 接口测试任务",
        "description": "手动获取 Apifox 接口数据和环境 → 保存接口数据和环境 → 筛选要测的模块和接口 → AI 根据选择的接口生成测试用例 → 执行，实时查看日志和报告",
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
                "apifox_update",
                "手动获取 Apifox 接口数据和环境",
                "done" if interface_count else "todo",
                "showApiAssetsPage()",
                f"{interface_count} 个接口已读取" if interface_count else "先手动更新 Apifox 接口和环境",
            ),
            _step(
                "save_snapshot",
                "保存接口数据和环境",
                "done" if (interface_count and has_saved_environment) else ("running" if interface_count else "todo"),
                "showApiEnvironmentPage()",
                "接口和环境已保存为本地快照" if (interface_count and has_saved_environment) else "检查 Base URL、变量和业务 token",
            ),
            _step(
                "filter_scope",
                "筛选要测的模块和接口",
                "done" if interface_count else "todo",
                "showApiAssetsPage()",
                f"{interface_count} 个接口可按模块筛选" if interface_count else "先保存本地接口快照",
            ),
            _step(
                "ai_generate",
                "AI 根据选择的接口生成测试用例",
                "done" if (draft_count or baseline_count) else "todo",
                "showApiPlanPage()",
                f"{draft_count or baseline_count} 条测试用例" if (draft_count or baseline_count) else "从模块任务卡发起 AI 生成",
            ),
            _step(
                "execute",
                "执行，实时查看日志和报告",
                "running" if running else ("done" if (latest_run.get("execution_id") or latest_report.get("report_id")) else ("todo" if not (draft_count or baseline_count or executable_count) else "ready")),
                "showApiRegressionPage()",
                "正在执行" if running else ("查看最近执行和报告" if (latest_run.get("execution_id") or latest_report.get("report_id")) else "批量调试草稿或执行已保存测试资产"),
            ),
        ],
    }
