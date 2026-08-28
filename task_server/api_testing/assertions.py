"""Bounded declarative response extraction and assertions."""

import copy
from dataclasses import dataclass
import re


_PATH_TOKEN = re.compile(
    r"(?:\.([A-Za-z_][A-Za-z0-9_-]*))|(?:\[(0|[1-9][0-9]{0,5})\])"
)
_MAX_PATH_LENGTH = 1000
_MAX_PATH_DEPTH = 64
_MISSING = object()
_NO_DEFAULT = object()


class JsonPathError(ValueError):
    pass


class AssertionDefinitionError(ValueError):
    pass


@dataclass(frozen=True)
class AssertionResult:
    type: str
    operator: str
    passed: bool
    actual: object
    expected: object
    message: str


def json_path_get(document, path, *, missing=_NO_DEFAULT):
    if not isinstance(path, str) or not path or len(path) > _MAX_PATH_LENGTH:
        raise JsonPathError("JSON 路径无效")
    if path == "$":
        return copy.deepcopy(document)
    if not path.startswith("$"):
        raise JsonPathError("JSON 路径必须以 $ 开头")
    position = 1
    tokens = []
    while position < len(path):
        match = _PATH_TOKEN.match(path, position)
        if match is None:
            raise JsonPathError("JSON 路径使用了不支持的语法")
        tokens.append(match.group(1) if match.group(1) is not None else int(match.group(2)))
        if len(tokens) > _MAX_PATH_DEPTH:
            raise JsonPathError("JSON 路径层级过深")
        position = match.end()
    value = document
    for token in tokens:
        if isinstance(token, str):
            if not isinstance(value, dict) or token not in value:
                if missing is _NO_DEFAULT:
                    raise JsonPathError("JSON 路径未找到对应字段")
                return missing
            value = value[token]
        else:
            if not isinstance(value, list) or token >= len(value):
                if missing is _NO_DEFAULT:
                    raise JsonPathError("JSON 路径未找到对应字段")
                return missing
            value = value[token]
    return copy.deepcopy(value)


def _matches(actual, operator, expected, *, exists=True):
    if operator == "exists":
        return exists
    if operator == "not_exists":
        return not exists
    if not exists:
        return False
    if operator == "equals":
        return _json_equals(actual, expected)
    if operator == "not_equals":
        return not _json_equals(actual, expected)
    if operator == "contains":
        if isinstance(actual, list):
            return any(_json_equals(item, expected) for item in actual)
        try:
            return expected in actual
        except TypeError:
            return False
    if operator == "not_contains":
        if isinstance(actual, list):
            return not any(_json_equals(item, expected) for item in actual)
        try:
            return expected not in actual
        except TypeError:
            return False
    if operator == "greater_than":
        try:
            return actual > expected
        except TypeError:
            return False
    if operator == "less_than":
        try:
            return actual < expected
        except TypeError:
            return False
    if operator == "matches":
        return isinstance(actual, str) and re.search(expected, actual) is not None
    if operator == "in":
        return any(_json_equals(actual, item) for item in expected)
    raise AssertionDefinitionError("不支持该断言比较方式")


def _json_equals(left, right):
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    return left == right


def _schema_matches(value, schema, depth=0):
    if depth > 20:
        raise AssertionDefinitionError("响应结构定义层级过深")
    if isinstance(schema, bool):
        return schema
    if not isinstance(schema, dict):
        raise AssertionDefinitionError("响应结构定义必须是对象或布尔值")
    schema_type = schema.get("type")
    type_map = {
        "object": dict,
        "array": list,
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
        "null": type(None),
    }
    if schema_type is not None:
        expected_type = type_map.get(schema_type)
        if expected_type is None:
            raise AssertionDefinitionError("不支持该响应结构类型")
        if schema_type in {"number", "integer"} and isinstance(value, bool):
            return False
        if not isinstance(value, expected_type):
            return False
    if "required" in schema:
        if not isinstance(value, dict) or not all(key in value for key in schema["required"]):
            return False
    properties = schema.get("properties", {})
    if properties:
        if not isinstance(properties, dict) or not isinstance(value, dict):
            return False
        for key, child_schema in properties.items():
            if key in value and not _schema_matches(value[key], child_schema, depth + 1):
                return False
    if "items" in schema and isinstance(value, list):
        if not all(_schema_matches(item, schema["items"], depth + 1) for item in value):
            return False
    return True


def evaluate_assertions(assertions, response):
    results = []
    for assertion in assertions:
        if not assertion.enabled:
            continue
        exists = True
        if assertion.type == "status_code":
            actual = response.status_code
        elif assertion.type == "response_time":
            actual = response.duration_ms
        elif assertion.type == "header":
            headers = {name.lower(): value for name, value in response.headers.items()}
            actual = headers.get((assertion.name or "").lower(), _MISSING)
            exists = actual is not _MISSING
        elif assertion.type == "json_path":
            if response.json_body is None:
                raise JsonPathError("响应不是有效的 JSON")
            actual = json_path_get(response.json_body, assertion.path, missing=_MISSING)
            exists = actual is not _MISSING
        elif assertion.type == "schema":
            if response.json_body is None:
                raise JsonPathError("响应不是有效的 JSON")
            actual = response.json_body
        else:
            raise AssertionDefinitionError("不支持该断言类型")
        if assertion.type == "schema":
            passed = _schema_matches(actual, assertion.expected)
        else:
            passed = _matches(actual, assertion.operator, assertion.expected, exists=exists)
        results.append(
            AssertionResult(
                type=assertion.type,
                operator=assertion.operator,
                passed=passed,
                actual=None if actual is _MISSING else copy.deepcopy(actual),
                expected=copy.deepcopy(assertion.expected),
                message="通过" if passed else "实际响应与期望值不一致",
            )
        )
    return tuple(results)


def evaluate_business_response(assertions, response):
    """Reject false-green JSON business failures unless the actual code is explicit."""
    body = response.json_body
    if not isinstance(body, dict) or "code" not in body:
        return None
    actual = body["code"]
    success_codes = (0, 200, "0", "200")
    if any(_json_equals(actual, item) for item in success_codes):
        return None
    for assertion in assertions:
        if not getattr(assertion, "enabled", True):
            continue
        if getattr(assertion, "type", "") != "json_path":
            continue
        if getattr(assertion, "path", "") != "$.code":
            continue
        operator = getattr(assertion, "operator", "")
        expected = getattr(assertion, "expected", None)
        if operator == "equals" and _json_equals(actual, expected):
            return None
        if (
            operator == "in"
            and isinstance(expected, (list, tuple))
            and any(_json_equals(actual, item) for item in expected)
        ):
            return None
    return AssertionResult(
        type="business_code",
        operator="exact_match",
        passed=False,
        actual=copy.deepcopy(actual),
        expected="0、200 或精确声明的预期业务码",
        message=f"HTTP 请求成功，但业务码 {actual} 未被精确断言接受",
    )


def extract_values(extractions, response):
    values = {}
    headers = {name.lower(): value for name, value in response.headers.items()}
    for extraction in extractions:
        try:
            if extraction.type == "json_path":
                if response.json_body is None:
                    raise JsonPathError("响应不是有效的 JSON")
                value = json_path_get(response.json_body, extraction.path)
            elif extraction.type == "header":
                value = headers[(extraction.name or "").lower()]
            elif extraction.type == "cookie":
                cookies = response.cookies
                value = cookies[extraction.name]
            elif extraction.type == "status_code":
                value = response.status_code
            else:
                raise AssertionDefinitionError("不支持该变量提取类型")
        except (JsonPathError, KeyError):
            if extraction.required:
                raise
            value = copy.deepcopy(extraction.default)
        values[extraction.target] = copy.deepcopy(value)
    return values
