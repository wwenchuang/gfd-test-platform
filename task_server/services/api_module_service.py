"""Module discovery, scope filtering, and read models for OpenAPI documents."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, Iterable, List


HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
MODULE_MATCHER_VERSION = "apifox_folder_v1"


def normalize_module_path(value: Any) -> str:
    parts = [part.strip() for part in str(value or "").replace("\\", "/").split("/")]
    return "/".join(part for part in parts if part)


def operation_module_path(path: str, operation: Dict[str, Any]) -> str:
    folder = normalize_module_path(operation.get("x-apifox-folder"))
    if folder:
        return folder
    tags = operation.get("tags") if isinstance(operation.get("tags"), list) else []
    if tags and normalize_module_path(tags[0]):
        return normalize_module_path(tags[0])
    segments = [part for part in str(path or "").split("/") if part]
    return normalize_module_path(segments[0] if segments else "未分组")


def module_selected(module_path: str, selected: Iterable[str]) -> bool:
    module = normalize_module_path(module_path)
    return any(
        module == candidate or module.startswith(f"{candidate}/")
        for candidate in (normalize_module_path(item) for item in selected)
        if candidate
    )


def _operations(document: Dict[str, Any]):
    paths = document.get("paths") if isinstance(document, dict) else {}
    if not isinstance(paths, dict):
        return
    for path, path_item in paths.items():
        if not isinstance(path_item, dict):
            continue
        for method, operation in path_item.items():
            if str(method or "").lower() in HTTP_METHODS and isinstance(operation, dict):
                yield str(path), str(method).lower(), operation


def module_catalog(document: Dict[str, Any]) -> List[Dict[str, Any]]:
    counts: Dict[str, int] = {}
    for path, _method, operation in _operations(document) or []:
        module_path = operation_module_path(path, operation)
        counts[module_path] = counts.get(module_path, 0) + 1
    return [
        {
            "path": path,
            "name": path.rsplit("/", 1)[-1],
            "parent_path": path.rsplit("/", 1)[0] if "/" in path else "",
            "depth": len(path.split("/")),
            "endpoint_count": counts[path],
        }
        for path in sorted(counts)
    ]


def filter_document(document: Dict[str, Any], module_paths: Iterable[str]) -> Dict[str, Any]:
    selected = [normalize_module_path(item) for item in module_paths]
    selected = [item for item in selected if item]
    filtered = copy.deepcopy(document)
    paths = filtered.get("paths") if isinstance(filtered, dict) else None
    if not isinstance(paths, dict):
        raise ValueError("OpenAPI paths 为空")
    kept_operations = 0
    for path, path_item in list(paths.items()):
        if not isinstance(path_item, dict):
            paths.pop(path, None)
            continue
        selected_item = {
            key: value
            for key, value in path_item.items()
            if str(key or "").lower() not in HTTP_METHODS
        }
        for method, operation in path_item.items():
            if str(method or "").lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            if module_selected(operation_module_path(str(path), operation), selected):
                selected_item[method] = operation
                kept_operations += 1
        if any(str(key or "").lower() in HTTP_METHODS for key in selected_item):
            paths[path] = selected_item
        else:
            paths.pop(path, None)
    if not kept_operations:
        raise ValueError("所选模块未包含可测试接口")
    return filtered


def scope_fingerprint(scope: Dict[str, Any]) -> str:
    normalized = {
        "mode": str(scope.get("mode") or "all").strip().lower(),
        "module_paths": sorted({normalize_module_path(item) for item in scope.get("module_paths", []) if normalize_module_path(item)}),
        "matcher_version": str(scope.get("matcher_version") or MODULE_MATCHER_VERSION),
    }
    raw = json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def module_summary(endpoints: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    nodes: Dict[str, Dict[str, Any]] = {}
    endpoint_count = 0
    for endpoint in endpoints:
        if not isinstance(endpoint, dict):
            continue
        endpoint_count += 1
        module_path = normalize_module_path(endpoint.get("module_path") or endpoint.get("module")) or "未分组"
        parts = module_path.split("/")
        for index in range(1, len(parts) + 1):
            path = "/".join(parts[:index])
            parent_path = "/".join(parts[:index - 1])
            node = nodes.setdefault(path, {
                "path": path,
                "name": parts[index - 1],
                "parent_path": parent_path,
                "depth": index,
                "endpoint_count": 0,
                "children": [],
            })
            node["endpoint_count"] += 1
    for path, node in nodes.items():
        if node["parent_path"]:
            nodes[node["parent_path"]]["children"].append(node)
    for node in nodes.values():
        node["children"].sort(key=lambda item: item["path"])
    roots = [node for node in nodes.values() if not node["parent_path"]]
    roots.sort(key=lambda item: item["path"])
    return {
        "total_modules": len(nodes),
        "total_endpoints": endpoint_count,
        "roots": roots,
    }


__all__ = [
    "MODULE_MATCHER_VERSION",
    "filter_document",
    "module_catalog",
    "module_selected",
    "module_summary",
    "normalize_module_path",
    "operation_module_path",
    "scope_fingerprint",
]
