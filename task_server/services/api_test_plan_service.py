"""AI-assisted API test plan drafts for imported API assets."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List

from task_server.config import safe_bool
from task_server.storage import clean_id, read_json_file, safe_join, unique_millis_id, write_json_file
from task_server.services import api_asset_service
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
        "name": plan.get("name"),
        "status": plan.get("status"),
        "case_count": plan.get("case_count"),
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


def _response_assertions(endpoint: Dict[str, Any]) -> List[str]:
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


def _case(case_id: str, endpoint: Dict[str, Any], name: str, case_type: str, priority: str, steps: List[str], assertions: List[str]) -> Dict[str, Any]:
    return {
        "case_id": case_id,
        "endpoint_id": endpoint.get("endpoint_id"),
        "endpoint": f"{endpoint.get('method')} {endpoint.get('path')}",
        "module": endpoint.get("module") or "未分组",
        "name": name,
        "type": case_type,
        "priority": priority,
        "steps": steps,
        "assertions": assertions,
        "status": "draft",
    }


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
            _response_assertions(endpoint),
        )
    ]
    required_fields = endpoint.get("required_fields") or []
    for index, field in enumerate(required_fields[:3], start=1):
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
        ))
    if endpoint.get("security"):
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
    for index, item in enumerate(raw_cases, start=1):
        if not isinstance(item, dict):
            continue
        endpoint_id = str(item.get("endpoint_id") or "").strip()
        endpoint = endpoint_ids.get(endpoint_id) or (endpoints[0] if endpoints else {})
        name = str(item.get("name") or "").strip()
        steps = [str(step).strip() for step in (item.get("steps") or []) if str(step).strip()]
        assertions = [str(assertion).strip() for assertion in (item.get("assertions") or []) if str(assertion).strip()]
        if not name or not steps or not assertions:
            continue
        case_type = str(item.get("type") or "positive").strip().lower()
        if case_type not in {"positive", "negative", "auth", "boundary", "chain", "error"}:
            case_type = "positive"
        normalized.append(_case(
            str(item.get("case_id") or f"API-AI-{index:03d}").strip(),
            endpoint,
            name,
            case_type,
            str(item.get("priority") or "P1").strip() or "P1",
            steps,
            assertions,
        ))
        normalized[-1]["plan_case_key"] = f"{plan_id}:{normalized[-1]['case_id']}"
    return normalized


def _ai_cases(snapshot: Dict[str, Any], endpoints: List[Dict[str, Any]], local_cases: List[Dict[str, Any]], plan_id: str, model_config: Dict[str, Any] | None) -> Dict[str, Any]:
    trace: Dict[str, Any] = {}
    try:
        result = run_ai_skill(
            "api_test_designer",
            {
                "snapshot": {
                    "snapshot_id": snapshot.get("snapshot_id"),
                    "title": snapshot.get("title"),
                    "version": snapshot.get("version"),
                },
                "endpoints": endpoints,
                "seed_cases": local_cases,
            },
            model_config=model_config,
            runtime_trace=trace,
            repair_invalid_json=True,
            output_defaults={"cases": [], "review": {}},
            timeout=180,
        )
        cases = _normalize_ai_cases(result.get("cases"), endpoints, plan_id)
        if cases:
            return {"ok": True, "cases": cases, "review": result.get("review") or {}, "trace": trace}
        return {"ok": False, "error": "AI 未返回有效接口用例", "trace": trace}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "trace": trace}


def generate_api_test_plan(snapshot_id: str, endpoint_ids: List[str] | None, model_config: Dict[str, Any] | None = None, use_ai: bool | None = None) -> Dict[str, Any]:
    snapshot = api_asset_service.get_api_snapshot(snapshot_id)
    if not snapshot:
        raise ValueError("API snapshot 不存在，请先导入 OpenAPI")
    endpoints = _selected_endpoints(snapshot.get("snapshot_id"), endpoint_ids or [])
    if not endpoints:
        raise ValueError("未选择可生成用例的接口")
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
    }
    write_json_file(_plan_path(plan_id), plan)
    _save_plan_index(plan)
    return plan


def get_api_test_plan(plan_id: str) -> Dict[str, Any]:
    plan = read_json_file(_plan_path(plan_id), default={}) or {}
    return plan if isinstance(plan, dict) else {}


def confirm_api_test_plan(plan_id: str) -> Dict[str, Any]:
    plan = get_api_test_plan(plan_id)
    if not plan:
        raise ValueError("API 测试计划不存在")
    plan["status"] = "confirmed"
    plan["confirmed_at"] = _now()
    write_json_file(_plan_path(plan_id), plan)
    _save_plan_index(plan)
    return plan


def list_api_test_plans(limit: int = 20) -> List[Dict[str, Any]]:
    index = read_json_file(_index_path(), default=[]) or []
    if not isinstance(index, list):
        return []
    try:
        size = max(1, int(limit))
    except Exception:
        size = 20
    return index[:size]


__all__ = [
    "API_TESTING_DIR",
    "generate_api_test_plan",
    "confirm_api_test_plan",
    "get_api_test_plan",
    "list_api_test_plans",
]
