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
        path = _binding_path(selected_source_id)
        write_json_file(path, binding)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        return binding


def get_api_workspace_binding(source_id: str, allow_legacy: bool = True) -> Dict[str, Any]:
    selected_source_id = str(source_id or "").strip()
    if not selected_source_id:
        return {}
    with _BINDING_LOCK:
        binding = _load_binding(selected_source_id)
        if binding:
            return binding
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
    "get_api_workspace_binding",
    "save_api_workspace_binding",
]
