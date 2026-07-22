"""API asset import and storage for the API testing workspace."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from typing import Any, Dict, List

from task_server.config import LEARNING_DIR
from task_server.storage import clean_asset_filename, clean_id, read_json_file, safe_join, unique_millis_id, write_json_file


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
API_TESTING_DIR = os.getenv("API_TESTING_DIR", safe_join(LEARNING_DIR, "api-testing"))
_ASSET_INDEX_LOCK = threading.RLock()


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _api_path(*parts: str) -> str:
    return safe_join(API_TESTING_DIR, *parts)


def _snapshot_path(snapshot_id: str) -> str:
    return _api_path("snapshots", f"{clean_id(snapshot_id, 'snapshot')}.json")


def _index_path() -> str:
    return _api_path("snapshots", "index.json")


def _asset_path(asset_id: str) -> str:
    return _api_path("assets", f"{clean_id(asset_id, 'api_asset')}.json")


def _asset_index_path() -> str:
    return _api_path("assets", "index.json")


def _revision_path(revision_id: str) -> str:
    return _api_path("revisions", f"{clean_id(revision_id, 'api_revision')}.json")


def _revision_index_path(asset_id: str) -> str:
    return _api_path("revisions", f"{clean_id(asset_id, 'api_asset')}-index.json")


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
    apifox_folder = str(operation.get("x-apifox-folder") or "").strip()
    if apifox_folder:
        return apifox_folder
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


def _document_hash(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalized_operation_id(value: Any) -> str:
    return re.sub(r"[^a-z0-9_.:-]+", "-", str(value or "").strip().lower()).strip("-._:")


def _provider_endpoint_id(operation: Dict[str, Any], source_type: str) -> str:
    if str(source_type or "").strip().lower() != "apifox":
        return ""
    for key in ("x-apifox-endpoint-id", "x-apifox-api-id", "x-apifox-id"):
        value = operation.get(key)
        if value not in (None, ""):
            return str(value).strip()
    extension = operation.get("x-apifox")
    if isinstance(extension, dict):
        for key in ("endpointId", "apiId", "id"):
            value = extension.get(key)
            if value not in (None, ""):
                return str(value).strip()
    run_link = str(operation.get("x-run-in-apifox") or "").strip()
    match = re.search(r"(?:^|/)api-(\d+)(?:-run)?(?:[/?#]|$)", run_link)
    if match:
        return match.group(1)
    return ""


def _operation_id_counts(doc: Dict[str, Any]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for path_item in (doc.get("paths") or {}).values():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if str(method or "").lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation_id = _normalized_operation_id(operation.get("operationId"))
            if operation_id:
                counts[operation_id] = counts.get(operation_id, 0) + 1
    return counts


def _endpoint_identity(
    path: str,
    method: str,
    operation: Dict[str, Any],
    source_type: str,
    operation_counts: Dict[str, int],
) -> tuple[str, str]:
    provider_id = _provider_endpoint_id(operation, source_type)
    if provider_id:
        return f"apifox:{provider_id}", provider_id
    operation_id = _normalized_operation_id(operation.get("operationId"))
    if operation_id and operation_counts.get(operation_id) == 1:
        return f"operation:{operation_id}", ""
    return f"route:{method.upper()} {path}", ""


def _operation_endpoint(
    path: str,
    method: str,
    operation: Dict[str, Any],
    endpoint_key: str,
    source_ref: str = "",
    asset_revision_id: str = "",
) -> Dict[str, Any]:
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
    endpoint_id = clean_id(f"api_{_schema_hash({'endpoint_key': endpoint_key})}", "api")
    endpoint_revision_id = f"api_endpoint_revision_{_schema_hash({'endpoint_key': endpoint_key, 'schema_hash': short_hash})}"
    return {
        "endpoint_id": endpoint_id,
        "endpoint_key": endpoint_key,
        "endpoint_revision_id": endpoint_revision_id,
        "asset_revision_id": asset_revision_id,
        "source_ref": source_ref,
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
        "deprecated": bool(operation.get("deprecated")),
        "schema_hash": short_hash,
    }


def build_revision_endpoints(
    doc: Dict[str, Any],
    source_type: str = "openapi_upload",
    asset_revision_id: str = "",
) -> List[Dict[str, Any]]:
    paths = doc.get("paths") or {}
    if not isinstance(paths, dict) or not paths:
        raise ValueError("OpenAPI paths 为空")
    operation_counts = _operation_id_counts(doc)
    endpoints: List[Dict[str, Any]] = []
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            method_key = str(method or "").lower()
            if method_key not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            endpoint_key, source_ref = _endpoint_identity(
                str(path), method_key, operation, source_type, operation_counts
            )
            endpoints.append(_operation_endpoint(
                str(path),
                method_key,
                operation,
                endpoint_key=endpoint_key,
                source_ref=source_ref,
                asset_revision_id=asset_revision_id,
            ))
    if not endpoints:
        raise ValueError("OpenAPI 未解析到可测试接口")
    return endpoints


def _extract_endpoints(doc: Dict[str, Any]) -> List[Dict[str, Any]]:
    return build_revision_endpoints(doc, source_type="openapi_upload")


def _save_snapshot_index(snapshot: Dict[str, Any]) -> None:
    with _ASSET_INDEX_LOCK:
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


def _asset_id_for_source(source_id: str) -> str:
    digest = hashlib.sha256(str(source_id or "").encode("utf-8")).hexdigest()[:16]
    return f"api_asset_{digest}"


def _asset_summary(asset: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "asset_id": asset.get("asset_id"),
        "source_id": asset.get("source_id"),
        "source_type": asset.get("source_type"),
        "name": asset.get("name"),
        "status": asset.get("status"),
        "active_revision_id": asset.get("active_revision_id"),
        "latest_revision_id": asset.get("latest_revision_id"),
        "endpoint_count": asset.get("endpoint_count", 0),
        "schema_version": asset.get("schema_version", ""),
        "last_sync_at": asset.get("last_sync_at", ""),
        "created_at": asset.get("created_at", ""),
        "updated_at": asset.get("updated_at", ""),
    }


def _write_asset(asset: Dict[str, Any]) -> None:
    with _ASSET_INDEX_LOCK:
        write_json_file(_asset_path(str(asset.get("asset_id") or "")), asset)
        index = read_json_file(_asset_index_path(), default=[]) or []
        if not isinstance(index, list):
            index = []
        summary = _asset_summary(asset)
        index = [item for item in index if isinstance(item, dict) and item.get("asset_id") != summary.get("asset_id")]
        index.insert(0, summary)
        write_json_file(_asset_index_path(), index[:100])


def get_api_asset(asset_id: str) -> Dict[str, Any]:
    asset = read_json_file(_asset_path(asset_id), default={}) or {}
    return asset if isinstance(asset, dict) else {}


def list_api_assets(limit: int = 20) -> List[Dict[str, Any]]:
    index = read_json_file(_asset_index_path(), default=[]) or []
    if not isinstance(index, list):
        return []
    try:
        size = max(1, int(limit))
    except Exception:
        size = 20
    return [item for item in index[:size] if isinstance(item, dict)]


def _revision_summary(revision: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "revision_id": revision.get("revision_id"),
        "snapshot_id": revision.get("revision_id"),
        "asset_id": revision.get("asset_id"),
        "source_id": revision.get("source_id"),
        "source_type": revision.get("source_type"),
        "name": revision.get("name"),
        "title": revision.get("title"),
        "version": revision.get("version"),
        "source_revision": revision.get("source_revision"),
        "document_hash": revision.get("document_hash"),
        "endpoint_count": revision.get("endpoint_count", 0),
        "openapi_version": revision.get("openapi_version", ""),
        "created_at": revision.get("created_at", ""),
    }


def _save_revision_index(revision: Dict[str, Any]) -> None:
    with _ASSET_INDEX_LOCK:
        path = _revision_index_path(str(revision.get("asset_id") or ""))
        index = read_json_file(path, default=[]) or []
        if not isinstance(index, list):
            index = []
        summary = _revision_summary(revision)
        index = [item for item in index if isinstance(item, dict) and item.get("revision_id") != summary.get("revision_id")]
        index.insert(0, summary)
        write_json_file(path, index[:200])


def get_api_revision(revision_id: str) -> Dict[str, Any]:
    revision = read_json_file(_revision_path(revision_id), default={}) or {}
    return revision if isinstance(revision, dict) else {}


def list_api_revisions(asset_id: str, limit: int = 50) -> List[Dict[str, Any]]:
    index = read_json_file(_revision_index_path(asset_id), default=[]) or []
    if not isinstance(index, list):
        return []
    try:
        size = max(1, int(limit))
    except Exception:
        size = 50
    return [item for item in index[:size] if isinstance(item, dict)]


def get_active_api_revision(asset_id: str) -> Dict[str, Any]:
    asset = get_api_asset(asset_id)
    return get_api_revision(str(asset.get("active_revision_id") or "")) if asset else {}


def stage_api_revision(
    source_id: str,
    source_name: str,
    document: Any,
    source_type: str = "apifox",
    source_revision: str = "",
    document_hash: str = "",
) -> Dict[str, Any]:
    source_key = str(source_id or "").strip()
    if not source_key:
        raise ValueError("API source_id 不能为空")
    doc = _parse_openapi_content(document)
    resolved_hash = str(document_hash or _document_hash(doc)).strip()
    asset_id = _asset_id_for_source(source_key)
    asset = get_api_asset(asset_id)
    active_revision = get_active_api_revision(asset_id) if asset else {}
    if active_revision and active_revision.get("document_hash") == resolved_hash:
        return {
            "status": "no_change",
            "asset_id": asset_id,
            "revision_id": active_revision.get("revision_id"),
            "asset": asset,
            "revision": active_revision,
        }
    revision_id = unique_millis_id("api_revision")
    endpoints = build_revision_endpoints(doc, source_type=source_type, asset_revision_id=revision_id)
    info = doc.get("info") if isinstance(doc.get("info"), dict) else {}
    now = _now()
    title = str(info.get("title") or source_name or "API 接口").strip()
    revision = {
        "revision_id": revision_id,
        "snapshot_id": revision_id,
        "asset_id": asset_id,
        "source_id": source_key,
        "source_type": str(source_type or "").strip() or "openapi_upload",
        "source_revision": str(source_revision or "").strip(),
        "document_hash": resolved_hash,
        "name": str(source_name or title).strip() or title,
        "title": title,
        "version": str(info.get("version") or "").strip(),
        "filename": "",
        "openapi_version": str(doc.get("openapi") or doc.get("swagger") or "").strip(),
        "endpoint_count": len(endpoints),
        "created_at": now,
        "endpoints": endpoints,
    }
    write_json_file(_revision_path(revision_id), revision)
    _save_revision_index(revision)
    if not asset:
        asset = {
            "asset_id": asset_id,
            "source_id": source_key,
            "source_type": str(source_type or "").strip() or "openapi_upload",
            "name": str(source_name or title).strip() or title,
            "status": "staged",
            "active_revision_id": "",
            "latest_revision_id": revision_id,
            "endpoint_count": 0,
            "schema_version": "",
            "last_sync_at": "",
            "created_at": now,
            "updated_at": now,
        }
    else:
        asset["latest_revision_id"] = revision_id
        asset["updated_at"] = now
    _write_asset(asset)
    return {
        "status": "staged",
        "asset_id": asset_id,
        "revision_id": revision_id,
        "asset": asset,
        "revision": revision,
        "previous_revision_id": str(asset.get("active_revision_id") or ""),
    }


def activate_api_revision(asset_id: str, revision_id: str) -> Dict[str, Any]:
    asset = get_api_asset(asset_id)
    if not asset:
        raise ValueError("API asset 不存在")
    revision = get_api_revision(revision_id)
    if not revision or revision.get("asset_id") != asset_id:
        raise ValueError("API revision 不存在或不属于当前 asset")
    now = _now()
    asset.update({
        "status": "active",
        "active_revision_id": revision_id,
        "latest_revision_id": revision_id,
        "endpoint_count": int(revision.get("endpoint_count") or 0),
        "schema_version": str(revision.get("openapi_version") or ""),
        "last_sync_at": now,
        "updated_at": now,
    })
    _write_asset(asset)
    return asset


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
        index = []
    combined = [item for item in index if isinstance(item, dict)]
    for asset in list_api_assets(limit=100):
        active_revision_id = str(asset.get("active_revision_id") or "").strip()
        active_revision = get_api_revision(active_revision_id) if active_revision_id else {}
        if active_revision:
            combined.append(_revision_summary(active_revision))
    deduped: Dict[str, Dict[str, Any]] = {}
    for item in combined:
        item_id = str(item.get("snapshot_id") or item.get("revision_id") or "").strip()
        if item_id and item_id not in deduped:
            deduped[item_id] = item
    ordered = sorted(deduped.values(), key=lambda item: str(item.get("created_at") or ""), reverse=True)
    try:
        size = max(1, int(limit))
    except Exception:
        size = 20
    return ordered[:size]


def get_api_snapshot(snapshot_id: str = "") -> Dict[str, Any]:
    target = str(snapshot_id or "").strip()
    if not target:
        snapshots = list_api_snapshots(limit=1)
        target = snapshots[0].get("snapshot_id") if snapshots else ""
    if not target:
        return {}
    revision = get_api_revision(target)
    if revision:
        return revision
    snapshot = read_json_file(_snapshot_path(target), default={}) or {}
    return snapshot if isinstance(snapshot, dict) else {}


def list_api_endpoints(snapshot_id: str = "") -> List[Dict[str, Any]]:
    snapshot = get_api_snapshot(snapshot_id)
    endpoints = snapshot.get("endpoints") or []
    return endpoints if isinstance(endpoints, list) else []


__all__ = [
    "API_TESTING_DIR",
    "activate_api_revision",
    "build_revision_endpoints",
    "get_active_api_revision",
    "get_api_asset",
    "get_api_revision",
    "import_openapi_document",
    "list_api_assets",
    "list_api_revisions",
    "list_api_snapshots",
    "list_api_endpoints",
    "get_api_snapshot",
    "stage_api_revision",
]
