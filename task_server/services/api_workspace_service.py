"""Non-secret MeterSphere execution bindings scoped to one API source."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from typing import Any, Dict

from task_server.config import LEARNING_DIR
from task_server.storage import clean_id, read_json_file, safe_join, write_json_file
from task_server.services import api_source_service


API_TESTING_DIR = os.getenv("API_TESTING_DIR", safe_join(LEARNING_DIR, "api-testing"))
_BINDING_LOCK = threading.RLock()
_HTTP_FIELD_NAME_RE = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class ApiWorkspaceBindingConflict(ValueError):
    """Raised when an older binding write races with a newer selection."""


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _binding_path(source_id: str) -> str:
    return safe_join(
        API_TESTING_DIR,
        "workspace-bindings",
        f"{clean_id(source_id, 'api_source')}.json",
    )


def _auth_profile_identity(project_id: str, environment_id: str) -> str:
    return f"metersphere:{str(project_id or '').strip()}:{str(environment_id or '').strip()}"


def _auth_profile_path(project_id: str, environment_id: str) -> str:
    digest = _stable_hash(_auth_profile_identity(project_id, environment_id), 24)
    return safe_join(API_TESTING_DIR, "auth-profiles", f"{digest}.json")


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


def _binding_version() -> str:
    return secrets.token_hex(12)


def _binding_compare_token(binding: Dict[str, Any]) -> str:
    return str(
        binding.get("binding_version")
        or binding.get("config_fingerprint")
        or ""
    ).strip()


def _client_write_guard(client_session_id: str, client_intent_id: Any) -> Dict[str, Any]:
    session_id = str(client_session_id or "").strip()
    raw_intent_id = str(client_intent_id or "").strip()
    if not session_id and not raw_intent_id:
        return {}
    if not session_id or not raw_intent_id:
        raise ValueError("binding client session 和 intent 必须同时提供")
    try:
        intent_id = int(raw_intent_id)
    except (TypeError, ValueError):
        raise ValueError("binding client intent 必须是正整数")
    if intent_id < 1:
        raise ValueError("binding client intent 必须是正整数")
    return {
        "session_hash": hashlib.sha256(session_id.encode("utf-8")).hexdigest(),
        "intent_id": intent_id,
    }


def _stable_hash(value: str, size: int) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()[:size]


def _public_auth_binding(value: Any) -> Dict[str, Any]:
    binding = value if isinstance(value, dict) else {}
    return {
        "auth_ref": str(binding.get("auth_ref") or "").strip(),
        "auth_type": str(binding.get("auth_type") or "").strip(),
        "header_name": str(binding.get("header_name") or "").strip(),
        "variable_name": str(binding.get("variable_name") or "").strip(),
        "project_id": str(binding.get("project_id") or "").strip(),
        "environment_id": str(binding.get("environment_id") or "").strip(),
        "configured": bool(binding.get("configured")),
        "configured_at": str(binding.get("configured_at") or "").strip(),
        "updated_at": str(binding.get("updated_at") or "").strip(),
        "binding_fingerprint": str(binding.get("binding_fingerprint") or "").strip(),
        "profile_fingerprint": str(binding.get("profile_fingerprint") or "").strip(),
        "scope": str(binding.get("scope") or "").strip(),
        "reused": bool(binding.get("reused")),
        "usage_count": max(0, int(binding.get("usage_count") or 0)),
    }


def _load_auth_profile(project_id: str, environment_id: str) -> Dict[str, Any]:
    profile = read_json_file(
        _auth_profile_path(project_id, environment_id),
        default={},
    ) or {}
    return profile if isinstance(profile, dict) else {}


def _profile_usage_count(project_id: str, environment_id: str) -> int:
    directory = safe_join(API_TESTING_DIR, "workspace-bindings")
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    count = 0
    for name in names:
        if not name.endswith(".json"):
            continue
        candidate = read_json_file(safe_join(directory, name), default={}) or {}
        if not isinstance(candidate, dict):
            continue
        if (
            str(candidate.get("project_id") or "").strip() == project_id
            and str(candidate.get("environment_id") or "").strip() == environment_id
        ):
            count += 1
    return count


def _public_auth_profile(
    value: Any,
    *,
    project_id: str = "",
    environment_id: str = "",
) -> Dict[str, Any]:
    public = _public_auth_binding(value)
    selected_project_id = str(project_id or public.get("project_id") or "").strip()
    selected_environment_id = str(
        environment_id or public.get("environment_id") or ""
    ).strip()
    usage_count = _profile_usage_count(selected_project_id, selected_environment_id)
    public.update({
        "project_id": selected_project_id,
        "environment_id": selected_environment_id,
        "scope": "environment",
        "reused": usage_count > 1,
        "usage_count": usage_count,
    })
    return public


def _write_auth_profile(profile: Dict[str, Any]) -> None:
    path = _auth_profile_path(
        str(profile.get("project_id") or ""),
        str(profile.get("environment_id") or ""),
    )
    write_json_file(path, profile)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _delete_auth_profile(project_id: str, environment_id: str) -> None:
    try:
        os.remove(_auth_profile_path(project_id, environment_id))
    except FileNotFoundError:
        pass


def _public_workspace_binding(value: Any) -> Dict[str, Any]:
    binding = value if isinstance(value, dict) else {}
    public = {
        key: binding.get(key)
        for key in (
            "binding_id", "source_id", "provider", "project_id", "project_name",
            "environment_id", "environment_name", "verified_at", "config_fingerprint",
            "binding_version", "created_at", "updated_at",
        )
    }
    project_id = str(binding.get("project_id") or "").strip()
    environment_id = str(binding.get("environment_id") or "").strip()
    profile = _load_auth_profile(project_id, environment_id)
    if profile.get("configured"):
        public["auth_binding"] = _public_auth_profile(
            profile,
            project_id=project_id,
            environment_id=environment_id,
        )
    elif isinstance(binding.get("auth_binding"), dict):
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
    expected_binding_fingerprint: str | None = None,
    client_session_id: str = "",
    client_intent_id: Any = None,
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
    incoming_guard = _client_write_guard(client_session_id, client_intent_id)
    with _BINDING_LOCK:
        current = _load_binding(selected_source_id)
        current_guard = (
            current.get("_client_write_guard")
            if isinstance(current.get("_client_write_guard"), dict)
            else {}
        )
        same_client = bool(
            incoming_guard
            and current_guard
            and incoming_guard.get("session_hash") == current_guard.get("session_hash")
        )
        newer_same_client_intent = bool(
            same_client
            and int(incoming_guard.get("intent_id") or 0)
            > int(current_guard.get("intent_id") or 0)
        )
        if (
            incoming_guard
            and same_client
            and not newer_same_client_intent
        ):
            raise ApiWorkspaceBindingConflict(
                "MeterSphere 执行绑定已由当前页面的更新选择覆盖"
            )
        if expected_binding_fingerprint is not None:
            expected = str(expected_binding_fingerprint or "").strip()
            current_token = _binding_compare_token(current)
            if expected != current_token and not newer_same_client_intent:
                raise ApiWorkspaceBindingConflict(
                    "MeterSphere 执行绑定已由其他请求更新"
                )
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
            "binding_version": _binding_version(),
            "created_at": str(current.get("created_at") or now),
            "updated_at": now,
        }
        if incoming_guard:
            binding["_client_write_guard"] = incoming_guard
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
        return _public_workspace_binding(binding)


def get_api_auth_binding(source_id: str) -> Dict[str, Any]:
    selected_source_id = str(source_id or "").strip()
    if not selected_source_id:
        return {}
    with _BINDING_LOCK:
        binding = _load_binding(selected_source_id)
        if not binding:
            return {}
        project_id = str(binding.get("project_id") or "").strip()
        environment_id = str(binding.get("environment_id") or "").strip()
        if not project_id or not environment_id:
            return {}
        profile = _load_auth_profile(project_id, environment_id)
        if not profile.get("configured"):
            legacy = _public_auth_binding(binding.get("auth_binding"))
            if (
                legacy.get("configured")
                and legacy.get("environment_id") == environment_id
            ):
                profile = {
                    **legacy,
                    "project_id": project_id,
                    "environment_id": environment_id,
                    "scope": "environment",
                    "profile_fingerprint": _stable_hash(
                        _auth_profile_identity(project_id, environment_id),
                        16,
                    ),
                }
                _write_auth_profile(profile)
        if not profile.get("configured"):
            return {}
        if (
            str(profile.get("project_id") or "").strip() != project_id
            or str(profile.get("environment_id") or "").strip() != environment_id
        ):
            return {}
        return _public_auth_profile(
            profile,
            project_id=project_id,
            environment_id=environment_id,
        )


def get_environment_auth_profile(
    project_id: str,
    environment_id: str,
) -> Dict[str, Any]:
    selected_project_id = str(project_id or "").strip()
    selected_environment_id = str(environment_id or "").strip()
    if not selected_project_id or not selected_environment_id:
        return {}
    with _BINDING_LOCK:
        profile = _load_auth_profile(
            selected_project_id,
            selected_environment_id,
        )
        if not profile.get("configured"):
            return {}
        return _public_auth_profile(
            profile,
            project_id=selected_project_id,
            environment_id=selected_environment_id,
        )


def normalize_api_auth_header(auth_type: str, header_name: str) -> tuple[str, str]:
    normalized_type = str(auth_type or "").strip().lower()
    raw_header = str(header_name or "")
    if normalized_type not in {"bearer", "api_key"}:
        raise ValueError("认证类型仅支持 bearer 或 api_key")
    if raw_header and not _HTTP_FIELD_NAME_RE.fullmatch(raw_header):
        raise ValueError("认证 header 必须符合 RFC HTTP field-name")
    if normalized_type == "bearer":
        if raw_header and raw_header.casefold() != "authorization":
            raise ValueError("Bearer header 只能使用 Authorization")
        return normalized_type, "Authorization"
    if not raw_header:
        raise ValueError("API Key header 必须符合 RFC HTTP field-name")
    return normalized_type, raw_header


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
        project_id = str(binding.get("project_id") or "").strip()
        if not project_id:
            raise ValueError("请先绑定当前来源的 MeterSphere 项目和环境")
        normalized_type, normalized_header = normalize_api_auth_header(
            auth_type,
            header_name,
        )
        identity = _auth_profile_identity(project_id, selected_environment_id)
        now = _now()
        auth = {
            "auth_ref": str(auth_ref or f"api_auth_{_stable_hash(identity, 16)}").strip(),
            "auth_type": normalized_type,
            "header_name": normalized_header,
            "variable_name": str(variable_name or f"MTP_API_AUTH_{_stable_hash(identity, 12).upper()}").strip(),
            "project_id": project_id,
            "environment_id": selected_environment_id,
            "configured": True,
            "configured_at": now,
            "updated_at": now,
            "binding_fingerprint": str(binding.get("config_fingerprint") or "").strip(),
            "profile_fingerprint": _stable_hash(identity, 16),
            "scope": "environment",
        }
        _write_auth_profile(auth)
        binding["auth_binding"] = {
            "auth_ref": auth["auth_ref"],
            "project_id": project_id,
            "environment_id": selected_environment_id,
        }
        binding["updated_at"] = now
        path = _binding_path(selected_source_id)
        write_json_file(path, binding)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return _public_auth_profile(
            auth,
            project_id=project_id,
            environment_id=selected_environment_id,
        )


def clear_api_auth_binding_metadata(source_id: str) -> Dict[str, Any]:
    selected_source_id = str(source_id or "").strip()
    if not selected_source_id:
        raise ValueError("source_id 不能为空")
    with _BINDING_LOCK:
        binding = _load_binding(selected_source_id)
        if not binding:
            return {}
        project_id = str(binding.get("project_id") or "").strip()
        environment_id = str(binding.get("environment_id") or "").strip()
        previous = _public_auth_profile(
            _load_auth_profile(project_id, environment_id)
            or binding.get("auth_binding"),
            project_id=project_id,
            environment_id=environment_id,
        )
        _delete_auth_profile(project_id, environment_id)
        directory = safe_join(API_TESTING_DIR, "workspace-bindings")
        try:
            names = os.listdir(directory)
        except OSError:
            names = []
        for name in names:
            if not name.endswith(".json"):
                continue
            path = safe_join(directory, name)
            candidate = read_json_file(path, default={}) or {}
            if not isinstance(candidate, dict):
                continue
            if (
                str(candidate.get("project_id") or "").strip() != project_id
                or str(candidate.get("environment_id") or "").strip() != environment_id
            ):
                continue
            candidate.pop("auth_binding", None)
            candidate["updated_at"] = _now()
            write_json_file(path, candidate)
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
    "ApiWorkspaceBindingConflict",
    "clear_api_auth_binding_metadata",
    "get_environment_auth_profile",
    "get_api_auth_binding",
    "get_api_workspace_binding",
    "normalize_api_auth_header",
    "save_api_auth_binding_metadata",
    "save_api_workspace_binding",
]
