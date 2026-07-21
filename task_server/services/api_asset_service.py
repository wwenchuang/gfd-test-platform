"""API asset import and storage for the API testing workspace."""

from __future__ import annotations

import hashlib
import json
import os
import time
from typing import Any, Dict, List

from task_server.config import LEARNING_DIR
from task_server.storage import clean_asset_filename, clean_id, read_json_file, safe_join, unique_millis_id, write_json_file


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
API_TESTING_DIR = os.getenv("API_TESTING_DIR", safe_join(LEARNING_DIR, "api-testing"))


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _api_path(*parts: str) -> str:
    return safe_join(API_TESTING_DIR, *parts)


def _snapshot_path(snapshot_id: str) -> str:
    return _api_path("snapshots", f"{clean_id(snapshot_id, 'snapshot')}.json")


def _index_path() -> str:
    return _api_path("snapshots", "index.json")


def _parse_openapi_content(content: Any) -> Dict[str, Any]:
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        text = content.strip()
        if not text:
            raise ValueError("OpenAPI 内容为空")
        try:
            parsed = json.loads(text)
        except Exception as exc:
            raise ValueError(f"OpenAPI JSON 解析失败：{exc}") from exc
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("OpenAPI 内容必须是 JSON 对象")


def _first_json_schema(content_map: Any) -> Dict[str, Any]:
    if not isinstance(content_map, dict) or not content_map:
        return {}
    preferred = content_map.get("application/json")
    if isinstance(preferred, dict) and isinstance(preferred.get("schema"), dict):
        return preferred.get("schema") or {}
    for item in content_map.values():
        if isinstance(item, dict) and isinstance(item.get("schema"), dict):
            return item.get("schema") or {}
    return {}


def _request_schema(operation: Dict[str, Any]) -> Dict[str, Any]:
    body = operation.get("requestBody") or {}
    if not isinstance(body, dict):
        return {}
    return _first_json_schema(body.get("content"))


def _response_schema(operation: Dict[str, Any]) -> Dict[str, Any]:
    responses = operation.get("responses") or {}
    if not isinstance(responses, dict):
        return {}
    for status in ("200", "201", "202", "default"):
        response = responses.get(status)
        if isinstance(response, dict):
            schema = _first_json_schema(response.get("content"))
            if schema:
                return schema
    for response in responses.values():
        if isinstance(response, dict):
            schema = _first_json_schema(response.get("content"))
            if schema:
                return schema
    return {}


def _response_summaries(operation: Dict[str, Any]) -> List[Dict[str, Any]]:
    responses = operation.get("responses") or {}
    if not isinstance(responses, dict):
        return []
    items: List[Dict[str, Any]] = []
    for status, response in responses.items():
        if not isinstance(response, dict):
            continue
        items.append({
            "status": str(status),
            "description": str(response.get("description") or "").strip(),
            "schema": _first_json_schema(response.get("content")),
        })
    return items


def _required_fields(operation: Dict[str, Any], request_schema: Dict[str, Any]) -> List[str]:
    fields: List[str] = []
    for name in request_schema.get("required") or []:
        text = str(name or "").strip()
        if text and text not in fields:
            fields.append(text)
    parameters = operation.get("parameters") or []
    if isinstance(parameters, list):
        for param in parameters:
            if not isinstance(param, dict) or not param.get("required"):
                continue
            name = str(param.get("name") or "").strip()
            if name and name not in fields:
                fields.append(name)
    return fields


def _module_for_operation(path: str, operation: Dict[str, Any]) -> str:
    tags = operation.get("tags")
    if isinstance(tags, list):
        for tag in tags:
            text = str(tag or "").strip()
            if text:
                return text
    segments = [item for item in str(path or "").split("/") if item]
    return segments[0] if segments else "未分组"


def _schema_hash(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _operation_endpoint(path: str, method: str, operation: Dict[str, Any]) -> Dict[str, Any]:
    request = _request_schema(operation)
    response = _response_schema(operation)
    operation_id = str(operation.get("operationId") or "").strip()
    hash_input = {
        "method": method.upper(),
        "path": path,
        "request": request,
        "response": response,
        "parameters": operation.get("parameters") or [],
        "security": operation.get("security") or [],
    }
    short_hash = _schema_hash(hash_input)
    endpoint_id = clean_id(f"{method.lower()}_{path.strip('/').replace('/', '_')}_{short_hash}", "api")
    return {
        "endpoint_id": endpoint_id,
        "operation_id": operation_id,
        "method": method.upper(),
        "path": path,
        "module": _module_for_operation(path, operation),
        "name": str(operation.get("summary") or operation_id or f"{method.upper()} {path}").strip(),
        "description": str(operation.get("description") or "").strip(),
        "tags": [str(tag).strip() for tag in (operation.get("tags") or []) if str(tag).strip()],
        "parameters": operation.get("parameters") if isinstance(operation.get("parameters"), list) else [],
        "request_body_required": bool((operation.get("requestBody") or {}).get("required")) if isinstance(operation.get("requestBody"), dict) else False,
        "request_schema": request,
        "response_schema": response,
        "responses": _response_summaries(operation),
        "required_fields": _required_fields(operation, request),
        "security": operation.get("security") if isinstance(operation.get("security"), list) else [],
        "schema_hash": short_hash,
    }


def _extract_endpoints(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    paths = doc.get("paths") or {}
    if not isinstance(paths, dict) or not paths:
        raise ValueError("OpenAPI paths 为空")
    endpoints: List[Dict[str, Any]] = []
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            method_key = str(method or "").lower()
            if method_key not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            endpoints.append(_operation_endpoint(str(path), method_key, operation))
    if not endpoints:
        raise ValueError("OpenAPI 未解析到可测试接口")
    return endpoints


def _save_snapshot_index(snapshot: Dict[str, Any]) -> None:
    index = read_json_file(_index_path(), default=[]) or []
    if not isinstance(index, list):
        index = []
    item = {
        "snapshot_id": snapshot.get("snapshot_id"),
        "name": snapshot.get("name"),
        "title": snapshot.get("title"),
        "version": snapshot.get("version"),
        "filename": snapshot.get("filename"),
        "endpoint_count": snapshot.get("endpoint_count"),
        "created_at": snapshot.get("created_at"),
    }
    index = [row for row in index if row.get("snapshot_id") != item.get("snapshot_id")]
    index.insert(0, item)
    write_json_file(_index_path(), index[:100])


def import_openapi_document(name: str, content: Any, filename: str = "") -> Dict[str, Any]:
    doc = _parse_openapi_content(content)
    endpoints = _extract_endpoints(doc)
    info = doc.get("info") if isinstance(doc.get("info"), dict) else {}
    snapshot_id = unique_millis_id("api_snapshot")
    title = str(info.get("title") or name or filename or "OpenAPI 接口").strip()
    snapshot = {
        "snapshot_id": snapshot_id,
        "source": "openapi",
        "name": str(name or title).strip() or title,
        "title": title,
        "version": str(info.get("version") or "").strip(),
        "filename": clean_asset_filename(filename or f"{title}.json", "openapi.json"),
        "created_at": _now(),
        "openapi_version": str(doc.get("openapi") or doc.get("swagger") or "").strip(),
        "endpoint_count": len(endpoints),
        "endpoints": endpoints,
    }
    write_json_file(_snapshot_path(snapshot_id), snapshot)
    _save_snapshot_index(snapshot)
    return snapshot


def list_api_snapshots(limit: int = 20) -> List[Dict[str, Any]]:
    index = read_json_file(_index_path(), default=[]) or []
    if not isinstance(index, list):
        return []
    try:
        size = max(1, int(limit))
    except Exception:
        size = 20
    return index[:size]


def get_api_snapshot(snapshot_id: str = "") -> Dict[str, Any]:
    target = str(snapshot_id or "").strip()
    if not target:
        snapshots = list_api_snapshots(limit=1)
        target = snapshots[0].get("snapshot_id") if snapshots else ""
    if not target:
        return {}
    snapshot = read_json_file(_snapshot_path(target), default={}) or {}
    return snapshot if isinstance(snapshot, dict) else {}


def list_api_endpoints(snapshot_id: str = "") -> List[Dict[str, Any]]:
    snapshot = get_api_snapshot(snapshot_id)
    endpoints = snapshot.get("endpoints") or []
    return endpoints if isinstance(endpoints, list) else []


__all__ = [
    "API_TESTING_DIR",
    "import_openapi_document",
    "list_api_snapshots",
    "list_api_endpoints",
    "get_api_snapshot",
]
