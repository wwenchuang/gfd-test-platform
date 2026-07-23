"""Server-side API source configuration for the API testing workspace."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
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
    public["sync_scope"] = normalized_sync_scope(public.get("sync_scope"))
    public["module_catalog"] = public.get("module_catalog") if isinstance(public.get("module_catalog"), list) else []
    public["scope_fingerprint"] = str(public.get("scope_fingerprint") or "")
    return public


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
    source = {
        "source_id": source_id,
        "source_type": source_type,
        "name": str(payload.get("name", current.get("name") or ("Apifox 接口" if source_type == "apifox" else "OpenAPI 上传"))).strip(),
        "base_url": base_url,
        "project_id": str(payload.get("project_id", payload.get("projectId", current.get("project_id", ""))) or "").strip(),
        "branch_id": str(payload.get("branch_id", payload.get("branchId", current.get("branch_id", ""))) or "").strip(),
        "environment_id": str(payload.get("environment_id", payload.get("environmentId", current.get("environment_id", ""))) or "").strip(),
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
        source["updated_at"] = _now()
        source["config_source"] = "file"
        _write_source(source)
        return _public_source(source)


__all__ = [
    "ALLOWED_SOURCE_TYPES",
    "API_TESTING_DIR",
    "ApiSourceConfigDriftError",
    "DEFAULT_APIFOX_SOURCE_ID",
    "get_api_source",
    "list_api_sources",
    "save_api_source",
    "normalized_sync_scope",
    "locked_api_source_config",
    "update_api_source_discovery_state",
    "update_api_source_sync_state",
    "source_config_fingerprint",
]
