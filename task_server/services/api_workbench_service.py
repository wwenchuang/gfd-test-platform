"""Simplified API testing workbench facade.

This module intentionally composes existing Apifox asset, API plan, native
runner, auth binding and report services. It does not own execution semantics;
it gives the frontend one stable payload for the day-to-day API workflow.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List

from task_server.services import (
    api_asset_service,
    api_case_contract_service,
    apifox_discovery_service,
    api_execution_service,
    api_module_service,
    api_report_service,
    api_task_service,
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


def _environment_has_base_url(source: Dict[str, Any]) -> bool:
    snapshot = source.get("environment_snapshot") if isinstance(source.get("environment_snapshot"), dict) else {}
    base_urls = snapshot.get("base_urls") if isinstance(snapshot.get("base_urls"), list) else []
    return any(str((item if isinstance(item, dict) else {}).get("url") or "").strip() for item in base_urls)


def _environment_snapshot_looks_like_grouped_parameter_placeholders(source: Dict[str, Any]) -> bool:
    snapshot = source.get("environment_snapshot") if isinstance(source.get("environment_snapshot"), dict) else {}
    variables = snapshot.get("variables") if isinstance(snapshot.get("variables"), list) else []
    names = [
        str((item if isinstance(item, dict) else {}).get("name") or "").strip().lower()
        for item in variables
        if str((item if isinstance(item, dict) else {}).get("name") or "").strip()
    ]
    if len(names) < 2:
        return False
    grouped_names = {"cookie", "cookies", "query", "queries", "header", "headers", "body", "path", "path_params"}
    return all(name in grouped_names for name in names)


def _select_named_option(rows: List[Dict[str, Any]], target_id: str) -> Dict[str, Any]:
    selected_id = str(target_id or "").strip()
    if selected_id:
        exact = next((item for item in rows if str(item.get("id") or "").strip() == selected_id), {})
        if exact:
            return exact
    return rows[0] if rows else {}


def _select_environment(rows: List[Dict[str, Any]], target_id: str) -> Dict[str, Any]:
    selected_id = str(target_id or "").strip()
    if selected_id:
        exact = next((item for item in rows if str(item.get("id") or "").strip() == selected_id), {})
        if exact:
            return exact
    with_base_url = next((
        item for item in rows
        if _environment_has_base_url({"environment_snapshot": item.get("environment_snapshot")})
    ), {})
    return with_base_url or (rows[0] if rows else {})


def persist_apifox_project_context(
    source_id: str,
    context: Dict[str, Any],
    *,
    branch_id: str = "",
    environment_id: str = "",
) -> Dict[str, Any]:
    """Persist discovered Apifox project/environment metadata into an existing source."""
    selected_source_id = str(source_id or "").strip()
    source = api_source_service.get_api_source(selected_source_id, masked=False)
    if not source or str(source.get("source_type") or "") != "apifox":
        return {}
    project = context.get("project") if isinstance(context.get("project"), dict) else {}
    branches = [item for item in (context.get("branches") or []) if isinstance(item, dict)]
    environments = [item for item in (context.get("environments") or []) if isinstance(item, dict)]
    selected_branch = _select_named_option(branches, branch_id or str(source.get("branch_id") or ""))
    selected_environment = _select_environment(
        environments,
        environment_id or str(source.get("environment_id") or ""),
    )
    environment_snapshot = selected_environment.get("environment_snapshot")
    if not isinstance(environment_snapshot, dict):
        environment_snapshot = source.get("environment_snapshot") if isinstance(source.get("environment_snapshot"), dict) else {}
    metadata = {
        **(source.get("provider_metadata") if isinstance(source.get("provider_metadata"), dict) else {}),
        "project_name": str(project.get("name") or source.get("name") or "").strip(),
        "project_description": str(project.get("description") or "").strip(),
        "team_id": str(((project.get("team") or {}) if isinstance(project.get("team"), dict) else {}).get("id") or "").strip(),
        "team_name": str(((project.get("team") or {}) if isinstance(project.get("team"), dict) else {}).get("name") or "").strip(),
        "branch_name": str(selected_branch.get("name") or "").strip(),
        "environment_name": str(selected_environment.get("name") or "").strip(),
        "discovery_source": "apifox_cli",
    }
    payload = {
        "source_id": selected_source_id,
        "source_type": "apifox",
        "name": str(project.get("name") or source.get("name") or "Apifox 接口").strip(),
        "base_url": str(source.get("base_url") or "https://api.apifox.com").strip(),
        "project_id": str(project.get("id") or source.get("project_id") or "").strip(),
        "branch_id": str(selected_branch.get("id") if selected_branch else source.get("branch_id") or "").strip(),
        "environment_id": str(selected_environment.get("id") if selected_environment else source.get("environment_id") or "").strip(),
        "provider_metadata": metadata,
        "environment_snapshot": environment_snapshot,
        "preserve_missing_environment_variables": True,
        "sync_enabled": bool(source.get("sync_enabled")),
        "sync_interval_minutes": source.get("sync_interval_minutes") or 60,
        "sync_scope": source.get("sync_scope") or {},
    }
    return api_source_service.save_api_source(payload)


def refresh_apifox_environment_snapshot(source_id: str, *, force: bool = False) -> Dict[str, Any]:
    """Fetch and persist Apifox environment metadata once when local cache is missing."""
    selected_source_id = str(source_id or "").strip()
    source = api_source_service.get_api_source(selected_source_id, masked=False)
    if (
        not source
        or str(source.get("source_type") or "") != "apifox"
        or not str(source.get("access_token") or "").strip()
        or not str(source.get("project_id") or "").strip()
    ):
        return api_source_service.get_api_source(selected_source_id, masked=True) if selected_source_id else {}
    if not force and _environment_has_base_url(source):
        return api_source_service.get_api_source(selected_source_id, masked=True)
    context = apifox_discovery_service.discover_project_context(
        str(source.get("access_token") or "").strip(),
        str(source.get("project_id") or "").strip(),
        base_url=str(source.get("base_url") or "https://api.apifox.com").strip(),
        preferred_environment_id=str(source.get("environment_id") or "").strip(),
        timeout_seconds=25.0,
    )
    return persist_apifox_project_context(
        selected_source_id,
        context,
        branch_id=str(source.get("branch_id") or "").strip(),
        environment_id=str(source.get("environment_id") or "").strip(),
    )


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
            "message": "还没有本地 API 资产快照，请先手动更新 Apifox。",
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
        "selected_endpoint_keys": [
            str(item)
            for item in (plan.get("selected_endpoint_keys") or [])
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


def _execution_with_detail(row: Dict[str, Any]) -> Dict[str, Any]:
    execution_id = str((row or {}).get("execution_id") or "").strip()
    if not execution_id:
        return row if isinstance(row, dict) else {}
    try:
        detail = api_execution_service.get_api_execution(execution_id)
    except Exception:
        detail = {}
    if not isinstance(detail, dict) or not detail:
        return row
    projected_keys = {
        "execution_id",
        "run_id",
        "run_mode",
        "provider",
        "plan_id",
        "plan_name",
        "source_id",
        "status",
        "report_status",
        "report_id",
        "current_phase",
        "stats",
        "created_at",
        "started_at",
        "finished_at",
        "updated_at",
        "duration_seconds",
        "error",
        "poll_after_ms",
        "base_url",
        "phases",
        "events",
        "results",
    }
    projected = {
        key: detail.get(key)
        for key in projected_keys
        if key in detail
    }
    events = projected.get("events")
    if isinstance(events, list):
        projected["events"] = events[-200:]
    results = projected.get("results")
    if isinstance(results, list):
        projected["results"] = results[-100:]
    return api_case_contract_service.sanitize_sensitive_data({**row, **projected})


def _execution_rows_with_detail(rows: List[Dict[str, Any]], limit: int) -> List[Dict[str, Any]]:
    detailed: List[Dict[str, Any]] = []
    for row in rows[:max(1, int(limit or 1))]:
        if not isinstance(row, dict):
            continue
        detailed.append(_execution_with_detail(row))
    return detailed


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
        "active_runs": _execution_rows_with_detail(context.get("active_runs") or [], 3),
        "recent_runs": _execution_rows_with_detail(context.get("recent_runs") or [], 8),
        "empty_reason": context.get("empty_reason") or "",
        "poll_after_ms": context.get("poll_after_ms") or 2000,
    }


def _sync_status_text(status: str, *, has_source: bool, has_snapshot: bool) -> str:
    value = str(status or "").strip().lower()
    if not has_source:
        return "未连接"
    if value in {"queued", "running"}:
        return "更新中"
    if value in {"succeeded", "no_change"}:
        return "更新完成"
    if value == "failed":
        return "更新失败"
    if has_snapshot:
        return "已就绪"
    return "待更新"


def _latest_sync(syncs: List[Dict[str, Any]]) -> Dict[str, Any]:
    return syncs[0] if syncs else {}


def _pending_change_count(sync: Dict[str, Any]) -> int:
    summary = sync.get("summary") if isinstance(sync.get("summary"), dict) else {}
    return (
        _safe_int(summary.get("added"), 0)
        + _safe_int(summary.get("changed"), 0)
        + _safe_int(summary.get("removed"), 0)
    )


def _pending_changes(sync: Dict[str, Any]) -> List[Dict[str, Any]]:
    summary = sync.get("summary") if isinstance(sync.get("summary"), dict) else {}
    rows: List[Dict[str, Any]] = []
    change_specs = [
        ("added", "新增接口", "为新增接口生成成功流、鉴权和异常测试资产"),
        ("changed", "接口结构变化", "重新审阅入参、响应断言和受影响测试资产"),
        ("removed", "接口删除", "确认下线范围并停用关联测试资产"),
    ]
    affected = _safe_int(summary.get("affected_plans"), 0)
    for key, label, suggestion in change_specs:
        count = _safe_int(summary.get(key), 0)
        if not count:
            continue
        rows.append({
            "type": key,
            "title": label,
            "count": count,
            "affected_tests": affected,
            "ai_suggestion": suggestion,
            "sync_id": str(sync.get("sync_id") or ""),
            "diff_id": str(sync.get("diff_id") or ""),
        })
    return rows


def _flatten_module_nodes(nodes: List[Dict[str, Any]], result: List[Dict[str, Any]] | None = None) -> List[Dict[str, Any]]:
    if result is None:
        result = []
    for node in nodes or []:
        if not isinstance(node, dict):
            continue
        result.append(node)
        _flatten_module_nodes(node.get("children") or [], result)
    return result


def _module_path(value: Any) -> str:
    return api_module_service.normalize_module_path(value)


def _module_matches(candidate: str, target: str) -> bool:
    normalized_candidate = _module_path(candidate)
    normalized_target = _module_path(target)
    if not normalized_candidate or not normalized_target:
        return False
    return (
        normalized_candidate == normalized_target
        or normalized_candidate.startswith(f"{normalized_target}/")
        or normalized_target.startswith(f"{normalized_candidate}/")
    )


def _module_endpoint_rows(
    endpoints: List[Dict[str, Any]],
    module_path: str,
) -> List[Dict[str, Any]]:
    normalized = _module_path(module_path)
    return [
        endpoint for endpoint in endpoints
        if api_module_service.module_selected(
            endpoint.get("module_path") or endpoint.get("module"),
            [normalized],
        )
    ]


def _first_environment_base_url(source: Dict[str, Any]) -> Dict[str, str]:
    snapshot = source.get("environment_snapshot") if isinstance(source.get("environment_snapshot"), dict) else {}
    for item in snapshot.get("base_urls") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if url:
            return {
                "name": str(item.get("name") or "default").strip() or "default",
                "url": url,
            }
    return {"name": "", "url": ""}


def _module_task_environment(source: Dict[str, Any]) -> Dict[str, Any]:
    base_url = _first_environment_base_url(source)
    return {
        "id": str(source.get("environment_id") or ""),
        "name": str(source.get("environment_name") or "") or "执行环境待配置",
        "base_url_name": base_url["name"],
        "base_url": base_url["url"],
        "source_id": str(source.get("source_id") or ""),
    }


def _module_task_auth(execution: Dict[str, Any]) -> Dict[str, Any]:
    binding = execution.get("auth_binding") if isinstance(execution.get("auth_binding"), dict) else {}
    auth_type = str(binding.get("auth_type") or "").strip()
    header_name = str(binding.get("header_name") or "").strip()
    configured = bool(binding.get("configured") or binding.get("auth_ref"))
    if not configured:
        label = "未配置"
    elif auth_type.lower() == "bearer":
        label = "Bearer 已配置"
    elif header_name:
        label = f"{header_name} 已配置"
    else:
        label = "鉴权已配置"
    return {
        "configured": configured,
        "label": label,
        "auth_type": auth_type,
        "header_name": header_name,
        "variable_name": str(binding.get("variable_name") or ""),
        "auth_ref": str(binding.get("auth_ref") or ""),
    }


def _plan_matches_module(
    plan: Dict[str, Any],
    module_path: str,
    endpoint_keys: set[str],
) -> bool:
    has_module_contract = False
    for plan_module in plan.get("module_paths") or []:
        normalized_plan_module = _module_path(plan_module)
        normalized_module_path = _module_path(module_path)
        if not normalized_plan_module or not normalized_module_path:
            continue
        has_module_contract = True
        if (
            normalized_module_path == normalized_plan_module
            or normalized_module_path.startswith(f"{normalized_plan_module}/")
        ):
            return True
    if has_module_contract:
        return False
    selected_keys = {
        str(item or "").strip()
        for item in (plan.get("selected_endpoint_keys") or [])
        if str(item or "").strip()
    }
    return bool(selected_keys and endpoint_keys and selected_keys.intersection(endpoint_keys))


def _first_matching_plan(
    plans: List[Dict[str, Any]],
    module_path: str,
    endpoint_keys: set[str],
) -> Dict[str, Any]:
    return next(
        (
            plan for plan in plans
            if _plan_matches_module(plan, module_path, endpoint_keys)
        ),
        {},
    )


def _run_for_plans(rows: List[Dict[str, Any]], plan_ids: set[str]) -> Dict[str, Any]:
    if not plan_ids:
        return {}
    return next(
        (
            row for row in rows or []
            if str(row.get("plan_id") or "").strip() in plan_ids
        ),
        {},
    )


def _module_task_primary_action(
    draft: Dict[str, Any],
    baseline: Dict[str, Any],
    active_run: Dict[str, Any],
    execution: Dict[str, Any],
) -> tuple[str, str, str, str]:
    if active_run:
        return "view_run", "查看执行日志", "运行中", "当前模块已有执行任务，优先查看实时日志和报告。"
    if draft:
        if _safe_int(draft.get("executable_case_count"), 0) > 0:
            return "debug_draft", "批量调试草稿", "可调试", "先跑通草稿，再保存成基线。"
        return "open_draft", "审阅草稿", "待补数据", "AI 已生成草稿，但仍有入参、断言或环境需要补齐。"
    readiness = execution.get("readiness") if isinstance(execution.get("readiness"), dict) else {}
    if baseline:
        if (
            bool(baseline.get("can_execute"))
            and (baseline.get("execution_readiness") or {}).get("can_execute") is not False
            and readiness.get("can_execute") is not False
        ):
            return "run_baseline", "执行基线", "可回归", "用已保存测试资产执行接口回归。"
        return "inspect_baseline", "检查基线", "待配置", "基线存在，但执行环境、业务鉴权或版本需要确认。"
    return "generate", "生成测试资产", "待生成", "从该模块接口生成 AI 测试草稿。"


def _module_task_sort_key(task: Dict[str, Any]) -> tuple[Any, ...]:
    action_rank = {
        "view_run": 0,
        "debug_draft": 1,
        "run_baseline": 2,
        "open_draft": 3,
        "inspect_baseline": 4,
        "generate": 5,
    }
    path = str(task.get("path") or "")
    depth = len([part for part in path.split("/") if part])
    has_plan = bool(task.get("draft") or task.get("baseline") or task.get("active_run"))
    return (
        0 if has_plan else 1,
        action_rank.get(str(task.get("primary_action") or ""), 9),
        -depth,
        _safe_int(task.get("endpoint_count"), 0),
        path,
    )


def _module_tasks(
    module_summary: Dict[str, Any],
    endpoints: List[Dict[str, Any]],
    plans: Dict[str, Any],
    execution: Dict[str, Any],
    source: Dict[str, Any],
) -> List[Dict[str, Any]]:
    modules = _flatten_module_nodes(module_summary.get("roots") or [])
    if not modules:
        return []
    environment = _module_task_environment(source)
    auth = _module_task_auth(execution)
    active_runs = execution.get("active_runs") or []
    recent_runs = execution.get("recent_runs") or []
    rows: List[Dict[str, Any]] = []
    for module in modules:
        path = _module_path(module.get("path"))
        if not path:
            continue
        module_endpoints = _module_endpoint_rows(endpoints, path)
        endpoint_keys = {
            str(endpoint.get("endpoint_key") or "").strip()
            for endpoint in module_endpoints
            if str(endpoint.get("endpoint_key") or "").strip()
        }
        draft = _first_matching_plan(plans.get("drafts") or [], path, endpoint_keys)
        baseline = _first_matching_plan(plans.get("baselines") or [], path, endpoint_keys)
        plan_ids = {
            str(plan.get("plan_id") or "").strip()
            for plan in (draft, baseline)
            if str(plan.get("plan_id") or "").strip()
        }
        active_run = _run_for_plans(active_runs, plan_ids)
        latest_run = active_run or _run_for_plans(recent_runs, plan_ids)
        if not (draft or baseline or active_run) and module.get("children"):
            continue
        primary_action, primary_label, status_label, primary_detail = _module_task_primary_action(
            draft,
            baseline,
            active_run,
            execution,
        )
        rows.append({
            "path": path,
            "name": str(module.get("name") or path.rsplit("/", 1)[-1] or "未分组"),
            "depth": _safe_int(module.get("depth"), len(path.split("/"))),
            "endpoint_count": len(module_endpoints) or _safe_int(module.get("endpoint_count"), 0),
            "endpoint_ids": [
                str(endpoint.get("endpoint_id") or "")
                for endpoint in module_endpoints[:60]
                if str(endpoint.get("endpoint_id") or "").strip()
            ],
            "endpoint_names": [
                str(endpoint.get("name") or endpoint.get("summary") or endpoint.get("path") or "")
                for endpoint in module_endpoints[:8]
                if str(endpoint.get("name") or endpoint.get("summary") or endpoint.get("path") or "").strip()
            ],
            "draft": draft,
            "baseline": baseline,
            "active_run": active_run,
            "latest_run": latest_run,
            "environment": environment,
            "auth": auth,
            "primary_action": primary_action,
            "primary_label": primary_label,
            "primary_detail": primary_detail,
            "status_label": status_label,
        })
    rows.sort(key=_module_task_sort_key)
    return rows[:30]


def _coverage_metrics(
    endpoints: List[Dict[str, Any]],
    plans_payload: Dict[str, Any],
    execution: Dict[str, Any],
    sync: Dict[str, Any],
) -> Dict[str, Any]:
    endpoint_keys = {
        str(endpoint.get("endpoint_key") or "").strip()
        for endpoint in endpoints
        if str(endpoint.get("endpoint_key") or "").strip()
    }
    covered_keys = set()
    for plan in plans_payload.get("baselines") or []:
        for key in plan.get("selected_endpoint_keys") or []:
            normalized = str(key or "").strip()
            if normalized and (not endpoint_keys or normalized in endpoint_keys):
                covered_keys.add(normalized)
    total = len(endpoints)
    covered = len(covered_keys)
    today = time.strftime("%Y-%m-%d")
    executions = list(execution.get("active_runs") or []) + list(execution.get("recent_runs") or [])
    today_executions = len([
        item for item in executions
        if str(item.get("created_at") or "").startswith(today)
    ])
    return {
        "total_endpoints": total,
        "covered_endpoints": min(covered, total),
        "coverage_rate": round((min(covered, total) / total) * 100) if total else 0,
        "pending_changes": _pending_change_count(sync),
        "today_executions": today_executions,
    }


def _sync_state(
    source: Dict[str, Any],
    snapshot: Dict[str, Any],
    sync: Dict[str, Any],
) -> Dict[str, Any]:
    has_source = bool(source.get("source_id"))
    has_snapshot = str(snapshot.get("state") or "") == "ready"
    status = str(sync.get("status") or source.get("last_sync_status") or "").strip()
    summary = sync.get("summary") if isinstance(sync.get("summary"), dict) else {}
    return {
        "project": str(source.get("project_name") or source.get("name") or "未连接"),
        "status": _sync_status_text(status, has_source=has_source, has_snapshot=has_snapshot),
        "status_raw": status,
        "last_sync_at": str(source.get("last_success_at") or snapshot.get("last_sync_at") or sync.get("finished_at") or sync.get("updated_at") or ""),
        "interface_count": _safe_int(snapshot.get("endpoint_count"), 0) or _safe_int(sync.get("scoped_endpoint_count"), 0),
        "pending_changes": _pending_change_count(sync),
        "added": _safe_int(summary.get("added"), 0),
        "changed": _safe_int(summary.get("changed"), 0),
        "removed": _safe_int(summary.get("removed"), 0),
        "affected_tests": _safe_int(summary.get("affected_plans"), 0),
        "action_label": "手动更新 Apifox" if has_source else "连接 Apifox",
        "source_id": str(source.get("source_id") or ""),
        "sync_id": str(sync.get("sync_id") or ""),
        "error": str(sync.get("error") or source.get("last_error") or ""),
    }


def api_testing_workbench(source_id: str = "") -> Dict[str, Any]:
    """Return the complete simplified API workbench state for one source."""
    sources = [_source_summary(source) for source in api_source_service.list_api_sources()]
    source = _selected_source(source_id)
    selected_source_id = str(source.get("source_id") or "").strip()
    should_refresh_environment = (
        selected_source_id
        and not _environment_has_base_url(source)
    )
    if should_refresh_environment:
        try:
            refreshed = refresh_apifox_environment_snapshot(
                selected_source_id,
                force=False,
            )
            if refreshed:
                source = refreshed
                sources = [_source_summary(item) for item in api_source_service.list_api_sources()]
        except Exception:
            pass
    asset, revision = _active_revision_for_source(selected_source_id) if selected_source_id else ({}, {})
    endpoints = [
        _endpoint_summary(endpoint)
        for endpoint in (revision.get("endpoints") or [])
        if isinstance(endpoint, dict)
    ]
    reports = api_report_service.list_api_reports(limit=10, source_id=selected_source_id)
    syncs = api_sync_service.list_api_syncs(limit=10, source_id=selected_source_id) if selected_source_id else []
    latest_sync = _latest_sync(syncs)
    plans_payload = _plans_for_source(selected_source_id) if selected_source_id else {
        "drafts": [],
        "baselines": [],
        "latest_draft": {},
        "latest_baseline": {},
        "draft_count": 0,
        "baseline_count": 0,
    }
    execution_payload = _execution_summary(selected_source_id)
    snapshot_payload = _snapshot_summary(revision, asset)
    source_payload = _source_summary(source)
    metrics_payload = _coverage_metrics(endpoints, plans_payload, execution_payload, latest_sync)
    sync_state_payload = _sync_state(source_payload, snapshot_payload, latest_sync)
    pending_changes_payload = _pending_changes(latest_sync)
    module_summary_payload = api_module_service.module_summary(revision.get("endpoints") or [])
    module_tasks_payload = _module_tasks(
        module_summary_payload,
        endpoints,
        plans_payload,
        execution_payload,
        source_payload,
    )
    return {
        "ok": True,
        "mode": "native_api_workbench",
        "source": source_payload,
        "sources": sources,
        "apifox_credential": api_source_service.get_apifox_credential(masked=True),
        "snapshot": snapshot_payload,
        "metrics": metrics_payload,
        "sync_state": sync_state_payload,
        "pending_changes": pending_changes_payload,
        "module_tasks": module_tasks_payload,
        "task": api_task_service.build_api_test_task(
            source=source_payload,
            snapshot=snapshot_payload,
            plans=plans_payload,
            execution=execution_payload,
            reports=reports,
            sync_state=sync_state_payload,
            metrics=metrics_payload,
            pending_changes=pending_changes_payload,
        ),
        "scope": {
            "endpoint_count": len(endpoints),
            "endpoints": endpoints[:300],
            "modules": module_summary_payload,
            "business_lines": api_module_service.business_line_summary(revision.get("endpoints") or []),
        },
        "cases": plans_payload,
        "execution": execution_payload,
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
    try:
        refresh_apifox_environment_snapshot(selected_source_id, force=True)
    except Exception:
        pass
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


def debug_api_cases(source_id: str, plan_id: str, case_ids: List[str] | None = None) -> Dict[str, Any]:
    """Run executable draft cases as one native debug batch before confirmation."""
    selected_source_id = str(source_id or "").strip()
    selected_plan_id = str(plan_id or "").strip()
    if not selected_plan_id:
        raise ValueError("请选择要批量调试的 API 用例计划")
    plan = api_test_plan_service.get_api_test_plan(selected_plan_id, source_id=selected_source_id)
    if not plan:
        raise ValueError("API 用例计划不存在")
    execution = api_execution_service.start_api_cases_debug(
        selected_plan_id,
        [
            str(item or "").strip()
            for item in (case_ids or [])
            if str(item or "").strip()
        ],
    )
    return {"ok": True, "execution": execution}


__all__ = [
    "api_testing_workbench",
    "debug_api_case",
    "debug_api_cases",
    "persist_apifox_project_context",
    "refresh_apifox_environment_snapshot",
    "update_apifox_snapshot",
]
