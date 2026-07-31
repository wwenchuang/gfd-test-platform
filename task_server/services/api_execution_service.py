"""Native API execution runner for platform-owned API tests."""

from __future__ import annotations

import copy
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List

from task_server.storage import clean_id, read_json_file, safe_join, unique_millis_id, write_json_file
from task_server.services import (
    api_asset_service,
    api_case_contract_service,
    api_report_service,
    api_source_service,
    api_test_plan_service,
    api_workspace_service,
)


API_TESTING_DIR = api_asset_service.API_TESTING_DIR
TERMINAL_EXECUTION_STATES = {"succeeded", "failed", "cancelled"}
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ApiExecutionConflict(ValueError):
    """Raised when a duplicate or conflicting execution is requested."""


class ApiExecutionValidationError(ValueError):
    """Raised when a plan or case cannot be executed by the native runner."""


class ApiExecutionNotFound(LookupError):
    """Raised when an execution record does not exist."""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _api_path(*parts: str) -> str:
    return safe_join(API_TESTING_DIR, *parts)


def _execution_path(execution_id: str) -> str:
    return _api_path("api-executions", f"{clean_id(execution_id, 'api_execution')}.json")


def _execution_index_path() -> str:
    return _api_path("api-executions", "index.json")


def _execution_index_item(execution: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "execution_id": execution.get("execution_id"),
        "run_id": execution.get("run_id"),
        "run_mode": execution.get("run_mode"),
        "plan_id": execution.get("plan_id"),
        "plan_name": execution.get("plan_name"),
        "source_id": execution.get("source_id"),
        "status": execution.get("status"),
        "report_status": execution.get("report_status"),
        "report_id": execution.get("report_id"),
        "current_phase": execution.get("current_phase"),
        "stats": execution.get("stats") or {},
        "created_at": execution.get("created_at"),
        "started_at": execution.get("started_at"),
        "updated_at": execution.get("updated_at"),
        "duration_seconds": execution.get("duration_seconds", 0),
        "error": execution.get("error", ""),
    }


def _save_execution_index(execution: Dict[str, Any]) -> None:
    index = read_json_file(_execution_index_path(), default=[]) or []
    if not isinstance(index, list):
        index = []
    item = _execution_index_item(execution)
    index = [row for row in index if row.get("execution_id") != item.get("execution_id")]
    index.insert(0, item)
    write_json_file(_execution_index_path(), index[:200])


def _save_execution(execution: Dict[str, Any]) -> Dict[str, Any]:
    execution["updated_at"] = _now()
    started = float(execution.get("_started_monotonic") or 0)
    if started:
        execution["duration_seconds"] = max(0, int(time.monotonic() - started))
    public = api_case_contract_service.sanitize_sensitive_data(execution)
    write_json_file(_execution_path(str(execution.get("execution_id") or "")), public)
    _save_execution_index(public)
    return public


def _append_event(
    execution: Dict[str, Any],
    phase_id: str,
    summary: str,
    detail: Any = None,
    *,
    status: str = "running",
) -> None:
    events = execution.setdefault("events", [])
    events.append(api_case_contract_service.sanitize_sensitive_data({
        "event_id": unique_millis_id("api_event"),
        "execution_id": execution.get("execution_id"),
        "run_id": execution.get("run_id"),
        "phase_id": phase_id,
        "status": status,
        "timestamp": _now(),
        "summary": summary,
        "detail": detail,
    }))
    execution["current_phase"] = phase_id
    _save_execution(execution)


def _read_execution(execution_id: str) -> Dict[str, Any]:
    execution = read_json_file(_execution_path(execution_id), default={}) or {}
    return execution if isinstance(execution, dict) else {}


def list_api_executions(limit: int = 20, source_id: str = "") -> List[Dict[str, Any]]:
    index = read_json_file(_execution_index_path(), default=[]) or []
    if not isinstance(index, list):
        return []
    selected_source = str(source_id or "").strip()
    rows = [
        row for row in index
        if isinstance(row, dict)
        and (not selected_source or str(row.get("source_id") or "") == selected_source)
    ]
    return rows[:max(1, int(limit or 20))]


def get_api_execution(execution_id: str) -> Dict[str, Any]:
    execution = _read_execution(execution_id)
    if not execution:
        raise ApiExecutionNotFound("API 执行记录不存在")
    return execution


def _source_base_urls(source: Dict[str, Any]) -> List[Dict[str, str]]:
    snapshot = source.get("environment_snapshot") if isinstance(source.get("environment_snapshot"), dict) else {}
    base_urls = snapshot.get("base_urls") if isinstance(snapshot.get("base_urls"), list) else []
    rows = []
    for index, item in enumerate(base_urls, start=1):
        raw = item if isinstance(item, dict) else {}
        url = str(raw.get("url") or raw.get("value") or "").strip().rstrip("/")
        if not url:
            continue
        rows.append({
            "id": str(raw.get("name") or f"baseUrl{index}").strip(),
            "name": str(raw.get("name") or f"服务地址 {index}").strip(),
            "url": url,
            "enabled": True,
        })
    return rows


def _selected_environment(source: Dict[str, Any], binding: Dict[str, Any]) -> Dict[str, str]:
    environments = _source_base_urls(source)
    selected_id = str(binding.get("environment_id") or source.get("environment_id") or "").strip()
    if selected_id:
        for item in environments:
            if str(item.get("id") or "") == selected_id:
                return item
    if environments:
        return environments[0]
    return {}


def _selected_base_url(source: Dict[str, Any], binding: Dict[str, Any]) -> str:
    environment = _selected_environment(source, binding)
    return str(environment.get("url") or "").strip().rstrip("/")


def _plan_latest_run(plan_id: str, source_id: str = "") -> Dict[str, Any]:
    for execution in list_api_executions(limit=100, source_id=source_id):
        if str(execution.get("plan_id") or "") == str(plan_id or ""):
            return execution
    return {}


def api_execution_context(source_id: str = "", force: bool = False) -> Dict[str, Any]:
    selected_source_id = str(source_id or "").strip()
    source = api_source_service.get_api_source(selected_source_id, masked=True) if selected_source_id else {}
    binding = api_workspace_service.get_api_workspace_binding(selected_source_id, allow_legacy=False) if selected_source_id else {}
    auth_binding = api_workspace_service.get_api_auth_binding(selected_source_id) if selected_source_id else {}
    plans = [
        plan for plan in api_test_plan_service.list_api_test_plans(limit=50, source_id=selected_source_id)
        if plan.get("status") == "confirmed"
    ]
    executions = list_api_executions(limit=50, source_id=selected_source_id)
    active_runs = [item for item in executions if str(item.get("status") or "") not in TERMINAL_EXECUTION_STATES]
    recent_runs = [item for item in executions if str(item.get("status") or "") in TERMINAL_EXECUTION_STATES][:20]
    base_url = _selected_base_url(source, binding)
    for plan in plans:
        latest = _plan_latest_run(str(plan.get("plan_id") or ""), selected_source_id)
        active = next((item for item in active_runs if item.get("plan_id") == plan.get("plan_id")), {})
        plan["latest_run"] = latest
        plan["active_run"] = active
        plan["can_execute"] = bool(base_url) and bool((plan.get("execution_readiness") or {}).get("can_execute")) and not active
    missing = []
    if not selected_source_id or not source:
        missing.append("api_source")
    if not base_url:
        missing.append("base_url")
    needs_auth = any((plan.get("auth_binding") or {}) for plan in plans)
    if needs_auth and not auth_binding:
        missing.append("business_auth")
    if active_runs:
        state = "running"
        primary = "查看实时进度"
    elif missing:
        state = "connected_needs_setup"
        primary = "补齐执行环境"
    elif plans:
        state = "ready"
        primary = "执行测试"
    else:
        state = "ready_no_plan"
        primary = "生成并采纳 API 基线"
    empty_reason = ""
    if not source:
        empty_reason = "no_assets"
    elif not plans:
        all_plans = api_test_plan_service.list_api_test_plans(limit=20, source_id=selected_source_id)
        empty_reason = "unconfirmed_plans" if all_plans else "no_plans"
    elif not any(plan.get("executable_case_count") for plan in plans):
        empty_reason = "no_executable_plans"
    elif missing:
        empty_reason = "not_ready"
    environments = _source_base_urls(source)
    selection_environment = _selected_environment(source, binding)
    project_id = str(binding.get("project_id") or source.get("project_id") or selected_source_id).strip()
    project_name = (
        str(binding.get("project_name") or "").strip()
        or str((source.get("provider_metadata") or {}).get("project_name") or "").strip()
        or str(source.get("name") or selected_source_id).strip()
    )
    return {
        "ok": True,
        "provider": "native_api",
        "source_id": selected_source_id,
        "source": source,
        "binding": binding,
        "auth_binding": auth_binding,
        "businesses": [{"id": project_id, "name": project_name, "enabled": True}] if project_id else [],
        "environments": environments,
        "selection": {
            "project_id": project_id,
            "environment_id": str(selection_environment.get("id") or binding.get("environment_id") or "").strip(),
        },
        "connection": {
            "state": "connected" if base_url else "disconnected",
            "base_url": base_url,
            "checked_at": _now(),
            "latency_ms": 0,
        },
        "readiness": {
            "state": state,
            "can_execute": bool(plans) and not missing and not active_runs,
            "missing": missing,
            "primary_action": primary,
        },
        "metadata": {"stale": False, "source": "platform"},
        "plans": plans,
        "active_runs": active_runs,
        "recent_runs": recent_runs,
        "empty_reason": empty_reason,
        "poll_after_ms": 2000,
    }


def _resolve_path(path: str, path_params: Dict[str, Any]) -> str:
    result = str(path or "")
    for key, value in (path_params or {}).items():
        encoded = urllib.parse.quote(str(value), safe="")
        result = result.replace("{" + str(key) + "}", encoded)
    return result


def _build_url(base_url: str, request: Dict[str, Any]) -> str:
    path = _resolve_path(str(request.get("path") or ""), request.get("path_params") or {})
    url = f"{base_url.rstrip('/')}/{path.lstrip('/')}"
    query = request.get("query") if isinstance(request.get("query"), dict) else {}
    if query:
        url = f"{url}?{urllib.parse.urlencode(query, doseq=True)}"
    return url


def _short_api_url(url: Any) -> str:
    try:
        parsed = urllib.parse.urlparse(str(url or ""))
    except Exception:
        return str(url or "")
    if parsed.path:
        return f"{parsed.path}{('?' + parsed.query) if parsed.query else ''}"
    return str(url or "")


def _request_body(request: Dict[str, Any], headers: Dict[str, str]) -> bytes | None:
    method = str(request.get("method") or "GET").upper()
    if method in {"GET", "HEAD"}:
        return None
    body = request.get("body")
    if body in (None, ""):
        return None
    if isinstance(body, (dict, list)):
        headers.setdefault("Content-Type", "application/json; charset=utf-8")
        return json.dumps(body, ensure_ascii=False).encode("utf-8")
    if isinstance(body, str):
        return body.encode("utf-8")
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def _public_headers(headers: Dict[str, str]) -> Dict[str, str]:
    public: Dict[str, str] = {}
    for key, value in (headers or {}).items():
        name = str(key or "").strip()
        if not name:
            continue
        public[name] = "Bearer ***" if api_case_contract_service.is_sensitive_header_name(name) else str(value)
    return public


def _request_log_detail(source_id: str, base_url: str, case: Dict[str, Any]) -> Dict[str, Any]:
    request = copy.deepcopy(case.get("request") or {})
    method = str(request.get("method") or "GET").upper()
    headers = {
        str(key): str(value)
        for key, value in (request.get("headers") or {}).items()
        if str(key or "").strip()
    }
    if str(request.get("auth_ref") or "").strip():
        auth = api_workspace_service.get_api_auth_secret(source_id)
        header_name = str(auth.get("header_name") or "Authorization").strip()
        if header_name:
            headers.setdefault(header_name, "Bearer ***")
    url = _build_url(base_url, request)
    has_auth = any(api_case_contract_service.is_sensitive_header_name(name) for name in headers)
    return api_case_contract_service.sanitize_sensitive_data({
        "case_id": case.get("case_id"),
        "name": case.get("name"),
        "endpoint": case.get("endpoint"),
        "auth_state": "Bearer ***" if has_auth else "未使用鉴权",
        "request": {
            "method": method,
            "url": url,
            "headers": _public_headers(headers),
            "body": request.get("body"),
        },
    })


def _response_log_detail(result: Dict[str, Any]) -> Dict[str, Any]:
    response = result.get("response") if isinstance(result.get("response"), dict) else {}
    request = result.get("request") if isinstance(result.get("request"), dict) else {}
    return api_case_contract_service.sanitize_sensitive_data({
        "case_id": result.get("case_id"),
        "name": result.get("name"),
        "status": result.get("status"),
        "duration_ms": result.get("duration_ms", 0),
        "request": {
            "method": request.get("method"),
            "url": request.get("url"),
        },
        "response": {
            "status_code": response.get("status_code"),
            "headers": response.get("headers") or {},
            "body": response.get("body"),
        },
        "error": result.get("error", ""),
    })


def _assertion_log_detail(result: Dict[str, Any]) -> Dict[str, Any]:
    return api_case_contract_service.sanitize_sensitive_data({
        "case_id": result.get("case_id"),
        "name": result.get("name"),
        "status": result.get("status"),
        "assertions": result.get("assertions") or [],
        "error": result.get("error", ""),
    })


def _apply_auth(source_id: str, request: Dict[str, Any], headers: Dict[str, str]) -> None:
    if not str(request.get("auth_ref") or "").strip():
        return
    auth = api_workspace_service.get_api_auth_secret(source_id)
    if not auth.get("configured") or not auth.get("secret"):
        raise ApiExecutionValidationError("当前接口需要业务鉴权，请先配置业务 token")
    header_name = str(auth.get("header_name") or "Authorization").strip()
    secret = str(auth.get("secret") or "")
    if str(auth.get("auth_type") or "bearer") == "bearer":
        headers[header_name] = secret if secret.lower().startswith("bearer ") else f"Bearer {secret}"
    else:
        headers[header_name] = secret


def _status_expected(assertion: Dict[str, Any], status_code: int) -> bool:
    expected = assertion.get("expected")
    values = expected if isinstance(expected, list) else [expected]
    for value in values:
        if isinstance(value, int) and status_code == value:
            return True
        text = str(value or "").upper()
        if text == "2XX" and 200 <= status_code < 300:
            return True
        if text == "4XX" and 400 <= status_code < 500:
            return True
    return False


def _assertions(case: Dict[str, Any], status_code: int, parsed_body: Any, parse_error: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for assertion in case.get("assertions") or []:
        if not isinstance(assertion, dict):
            continue
        kind = str(assertion.get("type") or "").strip()
        passed = True
        message = ""
        if kind == "status":
            passed = _status_expected(assertion, status_code)
            message = f"HTTP {status_code}"
        elif kind == "schema":
            passed = not parse_error and isinstance(parsed_body, (dict, list))
            message = parse_error or "响应 JSON 可解析"
        else:
            message = f"暂不支持断言类型 {kind}"
            passed = False
        rows.append({"type": kind, "passed": passed, "message": message})
    return rows


def _execute_case(source_id: str, base_url: str, case: Dict[str, Any]) -> Dict[str, Any]:
    request = copy.deepcopy(case.get("request") or {})
    method = str(request.get("method") or "GET").upper()
    headers = {
        str(key): str(value)
        for key, value in (request.get("headers") or {}).items()
        if str(key or "").strip()
    }
    _apply_auth(source_id, request, headers)
    url = _build_url(base_url, request)
    body = _request_body(request, headers)
    started = time.monotonic()
    status_code = 0
    response_headers: Dict[str, str] = {}
    response_text = ""
    error = ""
    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=30) as response:
            status_code = int(response.status)
            response_headers = dict(response.headers.items())
            response_text = response.read(MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        status_code = int(exc.code)
        response_headers = dict(exc.headers.items()) if exc.headers else {}
        response_text = exc.read(MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
    except Exception as exc:
        error = str(exc)
    duration_ms = max(0, int((time.monotonic() - started) * 1000))
    parsed_body: Any = None
    parse_error = ""
    if response_text:
        try:
            parsed_body = json.loads(response_text)
        except Exception:
            parse_error = "响应不是 JSON"
    assertion_rows = _assertions(case, status_code, parsed_body, parse_error)
    passed = bool(not error and assertion_rows and all(row.get("passed") for row in assertion_rows))
    if not assertion_rows and not error:
        passed = 200 <= status_code < 300
    result = {
        "case_id": case.get("case_id"),
        "name": case.get("name"),
        "endpoint": case.get("endpoint"),
        "status": "passed" if passed else "failed",
        "duration_ms": duration_ms,
        "error": error,
        "request": {
            "method": method,
            "url": url,
            "headers": headers,
            "body": request.get("body"),
        },
        "response": {
            "status_code": status_code,
            "headers": response_headers,
            "body": parsed_body if parsed_body is not None else response_text[:5000],
        },
        "assertions": assertion_rows,
    }
    if not passed and not error:
        result["error"] = "; ".join(row.get("message", "") for row in assertion_rows if not row.get("passed")) or f"HTTP {status_code}"
    return api_case_contract_service.sanitize_sensitive_data(result)


def _phases() -> List[Dict[str, Any]]:
    return [
        {"id": "prepare", "title": "准备环境", "state": "waiting", "summary": ""},
        {"id": "execute", "title": "执行接口", "state": "waiting", "summary": ""},
        {"id": "assert", "title": "校验断言", "state": "waiting", "summary": ""},
        {"id": "report", "title": "生成报告", "state": "waiting", "summary": ""},
    ]


def _set_phase(execution: Dict[str, Any], phase_id: str, state: str, summary: str) -> None:
    for phase in execution.get("phases") or []:
        if phase.get("id") == phase_id:
            phase["state"] = state
            phase["summary"] = summary
            phase["updated_at"] = _now()
            if state == "running" and not phase.get("started_at"):
                phase["started_at"] = _now()
            break
    execution["current_phase"] = phase_id
    _save_execution(execution)


def _execution_report(execution: Dict[str, Any]) -> Dict[str, Any]:
    results = execution.get("results") if isinstance(execution.get("results"), list) else []
    passed = len([item for item in results if item.get("status") == "passed"])
    failed = len([item for item in results if item.get("status") == "failed"])
    skipped = len([item for item in results if item.get("status") == "skipped"])
    total = len(results)
    binding = execution.get("binding") if isinstance(execution.get("binding"), dict) else {}
    failures: List[Dict[str, Any]] = []
    normalized_results: List[Dict[str, Any]] = []
    for item in results:
        failure_meta = {} if item.get("status") == "passed" else api_report_service.classify_api_failure(item)
        analysis = {}
        if failure_meta:
            response = item.get("response") if isinstance(item.get("response"), dict) else {}
            request = item.get("request") if isinstance(item.get("request"), dict) else {}
            assertion_failures = [
                row for row in (item.get("assertions") or [])
                if isinstance(row, dict) and not row.get("passed")
            ]
            evidence = [
                value for value in [
                    f"HTTP {response.get('status_code')}" if response.get("status_code") else "",
                    str(item.get("error") or "").strip(),
                    f"{request.get('method', '')} {request.get('url', '')}".strip(),
                ]
                if value
            ]
            suggestions = [
                value for value in [
                    failure_meta.get("suggestion"),
                    "确认 Apifox 环境 base_url、业务 token 和测试数据是否与当前环境一致。",
                    "必要时先单条调试该用例，再决定是否采纳或修正基线。",
                ]
                if value
            ]
            analysis = {
                **failure_meta,
                "summary": str(item.get("error") or failure_meta.get("failure_type") or "接口校验失败"),
                "evidence": evidence,
                "suggestions": suggestions,
                "assertion_failures": assertion_failures,
            }
            failures.append({
                "case_id": item.get("case_id"),
                "name": item.get("name"),
                "endpoint": item.get("endpoint"),
                "failure_type": failure_meta.get("failure_type"),
                "summary": analysis.get("summary"),
                "suggestion": failure_meta.get("suggestion"),
            })
        normalized_results.append({
            "case_id": item.get("case_id"),
            "name": item.get("name"),
            "endpoint": item.get("endpoint"),
            "status": item.get("status"),
            "duration_ms": item.get("duration_ms", 0),
            "error": item.get("error", ""),
            "request": item.get("request") or {},
            "response": item.get("response") or {},
            "assertions": item.get("assertions") or [],
            "analysis": analysis,
        })
    failure_types: Dict[str, int] = {}
    for item in failures:
        key = str(item.get("failure_type") or "UNKNOWN")
        failure_types[key] = failure_types.get(key, 0) + 1
    return {
        "report_id": unique_millis_id("api_report"),
        "run_id": execution.get("run_id"),
        "execution_id": execution.get("execution_id"),
        "plan_id": execution.get("plan_id"),
        "source_id": execution.get("source_id"),
        "binding_id": binding.get("binding_id", ""),
        "binding_fingerprint": binding.get("config_fingerprint", ""),
        "project_id": binding.get("project_id", ""),
        "project_name": binding.get("project_name", ""),
        "environment_id": binding.get("environment_id", ""),
        "environment_name": binding.get("environment_name", ""),
        "environment": {
            "base_url": execution.get("base_url", ""),
            "project_id": binding.get("project_id", ""),
            "project_name": binding.get("project_name", ""),
            "environment_id": binding.get("environment_id", ""),
            "environment_name": binding.get("environment_name", ""),
            "auth_variable": ((binding.get("auth_binding") or {}) if isinstance(binding.get("auth_binding"), dict) else {}).get("variable_name", ""),
        },
        "status": "passed" if results and failed == 0 else "failed",
        "created_at": _now(),
        "summary": {
            "total": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "pass_rate": round((passed / total) * 100, 1) if total else 0,
            "duration_seconds": execution.get("duration_seconds", 0),
        },
        "failure_analysis": {
            "total_failed": failed,
            "by_type": failure_types,
            "items": failures,
        },
        "results": normalized_results,
        "raw": {"execution_id": execution.get("execution_id"), "results": results},
    }


def _run_execution(execution_id: str) -> None:
    execution = _read_execution(execution_id)
    if not execution:
        return
    try:
        execution["_started_monotonic"] = time.monotonic()
        execution["status"] = "running"
        execution["started_at"] = _now()
        _set_phase(execution, "prepare", "running", "读取计划、环境和鉴权")
        plan = execution.get("plan_snapshot") if isinstance(execution.get("plan_snapshot"), dict) else {}
        source_id = str(execution.get("source_id") or plan.get("source_id") or "").strip()
        source = api_source_service.get_api_source(source_id, masked=True)
        binding = api_workspace_service.get_api_workspace_binding(source_id, allow_legacy=False)
        base_url = _selected_base_url(source, binding)
        if not base_url:
            raise ApiExecutionValidationError("当前 Apifox 环境没有可执行 base_url")
        execution["binding"] = binding
        execution["base_url"] = base_url
        _append_event(execution, "prepare", f"当前环境 {base_url}", {"source_id": source_id})
        _set_phase(execution, "prepare", "succeeded", "环境准备完成")
        _set_phase(execution, "execute", "running", "开始执行接口用例")
        cases = execution.get("cases") if isinstance(execution.get("cases"), list) else []
        results: List[Dict[str, Any]] = []
        for index, case in enumerate(cases, start=1):
            _append_event(execution, "execute", f"[{index}/{len(cases)}] {case.get('name') or case.get('case_id')} 开始", {"case_id": case.get("case_id")})
            request_detail = _request_log_detail(source_id, base_url, case)
            request = request_detail.get("request") if isinstance(request_detail.get("request"), dict) else {}
            _append_event(
                execution,
                "execute",
                f"[{index}/{len(cases)}] 发送请求 {request.get('method') or '-'} {_short_api_url(request.get('url') or '')}",
                request_detail,
            )
            result = _execute_case(source_id, base_url, case)
            results.append(result)
            execution["results"] = results
            execution["stats"] = {
                "total": len(cases),
                "passed": len([item for item in results if item.get("status") == "passed"]),
                "failed": len([item for item in results if item.get("status") == "failed"]),
                "completed": len(results),
            }
            response = result.get("response") if isinstance(result.get("response"), dict) else {}
            _append_event(
                execution,
                "execute",
                f"[{index}/{len(cases)}] 收到响应 HTTP {response.get('status_code') or '-'} · {result.get('duration_ms', 0)}ms",
                _response_log_detail(result),
                status=result.get("status") or "running",
            )
            assertion_summary = "断言通过" if result.get("status") == "passed" else "断言失败"
            _append_event(
                execution,
                "assert",
                f"[{index}/{len(cases)}] {assertion_summary}：{result.get('name') or result.get('case_id')}",
                _assertion_log_detail(result),
                status=result.get("status") or "running",
            )
        _set_phase(execution, "execute", "succeeded", "接口请求执行完成")
        failed = len([item for item in results if item.get("status") == "failed"])
        _set_phase(execution, "assert", "failed" if failed else "succeeded", f"{len(results) - failed} 通过 / {failed} 失败")
        _set_phase(execution, "report", "running", "生成平台 API 报告")
        _append_event(execution, "report", "生成报告：汇总执行结果与失败分析", {"total": len(results), "failed": failed})
        report = api_report_service.save_api_report(_execution_report(execution))
        execution["report_id"] = report.get("report_id")
        execution["report_status"] = report.get("status")
        execution["status"] = "failed" if failed else "succeeded"
        execution["finished_at"] = _now()
        _set_phase(execution, "report", "succeeded", f"报告 {report.get('report_id')}")
        _append_event(execution, "report", f"任务完成，通过 {len(results) - failed}，失败 {failed}", {"report_id": report.get("report_id")}, status=execution["status"])
    except Exception as exc:
        execution["status"] = "failed"
        execution["error"] = str(exc)
        execution["finished_at"] = _now()
        for phase in execution.get("phases") or []:
            if phase.get("id") == execution.get("current_phase"):
                phase["state"] = "failed"
                phase["summary"] = str(exc)
        _append_event(execution, execution.get("current_phase") or "execute", str(exc), {"error": str(exc)}, status="failed")
    finally:
        execution.pop("_started_monotonic", None)
        _save_execution(execution)


def _start_execution(plan: Dict[str, Any], cases: List[Dict[str, Any]], run_mode: str) -> Dict[str, Any]:
    if not cases:
        raise ApiExecutionValidationError("没有可执行 API 用例")
    source_id = str(plan.get("source_id") or "").strip()
    execution_id = unique_millis_id("api_execution")
    execution = {
        "execution_id": execution_id,
        "run_id": unique_millis_id("api_run"),
        "run_mode": run_mode,
        "provider": "native_api",
        "plan_id": plan.get("plan_id"),
        "plan_name": plan.get("name"),
        "source_id": source_id,
        "status": "queued",
        "report_status": "pending",
        "current_phase": "prepare",
        "created_at": _now(),
        "updated_at": _now(),
        "duration_seconds": 0,
        "stats": {"total": len(cases), "passed": 0, "failed": 0, "completed": 0},
        "phases": _phases(),
        "events": [],
        "cases": api_case_contract_service.sanitize_sensitive_data(cases),
        "plan_snapshot": api_case_contract_service.sanitize_sensitive_data(plan),
        "poll_after_ms": 1500,
    }
    _append_event(execution, "prepare", "API 执行已排队", {"case_count": len(cases)})
    thread = threading.Thread(target=_run_execution, args=(execution_id,), daemon=True)
    thread.start()
    return _read_execution(execution_id)


def start_api_execution(plan_id: str) -> Dict[str, Any]:
    plan = api_test_plan_service.get_api_test_plan(str(plan_id or "").strip())
    if not plan:
        raise ApiExecutionValidationError("API 基线不存在")
    if plan.get("status") != "confirmed":
        raise ApiExecutionValidationError("正式执行必须先采纳为 API 基线")
    cases = api_test_plan_service.executable_api_cases(plan)
    return _start_execution(plan, cases, "baseline")


def start_api_case_debug(plan_id: str, case_id: str) -> Dict[str, Any]:
    plan = api_test_plan_service.get_api_test_plan(str(plan_id or "").strip())
    if not plan:
        raise ApiExecutionValidationError("API 用例计划不存在")
    selected_case_id = str(case_id or "").strip()
    case = next((
        item for item in api_test_plan_service.executable_api_cases(plan)
        if str(item.get("case_id") or "") == selected_case_id
    ), {})
    if not case:
        raise ApiExecutionValidationError("该 draft 用例仍缺测试数据，不能单条调试")
    return _start_execution(plan, [case], "debug_case")


def _json_path_value(data: Any, path: str) -> Any:
    current = data
    for part in [item for item in str(path or "").strip().split(".") if item]:
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit():
            current = current[int(part)]
        else:
            return None
    return current


def _extract_business_login_token(data: Any, token_path: str = "") -> str:
    paths = [str(token_path or "").strip(), "data.token", "data.access_token", "data.accessToken", "token", "access_token", "accessToken"]
    for path in [item for item in paths if item]:
        value = _json_path_value(data, path)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError("登录接口未返回可识别的 token")


def _fetch_login_token(config: Dict[str, Any]) -> str:
    url = str(config.get("login_url") or config.get("loginUrl") or "").strip()
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("登录接口 URL 必须是 HTTP(S) 地址")
    method = str(config.get("method") or "POST").strip().upper()
    if method not in {"GET", "POST"}:
        raise ValueError("登录接口只支持 GET 或 POST")
    headers = {
        str(key): str(value)
        for key, value in (config.get("headers") or {}).items()
        if str(key or "").strip() and str(value or "").strip()
    }
    body = config.get("body") if "body" in config else {}
    data = None
    if method != "GET":
        headers.setdefault("Content-Type", "application/json; charset=utf-8")
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            text = response.read(MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise ValueError(f"登录接口返回 HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise ValueError("登录接口调用失败") from exc
    try:
        parsed_body = json.loads(text)
    except Exception as exc:
        raise ValueError("登录接口未返回 JSON") from exc
    return _extract_business_login_token(parsed_body, str(config.get("token_path") or config.get("tokenPath") or "data.token"))


def save_api_auth_binding(source_id: str, auth_type: str, header_name: str, secret: str) -> Dict[str, Any]:
    return api_workspace_service.save_api_auth_binding_metadata(
        source_id,
        auth_type=auth_type,
        header_name=header_name,
        secret_value=str(secret or ""),
    )


def save_api_auth_binding_from_login(source_id: str, login_config: Dict[str, Any]) -> Dict[str, Any]:
    token = _fetch_login_token(login_config if isinstance(login_config, dict) else {})
    return save_api_auth_binding(
        source_id,
        str((login_config or {}).get("auth_type") or (login_config or {}).get("authType") or "bearer"),
        str((login_config or {}).get("header_name") or (login_config or {}).get("headerName") or "Authorization"),
        token,
    )


__all__ = [
    "ApiExecutionConflict",
    "ApiExecutionNotFound",
    "ApiExecutionValidationError",
    "TERMINAL_EXECUTION_STATES",
    "api_execution_context",
    "get_api_execution",
    "list_api_executions",
    "save_api_auth_binding",
    "save_api_auth_binding_from_login",
    "start_api_case_debug",
    "start_api_execution",
]
