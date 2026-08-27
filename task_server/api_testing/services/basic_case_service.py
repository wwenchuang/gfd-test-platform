"""Deterministic basic positive API case generation."""

import copy
import json
import re
from typing import Mapping

from ..repositories.ai_job_repository import AiJobRepository
from .ai_service import AiCaseService, SENSITIVE_KEY
from .case_service import CaseService
from .response_assertion_policy import ResponseAssertionPolicy
from .workflow_policy import (
    PRINT_TASK_VARIABLE_NAMES,
    classify_endpoint_workflow,
    is_print_cancel_step,
    is_print_cancel_endpoint,
    is_print_dispatch_endpoint,
)


_MISSING = object()
_PLACEHOLDER = re.compile(r"\{\{\s*[A-Za-z_][A-Za-z0-9_.-]*\s*\}\}")


class BasicCaseService:
    """Create simple editable positive case drafts from imported endpoint contracts."""

    def __init__(self, session_factory):
        self.session_factory = session_factory

    def preview(self, endpoint_ids, environment_revision_id, actor_id):
        ordered_endpoints, environment_revision, variables, endpoint_catalog = self._generation_context(
            endpoint_ids,
            environment_revision_id,
        )
        previews = []
        for endpoint in ordered_endpoints:
            payload = self.build_case_payload(
                endpoint,
                environment_revision,
                variables,
                endpoint_catalog=endpoint_catalog,
            )
            AiCaseService._assert_no_literal_secrets(payload)
            previews.append(
                {
                    "id": f"basic-positive-{endpoint.id}",
                    "endpoint_id": endpoint.id,
                    "origin": "imported",
                    "workflow": classify_endpoint_workflow(endpoint),
                    "case": payload,
                }
            )
        return tuple(previews)

    def generate(self, endpoint_ids, environment_revision_id, actor_id):
        previews = self.preview(endpoint_ids, environment_revision_id, actor_id)
        case_service = CaseService(self.session_factory)
        generated = []
        for preview in previews:
            generated.append(
                case_service.create_draft(
                    preview["endpoint_id"],
                    preview["case"],
                    "imported",
                    actor_id,
                )
            )
        return tuple(generated)

    def _generation_context(self, endpoint_ids, environment_revision_id):
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
            endpoint_catalog = repository.get_revision_endpoints(source_revision.id)
        return ordered_endpoints, environment_revision, variables, endpoint_catalog

    @classmethod
    def build_case_payload(
        cls,
        endpoint,
        environment_revision,
        environment_variables,
        *,
        endpoint_catalog=(),
    ):
        operation = endpoint.operation if isinstance(endpoint.operation, Mapping) else {}
        request = cls._request_for_endpoint(
            endpoint, environment_revision, environment_variables
        )
        assertions = cls._assertions(operation)
        title = str(getattr(endpoint, "summary", "") or "").strip() or f"{request['method']} {request['path']}"
        payload = {
            "name": f"{title[:260]} - 基础正向流程",
            "purpose": f"验证{title}接口在平台环境鉴权与基础参数下可以成功返回",
            "priority": "P1",
            "business": cls._business_for_endpoint(endpoint),
            "request": request,
            "data_rows": [],
            "assertions": assertions,
            "extractions": [],
            "dependencies": [],
            "processing": {
                "pre": [],
                "post": [],
                "setup_steps": [],
                "cleanup_steps": [],
            },
        }
        cls._apply_print_cleanup_policy(
            payload,
            endpoint,
            endpoint_catalog,
            environment_revision,
            environment_variables,
        )
        return payload

    @staticmethod
    def _business_for_endpoint(endpoint):
        from task_server.services.business_line_service import preferred_business_line_id

        markers = [
            getattr(endpoint, "summary", ""),
            getattr(endpoint, "path", ""),
            *(getattr(endpoint, "tags", None) or ()),
        ]
        operation = getattr(endpoint, "operation", None)
        if isinstance(operation, Mapping):
            markers.append(operation.get("x-apifox-folder") or "")
        return preferred_business_line_id(*markers)

    @classmethod
    def _request_for_endpoint(
        cls, endpoint, environment_revision, environment_variables
    ):
        operation = endpoint.operation if isinstance(endpoint.operation, Mapping) else {}
        request = {
            "method": str(endpoint.method).upper(),
            "path": str(endpoint.path),
            "service": "default",
            "path_params": {},
            "query": {},
            "headers": cls._runtime_headers(
                operation, environment_revision, environment_variables
            ),
            "cookies": {},
            "body": cls._request_body(operation, environment_variables),
        }
        cls._complete_parameters(request, operation)
        return request

    @classmethod
    def _apply_print_cleanup_policy(
        cls,
        payload,
        endpoint,
        endpoint_catalog,
        environment_revision,
        environment_variables,
    ):
        if not is_print_dispatch_endpoint(endpoint):
            return
        processing = payload.setdefault("processing", {})
        processing.setdefault("pre", [])
        processing.setdefault("post", [])
        processing.setdefault("setup_steps", [])
        cleanup_steps = processing.setdefault("cleanup_steps", [])
        existing_targets = [
            item
            for item in payload.setdefault("extractions", [])
            if isinstance(item, Mapping)
            and item.get("target") in PRINT_TASK_VARIABLE_NAMES
            and isinstance(item.get("path"), str)
            and item["path"].startswith("$")
        ]
        if existing_targets and any(
            is_print_cancel_step(step, {item["target"] for item in existing_targets})
            for step in cleanup_steps
        ):
            return
        cancel_endpoint = cls._select_print_cancel_endpoint(
            endpoint, endpoint_catalog
        )
        task_binding = (
            (existing_targets[0]["target"], existing_targets[0]["path"])
            if existing_targets
            else cls._print_task_binding(
                endpoint.operation if isinstance(endpoint.operation, Mapping) else {}
            )
        )
        if cancel_endpoint is None or task_binding is None:
            return
        target, path = task_binding
        cancel_request = cls._request_for_endpoint(
            cancel_endpoint, environment_revision, environment_variables
        )
        if not cls._bind_print_task(cancel_request, target):
            return
        cancel_operation = (
            cancel_endpoint.operation
            if isinstance(cancel_endpoint.operation, Mapping)
            else {}
        )
        if not existing_targets:
            payload["extractions"].append(
                {
                    "target": target,
                    "type": "json_path",
                    "path": path,
                    "required": True,
                }
            )
        cleanup_steps.append(
            {
                "name": "取消本次打印",
                "enabled": True,
                "request": cancel_request,
                "assertions": cls._assertions(cancel_operation),
                "extractions": [],
                "required_variables": [target],
            }
        )
        payload["purpose"] += "；下发成功后使用本次响应任务标识取消打印并校验取消结果"

    @staticmethod
    def _select_print_cancel_endpoint(endpoint, endpoint_catalog):
        candidates = [
            item
            for item in endpoint_catalog or ()
            if getattr(item, "id", None) != getattr(endpoint, "id", None)
            and is_print_cancel_endpoint(item)
        ]
        if not candidates:
            return None
        parent = str(getattr(endpoint, "path", "") or "").rstrip("/").rsplit("/", 1)[0]
        exact = [
            item
            for item in candidates
            if str(getattr(item, "path", "") or "").rstrip("/").rsplit("/", 1)[0]
            == parent
            and str(getattr(item, "path", "") or "").rstrip("/").rsplit("/", 1)[-1].lower()
            in {"cancel", "stop", "terminate"}
        ]
        if len(exact) == 1:
            return exact[0]
        return candidates[0] if len(candidates) == 1 else None

    @classmethod
    def _print_task_binding(cls, operation):
        media = cls._success_response_media(operation)
        if not isinstance(media, Mapping):
            return None
        examples = []
        if "example" in media:
            examples.append(media["example"])
        media_examples = media.get("examples")
        if isinstance(media_examples, Mapping):
            for item in media_examples.values():
                if isinstance(item, Mapping) and "value" in item:
                    examples.append(item["value"])
        for example in examples:
            found = cls._find_named_path(example, PRINT_TASK_VARIABLE_NAMES)
            if found is not None:
                return found
        schema = cls._resolve_schema(media.get("schema"), operation)
        return cls._find_schema_path(schema, PRINT_TASK_VARIABLE_NAMES, operation)

    @classmethod
    def _success_response_media(cls, operation):
        responses = operation.get("responses")
        if not isinstance(responses, Mapping):
            return None
        ordered = sorted(
            (
                (str(status), response)
                for status, response in responses.items()
                if str(status).isdigit() and 200 <= int(status) < 300
            ),
            key=lambda item: int(item[0]),
        )
        for _status, response in ordered:
            response = cls._resolve_reference(response, operation)
            content = response.get("content") if isinstance(response, Mapping) else None
            if not isinstance(content, Mapping):
                continue
            media = cls._preferred_media(content)
            if isinstance(media, Mapping):
                return media
        return None

    @classmethod
    def _find_named_path(cls, value, names, path="$"):
        if isinstance(value, Mapping):
            for key, item in value.items():
                child_path = f"{path}.{key}"
                if str(key) in names:
                    return str(key), child_path
                found = cls._find_named_path(item, names, child_path)
                if found is not None:
                    return found
        elif isinstance(value, list) and value:
            return cls._find_named_path(value[0], names, f"{path}[0]")
        return None

    @classmethod
    def _find_schema_path(cls, schema, names, operation, path="$"):
        schema = cls._resolve_schema(schema, operation)
        if not isinstance(schema, Mapping):
            return None
        properties = schema.get("properties")
        if isinstance(properties, Mapping):
            for key, child in properties.items():
                child_path = f"{path}.{key}"
                if str(key) in names:
                    return str(key), child_path
                found = cls._find_schema_path(child, names, operation, child_path)
                if found is not None:
                    return found
        items = schema.get("items")
        if isinstance(items, Mapping):
            return cls._find_schema_path(items, names, operation, f"{path}[0]")
        return None

    @classmethod
    def _bind_print_task(cls, request, target):
        inserted = False
        accepted_names = {name.lower() for name in PRINT_TASK_VARIABLE_NAMES}

        def replace(value):
            nonlocal inserted
            if isinstance(value, Mapping):
                output = {}
                for key, item in value.items():
                    if str(key).lower() in accepted_names:
                        output[key] = "{{%s}}" % target
                        inserted = True
                    else:
                        output[key] = replace(item)
                return output
            if isinstance(value, list):
                return [replace(item) for item in value]
            return copy.deepcopy(value)

        for field in ("path_params", "query", "headers", "cookies", "body"):
            request[field] = replace(request.get(field))
        return inserted

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
            value, has_value = cls._parameter_seed_value(
                parameter,
                operation,
                allow_synthetic=parameter.get("required") is True,
            )
            if has_value:
                request[target_field][name] = value

    @classmethod
    def _parameter_seed_value(cls, parameter, operation, *, allow_synthetic):
        schema = cls._resolve_schema(parameter.get("schema"), operation)
        if "example" in parameter:
            return cls._conform_parameter_value(parameter["example"], schema)
        if "default" in parameter:
            return cls._conform_parameter_value(parameter["default"], schema)
        examples = parameter.get("examples")
        if isinstance(examples, Mapping):
            for item in examples.values():
                if isinstance(item, Mapping) and "value" in item:
                    return cls._conform_parameter_value(item["value"], schema)
                if item is not None and not isinstance(item, Mapping):
                    return cls._conform_parameter_value(item, schema)
        if isinstance(schema, Mapping):
            if "example" in schema:
                return cls._conform_parameter_value(schema["example"], schema)
            if "default" in schema:
                return cls._conform_parameter_value(schema["default"], schema)
            enum_values = schema.get("enum")
            if isinstance(enum_values, list) and enum_values:
                return cls._conform_parameter_value(enum_values[0], schema)
            if allow_synthetic:
                return cls._synthetic_value_for_schema(schema), True
        if allow_synthetic:
            return "sample", True
        return None, False

    @classmethod
    def _conform_parameter_value(cls, value, schema):
        if not isinstance(schema, Mapping):
            return copy.deepcopy(value), True
        schema_type = cls._schema_type(schema)
        if cls._value_matches_schema(value, schema_type) and not cls._has_malformed_placeholder(value):
            if cls._has_unsafe_literal_scalar(value):
                return cls._synthetic_value_for_schema(schema), True
            return copy.deepcopy(value), True
        return cls._synthetic_value_for_schema(schema), True

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
        schema = cls._resolve_schema(media.get("schema"), operation)
        required = bool(request_body.get("required"))
        if "example" in media:
            return cls._body_value_from_example(
                media["example"],
                schema,
                operation,
                environment_variables,
                required,
            )
        examples = media.get("examples")
        if isinstance(examples, Mapping):
            for item in examples.values():
                if isinstance(item, Mapping) and "value" in item:
                    return cls._body_value_from_example(
                        item["value"],
                        schema,
                        operation,
                        environment_variables,
                        required,
                    )
        value, has_value = cls._value_for_schema(
            schema,
            operation,
            environment_variables,
            field_name="body",
            required=required,
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
    def _body_value_from_example(
        cls,
        value,
        schema,
        operation,
        environment_variables,
        required,
    ):
        safe = cls._safe_body_value(value, environment_variables)
        if isinstance(schema, Mapping):
            conformed, has_value = cls._conform_value_to_schema(
                safe,
                schema,
                operation,
                environment_variables,
                field_name="body",
                required=required,
            )
            return conformed if has_value else None
        return cls._repair_malformed_placeholders(safe)

    @classmethod
    def _assertions(cls, operation):
        return ResponseAssertionPolicy.default_positive_assertions(operation)

    @classmethod
    def _value_for_schema(
        cls,
        schema,
        operation,
        environment_variables,
        *,
        field_name,
        required,
        allow_explicit=True,
    ):
        schema = cls._resolve_schema(schema, operation)
        if not isinstance(schema, Mapping):
            return ("sample", True) if required else (None, False)
        if cls._is_sensitive_name(field_name):
            return cls._placeholder_for(field_name, environment_variables), True
        explicit = cls._schema_explicit_value(schema, missing=_MISSING)
        schema_type = cls._schema_type(schema)
        if allow_explicit and explicit is not _MISSING:
            return cls._conform_value_to_schema(
                cls._safe_body_value(explicit, environment_variables, field_name),
                schema,
                operation,
                environment_variables,
                field_name=field_name,
                required=required,
            )
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
            for name in sorted(required_names):
                if name not in result and name not in properties:
                    result[str(name)] = "sample"
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
        if schema_type == "null":
            return None, True
        return cls._synthetic_value_for_schema(schema), True

    @classmethod
    def _conform_value_to_schema(
        cls,
        value,
        schema,
        operation,
        environment_variables,
        *,
        field_name,
        required,
    ):
        schema = cls._resolve_schema(schema, operation)
        if not isinstance(schema, Mapping):
            return cls._repair_malformed_placeholders(value), value is not None or required
        coerced = cls._coerce_json_string(value, schema)
        schema_type = cls._schema_type(schema)
        if schema_type is None:
            if isinstance(schema.get("properties"), Mapping):
                schema_type = "object"
            elif isinstance(schema.get("items"), Mapping):
                schema_type = "array"
        if schema_type == "object":
            return cls._conform_object_to_schema(
                coerced,
                schema,
                operation,
                environment_variables,
                field_name=field_name,
                required=required,
            )
        if schema_type == "array":
            return cls._conform_array_to_schema(
                coerced,
                schema,
                operation,
                environment_variables,
                field_name=field_name,
                required=required,
            )
        if cls._value_matches_schema(coerced, schema_type) and not cls._has_malformed_placeholder(coerced):
            if cls._has_unsafe_literal_scalar(coerced):
                return cls._value_for_schema(
                    schema,
                    operation,
                    environment_variables,
                    field_name=field_name,
                    required=True,
                    allow_explicit=False,
                )
            return copy.deepcopy(coerced), coerced is not None or required
        return cls._value_for_schema(
            schema,
            operation,
            environment_variables,
            field_name=field_name,
            required=True,
            allow_explicit=False,
        )

    @classmethod
    def _conform_object_to_schema(
        cls,
        value,
        schema,
        operation,
        environment_variables,
        *,
        field_name,
        required,
    ):
        source = value if isinstance(value, Mapping) else {}
        properties = schema.get("properties") if isinstance(schema.get("properties"), Mapping) else {}
        required_names = set(schema.get("required") or [])
        result = {}
        for raw_name, item in source.items():
            name = str(raw_name)
            child_schema = properties.get(name)
            if isinstance(child_schema, Mapping):
                child, has_child = cls._conform_value_to_schema(
                    item,
                    child_schema,
                    operation,
                    environment_variables,
                    field_name=name,
                    required=name in required_names,
                )
                if has_child:
                    result[name] = child
            else:
                result[name] = cls._repair_malformed_placeholders(item)
        for name in sorted(required_names):
            if name in result:
                continue
            child, has_child = cls._value_for_schema(
                properties.get(name),
                operation,
                environment_variables,
                field_name=str(name),
                required=True,
            )
            if has_child:
                result[str(name)] = child
        return result, bool(result) or required

    @classmethod
    def _conform_array_to_schema(
        cls,
        value,
        schema,
        operation,
        environment_variables,
        *,
        field_name,
        required,
    ):
        items_schema = schema.get("items")
        if isinstance(value, list):
            result = []
            for item in value:
                child, has_child = cls._conform_value_to_schema(
                    item,
                    items_schema,
                    operation,
                    environment_variables,
                    field_name=field_name,
                    required=True,
                )
                if has_child:
                    result.append(child)
            return result, bool(result) or required
        minimum = int(schema.get("minItems") or 0)
        if minimum > 0:
            child, has_child = cls._value_for_schema(
                items_schema,
                operation,
                environment_variables,
                field_name=field_name,
                required=True,
            )
            return ([child] if has_child else []), True
        return [], required

    @staticmethod
    def _coerce_json_string(value, schema):
        if not isinstance(value, str) or not isinstance(schema, Mapping):
            return value
        schema_type = schema.get("type")
        if schema_type not in {"object", "array"} and not (
            schema_type is None
            and (
                isinstance(schema.get("properties"), Mapping)
                or isinstance(schema.get("items"), Mapping)
            )
        ):
            return value
        try:
            parsed = json.loads(value)
        except Exception:
            return value
        return parsed

    @classmethod
    def _value_matches_schema(cls, value, schema_type):
        if isinstance(value, str) and _PLACEHOLDER.fullmatch(value.strip()):
            return True
        if schema_type is None:
            return True
        return {
            "null": value is None,
            "boolean": isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "string": isinstance(value, str),
            "array": isinstance(value, list),
            "object": isinstance(value, dict),
        }.get(schema_type, True)

    @classmethod
    def _synthetic_value_for_schema(cls, schema):
        schema_type = cls._schema_type(schema) if isinstance(schema, Mapping) else None
        if schema_type == "null":
            return None
        return AiCaseService._synthetic_value_for_schema(schema or {})

    @classmethod
    def _repair_malformed_placeholders(cls, value):
        if isinstance(value, Mapping):
            return {
                str(key): cls._repair_malformed_placeholders(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._repair_malformed_placeholders(item) for item in value]
        if isinstance(value, str) and cls._has_malformed_placeholder(value):
            return "sample"
        if cls._has_unsafe_literal_scalar(value):
            return "sample"
        return copy.deepcopy(value)

    @staticmethod
    def _has_malformed_placeholder(value):
        if not isinstance(value, str):
            return False
        scrubbed = _PLACEHOLDER.sub("", value)
        return "{{" in scrubbed or "}}" in scrubbed

    @staticmethod
    def _has_unsafe_literal_scalar(value):
        if not isinstance(value, str) or _PLACEHOLDER.fullmatch(value.strip()):
            return False
        return AiCaseService._redact_text(value) != value

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
