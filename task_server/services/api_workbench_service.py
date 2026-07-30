"""Simplified API testing workbench facade.

This module intentionally composes existing Apifox asset, API plan, native
runner, auth binding and report services. It does not own execution semantics;
it gives the frontend one stable payload for the day-to-day API workflow.
"""

from __future__ import annotations

from typing import Any, Dict, List

from task_server.services import (
    api_asset_service,
    api_case_contract_service,
    api_execution_service,
    api_module_service,
    api_report_service,
    api_source_service,
    api_sync_service,
    api_test_plan_service,
    api_workspace_service,
)


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or default)
    except (TypeError, ValueError):
        return default


def _source_label(source: Dict[str, Any]) -> str:
    metadata = source.get("provider_metadata") if isinstance(source.get("provider_metadata"), dict) else {}
    return (
        str(metadata.get("project_name") or "").strip()
        or str(source.get("name") or "").strip()
        or str(source.get("project_id") or "").strip()
        or str(source.get("source_id") or "").strip()
    )


def _selected_source(source_id: str = "") -> Dict[str, Any]:
    target = str(source_id or "").strip()
    if target:
        return api_source_service.get_api_source(target, masked=True)
    sources = api_source_service.list_api_sources()
    return sources[0] if sources else {}


def _source_summary(source: Dict[str, Any]) -> Dict[str, Any]:
    if not source:
        return {}
    metadata = source.get("provider_metadata") if isinstance(source.get("provider_metadata"), dict) else {}
    environment_snapshot = source.get("environment_snapshot") if isinstance(source.get("environment_snapshot"), dict) else {}
    sync_schedule = source.get("sync_schedule") if isinstance(source.get("sync_schedule"), dict) else {}
    return {
        "source_id": str(source.get("source_id") or ""),
        "source_type": str(source.get("source_type") or ""),
        "name": _source_label(source),
        "project_id": str(source.get("project_id") or ""),
        "project_name": str(metadata.get("project_name") or _source_label(source)),
        "branch_id": str(source.get("branch_id") or ""),
        "branch_name": str(metadata.get("branch_name") or ""),
        "environment_id": str(source.get("environment_id") or ""),
        "environment_name": str(metadata.get("environment_name") or ""),
        "credential_configured": bool(source.get("credential_configured")),
        "configured": bool(source.get("configured")),
        "sync_enabled": bool(source.get("sync_enabled")),
        "last_success_at": str(source.get("last_success_at") or sync_schedule.get("last_success_at") or ""),
        "last_sync_status": str(source.get("last_sync_status") or sync_schedule.get("status") or ""),
        "last_error": str(source.get("last_error") or ""),
        "sync_schedule": sync_schedule,
        "environment_snapshot": {
            "base_urls": environment_snapshot.get("base_urls") or [],
            "variables": environment_snapshot.get("variables") or [],
            "variable_count": _safe_int(environment_snapshot.get("variable_count"), 0),
            "sensitive_variable_count": _safe_int(environment_snapshot.get("sensitive_variable_count"), 0),
        },
    }


def _asset_for_source(source_id: str) -> Dict[str, Any]:
    for item in api_asset_service.list_api_assets(limit=100):
        if str(item.get("source_id") or "").strip() == str(source_id or "").strip():
            return api_asset_service.get_api_asset(str(item.get("asset_id") or ""))
    return {}


def _active_revision_for_source(source_id: str) -> tuple[Dict[str, Any], Dict[str, Any]]:
    asset = _asset_for_source(source_id)
    revision_id = str(asset.get("active_revision_id") or "").strip()
    revision = api_asset_service.get_api_revision(revision_id) if revision_id else {}
    if revision:
        return asset, revision
    return asset, {}


def _endpoint_summary(endpoint: Dict[str, Any]) -> Dict[str, Any]:
    method = str(endpoint.get("method") or "").upper()
    path = str(endpoint.get("path") or "")
    return {
        "endpoint_id": str(endpoint.get("endpoint_id") or ""),
        "endpoint_key": str(endpoint.get("endpoint_key") or ""),
        "source_ref": str(endpoint.get("source_ref") or ""),
        "method": method,
        "path": path,
        "endpoint": f"{method} {path}".strip(),
        "name": str(endpoint.get("name") or endpoint.get("summary") or path),
        "summary": str(endpoint.get("summary") or endpoint.get("name") or ""),
        "module": str(endpoint.get("module") or endpoint.get("module_path") or "未分组"),
        "module_path": str(endpoint.get("module_path") or endpoint.get("module") or ""),
        "required_fields": [
            str(item)
            for item in (endpoint.get("required_fields") or [])
            if str(item or "").strip()
        ],
        "requires_auth": api_case_contract_service.endpoint_requires_auth(endpoint),
        "schema_hash": str(endpoint.get("schema_hash") or ""),
        "deprecated": bool(endpoint.get("deprecated")),
    }


def _snapshot_summary(revision: Dict[str, Any], asset: Dict[str, Any]) -> Dict[str, Any]:
    if not revision:
        return {
            "state": "missing",
            "message": "还没有本地 API 资产快照，请先从 Apifox 同步一次。",
            "endpoint_count": 0,
        }
    endpoints = revision.get("endpoints") if isinstance(revision.get("endpoints"), list) else []
    return {
        "state": "ready",
        "asset_id": str(asset.get("asset_id") or revision.get("asset_id") or ""),
        "revision_id": str(revision.get("revision_id") or revision.get("snapshot_id") or ""),
        "snapshot_id": str(revision.get("snapshot_id") or revision.get("revision_id") or ""),
        "name": str(revision.get("name") or asset.get("name") or ""),
        "title": str(revision.get("title") or revision.get("name") or ""),
        "version": str(revision.get("version") or ""),
        "openapi_version": str(revision.get("openapi_version") or ""),
        "endpoint_count": len(endpoints),
        "created_at": str(revision.get("created_at") or ""),
        "last_sync_at": str(asset.get("last_sync_at") or ""),
        "source_revision": str(revision.get("source_revision") or ""),
    }


def _plan_summary(plan: Dict[str, Any]) -> Dict[str, Any]:
    readiness = plan.get("execution_readiness") if isinstance(plan.get("execution_readiness"), dict) else {}
    latest_run = plan.get("latest_run") if isinstance(plan.get("latest_run"), dict) else {}
    active_run = plan.get("active_run") if isinstance(plan.get("active_run"), dict) else {}
    return {
        "plan_id": str(plan.get("plan_id") or ""),
        "name": str(plan.get("name") or "API 接口测试计划"),
        "status": str(plan.get("status") or "draft"),
        "source": str(plan.get("source") or ""),
        "created_at": str(plan.get("created_at") or ""),
        "confirmed_at": str(plan.get("confirmed_at") or ""),
        "endpoint_count": _safe_int(plan.get("endpoint_count"), 0),
        "case_count": _safe_int(plan.get("case_count"), 0),
        "executable_case_count": _safe_int(plan.get("executable_case_count"), 0),
        "needs_review_case_count": _safe_int(plan.get("needs_review_case_count"), 0),
        "execution_readiness": readiness,
        "revision_state": plan.get("revision_state") if isinstance(plan.get("revision_state"), dict) else {},
        "module_paths": [
            str(item)
            for item in (plan.get("module_paths") or [])
            if str(item or "").strip()
        ],
        "latest_run": latest_run,
        "active_run": active_run,
        "can_execute": bool(readiness.get("can_execute")) and not active_run,
    }


def _plans_for_source(source_id: str) -> Dict[str, Any]:
    plans = api_test_plan_service.list_api_test_plans(limit=100, source_id=source_id)
    draft_plans = [
        _plan_summary(plan)
        for plan in plans
        if str(plan.get("status") or "") != "confirmed"
    ]
    baselines = [
        _plan_summary(plan)
        for plan in plans
        if str(plan.get("status") or "") == "confirmed"
    ]
    return {
        "drafts": draft_plans[:20],
        "baselines": baselines[:20],
        "latest_draft": draft_plans[0] if draft_plans else {},
        "latest_baseline": baselines[0] if baselines else {},
        "draft_count": len(draft_plans),
        "baseline_count": len(baselines),
    }


def _execution_summary(source_id: str) -> Dict[str, Any]:
    context = api_execution_service.api_execution_context(source_id=source_id) if source_id else {
        "readiness": {"state": "no_source", "can_execute": False, "missing": ["api_source"]},
        "active_runs": [],
        "recent_runs": [],
        "connection": {"state": "disconnected"},
    }
    return {
        "readiness": context.get("readiness") or {},
        "connection": context.get("connection") or {},
        "binding": context.get("binding") or {},
        "auth_binding": context.get("auth_binding") or {},
        "businesses": context.get("businesses") or [],
        "environments": context.get("environments") or [],
        "selection": context.get("selection") or {},
        "active_runs": context.get("active_runs") or [],
        "recent_runs": context.get("recent_runs") or [],
        "empty_reason": context.get("empty_reason") or "",
        "poll_after_ms": context.get("poll_after_ms") or 2000,
    }


def api_testing_workbench(source_id: str = "") -> Dict[str, Any]:
    """Return the complete simplified API workbench state for one source."""
    sources = [_source_summary(source) for source in api_source_service.list_api_sources()]
    source = _selected_source(source_id)
    selected_source_id = str(source.get("source_id") or "").strip()
    asset, revision = _active_revision_for_source(selected_source_id) if selected_source_id else ({}, {})
    endpoints = [
        _endpoint_summary(endpoint)
        for endpoint in (revision.get("endpoints") or [])
        if isinstance(endpoint, dict)
    ]
    reports = api_report_service.list_api_reports(limit=10, source_id=selected_source_id)
    syncs = api_sync_service.list_api_syncs(limit=10, source_id=selected_source_id) if selected_source_id else []
    return {
        "ok": True,
        "mode": "native_api_workbench",
        "source": _source_summary(source),
        "sources": sources,
        "snapshot": _snapshot_summary(revision, asset),
        "scope": {
            "endpoint_count": len(endpoints),
            "endpoints": endpoints[:300],
            "modules": api_module_service.module_summary(revision.get("endpoints") or []),
            "business_lines": api_module_service.business_line_summary(revision.get("endpoints") or []),
        },
        "cases": _plans_for_source(selected_source_id) if selected_source_id else {
            "drafts": [],
            "baselines": [],
            "latest_draft": {},
            "latest_baseline": {},
            "draft_count": 0,
            "baseline_count": 0,
        },
        "execution": _execution_summary(selected_source_id),
        "reports": reports,
        "syncs": syncs,
    }


def update_apifox_snapshot(source_id: str) -> Dict[str, Any]:
    """Start a manual Apifox asset refresh and return the refreshed workbench shell."""
    selected_source_id = str(source_id or "").strip()
    if not selected_source_id:
        source = _selected_source("")
        selected_source_id = str(source.get("source_id") or "").strip()
    if not selected_source_id or not api_source_service.get_api_source(selected_source_id, masked=True):
        raise ValueError("API source 不存在，请先连接 Apifox 项目")
    sync = api_sync_service.start_api_source_sync(
        selected_source_id,
        spawn=True,
        trigger="workbench_snapshot_update",
    )
    return {
        "ok": True,
        "sync": sync,
        "workbench": api_testing_workbench(selected_source_id),
    }


def debug_api_case(source_id: str, plan_id: str, case_id: str) -> Dict[str, Any]:
    """Run one executable draft/baseline case through the native API runner."""
    selected_source_id = str(source_id or "").strip()
    selected_plan_id = str(plan_id or "").strip()
    selected_case_id = str(case_id or "").strip()
    if not selected_plan_id:
        raise ValueError("请选择要调试的 API 用例计划")
    if not selected_case_id:
        raise ValueError("请选择要调试的 API 用例")
    plan = api_test_plan_service.get_api_test_plan(selected_plan_id, source_id=selected_source_id)
    if not plan:
        raise ValueError("API 用例计划不存在")
    execution = api_execution_service.start_api_case_debug(selected_plan_id, selected_case_id)
    return {"ok": True, "execution": execution}


__all__ = [
    "api_testing_workbench",
    "debug_api_case",
    "update_apifox_snapshot",
]
