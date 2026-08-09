"""Public contracts and strict input parsing for versioned API cases."""

import copy
from dataclasses import dataclass
from datetime import datetime
import json
from types import MappingProxyType
from typing import Any, Mapping, Optional, Tuple


PRIORITIES = frozenset({"P0", "P1", "P2", "P3"})
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
ASSERTION_TYPES = frozenset({"status_code", "json_path", "header", "response_time", "schema"})
ASSERTION_OPERATORS = frozenset(
    {
        "equals",
        "not_equals",
        "contains",
        "not_contains",
        "exists",
        "not_exists",
        "greater_than",
        "less_than",
        "matches",
        "in",
    }
)
EXTRACTION_TYPES = frozenset({"json_path", "header", "cookie", "status_code"})
PROCESSING_ACTIONS = frozenset(
    {"set_variable", "copy_variable", "remove_variable", "json_encode", "json_decode"}
)


class CasePayloadError(ValueError):
    """Raised when a case write payload is structurally invalid."""


def _frozen_mapping(value):
    return MappingProxyType(copy.deepcopy(dict(value)))


def _require_mapping(value, field):
    if not isinstance(value, dict):
        raise CasePayloadError(f"{field} must be an object")
    return value


def _reject_unknown(value, allowed, field):
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise CasePayloadError(f"{field} contains unknown field: {unknown[0]}")


def _text(value, field, *, minimum=1, maximum=1000):
    if not isinstance(value, str):
        raise CasePayloadError(f"{field} must be a string")
    if len(value) < minimum or len(value) > maximum:
        raise CasePayloadError(f"{field} length must be between {minimum} and {maximum}")
    return value


def _json_value(value, field):
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise CasePayloadError(f"{field} must contain JSON values") from exc
    if len(encoded.encode("utf-8")) > 1_000_000:
        raise CasePayloadError(f"{field} exceeds the 1 MB limit")
    return copy.deepcopy(value)


def _named_mapping(value, field):
    mapping = _require_mapping(value, field)
    for key in mapping:
        _text(key, f"{field} key", maximum=200)
    return _json_value(mapping, field)


def _parse_request(value):
    value = _require_mapping(value, "request")
    _reject_unknown(
        value,
        {"method", "path", "service", "path_params", "query", "headers", "cookies", "body"},
        "request",
    )
    required = {"method", "path", "path_params", "query", "headers", "cookies", "body"}
    missing = sorted(required - set(value))
    if missing:
        raise CasePayloadError(f"request is missing field: {missing[0]}")
    method = _text(value["method"], "request.method", maximum=16).upper()
    if method not in HTTP_METHODS:
        raise CasePayloadError("request.method is not supported")
    return {
        "method": method,
        "path": _text(value["path"], "request.path", maximum=4000),
        "service": _text(value.get("service", "default"), "request.service", maximum=200),
        "path_params": _named_mapping(value["path_params"], "request.path_params"),
        "query": _named_mapping(value["query"], "request.query"),
        "headers": _named_mapping(value["headers"], "request.headers"),
        "cookies": _named_mapping(value["cookies"], "request.cookies"),
        "body": _json_value(value["body"], "request.body"),
    }


def _parse_data_rows(value):
    if not isinstance(value, list) or len(value) > 200:
        raise CasePayloadError("data_rows must be an array with at most 200 entries")
    rows = []
    names = set()
    for index, row in enumerate(value):
        row = _require_mapping(row, f"data_rows[{index}]")
        _reject_unknown(row, {"name", "values", "enabled"}, f"data_rows[{index}]")
        name = _text(row.get("name"), f"data_rows[{index}].name", maximum=200)
        if name in names:
            raise CasePayloadError("data_rows names must be unique")
        names.add(name)
        enabled = row.get("enabled", True)
        if not isinstance(enabled, bool):
            raise CasePayloadError(f"data_rows[{index}].enabled must be a boolean")
        rows.append({"name": name, "values": _named_mapping(row.get("values"), "data row values"), "enabled": enabled})
    return rows


def _parse_assertions(value):
    if not isinstance(value, list) or len(value) > 200:
        raise CasePayloadError("assertions must be an array with at most 200 entries")
    assertions = []
    allowed = {"type", "operator", "expected", "path", "name", "timeout_ms", "enabled"}
    for index, item in enumerate(value):
        item = _require_mapping(item, f"assertions[{index}]")
        _reject_unknown(item, allowed, f"assertions[{index}]")
        assertion_type = _text(item.get("type"), f"assertions[{index}].type", maximum=64)
        operator = _text(item.get("operator"), f"assertions[{index}].operator", maximum=32)
        if assertion_type not in ASSERTION_TYPES:
            raise CasePayloadError(f"assertions[{index}].type is not supported")
        if operator not in ASSERTION_OPERATORS:
            raise CasePayloadError(f"assertions[{index}].operator is not supported")
        timeout_ms = item.get("timeout_ms", 0)
        if not isinstance(timeout_ms, int) or isinstance(timeout_ms, bool) or not 0 <= timeout_ms <= 60_000:
            raise CasePayloadError(f"assertions[{index}].timeout_ms is invalid")
        enabled = item.get("enabled", True)
        if not isinstance(enabled, bool):
            raise CasePayloadError(f"assertions[{index}].enabled must be a boolean")
        parsed = {
            "type": assertion_type,
            "operator": operator,
            "expected": _json_value(item.get("expected"), "assertion expected"),
            "timeout_ms": timeout_ms,
            "enabled": enabled,
        }
        if "path" in item:
            parsed["path"] = _text(item["path"], f"assertions[{index}].path", maximum=1000)
        if "name" in item:
            parsed["name"] = _text(item["name"], f"assertions[{index}].name", maximum=200)
        assertions.append(parsed)
    return assertions


def _parse_extractions(value):
    if not isinstance(value, list) or len(value) > 200:
        raise CasePayloadError("extractions must be an array with at most 200 entries")
    extractions = []
    allowed = {"target", "type", "path", "name", "required", "default"}
    targets = set()
    for index, item in enumerate(value):
        item = _require_mapping(item, f"extractions[{index}]")
        _reject_unknown(item, allowed, f"extractions[{index}]")
        target = _text(item.get("target"), f"extractions[{index}].target", maximum=200)
        if target in targets:
            raise CasePayloadError("extraction targets must be unique")
        targets.add(target)
        extraction_type = _text(item.get("type"), f"extractions[{index}].type", maximum=64)
        if extraction_type not in EXTRACTION_TYPES:
            raise CasePayloadError(f"extractions[{index}].type is not supported")
        required = item.get("required", True)
        if not isinstance(required, bool):
            raise CasePayloadError(f"extractions[{index}].required must be a boolean")
        parsed = {"target": target, "type": extraction_type, "required": required}
        for field in ("path", "name"):
            if field in item:
                parsed[field] = _text(item[field], f"extractions[{index}].{field}", maximum=1000)
        if "default" in item:
            parsed["default"] = _json_value(item["default"], "extraction default")
        extractions.append(parsed)
    return extractions


def _parse_dependencies(value):
    if not isinstance(value, list) or len(value) > 100:
        raise CasePayloadError("dependencies must be an array with at most 100 entries")
    dependencies = []
    for index, item in enumerate(value):
        item = _require_mapping(item, f"dependencies[{index}]")
        _reject_unknown(
            item,
            {"case_version_id", "required", "exports", "condition"},
            f"dependencies[{index}]",
        )
        required = item.get("required", True)
        if not isinstance(required, bool):
            raise CasePayloadError(f"dependencies[{index}].required must be a boolean")
        exports = item.get("exports", [])
        if not isinstance(exports, list) or len(exports) > 100 or not all(isinstance(name, str) for name in exports):
            raise CasePayloadError(f"dependencies[{index}].exports must be a string array")
        parsed = {
            "case_version_id": _text(item.get("case_version_id"), f"dependencies[{index}].case_version_id", maximum=100),
            "required": required,
            "exports": [_text(name, "dependency export", maximum=200) for name in exports],
        }
        if "condition" in item:
            parsed["condition"] = _text(item["condition"], f"dependencies[{index}].condition", maximum=1000)
        dependencies.append(parsed)
    return dependencies


def _parse_processing(value):
    value = _require_mapping(value, "processing")
    _reject_unknown(value, {"pre", "post"}, "processing")
    result = {}
    allowed = {"action", "name", "value", "source", "target"}
    for phase in ("pre", "post"):
        actions = value.get(phase, [])
        if not isinstance(actions, list) or len(actions) > 100:
            raise CasePayloadError(f"processing.{phase} must be an array with at most 100 entries")
        parsed_actions = []
        for index, item in enumerate(actions):
            item = _require_mapping(item, f"processing.{phase}[{index}]")
            _reject_unknown(item, allowed, f"processing.{phase}[{index}]")
            action = _text(item.get("action"), f"processing.{phase}[{index}].action", maximum=64)
            if action not in PROCESSING_ACTIONS:
                raise CasePayloadError(f"processing.{phase}[{index}].action is not supported")
            required_fields = {
                "set_variable": {"name", "value"},
                "copy_variable": {"source", "target"},
                "remove_variable": {"name"},
                "json_encode": {"source", "target"},
                "json_decode": {"source", "target"},
            }[action]
            missing = sorted(required_fields - set(item))
            if missing:
                raise CasePayloadError(
                    f"processing.{phase}[{index}] action {action} requires {missing[0]}"
                )
            parsed = {"action": action}
            for field in ("name", "source", "target"):
                if field in item:
                    parsed[field] = _text(item[field], f"processing.{phase}[{index}].{field}", maximum=200)
            if "value" in item:
                parsed["value"] = _json_value(item["value"], "processing value")
            parsed_actions.append(parsed)
        result[phase] = parsed_actions
    return result


def parse_case_payload(payload):
    payload = _require_mapping(payload, "case")
    allowed = {
        "name",
        "purpose",
        "priority",
        "request",
        "data_rows",
        "assertions",
        "extractions",
        "dependencies",
        "processing",
    }
    _reject_unknown(payload, allowed, "case")
    missing = sorted(allowed - set(payload))
    if missing:
        raise CasePayloadError(f"case is missing field: {missing[0]}")
    priority = _text(payload["priority"], "priority", maximum=16)
    if priority not in PRIORITIES:
        raise CasePayloadError("priority is not supported")
    return {
        "name": _text(payload["name"], "name", maximum=300),
        "purpose": _text(payload["purpose"], "purpose", maximum=10_000),
        "priority": priority,
        "request": _parse_request(payload["request"]),
        "data_rows": _parse_data_rows(payload["data_rows"]),
        "assertions": _parse_assertions(payload["assertions"]),
        "extractions": _parse_extractions(payload["extractions"]),
        "dependencies": _parse_dependencies(payload["dependencies"]),
        "processing": _parse_processing(payload["processing"]),
    }


@dataclass(frozen=True)
class DataRowView:
    name: str
    values: Mapping[str, Any]
    enabled: bool
    sequence: int

    def __post_init__(self):
        object.__setattr__(self, "values", _frozen_mapping(self.values))


@dataclass(frozen=True)
class AssertionView:
    type: str
    operator: str
    expected: Any
    path: Optional[str]
    name: Optional[str]
    timeout_ms: int
    enabled: bool
    sequence: int


@dataclass(frozen=True)
class ExtractionView:
    target: str
    type: str
    path: Optional[str]
    name: Optional[str]
    required: bool
    default: Any


@dataclass(frozen=True)
class CaseVersionView:
    id: str
    case_id: str
    project_id: str
    endpoint_id: str
    name: str
    status: str
    origin: str
    version: int
    purpose: str
    priority: str
    request: Mapping[str, Any]
    data_rows: Tuple[DataRowView, ...]
    assertions: Tuple[AssertionView, ...]
    extractions: Tuple[ExtractionView, ...]
    dependencies: Tuple[Mapping[str, Any], ...]
    processing: Mapping[str, Any]
    validation_summary: Mapping[str, Any]
    created_at: datetime
    updated_at: datetime

    def __post_init__(self):
        object.__setattr__(self, "request", _frozen_mapping(self.request))
        object.__setattr__(self, "data_rows", tuple(self.data_rows))
        object.__setattr__(self, "assertions", tuple(self.assertions))
        object.__setattr__(self, "extractions", tuple(self.extractions))
        object.__setattr__(self, "dependencies", tuple(_frozen_mapping(item) for item in self.dependencies))
        object.__setattr__(self, "processing", _frozen_mapping(self.processing))
        object.__setattr__(self, "validation_summary", _frozen_mapping(self.validation_summary))


@dataclass(frozen=True)
class CaseView:
    id: str
    project_id: str
    endpoint_id: str
    name: str
    status: str
    origin: str
    active_version_id: Optional[str]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class BaselineView:
    id: str
    project_id: str
    case_id: str
    case_version_id: str
    environment_revision_id: str
    debug_execution_case_id: str
    status: str
    adopted_by: str
    adopted_at: datetime
