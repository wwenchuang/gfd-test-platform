"""Read-only response correlation for one reviewed production list/detail pair."""

import re
from collections.abc import Mapping


ACTION = "bind_cleanup_response_id"
SERVICE_BASE = "https://print.wisebeginner3d.com/app"
LIST_PATH = "/print3d/api/v1/level/magicDoodle/list"
DETAIL_PATH = "/print3d/api/v1/level/magicDoodle/detail"
SOURCE_FIELD = "$.data.focusLevelSn"
DETAIL_FIELD = "$.data.levelSn"
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,99}")
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def _field(item, key, default=None):
    return item.get(key, default) if isinstance(item, Mapping) else getattr(item, key, default)


def validate_binding(processing, extractions, dependencies, request):
    pre = processing.get("pre", ())
    markers = [action for action in pre if action.get("action") == ACTION]
    if not markers:
        return None
    binding = markers[0]
    if (len(pre) != 1 or set(binding) != {"action", "source", "name", "target"}
            or binding["source"] != "main" or dependencies or processing.get("setup_steps")
            or processing.get("post") or len(processing.get("cleanup_steps", ())) != 1
            or not all(isinstance(binding[key], str) and _NAME.fullmatch(binding[key]) for key in ("name", "target"))):
        raise ValueError("响应关联仅支持独立主体、一个只读回查步骤，无依赖及额外处理")
    named = [item for item in extractions if _field(item, "target") == binding["name"]]
    if (len(named) != 1 or not _field(named[0], "required", True)
            or _field(named[0], "type") != "json_path" or _field(named[0], "path") != SOURCE_FIELD):
        raise ValueError("主体须从 $.data.focusLevelSn 必需提取回查编号")
    step = processing["cleanup_steps"][0]
    if (not step.get("enabled", True) or step.get("polling") or step.get("only_if_variable")
            or step.get("required_variables")):
        raise ValueError("响应关联需要启用一个无条件且不重试的只读回查")
    for req, path, query in ((request, LIST_PATH, {}), (step["request"], DETAIL_PATH, {"levelSn": "{{"+binding["target"]+"}}"})):
        if (req.get("method") != "GET" or req.get("path") != path or req.get("service", "default") != "default"
                or req.get("query", {}) != query or req.get("path_params") or req.get("headers") or req.get("cookies")
                or req.get("body") is not None or "run_id_binding" in req):
            raise ValueError("仅支持默认服务涂鸦关卡列表 GET 和详情 GET 的唯一 levelSn 查询，禁止覆盖请求")
    return binding


def checked_context(base_url, url, path, headers):
    if (base_url.rstrip("/") != SERVICE_BASE or url.split("?", 1)[0] != SERVICE_BASE + path
            or any(name.lower() not in {"authorization", "biz"} for name in headers)
            or len({name.lower() for name in headers}) != len(headers)):
        raise ValueError("只读回查限定已核验的服务地址、路径和环境请求头")
    # Held only during this call. Never persisted or copied into result/trace.
    return tuple(sorted((name.lower(), value) for name, value in headers.items()))


def response_id(outcome):
    if not outcome or outcome.status != "PASSED" or not outcome.raw_response:
        return None
    response = outcome.raw_response
    body = response.get("body")
    if (response.get("status_code") != 200 or not isinstance(body, dict)
            or type(body.get("code")) is not int or body["code"] != 0 or not isinstance(body.get("data"), dict)):
        return None
    value = body["data"].get("focusLevelSn")
    return value if (type(value) is int and value > 0) or (isinstance(value, str) and _ID.fullmatch(value)) else None
