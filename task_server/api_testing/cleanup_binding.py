"""Explicit single-resource cleanup contract, shared by case parsing and execution."""
import copy
import hmac
import json
import re
import secrets
from collections.abc import Mapping
from .assertions import JsonPathError, json_path_get

ACTION = "bind_cleanup_response_id"
_CONTEXT_KEY = secrets.token_bytes(32)


def transport_context(revision, service, base_url, headers):
    # Used only inside a worker, never serialized or shared across executions.
    effective = sorted((key, str(value)) for key, value in headers.items())
    data = json.dumps([revision, service, base_url, effective], separators=(",", ":")).encode()
    return hmac.digest(_CONTEXT_KEY, data, "sha256")

_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,99}")
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")
_PATH = re.compile(r"/(?:[A-Za-z0-9_.-]+|\{[A-Za-z_][A-Za-z0-9_.-]*\})(?:/(?:[A-Za-z0-9_.-]+|\{[A-Za-z_][A-Za-z0-9_.-]*\}))*")
_JSON_PATH = re.compile(r"\$(?:\.[A-Za-z_][A-Za-z0-9_-]*|\[[0-9]{1,6}\])+")


def field(item, key, default=None):
    return item.get(key, default) if isinstance(item, Mapping) else getattr(item, key, default)


def _direct(item):
    return (field(item, 'type') == 'json_path' and field(item, 'required', True)
            and _JSON_PATH.fullmatch(field(item, 'path') or ''))


def _leaves(value):
    if isinstance(value, Mapping):
        if len(value) != 1:
            raise ValueError('每层清理对象只允许唯一资源参数')
        for key, child in value.items():
            if not _NAME.fullmatch(key):
                raise ValueError('清理参数名不允许变量或表达式')
            yield from _leaves(child)
    elif isinstance(value, list):
        if len(value) != 1:
            raise ValueError('清理列表只允许一个资源 ID')
        for child in value:
            yield from _leaves(child)
    else:
        yield value


def validate_binding(processing, extractions, dependencies):
    pre = processing.get('pre', ())
    markers = [a for a in pre if a.get('action') == ACTION]
    if not markers:
        return None
    binding = markers[0]
    if (len(markers) != 1 or pre[0] != binding or set(binding) != {'action', 'source', 'name', 'target'}
            or not all(isinstance(binding[k], str) and _NAME.fullmatch(binding[k]) for k in ('name', 'target'))):
        raise ValueError('响应 ID 绑定须为第一项前置处理，并配置唯一来源、提取变量和清理变量')
    name = binding['name']
    if binding['source'] == 'main':
        definitions = [x for x in extractions if field(x,'target') == name]
        if len(definitions) != 1 or not _direct(definitions[0]):
            raise ValueError('主体须用固定 JSON 路径必需提取绑定 ID')
    else:
        deps = [d for d in dependencies if d.get('case_version_id') == binding['source'] and name in d.get('exports', ())]
        if len(deps) != 1:
            raise ValueError('清理绑定的依赖版本必须明确导出该变量')
    steps = [s for s in processing.get('cleanup_steps', ()) if s.get('enabled', True)]
    if len(steps) != 1:
        raise ValueError('响应 ID 绑定仅支持一个启用的清理步骤')
    step = steps[0]
    req = step['request']
    path = req['path']
    rules = json.dumps([step.get('assertions', []), step.get('extractions', [])])
    if '{{' in rules:
        raise ValueError('绑定清理只支持静态响应断言及提取路径')
    if (not _PATH.fullmatch(path) or any(s in ('.','..') for s in path.split('/'))
            or not _NAME.fullmatch(req.get('service','default'))
            or req.get('headers') or req.get('cookies') or step.get('polling')
            or step.get('only_if_variable') or step.get('required_variables')):
        raise ValueError('绑定清理需要固定接口路径、环境服务及凭证，不支持条件或重试')
    path_params = req.get('path_params', {})
    if set(re.findall(r'\{([^{}]+)\}', path)) != set(path_params):
        raise ValueError('清理路径参数与绑定不一致')
    inputs = [v for v in (path_params, req.get('query', {})) if v]
    if req.get('body') is not None:
        inputs.append(req['body'])
    if len(inputs) != 1 or list(_leaves(inputs[0])) != ['{{'+binding['target']+'}}']:
        raise ValueError('清理资源标识须为路径、查询或正文中的唯一值并直接绑定；不允许其他 ID 或变量')
    return binding


def bind_request(step, name, resource_id):
    def replace(value):
        if isinstance(value, dict):
            return {k: replace(v) for k, v in value.items()}
        if isinstance(value, list):
            return [replace(v) for v in value]
        return resource_id if value == "{{" + name + "}}" else value

    bound = copy.deepcopy(step)
    for slot in ("body", "query", "path_params"):
        bound["request"][slot] = replace(bound["request"].get(slot))
    return bound


def fresh_ids(outcome, extractions, names):
    """Only scalar IDs from the successful main response; no optional defaults."""
    if not names or not outcome or outcome.status != 'PASSED' or not outcome.raw_response:
        return {}
    body = outcome.raw_response['body']
    if not 200 <= outcome.raw_response['status_code'] < 300:
        return {}
    if isinstance(body, dict) and 'code' in body and (type(body['code']) not in {str, int} or body['code'] not in (0, 200, '0', '200')):
        return {}
    result = {}
    for definition in extractions:
        if field(definition,'target') not in names or not _direct(definition):
            continue
        try:
            value = json_path_get(body, field(definition,'path'))
        except JsonPathError:
            continue
        if ((type(value) is int and value > 0) or (isinstance(value,str) and _ID.fullmatch(value))):
            result[field(definition,'target')] = value
    return result
