"""Deterministic executable contracts for API test cases."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, Iterable, List, Set, Tuple


CONTRACT_VERSION = "api_case_contract/v1"
_MISSING = object()
_SENSITIVE_HEADER_PARTS = (
    "authorization",
    "apikey",
    "accesstoken",
    "accesskey",
    "token",
    "secret",
    "signature",
    "credential",
    "password",
    "cookie",
)
_SENSITIVE_FIELD_EXACT = frozenset({
    "authorization",
    "apikey",
    "accesskey",
    "token",
    "secret",
    "signature",
    "credential",
    "credentials",
    "password",
    "passwd",
    "cookie",
})
_SENSITIVE_FIELD_SUFFIXES = (
    "apikey",
    "accesskey",
    "token",
    "secret",
    "credential",
    "credentials",
    "password",
    "passwd",
)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(authorization|x[-_]?api[-_]?key|api[-_]?key|access[-_]?token|"
    r"refresh[-_]?token|token|secret|password|cookie)\b\s*[:=]\s*"
    r"(?:bearer\s+)?[^\s,;}\]]+"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized_name(name: Any) -> str:
    return "".join(char for char in _text(name).lower() if char.isalnum())


def is_sensitive_field_name(name: Any) -> bool:
    normalized = _normalized_name(name)
    return bool(normalized) and (
        normalized in _SENSITIVE_FIELD_EXACT
        or any(normalized.endswith(suffix) for suffix in _SENSITIVE_FIELD_SUFFIXES)
    )


def is_sensitive_header_name(name: Any) -> bool:
    normalized = _normalized_name(name)
    return bool(normalized) and any(part in normalized for part in _SENSITIVE_HEADER_PARTS)


def _explicit_value(schema: Any, owner: Any = None) -> Any:
    for source in (owner, schema):
        if not isinstance(source, dict):
            continue
        for key in ("example", "default", "const"):
            if key in source:
                return source[key]
        enum = source.get("enum")
        if isinstance(enum, list) and enum:
            return enum[0]
    return _MISSING


def _materialize_schema(
    schema: Any,
    prefix: str,
    required: bool = False,
    sensitive: bool = False,
    provided_value: Any = _MISSING,
) -> Tuple[Any, List[str]]:
    if sensitive:
        return (_MISSING, [prefix] if required else [])
    if not isinstance(schema, dict) or not schema:
        return (_MISSING, [prefix] if required else [])
    explicit = provided_value if provided_value is not _MISSING else _explicit_value(schema)
    schema_type = _text(schema.get("type"))
    properties = schema.get("properties")
    if isinstance(explicit, dict) or schema_type == "object" or isinstance(properties, dict):
        result: Dict[str, Any] = {}
        missing: List[str] = []
        property_map = properties if isinstance(properties, dict) else {}
        required_names = {
            _text(name)
            for name in (schema.get("required") or [])
            if _text(name)
        }
        for name, child_schema in property_map.items():
            child_name = _text(name)
            if not child_name:
                continue
            child_required = child_name in required_names
            value, child_missing = _materialize_schema(
                child_schema,
                f"{prefix}.{child_name}",
                required=child_required,
                sensitive=is_sensitive_field_name(child_name),
                provided_value=explicit.get(child_name, _MISSING) if isinstance(explicit, dict) else _MISSING,
            )
            if value is not _MISSING:
                result[child_name] = value
            missing.extend(child_missing)
        for name in sorted(required_names - set(property_map) - set(result)):
            missing.append(f"{prefix}.{name}")
        if result or not required or isinstance(explicit, dict):
            return result, missing
        return _MISSING, missing or [prefix]
    if explicit is not _MISSING:
        return explicit, []
    return (_MISSING, [prefix] if required else [])


def _request_parameters(endpoint: Dict[str, Any]) -> Tuple[Dict[str, Dict[str, Any]], List[str], List[str]]:
    buckets: Dict[str, Dict[str, Any]] = {
        "path": {},
        "query": {},
        "header": {},
    }
    missing: List[str] = []
    issues: List[str] = []
    request_keys = {
        "path": "path_params",
        "query": "query",
        "header": "headers",
    }
    for parameter in endpoint.get("parameters") or []:
        if not isinstance(parameter, dict):
            continue
        name = _text(parameter.get("name"))
        location = _text(parameter.get("in")).lower()
        if not name:
            continue
        if location not in buckets:
            if parameter.get("required"):
                missing.append(f"request.parameters.{location or 'unknown'}.{name}")
            issues.append(f"unsupported_parameter_location:{location or 'unknown'}:{name}")
            continue
        sensitive = (
            is_sensitive_header_name(name)
            if location == "header"
            else is_sensitive_field_name(name)
        )
        if sensitive:
            if parameter.get("required"):
                missing.append(f"request.{request_keys[location]}.{name}")
            continue
        value = _explicit_value(parameter.get("schema"), parameter)
        if value is not _MISSING:
            buckets[location][name] = value
        elif parameter.get("required"):
            missing.append(f"request.{request_keys[location]}.{name}")
    return buckets, missing, issues


def _response_statuses(endpoint: Dict[str, Any], case_type: str) -> List[int | str]:
    statuses: List[int | str] = []
    for response in endpoint.get("responses") or []:
        if not isinstance(response, dict):
            continue
        raw = _text(response.get("status")).upper()
        if not raw:
            continue
        status: int | str
        try:
            status = int(raw)
        except ValueError:
            status = raw
        if case_type in {"positive", "chain"} and (
            (isinstance(status, int) and 200 <= status < 300) or status == "2XX"
        ):
            statuses.append(status)
        elif case_type == "auth" and status in {401, 403}:
            statuses.append(status)
        elif case_type in {"negative", "boundary", "error"} and (
            (isinstance(status, int) and 400 <= status < 500 and status not in {401, 403})
            or status == "4XX"
        ):
            statuses.append(status)
    return sorted(set(statuses), key=lambda item: str(item))


def _has_success_schema(endpoint: Dict[str, Any]) -> bool:
    for response in endpoint.get("responses") or []:
        if not isinstance(response, dict):
            continue
        status = _text(response.get("status")).upper()
        if (status.startswith("2") or status == "2XX") and isinstance(response.get("schema"), dict) and response.get("schema"):
            return True
    return isinstance(endpoint.get("response_schema"), dict) and bool(endpoint.get("response_schema"))


def _normalize_omitted_field(value: Any, endpoint: Dict[str, Any]) -> str:
    target = _text(value)
    if not target:
        return ""
    if target.startswith(("body.", "path.", "query.", "header.")):
        return target
    for parameter in endpoint.get("parameters") or []:
        if isinstance(parameter, dict) and _text(parameter.get("name")) == target:
            location = _text(parameter.get("in")).lower()
            return f"{location}.{target}"
    return f"body.{target}"


def _drop_negative_target(request: Dict[str, Any], target: str) -> None:
    if not target or "." not in target:
        return
    location, dotted_name = target.split(".", 1)
    bucket_name = {
        "path": "path_params",
        "query": "query",
        "header": "headers",
        "body": "body",
    }.get(location)
    bucket = request.get(bucket_name) if bucket_name else None
    if not isinstance(bucket, dict):
        return
    parts = [part for part in dotted_name.split(".") if part]
    current = bucket
    for part in parts[:-1]:
        value = current.get(part)
        if not isinstance(value, dict):
            return
        current = value
    if parts:
        current.pop(parts[-1], None)


def _target_missing_key(target: str) -> str:
    if not target or "." not in target:
        return ""
    location, name = target.split(".", 1)
    bucket = {
        "path": "path_params",
        "query": "query",
        "header": "headers",
        "body": "body",
    }.get(location, location)
    return f"request.{bucket}.{name}"


def _required_negative_targets(endpoint: Dict[str, Any]) -> Set[str]:
    targets: Set[str] = set()
    for parameter in endpoint.get("parameters") or []:
        if not isinstance(parameter, dict) or not parameter.get("required"):
            continue
        name = _text(parameter.get("name"))
        location = _text(parameter.get("in")).lower()
        if name and location in {"path", "query", "header"}:
            targets.add(f"{location}.{name}")
    schema = endpoint.get("request_schema")
    if isinstance(schema, dict):
        for name in schema.get("required") or []:
            child_name = _text(name)
            if child_name:
                targets.add(f"body.{child_name}")
    return targets


def _normalize_dependencies(raw: Any) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            case_id = _text(item)
            required = True
        elif isinstance(item, dict):
            case_id = _text(item.get("case_id"))
            required = bool(item.get("required", True))
        else:
            continue
        if not case_id or case_id in seen:
            continue
        seen.add(case_id)
        result.append({"case_id": case_id, "required": required})
    return result


def _normalize_variables(raw: Any) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []
    seen: Set[str] = set()
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name"))
        source = _text(item.get("source"))
        if not name or not source or name in seen:
            continue
        seen.add(name)
        result.append({"name": name, "source": source})
    return result


def endpoint_requires_auth(endpoint: Dict[str, Any]) -> bool:
    security = endpoint.get("security")
    if not isinstance(security, list) or not security:
        return False
    return all(isinstance(requirement, dict) and bool(requirement) for requirement in security)


def _sanitize_text(value: str) -> str:
    return _SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}: [REDACTED]", value)


def _sanitize_openapi_value(
    value: Any,
    field_name: str = "",
    inherited_sensitive: bool = False,
    field_context: str = "schema",
) -> Any:
    field_sensitive = (
        is_sensitive_header_name(field_name)
        if field_context == "header"
        else is_sensitive_field_name(field_name)
    )
    sensitive = inherited_sensitive or field_sensitive
    if isinstance(value, list):
        return [_sanitize_openapi_value(item, "", sensitive, field_context) for item in value]
    if not isinstance(value, dict):
        return _sanitize_text(value) if isinstance(value, str) else copy.deepcopy(value)
    result: Dict[str, Any] = {}
    for key, nested in value.items():
        key_text = _text(key)
        if sensitive and key_text in {"example", "examples", "default", "const", "enum"}:
            continue
        if is_sensitive_field_name(key_text) and key_text not in {"required"}:
            continue
        if key_text == "properties" and isinstance(nested, dict):
            result[key_text] = {
                str(name): _sanitize_openapi_value(schema, str(name), sensitive, "schema")
                for name, schema in nested.items()
            }
            continue
        if key_text == "parameters" and isinstance(nested, list):
            parameters = []
            for item in nested:
                name = _text(item.get("name")) if isinstance(item, dict) else ""
                location = _text(item.get("in")).lower() if isinstance(item, dict) else ""
                parameters.append(_sanitize_openapi_value(
                    item,
                    name,
                    sensitive,
                    "header" if location == "header" else "schema",
                ))
            result[key_text] = parameters
            continue
        result[key_text] = _sanitize_openapi_value(nested, key_text, sensitive, "schema")
    return result


def sanitize_sensitive_data(value: Any) -> Any:
    """Return a recursive public copy suitable for AI and local persistence."""
    return _sanitize_openapi_value(value)


def sanitize_endpoint_for_plan(endpoint: Dict[str, Any]) -> Dict[str, Any]:
    """Keep execution identity/schema shape while removing sensitive OpenAPI values."""
    return sanitize_sensitive_data(endpoint if isinstance(endpoint, dict) else {})


def build_api_case_contract(
    endpoint: Dict[str, Any],
    case_type: str,
    omitted_field: str = "",
    proposed: Dict[str, Any] | None = None,
    known_case_ids: Iterable[str] | None = None,
) -> Dict[str, Any]:
    """Build a contract whose executability is derived only from trusted inputs."""

    proposed_case = proposed if isinstance(proposed, dict) else {}
    normalized_type = _text(case_type).lower()
    if normalized_type not in {"positive", "negative", "auth", "boundary", "chain", "error"}:
        normalized_type = "positive"
    parameter_values, missing, issues = _request_parameters(endpoint)
    for name in list(parameter_values["header"]):
        if is_sensitive_header_name(name):
            parameter_values["header"].pop(name, None)
    if not endpoint_requires_auth(endpoint):
        for parameter in endpoint.get("parameters") or []:
            if not isinstance(parameter, dict) or not parameter.get("required"):
                continue
            if _text(parameter.get("in")).lower() != "header":
                continue
            name = _text(parameter.get("name"))
            if name and is_sensitive_header_name(name):
                missing.append(f"request.headers.{name}")
    request_schema = endpoint.get("request_schema")
    body_required = bool(endpoint.get("request_body_required"))
    if not body_required and _explicit_value(request_schema) is _MISSING:
        body, body_missing = _MISSING, []
    else:
        body, body_missing = _materialize_schema(
            request_schema,
            "request.body",
            required=body_required,
        )
    missing.extend(body_missing)
    request = {
        "method": _text(endpoint.get("method")).upper(),
        "path": _text(endpoint.get("path")),
        "path_params": parameter_values["path"],
        "query": parameter_values["query"],
        "headers": parameter_values["header"],
        "body": {} if body is _MISSING else body,
        "auth_ref": "environment_default" if endpoint_requires_auth(endpoint) else "",
    }
    negative_target = _normalize_omitted_field(
        omitted_field or proposed_case.get("negative_target"),
        endpoint,
    )
    if normalized_type == "negative":
        if negative_target and negative_target in _required_negative_targets(endpoint):
            _drop_negative_target(request, negative_target)
            target_missing = _target_missing_key(negative_target)
            missing = [item for item in missing if item != target_missing]
        else:
            missing.append("negative_target")
    elif normalized_type in {"boundary", "error"}:
        missing.append("request.mutation")
    if normalized_type == "auth":
        request["auth_ref"] = ""
        if not endpoint_requires_auth(endpoint):
            missing.append("endpoint.security")

    statuses = _response_statuses(endpoint, normalized_type)
    assertions: List[Dict[str, Any]] = []
    if statuses:
        assertions.append({"type": "status", "operator": "in", "expected": statuses})
    else:
        missing.append("assertions.status")
    if normalized_type in {"positive", "chain"} and _has_success_schema(endpoint):
        assertions.append({"type": "schema", "schema_ref": "response:2xx"})

    dependencies = _normalize_dependencies(proposed_case.get("dependencies"))
    known = {_text(case_id) for case_id in (known_case_ids or []) if _text(case_id)}
    if known_case_ids is not None:
        for dependency in dependencies:
            case_id = dependency["case_id"]
            if dependency["required"] and case_id not in known:
                missing.append(f"dependencies.{case_id}")

    proposed_request = proposed_case.get("request")
    if isinstance(proposed_request, dict) and (
        _text(proposed_request.get("method")).upper() not in {"", request["method"]}
        or _text(proposed_request.get("path")) not in {"", request["path"]}
    ):
        issues.append("proposed_route_override_ignored")
    normalized_missing = sorted(set(_text(item) for item in missing if _text(item)))
    normalized_issues = sorted(set(_text(item) for item in issues if _text(item)))
    return {
        "contract_version": CONTRACT_VERSION,
        "request": request,
        "assertions": assertions,
        "variables": _normalize_variables(proposed_case.get("variables")),
        "dependencies": dependencies,
        "negative_target": negative_target,
        "readiness": {
            "state": "needs_review" if normalized_missing else "executable",
            "missing": normalized_missing,
            "issues": normalized_issues,
        },
    }


def normalize_api_case_contract(
    proposed: Dict[str, Any],
    endpoint: Dict[str, Any],
    case_type: str = "positive",
    known_case_ids: Iterable[str] | None = None,
) -> Dict[str, Any]:
    proposal = proposed if isinstance(proposed, dict) else {}
    return build_api_case_contract(
        endpoint,
        _text(proposal.get("type")) or case_type,
        omitted_field=_text(proposal.get("negative_target")),
        proposed=proposal,
        known_case_ids=known_case_ids,
    )


def summarize_api_case_readiness(cases: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    case_list = [case for case in (cases or []) if isinstance(case, dict)]
    executable = [
        case for case in case_list
        if (case.get("readiness") or {}).get("state") == "executable"
    ]
    missing = sorted({
        _text(item)
        for case in case_list
        for item in ((case.get("readiness") or {}).get("missing") or [])
        if _text(item)
    })
    executable_count = len(executable)
    total = len(case_list)
    state = "ready" if total > 0 and executable_count == total else "partial" if executable_count else "blocked"
    return {
        "state": state,
        "case_count": total,
        "executable_case_count": executable_count,
        "needs_review_case_count": total - executable_count,
        "can_confirm": executable_count > 0,
        "can_execute": executable_count > 0,
        "missing": missing,
    }


__all__ = [
    "CONTRACT_VERSION",
    "build_api_case_contract",
    "endpoint_requires_auth",
    "is_sensitive_field_name",
    "normalize_api_case_contract",
    "sanitize_sensitive_data",
    "sanitize_endpoint_for_plan",
    "summarize_api_case_readiness",
]
