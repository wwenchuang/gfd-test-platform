"""Strict, deterministic OpenAPI 3.0/3.1 normalization."""

import copy
import hashlib
import json
import re
import unicodedata
from typing import Any, Dict, Mapping, Set

from ..contracts.source import NormalizedEndpoint, NormalizedSourceDocument


HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
PATH_ITEM_FIELDS = frozenset({"$ref", "summary", "description", "servers", "parameters", "vendor_extensions"})
PATH_PARAMETER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")


class OpenApiValidationError(ValueError):
    """Raised before persistence when a source document is not importable."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _namespace_vendor_extensions(value: Any) -> Any:
    if isinstance(value, list):
        return [_namespace_vendor_extensions(item) for item in value]
    if not isinstance(value, dict):
        return value
    normalized = {}
    extensions = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise OpenApiValidationError("OpenAPI object keys must be strings")
        transformed = _namespace_vendor_extensions(item)
        if key.lower().startswith("x-"):
            extensions[key] = transformed
        else:
            normalized[key] = transformed
    if extensions:
        existing = normalized.get("vendor_extensions")
        if existing is not None and not isinstance(existing, dict):
            raise OpenApiValidationError("vendor_extensions must be an object")
        normalized["vendor_extensions"] = {**(existing or {}), **extensions}
    return normalized


def normalize_path(path: str) -> str:
    if not isinstance(path, str):
        raise OpenApiValidationError("OpenAPI path must be a string")
    normalized = unicodedata.normalize("NFC", path.strip())
    if not normalized.startswith("/"):
        raise OpenApiValidationError("OpenAPI paths must start with /")
    if "?" in normalized or "#" in normalized:
        raise OpenApiValidationError("OpenAPI paths must not contain query strings or fragments")
    if normalized.count("{") != normalized.count("}"):
        raise OpenApiValidationError("OpenAPI path parameters must use balanced braces")
    for part in re.findall(r"\{([^{}]*)\}", normalized):
        if not PATH_PARAMETER_PATTERN.fullmatch(part):
            raise OpenApiValidationError("OpenAPI path parameter names are invalid")
    without_parameters = re.sub(r"\{[^{}]+\}", "", normalized)
    if "{" in without_parameters or "}" in without_parameters:
        raise OpenApiValidationError("OpenAPI path parameters are malformed")
    return normalized


def stable_endpoint_key(source_id: str, operation_id: str, method: str, normalized_path: str) -> str:
    identity = [
        str(source_id),
        str(operation_id or ""),
        str(method).upper(),
        normalize_path(normalized_path),
    ]
    return hashlib.sha256(_canonical_json(identity).encode("utf-8")).hexdigest()


def _validate_root(document: Any) -> Mapping[str, Any]:
    if not isinstance(document, dict):
        raise OpenApiValidationError("OpenAPI document must be an object")
    for required in ("openapi", "info", "paths"):
        if required not in document:
            raise OpenApiValidationError("OpenAPI document is missing %s" % required)
    version = document["openapi"]
    if not isinstance(version, str) or not version.startswith(("3.0.", "3.1.")):
        raise OpenApiValidationError("Only OpenAPI 3.0 or 3.1 documents are supported")
    info = document["info"]
    if not isinstance(info, dict) or not str(info.get("title", "")).strip() or not str(info.get("version", "")).strip():
        raise OpenApiValidationError("OpenAPI info must contain title and version")
    if not isinstance(document["paths"], dict):
        raise OpenApiValidationError("OpenAPI paths must be an object")
    return document


def _referenced_schemas(value: Any, schemas: Mapping[str, Any]) -> Mapping[str, Any]:
    found: Dict[str, Any] = {}
    pending = [value]
    visited_objects: Set[int] = set()
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            object_id = id(current)
            if object_id in visited_objects:
                continue
            visited_objects.add(object_id)
            reference = current.get("$ref")
            prefix = "#/components/schemas/"
            if isinstance(reference, str) and reference.startswith(prefix):
                schema_name = reference[len(prefix):]
                if schema_name in schemas and schema_name not in found:
                    found[schema_name] = copy.deepcopy(schemas[schema_name])
                    pending.append(schemas[schema_name])
            pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return {key: found[key] for key in sorted(found)}


def normalize_openapi_document(document: Any, source_id: str) -> NormalizedSourceDocument:
    raw_document = _validate_root(copy.deepcopy(document))
    normalized_document = _namespace_vendor_extensions(raw_document)
    paths = normalized_document["paths"]
    components = normalized_document.get("components") or {}
    if not isinstance(components, dict):
        raise OpenApiValidationError("OpenAPI components must be an object")
    schemas = components.get("schemas") or {}
    if not isinstance(schemas, dict):
        raise OpenApiValidationError("OpenAPI components.schemas must be an object")

    endpoints = []
    method_path_identities = set()
    normalized_paths: Dict[str, Any] = {}
    for original_path, path_item in paths.items():
        normalized_path = normalize_path(original_path)
        if not isinstance(path_item, dict):
            raise OpenApiValidationError("OpenAPI path item must be an object")
        # The complete document was already transformed recursively. Repeating
        # the transform here would namespace x-* keys inside vendor_extensions
        # a second time.
        normalized_path_item = copy.deepcopy(path_item)
        for field in normalized_path_item:
            lowered = field.lower()
            if lowered not in HTTP_METHODS and field not in PATH_ITEM_FIELDS:
                raise OpenApiValidationError("Invalid OpenAPI method or path field: %s" % field)

        canonical_path_item = {}
        path_parameters = normalized_path_item.get("parameters", [])
        if not isinstance(path_parameters, list):
            raise OpenApiValidationError("OpenAPI path parameters must be an array")
        for field, value in normalized_path_item.items():
            lowered = field.lower()
            if lowered not in HTTP_METHODS:
                canonical_path_item[field] = value
                continue
            if not isinstance(value, dict):
                raise OpenApiValidationError("OpenAPI operation must be an object")
            method = lowered.upper()
            identity = (method, normalized_path)
            if identity in method_path_identities:
                raise OpenApiValidationError("OpenAPI contains duplicate method/path identity")
            method_path_identities.add(identity)
            document_operation = copy.deepcopy(value)
            if "responses" not in document_operation or not isinstance(
                document_operation["responses"], dict
            ):
                raise OpenApiValidationError("OpenAPI operation responses must be an object")
            parameters = document_operation.get("parameters", [])
            if not isinstance(parameters, list):
                raise OpenApiValidationError("OpenAPI operation parameters must be an array")
            document_operation["parameters"] = parameters
            operation = copy.deepcopy(document_operation)
            operation["parameters"] = parameters
            operation["path_parameters"] = copy.deepcopy(path_parameters)
            referenced_schemas = _referenced_schemas(operation, schemas)
            if referenced_schemas:
                operation["resolved_schemas"] = referenced_schemas
            operation_id = str(operation.get("operationId") or "")
            tags = operation.get("tags") or []
            if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                raise OpenApiValidationError("OpenAPI operation tags must be an array of strings")
            endpoint = NormalizedEndpoint(
                stable_key=stable_endpoint_key(source_id, operation_id, method, normalized_path),
                operation_id=operation_id,
                method=method,
                path=normalized_path,
                normalized_path=normalized_path,
                summary=str(operation.get("summary") or ""),
                tags=tuple(tags),
                operation=operation,
            )
            endpoints.append(endpoint)
            canonical_path_item[lowered] = document_operation
        if normalized_path in normalized_paths:
            raise OpenApiValidationError("OpenAPI contains duplicate normalized paths")
        normalized_paths[normalized_path] = canonical_path_item

    normalized_document["paths"] = normalized_paths
    endpoints.sort(key=lambda item: (item.normalized_path, item.method, item.operation_id))
    document_hash = hashlib.sha256(_canonical_json(normalized_document).encode("utf-8")).hexdigest()
    normalized_schemas = {str(key): value for key, value in sorted(schemas.items())}
    return NormalizedSourceDocument(
        document=normalized_document,
        document_hash=document_hash,
        endpoints=tuple(endpoints),
        schemas=normalized_schemas,
    )
