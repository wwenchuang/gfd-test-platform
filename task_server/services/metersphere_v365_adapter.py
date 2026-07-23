"""Exact protocol adapter primitives for MeterSphere v3.6.5-lts."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import re
import time
import uuid
from typing import Any, Callable, Dict, List

from task_server.storage import clean_id, read_json_file, safe_join, write_json_file


ADAPTER_ID = "metersphere_v3.6.5"
SUPPORTED_VERSIONS = {
    "v3.6.5-lts",
    "v3.6.5-lts-f043cdd2",
}
PROVIDER_TERMINAL_GRACE_MS = 300_000
_SENSITIVE_HEADER_KEY_PARTS = (
    "authorization",
    "token",
    "apikey",
    "accesskey",
    "secret",
    "cookie",
    "signature",
    "password",
    "credential",
)
_REQUEST_STEP_TYPES = {"API_CASE", "API", "CUSTOM_REQUEST", "SCRIPT"}


class MeterSphereV365DependencyError(RuntimeError):
    """Raised when the exact adapter cannot satisfy a runtime dependency."""


class MeterSphereV365ContractError(ValueError):
    """Raised when a platform case cannot be represented without losing intent."""

    def __init__(self, reason: str, message: str):
        super().__init__(message)
        self.reason = str(reason or "unsupported_contract")


def build_v365_auth_headers(
    access_key: str,
    secret_key: str,
    *,
    now_ms: int | None = None,
    nonce: str | None = None,
) -> Dict[str, str]:
    """Build the AES-CBC signature used by MeterSphere v3.6.5 API keys."""

    try:
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.padding import PKCS7
    except ImportError as exc:
        raise MeterSphereV365DependencyError(
            "MeterSphere 3.6.5 Access Key 认证需要服务端安装 cryptography"
        ) from exc

    access = str(access_key or "").strip()
    secret = str(secret_key or "").strip()
    access_bytes = access.encode("utf-8")
    secret_bytes = secret.encode("utf-8")
    if len(access_bytes) != 16:
        raise ValueError("MeterSphere Access Key 必须是 16 字节 AES IV")
    if len(secret_bytes) not in {16, 24, 32}:
        raise ValueError("MeterSphere Secret Key 必须是 16、24 或 32 字节 AES key")

    timestamp = int(now_ms if now_ms is not None else time.time() * 1000)
    plaintext = f"{access}|{nonce or uuid.uuid4()}|{timestamp}".encode("utf-8")
    padder = PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(
        algorithms.AES(secret_bytes),
        modes.CBC(access_bytes),
    ).encryptor()
    encrypted = encryptor.update(padded) + encryptor.finalize()
    return {
        "accessKey": access,
        "signature": base64.b64encode(encrypted).decode("ascii"),
    }


def _result_data(result: Dict[str, Any]) -> Any:
    return result.get("data", result) if isinstance(result, dict) else result


def _result_items(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    value = _result_data(result)
    for _index in range(4):
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if not isinstance(value, dict):
            return []
        nested = next((
            value.get(key)
            for key in ("list", "records", "items", "content", "data")
            if isinstance(value.get(key), (list, dict))
        ), None)
        if nested is None:
            return []
        value = nested
    return []


def _version_from_result(result: Dict[str, Any]) -> str:
    value = _result_data(result)
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(
            value.get("version")
            or value.get("currentVersion")
            or value.get("current_version")
            or ""
        ).strip()
    return ""


def _normalize_http_path(value: Any) -> str:
    path = str(value or "").strip()
    if not path:
        return ""
    path = "/" + path.lstrip("/")
    path = re.sub(r"/{2,}", "/", path)
    return path if path == "/" else path.rstrip("/")


def _stable_hash(value: str, size: int = 12) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:size]


def _content_hash(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _provider_semantic_projection(
    desired: Any,
    remote: Any,
    *,
    ignored_keys: frozenset[str] = frozenset(),
) -> Any:
    """Project a provider-enriched response onto the fields owned by this adapter."""

    if isinstance(desired, dict):
        if remote is None and not desired:
            return {}
        if not isinstance(remote, dict):
            return remote
        return {
            key: _provider_semantic_projection(
                value,
                remote.get(key),
                ignored_keys=ignored_keys,
            )
            for key, value in desired.items()
            if key not in ignored_keys
        }
    if isinstance(desired, list):
        if remote is None and not desired:
            return []
        if not isinstance(remote, list) or len(remote) != len(desired):
            return remote
        return [
            _provider_semantic_projection(
                desired_item,
                remote_item,
                ignored_keys=ignored_keys,
            )
            for desired_item, remote_item in zip(desired, remote)
        ]
    return remote


def _provider_semantically_equal(
    desired: Any,
    remote: Any,
    *,
    ignored_keys: frozenset[str] = frozenset(),
) -> bool:
    desired_projection = _provider_semantic_projection(
        desired,
        desired,
        ignored_keys=ignored_keys,
    )
    remote_projection = _provider_semantic_projection(
        desired,
        remote,
        ignored_keys=ignored_keys,
    )
    return _content_hash(desired_projection) == _content_hash(remote_projection)


def _remote_id(result: Dict[str, Any]) -> str:
    data = _result_data(result)
    if isinstance(data, (str, int)) and str(data).strip():
        return str(data).strip()
    for source in (result, data):
        if isinstance(source, dict) and str(source.get("id") or "").strip():
            return str(source.get("id")).strip()
    return ""


def _scalar_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if value is None:
        return ""
    return str(value)


def _json_path_child(parent: str, key: str) -> str:
    name = str(key or "")
    if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        return f"{parent}.{name}"
    escaped = name.replace("\\", "\\\\").replace("'", "\\'")
    return f"{parent}['{escaped}']"


def _required_json_paths(schema: Any, parent: str = "$") -> List[str]:
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    required = {
        str(name or "").strip()
        for name in (schema.get("required") or [])
        if str(name or "").strip()
    }
    paths: List[str] = []
    for name in sorted(required):
        path = _json_path_child(parent, name)
        paths.append(path)
        child = properties.get(name)
        paths.extend(_required_json_paths(child, path))
    return paths


class MeterSphereV365Adapter:
    """Remote contract boundary for the verified MeterSphere 3.6.5 build."""

    def __init__(
        self,
        config: Dict[str, Any],
        request_json: Callable[..., Dict[str, Any]],
        *,
        bindings_dir: str = "",
        request_supports_config: bool = False,
    ) -> None:
        self.config = dict(config or {})
        self.request_json = request_json
        self.bindings_dir = str(bindings_dir or "").strip()
        self.request_supports_config = bool(request_supports_config)

    def _request(
        self,
        method: str,
        path: str,
        payload: Dict[str, Any] | None = None,
        timeout: float = 30,
    ) -> Dict[str, Any]:
        if self.request_supports_config:
            return self.request_json(method, path, payload, timeout, config=self.config)
        return self.request_json(method, path, payload, timeout)

    @staticmethod
    def _enabled(item: Dict[str, Any]) -> bool:
        for key in ("enable", "enabled", "isEnable", "isEnabled"):
            value = item.get(key)
            if value is False or str(value).strip().lower() in {"0", "false", "disabled"}:
                return False
        return str(item.get("status") or "").strip().lower() not in {"disabled", "disable", "inactive"}

    @classmethod
    def _normalize_project_options(cls, result: Dict[str, Any]) -> List[Dict[str, Any]]:
        projects = []
        for item in _result_items(result):
            project_id = str(item.get("id") or item.get("projectId") or "").strip()
            name = str(item.get("name") or item.get("label") or "").strip()
            if project_id and name and cls._enabled(item):
                projects.append({"id": project_id, "name": name, "enabled": True})
        return projects

    @classmethod
    def _normalize_environment_options(
        cls,
        result: Dict[str, Any],
        project_id: str,
    ) -> List[Dict[str, Any]]:
        environments = []
        for item in _result_items(result):
            item_project_id = str(item.get("projectId") or item.get("project_id") or project_id).strip()
            environment_id = str(item.get("id") or item.get("environmentId") or "").strip()
            name = str(item.get("name") or item.get("label") or "").strip()
            if environment_id and name and item_project_id == project_id and cls._enabled(item):
                environments.append({
                    "id": environment_id,
                    "name": name,
                    "project_id": project_id,
                    "enabled": True,
                })
        return environments

    def list_projects(self) -> List[Dict[str, Any]]:
        workspace_id = str(self.config.get("workspace_id") or "").strip()
        if not workspace_id:
            raise MeterSphereV365ContractError("organization_missing", "MeterSphere organization ID 未配置")
        result = self._request("GET", f"/project/list/options/{workspace_id}", timeout=20)
        return self._normalize_project_options(result) if result.get("ok") else []

    def list_environments(self, project_id: str) -> List[Dict[str, Any]]:
        selected_project_id = str(project_id or "").strip()
        if not selected_project_id:
            raise MeterSphereV365ContractError("project_missing", "MeterSphere project ID 未配置")
        result = self._request("GET", f"/api/test/env-list/{selected_project_id}", timeout=20)
        return self._normalize_environment_options(result, selected_project_id) if result.get("ok") else []

    def probe(self) -> Dict[str, Any]:
        project_id = str(self.config.get("project_id") or "").strip()
        environment_id = str(self.config.get("environment_id") or "").strip()
        version_result = self._request("GET", "/system/version/current", timeout=20)
        version = _version_from_result(version_result)
        supported = bool(version_result.get("ok") and version in SUPPORTED_VERSIONS)
        empty_capabilities = {
            "can_read_assets": False,
            "can_push": False,
            "can_run": False,
            "can_query_run": False,
            "can_pull_report": False,
            "ready": False,
            "missing": ["受支持的 MeterSphere 版本"],
        }
        if not supported:
            return {
                "ok": False,
                "adapter": ADAPTER_ID,
                "version": version,
                "project": {},
                "environments": [],
                "selected_environment": {},
                "capabilities": empty_capabilities,
            }

        project_result = self._request("GET", f"/project/get/{project_id}", timeout=20)
        project_data = _result_data(project_result)
        project_data = project_data if isinstance(project_data, dict) else {}
        project_valid = bool(
            project_result.get("ok")
            and str(project_data.get("id") or "") == project_id
            and "apiTest" in (project_data.get("moduleSetting") or [])
        )
        project = {
            "id": project_id,
            "name": str(project_data.get("name") or "").strip(),
        } if project_valid else {}

        environment_result = self._request(
            "GET",
            f"/api/test/env-list/{project_id}",
            timeout=20,
        )
        environments = self._normalize_environment_options(environment_result, project_id) if environment_result.get("ok") else []
        selected_environment = next((
            item for item in environments if item["id"] == environment_id
        ), {})

        page_payload = {
            "current": 1,
            "pageSize": 5,
            "projectId": project_id,
            "protocols": ["HTTP"],
            "deleted": False,
        }
        definition_result = self._request("POST", "/api/definition/page", page_payload, 20)
        case_result = self._request("POST", "/api/case/page", page_payload, 20)
        module_result = self._request(
            "POST",
            "/api/scenario/module/tree",
            {"projectId": project_id},
            20,
        )
        report_result = self._request(
            "POST",
            "/api/report/scenario/page",
            {"current": 1, "pageSize": 5, "projectId": project_id},
            20,
        )
        definitions_ready = bool(definition_result.get("ok"))
        cases_ready = bool(case_result.get("ok"))
        modules_ready = bool(module_result.get("ok"))
        reports_ready = bool(report_result.get("ok"))
        environment_ready = bool(environment_result.get("ok") and selected_environment)
        can_read_assets = bool(project_valid and environment_ready and definitions_ready and cases_ready)
        can_push = can_read_assets
        can_run = bool(can_push and modules_ready and reports_ready)
        missing = []
        if not project_valid:
            missing.append("有效业务")
        if not environment_ready:
            missing.append("有效环境")
        if not definitions_ready:
            missing.append("接口定义查询合同")
        if not cases_ready:
            missing.append("接口用例查询合同")
        if not modules_ready:
            missing.append("接口场景模块合同")
        if not reports_ready:
            missing.append("接口场景报告合同")
        capabilities = {
            "can_read_assets": can_read_assets,
            "can_push": can_push,
            "can_run": can_run,
            "can_query_run": bool(can_run and reports_ready),
            "can_pull_report": bool(can_run and reports_ready),
            "ready": bool(can_run and not missing),
            "missing": missing,
        }
        return {
            "ok": capabilities["ready"],
            "adapter": ADAPTER_ID,
            "version": version,
            "project": project,
            "environments": environments,
            "selected_environment": selected_environment,
            "capabilities": capabilities,
        }

    def _binding_path(self, plan_id: str) -> str:
        if not self.bindings_dir:
            raise MeterSphereV365ContractError(
                "binding_storage_missing",
                "MeterSphere binding 存储目录未配置",
            )
        return safe_join(
            self.bindings_dir,
            f"{clean_id(plan_id, 'api_plan')}.json",
        )

    def load_binding(self, plan_id: str) -> Dict[str, Any]:
        value = read_json_file(self._binding_path(plan_id), default={}) or {}
        return value if isinstance(value, dict) else {}

    def _save_binding(self, binding: Dict[str, Any]) -> None:
        binding["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        write_json_file(self._binding_path(str(binding.get("plan_id") or "")), binding)

    def _definitions(self) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []
        page_size = 500
        for current in range(1, 101):
            result = self._request(
                "POST",
                "/api/definition/page",
                {
                    "current": current,
                    "pageSize": page_size,
                    "projectId": str(self.config.get("project_id") or ""),
                    "protocols": ["HTTP"],
                    "deleted": False,
                },
                60,
            )
            if not result.get("ok"):
                raise MeterSphereV365ContractError(
                    "definition_query_failed",
                    str(result.get("error") or "MeterSphere 接口定义查询失败"),
                )
            page = _result_items(result)
            rows.extend(page)
            data = _result_data(result)
            total_value = (
                result.get("total")
                if "total" in result
                else data.get("total") if isinstance(data, dict) else None
            )
            try:
                total = max(0, int(total_value)) if total_value is not None else 0
            except (TypeError, ValueError):
                total = 0
            if (total and len(rows) >= total) or len(page) < page_size:
                break
        return rows

    @staticmethod
    def _definition_matches(
        definitions: List[Dict[str, Any]],
        method: str,
        path: str,
    ) -> List[Dict[str, Any]]:
        expected_method = str(method or "").strip().upper()
        expected_path = _normalize_http_path(path)
        return [
            item for item in definitions
            if str(item.get("protocol") or "HTTP").strip().upper() == "HTTP"
            and str(item.get("method") or "").strip().upper() == expected_method
            and _normalize_http_path(item.get("path")) == expected_path
        ]

    @staticmethod
    def _case_marker(plan_id: str, case_id: str) -> str:
        return f"[MTP:{_stable_hash(f'{plan_id}:{case_id}')}]"

    @staticmethod
    def _case_name(marker: str, case: Dict[str, Any]) -> str:
        label = str(case.get("name") or case.get("case_id") or "API case").strip()
        return f"{marker} {label}"[:255]

    @staticmethod
    def _parameter_items(values: Any, *, rest: bool = False) -> List[Dict[str, Any]]:
        if not isinstance(values, dict):
            raise MeterSphereV365ContractError(
                "unsupported_request_parameters",
                "API case 参数必须是 key/value 对象",
            )
        items = []
        for key, value in values.items():
            item = {
                "key": str(key),
                "value": _scalar_text(value),
                "enable": True,
                "description": "",
            }
            if rest:
                item.update({"paramType": "string", "required": True, "encode": False})
            items.append(item)
        return items

    @staticmethod
    def _body(value: Any) -> Dict[str, Any]:
        common = {
            "noneBody": {},
            "formDataBody": {"formValues": []},
            "wwwFormBody": {"formValues": []},
            "jsonBody": {"enableJsonSchema": False, "jsonValue": "", "jsonSchema": None},
            "xmlBody": {"value": ""},
            "rawBody": {"value": ""},
            "binaryBody": {"description": "", "file": None},
        }
        if value in (None, {}, []):
            return {"bodyType": "NONE", **common}
        common["jsonBody"]["jsonValue"] = json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return {"bodyType": "JSON", **common}

    @staticmethod
    def _status_assertion(assertion: Dict[str, Any]) -> Dict[str, Any]:
        if str(assertion.get("operator") or "") != "in":
            raise MeterSphereV365ContractError(
                "unsupported_status_assertion",
                "MeterSphere 3.6.5 adapter 仅支持 status/in 断言",
            )
        values = []
        for value in assertion.get("expected") or []:
            text = str(value or "").strip().upper()
            if not re.fullmatch(r"[1-5]\d\d", text):
                raise MeterSphereV365ContractError(
                    "unsupported_status_assertion",
                    f"不支持的 HTTP 状态断言：{text or value}",
                )
            values.append(text)
        values = sorted(set(values))
        if not values:
            raise MeterSphereV365ContractError(
                "unsupported_status_assertion",
                "HTTP 状态断言没有 expected 值",
            )
        if len(values) == 1:
            condition = "EQUALS"
            expected = values[0]
        else:
            condition = "REGEX"
            expected = f"^(?:{'|'.join(values)})$"
        return {
            "assertionType": "RESPONSE_CODE",
            "enable": True,
            "name": "响应状态码",
            "condition": condition,
            "expectedValue": expected,
        }

    @staticmethod
    def _schema_assertion(endpoint: Dict[str, Any]) -> tuple[Dict[str, Any], str]:
        schema = endpoint.get("response_schema")
        if not isinstance(schema, dict) or not schema:
            raise MeterSphereV365ContractError(
                "response_schema_missing",
                "schema 断言引用的 OpenAPI response schema 不存在",
            )
        paths = _required_json_paths(schema)
        coverage = "required_fields" if paths else "root_only"
        if not paths:
            paths = ["$"]
        items = [{
            "enable": True,
            "expression": path,
            "condition": "NOT_EMPTY",
            "expectedValue": "",
        } for path in paths[:100]]
        return ({
            "assertionType": "RESPONSE_BODY",
            "enable": True,
            "name": "响应结构",
            "assertionBodyType": "JSON_PATH",
            "jsonPathAssertion": {"assertions": items},
            "xpathAssertion": {"assertions": []},
            "documentAssertion": None,
            "regexAssertion": {"assertions": []},
        }, coverage)

    def _materialize_case(
        self,
        plan: Dict[str, Any],
        case: Dict[str, Any],
        endpoint: Dict[str, Any],
        definition: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        if case.get("variables"):
            raise MeterSphereV365ContractError(
                "unsupported_variables",
                "MeterSphere 3.6.5 adapter 尚不能无损映射跨用例变量",
            )
        if str(case.get("type") or "").strip().lower() == "auth":
            raise MeterSphereV365ContractError(
                "unsupported_auth_suppression",
                "无法证明所选环境的鉴权可被当前用例可靠移除",
            )
        request = case.get("request")
        if not isinstance(request, dict):
            raise MeterSphereV365ContractError("request_missing", "API case request 不完整")
        method = str(request.get("method") or "").strip().upper()
        path = _normalize_http_path(request.get("path"))
        if (
            method != str(definition.get("method") or "").strip().upper()
            or path != _normalize_http_path(definition.get("path"))
        ):
            raise MeterSphereV365ContractError(
                "definition_mismatch",
                "API case method/path 与 MeterSphere definition 不一致",
            )
        headers = request.get("headers") or {}
        if not isinstance(headers, dict):
            raise MeterSphereV365ContractError(
                "unsupported_request_headers",
                "API case headers 必须是 key/value 对象",
            )
        if any(
            any(
                part in re.sub(r"[^a-z0-9]+", "", str(key).lower())
                for part in _SENSITIVE_HEADER_KEY_PARTS
            )
            for key in headers
        ):
            raise MeterSphereV365ContractError(
                "sensitive_header_in_contract",
                "请求合同不能携带明文鉴权 header",
            )
        assertion_items = []
        schema_coverage = "none"
        for assertion in case.get("assertions") or []:
            if not isinstance(assertion, dict):
                raise MeterSphereV365ContractError(
                    "unsupported_assertion",
                    "API case assertion 必须是结构化对象",
                )
            assertion_type = str(assertion.get("type") or "").strip().lower()
            if assertion_type == "status":
                assertion_items.append(self._status_assertion(assertion))
            elif assertion_type == "schema" and assertion.get("schema_ref") == "response:2xx":
                mapped, schema_coverage = self._schema_assertion(endpoint)
                assertion_items.append(mapped)
            else:
                raise MeterSphereV365ContractError(
                    "unsupported_assertion",
                    f"MeterSphere 3.6.5 adapter 不支持断言：{assertion_type or 'unknown'}",
                )
        if not any(item.get("assertionType") == "RESPONSE_CODE" for item in assertion_items):
            raise MeterSphereV365ContractError(
                "status_assertion_missing",
                "API case 缺少可执行状态码断言",
            )
        marker = self._case_marker(str(plan.get("plan_id") or ""), str(case.get("case_id") or ""))
        ms_request = {
            "polymorphicName": "MsHTTPElement",
            "name": self._case_name(marker, case),
            "enable": True,
            "children": [{
                "polymorphicName": "MsCommonElement",
                "enable": True,
                "children": [],
                "preProcessorConfig": {"enableGlobal": True, "processors": []},
                "postProcessorConfig": {"enableGlobal": True, "processors": []},
                "assertionConfig": {"enableGlobal": True, "assertions": assertion_items},
            }],
            "customizeRequest": False,
            "customizeRequestEnvEnable": False,
            "path": str(request.get("path") or "").strip(),
            "method": method,
            "body": self._body(request.get("body")),
            "headers": self._parameter_items(headers),
            "rest": self._parameter_items(request.get("path_params") or {}, rest=True),
            "query": self._parameter_items(request.get("query") or {}),
            "otherConfig": {
                "connectTimeout": 60000,
                "responseTimeout": 60000,
                "certificateAlias": "",
                "followRedirects": True,
                "autoRedirects": False,
            },
            "authConfig": {
                "authType": "NONE",
                "basicAuth": {"userName": "", "password": ""},
                "digestAuth": {"userName": "", "password": ""},
            },
        }
        payload = {
            "name": self._case_name(marker, case),
            "projectId": str(self.config.get("project_id") or ""),
            "priority": str(case.get("priority") or "P1")[:50],
            "status": "PROCESSING",
            "apiDefinitionId": str(definition.get("id") or ""),
            "tags": ["midscene-managed", f"mtp:{marker[5:-1]}"],
            "environmentId": str(self.config.get("environment_id") or ""),
            "request": ms_request,
            "aiCreate": bool(str(case.get("source") or "").lower() == "ai"),
        }
        evidence = {
            "marker": marker,
            "schema_coverage": schema_coverage,
            "provider_identity": {
                "endpoint_id": str(case.get("endpoint_id") or endpoint.get("endpoint_id") or ""),
                "endpoint_key": str(endpoint.get("endpoint_key") or ""),
                "asset_revision_id": str(endpoint.get("asset_revision_id") or ""),
                "method": method,
                "path": path,
            },
        }
        return payload, evidence

    @staticmethod
    def _remote_case_view(remote: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: copy.deepcopy(remote.get(key))
            for key in (
                "name", "projectId", "priority", "status", "apiDefinitionId",
                "tags", "environmentId", "request", "aiCreate",
            )
        }

    def _find_remote_case(self, marker: str) -> List[Dict[str, Any]]:
        result = self._request(
            "POST",
            "/api/case/page",
            {
                "current": 1,
                "pageSize": 100,
                "projectId": str(self.config.get("project_id") or ""),
                "protocols": ["HTTP"],
                "deleted": False,
                "keyword": marker,
            },
            30,
        )
        if not result.get("ok"):
            raise MeterSphereV365ContractError(
                "case_query_failed",
                str(result.get("error") or "MeterSphere 接口用例查询失败"),
            )
        return [
            item for item in _result_items(result)
            if str(item.get("name") or "").startswith(marker)
        ]

    def _get_remote_case(self, remote_case_id: str) -> Dict[str, Any]:
        result = self._request(
            "GET",
            f"/api/case/get-detail/{remote_case_id}",
            timeout=30,
        )
        if not result.get("ok"):
            return {}
        value = _result_data(result)
        return value if isinstance(value, dict) else {}

    def _owned_remote_case(
        self,
        remote: Dict[str, Any],
        marker: str,
        definition_id: str,
    ) -> bool:
        return bool(
            remote
            and str(remote.get("projectId") or "") == str(self.config.get("project_id") or "")
            and str(remote.get("apiDefinitionId") or "") == definition_id
            and str(remote.get("name") or "").startswith(marker)
        )

    def upsert_plan_cases(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        probe = self.probe()
        if not (probe.get("capabilities") or {}).get("can_push"):
            return {
                "ok": False,
                "adapter": ADAPTER_ID,
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "recovered": 0,
                "remote_case_ids": {},
                "blocked": [{
                    "case_id": "",
                    "reason": "adapter_not_ready",
                    "message": "、".join((probe.get("capabilities") or {}).get("missing") or []),
                }],
            }
        plan_id = str(plan.get("plan_id") or "").strip()
        endpoints = {
            str(item.get("endpoint_id") or ""): item
            for item in (plan.get("endpoints") or [])
            if isinstance(item, dict) and str(item.get("endpoint_id") or "")
        }
        definitions = self._definitions()
        binding = self.load_binding(plan_id)
        if not binding:
            binding = {
                "adapter": ADAPTER_ID,
                "version": probe.get("version") or "",
                "plan_id": plan_id,
                "project_id": str(self.config.get("project_id") or ""),
                "environment_id": str(self.config.get("environment_id") or ""),
                "cases": {},
                "scenario": {},
            }
        bound_cases = binding.get("cases") if isinstance(binding.get("cases"), dict) else {}
        binding["cases"] = bound_cases
        result = {
            "ok": True,
            "adapter": ADAPTER_ID,
            "version": probe.get("version") or "",
            "created": 0,
            "updated": 0,
            "unchanged": 0,
            "recovered": 0,
            "remote_case_ids": {},
            "blocked": [],
        }
        cases = [
            case for case in (plan.get("cases") or [])
            if isinstance(case, dict)
            and (case.get("readiness") or {}).get("state") == "executable"
        ]
        for case in cases:
            case_id = str(case.get("case_id") or "").strip()
            try:
                endpoint = endpoints.get(str(case.get("endpoint_id") or ""))
                if not endpoint:
                    raise MeterSphereV365ContractError(
                        "endpoint_missing",
                        "API case 绑定的 endpoint 不存在",
                    )
                request = case.get("request") if isinstance(case.get("request"), dict) else {}
                matches = self._definition_matches(
                    definitions,
                    request.get("method"),
                    request.get("path"),
                )
                if not matches:
                    raise MeterSphereV365ContractError(
                        "definition_missing",
                        "MeterSphere 中不存在 method/path 完全一致的接口定义",
                    )
                if len(matches) > 1:
                    raise MeterSphereV365ContractError(
                        "ambiguous_definition",
                        "MeterSphere 中存在多个 method/path 相同的接口定义",
                    )
                definition = matches[0]
                desired, evidence = self._materialize_case(plan, case, endpoint, definition)
                desired_hash = _content_hash(desired)
                marker = evidence["marker"]
                current_binding = bound_cases.get(case_id) if isinstance(bound_cases.get(case_id), dict) else {}
                remote_case_id = str(current_binding.get("remote_case_id") or "")
                remote = self._get_remote_case(remote_case_id) if remote_case_id else {}
                recovered = False
                if not remote:
                    candidates = self._find_remote_case(marker)
                    if len(candidates) > 1:
                        raise MeterSphereV365ContractError(
                            "ambiguous_remote_case",
                            "稳定标记匹配到多个 MeterSphere 接口用例",
                        )
                    if candidates:
                        remote_case_id = str(candidates[0].get("id") or "")
                        remote = self._get_remote_case(remote_case_id) or candidates[0]
                        recovered = True
                definition_id = str(definition.get("id") or "")
                if remote and not self._owned_remote_case(remote, marker, definition_id):
                    raise MeterSphereV365ContractError(
                        "ownership_mismatch",
                        "远端用例与当前 binding 的 ownership 不一致",
                    )
                desired_view = self._remote_case_view(desired)
                if not remote:
                    create_result = self._request("POST", "/api/case/add", desired, 60)
                    remote_case_id = _remote_id(create_result)
                    if not create_result.get("ok") or not remote_case_id:
                        raise MeterSphereV365ContractError(
                            "case_create_failed",
                            str(create_result.get("error") or "MeterSphere 创建接口用例未返回真实 ID"),
                        )
                    result["created"] += 1
                elif _provider_semantically_equal(desired_view, self._remote_case_view(remote)):
                    result["unchanged"] += 1
                else:
                    update_payload = {
                        key: copy.deepcopy(desired[key])
                        for key in (
                            "name", "priority", "status", "tags", "environmentId", "request",
                        )
                    }
                    update_payload["id"] = remote_case_id
                    update_result = self._request("POST", "/api/case/update", update_payload, 60)
                    if not update_result.get("ok"):
                        raise MeterSphereV365ContractError(
                            "case_update_failed",
                            str(update_result.get("error") or "MeterSphere 更新接口用例失败"),
                        )
                    result["updated"] += 1
                if recovered:
                    result["recovered"] += 1
                result["remote_case_ids"][case_id] = remote_case_id
                bound_cases[case_id] = {
                    "remote_case_id": remote_case_id,
                    "remote_definition_id": definition_id,
                    "marker": marker,
                    "content_hash": desired_hash,
                    "schema_coverage": evidence["schema_coverage"],
                    "provider_identity": evidence["provider_identity"],
                }
                self._save_binding(binding)
            except MeterSphereV365ContractError as exc:
                result["blocked"].append({
                    "case_id": case_id,
                    "reason": exc.reason,
                    "message": str(exc),
                })
        result["ok"] = not result["blocked"] and bool(result["remote_case_ids"])
        return result

    @staticmethod
    def _flatten_tree(items: Any) -> List[Dict[str, Any]]:
        flattened: List[Dict[str, Any]] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            flattened.append(item)
            flattened.extend(MeterSphereV365Adapter._flatten_tree(item.get("children")))
        return flattened

    def _scenario_module_id(self) -> str:
        name = "Midscene 自动化"
        result = self._request(
            "POST",
            "/api/scenario/module/tree",
            {"projectId": str(self.config.get("project_id") or "")},
            30,
        )
        if not result.get("ok"):
            raise MeterSphereV365ContractError(
                "scenario_module_query_failed",
                str(result.get("error") or "MeterSphere 场景模块查询失败"),
            )
        tree = _result_data(result)
        candidates = [
            item for item in self._flatten_tree(tree)
            if str(item.get("name") or "").strip() == name
            and str(item.get("projectId") or self.config.get("project_id") or "")
            == str(self.config.get("project_id") or "")
        ]
        if len(candidates) > 1:
            raise MeterSphereV365ContractError(
                "ambiguous_scenario_module",
                "MeterSphere 中存在多个 Midscene 自动化场景模块",
            )
        if candidates:
            return str(candidates[0].get("id") or "").strip()
        created = self._request(
            "POST",
            "/api/scenario/module/add",
            {
                "projectId": str(self.config.get("project_id") or ""),
                "name": name,
                "parentId": "NONE",
            },
            30,
        )
        module_id = _remote_id(created)
        if not created.get("ok") or not module_id:
            raise MeterSphereV365ContractError(
                "scenario_module_create_failed",
                str(created.get("error") or "MeterSphere 创建场景模块未返回真实 ID"),
            )
        return module_id

    @staticmethod
    def _scenario_marker(plan_id: str) -> str:
        return f"[MTP:{_stable_hash(plan_id)}]"

    @staticmethod
    def _scenario_view(remote: Dict[str, Any]) -> Dict[str, Any]:
        return {
            key: copy.deepcopy(remote.get(key))
            for key in (
                "name", "priority", "status", "projectId", "moduleId", "description",
                "tags", "grouped", "environmentId", "scenarioConfig", "steps", "stepDetails",
            )
        }

    @staticmethod
    def _scenario_config() -> Dict[str, Any]:
        return {
            "variable": {"commonVariables": [], "csvVariables": []},
            "preProcessorConfig": {"enableGlobal": True, "processors": []},
            "postProcessorConfig": {"enableGlobal": True, "processors": []},
            "assertionConfig": {"assertions": []},
            "otherConfig": {
                "enableGlobalCookie": True,
                "enableCookieShare": False,
                "enableStepWait": False,
                "failureStrategy": "CONTINUE",
            },
        }

    def _desired_scenario(
        self,
        plan: Dict[str, Any],
        module_id: str,
        binding: Dict[str, Any],
    ) -> tuple[Dict[str, Any], Dict[str, str]]:
        plan_id = str(plan.get("plan_id") or "").strip()
        marker = self._scenario_marker(plan_id)
        bound_cases = binding.get("cases") if isinstance(binding.get("cases"), dict) else {}
        executable = [
            item for item in (plan.get("cases") or [])
            if isinstance(item, dict)
            and (item.get("readiness") or {}).get("state") == "executable"
        ]
        steps = []
        step_case_ids: Dict[str, str] = {}
        for case in executable:
            case_id = str(case.get("case_id") or "").strip()
            case_binding = bound_cases.get(case_id) if isinstance(bound_cases.get(case_id), dict) else {}
            remote_case_id = str(case_binding.get("remote_case_id") or "").strip()
            if not remote_case_id:
                raise MeterSphereV365ContractError(
                    "remote_case_binding_missing",
                    f"API case {case_id} 尚未写入 MeterSphere",
                )
            request = case.get("request") if isinstance(case.get("request"), dict) else {}
            step_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"midscene:{plan_id}:{case_id}"))
            step_case_ids[step_id] = case_id
            steps.append({
                "id": step_id,
                "enable": True,
                "resourceId": remote_case_id,
                "originProjectId": str(self.config.get("project_id") or ""),
                "stepType": "API_CASE",
                "refType": "REF",
                "config": {
                    "id": "",
                    "name": "",
                    "enable": True,
                    "protocol": "HTTP",
                    "method": str(request.get("method") or "").strip().upper(),
                },
                "csvIds": [],
                "projectId": str(self.config.get("project_id") or ""),
                "name": str(case.get("name") or case_id)[:255],
                "children": [],
                "uniqueId": step_id,
            })
        if not steps:
            raise MeterSphereV365ContractError(
                "remote_case_binding_missing",
                "没有可组成 MeterSphere 场景的远端接口用例",
            )
        payload = {
            "name": f"{marker} {str(plan.get('name') or plan_id)}"[:255],
            "priority": "P0",
            "status": "UNDERWAY",
            "projectId": str(self.config.get("project_id") or ""),
            "moduleId": module_id,
            "description": "Managed by midscene-task-platform",
            "tags": ["midscene-managed", f"mtp:{marker[5:-1]}"],
            "grouped": False,
            "environmentId": str(self.config.get("environment_id") or ""),
            "scenarioConfig": self._scenario_config(),
            "steps": steps,
            "stepDetails": {},
            "stepFileParam": {},
        }
        return payload, step_case_ids

    def _find_remote_scenario(self, marker: str) -> List[Dict[str, Any]]:
        result = self._request(
            "POST",
            "/api/scenario/page",
            {
                "current": 1,
                "pageSize": 100,
                "projectId": str(self.config.get("project_id") or ""),
                "deleted": False,
                "keyword": marker,
            },
            30,
        )
        if not result.get("ok"):
            raise MeterSphereV365ContractError(
                "scenario_query_failed",
                str(result.get("error") or "MeterSphere 场景查询失败"),
            )
        return [
            item for item in _result_items(result)
            if str(item.get("name") or "").startswith(marker)
        ]

    def _get_remote_scenario(self, scenario_id: str) -> Dict[str, Any]:
        result = self._request("GET", f"/api/scenario/get/{scenario_id}", timeout=30)
        if not result.get("ok"):
            return {}
        value = _result_data(result)
        return value if isinstance(value, dict) else {}

    def upsert_plan_scenario(self, plan: Dict[str, Any]) -> Dict[str, Any]:
        probe = self.probe()
        if not (probe.get("capabilities") or {}).get("can_run"):
            return {
                "ok": False,
                "adapter": ADAPTER_ID,
                "scenario_id": "",
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "recovered": 0,
                "error": "、".join((probe.get("capabilities") or {}).get("missing") or []),
            }
        plan_id = str(plan.get("plan_id") or "").strip()
        binding = self.load_binding(plan_id)
        try:
            module_id = self._scenario_module_id()
            desired, step_case_ids = self._desired_scenario(plan, module_id, binding)
            marker = self._scenario_marker(plan_id)
            scenario_binding = binding.get("scenario") if isinstance(binding.get("scenario"), dict) else {}
            scenario_id = str(scenario_binding.get("remote_scenario_id") or "").strip()
            remote = self._get_remote_scenario(scenario_id) if scenario_id else {}
            recovered = False
            if not remote:
                candidates = self._find_remote_scenario(marker)
                if len(candidates) > 1:
                    raise MeterSphereV365ContractError(
                        "ambiguous_remote_scenario",
                        "稳定标记匹配到多个 MeterSphere 场景",
                    )
                if candidates:
                    scenario_id = str(candidates[0].get("id") or "")
                    remote = self._get_remote_scenario(scenario_id) or candidates[0]
                    recovered = True
            if remote and (
                str(remote.get("projectId") or "") != str(self.config.get("project_id") or "")
                or not str(remote.get("name") or "").startswith(marker)
            ):
                raise MeterSphereV365ContractError(
                    "scenario_ownership_mismatch",
                    "远端场景与当前 binding 的 ownership 不一致",
                )
            created = updated = unchanged = 0
            if not remote:
                create_result = self._request("POST", "/api/scenario/add", desired, 60)
                scenario_id = _remote_id(create_result)
                if not create_result.get("ok") or not scenario_id:
                    raise MeterSphereV365ContractError(
                        "scenario_create_failed",
                        str(create_result.get("error") or "MeterSphere 创建场景未返回真实 ID"),
                    )
                created = 1
            elif _provider_semantically_equal(
                self._scenario_view(desired),
                self._scenario_view(remote),
                ignored_keys=frozenset({"uniqueId"}),
            ):
                unchanged = 1
            else:
                update_payload = copy.deepcopy(desired)
                update_payload["id"] = scenario_id
                update_result = self._request("POST", "/api/scenario/update", update_payload, 60)
                if not update_result.get("ok"):
                    raise MeterSphereV365ContractError(
                        "scenario_update_failed",
                        str(update_result.get("error") or "MeterSphere 更新场景失败"),
                    )
                updated = 1
            binding["scenario"] = {
                "remote_scenario_id": scenario_id,
                "module_id": module_id,
                "marker": marker,
                "content_hash": _content_hash(desired),
                "step_case_ids": step_case_ids,
                "last_run_id": str(scenario_binding.get("last_run_id") or ""),
            }
            self._save_binding(binding)
            return {
                "ok": True,
                "adapter": ADAPTER_ID,
                "scenario_id": scenario_id,
                "created": created,
                "updated": updated,
                "unchanged": unchanged,
                "recovered": 1 if recovered else 0,
            }
        except MeterSphereV365ContractError as exc:
            return {
                "ok": False,
                "adapter": ADAPTER_ID,
                "scenario_id": "",
                "created": 0,
                "updated": 0,
                "unchanged": 0,
                "recovered": 0,
                "reason": exc.reason,
                "error": str(exc),
            }

    def trigger_plan(self, plan_id: str) -> Dict[str, Any]:
        binding = self.load_binding(plan_id)
        scenario_binding = binding.get("scenario") if isinstance(binding.get("scenario"), dict) else {}
        scenario_id = str(scenario_binding.get("remote_scenario_id") or "").strip()
        marker = str(scenario_binding.get("marker") or "").strip()
        remote = self._get_remote_scenario(scenario_id) if scenario_id else {}
        if not scenario_id or not remote or not str(remote.get("name") or "").startswith(marker):
            return {
                "ok": False,
                "adapter": ADAPTER_ID,
                "run_id": "",
                "scenario_id": scenario_id,
                "error": "MeterSphere 场景 binding 不存在或 ownership 校验失败",
            }
        step_case_ids = (
            scenario_binding.get("step_case_ids")
            if isinstance(scenario_binding.get("step_case_ids"), dict)
            else {}
        )
        steps = copy.deepcopy(remote.get("steps") or [])
        remote_step_ids = {
            str(step.get("id") or "").strip()
            for step in self._flatten_tree(steps)
            if str(step.get("stepType") or "") == "API_CASE"
        }
        if not remote_step_ids or remote_step_ids != set(step_case_ids):
            return {
                "ok": False,
                "adapter": ADAPTER_ID,
                "run_id": "",
                "scenario_id": scenario_id,
                "error": "MeterSphere 场景步骤与本地 binding 不一致",
            }
        for step in self._flatten_tree(steps):
            step_id = str(step.get("id") or "").strip()
            if not step_id:
                return {
                    "ok": False,
                    "adapter": ADAPTER_ID,
                    "run_id": "",
                    "scenario_id": scenario_id,
                    "error": "MeterSphere 场景包含无稳定 ID 的步骤",
                }
            step["uniqueId"] = step_id
            step["children"] = step.get("children") or []
            step["csvIds"] = step.get("csvIds") or []
        requested_report_id = str(uuid.uuid4())
        run_payload = {
            "id": scenario_id,
            "reportId": requested_report_id,
            "projectId": str(self.config.get("project_id") or ""),
            "environmentId": str(self.config.get("environment_id") or ""),
            "grouped": bool(remote.get("grouped")),
            "scenarioConfig": copy.deepcopy(
                remote.get("scenarioConfig") or self._scenario_config()
            ),
            "steps": steps,
            "stepDetails": copy.deepcopy(remote.get("stepDetails") or {}),
            "stepFileParam": {},
            "fileParam": {"uploadFileIds": [], "linkFileIds": []},
        }
        result = self._request(
            "POST",
            "/api/scenario/run",
            run_payload,
            timeout=60,
        )
        data = _result_data(result)
        data = data if isinstance(data, dict) else {}
        task_item = data.get("taskItem") if isinstance(data.get("taskItem"), dict) else {}
        run_id = str(task_item.get("reportId") or "").strip()
        if not result.get("ok") or not run_id:
            return {
                "ok": False,
                "adapter": ADAPTER_ID,
                "run_id": "",
                "scenario_id": scenario_id,
                "error": str(result.get("error") or "MeterSphere 场景执行未返回真实 reportId"),
            }
        scenario_binding["last_run_id"] = run_id
        binding["scenario"] = scenario_binding
        self._save_binding(binding)
        return {
            "ok": True,
            "adapter": ADAPTER_ID,
            "run_id": run_id,
            "scenario_id": scenario_id,
            "status": "running",
        }

    def _report_result(self, run_id: str) -> Dict[str, Any]:
        result = self._request("GET", f"/api/report/scenario/get/{run_id}", timeout=60)
        if not result.get("ok"):
            return result
        value = _result_data(result)
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _provider_terminal_state_missing(report: Dict[str, Any]) -> bool:
        exec_status = str(report.get("execStatus") or "").strip().upper()
        if exec_status in {"COMPLETED", "STOPPED"}:
            return False
        try:
            start_time = int(report.get("startTime") or 0)
        except (TypeError, ValueError):
            return False
        if start_time <= 0 or int(time.time() * 1000) - start_time < PROVIDER_TERMINAL_GRACE_MS:
            return False
        request_steps = [
            item
            for item in MeterSphereV365Adapter._flatten_report_steps(report.get("children"))
            if str(item.get("stepType") or "").strip().upper() in _REQUEST_STEP_TYPES
        ]
        return bool(request_steps) and all(
            str(item.get("status") or "").strip().upper()
            in {"SUCCESS", "ERROR", "FAKE_ERROR"}
            for item in request_steps
        )

    @staticmethod
    def _report_state(report: Dict[str, Any]) -> str:
        exec_status = str(report.get("execStatus") or "").strip().upper()
        status = str(report.get("status") or "").strip().upper()
        if MeterSphereV365Adapter._provider_terminal_state_missing(report):
            return "failed"
        if exec_status not in {"COMPLETED", "STOPPED"}:
            return "running"
        return "succeeded" if status == "SUCCESS" else "failed"

    @staticmethod
    def _report_stats(report: Dict[str, Any]) -> Dict[str, int]:
        def number(*keys: str) -> int:
            for key in keys:
                if key in report:
                    try:
                        return max(0, int(report.get(key) or 0))
                    except (TypeError, ValueError):
                        return 0
            return 0

        passed = number("stepSuccessCount", "successCount")
        failed = number("stepErrorCount", "errorCount") + number("stepFakeErrorCount", "fakeErrorCount")
        total = number("requestTotal")
        if not total:
            total = passed + failed + number("stepPendingCount", "pendingCount")
        return {"total": total, "passed": passed, "failed": failed}

    def get_run(self, run_id: str) -> Dict[str, Any]:
        report = self._report_result(run_id)
        if not report or not str(report.get("id") or "").strip():
            return {
                "ok": False,
                "adapter": ADAPTER_ID,
                "run_id": str(run_id or ""),
                "status": "running",
                "error": str(report.get("error") or "MeterSphere 场景报告暂不可用"),
            }
        response = {
            "ok": True,
            "adapter": ADAPTER_ID,
            "run_id": str(report.get("id") or run_id),
            "status": self._report_state(report),
            "stats": self._report_stats(report),
            "remote_status": str(report.get("status") or report.get("execStatus") or ""),
        }
        if self._provider_terminal_state_missing(report):
            response.update({
                "failure_reason": "provider_terminal_state_missing",
                "error": "MeterSphere 已返回全部步骤结果，但主报告超过 5 分钟未回写终态",
            })
        return response

    def _binding_for_run(self, run_id: str) -> Dict[str, Any]:
        if not self.bindings_dir or not os.path.isdir(self.bindings_dir):
            return {}
        for name in os.listdir(self.bindings_dir):
            if not name.endswith(".json"):
                continue
            value = read_json_file(safe_join(self.bindings_dir, name), default={}) or {}
            scenario = value.get("scenario") if isinstance(value, dict) else {}
            if isinstance(scenario, dict) and str(scenario.get("last_run_id") or "") == str(run_id or ""):
                return value
        return {}

    @staticmethod
    def _flatten_report_steps(items: Any) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, dict):
                continue
            result.append(item)
            result.extend(MeterSphereV365Adapter._flatten_report_steps(item.get("children")))
        return result

    def get_report(self, run_id: str) -> Dict[str, Any]:
        report = self._report_result(run_id)
        if not report or not str(report.get("id") or "").strip():
            return {
                "ok": False,
                "id": "",
                "run_id": str(run_id or ""),
                "error": str(report.get("error") or "MeterSphere 场景报告读取失败"),
            }
        binding = self._binding_for_run(run_id)
        scenario_binding = binding.get("scenario") if isinstance(binding.get("scenario"), dict) else {}
        step_case_ids = scenario_binding.get("step_case_ids") if isinstance(scenario_binding.get("step_case_ids"), dict) else {}
        report_steps = [
            item
            for item in self._flatten_report_steps(report.get("children"))
            if str(item.get("stepId") or "") in step_case_ids
            or str(item.get("stepType") or "").strip().upper() in _REQUEST_STEP_TYPES
        ]
        rows = []
        for index, item in enumerate(report_steps, start=1):
            remote_status = str(item.get("status") or "").strip().upper()
            rows.append({
                "case_id": step_case_ids.get(str(item.get("stepId") or "")) or str(item.get("stepId") or f"step-{index}"),
                "name": str(item.get("name") or item.get("requestName") or f"接口步骤 {index}"),
                "status": "passed" if remote_status == "SUCCESS" else "failed",
                "duration_ms": int(item.get("requestTime") or 0),
                "error": "" if remote_status == "SUCCESS" else str(item.get("message") or remote_status),
                "remote_step_id": str(item.get("stepId") or ""),
            })
        response = {
            "ok": True,
            "id": str(report.get("id") or run_id),
            "run_id": str(report.get("id") or run_id),
            "plan_id": str(binding.get("plan_id") or ""),
            "status": self._report_state(report),
            "summary": self._report_stats(report),
            "results": rows,
            "remote": {
                "name": str(report.get("name") or ""),
                "status": str(report.get("status") or ""),
                "exec_status": str(report.get("execStatus") or ""),
                "start_time": report.get("startTime"),
                "end_time": report.get("endTime"),
            },
        }
        if self._provider_terminal_state_missing(report):
            response["failure_reason"] = "provider_terminal_state_missing"
        return response


__all__ = [
    "ADAPTER_ID",
    "SUPPORTED_VERSIONS",
    "MeterSphereV365ContractError",
    "MeterSphereV365DependencyError",
    "MeterSphereV365Adapter",
    "build_v365_auth_headers",
]
