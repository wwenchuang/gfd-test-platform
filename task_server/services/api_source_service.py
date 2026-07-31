"""Server-side API source configuration for the API testing workspace."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
from typing import Any, Dict, List

from task_server.config import LEARNING_DIR, safe_bool, safe_int
from task_server.storage import clean_id, read_json_file, safe_join, unique_millis_id, write_json_file
from task_server.services import api_module_service


API_TESTING_DIR = os.getenv("API_TESTING_DIR", safe_join(LEARNING_DIR, "api-testing"))
ALLOWED_SOURCE_TYPES = {"apifox", "openapi_upload"}
DEFAULT_APIFOX_SOURCE_ID = "api_source_apifox_default"
MIN_SYNC_INTERVAL_MINUTES = 15
MAX_SYNC_INTERVAL_MINUTES = 1440
_SOURCE_LOCK = threading.RLock()
_SENSITIVE_NAME_RE = re.compile(
    r"(token|secret|password|passwd|pwd|authorization|cookie|session|apikey|api_key|accesskey|private)",
    re.IGNORECASE,
)
_BASE_URL_SOURCE_KEYS = (
    "base_urls",
    "baseUrls",
    "baseUrl",
    "base_url",
    "services",
    "serviceList",
    "service_list",
    "servers",
    "serverList",
    "server_list",
    "hosts",
)
_BASE_URL_VALUE_KEYS = (
    "url",
    "value",
    "currentValue",
    "current_value",
    "localValue",
    "local_value",
    "defaultValue",
    "default_value",
    "baseUrl",
    "base_url",
    "server",
    "host",
)
_VARIABLE_SOURCE_KEYS = (
    "variables",
    "variableList",
    "variable_list",
    "environmentVariables",
    "environment_variables",
    "commonVariables",
    "common_variables",
    "values",
    "valueList",
    "value_list",
    "parameters",
    "parameterList",
    "parameter_list",
    "globalParameters",
    "global_parameters",
    "globalParameterList",
    "global_parameter_list",
    "globals",
)
_VARIABLE_NAME_KEYS = (
    "name",
    "key",
    "variableName",
    "variable_name",
    "parameterName",
    "parameter_name",
    "title",
    "id",
)
_VARIABLE_VALUE_KEYS = (
    "value",
    "currentValue",
    "current_value",
    "localValue",
    "local_value",
    "defaultValue",
    "default_value",
    "initialValue",
    "initial_value",
    "example",
    "content",
)


class ApiSourceConfigDriftError(RuntimeError):
    """Raised when a sync is no longer operating on its original source config."""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _api_path(*parts: str) -> str:
    return safe_join(API_TESTING_DIR, *parts)


def _source_path(source_id: str) -> str:
    return _api_path("sources", f"{clean_id(source_id, 'api_source')}.json")


def _index_path() -> str:
    return _api_path("sources", "index.json")


def _apifox_credential_path() -> str:
    return _api_path("credentials", "apifox.json")


def _env_source() -> Dict[str, Any]:
    token = os.getenv("APIFOX_ACCESS_TOKEN", "").strip()
    project_id = os.getenv("APIFOX_PROJECT_ID", "").strip()
    if not token and not project_id:
        return {}
    return {
        "source_id": DEFAULT_APIFOX_SOURCE_ID,
        "source_type": "apifox",
        "name": os.getenv("APIFOX_SOURCE_NAME", "Apifox 接口").strip() or "Apifox 接口",
        "base_url": os.getenv("APIFOX_BASE_URL", "https://api.apifox.com").strip() or "https://api.apifox.com",
        "project_id": project_id,
        "branch_id": os.getenv("APIFOX_BRANCH_ID", "").strip(),
        "environment_id": os.getenv("APIFOX_ENVIRONMENT_ID", "").strip(),
        "access_token": token,
        "credential_mode": "access_token",
        "sync_enabled": safe_bool(os.getenv("APIFOX_SYNC_ENABLED", "1"), True),
        "sync_interval_minutes": _sync_interval(os.getenv("APIFOX_SYNC_INTERVAL_MINUTES", "60")),
        "last_sync_id": "",
        "last_attempt_at": "",
        "last_success_at": "",
        "last_sync_status": "",
        "last_error": "",
        "sync_scope": normalized_sync_scope({}),
        "module_catalog": [],
        "scope_fingerprint": "",
        "created_at": "",
        "updated_at": "",
        "config_source": "environment",
    }


def _sync_interval(value: Any) -> int:
    interval = safe_int(value, 60)
    return max(MIN_SYNC_INTERVAL_MINUTES, min(MAX_SYNC_INTERVAL_MINUTES, interval))


def _bounded_text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _is_blank(value: Any) -> bool:
    return value in (None, "", [], {})


def _first_present(raw: Dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in raw and not _is_blank(raw.get(key)):
            return raw.get(key)
    return None


def _field_value(raw: Any, keys: tuple[str, ...]) -> Any:
    if not isinstance(raw, dict):
        return raw
    value = _first_present(raw, keys)
    if isinstance(value, dict):
        nested = _field_value(value, keys)
        return nested if nested is not None else value
    return value


def normalize_provider_metadata(value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    discovery_source = _bounded_text(
        raw.get("discovery_source", raw.get("discoverySource")),
        40,
    )
    if discovery_source not in {"apifox_cli", "openapi_info"}:
        discovery_source = ""
    return {
        "project_name": _bounded_text(
            raw.get("project_name", raw.get("projectName")),
            200,
        ),
        "project_description": _bounded_text(
            raw.get("project_description", raw.get("projectDescription")),
            500,
        ),
        "team_id": _bounded_text(raw.get("team_id", raw.get("teamId")), 100),
        "team_name": _bounded_text(raw.get("team_name", raw.get("teamName")), 200),
        "branch_name": _bounded_text(
            raw.get("branch_name", raw.get("branchName")),
            200,
        ),
        "environment_name": _bounded_text(
            raw.get("environment_name", raw.get("environmentName")),
            200,
        ),
        "discovered_at": _bounded_text(
            raw.get("discovered_at", raw.get("discoveredAt")),
            40,
        ),
        "discovery_source": discovery_source,
    }


def _merge_provider_metadata(current: Any, changes: Any) -> Dict[str, Any]:
    merged = normalize_provider_metadata(current)
    incoming = normalize_provider_metadata(changes)
    for key, value in incoming.items():
        if value:
            merged[key] = value
    return merged


def normalized_sync_scope(value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    mode = str(raw.get("mode") or "all").strip().lower()
    if mode not in {"all", "selected"}:
        raise ValueError("sync_scope.mode 仅支持 all 或 selected")
    raw_paths = raw.get("module_paths", raw.get("modulePaths", []))
    values = raw_paths if isinstance(raw_paths, list) else []
    paths = sorted({api_module_service.normalize_module_path(item) for item in values if api_module_service.normalize_module_path(item)})
    if mode == "selected" and not paths:
        raise ValueError("selected 同步范围至少选择一个模块")
    return {
        "mode": mode,
        "module_paths": paths if mode == "selected" else [],
        "matcher_version": api_module_service.MODULE_MATCHER_VERSION,
    }


def _looks_sensitive_name(name: Any) -> bool:
    return bool(_SENSITIVE_NAME_RE.search(str(name or "")))


def _snapshot_base_url_rows(value: Any) -> List[Dict[str, str]]:
    result: List[Dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add_row(name: Any, url: Any) -> None:
        if isinstance(url, dict):
            url = _field_value(url, _BASE_URL_VALUE_KEYS)
        clean_name = _bounded_text(name, 120) or f"baseUrl{len(result) + 1}"
        clean_url = _bounded_text(url, 1000)
        if not clean_url:
            return
        key = (clean_name, clean_url)
        if key in seen:
            return
        seen.add(key)
        result.append({"name": clean_name, "url": clean_url})

    def consume(candidate: Any, fallback_name: str = "") -> None:
        if len(result) >= 50 or _is_blank(candidate):
            return
        if isinstance(candidate, str):
            add_row(fallback_name or f"baseUrl{len(result) + 1}", candidate)
            return
        if isinstance(candidate, list):
            for index, item in enumerate(candidate, start=1):
                consume(item, f"baseUrl{index}")
                if len(result) >= 50:
                    break
            return
        if isinstance(candidate, dict):
            url = _field_value(candidate, _BASE_URL_VALUE_KEYS)
            if url is not None and url is not candidate:
                add_row(
                    candidate.get("name")
                    or candidate.get("key")
                    or candidate.get("scope")
                    or candidate.get("id")
                    or candidate.get("title")
                    or fallback_name,
                    url,
                )
                return
            for key, item in candidate.items():
                consume(item, _bounded_text(key, 120))
                if len(result) >= 50:
                    break

    consume(value)
    return result[:50]


def _snapshot_variable_rows(value: Any) -> List[Dict[str, Any]]:
    result: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def append_row(item: Any, fallback_name: str = "", inherited_scope: str = "") -> None:
        if len(result) >= 200:
            return
        raw = item if isinstance(item, dict) else {}
        name = _bounded_text(_field_value(raw, _VARIABLE_NAME_KEYS) or fallback_name, 160)
        if not name or name in seen:
            return
        seen.add(name)
        sensitive = bool(
            raw.get("sensitive")
            or raw.get("secret")
            or raw.get("private")
            or raw.get("isSensitive")
            or raw.get("isSecret")
            or raw.get("isPrivate")
            or _looks_sensitive_name(name)
        )
        variable_value = _field_value(raw, _VARIABLE_VALUE_KEYS)
        if variable_value is raw:
            variable_value = ""
        result.append({
            "name": name,
            "value": "" if sensitive else _bounded_text(variable_value, 2000),
            "sensitive": sensitive,
            "scope": _bounded_text(
                raw.get("scope") or raw.get("type") or raw.get("in") or inherited_scope,
                80,
            ) or "environment",
        })

    def nested_variable_sources(raw: Dict[str, Any]) -> List[Any]:
        return [
            raw.get(key)
            for key in _VARIABLE_SOURCE_KEYS
            if key in raw and not _is_blank(raw.get(key))
        ]

    def consume(candidate: Any, fallback_name: str = "", inherited_scope: str = "") -> None:
        if len(result) >= 200 or _is_blank(candidate):
            return
        if isinstance(candidate, dict):
            nested_sources = nested_variable_sources(candidate)
            if nested_sources:
                nested_scope = _bounded_text(
                    candidate.get("scope")
                    or candidate.get("type")
                    or candidate.get("in")
                    or _field_value(candidate, _VARIABLE_NAME_KEYS)
                    or inherited_scope
                    or fallback_name,
                    80,
                )
                for item in nested_sources:
                    consume(item, "", nested_scope)
                    if len(result) >= 200:
                        break
                return
            has_variable_shape = any(key in candidate for key in (*_VARIABLE_NAME_KEYS, *_VARIABLE_VALUE_KEYS))
            if has_variable_shape:
                append_row(candidate, fallback_name, inherited_scope)
                return
            for key, item in candidate.items():
                if isinstance(item, dict):
                    merged = dict(item)
                    merged.setdefault("name", key)
                    consume(merged, key, inherited_scope)
                else:
                    append_row({"name": key, "value": item}, key, inherited_scope)
                if len(result) >= 200:
                    break
            return
        if isinstance(candidate, list):
            for item in candidate:
                consume(item, fallback_name, inherited_scope)
                if len(result) >= 200:
                    break

    consume(value)
    return result[:200]


def _environment_snapshot_sources(raw: Dict[str, Any], keys: tuple[str, ...]) -> List[Any]:
    return [
        raw.get(key)
        for key in keys
        if key in raw and not _is_blank(raw.get(key))
    ]


def _read_apifox_credential() -> Dict[str, Any]:
    value = read_json_file(_apifox_credential_path(), default={}) or {}
    return value if isinstance(value, dict) else {}


def _public_apifox_credential(value: Dict[str, Any]) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    token = str(raw.get("access_token") or "").strip()
    return {
        "credential_configured": bool(token),
        "base_url": _bounded_text(raw.get("base_url") or "https://api.apifox.com", 500),
        "credential_label": "Apifox 访问令牌" if token else "",
        "updated_at": _bounded_text(raw.get("updated_at"), 40),
    }


def get_apifox_credential(masked: bool = True) -> Dict[str, Any]:
    credential = _read_apifox_credential()
    if not credential:
        return _public_apifox_credential({}) if masked else {}
    return _public_apifox_credential(credential) if masked else dict(credential)


def save_apifox_credential(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("Apifox 凭据必须是对象")
    current = _read_apifox_credential()
    clear = safe_bool(payload.get("clear_credentials", payload.get("clearCredentials")), False)
    token_input = str(
        payload.get("access_token") or payload.get("accessToken") or payload.get("token") or ""
    ).strip()
    base_url = _validate_base_url(
        payload.get("base_url", payload.get("baseUrl", current.get("base_url") or "https://api.apifox.com"))
    )
    if clear:
        credential = {"base_url": base_url, "access_token": "", "updated_at": _now()}
    else:
        access_token = token_input or str(current.get("access_token") or "").strip()
        if not access_token:
            raise ValueError("请输入 Apifox 访问令牌")
        credential = {"base_url": base_url, "access_token": access_token, "updated_at": _now()}
    write_json_file(_apifox_credential_path(), credential)
    try:
        os.chmod(_apifox_credential_path(), 0o600)
    except OSError:
        pass
    return _public_apifox_credential(credential)


def apply_saved_apifox_credential(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Attach the global Apifox credential when a new source omits a token."""
    data = dict(payload or {})
    source_type = str(data.get("source_type") or data.get("sourceType") or "apifox").strip().lower()
    if source_type != "apifox":
        return data
    has_token = bool(str(data.get("access_token") or data.get("accessToken") or data.get("token") or "").strip())
    if has_token:
        return data
    requested_id = str(data.get("source_id") or data.get("sourceId") or "").strip()
    if requested_id and str(_raw_source(requested_id).get("access_token") or "").strip():
        return data
    credential = get_apifox_credential(masked=False)
    token = str(credential.get("access_token") or "").strip()
    if not token:
        return data
    data["access_token"] = token
    data.setdefault("base_url", credential.get("base_url") or "https://api.apifox.com")
    return data


def normalize_environment_snapshot(value: Any) -> Dict[str, Any]:
    """Return a safe public Apifox environment snapshot without secret values."""
    raw = value if isinstance(value, dict) else {}
    base_urls = _snapshot_base_url_rows(_environment_snapshot_sources(raw, _BASE_URL_SOURCE_KEYS))
    variables = _snapshot_variable_rows(_environment_snapshot_sources(raw, _VARIABLE_SOURCE_KEYS))
    if not base_urls and not variables:
        return {}
    sensitive_count = sum(1 for item in variables if item.get("sensitive"))
    return {
        "base_urls": base_urls,
        "variables": variables,
        "variable_count": len(variables),
        "sensitive_variable_count": sensitive_count,
    }


def source_config_fingerprint(source: Dict[str, Any]) -> str:
    """Return a stable, non-reversible identity for sync-relevant source config."""
    scope = normalized_sync_scope((source or {}).get("sync_scope"))
    credential = str((source or {}).get("access_token") or "").strip()
    identity = {
        "source_type": str((source or {}).get("source_type") or "").strip().lower(),
        "project_id": str((source or {}).get("project_id") or "").strip(),
        "base_url": str((source or {}).get("base_url") or "").strip().rstrip("/").lower(),
        "branch_id": str((source or {}).get("branch_id") or "").strip(),
        "environment_id": str((source or {}).get("environment_id") or "").strip(),
        "sync_enabled": bool((source or {}).get("sync_enabled")),
        "scope_fingerprint": api_module_service.scope_fingerprint(scope),
        "credential_identity_hash": hashlib.sha256(credential.encode("utf-8")).hexdigest(),
    }
    raw = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


@contextmanager
def locked_api_source_config(source_id: str, expected_fingerprint: str):
    """Keep a sync persistence boundary coupled to its original source config."""
    with _SOURCE_LOCK:
        source = _raw_source(source_id)
        if not source or source_config_fingerprint(source) != str(expected_fingerprint or ""):
            raise ApiSourceConfigDriftError("API source configuration changed during synchronization")
        yield dict(source)


def _validate_base_url(value: Any) -> str:
    base_url = str(value or "https://api.apifox.com").strip().rstrip("/")
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise ValueError("base_url 必须是有效的 HTTP(S) 服务地址")
    return base_url


def _load_file_source(source_id: str) -> Dict[str, Any]:
    source = read_json_file(_source_path(source_id), default={}) or {}
    return source if isinstance(source, dict) else {}


def _source_ids() -> List[str]:
    index = read_json_file(_index_path(), default=[]) or []
    values = index if isinstance(index, list) else []
    result = [str(item or "").strip() for item in values if str(item or "").strip()]
    env_source = _env_source()
    if env_source and DEFAULT_APIFOX_SOURCE_ID not in result:
        result.append(DEFAULT_APIFOX_SOURCE_ID)
    return result


def _save_source_index(source_id: str) -> None:
    values = [item for item in _source_ids() if item != source_id]
    values.insert(0, source_id)
    write_json_file(_index_path(), values[:100])


def _write_source(source: Dict[str, Any]) -> None:
    path = _source_path(str(source.get("source_id") or ""))
    write_json_file(path, source)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    _save_source_index(str(source.get("source_id") or ""))


def _raw_source(source_id: str) -> Dict[str, Any]:
    target = str(source_id or "").strip()
    if not target:
        return {}
    stored = _load_file_source(target)
    if target != DEFAULT_APIFOX_SOURCE_ID:
        return stored
    env_source = _env_source()
    if not stored:
        return env_source
    if env_source:
        merged = dict(env_source)
        merged.update({key: value for key, value in stored.items() if value not in (None, "")})
        if not stored.get("access_token"):
            merged["access_token"] = env_source.get("access_token", "")
        merged["config_source"] = "file+environment"
        return merged
    return stored


def _public_source(source: Dict[str, Any]) -> Dict[str, Any]:
    public = dict(source or {})
    token = str(public.pop("access_token", "") or "").strip()
    public.pop("token", None)
    public["credential_configured"] = bool(token)
    public["configured"] = bool(public.get("project_id") and token) if public.get("source_type") == "apifox" else True
    public["provider_metadata"] = normalize_provider_metadata(public.get("provider_metadata"))
    public["environment_snapshot"] = normalize_environment_snapshot(public.get("environment_snapshot"))
    public["sync_scope"] = normalized_sync_scope(public.get("sync_scope"))
    public["module_catalog"] = public.get("module_catalog") if isinstance(public.get("module_catalog"), list) else []
    public["scope_fingerprint"] = str(public.get("scope_fingerprint") or "")
    interval = _sync_interval(public.get("sync_interval_minutes"))
    reference = max(
        _timestamp(public.get("last_attempt_at")),
        _timestamp(public.get("last_success_at")),
    )
    if reference:
        next_check_at = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(reference + interval * 60),
        )
    else:
        next_check_at = str(public.get("created_at") or _now())
    public["sync_schedule"] = {
        "mode": "automatic" if public.get("sync_enabled") else "manual",
        "interval_minutes": interval,
        "last_success_at": str(public.get("last_success_at") or ""),
        "next_check_at": next_check_at if public.get("sync_enabled") else "",
        "status": str(public.get("last_sync_status") or ""),
    }
    return public


def _timestamp(value: Any) -> float:
    try:
        return time.mktime(time.strptime(str(value or ""), "%Y-%m-%d %H:%M:%S"))
    except (TypeError, ValueError):
        return 0.0


def get_api_source(source_id: str, masked: bool = True) -> Dict[str, Any]:
    source = _raw_source(source_id)
    if not source:
        return {}
    return _public_source(source) if masked else dict(source)


def list_api_sources() -> List[Dict[str, Any]]:
    return [
        source
        for source_id in _source_ids()
        for source in [get_api_source(source_id, masked=True)]
        if source
    ]


def _save_api_source_locked(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("API source 配置必须是对象")
    requested_id = str(payload.get("source_id") or payload.get("sourceId") or "").strip()
    current = _raw_source(requested_id) if requested_id else {}
    source_type = str(payload.get("source_type") or payload.get("sourceType") or current.get("source_type") or "apifox").strip().lower()
    if source_type not in ALLOWED_SOURCE_TYPES:
        raise ValueError("source_type 仅支持 apifox 或 openapi_upload")
    source_id = requested_id or unique_millis_id("api_source")
    now = _now()
    token_input_present = "access_token" in payload or "accessToken" in payload or "token" in payload
    token_input = payload.get("access_token", payload.get("accessToken", payload.get("token", "")))
    clear_credentials = safe_bool(payload.get("clear_credentials", payload.get("clearCredentials")), False)
    base_url = _validate_base_url(
        payload.get("base_url", payload.get("baseUrl", current.get("base_url") or "https://api.apifox.com"))
    )
    current_base_url = str(current.get("base_url") or "").strip().rstrip("/")
    replacement_token = token_input_present and bool(str(token_input or "").strip())
    if (
        source_type == "apifox"
        and current.get("access_token")
        and current_base_url
        and base_url != current_base_url
        and not replacement_token
        and not clear_credentials
    ):
        raise ValueError("修改 Apifox base_url 时必须重新提交访问令牌")
    if source_type != "apifox" or clear_credentials:
        access_token = ""
    elif token_input_present and str(token_input or "").strip():
        access_token = str(token_input).strip()
    else:
        access_token = str(current.get("access_token") or "").strip()
    sync_enabled_default = source_type == "apifox"
    scope_input = payload.get("sync_scope", payload.get("syncScope", current.get("sync_scope")))
    sync_scope = normalized_sync_scope(scope_input)
    provider_input_present = "provider_metadata" in payload or "providerMetadata" in payload
    provider_input = payload.get("provider_metadata", payload.get("providerMetadata"))
    provider_metadata = (
        normalize_provider_metadata(provider_input)
        if provider_input_present
        else normalize_provider_metadata(current.get("provider_metadata"))
    )
    environment_snapshot = normalize_environment_snapshot(
        payload.get(
            "environment_snapshot",
            payload.get("environmentSnapshot", current.get("environment_snapshot")),
        )
    )
    default_name = (
        provider_metadata.get("project_name")
        or ("Apifox 接口" if source_type == "apifox" else "OpenAPI 上传")
    )
    source = {
        "source_id": source_id,
        "source_type": source_type,
        "name": str(payload.get("name", current.get("name") or default_name)).strip(),
        "base_url": base_url,
        "project_id": str(payload.get("project_id", payload.get("projectId", current.get("project_id", ""))) or "").strip(),
        "branch_id": str(payload.get("branch_id", payload.get("branchId", current.get("branch_id", ""))) or "").strip(),
        "environment_id": str(payload.get("environment_id", payload.get("environmentId", current.get("environment_id", ""))) or "").strip(),
        "provider_metadata": provider_metadata,
        "environment_snapshot": environment_snapshot,
        "credential_mode": "access_token" if source_type == "apifox" else "none",
        "access_token": access_token,
        "sync_enabled": safe_bool(payload.get("sync_enabled", payload.get("syncEnabled", current.get("sync_enabled"))), sync_enabled_default),
        "sync_interval_minutes": _sync_interval(payload.get("sync_interval_minutes", payload.get("syncIntervalMinutes", current.get("sync_interval_minutes", 60)))),
        "last_sync_id": str(current.get("last_sync_id") or ""),
        "last_attempt_at": str(current.get("last_attempt_at") or ""),
        "last_success_at": str(current.get("last_success_at") or ""),
        "last_sync_status": str(current.get("last_sync_status") or ""),
        "last_error": str(current.get("last_error") or ""),
        "sync_scope": sync_scope,
        "module_catalog": current.get("module_catalog") if isinstance(current.get("module_catalog"), list) else [],
        "scope_fingerprint": str(current.get("scope_fingerprint") or ""),
        "created_at": str(current.get("created_at") or now),
        "updated_at": now,
        "config_source": "file",
    }
    if not source["name"]:
        raise ValueError("API source name 不能为空")
    _write_source(source)
    return _public_source(source)


def save_api_source(payload: Dict[str, Any]) -> Dict[str, Any]:
    with _SOURCE_LOCK:
        return _save_api_source_locked(payload)


def _update_api_source_sync_state_locked(
    source_id: str,
    *,
    expected_config_fingerprint: str = "",
    **changes: Any,
) -> Dict[str, Any]:
    source = _raw_source(source_id)
    if not source:
        raise ValueError("API source 不存在")
    if expected_config_fingerprint and source_config_fingerprint(source) != expected_config_fingerprint:
        raise ApiSourceConfigDriftError("API source configuration changed during synchronization")
    allowed = {"last_sync_id", "last_attempt_at", "last_success_at", "last_sync_status", "last_error", "updated_at"}
    for key, value in changes.items():
        if key in allowed:
            source[key] = str(value or "")
    source["updated_at"] = _now()
    source["config_source"] = "file"
    _write_source(source)
    return _public_source(source)


def update_api_source_sync_state(
    source_id: str,
    *,
    expected_config_fingerprint: str = "",
    **changes: Any,
) -> Dict[str, Any]:
    with _SOURCE_LOCK:
        return _update_api_source_sync_state_locked(
            source_id,
            expected_config_fingerprint=expected_config_fingerprint,
            **changes,
        )


def update_api_source_discovery_state(
    source_id: str,
    module_catalog: List[Dict[str, Any]],
    scope_fingerprint: str,
    *,
    provider_metadata: Any = None,
    expected_config_fingerprint: str = "",
) -> Dict[str, Any]:
    with _SOURCE_LOCK:
        source = _raw_source(source_id)
        if not source:
            raise ValueError("API source 不存在")
        if expected_config_fingerprint and source_config_fingerprint(source) != expected_config_fingerprint:
            raise ApiSourceConfigDriftError("API source configuration changed during synchronization")
        source["module_catalog"] = [dict(item) for item in module_catalog if isinstance(item, dict)]
        source["scope_fingerprint"] = str(scope_fingerprint or "")
        if provider_metadata is not None:
            source["provider_metadata"] = _merge_provider_metadata(
                source.get("provider_metadata"),
                provider_metadata,
            )
        source["updated_at"] = _now()
        source["config_source"] = "file"
        _write_source(source)
        return _public_source(source)


__all__ = [
    "ALLOWED_SOURCE_TYPES",
    "API_TESTING_DIR",
    "ApiSourceConfigDriftError",
    "DEFAULT_APIFOX_SOURCE_ID",
    "apply_saved_apifox_credential",
    "get_apifox_credential",
    "get_api_source",
    "list_api_sources",
    "save_apifox_credential",
    "save_api_source",
    "normalize_environment_snapshot",
    "normalize_provider_metadata",
    "normalized_sync_scope",
    "locked_api_source_config",
    "update_api_source_discovery_state",
    "update_api_source_sync_state",
    "source_config_fingerprint",
]
