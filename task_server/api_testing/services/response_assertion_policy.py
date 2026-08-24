"""Shared OpenAPI-derived response assertion defaults for generated API cases."""

import copy
import re
from typing import Mapping


_MISSING = object()


class ResponseAssertionPolicy:
    """Derive safe response assertions without overriding explicit AI scenarios."""

    @classmethod
    def default_positive_assertions(cls, operation):
        status_code = cls._success_status_code(operation)
        return cls._positive_assertions_for_status(operation, status_code)

    @classmethod
    def _positive_assertions_for_status(cls, operation, status_code):
        assertions = [cls._status_assertion(status_code)]
        assertions.extend(cls._business_success_assertions(operation, status_code))
        return assertions

    @classmethod
    def complete_candidate_assertions(cls, assertions, operation):
        current = copy.deepcopy(list(assertions or []))
        status_state, status_code = cls._candidate_status(current, operation)
        if status_state == "negative":
            return current

        defaults = cls._positive_assertions_for_status(operation, status_code)
        status_default = defaults[0]
        business_defaults = defaults[1:]

        if cls._has_explicit_data_absence(current, business_defaults):
            return current
        outcome_state = cls._explicit_outcome_state(current, business_defaults)
        if outcome_state == "negative":
            return current

        if status_state in {"missing", "broad"}:
            current.append(copy.deepcopy(status_default))

        for default in business_defaults:
            if not cls._is_business_default_covered(current, default):
                current.append(copy.deepcopy(default))
        return current

    @staticmethod
    def _status_assertion(expected):
        return {
            "type": "status_code",
            "operator": "equals",
            "expected": expected,
            "timeout_ms": 0,
            "enabled": True,
        }

    @staticmethod
    def _code_assertion(expected):
        return {
            "type": "json_path",
            "path": "$.code",
            "operator": "equals",
            "expected": expected,
            "timeout_ms": 0,
            "enabled": True,
        }

    @staticmethod
    def _success_true_assertion():
        return {
            "type": "json_path",
            "path": "$.success",
            "operator": "equals",
            "expected": True,
            "timeout_ms": 0,
            "enabled": True,
        }

    @staticmethod
    def _json_path_exists_assertion(path):
        return {
            "type": "json_path",
            "path": path,
            "operator": "exists",
            "timeout_ms": 0,
            "enabled": True,
        }

    @staticmethod
    def _enabled(assertion):
        return isinstance(assertion, Mapping) and assertion.get("enabled", True) is not False

    @classmethod
    def _candidate_status(cls, assertions, operation):
        status_assertions = [
            item
            for item in assertions
            if cls._enabled(item) and item.get("type") == "status_code"
        ]
        documented = cls._documented_success_status_codes(operation)
        default = cls._success_status_code(operation)
        if not status_assertions:
            return "missing", default
        accepted = [
            status
            for status in documented
            if all(cls._status_assertion_accepts(item, status) for item in status_assertions)
        ]
        if not accepted:
            return "negative", default
        selected = default if default in accepted else min(accepted)
        precise = any(
            cls._is_precise_status_assertion(item, selected)
            for item in status_assertions
        )
        return ("positive" if precise else "broad"), selected

    @staticmethod
    def _status_assertion_accepts(assertion, status):
        operator = assertion.get("operator")
        expected = assertion.get("expected")
        if operator == "equals":
            return status == expected
        if operator == "not_equals":
            return status != expected
        if operator == "in":
            return isinstance(expected, list) and status in expected
        return False

    @staticmethod
    def _is_precise_status_assertion(assertion, status):
        operator = assertion.get("operator")
        expected = assertion.get("expected")
        if operator == "equals":
            return expected == status
        if operator == "in" and isinstance(expected, list) and expected:
            return all(item == status for item in expected)
        return False

    @classmethod
    def _explicit_outcome_state(cls, assertions, defaults):
        expected_values = {
            "$.code": (0, 200),
            "$.success": (True,),
        }
        expected_values.update(
            {
                item.get("path"): (item.get("expected"),)
                for item in defaults
                if item.get("path") in expected_values
                and item.get("operator") == "equals"
            }
        )
        saw_precise_success = False
        for item in assertions:
            if not cls._enabled(item) or item.get("type") != "json_path":
                continue
            path = item.get("path")
            if path not in expected_values:
                continue
            accepted = [
                expected
                for expected in expected_values[path]
                if cls._assertion_accepts_value(item, expected)
            ]
            if not accepted:
                return "negative"
            if any(cls._is_precise_value_assertion(item, expected) for expected in accepted):
                saw_precise_success = True
        return "positive" if saw_precise_success else "unspecified"

    @staticmethod
    def _assertion_accepts_value(assertion, actual):
        operator = assertion.get("operator")
        expected = assertion.get("expected")
        if operator == "exists":
            return True
        if operator == "not_exists":
            return False
        if operator == "equals":
            return actual == expected
        if operator == "not_equals":
            return actual != expected
        if operator == "contains":
            try:
                return expected in actual
            except TypeError:
                return False
        if operator == "not_contains":
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
            try:
                return isinstance(actual, str) and re.search(expected, actual) is not None
            except (re.error, TypeError):
                return False
        if operator == "in":
            return isinstance(expected, list) and actual in expected
        return False

    @staticmethod
    def _is_precise_value_assertion(assertion, expected):
        operator = assertion.get("operator")
        actual_expected = assertion.get("expected")
        if operator == "equals":
            return actual_expected == expected
        if operator == "in" and isinstance(actual_expected, list) and actual_expected:
            return all(item == expected for item in actual_expected)
        return False

    @classmethod
    def _is_business_default_covered(cls, assertions, default):
        default_path = default.get("path")
        for item in assertions:
            if not cls._enabled(item) or item.get("type") != "json_path":
                continue
            path = item.get("path")
            if default_path == "$.data" and cls._is_data_path(path):
                if item.get("operator") != "not_exists":
                    return True
                continue
            if path != default_path:
                continue
            if default.get("operator") == "equals":
                if (
                    item.get("operator") == "equals"
                    and item.get("expected") == default.get("expected")
                ):
                    return True
                if item.get("operator") == "in":
                    values = item.get("expected")
                    if (
                        isinstance(values, list)
                        and values
                        and all(value == default.get("expected") for value in values)
                    ):
                        return True
            elif default.get("operator") == "exists":
                return True
        return False

    @classmethod
    def _has_explicit_data_absence(cls, assertions, defaults):
        if not any(
            item.get("path") == "$.data" and item.get("operator") == "exists"
            for item in defaults
        ):
            return False
        return any(
            cls._enabled(item)
            and item.get("type") == "json_path"
            and item.get("path") == "$.data"
            and item.get("operator") == "not_exists"
            for item in assertions
        )

    @staticmethod
    def _is_data_path(path):
        return isinstance(path, str) and (
            path == "$.data" or path.startswith("$.data.") or path.startswith("$.data[")
        )

    @staticmethod
    def _success_status_code(operation):
        codes = ResponseAssertionPolicy._documented_success_status_codes(operation)
        if 200 in codes:
            return 200
        return min(codes) if codes else 200

    @staticmethod
    def _documented_success_status_codes(operation):
        responses = operation.get("responses") if isinstance(operation, Mapping) else {}
        if not isinstance(responses, Mapping):
            return (200,)
        codes = []
        for key in responses:
            text = str(key)
            if text.isdigit() and 200 <= int(text) <= 299:
                codes.append(int(text))
        return tuple(sorted(set(codes))) or (200,)

    @classmethod
    def _business_success_assertions(cls, operation, status_code):
        response_entry = cls._response_media_entry(operation, status_code)
        if not response_entry:
            return ()
        media_type, response = response_entry
        if not cls._is_json_media_type(media_type):
            return ()
        schema = cls._resolve_schema(response.get("schema"), operation)
        example = cls._response_example(response)
        assertions = []
        success_assertion = (
            cls._success_assertion_from_value(example)
            or cls._success_assertion_from_schema(schema, operation)
        )
        if success_assertion is None and cls._uses_platform_success_envelope(
            schema,
            example,
        ):
            success_assertion = cls._code_assertion(0)
        if success_assertion:
            assertions.append(success_assertion)
        data_assertion = cls._data_presence_assertion(schema, example, operation)
        if data_assertion:
            assertions.append(data_assertion)
        return tuple(assertions)

    @classmethod
    def _response_media_entry(cls, operation, status_code):
        responses = operation.get("responses") if isinstance(operation, Mapping) else {}
        if not isinstance(responses, Mapping):
            return None
        response = responses.get(str(status_code)) or responses.get(status_code)
        response = cls._resolve_reference(response, operation)
        content = response.get("content") if isinstance(response, Mapping) else None
        if not isinstance(content, Mapping):
            return None
        return cls._preferred_media_entry(content)

    @staticmethod
    def _preferred_media_entry(content):
        entries = list(content.items())
        preferred = (
            next((item for item in entries if item[0] == "application/json"), None)
            or next((item for item in entries if str(item[0]).endswith("+json")), None)
            or (entries[0] if entries else None)
        )
        return preferred if preferred and isinstance(preferred[1], Mapping) else None

    @staticmethod
    def _is_json_media_type(media_type):
        if not isinstance(media_type, str):
            return False
        normalized = media_type.split(";", 1)[0].strip().lower()
        if (
            "ndjson" in normalized
            or "json-seq" in normalized
            or "stream+json" in normalized
        ):
            return False
        return normalized == "application/json" or normalized.endswith("+json")

    @staticmethod
    def _response_example(media):
        if not isinstance(media, Mapping):
            return None
        if "example" in media:
            return copy.deepcopy(media["example"])
        examples = media.get("examples")
        if isinstance(examples, Mapping):
            for item in examples.values():
                if isinstance(item, Mapping) and "value" in item:
                    return copy.deepcopy(item["value"])
        return None

    @classmethod
    def _success_assertion_from_value(cls, value):
        if not isinstance(value, Mapping):
            return None
        code = value.get("code")
        if code in (0, 200):
            return cls._code_assertion(code)
        if value.get("success") is True:
            return cls._success_true_assertion()
        return None

    @classmethod
    def _success_assertion_from_schema(cls, schema, operation):
        schema = cls._resolve_schema(schema, operation)
        if not isinstance(schema, Mapping):
            return None
        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            return None
        code = cls._resolve_schema(properties.get("code"), operation)
        if isinstance(code, Mapping):
            expected = cls._schema_explicit_value(code, missing=_MISSING)
            if expected in (0, 200):
                return cls._code_assertion(expected)
            if expected is _MISSING:
                return cls._code_assertion(0)
        success = cls._resolve_schema(properties.get("success"), operation)
        if isinstance(success, Mapping):
            expected = cls._schema_explicit_value(success)
            if expected is True:
                return cls._success_true_assertion()
        return None

    @classmethod
    def _data_presence_assertion(cls, schema, example, operation):
        if isinstance(example, Mapping) and "data" in example:
            return cls._json_path_exists_assertion("$.data")
        schema = cls._resolve_schema(schema, operation)
        properties = schema.get("properties") if isinstance(schema, Mapping) else None
        if isinstance(properties, Mapping) and "data" in properties:
            return cls._json_path_exists_assertion("$.data")
        return None

    @classmethod
    def _uses_platform_success_envelope(cls, schema, example):
        schema_type = cls._schema_type(schema) if isinstance(schema, Mapping) else None
        if schema_type == "object" or (
            isinstance(schema, Mapping)
            and isinstance(schema.get("properties"), Mapping)
        ):
            return True
        return isinstance(example, Mapping)

    @staticmethod
    def _schema_type(schema):
        schema_type = schema.get("type") if isinstance(schema, Mapping) else None
        if isinstance(schema_type, list):
            return next((item for item in schema_type if item != "null"), None)
        return schema_type

    @staticmethod
    def _schema_explicit_value(schema, missing=_MISSING):
        if not isinstance(schema, Mapping):
            return missing
        for key in ("example", "default", "const"):
            if key in schema:
                return schema[key]
        enum_values = schema.get("enum")
        if isinstance(enum_values, list) and enum_values:
            return enum_values[0]
        return missing

    @classmethod
    def _resolve_schema(cls, schema, operation):
        current = schema
        visited = set()
        while isinstance(current, Mapping) and isinstance(current.get("$ref"), str):
            reference = current["$ref"]
            if reference in visited:
                return current
            visited.add(reference)
            resolved = cls._resolve_reference({"$ref": reference}, operation)
            if resolved is current:
                return current
            current = resolved
        if isinstance(current, Mapping) and isinstance(current.get("allOf"), list):
            merged = {}
            for item in current["allOf"]:
                child = cls._resolve_schema(item, operation)
                if isinstance(child, Mapping):
                    merged = cls._merge_schema(merged, child)
            return merged or current
        if isinstance(current, Mapping):
            for key in ("oneOf", "anyOf"):
                items = current.get(key)
                if isinstance(items, list) and items:
                    first = cls._resolve_schema(items[0], operation)
                    return first if isinstance(first, Mapping) else current
        return current

    @staticmethod
    def _resolve_reference(value, operation):
        current = value
        dependencies = (
            operation.get("resolved_dependencies", {})
            if isinstance(operation, Mapping)
            else {}
        )
        seen = set()
        while isinstance(current, Mapping) and isinstance(current.get("$ref"), str):
            reference = current["$ref"]
            if (
                reference in seen
                or not isinstance(dependencies, Mapping)
                or reference not in dependencies
            ):
                return current
            seen.add(reference)
            current = dependencies[reference]
        return current

    @staticmethod
    def _merge_schema(left, right):
        output = copy.deepcopy(dict(left))
        for key, value in right.items():
            if (
                key == "properties"
                and isinstance(value, Mapping)
                and isinstance(output.get(key), Mapping)
            ):
                merged = copy.deepcopy(dict(output[key]))
                merged.update(copy.deepcopy(dict(value)))
                output[key] = merged
            elif (
                key == "required"
                and isinstance(value, list)
                and isinstance(output.get(key), list)
            ):
                output[key] = list(dict.fromkeys(output[key] + value))
            else:
                output[key] = copy.deepcopy(value)
        return output
