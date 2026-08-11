"""Strict, deterministic OpenAPI 3.0/3.1 normalization."""

import copy
import hashlib
import json
import re
import unicodedata
from typing import Any, Dict, Mapping, MutableMapping, Optional, Sequence, Set, Tuple
from urllib.parse import unquote

from ..contracts.source import NormalizedEndpoint, NormalizedSourceDocument


HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})
PATH_ITEM_FIELDS = frozenset({"$ref", "summary", "description", "servers", "parameters"})
PATH_PARAMETER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
OPENAPI_VERSION_PATTERN = re.compile(r"^3\.(0|1)\.\d+$")
JSON_POINTER_ESCAPE_PATTERN = re.compile(r"~(?:[^01]|$)")


class OpenApiValidationError(ValueError):
    """Raised before persistence when a source document is not importable."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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
    placeholders = re.findall(r"\{([^{}]*)\}", normalized)
    for part in placeholders:
        if not PATH_PARAMETER_PATTERN.fullmatch(part):
            raise OpenApiValidationError("OpenAPI path parameter names are invalid")
    if len(placeholders) != len(set(placeholders)):
        raise OpenApiValidationError("OpenAPI path parameter placeholders must be unique")
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
    if not isinstance(version, str) or not OPENAPI_VERSION_PATTERN.fullmatch(version):
        raise OpenApiValidationError("Only OpenAPI 3.0 or 3.1 documents are supported")
    info = document["info"]
    if not isinstance(info, dict) or not str(info.get("title", "")).strip() or not str(info.get("version", "")).strip():
        raise OpenApiValidationError("OpenAPI info must contain title and version")
    if not isinstance(document["paths"], dict):
        raise OpenApiValidationError("OpenAPI paths must be an object")
    return document


def _decode_pointer_segment(segment: str, reference: str) -> str:
    if JSON_POINTER_ESCAPE_PATTERN.search(segment):
        raise OpenApiValidationError("Invalid local JSON Pointer escape: %s" % reference)
    return segment.replace("~1", "/").replace("~0", "~")


class _LocalReferenceResolver:
    """Resolve local JSON Pointers without mutating the canonical document."""

    def __init__(self, document: Mapping[str, Any]):
        self._document = document

    @staticmethod
    def is_local(reference: str) -> bool:
        return reference == "#" or reference.startswith("#/")

    def resolve(self, reference: str) -> Any:
        value, _ = self.resolve_with_context(reference)
        return value

    def resolve_with_context(self, reference: str) -> Tuple[Any, str]:
        if not self.is_local(reference):
            raise OpenApiValidationError("Reference is not local: %s" % reference)
        current: Any = self._document
        context = "root"
        if reference == "#":
            return current, context
        pointer = unquote(reference[1:])
        if not pointer.startswith("/"):
            raise OpenApiValidationError("Invalid local JSON Pointer: %s" % reference)
        for encoded in pointer[1:].split("/"):
            segment = _decode_pointer_segment(encoded, reference)
            if isinstance(current, Mapping):
                if segment not in current:
                    raise OpenApiValidationError("Unresolved local reference: %s" % reference)
                context = self._mapping_child_context(context, segment)
                current = current[segment]
            elif isinstance(current, list):
                if not segment.isdigit() or int(segment) >= len(current):
                    raise OpenApiValidationError("Unresolved local reference: %s" % reference)
                context = self._sequence_item_context(context)
                current = current[int(segment)]
            else:
                raise OpenApiValidationError("Unresolved local reference: %s" % reference)
        return current, context

    @staticmethod
    def _mapping_child_context(context: str, key: str) -> str:
        if context == "payload":
            return "payload"
        if context == "components":
            return {
                "schemas": "schema_map",
                "parameters": "parameter_map",
                "headers": "header_map",
                "examples": "example_map",
                "pathItems": "path_item_map",
                "callbacks": "callback_component_map",
                "responses": "response_map",
                "requestBodies": "request_body_map",
            }.get(key, "generic")
        if context in {"schema_map"}:
            return "schema"
        if context == "schema":
            if key in {"example", "examples"}:
                return "payload"
            if key in {
                "properties",
                "patternProperties",
                "$defs",
                "definitions",
                "dependentSchemas",
            }:
                return "schema_map"
            if key in {"allOf", "anyOf", "oneOf", "prefixItems"}:
                return "schema_list"
            if key in {
                "items",
                "not",
                "if",
                "then",
                "else",
                "contains",
                "propertyNames",
                "additionalProperties",
                "unevaluatedProperties",
                "unevaluatedItems",
                "contentSchema",
            }:
                return "schema"
            return "generic"
        if context in {"path_item_map", "callback_map"}:
            return "path_item"
        if context == "callback_component_map":
            return "callback_map"
        if context in {"parameter_map"}:
            return "parameter"
        if context in {"header_map"}:
            return "header"
        if context == "example_map":
            return "example_object"
        if context == "media_type_map":
            return "media_type"
        if context == "response_map":
            return "response"
        if context == "request_body_map":
            return "request_body"
        if context == "example_object":
            return "payload" if key == "value" else "generic"
        if context in {"parameter", "header"}:
            if key == "schema":
                return "schema"
            if key == "content":
                return "media_type_map"
            if key == "example":
                return "payload"
            if key == "examples":
                return "example_map"
            return "generic"
        if context == "media_type":
            if key == "schema":
                return "schema"
            if key == "example":
                return "payload"
            if key == "examples":
                return "example_map"
            return "generic"

        if key == "components":
            return "components"
        if key in {"paths", "webhooks"}:
            return "path_item_map"
        if key.lower() in HTTP_METHODS:
            return "operation"
        if key == "schema":
            return "schema"
        if key == "content":
            return "media_type_map"
        if key == "parameters":
            return "parameter_list"
        if key == "headers":
            return "header_map"
        if key == "responses":
            return "response_map"
        if key == "requestBody":
            return "request_body"
        if key == "callbacks":
            return "callback_component_map"
        return "generic"

    @staticmethod
    def _sequence_item_context(context: str) -> str:
        return {
            "schema_list": "schema",
            "parameter_list": "parameter",
        }.get(context, "generic")

    def dependency_closure(self, value: Any) -> Tuple[Mapping[str, Any], Tuple[str, ...]]:
        resolved: Dict[str, Any] = {}
        external: Set[str] = set()
        visited_objects: Set[Tuple[int, str]] = set()
        pending = [(value, "root")]
        while pending:
            current, context = pending.pop()
            if context == "payload":
                continue
            if isinstance(current, Mapping):
                object_identity = (id(current), context)
                if object_identity in visited_objects:
                    continue
                visited_objects.add(object_identity)
                reference = current.get("$ref")
                if isinstance(reference, str):
                    if self.is_local(reference):
                        target, target_context = self.resolve_with_context(reference)
                        if reference not in resolved:
                            resolved[reference] = copy.deepcopy(target)
                            pending.append((target, target_context))
                    else:
                        external.add(reference)
                pending.extend(
                    (item, self._mapping_child_context(context, key))
                    for key, item in current.items()
                )
            elif isinstance(current, list):
                item_context = self._sequence_item_context(context)
                pending.extend((item, item_context) for item in current)
        return (
            {reference: resolved[reference] for reference in sorted(resolved)},
            tuple(sorted(external)),
        )

    def resolve_reference_chain(self, value: Any, description: str) -> Any:
        current = value
        visited: Set[str] = set()
        while isinstance(current, Mapping) and isinstance(current.get("$ref"), str):
            reference = current["$ref"]
            if not self.is_local(reference):
                return current
            if reference in visited:
                raise OpenApiValidationError("Cyclic local %s reference: %s" % (description, reference))
            visited.add(reference)
            current = self.resolve(reference)
        return current


def _resolve_path_item(
    path_item: Mapping[str, Any],
    resolver: _LocalReferenceResolver,
    resolving: Optional[Set[str]] = None,
    cycles: Optional[Set[str]] = None,
) -> Mapping[str, Any]:
    resolving = set(resolving or ())
    cycles = cycles if cycles is not None else set()
    merged: Dict[str, Any] = {}
    reference = path_item.get("$ref")
    if isinstance(reference, str):
        if not resolver.is_local(reference):
            return {key: value for key, value in path_item.items() if key != "$ref"}
        if reference in resolving:
            cycles.add(reference)
            return {}
        target = resolver.resolve(reference)
        if not isinstance(target, Mapping):
            raise OpenApiValidationError("Referenced OpenAPI Path Item must be an object: %s" % reference)
        merged.update(
            _resolve_path_item(
                target,
                resolver,
                resolving | {reference},
                cycles,
            )
        )
    merged.update({key: value for key, value in path_item.items() if key != "$ref"})
    return merged


def _resolve_parameters(
    parameters: Any,
    resolver: _LocalReferenceResolver,
    location: str,
) -> Sequence[Mapping[str, Any]]:
    if not isinstance(parameters, list):
        raise OpenApiValidationError("OpenAPI %s parameters must be an array" % location)
    resolved = []
    identities = set()
    for parameter in parameters:
        if not isinstance(parameter, Mapping):
            raise OpenApiValidationError("OpenAPI parameter must be an object")
        actual = resolver.resolve_reference_chain(parameter, "parameter")
        if not isinstance(actual, Mapping):
            raise OpenApiValidationError("Referenced OpenAPI parameter must be an object")
        if "$ref" in actual:
            resolved.append(copy.deepcopy(actual))
            continue
        name = actual.get("name")
        parameter_in = actual.get("in")
        if not isinstance(name, str) or not name or not isinstance(parameter_in, str):
            raise OpenApiValidationError("OpenAPI parameters must define name and in")
        identity = (name, parameter_in)
        if identity in identities:
            raise OpenApiValidationError("OpenAPI parameters must be unique by name and in")
        identities.add(identity)
        resolved.append(copy.deepcopy(actual))
    return resolved


def _effective_parameters(
    path_parameters: Sequence[Mapping[str, Any]],
    operation_parameters: Sequence[Mapping[str, Any]],
) -> Sequence[Mapping[str, Any]]:
    effective: MutableMapping[Tuple[str, str], Mapping[str, Any]] = {
        (
            (item["name"], item["in"])
            if "name" in item and "in" in item
            else ("$ref", str(item.get("$ref", "")))
        ): item
        for item in path_parameters
    }
    for item in operation_parameters:
        identity = (
            (item["name"], item["in"])
            if "name" in item and "in" in item
            else ("$ref", str(item.get("$ref", "")))
        )
        effective[identity] = item
    return tuple(effective[key] for key in sorted(effective))


def _validate_path_parameter_contract(
    normalized_path: str,
    effective_parameters: Sequence[Mapping[str, Any]],
) -> None:
    placeholders = set(re.findall(r"\{([^{}]+)\}", normalized_path))
    declared = [item for item in effective_parameters if item.get("in") == "path"]
    declared_names = {item["name"] for item in declared}
    if placeholders != declared_names:
        raise OpenApiValidationError(
            "OpenAPI path placeholders and inherited path parameters must exactly match"
        )
    for parameter in declared:
        if parameter.get("required") is not True:
            raise OpenApiValidationError("OpenAPI path parameters must set required true")


def _validate_schema_dialect(version: str, schemas: Mapping[str, Any]) -> None:
    if version.startswith("3.0."):
        for schema_key, schema in schemas.items():
            if isinstance(schema, bool):
                raise OpenApiValidationError(
                    "OpenAPI 3.0 does not support top-level boolean schema: %s" % schema_key
                )


def normalize_openapi_document(document: Any, source_id: str) -> NormalizedSourceDocument:
    canonical_document = copy.deepcopy(_validate_root(copy.deepcopy(document)))
    paths = canonical_document["paths"]
    components = canonical_document.get("components") or {}
    if not isinstance(components, dict):
        raise OpenApiValidationError("OpenAPI components must be an object")
    schemas = components.get("schemas") or {}
    if not isinstance(schemas, dict):
        raise OpenApiValidationError("OpenAPI components.schemas must be an object")
    _validate_schema_dialect(canonical_document["openapi"], schemas)
    resolver = _LocalReferenceResolver(canonical_document)
    # Validate all non-example local references, including currently unused
    # components. External references remain canonical and are surfaced per endpoint.
    resolver.dependency_closure(canonical_document)

    endpoints = []
    method_path_identities = set()
    normalized_paths: Dict[str, Any] = {}
    for original_path, canonical_path_item in paths.items():
        normalized_path = normalize_path(original_path)
        if not isinstance(canonical_path_item, dict):
            raise OpenApiValidationError("OpenAPI path item must be an object")
        path_item_cycles: Set[str] = set()
        resolved_path_item = _resolve_path_item(
            canonical_path_item,
            resolver,
            cycles=path_item_cycles,
        )
        if path_item_cycles and not any(
            field.lower() in HTTP_METHODS for field in resolved_path_item
        ):
            raise OpenApiValidationError(
                "Cyclic local Path Item reference: %s"
                % sorted(path_item_cycles)[0]
            )
        for field in resolved_path_item:
            lowered = field.lower()
            if lowered not in HTTP_METHODS and field not in PATH_ITEM_FIELDS and not field.lower().startswith("x-"):
                raise OpenApiValidationError("Invalid OpenAPI method or path field: %s" % field)

        path_parameters = _resolve_parameters(
            resolved_path_item.get("parameters", []), resolver, "path item"
        )
        for field, value in resolved_path_item.items():
            lowered = field.lower()
            if lowered not in HTTP_METHODS:
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
            operation_parameters = _resolve_parameters(
                document_operation.get("parameters", []), resolver, "operation"
            )
            effective_parameters = _effective_parameters(path_parameters, operation_parameters)
            _validate_path_parameter_contract(normalized_path, effective_parameters)

            operation = copy.deepcopy(document_operation)
            operation["path_parameters"] = copy.deepcopy(path_parameters)
            dependency_seed = {
                "path_item": canonical_path_item,
                "operation": document_operation,
                "effective_parameters": effective_parameters,
            }
            dependencies, external_references = resolver.dependency_closure(dependency_seed)
            if dependencies:
                operation["resolved_dependencies"] = dependencies
            if external_references:
                operation["external_references"] = list(external_references)
            operation_id = str(operation.get("operationId") or "")
            tags = operation.get("tags") or []
            if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
                raise OpenApiValidationError("OpenAPI operation tags must be an array of strings")
            endpoints.append(
                NormalizedEndpoint(
                    stable_key=stable_endpoint_key(source_id, operation_id, method, normalized_path),
                    operation_id=operation_id,
                    method=method,
                    path=normalized_path,
                    normalized_path=normalized_path,
                    summary=str(operation.get("summary") or ""),
                    tags=tuple(tags),
                    operation=operation,
                )
            )

        if normalized_path in normalized_paths:
            raise OpenApiValidationError("OpenAPI contains duplicate normalized paths")
        normalized_paths[normalized_path] = copy.deepcopy(canonical_path_item)

    canonical_document["paths"] = normalized_paths
    endpoints.sort(key=lambda item: (item.normalized_path, item.method, item.operation_id))
    document_hash = hashlib.sha256(_canonical_json(canonical_document).encode("utf-8")).hexdigest()
    normalized_schemas = {str(key): copy.deepcopy(value) for key, value in sorted(schemas.items())}
    return NormalizedSourceDocument(
        document=canonical_document,
        document_hash=document_hash,
        endpoints=tuple(endpoints),
        schemas=normalized_schemas,
    )
