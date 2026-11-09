"""Prepare an optional response assertion from a saved OpenAPI operation.

Only fully supported, bounded local definitions are converted. This does not
contact the service or certify that its actual response matches the definition.
"""

import copy
import json
from collections.abc import Mapping

from ..assertions import (
    AssertionDefinitionError, _SCHEMA_BRANCHES, _SCHEMA_CHILDREN, _schema_matches,
    _SCHEMA_KEYWORDS, validate_response_schema,
)
from .response_assertion_policy import ResponseAssertionPolicy


class _Unsupported(ValueError):
    pass


def response_schema_draft(operation, openapi_version=None):
    """Return (disabled assertion or None, explanation) for a saved operation."""
    status = ResponseAssertionPolicy._success_status_code(operation)
    responses = operation.get('responses', {}) if isinstance(operation, Mapping) else {}
    response = (responses.get(str(status)) or responses.get(status)) if isinstance(responses, Mapping) else None
    if isinstance(response, Mapping) and '$ref' in response:
        return None, '未生成响应结构断言：响应引用需人工核对原始接口定义。'
    entry = ResponseAssertionPolicy._response_media_entry(operation, status)
    if not entry or not ResponseAssertionPolicy._is_json_media_type(entry[0]):
        return None, '未生成响应结构断言：接口成功响应没有可用 JSON 结构定义。'
    schema = entry[1].get('schema')
    if schema is None:
        return None, '未生成响应结构断言：接口成功响应没有可用 JSON 结构定义。'
    dependencies = operation.get('resolved_dependencies', {}) if isinstance(operation, Mapping) else {}
    count = 0

    def convert(value, seen, depth):
        nonlocal count
        count += 1
        if count > 2000 or depth > 20:
            raise _Unsupported('定义过大或层级过深')
        if isinstance(value, bool):
            return value
        if not isinstance(value, Mapping):
            raise _Unsupported('定义格式无效')
        if '$ref' in value:
            ref = value['$ref']
            if (len(value) != 1 or not isinstance(ref, str)
                    or not ref.startswith('#/components/schemas/')
                    or ref in seen or not isinstance(dependencies, Mapping)
                    or ref not in dependencies):
                raise _Unsupported('引用缺失、循环或无法安全展开')
            return convert(dependencies[ref], seen | {ref}, depth + 1)
        unknown = set(value) - _SCHEMA_KEYWORDS - {'nullable', 'example'}
        if unknown or any(key in value and value[key] is not False for key in ('readOnly', 'writeOnly')):
            raise _Unsupported('包含不支持或需人工确认的约束')
        if ('properties' in value or 'required' in value) and value.get('type') != 'object':
            raise _Unsupported('对象约束缺少明确对象类型')
        output = {}
        for key, item in value.items():
            if key in {'nullable', 'example', 'examples', 'default', 'title', 'description', '$comment', 'deprecated', 'readOnly', 'writeOnly'}:
                continue
            if key == 'properties':
                if not isinstance(item, Mapping):
                    raise _Unsupported('字段定义无效')
                output[key] = {name: convert(child, seen, depth + 1) for name, child in item.items()}
            elif key in _SCHEMA_CHILDREN:
                output[key] = convert(item, seen, depth + 1)
            elif key in _SCHEMA_BRANCHES:
                if not isinstance(item, list):
                    raise _Unsupported('组合定义无效')
                output[key] = [convert(child, seen, depth + 1) for child in item]
            else:
                output[key] = copy.deepcopy(item)
        if 'nullable' in value:
            nullable = value['nullable']
            if (not isinstance(openapi_version, str) or not openapi_version.startswith('3.0.')
                    or not isinstance(nullable, bool) or not isinstance(output.get('type'), str)):
                raise _Unsupported('可空定义需人工确认')
            if nullable and output['type'] != 'null':
                output['type'] = [output['type'], 'null']
        return output

    try:
        result = convert(schema, frozenset(), 0)
        pending = [result]
        while pending:
            item = pending.pop()
            if isinstance(item, str) and ('{{' in item or '}}' in item):
                raise _Unsupported('包含可能被解析的变量占位符')
            if isinstance(item, Mapping):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
        if len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > 256 * 1024:
            raise _Unsupported('定义过大')
        validate_response_schema(result)
        if _schema_matches(None, result):
            raise _Unsupported('顶层 JSON null 暂不支持自动校验')
        from ..contracts.case import _schema_has_meaningful_constraint
        if not _schema_has_meaningful_constraint(result):
            raise _Unsupported('响应结构未定义有效约束')
    except (_Unsupported, AssertionDefinitionError) as exc:
        return None, f'未生成响应结构断言：{exc}，请核对原始接口定义。'
    return {
        'type': 'schema', 'operator': 'equals', 'expected': result,
        'timeout_ms': 0, 'enabled': False,
    }, '已生成默认停用的响应结构断言，请核对接口定义并审核后启用。'
