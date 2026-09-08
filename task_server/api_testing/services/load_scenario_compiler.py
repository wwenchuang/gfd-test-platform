"""Deterministically compile admitted API scenarios into bounded k6 scripts."""

import hashlib
import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping, Tuple

from ..contracts.load_testing import LoadScenarioPayloadError, parse_load_scenario_definition
from .load_scenario_service import LoadScenarioService


COMPILER_VERSION = "k6-safe-v3"
EXECUTORS = frozenset(
    {"constant-vus", "ramping-vus", "constant-arrival-rate", "ramping-arrival-rate"}
)


class LoadScenarioCompileError(ValueError):
    """Raised when a scenario cannot be represented safely by the k6 compiler."""


@dataclass(frozen=True)
class CompiledLoadScenario:
    script: str
    options: Mapping
    content_hash: str
    step_manifest: Tuple[Mapping, ...]
    required_environment_variables: Tuple[str, ...]
    compiler_version: str = COMPILER_VERSION

    def __post_init__(self):
        object.__setattr__(self, "options", MappingProxyType(dict(self.options)))
        object.__setattr__(self, "step_manifest", tuple(MappingProxyType(dict(item)) for item in self.step_manifest))


def _positive_int(value, field, *, maximum=1_000_000):
    if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= maximum:
        raise LoadScenarioCompileError(f"{field} 必须在 1 到 {maximum} 之间")
    return value


def _non_negative_int(value, field, *, maximum=1_000_000):
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= maximum:
        raise LoadScenarioCompileError(f"{field} 必须在 0 到 {maximum} 之间")
    return value


def _duration(value, field):
    return f"{_positive_int(value, field, maximum=86_400)}s"


def _parse_stages(value, field):
    if not isinstance(value, list) or not 1 <= len(value) <= 20:
        raise LoadScenarioCompileError(f"{field} 必须包含 1 到 20 个阶段")
    stages = []
    for index, stage in enumerate(value):
        if not isinstance(stage, dict):
            raise LoadScenarioCompileError(f"{field}[{index}] 必须是对象")
        unknown = sorted(set(stage) - {"duration_seconds", "target"})
        if unknown:
            raise LoadScenarioCompileError(f"{field}[{index}] 包含不支持字段：{unknown[0]}")
        stages.append(
            {
                "duration": _duration(stage.get("duration_seconds"), f"{field}[{index}].duration_seconds"),
                "target": _non_negative_int(stage.get("target"), f"{field}[{index}].target"),
            }
        )
    return stages


def _parse_workload(workload):
    if not isinstance(workload, dict):
        raise LoadScenarioCompileError("负载配置必须是对象")
    executor = workload.get("executor")
    if executor not in EXECUTORS:
        raise LoadScenarioCompileError("不支持的负载模型")
    allowed_by_executor = {
        "constant-vus": {"executor", "vus", "duration_seconds"},
        "ramping-vus": {"executor", "start_vus", "stages"},
        "constant-arrival-rate": {"executor", "rate", "time_unit", "duration_seconds", "pre_allocated_vus", "max_vus"},
        "ramping-arrival-rate": {"executor", "start_rate", "time_unit", "pre_allocated_vus", "max_vus", "stages"},
    }
    unknown = sorted(set(workload) - allowed_by_executor[executor])
    if unknown:
        raise LoadScenarioCompileError(f"负载配置包含不支持字段：{unknown[0]}")
    parsed = {"executor": executor}
    if executor == "constant-vus":
        parsed.update(vus=_positive_int(workload.get("vus"), "并发用户数"), duration=_duration(workload.get("duration_seconds"), "持续时间"))
    elif executor == "ramping-vus":
        parsed.update(startVUs=_non_negative_int(workload.get("start_vus"), "初始并发用户数"), stages=_parse_stages(workload.get("stages"), "并发阶段"))
    elif executor == "constant-arrival-rate":
        parsed.update(
            rate=_positive_int(workload.get("rate"), "每时间单位迭代数"),
            timeUnit=_time_unit(workload.get("time_unit")),
            duration=_duration(workload.get("duration_seconds"), "持续时间"),
            preAllocatedVUs=_positive_int(workload.get("pre_allocated_vus"), "预分配虚拟用户数"),
            maxVUs=_positive_int(workload.get("max_vus"), "最大虚拟用户数"),
        )
    else:
        parsed.update(
            startRate=_non_negative_int(workload.get("start_rate"), "初始迭代率"),
            timeUnit=_time_unit(workload.get("time_unit")),
            preAllocatedVUs=_positive_int(workload.get("pre_allocated_vus"), "预分配虚拟用户数"),
            maxVUs=_positive_int(workload.get("max_vus"), "最大虚拟用户数"),
            stages=_parse_stages(workload.get("stages"), "吞吐阶段"),
        )
    if "maxVUs" in parsed and parsed["maxVUs"] < parsed["preAllocatedVUs"]:
        raise LoadScenarioCompileError("最大虚拟用户数不能小于预分配虚拟用户数")
    return {"scenarios": {"main": parsed}}


def _time_unit(value):
    if not isinstance(value, str) or value not in {"1s", "1m"}:
        raise LoadScenarioCompileError("时间单位只支持 1s 或 1m")
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _environment_variables(definition):
    result = set()

    def visit(value):
        if isinstance(value, dict):
            if set(value) == {"$env"}:
                result.add(value["$env"])
            else:
                for item in value.values():
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for step in definition["steps"]:
        visit(step["request"])
    return tuple(sorted(result))


def _assertion_expression(assertion):
    kind = assertion["type"]
    operator = assertion["operator"]
    expected = _json(assertion.get("expected"))
    if kind == "status_code":
        actual = "item.status"
        label = "HTTP状态符合预期"
    elif kind == "response_time":
        actual = "item.timings.duration"
        label = "响应时间符合预期"
    elif kind == "header":
        actual = f"item.headers[{_json(assertion.get('name', ''))}]"
        label = f"响应头断言：{assertion.get('name', '')}"
    else:
        path = assertion.get("path", "")
        actual = f"jsonPath(responseJson(item), {_json(path)})"
        label = f"业务断言：{path}"
    expression = {
        "equals": f"{actual} === {expected}",
        "not_equals": f"{actual} !== {expected}",
        "contains": f"containsValue({actual}, {expected})",
        "not_contains": f"!containsValue({actual}, {expected})",
        "exists": f"{actual} !== undefined && {actual} !== null",
        "not_exists": f"{actual} === undefined || {actual} === null",
        "greater_than": f"{actual} > {expected}",
        "less_than": f"{actual} < {expected}",
        "matches": f"new RegExp({expected}).test(String(({actual}) === undefined || ({actual}) === null ? '' : ({actual})))",
        "in": f"{expected}.includes({actual})",
    }[operator]
    return label, expression


def _step_function(step, ownership_variable=None):
    step_json = _json(step)
    ownership_capture = ''
    if step['side_effect'] == 'creates_owned_resource':
        extraction = next(e for e in step['extractions'] if e['target'] == ownership_variable)
        ownership_capture = (f'if (response.status < 200 || response.status >= 300) throw new Error("创建请求未确认成功，不重发创建或猜测资源ID");\n'
            f'  applyExtractions({_json([extraction])}, response, state);\n'
            f'  if (!validOwnedId(state[{_json(ownership_variable)}])) {{ delete state[{_json(ownership_variable)}]; throw new Error("创建资源ID无效，需人工核对"); }}')
    timeout = ', timeout: "10s", redirects: 0' if step['side_effect'] != 'readonly' else ''
    response_guard = ' && response.status >= 200 && response.status < 300' if step['side_effect'] != 'readonly' else ''
    assertion_parts = []
    for assertion in step["assertions"]:
        if not assertion.get("enabled", True):
            continue
        label, expression = _assertion_expression(assertion)
        assertion_parts.append(f"{_json(label)}: (item) => {expression}")
    checks = ",".join(assertion_parts) or '"HTTP请求已完成": (item) => item.status > 0'
    safe_identifier = step["id"].replace("-", "_")
    request_name = f"{step['id']} {step['name']}"
    return f"""
function step_{safe_identifier}(state, data) {{
  const step = {step_json};
  const request = resolveValue(step.request, state, data);
  const baseUrl = serviceBaseUrl(request.service);
  const pathVariables = Object.assign({{}}, state, request.path_params || {{}});
  const url = baseUrl + resolveTemplate(step.request.path, pathVariables, true) + encodeQuery(request.query);
  const body = request.body === null ? null : JSON.stringify(request.body);
  const params = {{ headers: Object.assign({{}}, defaultHeaders, request.headers), cookies: request.cookies, tags: {{ name: {_json(request_name)}, step_id: {_json(step['id'])} }}{timeout} }};
  const response = http.request(request.method, url, body, params);
  const stepOk = check(response, {{{checks}}});
  {ownership_capture}
  applyExtractions(step.extractions, response, state);
  if (step.sleep_ms > 0) sleep(step.sleep_ms / 1000);
  return stepOk{response_guard};
}}
""".strip()


def compile_scenario(definition, workload, *, stop_policy=None, compiler_version=COMPILER_VERSION):
    if compiler_version == "k6-safe-v2":
        from .load_scenario_compiler_v2 import compile_scenario as compile_v2, LoadScenarioCompileError as V2CompileError
        try:
            return compile_v2(definition, workload, stop_policy=stop_policy)
        except V2CompileError as error:
            raise LoadScenarioCompileError(str(error)) from error
    if compiler_version == "k6-safe-v1":
        if stop_policy is not None:
            raise LoadScenarioCompileError("旧版冻结执行不支持新增停止策略，请创建新版执行")
        from .load_scenario_compiler_v1 import compile_scenario as compile_v1, LoadScenarioCompileError as V1CompileError
        try:
            return compile_v1(definition, workload)
        except V1CompileError as error:
            raise LoadScenarioCompileError(str(error)) from error
    if compiler_version != COMPILER_VERSION:
        raise LoadScenarioCompileError("不支持的冻结编译器版本，请创建新版执行")
    try:
        parsed = parse_load_scenario_definition(definition)
    except LoadScenarioPayloadError as exc:
        raise LoadScenarioCompileError(str(exc)) from exc
    admission = LoadScenarioService.validate_definition(parsed)
    if not admission.accepted:
        issue = admission.issues[0]
        raise LoadScenarioCompileError(f"{issue.message}；{issue.remedy}")
    from .load_lifecycle_policy import require_lifecycle
    try:
        lifecycle = require_lifecycle(parsed)
    except ValueError as error:
        raise LoadScenarioCompileError(str(error)) from error
    options = _parse_workload(workload)
    from .load_execution_policy import safety_thresholds
    stop_thresholds = safety_thresholds(stop_policy)
    if stop_thresholds:
        options["thresholds"] = stop_thresholds
    env_names = _environment_variables(parsed)
    dataset_variables = parsed["dataset_contract"]["variables"]
    secret_bindings = "\n".join(
        f"requiredSecrets[{_json(name)}] = __ENV[{_json(name)}];" for name in env_names
    )
    dataset_readers = ",".join(
        f"{_json(name)}: () => dataValue({_json(name)})" for name in dataset_variables
    )
    agent_steps = [
        step
        for step in parsed["steps"]
        if step["scope"] in {"agent_setup", "vu_once", "iteration", "cleanup_once"}
    ]
    functions = "\n\n".join(_step_function(step, lifecycle["owner"] if lifecycle else None) for step in agent_steps)
    by_scope = {}
    for scope in ("setup_once", "agent_setup", "vu_once", "iteration", "cleanup_once"):
        by_scope[scope] = [
            f"step_{step['id'].replace('-', '_')}(state, data)"
            for step in parsed["steps"]
            if step["scope"] == scope
        ]
    run_lines = "\n  ".join(
        f"if (!{call.replace('(state, data)', '(vuState, data)')}) throw new Error(\"业务步骤断言失败\");"
        for call in by_scope["iteration"]
    )
    vu_lines = "\n      ".join(
        f"if (!{call.replace('(state, data)', '(vuState, data)')}) throw new Error(\"业务步骤断言失败\");"
        for call in by_scope["vu_once"]
    )
    agent_setup_lines = "\n  ".join(
        f"if (!{call}) throw new Error(\"节点初始化步骤失败\");"
        for call in by_scope["agent_setup"]
    )
    clear_owner = f'delete vuState[{_json(lifecycle["owner"])}];' if lifecycle else ''
    cleanup_lines = ''
    if lifecycle:
        owner_key = _json(lifecycle['owner'])
        cleanup_call = by_scope['cleanup_once'][0].replace('(state, data)', '(vuState, data)')
        cleanup_lines = f'''if (validOwnedId(vuState[{owner_key}])) {{
      try {{ if (!{cleanup_call}) iterationOk = false; }}
      catch (_) {{ iterationOk = false; }}
      delete vuState[{owner_key}];
    }}'''
    script = f"""import http from \"k6/http\";
import {{ check, sleep }} from \"k6\";
import {{ Rate, Counter }} from \"k6/metrics\";
import exec from \"k6/execution\";

export const options = {_json(options)};
const workflowIterationStarted = new Counter("workflow_iteration_started");
const loadStageDurations = {_json([stage["duration_seconds"] for stage in workload.get("stages", [])])};
const workflowIterationSuccess = new Rate(\"workflow_iteration_success\");
const datasetRows = JSON.parse(open(__ENV[\"LOAD_DATASET_FILE\"]));
const defaultHeaders = JSON.parse(__ENV[\"LOAD_DEFAULT_HEADERS_JSON\"] || \"{{}}\");
const requiredSecrets = {{}};
{secret_bindings}
const datasetMode = {_json(parsed['dataset_contract']['usage_mode'])};
const datasetVariableReaders = {{{dataset_readers}}};
let vuInitialized = false;
const vuState = {{}};

function dataValue(name) {{
  if (datasetRows.length === 0) return undefined;
  let index;
  if (datasetMode === \"fixed_per_vu\") index = (__VU - 1) % datasetRows.length;
  else if (datasetMode === \"exclusive_per_iteration\") index = exec.scenario.iterationInTest;
  else index = exec.scenario.iterationInTest % datasetRows.length;
  if (index >= datasetRows.length) throw new Error(\"独占数据已经耗尽\");
  return datasetRows[index][name];
}}

function resolveValue(value, state, data) {{
  if (Array.isArray(value)) return value.map((item) => resolveValue(item, state, data));
  if (value && typeof value === \"object\") {{
    if (Object.keys(value).length === 1 && value.$env) return requiredSecrets[value.$env];
    if (Object.keys(value).length === 1 && value.$data) return datasetVariableReaders[value.$data]();
    if (Object.keys(value).length === 1 && value.$extract) return state[value.$extract];
    const result = {{}};
    for (const key of Object.keys(value).sort()) result[key] = resolveValue(value[key], state, data);
    return result;
  }}
  if (typeof value === \"string\") return resolveTemplate(value, state, false);
  return value;
}}

function resolveTemplate(value, state, encode) {{
  return value.replace(/\\{{\\{{([A-Za-z_][A-Za-z0-9_.-]*)\\}}\\}}/g, (_, name) => {{
    const rendered = String(state[name] === undefined || state[name] === null ? \"\" : state[name]);
    return encode ? encodeURIComponent(rendered) : rendered;
  }});
}}

function serviceBaseUrl(service) {{
  const key = \"BASE_URL_\" + String(service || \"default\").toUpperCase().replace(/[^A-Z0-9]/g, \"_\");
  const value = __ENV[key] || (service === \"default\" ? __ENV[\"BASE_URL\"] : undefined);
  if (!value) throw new Error(\"缺少服务地址环境变量：\" + key);
  return value.replace(/\\/$/, \"\");
}}

function encodeQuery(query) {{
  const pairs = [];
  for (const key of Object.keys(query || {{}}).sort()) {{
    const value = query[key];
    if (value === undefined || value === null) continue;
    pairs.push(encodeURIComponent(key) + \"=\" + encodeURIComponent(String(value)));
  }}
  return pairs.length ? \"?\" + pairs.join(\"&\") : \"\";
}}

function responseJson(response) {{ try {{ return response.json(); }} catch (_) {{ return undefined; }} }}
function jsonPath(value, path) {{
  if (path === \"$\") return value;
  return String(path).replace(/^\\$\\.?/, \"\").split(\".\").filter(Boolean).reduce((current, key) => current == null ? undefined : current[key], value);
}}
function validOwnedId(value) {{ return (typeof value === "string" && /^[A-Za-z0-9_-]{{1,256}}$/.test(value)) || (typeof value === "number" && Number.isSafeInteger(value) && value >= 0); }}
function containsValue(actual, expected) {{ return Array.isArray(actual) ? actual.includes(expected) : String(actual === undefined || actual === null ? \"\" : actual).includes(String(expected)); }}
function applyExtractions(extractions, response, state) {{
  for (const item of extractions) {{
    let value;
    if (item.type === \"json_path\") value = jsonPath(responseJson(response), item.path);
    else if (item.type === \"header\") value = response.headers[item.name];
    else if (item.type === \"cookie\") {{
      const cookies = response.cookies[item.name];
      value = cookies && cookies[0] ? cookies[0].value : undefined;
    }}
    else if (item.type === \"status_code\") value = response.status;
    if ((value === undefined || value === null) && Object.prototype.hasOwnProperty.call(item, \"default\")) value = item.default;
    if ((value === undefined || value === null) && item.required) throw new Error(\"必需变量提取失败：\" + item.target);
    state[item.target] = value;
  }}
}}

{functions}

export function setup() {{
  const state = JSON.parse(__ENV[\"LOAD_SETUP_JSON\"] || \"{{}}\");
  const data = {{}};
  {agent_setup_lines}
  return {{ state }};
}}

export default function(data) {{
  const elapsedSeconds = (Date.now() - exec.scenario.startTime) / 1000;
  let phaseEnd = 0;
  let phase = "__load_stage_outside";
  for (let i = 0; i < loadStageDurations.length; i++) {{
    phaseEnd += loadStageDurations[i];
    if (elapsedSeconds < phaseEnd) {{ phase = "__load_stage_" + i; break; }}
  }}
  if (loadStageDurations.length) workflowIterationStarted.add(1, {{ step_id: phase }});
  Object.assign(vuState, data && data.state ? data.state : {{}});
  {clear_owner}
  let iterationOk = true;
  try {{
    if (!vuInitialized) {{
      {vu_lines}
      vuInitialized = true;
    }}
    {run_lines}
  }} catch (error) {{
    iterationOk = false;
    throw error;
  }} finally {{
    {cleanup_lines}
    workflowIterationSuccess.add(iterationOk);
  }}
}}

"""
    content_hash = hashlib.sha256(script.encode("utf-8")).hexdigest()
    manifest = tuple(
        {"id": step["id"], "name": step["name"], "scope": step["scope"], "method": step["request"]["method"], "path": step["request"]["path"]}
        for step in parsed["steps"]
    )
    return CompiledLoadScenario(script, options, content_hash, manifest, env_names)
