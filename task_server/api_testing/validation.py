"""Deterministic, network-free validation for API case drafts."""

from dataclasses import dataclass
import re
from typing import Any, Mapping, Tuple
from urllib.parse import urlsplit

from .services.workflow_policy import (
    is_print_cancel_step,
    is_print_dispatch_endpoint,
    print_task_extraction_targets,
)


VARIABLE_PATTERN = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_.-]*)\s*\}\}")
VARIABLE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-5][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}$"
)


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    field: str
    message: str


@dataclass(frozen=True)
class ValidationResult:
    errors: Tuple[ValidationIssue, ...]
    warnings: Tuple[ValidationIssue, ...]

    @property
    def valid(self):
        return not self.errors


def _issue(target, code, field, message):
    target.append(ValidationIssue(code, field, message))


def _is_placeholder(value):
    return isinstance(value, str) and VARIABLE_PATTERN.fullmatch(value.strip()) is not None


def _variables_in(value):
    names = set()
    if isinstance(value, str):
        names.update(VARIABLE_PATTERN.findall(value))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            names.update(_variables_in(key))
            names.update(_variables_in(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            names.update(_variables_in(item))
    return names


def _malformed_placeholder_fields(value, field="request"):
    invalid = []
    if isinstance(value, str):
        scrubbed = VARIABLE_PATTERN.sub("", value)
        if "{{" in scrubbed or "}}" in scrubbed:
            invalid.append(field)
    elif isinstance(value, Mapping):
        for key, item in value.items():
            invalid.extend(_malformed_placeholder_fields(key, f"{field}.<key>"))
            invalid.extend(_malformed_placeholder_fields(item, f"{field}.{key}"))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            invalid.extend(_malformed_placeholder_fields(item, f"{field}[{index}]"))
    return invalid


def _available_environment_variables(metadata):
    available = set()
    variables = metadata.get("variables", {}) if isinstance(metadata, Mapping) else {}
    if isinstance(variables, Mapping):
        for name, value in variables.items():
            if isinstance(value, Mapping) and value.get("configured") is False:
                continue
            available.add(name)
    return available


def _available_environment_headers(metadata):
    available = set()
    headers = metadata.get("headers", {}) if isinstance(metadata, Mapping) else {}
    if isinstance(headers, Mapping):
        for name, value in headers.items():
            if isinstance(value, Mapping) and value.get("configured") is False:
                continue
            available.add(str(name).lower())
    return available


def _item_value(item, name, default=None):
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _validate_response_rules(assertions, extractions, field, errors):
    prefix = f"{field}." if field else ""
    for index, assertion in enumerate(assertions):
        assertion_type = _item_value(assertion, "type")
        path = _item_value(assertion, "path")
        name = _item_value(assertion, "name")
        if assertion_type == "json_path" and (
            not path or not path.startswith("$")
        ):
            _issue(
                errors,
                "assertion_path_invalid",
                f"{prefix}assertions[{index}].path",
                "JSONPath assertions must start with $",
            )
        if assertion_type == "header" and not name:
            _issue(
                errors,
                "assertion_header_invalid",
                f"{prefix}assertions[{index}].name",
                "header assertions require a header name",
            )

    for index, extraction in enumerate(extractions):
        target = _item_value(extraction, "target", "")
        extraction_type = _item_value(extraction, "type")
        path = _item_value(extraction, "path")
        name = _item_value(extraction, "name")
        if not VARIABLE_NAME_PATTERN.fullmatch(target):
            _issue(
                errors,
                "extraction_target_invalid",
                f"{prefix}extractions[{index}].target",
                "extraction target is not a valid variable name",
            )
        if extraction_type == "json_path" and (
            not path or not path.startswith("$")
        ):
            _issue(
                errors,
                "extraction_path_invalid",
                f"{prefix}extractions[{index}].path",
                "JSONPath extraction must start with $",
            )
        if extraction_type in {"header", "cookie"} and not name:
            _issue(
                errors,
                "extraction_name_invalid",
                f"{prefix}extractions[{index}].name",
                "header and cookie extraction require a name",
            )


def _validate_inline_step(step, field, available, errors):
    request = step.get("request", {})
    if _unsafe_absolute_path(request.get("path")):
        _issue(
            errors,
            "unsafe_absolute_url",
            f"{field}.request.path",
            "case paths must be relative to the selected environment",
        )
    for invalid_field in _malformed_placeholder_fields(
        request, f"{field}.request"
    ):
        _issue(
            errors,
            "placeholder_invalid",
            invalid_field,
            "placeholder syntax must be {{variableName}}",
        )
    required = set(step.get("required_variables", []))
    missing = sorted((_variables_in(request) | required) - available)
    for name in missing:
        _issue(
            errors,
            "undefined_variable",
            f"{field}.request",
            f"variable is undefined: {name}",
        )
    _validate_response_rules(
        step.get("assertions", []),
        step.get("extractions", []),
        field,
        errors,
    )
    return {
        _item_value(extraction, "target")
        for extraction in step.get("extractions", [])
        if VARIABLE_NAME_PATTERN.fullmatch(
            str(_item_value(extraction, "target", ""))
        )
    }


def _json_type_matches(value, schema_type):
    if _is_placeholder(value) or schema_type is None:
        return True
    if isinstance(schema_type, list):
        return any(_json_type_matches(value, item) for item in schema_type)
    return {
        "null": value is None,
        "boolean": isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "string": isinstance(value, str),
        "array": isinstance(value, list),
        "object": isinstance(value, dict),
    }.get(schema_type, True)


def _resolve_schema(schema, operation):
    current = schema
    visited = set()
    dependencies = operation.get("resolved_dependencies", {})
    while isinstance(current, Mapping) and isinstance(current.get("$ref"), str):
        reference = current["$ref"]
        if reference in visited or reference not in dependencies:
            return current
        visited.add(reference)
        current = dependencies[reference]
    return current


def _validate_value(value, schema, operation, field, errors):
    schema = _resolve_schema(schema, operation)
    if not isinstance(schema, Mapping):
        return
    schema_type = schema.get("type")
    if not _json_type_matches(value, schema_type):
        _issue(errors, "body_type_mismatch", field, f"value does not match OpenAPI type {schema_type}")
        return
    if isinstance(value, dict) and (schema_type == "object" or "properties" in schema):
        required = schema.get("required", [])
        for name in required if isinstance(required, list) else []:
            if name not in value:
                _issue(errors, "body_required_property", f"{field}.{name}", "required body property is missing")
        properties = schema.get("properties", {})
        if isinstance(properties, Mapping):
            for name, item in value.items():
                if name in properties:
                    _validate_value(item, properties[name], operation, f"{field}.{name}", errors)
    if isinstance(value, list) and isinstance(schema.get("items"), Mapping):
        for index, item in enumerate(value):
            _validate_value(item, schema["items"], operation, f"{field}[{index}]", errors)


def _request_body_schema(operation):
    request_body = _resolve_schema(operation.get("requestBody"), operation)
    if not isinstance(request_body, Mapping):
        return False, None
    content = request_body.get("content", {})
    if not isinstance(content, Mapping):
        return bool(request_body.get("required")), None
    media = content.get("application/json")
    if not isinstance(media, Mapping):
        media = next((item for item in content.values() if isinstance(item, Mapping)), None)
    return bool(request_body.get("required")), media.get("schema") if isinstance(media, Mapping) else None


def _unsafe_absolute_path(path):
    if not isinstance(path, str):
        return False
    parsed = urlsplit(path)
    return bool(parsed.scheme or parsed.netloc or path.startswith("//"))


def validate_case(
    case_version,
    endpoint,
    environment_metadata,
    dependency_metadata=None,
):
    errors = []
    warnings = []
    request = dict(case_version.request)
    operation = endpoint.operation if isinstance(endpoint.operation, Mapping) else {}

    if case_version.endpoint_id != endpoint.id:
        _issue(errors, "endpoint_identity_mismatch", "endpoint_id", "case version belongs to a different source endpoint")

    if request.get("method") != endpoint.method.upper():
        _issue(errors, "method_mismatch", "request.method", "request method differs from the source endpoint")
    if request.get("path") != endpoint.path:
        _issue(errors, "path_mismatch", "request.path", "request path differs from the source endpoint")
    if _unsafe_absolute_path(request.get("path")):
        _issue(errors, "unsafe_absolute_url", "request.path", "case paths must be relative to the selected environment")
    for field in _malformed_placeholder_fields(request):
        _issue(errors, "placeholder_invalid", field, "placeholder syntax must be {{variableName}}")

    location_fields = {
        "path": "path_params",
        "query": "query",
        "header": "headers",
        "cookie": "cookies",
    }
    parameters = []
    seen_parameters = set()
    for collection in (
        operation.get("path_parameters", []),
        operation.get("parameters", []),
    ):
        if not isinstance(collection, list):
            continue
        for parameter in collection:
            if not isinstance(parameter, Mapping):
                continue
            identity = (parameter.get("in"), parameter.get("name"))
            if identity not in seen_parameters:
                seen_parameters.add(identity)
                parameters.append(parameter)
    for parameter in parameters:
        if not isinstance(parameter, Mapping):
            continue
        location = parameter.get("in")
        name = parameter.get("name")
        target_field = location_fields.get(location)
        if not target_field or not isinstance(name, str):
            continue
        supplied = request.get(target_field, {})
        explicitly_supplied = isinstance(supplied, Mapping) and name in supplied
        present = explicitly_supplied
        if location == "header" and name.lower() in _available_environment_headers(
            environment_metadata
        ):
            present = True
        if parameter.get("required") and not present:
            _issue(errors, "required_parameter_missing", f"request.{target_field}.{name}", "required OpenAPI parameter is missing")
        elif not parameter.get("required") and not present:
            _issue(warnings, "optional_parameter_omitted", f"request.{target_field}.{name}", "optional OpenAPI parameter is omitted")
        if explicitly_supplied:
            schema = parameter.get("schema", {})
            schema = _resolve_schema(schema, operation)
            if isinstance(schema, Mapping) and not _json_type_matches(supplied[name], schema.get("type")):
                _issue(errors, "parameter_type_mismatch", f"request.{target_field}.{name}", "parameter type differs from OpenAPI")

    body_required, body_schema = _request_body_schema(operation)
    body = request.get("body")
    if body_required and body is None:
        _issue(errors, "body_required", "request.body", "OpenAPI request body is required")
    elif body is not None and body_schema is not None:
        _validate_value(body, body_schema, operation, "request.body", errors)

    available = _available_environment_variables(environment_metadata)
    dependency_metadata = dependency_metadata or {}
    for dependency in case_version.dependencies:
        identifier = dependency.get("case_version_id", "")
        metadata = dependency_metadata.get(identifier, {})
        if metadata.get("status") == "trusted":
            available.update(
                set(dependency.get("exports", []))
                & set(metadata.get("exports", []))
            )
    for action in case_version.processing.get("pre", []):
        if action.get("action") == "set_variable" and isinstance(action.get("name"), str):
            available.add(action["name"])
    setup_steps = case_version.processing.get("setup_steps", [])
    for index, step in enumerate(setup_steps):
        if not step.get("enabled", True):
            continue
        available.update(
            _validate_inline_step(
                step,
                f"processing.setup_steps[{index}]",
                available,
                errors,
            )
        )

    request_variables = _variables_in(request)
    enabled_rows = [row for row in case_version.data_rows if row.enabled]
    validation_rows = enabled_rows or [None]
    for row in validation_rows:
        row_available = set(available)
        if row is not None:
            row_available.update(row.values.keys())
        missing = sorted(request_variables - row_available)
        for name in missing:
            field = "request" if row is None else f"data_rows[{row.name}].request"
            _issue(errors, "undefined_variable", field, f"variable is undefined: {name}")

    _validate_response_rules(
        case_version.assertions, case_version.extractions, "", errors
    )

    cleanup_available = set(available)
    cleanup_available.update(
        _item_value(extraction, "target")
        for extraction in case_version.extractions
        if VARIABLE_NAME_PATTERN.fullmatch(
            str(_item_value(extraction, "target", ""))
        )
    )
    cleanup_steps = case_version.processing.get("cleanup_steps", [])
    for index, step in enumerate(cleanup_steps):
        if not step.get("enabled", True):
            continue
        cleanup_available.update(
            _validate_inline_step(
                step,
                f"processing.cleanup_steps[{index}]",
                cleanup_available,
                errors,
            )
        )

    if is_print_dispatch_endpoint(endpoint):
        task_targets = print_task_extraction_targets(case_version.extractions)
        if not task_targets:
            _issue(
                errors,
                "print_task_extraction_required",
                "extractions",
                "print dispatch must extract the task identifier returned by this execution",
            )
        if not task_targets or not any(
            is_print_cancel_step(step, task_targets) for step in cleanup_steps
        ):
            _issue(
                errors,
                "print_cleanup_required",
                "processing.cleanup_steps",
                "print dispatch must cancel the task created by this execution",
            )

    for index, dependency in enumerate(case_version.dependencies):
        identifier = dependency.get("case_version_id", "")
        if not UUID_PATTERN.fullmatch(identifier) or identifier == case_version.id:
            _issue(errors, "dependency_invalid", f"dependencies[{index}].case_version_id", "dependency must reference another case version UUID")
            continue
        metadata = dependency_metadata.get(identifier, {})
        if metadata.get("status") == "missing" or not metadata:
            _issue(errors, "dependency_not_found", f"dependencies[{index}].case_version_id", "dependency case version does not exist")
            continue
        if metadata.get("status") == "project_mismatch":
            _issue(errors, "dependency_project_mismatch", f"dependencies[{index}].case_version_id", "dependency belongs to a different project")
            continue
        actual_exports = set(metadata.get("exports", []))
        for name in dependency.get("exports", []):
            if not VARIABLE_NAME_PATTERN.fullmatch(name):
                _issue(errors, "dependency_invalid", f"dependencies[{index}].exports", "dependency exports must be valid variable names")
            elif name not in actual_exports:
                _issue(errors, "dependency_export_invalid", f"dependencies[{index}].exports", f"dependency does not extract variable: {name}")

    return ValidationResult(tuple(errors), tuple(warnings))
