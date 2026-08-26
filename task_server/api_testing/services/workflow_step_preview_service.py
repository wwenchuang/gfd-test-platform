"""Request-scoped setup-step previews and selectable response fields."""

import re

from ..contracts.case import parse_workflow_step_preview_payload
from ..executor import HttpExecutor
from .case_service import CaseService
from .environment_service import EnvironmentService


_SENSITIVE_FIELD = re.compile(
    r"(?:authorization|cookie|token|password|passwd|secret|api[-_]?key|"
    r"access[-_]?key[-_]?id|access[-_]?id|policy|signature)",
    re.I,
)
_VARIABLE_CHARACTER = re.compile(r"[^A-Za-z0-9_.-]+")
_MAX_FIELDS = 500
_MAX_DEPTH = 16


class WorkflowStepPreviewService:
    def __init__(self, session_factory, *, executor=None):
        self.executor = executor or HttpExecutor(
            CaseService(session_factory), EnvironmentService(session_factory)
        )

    def preview(self, payload):
        environment_revision_id = payload.get("environment_revision_id")
        parsed = parse_workflow_step_preview_payload(
            {
                key: value
                for key, value in payload.items()
                if key != "environment_revision_id"
            }
        )
        result = self.executor.preview_setup_steps(
            environment_revision_id,
            parsed["setup_steps"],
            parsed["target_index"],
            initial_variables=parsed["initial_variables"],
            processing_pre=parsed["processing_pre"],
            extraction_overrides=parsed["extraction_overrides"],
        )
        selectable = (
            flatten_response_fields(result.get("response") or {})
            if result.get("target_reached")
            else {"fields": [], "truncated": False}
        )
        return {**result, **selectable}


def flatten_response_fields(response, *, max_fields=_MAX_FIELDS, max_depth=_MAX_DEPTH):
    fields = []
    truncated = False
    used_targets = set()

    def append(field):
        nonlocal truncated
        if len(fields) >= max_fields:
            truncated = True
            return False
        target = _unique_target(field["suggested_target"], used_targets)
        fields.append({**field, "suggested_target": target})
        return True

    append(
        _field(
            "status_code",
            response.get("status_code"),
            name="status_code",
        )
    )
    for name, value in dict(response.get("headers") or {}).items():
        if not append(_field("header", value, name=str(name))):
            break
    for name, value in dict(response.get("cookies") or {}).items():
        if not append(_field("cookie", value, name=str(name), sensitive=True)):
            break

    def walk(value, path, name, depth, sensitive=False):
        nonlocal truncated
        if len(fields) >= max_fields:
            truncated = True
            return
        named_sensitive = sensitive or bool(_SENSITIVE_FIELD.search(str(name)))
        if depth > max_depth:
            truncated = True
            return
        if isinstance(value, dict):
            if not value:
                append(_field("json_path", value, path=path, name=name, sensitive=named_sensitive))
                return
            for child_name, child in value.items():
                child_name = str(child_name)
                walk(
                    child,
                    _json_child_path(path, child_name),
                    child_name,
                    depth + 1,
                    named_sensitive,
                )
            return
        if isinstance(value, list):
            if not value:
                append(_field("json_path", value, path=path, name=name, sensitive=named_sensitive))
                return
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]", str(index), depth + 1, named_sensitive)
            return
        append(_field("json_path", value, path=path, name=name, sensitive=named_sensitive))

    body = response.get("body")
    if isinstance(body, (dict, list)):
        walk(body, "$", "body", 0)
    return {"fields": fields, "truncated": truncated}


def _field(source, value, *, name, path=None, sensitive=False):
    locator = path if source == "json_path" else name
    target_seed = name if not str(name).isdigit() else "item"
    result = {
        "id": f"{source}:{locator}",
        "source": source,
        "name": name,
        "value": value,
        "value_type": _value_type(value),
        "sensitive": bool(sensitive or _SENSITIVE_FIELD.search(str(name))),
        "suggested_target": _variable_name(target_seed),
    }
    if path is not None:
        result["path"] = path
    return result


def _json_child_path(parent, name):
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return f"{parent}.{name}"
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    return f"{parent}['{escaped}']"


def _variable_name(value):
    normalized = _VARIABLE_CHARACTER.sub("_", str(value)).strip("_.-")
    if not normalized:
        normalized = "value"
    if normalized[0].isdigit():
        normalized = f"value_{normalized}"
    return normalized[:200]


def _unique_target(seed, used):
    target = seed
    suffix = 2
    while target in used:
        target = f"{seed}_{suffix}"
        suffix += 1
    used.add(target)
    return target


def _value_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"
