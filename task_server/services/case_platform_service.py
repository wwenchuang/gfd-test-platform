"""Read-only integration helpers for the external AgileTC case platform."""

from __future__ import annotations

import os
import re
import urllib.parse
from typing import Any, Dict, List, Optional

from .case_platform_auth import (
    CasePlatformAuthError,
    CasePlatformRequestError,
    get_case_platform_client,
)


DEFAULT_CASE_PLATFORM_BASE_URL = "http://qa-agiletc.gongfudou.com"
DEFAULT_CASE_PLATFORM_PRODUCT_LINE_ID = "1"
DEFAULT_CASE_PLATFORM_TIMEOUT_SECONDS = 15


class CasePlatformError(RuntimeError):
    """Raised when the external case platform cannot be queried."""


def _clean_text(value: Any, default: str = "") -> str:
    text = "" if value is None else str(value)
    text = re.sub(r"\s+", " ", text).strip()
    return text or default


def _safe_int(value: Any, default: int, *, minimum: int = 1, maximum: int = 50) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _base_url(value: Optional[str] = None) -> str:
    raw = _clean_text(value or os.environ.get("CASE_PLATFORM_BASE_URL"), DEFAULT_CASE_PLATFORM_BASE_URL)
    return raw.rstrip("/")


def _product_line_id(value: Optional[str] = None) -> str:
    return _clean_text(value or os.environ.get("CASE_PLATFORM_PRODUCT_LINE_ID"), DEFAULT_CASE_PLATFORM_PRODUCT_LINE_ID)


def _timeout_seconds(value: Optional[Any] = None) -> int:
    return _safe_int(value or os.environ.get("CASE_PLATFORM_TIMEOUT_SECONDS"), DEFAULT_CASE_PLATFORM_TIMEOUT_SECONDS, maximum=60)


def infer_case_platform_version(*values: Any) -> str:
    text = " ".join(_clean_text(value) for value in values if _clean_text(value))
    if not text:
        return ""
    match = re.search(r"(?:版本号?|version|ver|v)[\s:=：-]*([A-Za-z]?\d+(?:[._-]\d+){1,3})", text, re.I)
    if not match:
        match = re.search(r"\bV?\d+(?:[._-]\d+){1,3}\b", text, re.I)
    if not match:
        return ""
    raw = match.group(1) if match.lastindex else match.group(0)
    raw = _clean_text(raw).replace("_", ".")
    return raw if raw.upper().startswith("V") else f"V{raw}"


def _description_version_text(description: Any) -> str:
    text = "" if description is None else str(description)
    if not infer_case_platform_version(text):
        return ""
    segments = [part.strip() for part in re.split(r"[\n\r；;]+", text) if part.strip()]
    if not segments:
        segments = [_clean_text(text)]
    for segment in segments:
        if not infer_case_platform_version(segment):
            continue
        cleaned = re.sub(r"^(?:测试)?版本号?\s*[:：= -]*", "", segment.strip(), flags=re.I)
        cleaned = re.sub(r"^(?:version|ver)\s*[:：= -]*", "", cleaned.strip(), flags=re.I)
        return _clean_text(cleaned)
    return ""


def _report_version_text(description: Any, title: Any, requirement_link: Any) -> str:
    return _description_version_text(description) or infer_case_platform_version(title, requirement_link)


def _json_get(base_url: str, path: str, params: Dict[str, Any], *, timeout: int) -> Dict[str, Any]:
    try:
        client = get_case_platform_client(base_url, timeout)
        return client.request_json(path, params)
    except (CasePlatformAuthError, CasePlatformRequestError) as exc:
        raise CasePlatformError(str(exc)) from exc


def _case_link(base_url: str, product_line_id: Any, case_id: Any) -> str:
    product = urllib.parse.quote(_clean_text(product_line_id, DEFAULT_CASE_PLATFORM_PRODUCT_LINE_ID))
    case = urllib.parse.quote(_clean_text(case_id))
    return f"{base_url}/caseManager/{product}/{case}/undefined/0"


def _normalize_case_item(row: Dict[str, Any], *, base_url: str, fallback_product_line_id: str) -> Dict[str, Any]:
    case_id = _clean_text(row.get("id"))
    product_line_id = _clean_text(row.get("productLineId"), fallback_product_line_id)
    title = _clean_text(row.get("title"), f"用例集 {case_id}" if case_id else "未命名用例集")
    description = _clean_text(row.get("description"))
    requirement_link = _clean_text(row.get("requirementId"))
    version = _report_version_text(description, title, requirement_link)
    label_parts = [title, version, f"#{case_id}" if case_id else ""]
    label = " · ".join(part for part in label_parts if part)
    link = _case_link(base_url, product_line_id, case_id)
    return {
        "source": "agiletc",
        "id": case_id,
        "title": title,
        "description": description,
        "label": label,
        "version": version,
        "requirement_link": requirement_link,
        "case_link": link,
        "url": link,
        "product_line_id": product_line_id,
        "creator": _clean_text(row.get("creator")),
        "modifier": _clean_text(row.get("modifier")),
        "created_at": _clean_text(row.get("gmtCreated")),
        "updated_at": _clean_text(row.get("gmtModified") or row.get("gmtCreated")),
        "record_num": row.get("recordNum") if row.get("recordNum") is not None else 0,
    }


def _case_detail(base_url: str, case_id: str, *, timeout: int) -> Dict[str, Any]:
    if not case_id:
        return {}
    response = _json_get(base_url, "/api/case/detail", {"caseId": case_id}, timeout=timeout)
    if int(response.get("code") or 0) != 200:
        raise CasePlatformError(_clean_text(response.get("msg"), "读取用例集详情失败"))
    data = response.get("data")
    return data if isinstance(data, dict) else {}


def _merge_case_detail(row: Dict[str, Any], detail: Dict[str, Any]) -> Dict[str, Any]:
    if not detail:
        return dict(row)
    merged = dict(row)
    for key in ("id", "title", "description", "productLineId", "requirementId", "modifier", "groupId"):
        value = detail.get(key)
        if value is not None and _clean_text(value):
            merged[key] = value
    return merged


def _list_cases(
    *,
    base_url: str,
    product_line_id: str,
    limit: int,
    timeout: int,
    extra_params: Dict[str, Any],
) -> Dict[str, Any]:
    params = {
        "pageSize": limit,
        "pageNum": 1,
        "productLineId": product_line_id,
        "caseType": 0,
        "channel": 1,
        "bizId": "root",
    }
    params.update(extra_params)
    response = _json_get(base_url, "/api/case/list", params, timeout=timeout)
    if int(response.get("code") or 0) != 200:
        raise CasePlatformError(_clean_text(response.get("msg"), "查询用例平台失败"))
    data = response.get("data") if isinstance(response.get("data"), dict) else {}
    return {
        "total": data.get("total") if data.get("total") is not None else 0,
        "rows": data.get("dataSources") if isinstance(data.get("dataSources"), list) else [],
    }


def _search_queries(query: str, requirement_link: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if requirement_link:
        rows.append({"requirementId": requirement_link})
    elif query and "project.feishu.cn/" in query:
        rows.append({"requirementId": query})
    if query and "project.feishu.cn/" not in query:
        rows.append({"title": query})
        rows.append({"caseKeyWords": query})
    if not rows:
        rows.append({})
    return rows


def search_case_platform_cases(
    query: str = "",
    *,
    requirement_link: str = "",
    product_line_id: Optional[str] = None,
    limit: int = 10,
    base_url: Optional[str] = None,
    timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """Search AgileTC case sets and return normalized options for report metadata."""

    clean_query = _clean_text(query)
    clean_requirement = _clean_text(requirement_link)
    resolved_base_url = _base_url(base_url)
    resolved_product_line_id = _product_line_id(product_line_id)
    resolved_limit = _safe_int(limit, 10)
    resolved_timeout = _timeout_seconds(timeout)

    items: List[Dict[str, Any]] = []
    seen = set()
    remote_total = 0
    for extra_params in _search_queries(clean_query, clean_requirement):
        result = _list_cases(
            base_url=resolved_base_url,
            product_line_id=resolved_product_line_id,
            limit=resolved_limit,
            timeout=resolved_timeout,
            extra_params=extra_params,
        )
        try:
            remote_total = max(remote_total, int(result.get("total") or 0))
        except (TypeError, ValueError):
            pass
        for row in result.get("rows") or []:
            if not isinstance(row, dict):
                continue
            case_id = _clean_text(row.get("id"))
            try:
                detail = _case_detail(resolved_base_url, case_id, timeout=resolved_timeout)
            except CasePlatformError:
                detail = {}
            item = _normalize_case_item(
                _merge_case_detail(row, detail),
                base_url=resolved_base_url,
                fallback_product_line_id=resolved_product_line_id,
            )
            item_id = item.get("id") or item.get("case_link")
            if not item_id or item_id in seen:
                continue
            seen.add(item_id)
            items.append(item)
            if len(items) >= resolved_limit:
                break
        if len(items) >= resolved_limit:
            break

    return {
        "ok": True,
        "source": "agiletc",
        "query": clean_query,
        "requirement_link": clean_requirement,
        "base_url": resolved_base_url,
        "product_line_id": resolved_product_line_id,
        "total": len(items),
        "remote_total": remote_total,
        "items": items[:resolved_limit],
    }
