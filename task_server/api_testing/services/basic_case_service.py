"""Deterministic basic positive API case generation."""

import copy
from typing import Mapping

from ..repositories.ai_job_repository import AiJobRepository
from .ai_service import AiCaseService, SENSITIVE_KEY
from .case_service import CaseService


_MISSING = object()


class BasicCaseService:
    """Create simple editable positive case drafts from imported endpoint contracts."""

    def __init__(self, session_factory):
        self.session_factory = session_factory

    def generate(self, endpoint_ids, environment_revision_id, actor_id):
        identifiers = AiCaseService._endpoint_ids(endpoint_ids)
        with self.session_factory() as session:
            repository = AiJobRepository(session)
            endpoints = repository.get_endpoints(identifiers)
            if len(endpoints) != len(identifiers):
                raise ValueError("all selected API endpoints must exist")
            ordered_endpoints = [endpoints[item] for item in identifiers]
            revision_ids = {item.revision_id for item in ordered_endpoints}
            if len(revision_ids) != 1:
                raise ValueError("selected endpoints must share one source revision")
            source_revision = repository.get_source_revision(next(iter(revision_ids)))
            source = repository.get_source(source_revision.source_id) if source_revision else None
            if (
                source is None
                or source_revision.status != "active"
                or source.active_revision_id != source_revision.id
            ):
                raise ValueError("selected endpoints must belong to the active source revision")
            environment_revision = repository.get_environment_revision(environment_revision_id)
            environment = (
                repository.get_environment(environment_revision.environment_id)
                if environment_revision
                else None
            )
            if environment is None or environment.project_id != source.project_id:
                raise ValueError("environment and endpoints must belong to the same project")
            variables = repository.get_environment_variables(environment_revision.id)

        case_service = CaseService(self.session_factory)
        generated = []
        for endpoint in ordered_endpoints:
            payload = self.build_case_payload(endpoint, environment_revision, variables)
            AiCaseService._assert_no_literal_secrets(payload)
            generated.append(case_service.create_draft(endpoint.id, payload, "imported", actor_id))
        return tuple(generated)

    @classmethod
    def build_case_payload(cls, endpoint, environment_revision, environment_variables):
        operation = endpoint.operation if isinstance(endpoint.operation, Mapping) else {}
        request = {
            "method": str(endpoint.method).upper(),
            "path": str(endpoint.path),
            "service": "default",
            "path_params": {},
            "query": {},
            "headers": cls._runtime_headers(operation, environment_revision, environment_variables),
            "cookies": {},
            "body": cls._request_body(operation, environment_variables),
        }
        cls._complete_parameters(request, operation)
        assertions = cls._assertions(operation)
        title = str(getattr(endpoint, "summary", "") or "").strip() or f"{request['method']} {request['path']}"
        return {
            "name": f"{title[:260]} - 基础正向流程",
            "purpose": f"验证{title}接口在平台环境鉴权与基础参数下可以成功返回",
            "priority": "P1",
            "request": request,
            "data_rows": [],
            "assertions": assertions,
            "extractions": [],
            "dependencies": [],
            "processing": {"pre": [], "post": []},
        }

    @classmethod
    def _complete_parameters(cls, request, operation):
        location_fields = {
            "path": "path_params",
            "query": "query",
            "cookie": "cookies",
        }
        for parameter in cls._parameters(operation):
            target_field = location_fields.get(parameter.get("in"))
            name = parameter.get("name")
            if not target_field or not isinstance(name, str) or not name:
                continue
            value, has_value = AiCaseService._parameter_seed_value(
                parameter,
                operation,
                allow_synthetic=parameter.get("required") is True,
            )
            if has_value:
                request[target_field][name] = value

    @staticmethod
    def _parameters(operation):
        items = []
        seen = set()
        for collection in (operation.get("path_parameters", []), operation.get("parameters", [])):
            if not isinstance(collection, list):
                continue
            for parameter in collection:
                if not isinstance(parameter, Mapping):
                    continue
                identity = (parameter.get("in"), parameter.get("name"))
                if identity in seen:
                    continue
                seen.add(identity)
                items.append(parameter)
        return items

    @classmethod
    def _runtime_headers(cls, operation, environment_revision, environment_variables):
        configured_headers = {
            str(name).lower()
            for name in (getattr(environment_revision, "default_headers", {}) or {})
        }
        variable_names = {
            item.name.lower(): item.name
            for item in environment_variables
            if getattr(item, "enabled", False)
        }
        headers = {}
        for parameter in cls._parameters(operation):
            if parameter.get("in") != "header" or parameter.get("required") is not True:
                continue
            name = parameter.get("name")
            if not isinstance(name, str) or not name:
                continue
            normalized = name.lower()
            if normalized in configured_headers:
                continue
            variable_name = variable_names.get(normalized)
            if variable_name:
                headers[name] = "{{%s}}" % variable_name
        cls._add_platform_runtime_headers(headers, configured_headers, variable_names)
        return headers

    @staticmethod
    def _add_platform_runtime_headers(headers, configured_headers, variable_names):
        for header_name, variable_name in (
            ("Biz", "biz"),
            ("Authorization", "authorization"),
            ("Authorization", "zxbtoken"),
        ):
            normalized_header = header_name.lower()
            existing_headers = {str(name).lower() for name in headers}
            if normalized_header in configured_headers or normalized_header in existing_headers:
                continue
            resolved_variable = variable_names.get(variable_name)
            if resolved_variable:
                headers[header_name] = "{{%s}}" % resolved_variable

    @classmethod
    def _request_body(cls, operation, environment_variables):
        request_body = cls._resolve_reference(operation.get("requestBody"), operation)
        if not isinstance(request_body, Mapping):
            return None
        content = request_body.get("content")
        if not isinstance(content, Mapping):
            return None
        media = cls._preferred_media(content)
        if not media:
            return None
        if "example" in media:
            return cls._safe_body_value(media["example"], environment_variables)
        examples = media.get("examples")
        if isinstance(examples, Mapping):
            for item in examples.values():
                if isinstance(item, Mapping) and "value" in item:
                    return cls._safe_body_value(item["value"], environment_variables)
        schema = cls._resolve_schema(media.get("schema"), operation)
        value, has_value = cls._value_for_schema(
            schema,
            operation,
            environment_variables,
            field_name="body",
            required=True,
        )
        return value if has_value else None

    @staticmethod
    def _preferred_media(content):
        entries = list(content.items())
        preferred = (
            next((item for item in entries if item[0] == "application/json"), None)
            or next((item for item in entries if str(item[0]).endswith("+json")), None)
            or (entries[0] if entries else None)
        )
        return preferred[1] if preferred and isinstance(preferred[1], Mapping) else None

    @classmethod
    def _assertions(cls, operation):
        status_code = cls._success_status_code(operation)
        assertions = [
            {
                "type": "status_code",
                "operator": "equals",
                "expected": status_code,
                "timeout_ms": 0,
                "enabled": True,
            }
        ]
        success_assertion = cls._business_success_assertion(operation, status_code)
        if success_assertion:
            assertions.append(success_assertion)
        return assertions

    @staticmethod
    def _success_status_code(operation):
        responses = operation.get("responses") if isinstance(operation, Mapping) else {}
        if not isinstance(responses, Mapping):
            return 200
        codes = []
        for key in responses:
            text = str(key)
            if text.isdigit() and 200 <= int(text) <= 299:
                codes.append(int(text))
        if 200 in codes:
            return 200
        return min(codes) if codes else 200

    @classmethod
    def _business_success_assertion(cls, operation, status_code):
        response = cls._response_media(operation, status_code)
        if not response:
            return None
        example = cls._response_example(response)
        assertion = cls._success_assertion_from_value(example)
        if assertion:
            return assertion
        schema = cls._resolve_schema(response.get("schema"), operation)
        return cls._success_assertion_from_schema(schema, operation)

    @classmethod
    def _response_media(cls, operation, status_code):
        responses = operation.get("responses") if isinstance(operation, Mapping) else {}
        if not isinstance(responses, Mapping):
            return None
        response = responses.get(str(status_code)) or responses.get(status_code)
        response = cls._resolve_reference(response, operation)
        content = response.get("content") if isinstance(response, Mapping) else None
        if not isinstance(content, Mapping):
            return None
        return cls._preferred_media(content)

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

    @staticmethod
    def _success_assertion_from_value(value):
        if not isinstance(value, Mapping):
            return None
        if value.get("code") == 0:
            return {
                "type": "json_path",
                "path": "$.code",
                "operator": "equals",
                "expected": 0,
                "timeout_ms": 0,
                "enabled": True,
            }
        if value.get("success") is True:
            return {
                "type": "json_path",
                "path": "$.success",
                "operator": "equals",
                "expected": True,
                "timeout_ms": 0,
                "enabled": True,
            }
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
            expected = cls._schema_explicit_value(code)
            if expected == 0:
                return {
                    "type": "json_path",
                    "path": "$.code",
                    "operator": "equals",
                    "expected": 0,
                    "timeout_ms": 0,
                    "enabled": True,
                }
        success = cls._resolve_schema(properties.get("success"), operation)
        if isinstance(success, Mapping):
            expected = cls._schema_explicit_value(success)
            if expected is True:
                return {
                    "type": "json_path",
                    "path": "$.success",
                    "operator": "equals",
                    "expected": True,
                    "timeout_ms": 0,
                    "enabled": True,
                }
        return None

    @classmethod
    def _value_for_schema(
        cls,
        schema,
        operation,
        environment_variables,
        *,
        field_name,
        required,
    ):
        schema = cls._resolve_schema(schema, operation)
        if not isinstance(schema, Mapping):
            return None, False
        if cls._is_sensitive_name(field_name):
            return cls._placeholder_for(field_name, environment_variables), True
        explicit = cls._schema_explicit_value(schema, missing=_MISSING)
        if explicit is not _MISSING:
            return copy.deepcopy(explicit), True
        schema_type = cls._schema_type(schema)
        if schema_type == "object" or isinstance(schema.get("properties"), Mapping):
            properties = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
            required_names = set(schema.get("required") or [])
            result = {}
            for name, child_schema in properties.items():
                child_schema = cls._resolve_schema(child_schema, operation)
                include = (
                    name in required_names
                    or cls._has_explicit_value(child_schema)
                    or (
                        isinstance(child_schema, Mapping)
                        and isinstance(child_schema.get("enum"), list)
                        and bool(child_schema.get("enum"))
                    )
                )
                if not include:
                    continue
                value, has_value = cls._value_for_schema(
                    child_schema,
                    operation,
                    environment_variables,
                    field_name=str(name),
                    required=name in required_names,
                )
                if has_value:
                    result[str(name)] = value
            return result, bool(result) or required
        if schema_type == "array":
            items = schema.get("items")
            if int(schema.get("minItems") or 0) > 0:
                value, has_value = cls._value_for_schema(
                    items,
                    operation,
                    environment_variables,
                    field_name=field_name,
                    required=True,
                )
                return ([value] if has_value else []), True
            return [], required
        return AiCaseService._synthetic_value_for_schema(schema), True

    @staticmethod
    def _schema_type(schema):
        schema_type = schema.get("type") if isinstance(schema, Mapping) else None
        if isinstance(schema_type, list):
            return next((item for item in schema_type if item != "null"), None)
        return schema_type

    @classmethod
    def _schema_explicit_value(cls, schema, missing=_MISSING):
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
    def _has_explicit_value(cls, schema):
        return cls._schema_explicit_value(schema, missing=_MISSING) is not _MISSING

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
        dependencies = operation.get("resolved_dependencies", {}) if isinstance(operation, Mapping) else {}
        seen = set()
        while isinstance(current, Mapping) and isinstance(current.get("$ref"), str):
            reference = current["$ref"]
            if reference in seen or not isinstance(dependencies, Mapping) or reference not in dependencies:
                return current
            seen.add(reference)
            current = dependencies[reference]
        return current

    @staticmethod
    def _merge_schema(left, right):
        output = copy.deepcopy(dict(left))
        for key, value in right.items():
            if key == "properties" and isinstance(value, Mapping) and isinstance(output.get(key), Mapping):
                merged = copy.deepcopy(dict(output[key]))
                merged.update(copy.deepcopy(dict(value)))
                output[key] = merged
            elif key == "required" and isinstance(value, list) and isinstance(output.get(key), list):
                output[key] = list(dict.fromkeys(output[key] + value))
            else:
                output[key] = copy.deepcopy(value)
        return output

    @classmethod
    def _safe_body_value(cls, value, environment_variables, field_name="body"):
        if isinstance(value, Mapping):
            output = {}
            for raw_key, item in value.items():
                name = str(raw_key)
                if cls._is_sensitive_name(name) and cls._is_nonempty(item):
                    output[name] = cls._placeholder_for(name, environment_variables)
                else:
                    output[name] = cls._safe_body_value(
                        item,
                        environment_variables,
                        field_name=name,
                    )
            return output
        if isinstance(value, list):
            return [
                cls._safe_body_value(item, environment_variables, field_name=field_name)
                for item in value
            ]
        if cls._is_sensitive_name(field_name) and cls._is_nonempty(value):
            return cls._placeholder_for(field_name, environment_variables)
        return copy.deepcopy(value)

    @staticmethod
    def _is_nonempty(value):
        return value not in (None, "", [], {})

    @staticmethod
    def _placeholder_for(name, environment_variables):
        normalized = str(name).lower()
        for variable in environment_variables:
            variable_name = getattr(variable, "name", "")
            if str(variable_name).lower() == normalized and getattr(variable, "enabled", False):
                return "{{%s}}" % variable_name
        return "{{%s}}" % str(name)

    @staticmethod
    def _is_sensitive_name(name):
        return bool(SENSITIVE_KEY.search(str(name)))
