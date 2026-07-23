"""Unified API test report storage and lightweight failure classification."""

from __future__ import annotations

import os
import time
from typing import Any, Dict, List

from task_server.storage import clean_id, read_json_file, safe_join, unique_millis_id, write_json_file
from task_server.services import api_asset_service


API_TESTING_DIR = api_asset_service.API_TESTING_DIR


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _api_path(*parts: str) -> str:
    return safe_join(API_TESTING_DIR, *parts)


def _report_path(report_id: str) -> str:
    return _api_path("reports", f"{clean_id(report_id, 'api_report')}.json")


def _index_path() -> str:
    return _api_path("reports", "index.json")


def _report_index_item(report: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "report_id": report.get("report_id"),
        "run_id": report.get("run_id"),
        "plan_id": report.get("plan_id"),
        "source_id": report.get("source_id"),
        "execution_id": report.get("execution_id"),
        "binding_id": report.get("binding_id"),
        "binding_fingerprint": report.get("binding_fingerprint"),
        "project_id": report.get("project_id"),
        "environment_id": report.get("environment_id"),
        "status": report.get("status"),
        "total": report.get("summary", {}).get("total", 0),
        "passed": report.get("summary", {}).get("passed", 0),
        "failed": report.get("summary", {}).get("failed", 0),
        "created_at": report.get("created_at"),
    }


def _save_index(report: Dict[str, Any]) -> None:
    index = read_json_file(_index_path(), default=[]) or []
    if not isinstance(index, list):
        index = []
    item = _report_index_item(report)
    index = [row for row in index if row.get("report_id") != item.get("report_id")]
    index.insert(0, item)
    write_json_file(_index_path(), index[:100])


def _plan_source_id(plan_id: str) -> str:
    selected_plan_id = str(plan_id or "").strip()
    if not selected_plan_id:
        return ""
    from task_server.services import api_test_plan_service

    plan = api_test_plan_service.get_api_test_plan(selected_plan_id)
    return str((plan or {}).get("source_id") or "").strip()


def _report_source_ownership(report: Dict[str, Any]) -> tuple[str, bool, bool]:
    explicit_source_id = str(report.get("source_id") or "").strip()
    derived_source_id = _plan_source_id(str(report.get("plan_id") or ""))
    if explicit_source_id and derived_source_id and explicit_source_id != derived_source_id:
        return "", False, False
    if explicit_source_id:
        return explicit_source_id, True, False
    if derived_source_id:
        return derived_source_id, True, True
    return "", False, False


def classify_api_failure(item: Dict[str, Any]) -> Dict[str, str]:
    text = " ".join(str(item.get(key) or "") for key in ("name", "error", "message", "reason", "status")).lower()
    if any(word in text for word in ("401", "403", "unauthorized", "forbidden", "token", "鉴权", "未授权")):
        failure_type = "AUTH_ISSUE"
        suggestion = "检查 MeterSphere 环境变量中的鉴权 token、登录前置步骤或接口权限配置。"
    elif any(word in text for word in ("timeout", "timed out", "connection", "dns", "network", "超时", "连接")):
        failure_type = "ENV_ISSUE"
        suggestion = "先确认测试环境、域名、网络连通性和 MeterSphere 执行节点。"
    elif any(word in text for word in ("assert", "断言", "expected", "schema", "jsonpath")):
        failure_type = "ASSERTION_ISSUE"
        suggestion = "复核 OpenAPI 响应定义与真实响应，必要时调整断言。"
    elif any(word in text for word in ("data", "不存在", "not found", "empty", "测试数据")):
        failure_type = "TEST_DATA_ISSUE"
        suggestion = "补齐接口依赖数据或前置造数步骤。"
    else:
        failure_type = "API_OR_PRODUCT_ISSUE"
        suggestion = "结合请求、响应和后端日志确认是否为接口业务缺陷。"
    return {"failure_type": failure_type, "suggestion": suggestion}


def normalize_metersphere_report(
    run_id: str,
    raw_report: Dict[str, Any] | None = None,
    plan_id: str = "",
    *,
    source_id: str = "",
    execution_id: str = "",
    binding_id: str = "",
    binding_fingerprint: str = "",
    project_id: str = "",
    environment_id: str = "",
) -> Dict[str, Any]:
    raw = raw_report if isinstance(raw_report, dict) else {}
    rows = raw.get("results") or raw.get("cases") or raw.get("items") or []
    if not isinstance(rows, list):
        rows = []
    normalized_rows: List[Dict[str, Any]] = []
    passed = 0
    failed = 0
    for index, row in enumerate(rows, start=1):
        item = row if isinstance(row, dict) else {"name": str(row)}
        status = str(item.get("status") or item.get("result") or "").strip().lower()
        ok = status in {"success", "passed", "pass", "ok"}
        if ok:
            passed += 1
        else:
            failed += 1
        normalized = {
            "case_id": item.get("case_id") or item.get("id") or f"case-{index}",
            "name": item.get("name") or item.get("title") or f"接口用例 {index}",
            "status": "passed" if ok else "failed",
            "duration_ms": item.get("duration_ms") or item.get("duration") or 0,
            "error": item.get("error") or item.get("message") or item.get("reason") or "",
        }
        if not ok:
            normalized.update(classify_api_failure(normalized))
        normalized_rows.append(normalized)
    total = len(normalized_rows)
    if not total:
        summary = raw.get("summary") if isinstance(raw.get("summary"), dict) else {}
        total = int(summary.get("total") or raw.get("total") or 0)
        passed = int(summary.get("passed") or summary.get("success") or raw.get("passed") or 0)
        failed = int(summary.get("failed") or raw.get("failed") or max(total - passed, 0))
    status = "passed" if total and failed == 0 else ("failed" if failed else "unknown")
    return {
        "report_id": unique_millis_id("api_report"),
        "run_id": str(run_id or raw.get("run_id") or raw.get("id") or "").strip(),
        "plan_id": str(plan_id or raw.get("plan_id") or "").strip(),
        "source_id": str(source_id or "").strip(),
        "execution_id": str(execution_id or "").strip(),
        "binding_id": str(binding_id or "").strip(),
        "binding_fingerprint": str(binding_fingerprint or "").strip(),
        "project_id": str(project_id or "").strip(),
        "environment_id": str(environment_id or "").strip(),
        "status": status,
        "created_at": _now(),
        "summary": {"total": total, "passed": passed, "failed": failed},
        "results": normalized_rows,
        "raw": raw,
    }


def save_api_report(report: Dict[str, Any]) -> Dict[str, Any]:
    source_id, ownership_valid, source_derived = _report_source_ownership(report)
    if str(report.get("source_id") or "").strip() and not ownership_valid:
        raise ValueError("API report 不属于计划对应的 source")
    if source_derived:
        report["source_id"] = source_id
        report["source_id_derived"] = True
    if not report.get("report_id"):
        report["report_id"] = unique_millis_id("api_report")
    if not report.get("created_at"):
        report["created_at"] = _now()
    write_json_file(_report_path(report.get("report_id")), report)
    _save_index(report)
    return report


def get_api_report(report_id: str, source_id: str = "") -> Dict[str, Any]:
    report = read_json_file(_report_path(report_id), default={}) or {}
    if not isinstance(report, dict) or not report.get("report_id"):
        return {}
    selected_source_id = str(source_id or "").strip()
    resolved_source_id, ownership_valid, source_derived = _report_source_ownership(
        report
    )
    if selected_source_id and (
        not ownership_valid or resolved_source_id != selected_source_id
    ):
        return {}
    if source_derived:
        report["source_id"] = resolved_source_id
        report["source_id_derived"] = True
    return report


def list_api_reports(limit: int = 20, source_id: str = "") -> List[Dict[str, Any]]:
    index = read_json_file(_index_path(), default=[]) or []
    if not isinstance(index, list):
        return []
    try:
        size = max(1, int(limit))
    except Exception:
        size = 20
    selected_source_id = str(source_id or "").strip()
    if not selected_source_id:
        return index[:size]
    reports = []
    for item in index:
        report = get_api_report(
            str(item.get("report_id") or ""),
            source_id=selected_source_id,
        )
        if not report:
            continue
        reports.append(_report_index_item(report))
        if len(reports) >= size:
            break
    return reports


__all__ = [
    "API_TESTING_DIR",
    "classify_api_failure",
    "get_api_report",
    "normalize_metersphere_report",
    "save_api_report",
    "list_api_reports",
]
