"""Resolve explicit ID templates from the currently executing database rows."""

from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from functools import wraps
import re

TOKEN = re.compile(r"\{\{@(execution_id|execution_case_id)\}\}")


@dataclass
class _BoundCase:
    version_id: str
    ids: dict
    header_names: set = field(default_factory=set)


_current_case = ContextVar("api_testing_bound_case", default=None)


def bind_persisted_case_ids(run_child):
    @wraps(run_child)
    def wrapped(service, execution_id, child, *args, **kwargs):
        state = _BoundCase(child.case_version_id, {
            "execution_id": str(execution_id), "execution_case_id": str(child.id),
        })
        token = _current_case.set(state)
        try:
            return run_child(service, execution_id, child, *args, **kwargs)
        finally:
            _current_case.reset(token)
    return wrapped


def substitute_request(request, replacement):
    """Transform only explicit templates in direct header and query values."""
    result = dict(request)
    for field_name in ("headers", "query"):
        result[field_name] = {
            key: TOKEN.sub(replacement, value) if isinstance(value, str) else value
            for key, value in request.get(field_name, {}).items()
        }
    return result


def binding_error(request, dependencies, processing):
    if "run_id_binding" not in request:
        return ""
    if type(request["run_id_binding"]) is not int or request["run_id_binding"] != 1:
        return "运行标识绑定版本无效"
    if (request.get("method") not in {"GET", "HEAD"} or dependencies
            or processing.get("setup_steps") or processing.get("cleanup_steps")):
        return "运行标识仅支持无依赖、无前置与清理步骤的独立 GET 或 HEAD 用例"
    keys = [key.lower() for key in request.get("headers", {})]
    if len(keys) != len(set(keys)) or any("{{" in key for key in keys):
        return "使用运行标识时请求头名称必须固定且不区分大小写唯一"
    has_token = any(isinstance(value, str) and TOKEN.search(value)
                    for field_name in ("headers", "query") for value in request.get(field_name, {}).values())
    if not has_token:
        return "请在请求头或查询参数值中插入运行标识，或移除运行标识绑定"
    # Request objects contain only JSON scalars, maps and arrays at this boundary.
    if "{{@" in str(substitute_request(request, "")):
        return "运行标识仅可放在主体请求头或查询参数的字符串值内"
    return ""


def resolve_case_version(version):
    state = _current_case.get()
    if state is None or state.version_id != version.id or "run_id_binding" not in version.request:
        return version
    issue = binding_error(version.request, version.dependencies, version.processing)
    if issue:
        raise ValueError(issue)
    state.header_names = {key.lower() for key, value in version.request.get("headers", {}).items()
                          if isinstance(value, str) and TOKEN.search(value)}
    request = substitute_request(version.request, lambda m: state.ids[m.group(1)])
    return replace(version, request=request)


def omit_bound_header_defaults(headers):
    state = _current_case.get()
    if state is None or not state.header_names:
        return headers
    return {key: value for key, value in headers.items() if key.lower() not in state.header_names}
