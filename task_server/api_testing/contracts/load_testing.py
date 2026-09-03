"""Strict public contracts and Chinese option help for API load testing."""

import copy
import json
import re


LOAD_SCENARIO_MODES = frozenset({"single_interface", "workflow"})
LOAD_STEP_SCOPES = frozenset(
    {"setup_once", "agent_setup", "vu_once", "iteration", "cleanup_once"}
)
LOAD_STEP_ACTIONS = frozenset({"http_request"})
LOAD_SIDE_EFFECTS = frozenset(
    {
        "readonly",
        "creates_owned_resource",
        "mutates_owned_resource",
        "cleanup_owned_resource",
    }
)
DATASET_USAGE_MODES = frozenset({"cycle", "fixed_per_vu", "exclusive_per_iteration"})
RISK_LEVELS = frozenset({"low", "medium", "high"})
HTTP_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
ASSERTION_TYPES = frozenset({"status_code", "json_path", "header", "response_time"})
ASSERTION_OPERATORS = frozenset(
    {"equals", "not_equals", "contains", "not_contains", "exists", "not_exists", "greater_than", "less_than", "matches", "in"}
)
ASSERTION_OPERATOR_MATRIX = {
    "status_code": frozenset({"equals", "not_equals", "in"}),
    "json_path": ASSERTION_OPERATORS,
    "header": frozenset({"equals", "not_equals", "contains", "not_contains", "exists", "not_exists", "matches", "in"}),
    "response_time": frozenset({"greater_than", "less_than"}),
}
EXTRACTION_TYPES = frozenset({"json_path", "header", "cookie", "status_code"})
VARIABLE_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_.-]*$")
STEP_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
REFERENCE_KEYS = frozenset({"$env", "$data", "$extract"})
SENSITIVE_KEY_PARTS = ("authorization", "cookie", "password", "passwd", "token", "secret", "api_key", "apikey")


class LoadScenarioPayloadError(ValueError):
    """Raised when a load scenario would be ambiguous or unsafe to compile."""


def _mapping(value, field):
    if not isinstance(value, dict):
        raise LoadScenarioPayloadError(f"{field} 必须是对象")
    return value


def _reject_unknown(value, allowed, field):
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise LoadScenarioPayloadError(f"{field} 包含不支持字段：{unknown[0]}")


def _text(value, field, *, minimum=1, maximum=1000):
    if not isinstance(value, str) or not minimum <= len(value) <= maximum:
        raise LoadScenarioPayloadError(f"{field} 必须是 {minimum} 到 {maximum} 个字符")
    return value


def _boolean(value, field):
    if not isinstance(value, bool):
        raise LoadScenarioPayloadError(f"{field} 必须是布尔值")
    return value


def _bounded_int(value, field, minimum, maximum):
    if not isinstance(value, int) or isinstance(value, bool) or not minimum <= value <= maximum:
        raise LoadScenarioPayloadError(f"{field} 必须在 {minimum} 到 {maximum} 之间")
    return value


def _json_copy(value, field):
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise LoadScenarioPayloadError(f"{field} 必须是 JSON 数据") from exc
    if len(encoded.encode("utf-8")) > 1_000_000:
        raise LoadScenarioPayloadError(f"{field} 超过 1 MB 限制")
    return copy.deepcopy(value)


def _parse_reference_value(value, field, *, key_name=""):
    if isinstance(value, dict):
        reference_keys = set(value) & REFERENCE_KEYS
        if reference_keys:
            if len(value) != 1 or len(reference_keys) != 1:
                raise LoadScenarioPayloadError(f"{field} 的变量引用只能包含一个引用字段")
            reference_key = next(iter(reference_keys))
            reference_name = _text(value[reference_key], field, maximum=128)
            pattern = ENV_NAME_PATTERN if reference_key == "$env" else VARIABLE_PATTERN
            if not pattern.fullmatch(reference_name):
                raise LoadScenarioPayloadError(f"{field} 的变量名称格式不正确")
            return {reference_key: reference_name}
        return {
            _text(str(key), f"{field} 键", maximum=200): _parse_reference_value(
                item, f"{field}.{key}", key_name=str(key)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_parse_reference_value(item, f"{field}[]") for item in value]
    if value is not None and not isinstance(value, (str, int, float, bool)):
        raise LoadScenarioPayloadError(f"{field} 包含不支持的数据类型")
    lowered = key_name.lower()
    if isinstance(value, str) and value and any(part in lowered for part in SENSITIVE_KEY_PARTS):
        raise LoadScenarioPayloadError(f"{field} 疑似密钥，必须改用 $env 环境变量引用")
    return copy.deepcopy(value)


def _parse_named_values(value, field):
    value = _mapping(value, field)
    return {
        _text(str(key), f"{field} 键", maximum=200): _parse_reference_value(
            item, f"{field}.{key}", key_name=str(key)
        )
        for key, item in value.items()
    }


def _parse_request(value, field):
    value = _mapping(value, field)
    allowed = {"method", "path", "service", "path_params", "query", "headers", "cookies", "body"}
    _reject_unknown(value, allowed, field)
    missing = sorted({"method", "path", "path_params", "query", "headers", "cookies", "body"} - set(value))
    if missing:
        raise LoadScenarioPayloadError(f"{field} 缺少字段：{missing[0]}")
    method = _text(value["method"], f"{field}.method", maximum=16).upper()
    if method not in HTTP_METHODS:
        raise LoadScenarioPayloadError(f"{field}.method 不支持")
    path = _text(value["path"], f"{field}.path", maximum=4000)
    if not path.startswith("/"):
        raise LoadScenarioPayloadError(f"{field}.path 必须是相对路径，不能绕过环境地址")
    return {
        "method": method,
        "path": path,
        "service": _text(value.get("service", "default"), f"{field}.service", maximum=100),
        "path_params": _parse_named_values(value["path_params"], f"{field}.path_params"),
        "query": _parse_named_values(value["query"], f"{field}.query"),
        "headers": _parse_named_values(value["headers"], f"{field}.headers"),
        "cookies": _parse_named_values(value["cookies"], f"{field}.cookies"),
        "body": _parse_reference_value(value["body"], f"{field}.body"),
    }


def _parse_assertions(value, field):
    if not isinstance(value, list) or len(value) > 100:
        raise LoadScenarioPayloadError(f"{field} 必须是最多 100 项的数组")
    result = []
    allowed = {"type", "operator", "expected", "path", "name", "enabled"}
    for index, raw in enumerate(value):
        item_field = f"{field}[{index}]"
        raw = _mapping(raw, item_field)
        _reject_unknown(raw, allowed, item_field)
        assertion_type = _text(raw.get("type"), f"{item_field}.type", maximum=40)
        operator = _text(raw.get("operator"), f"{item_field}.operator", maximum=40)
        if assertion_type not in ASSERTION_TYPES:
            raise LoadScenarioPayloadError(f"{item_field}.type 暂不支持 k6 编译")
        if operator not in ASSERTION_OPERATORS:
            raise LoadScenarioPayloadError(f"{item_field}.operator 暂不支持 k6 编译")
        if operator not in ASSERTION_OPERATOR_MATRIX[assertion_type]:
            raise LoadScenarioPayloadError(
                f"{item_field} 的 {assertion_type} 不支持 {operator}"
            )
        if operator not in {"exists", "not_exists"} and "expected" not in raw:
            raise LoadScenarioPayloadError(f"{item_field} 缺少 expected")
        parsed = {
            "type": assertion_type,
            "operator": operator,
            "expected": _json_copy(raw.get("expected"), f"{item_field}.expected"),
            "enabled": _boolean(raw.get("enabled", True), f"{item_field}.enabled"),
        }
        for optional in ("path", "name"):
            if optional in raw:
                parsed[optional] = _text(raw[optional], f"{item_field}.{optional}", maximum=1000)
        if assertion_type == "status_code" and not isinstance(parsed["expected"], (int, list)):
            raise LoadScenarioPayloadError(f"{item_field}.expected 必须是 HTTP 状态码")
        if assertion_type == "status_code":
            expected_values = parsed["expected"] if operator == "in" else [parsed["expected"]]
            if (
                not expected_values
                or any(
                    not isinstance(item, int)
                    or isinstance(item, bool)
                    or not 100 <= item <= 599
                    for item in expected_values
                )
            ):
                raise LoadScenarioPayloadError(f"{item_field}.expected 必须是有效 HTTP 状态码")
        if assertion_type in {"json_path", "header"}:
            required_field = "path" if assertion_type == "json_path" else "name"
            if required_field not in parsed:
                raise LoadScenarioPayloadError(f"{item_field} 缺少 {required_field}")
        if assertion_type == "response_time" and (
            not isinstance(parsed["expected"], (int, float))
            or isinstance(parsed["expected"], bool)
            or parsed["expected"] < 0
        ):
            raise LoadScenarioPayloadError(f"{item_field}.expected 必须是非负数")
        result.append(parsed)
    return result


def _parse_extractions(value, field):
    if not isinstance(value, list) or len(value) > 100:
        raise LoadScenarioPayloadError(f"{field} 必须是最多 100 项的数组")
    result = []
    targets = set()
    allowed = {"target", "type", "path", "name", "required", "default"}
    for index, raw in enumerate(value):
        item_field = f"{field}[{index}]"
        raw = _mapping(raw, item_field)
        _reject_unknown(raw, allowed, item_field)
        target = _text(raw.get("target"), f"{item_field}.target", maximum=128)
        if not VARIABLE_PATTERN.fullmatch(target) or target in targets:
            raise LoadScenarioPayloadError(f"{item_field}.target 必须是唯一的变量名")
        targets.add(target)
        extraction_type = _text(raw.get("type"), f"{item_field}.type", maximum=40)
        if extraction_type not in EXTRACTION_TYPES:
            raise LoadScenarioPayloadError(f"{item_field}.type 暂不支持 k6 编译")
        parsed = {
            "target": target,
            "type": extraction_type,
            "required": _boolean(raw.get("required", True), f"{item_field}.required"),
        }
        for optional in ("path", "name"):
            if optional in raw:
                parsed[optional] = _text(raw[optional], f"{item_field}.{optional}", maximum=1000)
        if "default" in raw:
            parsed["default"] = _json_copy(raw["default"], f"{item_field}.default")
        result.append(parsed)
    return result


def _parse_step(raw, index):
    field = f"steps[{index}]"
    raw = _mapping(raw, field)
    allowed = {
        "id", "name", "scope", "action", "request", "assertions", "extractions",
        "sleep_ms", "side_effect", "cleanup_step_id",
    }
    _reject_unknown(raw, allowed, field)
    step_id = _text(raw.get("id"), f"{field}.id", maximum=64)
    if not STEP_ID_PATTERN.fullmatch(step_id):
        raise LoadScenarioPayloadError(f"{field}.id 必须使用小写字母、数字、下划线或连字符")
    scope = _text(raw.get("scope"), f"{field}.scope", maximum=32)
    if scope not in LOAD_STEP_SCOPES:
        raise LoadScenarioPayloadError(f"{field} 的步骤作用域不支持")
    action = _text(raw.get("action"), f"{field}.action", maximum=40)
    if action not in LOAD_STEP_ACTIONS:
        raise LoadScenarioPayloadError(f"{field} 只允许 HTTP 请求、有限等待、断言和变量提取")
    side_effect = _text(raw.get("side_effect"), f"{field}.side_effect", maximum=40)
    if side_effect not in LOAD_SIDE_EFFECTS:
        raise LoadScenarioPayloadError(f"{field}.side_effect 不支持")
    parsed = {
        "id": step_id,
        "name": _text(raw.get("name"), f"{field}.name", maximum=200),
        "scope": scope,
        "action": action,
        "request": _parse_request(raw.get("request"), f"{field}.request"),
        "assertions": _parse_assertions(raw.get("assertions", []), f"{field}.assertions"),
        "extractions": _parse_extractions(raw.get("extractions", []), f"{field}.extractions"),
        "sleep_ms": _bounded_int(raw.get("sleep_ms", 0), f"{field} 等待时间（毫秒）", 0, 60_000),
        "side_effect": side_effect,
    }
    if "cleanup_step_id" in raw:
        parsed["cleanup_step_id"] = _text(raw["cleanup_step_id"], f"{field}.cleanup_step_id", maximum=64)
    return parsed


def parse_load_scenario_definition(payload):
    """Return a deep, canonicalizable load scenario after strict validation."""
    payload = _mapping(payload, "压测场景")
    allowed = {"name", "description", "mode", "steps", "dataset_contract", "risk", "source_snapshot"}
    _reject_unknown(payload, allowed, "压测场景")
    missing = sorted(allowed - set(payload))
    if missing:
        raise LoadScenarioPayloadError(f"压测场景缺少字段：{missing[0]}")
    mode = _text(payload["mode"], "场景模式", maximum=32)
    if mode not in LOAD_SCENARIO_MODES:
        raise LoadScenarioPayloadError("场景模式不支持")
    raw_steps = payload["steps"]
    if not isinstance(raw_steps, list) or not 1 <= len(raw_steps) <= 50:
        raise LoadScenarioPayloadError("steps 必须包含 1 到 50 个步骤")
    steps = [_parse_step(raw, index) for index, raw in enumerate(raw_steps)]
    step_ids = [item["id"] for item in steps]
    if len(step_ids) != len(set(step_ids)):
        raise LoadScenarioPayloadError("步骤 ID 不能重复")
    if mode == "single_interface" and len(steps) != 1:
        raise LoadScenarioPayloadError("单接口场景只能包含一个步骤")
    if not any(item["scope"] == "iteration" for item in steps):
        raise LoadScenarioPayloadError("场景至少需要一个 iteration 步骤")

    dataset = _mapping(payload["dataset_contract"], "dataset_contract")
    _reject_unknown(dataset, {"dataset_id", "usage_mode", "variables"}, "dataset_contract")
    usage_mode = _text(dataset.get("usage_mode"), "dataset_contract.usage_mode", maximum=40)
    if usage_mode not in DATASET_USAGE_MODES:
        raise LoadScenarioPayloadError("数据取用方式不支持")
    variables = dataset.get("variables")
    if not isinstance(variables, list) or len(variables) > 200:
        raise LoadScenarioPayloadError("dataset_contract.variables 必须是最多 200 项的数组")
    parsed_variables = []
    for index, item in enumerate(variables):
        item = _text(item, f"dataset_contract.variables[{index}]", maximum=128)
        if not VARIABLE_PATTERN.fullmatch(item) or item in parsed_variables:
            raise LoadScenarioPayloadError("数据变量必须是唯一的合法变量名")
        parsed_variables.append(item)
    dataset_id = dataset.get("dataset_id")
    if dataset_id is not None:
        dataset_id = _text(dataset_id, "dataset_contract.dataset_id", maximum=100)
    if parsed_variables and not dataset_id:
        raise LoadScenarioPayloadError("使用数据变量时必须选择数据集")

    available_extractions = set()
    declared_data = set(parsed_variables)
    for step in steps:
        data_refs, extraction_refs, template_refs = _request_references(step["request"])
        for name in sorted(data_refs - declared_data):
            raise LoadScenarioPayloadError(f"数据变量 {name} 没有在数据契约中声明")
        local_path_params = set(step["request"]["path_params"])
        unavailable = (extraction_refs | (template_refs - local_path_params)) - available_extractions
        if unavailable:
            name = sorted(unavailable)[0]
            raise LoadScenarioPayloadError(f"变量 {name} 在使用前尚未提取")
        available_extractions.update(item["target"] for item in step["extractions"])

    risk = _mapping(payload["risk"], "risk")
    _reject_unknown(risk, {"level", "ownership_variable", "notes"}, "risk")
    level = _text(risk.get("level"), "risk.level", maximum=20)
    if level not in RISK_LEVELS:
        raise LoadScenarioPayloadError("风险等级不支持")
    ownership_variable = risk.get("ownership_variable")
    if ownership_variable is not None:
        ownership_variable = _text(ownership_variable, "risk.ownership_variable", maximum=128)
        if not VARIABLE_PATTERN.fullmatch(ownership_variable):
            raise LoadScenarioPayloadError("risk.ownership_variable 不是合法变量名")

    snapshot = _mapping(payload["source_snapshot"], "source_snapshot")
    _reject_unknown(snapshot, {"type", "version_ids", "items"}, "source_snapshot")
    snapshot_type = _text(snapshot.get("type"), "source_snapshot.type", maximum=40)
    if snapshot_type not in {"endpoint", "case_version", "manual"}:
        raise LoadScenarioPayloadError("来源快照类型不支持")
    version_ids = snapshot.get("version_ids")
    if not isinstance(version_ids, list) or len(version_ids) > 100:
        raise LoadScenarioPayloadError("source_snapshot.version_ids 必须是最多 100 项的数组")
    parsed_version_ids = [_text(item, "来源版本 ID", maximum=100) for item in version_ids]
    items = snapshot.get("items")
    if not isinstance(items, list) or len(items) > 100:
        raise LoadScenarioPayloadError("source_snapshot.items 必须是最多 100 项的数组")

    return {
        "name": _text(payload["name"], "场景名称", maximum=200),
        "description": _text(payload["description"], "场景说明", minimum=0, maximum=5000),
        "mode": mode,
        "steps": steps,
        "dataset_contract": {"dataset_id": dataset_id, "usage_mode": usage_mode, "variables": parsed_variables},
        "risk": {
            "level": level,
            "ownership_variable": ownership_variable,
            "notes": _text(risk.get("notes", ""), "risk.notes", minimum=0, maximum=2000),
        },
        "source_snapshot": {
            "type": snapshot_type,
            "version_ids": parsed_version_ids,
            "items": _json_copy(items, "source_snapshot.items"),
        },
    }


def _request_references(request):
    data_refs = set()
    extraction_refs = set()
    template_refs = set()
    template_pattern = re.compile(r"\{\{([A-Za-z_][A-Za-z0-9_.-]*)\}\}")

    def visit(value):
        if isinstance(value, dict):
            if set(value) == {"$data"}:
                data_refs.add(value["$data"])
            elif set(value) == {"$extract"}:
                extraction_refs.add(value["$extract"])
            else:
                for item in value.values():
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, str):
            template_refs.update(template_pattern.findall(value))

    visit(request)
    return data_refs, extraction_refs, template_refs


def load_testing_option_catalog():
    """Chinese help shown beside every technical performance-test option."""
    return copy.deepcopy(
        {
            "executors": [
                {"value": "constant-vus", "name": "固定并发", "description": "在指定时长内保持固定数量的虚拟用户，适合模拟稳定在线用户。", "risk_tip": "接口越慢，每秒请求数越低；不要把虚拟用户数误认为 QPS。"},
                {"value": "ramping-vus", "name": "阶梯并发", "description": "按阶段增加或降低虚拟用户，用于观察并发升高时的性能拐点。", "risk_tip": "升压过快可能掩盖预热过程，建议从低并发逐级增加。"},
                {"value": "constant-arrival-rate", "name": "固定吞吐", "description": "持续发起目标次数的完整迭代，适合验证目标 QPS 或链路吞吐。", "risk_tip": "容量不足会产生丢弃迭代；达到时延阈值但没达到目标吞吐仍不能判定通过。"},
                {"value": "ramping-arrival-rate", "name": "阶梯吞吐", "description": "按阶段改变每秒迭代数，用于寻找吞吐上限和服务退化区间。", "risk_tip": "高阶段会直接增加下游压力，先确认数据池、节点容量和自动停止阈值。"},
            ],
            "scenario_modes": [
                {"value": "single_interface", "name": "单接口压测", "description": "每轮只请求一个目标接口，适合测容量上限和接口延迟。", "risk_tip": "不能代表登录、查询、清理等完整业务链路的用户体验。"},
                {"value": "workflow", "name": "业务链路压测", "description": "每轮按顺序执行多个接口，并传递提取变量。", "risk_tip": "页面中的目标吞吐表示每秒完整链路次数，实际接口请求数会更高。"},
            ],
            "dataset_modes": [
                {"value": "cycle", "name": "循环共享", "description": "数据用完后循环使用，适合无副作用的只读查询。", "risk_tip": "会重复使用同一数据，不适合要求唯一账号或唯一资源的写操作。"},
                {"value": "fixed_per_vu", "name": "每个用户固定一行", "description": "每个虚拟用户持续使用同一行数据，适合固定账号或会话。", "risk_tip": "数据行少于虚拟用户数时会重复分配，需先检查账号并发限制。"},
                {"value": "exclusive_per_iteration", "name": "每次迭代独占一行", "description": "每轮流程消费一行且不复用，适合一次性业务数据。", "risk_tip": "数据耗尽会停止分配，报告会判定目标负载未完整达到。"},
            ],
            "queue_priorities": [
                {"value": "urgent", "name": "紧急", "description": "在等待队列中最先领取。", "risk_tip": "不会中断已经运行的任务，也不能突破节点硬上限。"},
                {"value": "high", "name": "高", "description": "优先于普通和低优先级任务。", "risk_tip": "仅影响排队顺序，不代表使用更多节点。"},
                {"value": "normal", "name": "普通", "description": "日常压测的默认排队级别。", "risk_tip": "节点容量不足时会继续排队。"},
                {"value": "low", "name": "低", "description": "适合不紧急的探索性测试。", "risk_tip": "繁忙时等待时间可能较长。"},
            ],
            "agent_tiers": [
                {"value": "preferred", "name": "首选节点", "description": "自动调度时优先使用的专用压测机。", "risk_tip": "仍受节点软上限和硬上限限制。"},
                {"value": "normal", "name": "普通节点", "description": "首选容量不足时补充使用。", "risk_tip": "可能与该节点上的其他任务竞争资源。"},
                {"value": "fallback", "name": "备用节点", "description": "只有任务明确允许时才参与调度。", "risk_tip": "主平台机器建议保持此级别，避免压测影响平台服务。"},
                {"value": "disabled", "name": "停用节点", "description": "保留注册记录但不领取任务。", "risk_tip": "停用后凭据失效，需要重新注册才能恢复。"},
            ],
            "calibration_states": [
                {"value": "uncalibrated", "name": "未校准", "description": "节点已经注册，但尚未测量可信压测容量。", "risk_tip": "不能执行正式压测，请先运行本地校准。"},
                {"value": "calibrating", "name": "校准中", "description": "节点正在运行本地 k6 容量测量。", "risk_tip": "校准期间不会领取业务压测任务。"},
                {"value": "valid", "name": "校准有效", "description": "校准结果仍在有效期内且版本、硬件签名一致。", "risk_tip": "实际容量仍取硬上限、软上限和校准上限中的最小值。"},
                {"value": "expired", "name": "校准已过期", "description": "结果超过七天，或 Agent、k6、硬件已经变化。", "risk_tip": "不能执行正式压测，需要重新校准。"},
                {"value": "failed", "name": "校准失败", "description": "本地 k6、资源采集或稳定性检查没有完成。", "risk_tip": "先按页面错误处理后重新校准，不能手工填写结果绕过。"},
            ],
            "capacity_fields": [
                {"value": "hard_limit", "name": "本机硬上限", "description": "Agent 根据容器和主机资源声明的绝对上限。", "risk_tip": "平台和任务永远不能突破该值。"},
                {"value": "soft_limit", "name": "平台软上限", "description": "管理员为共享资源预留余量后设置的日常上限。", "risk_tip": "必须小于等于本机硬上限。"},
                {"value": "calibrated_limit", "name": "校准容量", "description": "本地 k6 校准实测得到的可持续容量。", "risk_tip": "过期或版本变化后不再作为有效证据。"},
                {"value": "available_capacity", "name": "当前可用容量", "description": "三类上限的最小值减去节点当前占用。", "risk_tip": "这是本次可分配值，会随其他任务实时变化。"},
            ],
            "thresholds": [
                {"value": "minimum_iteration_rate", "name": "最低实际吞吐", "description": "检查实际完成的每秒迭代数是否达到目标。", "risk_tip": "业务链路的一次迭代可能包含多个接口，不能直接当作单接口 QPS。"},
                {"value": "maximum_http_error_rate", "name": "最大 HTTP 错误率", "description": "限制网络错误和非预期 HTTP 状态所占比例。", "risk_tip": "HTTP 200 仍可能是业务失败，必须同时配置业务断言失败率。"},
                {"value": "maximum_business_failure_rate", "name": "最大业务失败率", "description": "限制业务码、布尔值或领域字段断言失败的比例。", "risk_tip": "断言过宽会掩盖真实失败，应沿用已验证的业务断言。"},
                {"value": "maximum_workflow_failure_rate", "name": "最大链路失败率", "description": "限制完整业务流程任一步骤失败的比例。", "risk_tip": "链路越长越容易累计失败，需要结合各步骤报告定位。"},
                {"value": "p95_response_time", "name": "95% 请求响应时间", "description": "要求 95% 的请求在指定时间内完成。", "risk_tip": "仍有 5% 的慢请求未被该值约束，关键接口可同时观察 P99。"},
                {"value": "p99_response_time", "name": "99% 请求响应时间", "description": "要求 99% 的请求在指定时间内完成。", "risk_tip": "样本太少时 P99 波动较大，应结合请求总量判断。"},
                {"value": "maximum_dropped_iteration_rate", "name": "最大丢弃迭代率", "description": "限制因虚拟用户或节点容量不足而未能发起的目标迭代。", "risk_tip": "只要目标负载未达到，即使延迟很好也不能判定性能通过。"},
            ],
        }
    )
