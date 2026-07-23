"""Non-secret MeterSphere execution bindings scoped to one API source."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from typing import Any, Dict

from task_server.config import LEARNING_DIR
from task_server.storage import clean_id, read_json_file, safe_join, write_json_file
from task_server.services import api_source_service


API_TESTING_DIR = os.getenv("API_TESTING_DIR", safe_join(LEARNING_DIR, "api-testing"))
_BINDING_LOCK = threading.RLock()


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _binding_path(source_id: str) -> str:
    return safe_join(
        API_TESTING_DIR,
        "workspace-bindings",
        f"{clean_id(source_id, 'api_source')}.json",
    )


def _binding_id(source_id: str) -> str:
    digest = hashlib.sha256(str(source_id).encode("utf-8")).hexdigest()[:16]
    return f"api_execution_binding_{digest}"


def _config_fingerprint(project_id: str, environment_id: str) -> str:
    payload = json.dumps({
        "provider": "metersphere",
        "project_id": str(project_id),
        "environment_id": str(environment_id),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _stable_hash(value: str, size: int) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:size]


def _public_auth_binding(value: Any) -> Dict[str, Any]:
    binding = value if isinstance(value, dict) else {}
    return {
        "auth_ref": str(binding.get("auth_ref") or "").strip(),
        "auth_type": str(binding.get("auth_type") or "").strip(),
        "header_name": str(binding.get("header_name") or "").strip(),
        "variable_name": str(binding.get("variable_name") or "").strip(),
        "environment_id": str(binding.get("environment_id") or "").strip(),
        "configured": bool(binding.get("configured")),
        "configured_at": str(binding.get("configured_at") or "").strip(),
        "updated_at": str(binding.get("updated_at") or "").strip(),
        "binding_fingerprint": str(binding.get("binding_fingerprint") or "").strip(),
    }


def _public_workspace_binding(value: Any) -> Dict[str, Any]:
    binding = value if isinstance(value, dict) else {}
    public = {
        key: binding.get(key)
        for key in (
            "binding_id", "source_id", "provider", "project_id", "project_name",
            "environment_id", "environment_name", "verified_at", "config_fingerprint",
            "created_at", "updated_at",
        )
    }
    if isinstance(binding.get("auth_binding"), dict):
        public["auth_binding"] = _public_auth_binding(binding["auth_binding"])
    return public


def _load_binding(source_id: str) -> Dict[str, Any]:
    binding = read_json_file(_binding_path(source_id), default={}) or {}
    return binding if isinstance(binding, dict) else {}


def save_api_workspace_binding(
    source_id: str,
    project_id: str,
    environment_id: str,
    *,
    project_name: str = "",
    environment_name: str = "",
    verified_at: str = "",
) -> Dict[str, Any]:
    """Persist a source-specific MeterSphere selection without connection secrets."""
    selected_source_id = str(source_id or "").strip()
    selected_project_id = str(project_id or "").strip()
    selected_environment_id = str(environment_id or "").strip()
    if not selected_source_id:
        raise ValueError("source_id 不能为空")
    if not selected_project_id:
        raise ValueError("MeterSphere project_id 不能为空")
    if not selected_environment_id:
        raise ValueError("MeterSphere environment_id 不能为空")
    with _BINDING_LOCK:
        current = _load_binding(selected_source_id)
        now = _now()
        binding = {
            "binding_id": _binding_id(selected_source_id),
            "source_id": selected_source_id,
            "provider": "metersphere",
            "project_id": selected_project_id,
            "project_name": str(project_name or "").strip(),
            "environment_id": selected_environment_id,
            "environment_name": str(environment_name or "").strip(),
            "verified_at": str(verified_at or now).strip(),
            "config_fingerprint": _config_fingerprint(selected_project_id, selected_environment_id),
            "created_at": str(current.get("created_at") or now),
            "updated_at": now,
        }
        current_auth = _public_auth_binding(current.get("auth_binding"))
        if (
            current_auth.get("configured")
            and current_auth.get("environment_id") == selected_environment_id
        ):
            binding["auth_binding"] = current_auth
        path = _binding_path(selected_source_id)
        write_json_file(path, binding)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return binding


def get_api_auth_binding(source_id: str) -> Dict[str, Any]:
    binding = get_api_workspace_binding(source_id, allow_legacy=False)
    auth_binding = _public_auth_binding(binding.get("auth_binding"))
    if not auth_binding.get("configured"):
        return {}
    if auth_binding.get("environment_id") != str(binding.get("environment_id") or "").strip():
        return {}
    return auth_binding


def save_api_auth_binding_metadata(
    source_id: str,
    *,
    auth_type: str,
    header_name: str,
    auth_ref: str = "",
    variable_name: str = "",
    environment_id: str = "",
) -> Dict[str, Any]:
    selected_source_id = str(source_id or "").strip()
    if not selected_source_id:
        raise ValueError("source_id 不能为空")
    with _BINDING_LOCK:
        binding = _load_binding(selected_source_id)
        if not binding:
            raise ValueError("请先绑定当前来源的 MeterSphere 项目和环境")
        selected_environment_id = str(environment_id or binding.get("environment_id") or "").strip()
        if selected_environment_id != str(binding.get("environment_id") or "").strip():
            raise ValueError("认证引用必须绑定当前 MeterSphere 环境")
        normalized_type = str(auth_type or "").strip().lower()
        normalized_header = str(header_name or "").strip()
        if normalized_type not in {"bearer", "api_key"}:
            raise ValueError("认证类型仅支持 bearer 或 api_key")
        if normalized_type == "bearer":
            normalized_header = "Authorization"
        if normalized_type == "api_key" and (
            "\r" in normalized_header
            or "\n" in normalized_header
            or any(ord(char) < 33 or ord(char) > 126 for char in normalized_header)
        ):
            raise ValueError("API Key header 必须是可打印 ASCII 名称")
        if not normalized_header:
            raise ValueError("认证 header 不能为空")
        identity = f"{selected_source_id}:{selected_environment_id}"
        auth = {
            "auth_ref": str(auth_ref or f"api_auth_{_stable_hash(identity, 16)}").strip(),
            "auth_type": normalized_type,
            "header_name": normalized_header,
            "variable_name": str(variable_name or f"MTP_API_AUTH_{_stable_hash(identity, 12).upper()}").strip(),
            "environment_id": selected_environment_id,
            "configured": True,
            "configured_at": _now(),
            "updated_at": _now(),
            "binding_fingerprint": str(binding.get("config_fingerprint") or "").strip(),
        }
        binding["auth_binding"] = auth
        binding["updated_at"] = _now()
        path = _binding_path(selected_source_id)
        write_json_file(path, binding)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return _public_auth_binding(auth)


def clear_api_auth_binding_metadata(source_id: str) -> Dict[str, Any]:
    selected_source_id = str(source_id or "").strip()
    if not selected_source_id:
        raise ValueError("source_id 不能为空")
    with _BINDING_LOCK:
        binding = _load_binding(selected_source_id)
        if not binding:
            return {}
        previous = _public_auth_binding(binding.get("auth_binding"))
        binding.pop("auth_binding", None)
        binding["updated_at"] = _now()
        path = _binding_path(selected_source_id)
        write_json_file(path, binding)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return previous


def get_api_workspace_binding(source_id: str, allow_legacy: bool = True) -> Dict[str, Any]:
    selected_source_id = str(source_id or "").strip()
    if not selected_source_id:
        return {}
    with _BINDING_LOCK:
        binding = _load_binding(selected_source_id)
        if binding:
            return _public_workspace_binding(binding)
        if not allow_legacy:
            return {}
        sources = api_source_service.list_api_sources()
        if len(sources) != 1 or str(sources[0].get("source_id") or "") != selected_source_id:
            return {}
        # The legacy selection is only a one-source migration path. It is never copied
        # into a second source and connection credentials remain outside this store.
        from task_server.services import metersphere_service

        config = metersphere_service._load_raw_config()
        project_id = str(config.get("project_id") or "").strip()
        environment_id = str(config.get("environment_id") or "").strip()
        if not project_id or not environment_id:
            return {}
        return save_api_workspace_binding(
            selected_source_id,
            project_id,
            environment_id,
            verified_at="",
        )


__all__ = [
    "API_TESTING_DIR",
    "clear_api_auth_binding_metadata",
    "get_api_auth_binding",
    "get_api_workspace_binding",
    "save_api_auth_binding_metadata",
    "save_api_workspace_binding",
]
