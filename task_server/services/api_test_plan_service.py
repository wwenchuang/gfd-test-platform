"""AI-assisted API test plan drafts for imported API assets."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import time
from typing import Any, Dict, List

from task_server.config import safe_bool
from task_server.storage import clean_id, read_json_file, safe_join, unique_millis_id, write_json_file
from task_server.services import api_asset_service, api_case_contract_service, api_schema_diff_service, api_workspace_service
from task_server.services.ai_skill_service import run_ai_skill


API_TESTING_DIR = api_asset_service.API_TESTING_DIR


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _api_path(*parts: str) -> str:
    return safe_join(API_TESTING_DIR, *parts)


def _plan_path(plan_id: str) -> str:
    return _api_path("plans", f"{clean_id(plan_id, 'api_plan')}.json")


def _index_path() -> str:
    return _api_path("plans", "index.json")


def _save_plan_index(plan: Dict[str, Any]) -> None:
    index = read_json_file(_index_path(), default=[]) or []
    if not isinstance(index, list):
        index = []
    item = {
        "plan_id": plan.get("plan_id"),
        "snapshot_id": plan.get("snapshot_id"),
        "asset_id": plan.get("asset_id"),
        "asset_revision_id": plan.get("asset_revision_id"),
        "name": plan.get("name"),
        "status": plan.get("status"),
        "case_count": plan.get("case_count"),
        "executable_case_count": plan.get("executable_case_count", 0),
        "needs_review_case_count": plan.get("needs_review_case_count", 0),
        "execution_readiness": plan.get("execution_readiness") or {},
        "revision_state": plan.get("revision_state") or {},
        "endpoint_count": plan.get("endpoint_count"),
        "source": plan.get("source"),
        "created_at": plan.get("created_at"),
        "confirmed_at": plan.get("confirmed_at", ""),
    }
    index = [row for row in index if row.get("plan_id") != item.get("plan_id")]
    index.insert(0, item)
    write_json_file(_index_path(), index[:100])


def _selected_endpoints(snapshot_id: str, endpoint_ids: List[str]) -> List[Dict[str, Any]]:
    snapshot = api_asset_service.get_api_snapshot(snapshot_id)
    endpoints = snapshot.get("endpoints") or []
    if not isinstance(endpoints, list):
        return []
    selected = {str(item).strip() for item in (endpoint_ids or []) if str(item).strip()}
    if not selected:
        return endpoints
    return [endpoint for endpoint in endpoints if endpoint.get("endpoint_id") in selected]


def _response_assertion_texts(endpoint: Dict[str, Any]) -> List[str]:
    assertions = ["HTTP 状态码为 2xx"]
    schema = endpoint.get("response_schema") or {}
    properties = schema.get("properties") if isinstance(schema, dict) else {}
    if isinstance(properties, dict):
        if "code" in properties:
            assertions.append("$.code 符合接口成功约定")
        if "data" in properties:
            assertions.append("$.data 结构符合 OpenAPI response schema")
    if len(assertions) == 1:
        assertions.append("响应结构符合 OpenAPI response schema")
    return assertions


def _case(
    case_id: str,
    endpoint: Dict[str, Any],
    name: str,
    case_type: str,
    priority: str,
    steps: List[str],
    assertion_texts: List[str],
    omitted_field: str = "",
    proposed: Dict[str, Any] | None = None,
    known_case_ids: set[str] | None = None,
) -> Dict[str, Any]:
    contract = api_case_contract_service.build_api_case_contract(
        endpoint,
        case_type,
        omitted_field=omitted_field,
        proposed=proposed,
        known_case_ids=known_case_ids,
    )
    return {
        "case_id": case_id,
        "endpoint_id": endpoint.get("endpoint_id"),
        "endpoint_key": endpoint.get("endpoint_key"),
        "asset_revision_id": endpoint.get("asset_revision_id"),
        "endpoint": f"{endpoint.get('method')} {endpoint.get('path')}",
        "module": endpoint.get("module") or "未分组",
        "name": name,
        "type": case_type,
        "priority": priority,
        "steps": steps,
        "assertion_texts": assertion_texts,
        "status": "draft",
        **contract,
    }


def _required_targets(endpoint: Dict[str, Any]) -> List[tuple[str, str]]:
    targets: List[tuple[str, str]] = []
    for parameter in endpoint.get("parameters") or []:
        if not isinstance(parameter, dict) or not parameter.get("required"):
            continue
        name = str(parameter.get("name") or "").strip()
        location = str(parameter.get("in") or "").strip().lower()
        if name and location in {"path", "query", "header"}:
            targets.append((name, f"{location}.{name}"))
    schema = endpoint.get("request_schema") or {}
    if isinstance(schema, dict):
        for name in schema.get("required") or []:
            text = str(name or "").strip()
            if text:
                targets.append((text, f"body.{text}"))
    return targets


def _local_cases_for_endpoint(endpoint: Dict[str, Any], plan_id: str, offset: int) -> List[Dict[str, Any]]:
    label = endpoint.get("name") or f"{endpoint.get('method')} {endpoint.get('path')}"
    endpoint_text = f"{endpoint.get('method')} {endpoint.get('path')}"
    cases = [
        _case(
            f"API-{offset:03d}-P",
            endpoint,
            f"{label}-成功响应",
            "positive",
            "P0",
            [
                f"按 OpenAPI 定义准备 {endpoint_text} 的合法请求参数",
                "使用当前 MeterSphere 环境变量发送请求",
                "保存响应体用于断言",
            ],
            _response_assertion_texts(endpoint),
        )
    ]
    required_targets = _required_targets(endpoint)
    for index, (field, target) in enumerate(required_targets[:3], start=1):
        cases.append(_case(
            f"API-{offset:03d}-N{index}",
            endpoint,
            f"{label}-{field} 缺失校验",
            "negative",
            "P1",
            [
                f"按 OpenAPI 定义准备 {endpoint_text} 请求",
                f"移除必填字段 {field}",
                "发送请求并保存响应",
            ],
            [
                "接口返回业务失败或 4xx 状态",
                f"错误信息能定位到 {field} 参数缺失或非法",
            ],
            omitted_field=target,
        ))
    if api_case_contract_service.endpoint_requires_auth(endpoint):
        cases.append(_case(
            f"API-{offset:03d}-A",
            endpoint,
            f"{label}-未授权访问校验",
            "auth",
            "P1",
            [
                f"准备 {endpoint_text} 的合法请求参数",
                "移除或置空鉴权信息",
                "发送请求并保存响应",
            ],
            ["接口返回 401/403 或明确的未授权业务错误"],
        ))
    for case in cases:
        case["plan_case_key"] = f"{plan_id}:{case['case_id']}"
    return cases


def _local_plan_cases(endpoints: List[Dict[str, Any]], plan_id: str) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    for offset, endpoint in enumerate(endpoints, start=1):
        cases.extend(_local_cases_for_endpoint(endpoint, plan_id, offset))
    return cases


def _normalize_ai_cases(raw_cases: Any, endpoints: List[Dict[str, Any]], plan_id: str) -> List[Dict[str, Any]]:
    endpoint_ids = {endpoint.get("endpoint_id"): endpoint for endpoint in endpoints}
    normalized: List[Dict[str, Any]] = []
    if not isinstance(raw_cases, list):
        return []
    prepared: List[tuple[Dict[str, Any], Dict[str, Any], str]] = []
    used_case_ids: Dict[str, int] = {}
    for index, item in enumerate(raw_cases, start=1):
        if not isinstance(item, dict):
            continue
        endpoint_id = str(item.get("endpoint_id") or "").strip()
        endpoint = endpoint_ids.get(endpoint_id)
        if not endpoint:
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        base_case_id = str(item.get("case_id") or f"API-AI-{index:03d}").strip() or f"API-AI-{index:03d}"
        duplicate_index = used_case_ids.get(base_case_id, 0)
        used_case_ids[base_case_id] = duplicate_index + 1
        case_id = base_case_id if duplicate_index == 0 else f"{base_case_id}-{duplicate_index + 1}"
        prepared.append((item, endpoint, case_id))
    known_case_ids = {case_id for _, _, case_id in prepared}
    for item, endpoint, case_id in prepared:
        case_type = str(item.get("type") or "positive").strip().lower()
        if case_type not in {"positive", "negative", "auth", "boundary", "chain", "error"}:
            case_type = "positive"
        name = str(item.get("name") or "").strip()
        steps = [str(step).strip() for step in (item.get("steps") or []) if str(step).strip()]
        assertion_texts = [
            str(assertion).strip()
            for assertion in (item.get("assertion_texts") or item.get("assertions") or [])
            if isinstance(assertion, str) and str(assertion).strip()
        ]
        normalized.append(_case(
            case_id,
            endpoint,
            name,
            case_type,
            str(item.get("priority") or "P1").strip() or "P1",
            steps,
            assertion_texts,
            omitted_field=str(item.get("negative_target") or "").strip(),
            proposed=item,
            known_case_ids=known_case_ids,
        ))
        normalized[-1]["plan_case_key"] = f"{plan_id}:{normalized[-1]['case_id']}"
    return normalized


def _ensure_positive_seed_coverage(
    ai_cases: List[Dict[str, Any]],
    local_cases: List[Dict[str, Any]],
    plan_id: str,
) -> List[Dict[str, Any]]:
    merged = [copy.deepcopy(case) for case in ai_cases]
    covered_endpoint_ids = {
        str(case.get("endpoint_id") or "")
        for case in merged
        if case.get("type") in {"positive", "chain"}
    }
    used_case_ids = {
        str(case.get("case_id") or "")
        for case in merged
        if str(case.get("case_id") or "")
    }
    for seed in local_cases:
        endpoint_id = str(seed.get("endpoint_id") or "")
        if seed.get("type") != "positive" or endpoint_id in covered_endpoint_ids:
            continue
        item = copy.deepcopy(seed)
        base_case_id = str(item.get("case_id") or "API-SEED")
        case_id = base_case_id
        suffix = 2
        while case_id in used_case_ids:
            case_id = f"{base_case_id}-{suffix}"
            suffix += 1
        item["case_id"] = case_id
        item["plan_case_key"] = f"{plan_id}:{case_id}"
        merged.append(item)
        used_case_ids.add(case_id)
        covered_endpoint_ids.add(endpoint_id)
    return merged


def _decision_trace(
    runtime_trace: Dict[str, Any],
    input_hash: str,
    started_at: str,
    started_monotonic: float,
    case_count: int,
    needs_review_count: int,
    success: bool,
    error: str = "",
) -> Dict[str, Any]:
    return {
        "trace_id": unique_millis_id("ai_trace"),
        "skill": "api_test_designer",
        "action": "generate_case",
        "provider_id": str(runtime_trace.get("providerId") or runtime_trace.get("selectedProviderId") or ""),
        "model": str(runtime_trace.get("model") or runtime_trace.get("selectedModel") or ""),
        "fallback_used": bool(runtime_trace.get("fallbackUsed")),
        "input_hash": input_hash,
        "output_summary": f"generated {case_count} cases; {needs_review_count} need review",
        "started_at": started_at,
        "finished_at": _now(),
        "duration_ms": max(0, int((time.monotonic() - started_monotonic) * 1000)),
        "success": bool(success),
        "error": str(error or runtime_trace.get("error") or "")[:500],
    }


def _ai_cases(snapshot: Dict[str, Any], endpoints: List[Dict[str, Any]], local_cases: List[Dict[str, Any]], plan_id: str, model_config: Dict[str, Any] | None) -> Dict[str, Any]:
    trace: Dict[str, Any] = {}
    payload = {
        "snapshot": {
            "snapshot_id": snapshot.get("snapshot_id"),
            "title": snapshot.get("title"),
            "version": snapshot.get("version"),
        },
        "endpoints": endpoints,
        "seed_cases": local_cases,
    }
    input_hash = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    started_at = _now()
    started_monotonic = time.monotonic()
    try:
        result = run_ai_skill(
            "api_test_designer",
            payload,
            model_config=model_config,
            runtime_trace=trace,
            repair_invalid_json=True,
            output_defaults={"cases": [], "review": {}},
            timeout=180,
        )
        cases = _normalize_ai_cases(result.get("cases"), endpoints, plan_id)
        if cases:
            cases = _ensure_positive_seed_coverage(cases, local_cases, plan_id)
            readiness = api_case_contract_service.summarize_api_case_readiness(cases)
            return {
                "ok": True,
                "cases": cases,
                "review": result.get("review") or {},
                "trace": trace,
                "decision_trace": _decision_trace(
                    trace,
                    input_hash,
                    started_at,
                    started_monotonic,
                    len(cases),
                    readiness.get("needs_review_case_count", 0),
                    True,
                ),
            }
        error = "AI 未返回有效接口用例"
        return {
            "ok": False,
            "error": error,
            "trace": trace,
            "decision_trace": _decision_trace(
                trace, input_hash, started_at, started_monotonic, 0, 0, False, error
            ),
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": str(exc),
            "trace": trace,
            "decision_trace": _decision_trace(
                trace, input_hash, started_at, started_monotonic, 0, 0, False, str(exc)
            ),
        }


def _case_contract_missing(case: Dict[str, Any], endpoint: Dict[str, Any] | None, known_case_ids: set[str]) -> List[str]:
    missing: List[str] = []
    if not str(case.get("case_id") or "").strip():
        missing.append("case_id")
    if case.get("contract_version") != api_case_contract_service.CONTRACT_VERSION:
        missing.append("contract_version")
    request = case.get("request")
    if not isinstance(request, dict):
        missing.append("request")
    else:
        method = str(request.get("method") or "").strip().upper()
        path = str(request.get("path") or "").strip()
        if not method:
            missing.append("request.method")
        if not path:
            missing.append("request.path")
        if endpoint and (
            method != str(endpoint.get("method") or "").strip().upper()
            or path != str(endpoint.get("path") or "").strip()
        ):
            missing.append("request.endpoint_match")
    assertions = case.get("assertions")
    if not isinstance(assertions, list) or not any(
        isinstance(assertion, dict) and assertion.get("type") == "status"
        for assertion in (assertions or [])
    ):
        missing.append("assertions.status")
    for dependency in case.get("dependencies") or []:
        if not isinstance(dependency, dict) or not dependency.get("required", True):
            continue
        dependency_id = str(dependency.get("case_id") or "").strip()
        if dependency_id and dependency_id not in known_case_ids:
            missing.append(f"dependencies.{dependency_id}")
    readiness = case.get("readiness")
    if not isinstance(readiness, dict):
        missing.append("readiness")
    else:
        missing.extend(str(item).strip() for item in (readiness.get("missing") or []) if str(item).strip())
    return sorted(set(missing))


def _plan_auth_binding(plan: Dict[str, Any]) -> Dict[str, Any]:
    binding = plan.get("auth_binding") if isinstance(plan.get("auth_binding"), dict) else {}
    required = ("auth_ref", "auth_type", "header_name", "variable_name", "environment_id")
    if not binding.get("configured") or any(not str(binding.get(key) or "").strip() for key in required):
        return {}
    return {
        key: binding.get(key)
        for key in (*required, "configured", "configured_at", "updated_at", "binding_fingerprint")
    }


def _evaluated_cases(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    endpoint_by_id = {
        str(endpoint.get("endpoint_id") or ""): endpoint
        for endpoint in (plan.get("endpoints") or [])
        if isinstance(endpoint, dict) and endpoint.get("endpoint_id")
    }
    raw_cases = [case for case in (plan.get("cases") or []) if isinstance(case, dict)]
    auth_binding = _plan_auth_binding(plan)
    known_case_ids = {
        str(case.get("case_id") or "").strip()
        for case in raw_cases
        if str(case.get("case_id") or "").strip()
    }
    evaluated: List[Dict[str, Any]] = []
    for raw_case in raw_cases:
        case = copy.deepcopy(raw_case)
        endpoint = endpoint_by_id.get(str(case.get("endpoint_id") or ""))
        request = case.get("request") if isinstance(case.get("request"), dict) else {}
        if (
            endpoint
            and api_case_contract_service.endpoint_requires_auth(endpoint)
            and str(case.get("type") or "").strip().lower() in {"positive", "chain"}
        ):
            if auth_binding:
                request["auth_ref"] = str(auth_binding["auth_ref"])
            else:
                request["auth_ref"] = ""
            case["request"] = request
        missing = _case_contract_missing(case, endpoint, known_case_ids)
        if (
            endpoint
            and api_case_contract_service.endpoint_requires_auth(endpoint)
            and str(case.get("type") or "").strip().lower() in {"positive", "chain"}
            and not auth_binding
        ):
            missing.append("auth_binding")
        if endpoint_by_id and not endpoint:
            missing.append("endpoint_id")
            missing = sorted(set(missing))
        readiness = case.get("readiness") if isinstance(case.get("readiness"), dict) else {}
        issues = [str(item).strip() for item in (readiness.get("issues") or []) if str(item).strip()]
        case["readiness"] = {
            "state": "needs_review" if missing else "executable",
            "missing": missing,
            "issues": sorted(set(issues)),
        }
        evaluated.append(case)

    case_id_counts: Dict[str, int] = {}
    for case in evaluated:
        case_id = str(case.get("case_id") or "").strip()
        if case_id:
            case_id_counts[case_id] = case_id_counts.get(case_id, 0) + 1

    def add_missing(case: Dict[str, Any], key: str) -> bool:
        readiness = case.get("readiness") or {}
        missing = set(readiness.get("missing") or [])
        if key in missing:
            return False
        missing.add(key)
        readiness["missing"] = sorted(missing)
        readiness["state"] = "needs_review"
        case["readiness"] = readiness
        return True

    for case in evaluated:
        case_id = str(case.get("case_id") or "").strip()
        if case_id and case_id_counts.get(case_id, 0) > 1:
            add_missing(case, "case_id.duplicate")

    case_by_id = {
        str(case.get("case_id") or "").strip(): case
        for case in evaluated
        if str(case.get("case_id") or "").strip()
    }
    graph = {
        case_id: [
            str(dependency.get("case_id") or "").strip()
            for dependency in (case.get("dependencies") or [])
            if isinstance(dependency, dict)
            and dependency.get("required", True)
            and str(dependency.get("case_id") or "").strip() in case_by_id
        ]
        for case_id, case in case_by_id.items()
    }
    visit_state: Dict[str, int] = {}
    stack: List[str] = []
    cycle_ids: set[str] = set()

    def visit(case_id: str) -> None:
        state = visit_state.get(case_id, 0)
        if state == 2:
            return
        if state == 1:
            if case_id in stack:
                cycle_ids.update(stack[stack.index(case_id):])
            return
        visit_state[case_id] = 1
        stack.append(case_id)
        for dependency_id in graph.get(case_id, []):
            visit(dependency_id)
        stack.pop()
        visit_state[case_id] = 2

    for case_id in graph:
        visit(case_id)
    for case_id in cycle_ids:
        add_missing(case_by_id[case_id], "dependencies.cycle")

    changed = True
    while changed:
        changed = False
        for case_id, case in case_by_id.items():
            for dependency_id in graph.get(case_id, []):
                dependency = case_by_id[dependency_id]
                if (dependency.get("readiness") or {}).get("state") != "executable":
                    changed = add_missing(
                        case,
                        f"dependencies.{dependency_id}.not_executable",
                    ) or changed
    return evaluated


def _revision_state(plan: Dict[str, Any]) -> Dict[str, Any]:
    asset_id = str(plan.get("asset_id") or "").strip()
    planned_revision_id = str(plan.get("asset_revision_id") or "").strip()
    if not asset_id or not planned_revision_id:
        return {
            "state": "unversioned",
            "planned_revision_id": planned_revision_id,
            "active_revision_id": "",
            "affected_case_ids": [],
        }
    asset = api_asset_service.get_api_asset(asset_id)
    active_revision_id = str(asset.get("active_revision_id") or "").strip() if asset else ""
    base = {
        "state": "fresh",
        "planned_revision_id": planned_revision_id,
        "active_revision_id": active_revision_id,
        "affected_case_ids": [],
    }
    if active_revision_id == planned_revision_id:
        return base
    old_revision = api_asset_service.get_api_revision(planned_revision_id)
    new_revision = api_asset_service.get_api_revision(active_revision_id)
    if not old_revision or not new_revision:
        return {
            **base,
            "state": "stale",
            "reason": "计划绑定版本或当前活动版本不可用",
            "affected_case_ids": sorted({
                str(case.get("case_id") or "").strip()
                for case in (plan.get("cases") or [])
                if isinstance(case, dict) and str(case.get("case_id") or "").strip()
            }),
        }
    diff = api_schema_diff_service.compare_api_revisions(old_revision, new_revision)
    impact = api_schema_diff_service.analyze_api_plan_impact(diff, [plan])
    affected_case_ids = impact.get("affected_case_ids") or []
    unresolved = impact.get("unresolved_legacy_plan_ids") or []
    if affected_case_ids or str(plan.get("plan_id") or "") in unresolved:
        return {
            **base,
            "state": "stale",
            "reason": "所选接口在当前活动版本中已变更或删除",
            "affected_case_ids": sorted(set(affected_case_ids)),
            "diff_summary": diff.get("summary") or {},
        }
    return {**base, "diff_summary": diff.get("summary") or {}}


def evaluate_api_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    evaluated = copy.deepcopy(plan) if isinstance(plan, dict) else {}
    cases = _evaluated_cases(evaluated)
    evaluated["cases"] = cases
    evaluated["case_count"] = len(cases)
    readiness = api_case_contract_service.summarize_api_case_readiness(cases)
    revision_state = _revision_state(evaluated)
    is_stale = revision_state.get("state") == "stale"
    readiness["can_confirm"] = bool(readiness.get("executable_case_count")) and not is_stale
    readiness["can_execute"] = (
        evaluated.get("status") == "confirmed"
        and bool(readiness.get("executable_case_count"))
        and not is_stale
    )
    if is_stale:
        readiness["state"] = "stale"
    all_cases_use_contract = bool(cases) and all(
        case.get("contract_version") == api_case_contract_service.CONTRACT_VERSION
        for case in cases
    )
    evaluated["contract_version"] = (
        api_case_contract_service.CONTRACT_VERSION if all_cases_use_contract else "legacy"
    )
    evaluated["executable_case_count"] = readiness.get("executable_case_count", 0)
    evaluated["needs_review_case_count"] = readiness.get("needs_review_case_count", 0)
    evaluated["execution_readiness"] = readiness
    evaluated["revision_state"] = revision_state
    return evaluated


def executable_api_cases(plan: Dict[str, Any]) -> List[Dict[str, Any]]:
    evaluated = evaluate_api_plan(plan)
    executable = [
        case
        for case in (evaluated.get("cases") or [])
        if isinstance(case, dict) and (case.get("readiness") or {}).get("state") == "executable"
    ]
    case_by_id = {
        str(case.get("case_id") or "").strip(): case
        for case in executable
        if str(case.get("case_id") or "").strip()
    }
    ordered: List[Dict[str, Any]] = []
    visited: set[str] = set()

    def append_case(case: Dict[str, Any]) -> None:
        case_id = str(case.get("case_id") or "").strip()
        if not case_id or case_id in visited:
            return
        visited.add(case_id)
        for dependency in case.get("dependencies") or []:
            if not isinstance(dependency, dict) or not dependency.get("required", True):
                continue
            dependency_case = case_by_id.get(str(dependency.get("case_id") or "").strip())
            if dependency_case:
                append_case(dependency_case)
        ordered.append(case)

    for case in executable:
        append_case(case)
    return ordered


def generate_api_test_plan(snapshot_id: str, endpoint_ids: List[str] | None, model_config: Dict[str, Any] | None = None, use_ai: bool | None = None) -> Dict[str, Any]:
    snapshot = api_asset_service.get_api_snapshot(snapshot_id)
    if not snapshot:
        raise ValueError("API snapshot 不存在，请先导入 OpenAPI")
    endpoints = _selected_endpoints(snapshot.get("snapshot_id"), endpoint_ids or [])
    if not endpoints:
        raise ValueError("未选择可生成用例的接口")
    source_id = str(snapshot.get("source_id") or "").strip()
    auth_binding = api_workspace_service.get_api_auth_binding(source_id) if source_id else {}
    plan_id = unique_millis_id("api_plan")
    local_cases = _local_plan_cases(endpoints, plan_id)
    ai_enabled = safe_bool(os.getenv("API_TESTING_AI_ENABLED", "0"), False) if use_ai is None else bool(use_ai)
    selected_cases = local_cases
    ai_meta: Dict[str, Any] = {"enabled": ai_enabled, "used": False, "fallback_reason": ""}
    source = "local"
    if ai_enabled:
        ai_result = _ai_cases(snapshot, endpoints, local_cases, plan_id, model_config)
        ai_meta.update({
            "used": bool(ai_result.get("ok")),
            "fallback_reason": "" if ai_result.get("ok") else str(ai_result.get("error") or "AI 生成失败"),
            "trace": ai_result.get("trace") or {},
            "decision_trace": ai_result.get("decision_trace") or {},
            "review": ai_result.get("review") or {},
        })
        if ai_result.get("ok"):
            selected_cases = ai_result.get("cases") or local_cases
            source = "ai"
        else:
            source = "local_fallback"
    plan = {
        "plan_id": plan_id,
        "snapshot_id": snapshot.get("snapshot_id"),
        "asset_id": snapshot.get("asset_id"),
        "asset_revision_id": snapshot.get("revision_id") or snapshot.get("asset_revision_id") or snapshot.get("snapshot_id"),
        "source_id": source_id,
        "name": f"{snapshot.get('title') or snapshot.get('name') or 'API'} 接口测试计划",
        "status": "draft",
        "source": source,
        "created_at": _now(),
        "confirmed_at": "",
        "endpoint_count": len(endpoints),
        "case_count": len(selected_cases),
        "endpoints": endpoints,
        "cases": selected_cases,
        "ai": ai_meta,
        "auth_binding": auth_binding,
        "binding_fingerprint": str(auth_binding.get("binding_fingerprint") or ""),
    }
    plan = evaluate_api_plan(plan)
    write_json_file(_plan_path(plan_id), plan)
    _save_plan_index(plan)
    return plan


def _read_api_test_plan(plan_id: str) -> Dict[str, Any]:
    plan = read_json_file(_plan_path(plan_id), default={}) or {}
    return plan if isinstance(plan, dict) else {}


def get_api_test_plan(plan_id: str) -> Dict[str, Any]:
    plan = _read_api_test_plan(plan_id)
    return evaluate_api_plan(plan) if plan else {}


def confirm_api_test_plan(plan_id: str) -> Dict[str, Any]:
    plan = _read_api_test_plan(plan_id)
    if not plan:
        raise ValueError("API 测试计划不存在")
    evaluated = evaluate_api_plan(plan)
    if (evaluated.get("revision_state") or {}).get("state") == "stale":
        raise ValueError("API 测试计划已过期，请按当前接口版本重新生成")
    if not (evaluated.get("execution_readiness") or {}).get("can_confirm"):
        raise ValueError("API 测试计划没有可执行用例，请先补齐必填测试数据")
    plan["status"] = "confirmed"
    plan["confirmed_at"] = _now()
    write_json_file(_plan_path(plan_id), plan)
    confirmed = evaluate_api_plan(plan)
    _save_plan_index(confirmed)
    return confirmed


def list_api_test_plans(limit: int = 20) -> List[Dict[str, Any]]:
    index = read_json_file(_index_path(), default=[]) or []
    if not isinstance(index, list):
        return []
    try:
        size = max(1, int(limit))
    except Exception:
        size = 20
    result: List[Dict[str, Any]] = []
    for item in index[:size]:
        if not isinstance(item, dict):
            continue
        plan = get_api_test_plan(str(item.get("plan_id") or ""))
        if not plan:
            continue
        result.append({
            key: plan.get(key)
            for key in (
                "plan_id", "snapshot_id", "asset_id", "asset_revision_id", "name",
                "status", "case_count", "endpoint_count", "source", "created_at",
                "confirmed_at", "contract_version", "executable_case_count",
                "needs_review_case_count", "execution_readiness", "revision_state",
            )
        })
    return result


def list_full_api_test_plans(limit: int = 1000) -> List[Dict[str, Any]]:
    index = read_json_file(_index_path(), default=[]) or []
    if not isinstance(index, list):
        return []
    try:
        size = max(1, int(limit))
    except Exception:
        size = 1000
    return [
        plan
        for item in index[:size]
        if isinstance(item, dict)
        for plan in [get_api_test_plan(str(item.get("plan_id") or ""))]
        if plan
    ]


__all__ = [
    "API_TESTING_DIR",
    "evaluate_api_plan",
    "executable_api_cases",
    "generate_api_test_plan",
    "confirm_api_test_plan",
    "get_api_test_plan",
    "list_api_test_plans",
    "list_full_api_test_plans",
]
