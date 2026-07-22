"""MeterSphere adapter for the API testing workspace."""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List

from task_server.storage import clean_id, read_json_file, safe_join, unique_millis_id, write_json_file
from task_server.services import api_asset_service, api_test_plan_service


API_TESTING_DIR = api_asset_service.API_TESTING_DIR
METADATA_CACHE_TTL_SECONDS = 30
_SENSITIVE_KEY_PARTS = (
    "authorization", "token", "accesskey", "secretkey", "cookie", "signature",
    "password", "credential",
)
EXECUTION_PHASES = (
    ("push_cases", "推送用例"),
    ("trigger_plan", "触发计划"),
    ("metersphere_run", "MeterSphere 执行"),
    ("sync_report", "同步报告"),
)
TERMINAL_EXECUTION_STATES = {"succeeded", "failed", "cancelled"}
_EXECUTION_LOCK = threading.RLock()


class MeterSphereExecutionValidationError(ValueError):
    pass


class MeterSphereExecutionConflict(ValueError):
    pass


class MeterSphereExecutionNotFound(ValueError):
    pass


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _api_path(*parts: str) -> str:
    return safe_join(API_TESTING_DIR, *parts)


def _config_path() -> str:
    return _api_path("metersphere-config.json")


def _push_path(push_id: str) -> str:
    return _api_path("metersphere-pushes", f"{clean_id(push_id, 'ms_push')}.json")


def _run_path(run_id: str) -> str:
    return _api_path("metersphere-runs", f"{clean_id(run_id, 'ms_run')}.json")


def _execution_path(execution_id: str) -> str:
    return _api_path("metersphere-executions", f"{clean_id(execution_id, 'ms_execution')}.json")


def _metadata_cache_path(kind: str, project_id: str = "") -> str:
    suffix = f"-{clean_id(project_id, 'project')}" if project_id else ""
    return _api_path("metersphere-cache", f"{clean_id(kind, 'metadata')}{suffix}.json")


def _env_config() -> Dict[str, Any]:
    return {
        "base_url": os.getenv("METERSPHERE_BASE_URL", "").strip(),
        "auth_mode": os.getenv("METERSPHERE_AUTH_MODE", "").strip().lower(),
        "token": os.getenv("METERSPHERE_TOKEN", "").strip(),
        "access_key": os.getenv("METERSPHERE_ACCESS_KEY", "").strip(),
        "secret_key": os.getenv("METERSPHERE_SECRET_KEY", "").strip(),
        "workspace_id": os.getenv("METERSPHERE_WORKSPACE_ID", "").strip(),
        "project_id": os.getenv("METERSPHERE_PROJECT_ID", "").strip(),
        "environment_id": os.getenv("METERSPHERE_ENVIRONMENT_ID", "").strip(),
        "health_path": os.getenv("METERSPHERE_HEALTH_PATH", "/api/health").strip() or "/api/health",
        "project_list_path": os.getenv("METERSPHERE_PROJECT_LIST_PATH", "").strip(),
        "environment_list_path": os.getenv("METERSPHERE_ENVIRONMENT_LIST_PATH", "").strip(),
        "case_push_path": os.getenv("METERSPHERE_CASE_PUSH_PATH", "").strip(),
        "plan_run_path": os.getenv("METERSPHERE_PLAN_RUN_PATH", "").strip(),
        "run_status_path": os.getenv("METERSPHERE_RUN_STATUS_PATH", "").strip(),
        "report_path": os.getenv("METERSPHERE_REPORT_PATH", "").strip(),
    }


def _load_raw_config() -> Dict[str, Any]:
    file_config = read_json_file(_config_path(), default={}) or {}
    if not isinstance(file_config, dict):
        file_config = {}
    merged = _env_config()
    merged.update({key: value for key, value in file_config.items() if value not in (None, "")})
    merged["base_url"] = str(merged.get("base_url") or "").strip().rstrip("/")
    auth_mode = str(merged.get("auth_mode") or "").strip().lower()
    if auth_mode not in {"access_key", "token"}:
        auth_mode = "access_key" if merged.get("access_key") else "token"
    merged["auth_mode"] = auth_mode
    return merged


def _mask_secret(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 6:
        return "******"
    return f"{text[:2]}***{text[-4:]}"


def metersphere_config(masked: bool = True) -> Dict[str, Any]:
    cfg = _load_raw_config()
    cfg["configured"] = bool(cfg.get("base_url"))
    cfg["token_configured"] = bool(cfg.get("token"))
    cfg["access_key_configured"] = bool(cfg.get("access_key"))
    cfg["secret_key_configured"] = bool(cfg.get("secret_key"))
    project_names = {
        str(item.get("id") or ""): str(item.get("name") or "")
        for item in (_load_metadata_cache("projects").get("items") or [])
        if isinstance(item, dict)
    }
    environment_names = {
        str(item.get("id") or ""): str(item.get("name") or "")
        for item in (
            _load_metadata_cache("environments", str(cfg.get("project_id") or "")).get("items")
            or []
        )
        if isinstance(item, dict)
    }
    cfg["project_name"] = project_names.get(str(cfg.get("project_id") or ""), "")
    cfg["environment_name"] = environment_names.get(str(cfg.get("environment_id") or ""), "")
    cfg["capabilities"] = _execution_capabilities(cfg)
    if masked:
        cfg["token"] = ""
        cfg["access_key"] = ""
        cfg["secret_key"] = ""
    return cfg


def _looks_like_masked_secret(value: Any, current: Any) -> bool:
    text = str(value or "").strip()
    return bool(current) and ("***" in text or text == "******")


def save_metersphere_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    current = _load_raw_config()
    allowed = {
        "base_url",
        "auth_mode",
        "token",
        "access_key",
        "secret_key",
        "workspace_id",
        "project_id",
        "environment_id",
        "health_path",
        "project_list_path",
        "environment_list_path",
        "case_push_path",
        "plan_run_path",
        "run_status_path",
        "report_path",
    }
    clear_secrets = {
        str(item or "").strip()
        for item in (payload.get("clear_secrets") or payload.get("clearSecrets") or [])
        if str(item or "").strip()
    }
    next_cfg: Dict[str, Any] = {}
    for key in allowed:
        value = payload.get(key, current.get(key, ""))
        if key in {"token", "access_key", "secret_key"}:
            if key in clear_secrets:
                value = ""
            elif key not in payload or not str(value or "").strip() or _looks_like_masked_secret(value, current.get(key, "")):
                value = current.get(key, "")
        next_cfg[key] = str(value or "").strip()
    next_cfg["base_url"] = next_cfg.get("base_url", "").rstrip("/")
    if next_cfg.get("auth_mode") not in {"access_key", "token"}:
        next_cfg["auth_mode"] = "access_key" if next_cfg.get("access_key") else "token"
    if not next_cfg.get("health_path"):
        next_cfg["health_path"] = "/api/health"
    next_cfg["updated_at"] = _now()
    write_json_file(_config_path(), next_cfg)
    return metersphere_config(masked=True)


def _normalized_sensitive_key(key: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(key or "").lower())


def sanitize_metersphere_data(value: Any) -> Any:
    """Recursively remove credentials from remote responses and persisted events."""
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            normalized = _normalized_sensitive_key(key)
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                continue
            result[str(key)] = sanitize_metersphere_data(item)
        return result
    if isinstance(value, list):
        return [sanitize_metersphere_data(item) for item in value]
    if isinstance(value, tuple):
        return [sanitize_metersphere_data(item) for item in value]
    if isinstance(value, str):
        return re.sub(
            r"(?i)(bearer\s+|token[=:]\s*|accesskey[=:]\s*|secretkey[=:]\s*)[^\s,;]+",
            r"\1[REDACTED]",
            value,
        )
    return value


def _metersphere_auth_headers(
    cfg: Dict[str, Any],
    method: str = "GET",
    path: str = "",
    payload: Dict[str, Any] | None = None,
) -> Dict[str, str]:
    auth_mode = str(cfg.get("auth_mode") or "").strip().lower()
    access_key = str(cfg.get("access_key") or "").strip()
    secret_key = str(cfg.get("secret_key") or "").strip()
    if auth_mode == "access_key" and access_key and secret_key:
        timestamp = str(int(time.time() * 1000))
        normalized_path = "/" + str(path or "").lstrip("/")
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload is not None else ""
        canonical = "\n".join([str(method or "GET").upper(), normalized_path, access_key, timestamp, body])
        signature = hmac.new(secret_key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        return {"accessKey": access_key, "timestamp": timestamp, "signature": signature}
    token = str(cfg.get("token") or "").strip()
    if auth_mode == "token" and token:
        return {"Authorization": f"Bearer {token}"}
    return {}


def _request_json(method: str, path: str, payload: Dict[str, Any] | None = None, timeout: float = 30) -> Dict[str, Any]:
    cfg = _load_raw_config()
    base_url = cfg.get("base_url")
    if not base_url:
        return {"ok": False, "configured": False, "error": "MeterSphere base_url 未配置"}
    api_path = str(path or "").strip()
    if not api_path:
        return {"ok": False, "requires_config": True, "error": "MeterSphere API 路径未配置"}
    url = base_url + (api_path if api_path.startswith("/") else f"/{api_path}")
    data = None
    headers = {"Content-Type": "application/json; charset=utf-8"}
    headers.update(_metersphere_auth_headers(cfg, method=method, path=api_path, payload=payload))
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    started = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw) if raw else {}
        if not isinstance(parsed, dict):
            parsed = {"data": parsed}
        parsed.setdefault("ok", True)
        parsed["elapsed_ms"] = int((time.time() - started) * 1000)
        return sanitize_metersphere_data(parsed)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        return sanitize_metersphere_data({
            "ok": False,
            "http_status": exc.code,
            "error": f"MeterSphere HTTP {exc.code}: {body}",
        })
    except Exception as exc:
        return sanitize_metersphere_data({"ok": False, "error": f"MeterSphere 请求失败：{exc}"})


def _load_metadata_cache(kind: str, project_id: str = "") -> Dict[str, Any]:
    cached = read_json_file(_metadata_cache_path(kind, project_id), default={}) or {}
    return cached if isinstance(cached, dict) else {}


def _metadata_result_items(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    value: Any = result.get("data", result)
    for _index in range(4):
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if not isinstance(value, dict):
            return []
        nested = None
        for key in ("list", "records", "items", "content", "data", "projects", "environments"):
            candidate = value.get(key)
            if isinstance(candidate, (list, dict)):
                nested = candidate
                break
        if nested is None:
            return []
        value = nested
    return []


def _remote_item_enabled(item: Dict[str, Any]) -> bool:
    for key in ("enabled", "enable", "active"):
        if key in item:
            value = item.get(key)
            if isinstance(value, bool):
                return value
            return str(value or "").strip().lower() not in {"0", "false", "disabled", "inactive"}
    status = str(item.get("status") or "").strip().lower()
    return status not in {"disabled", "inactive", "deleted", "closed"}


def _normalize_projects(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    projects = []
    for item in _metadata_result_items(result):
        project_id = str(
            item.get("id") or item.get("projectId") or item.get("project_id") or item.get("value") or ""
        ).strip()
        name = str(
            item.get("name") or item.get("projectName") or item.get("project_name") or item.get("label") or ""
        ).strip()
        if project_id and name:
            projects.append({"id": project_id, "name": name, "enabled": _remote_item_enabled(item)})
    return list({item["id"]: item for item in projects}.values())


def _normalize_environments(result: Dict[str, Any], project_id: str) -> List[Dict[str, Any]]:
    environments = []
    for item in _metadata_result_items(result):
        environment_id = str(
            item.get("id") or item.get("environmentId") or item.get("environment_id") or item.get("value") or ""
        ).strip()
        name = str(
            item.get("name") or item.get("environmentName") or item.get("environment_name") or item.get("label") or ""
        ).strip()
        item_project_id = str(
            item.get("projectId") or item.get("project_id") or item.get("project") or project_id or ""
        ).strip()
        if environment_id and name and item_project_id == project_id:
            environments.append({
                "id": environment_id,
                "name": name,
                "project_id": item_project_id,
                "enabled": _remote_item_enabled(item),
            })
    return list({item["id"]: item for item in environments}.values())


def _cached_or_live_metadata(
    kind: str,
    path: str,
    normalizer,
    project_id: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    cached = _load_metadata_cache(kind, project_id)
    cached_epoch = float(cached.get("fetched_epoch") or 0)
    cache_fresh = bool(cached.get("items")) and (time.time() - cached_epoch) <= METADATA_CACHE_TTL_SECONDS
    if cache_fresh and not force:
        return {
            "ok": True,
            "items": cached.get("items") or [],
            "source": "cache",
            "stale": False,
            "fetched_at": cached.get("fetched_at") or "",
        }
    if not str(path or "").strip():
        result = {"ok": False, "error": f"MeterSphere {kind} API 路径未配置"}
    else:
        result = _request_json("GET", path, timeout=20)
    if result.get("ok"):
        items = normalizer(result)
        record = {
            "items": items,
            "fetched_at": _now(),
            "fetched_epoch": time.time(),
        }
        write_json_file(_metadata_cache_path(kind, project_id), record)
        return {
            "ok": True,
            "items": items,
            "source": "live",
            "stale": False,
            "fetched_at": record["fetched_at"],
        }
    if isinstance(cached.get("items"), list) and cached.get("items"):
        return {
            "ok": True,
            "items": cached.get("items") or [],
            "source": "cache",
            "stale": True,
            "fetched_at": cached.get("fetched_at") or "",
            "error": str(result.get("error") or "MeterSphere 元数据刷新失败"),
        }
    return {
        "ok": False,
        "items": [],
        "source": "none",
        "stale": False,
        "fetched_at": "",
        "error": str(result.get("error") or "MeterSphere 元数据读取失败"),
    }


def list_metersphere_projects(force: bool = False) -> Dict[str, Any]:
    cfg = _load_raw_config()
    return _cached_or_live_metadata(
        "projects",
        str(cfg.get("project_list_path") or "").strip(),
        _normalize_projects,
        force=force,
    )


def list_metersphere_environments(project_id: str, force: bool = False) -> Dict[str, Any]:
    cfg = _load_raw_config()
    selected_project_id = str(project_id or "").strip()
    path = str(cfg.get("environment_list_path") or "").strip()
    path = path.replace("{project_id}", selected_project_id).replace("{projectId}", selected_project_id)
    return _cached_or_live_metadata(
        "environments",
        path,
        lambda result: _normalize_environments(result, selected_project_id),
        project_id=selected_project_id,
        force=force,
    )


def _execution_capabilities(cfg: Dict[str, Any]) -> Dict[str, Any]:
    fields = (
        ("case_push_path", "can_push", "用例推送接口"),
        ("plan_run_path", "can_run", "计划执行接口"),
        ("run_status_path", "can_query_run", "运行状态查询接口"),
        ("report_path", "can_pull_report", "报告查询接口"),
    )
    result = {capability: bool(str(cfg.get(field) or "").strip()) for field, capability, _label in fields}
    result["missing"] = [label for field, _capability, label in fields if not str(cfg.get(field) or "").strip()]
    result["ready"] = not result["missing"]
    return result


def _configured_auth_ready(cfg: Dict[str, Any]) -> bool:
    if cfg.get("auth_mode") == "access_key":
        return bool(cfg.get("access_key") and cfg.get("secret_key"))
    return bool(cfg.get("token"))


def _readiness_state(
    cfg: Dict[str, Any],
    connection: Dict[str, Any],
    capabilities: Dict[str, Any],
    projects_result: Dict[str, Any],
    environments_result: Dict[str, Any],
    confirmed_plans: List[Dict[str, Any]],
) -> Dict[str, Any]:
    missing = list(capabilities.get("missing") or [])
    selected_project = next((
        item for item in (projects_result.get("items") or [])
        if str(item.get("id") or "") == str(cfg.get("project_id") or "") and item.get("enabled") is not False
    ), None)
    selected_environment = next((
        item for item in (environments_result.get("items") or [])
        if str(item.get("id") or "") == str(cfg.get("environment_id") or "") and item.get("enabled") is not False
    ), None)
    metadata_stale = bool(projects_result.get("stale") or environments_result.get("stale"))
    if not cfg.get("base_url") or not _configured_auth_ready(cfg):
        return {"state": "not_configured", "can_execute": False, "missing": ["服务地址或认证信息"], "primary_action": "配置连接"}
    if connection.get("state") != "connected":
        return {"state": "disconnected", "can_execute": False, "missing": [connection.get("error") or "MeterSphere 连接失败"], "primary_action": "重试连接"}
    if not projects_result.get("ok"):
        missing.append("业务列表接口")
    if not selected_project:
        missing.append("有效业务")
    if not environments_result.get("ok"):
        missing.append("环境列表接口")
    if not selected_environment:
        missing.append("有效环境")
    if metadata_stale:
        missing.append("实时业务与环境校验")
    if missing:
        return {"state": "connected_needs_setup", "can_execute": False, "missing": list(dict.fromkeys(missing)), "primary_action": "完成配置"}
    if not confirmed_plans:
        return {"state": "ready_no_plan", "can_execute": False, "missing": ["已确认 API 用例计划"], "primary_action": "去生成或确认计划"}
    return {"state": "ready", "can_execute": True, "missing": [], "primary_action": "推送并执行"}


def metersphere_health() -> Dict[str, Any]:
    cfg = _load_raw_config()
    if not cfg.get("base_url"):
        return {"ok": False, "configured": False, "error": "MeterSphere base_url 未配置"}
    health_path = cfg.get("health_path") or "/api/health"
    if (
        cfg.get("auth_mode") == "access_key"
        and cfg.get("access_key")
        and cfg.get("project_id")
        and str(health_path).strip("/") == "api/health"
    ):
        health_path = f"/project/get/{cfg.get('project_id')}"
    result = _request_json("GET", health_path, timeout=10)
    result["configured"] = True
    result["base_url"] = cfg.get("base_url")
    result["token_configured"] = bool(cfg.get("token"))
    result["access_key_configured"] = bool(cfg.get("access_key"))
    result["secret_key_configured"] = bool(cfg.get("secret_key"))
    return sanitize_metersphere_data(result)


def metersphere_execution_context(force: bool = False) -> Dict[str, Any]:
    cfg = _load_raw_config()
    checked_at = _now()
    health = metersphere_health()
    connection = {
        "state": (
            "not_configured"
            if not cfg.get("base_url") or not _configured_auth_ready(cfg)
            else ("connected" if health.get("ok") else "disconnected")
        ),
        "base_url": cfg.get("base_url") or "",
        "auth_mode": cfg.get("auth_mode") or "token",
        "latency_ms": int(health.get("elapsed_ms") or 0),
        "checked_at": checked_at,
        "error": "" if health.get("ok") else str(health.get("error") or ""),
    }
    projects_result = list_metersphere_projects(force=force)
    project_id = str(cfg.get("project_id") or "").strip()
    environments_result = list_metersphere_environments(project_id, force=force) if project_id else {
        "ok": False,
        "items": [],
        "source": "none",
        "stale": False,
        "fetched_at": "",
        "error": "尚未选择业务",
    }
    plans = api_test_plan_service.list_api_test_plans(limit=50)
    confirmed_plans = [
        item for item in plans
        if item.get("status") == "confirmed"
    ]
    executable_confirmed_plans = [
        item for item in plans
        if item.get("status") == "confirmed"
        and bool((item.get("execution_readiness") or {}).get("can_execute"))
    ]
    capabilities = _execution_capabilities(cfg)
    readiness = _readiness_state(
        cfg,
        connection,
        capabilities,
        projects_result,
        environments_result,
        executable_confirmed_plans,
    )
    if confirmed_plans and not executable_confirmed_plans and readiness.get("state") == "ready_no_plan":
        readiness = {
            **readiness,
            "state": "ready_no_executable_plan",
            "missing": ["至少一条可执行且已确认的 API 用例"],
            "primary_action": "补齐计划测试数据",
        }
    snapshots = api_asset_service.list_api_snapshots(limit=1)
    if not snapshots:
        empty_reason = "no_assets"
    elif not plans:
        empty_reason = "no_plans"
    elif not confirmed_plans:
        empty_reason = "unconfirmed_plans"
    elif not executable_confirmed_plans:
        empty_reason = "no_executable_plans"
    elif not readiness.get("can_execute"):
        empty_reason = "metersphere_not_ready"
    else:
        empty_reason = "ready_first_run"
    metadata_stale = bool(projects_result.get("stale") or environments_result.get("stale"))
    metadata_source = "cache" if (
        projects_result.get("source") == "cache" or environments_result.get("source") == "cache"
    ) else "live"
    executions = list_metersphere_executions(limit=50)
    active_runs = [item for item in executions if item.get("status") not in TERMINAL_EXECUTION_STATES]
    recent_runs = [item for item in executions if item.get("status") in TERMINAL_EXECUTION_STATES][:20]
    latest_by_plan: Dict[str, Dict[str, Any]] = {}
    active_by_plan: Dict[str, Dict[str, Any]] = {}
    for execution in executions:
        plan_key = str(execution.get("plan_id") or "")
        latest_by_plan.setdefault(plan_key, execution)
        if execution.get("status") not in TERMINAL_EXECUTION_STATES:
            active_by_plan.setdefault(plan_key, execution)
    context_plans = []
    for plan in confirmed_plans:
        plan_item = copy_dict(plan)
        plan_id = str(plan_item.get("plan_id") or "")
        plan_item["latest_run"] = latest_by_plan.get(plan_id) or {}
        plan_item["active_run"] = active_by_plan.get(plan_id) or {}
        plan_item["can_execute"] = bool(
            readiness.get("can_execute")
            and (plan_item.get("execution_readiness") or {}).get("can_execute")
            and not plan_item["active_run"]
        )
        context_plans.append(plan_item)
    if active_runs:
        readiness = {
            **readiness,
            "state": "running",
            "primary_action": "查看实时进度",
        }
        empty_reason = ""
    return {
        "ok": True,
        "connection": connection,
        "selection": {
            "project_id": project_id,
            "environment_id": str(cfg.get("environment_id") or "").strip(),
        },
        "businesses": projects_result.get("items") or [],
        "environments": environments_result.get("items") or [],
        "metadata": {
            "source": metadata_source,
            "stale": metadata_stale,
            "fetched_at": min(
                [value for value in (
                    projects_result.get("fetched_at"),
                    environments_result.get("fetched_at"),
                ) if value]
                or [""]
            ),
            "errors": [
                str(result.get("error") or "")
                for result in (projects_result, environments_result)
                if result.get("error")
            ],
        },
        "config": metersphere_config(masked=True),
        "capabilities": capabilities,
        "readiness": readiness,
        "plans": context_plans,
        "active_runs": active_runs,
        "recent_runs": recent_runs,
        "empty_reason": empty_reason,
    }


def copy_dict(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _new_execution_phases() -> List[Dict[str, Any]]:
    return [{
        "id": phase_id,
        "title": title,
        "state": "waiting",
        "started_at": "",
        "ended_at": "",
        "updated_at": "",
        "summary": "",
    } for phase_id, title in EXECUTION_PHASES]


def _load_execution(execution_id: str) -> Dict[str, Any]:
    record = read_json_file(_execution_path(execution_id), default={}) or {}
    return record if isinstance(record, dict) else {}


def _save_execution(record: Dict[str, Any]) -> None:
    record["updated_at"] = _now()
    write_json_file(_execution_path(str(record.get("execution_id") or "")), record)


def _execution_records() -> List[Dict[str, Any]]:
    root = _api_path("metersphere-executions")
    if not os.path.isdir(root):
        return []
    records = []
    for name in os.listdir(root):
        if not name.endswith(".json"):
            continue
        record = read_json_file(safe_join(root, name), default={}) or {}
        if isinstance(record, dict) and record.get("execution_id"):
            records.append(record)
    records.sort(
        key=lambda item: str(item.get("created_at") or item.get("updated_at") or ""),
        reverse=True,
    )
    return records


def _execution_poll_after_ms(record: Dict[str, Any]) -> int:
    if str(record.get("status") or "") in TERMINAL_EXECUTION_STATES:
        return 0
    return 5000 if int(record.get("unchanged_polls") or 0) >= 3 else 3000


def _timestamp_epoch(value: Any) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        return time.mktime(time.strptime(text[:19], "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


def _duration_seconds(started_at: Any, ended_at: Any = "") -> int:
    started_epoch = _timestamp_epoch(started_at)
    if not started_epoch:
        return 0
    ended_epoch = _timestamp_epoch(ended_at) or time.time()
    return max(0, int(ended_epoch - started_epoch))


def _public_execution(record: Dict[str, Any]) -> Dict[str, Any]:
    public = sanitize_metersphere_data(record)
    public["duration_seconds"] = _duration_seconds(
        public.get("started_at") or public.get("created_at"),
        public.get("finished_at"),
    )
    for phase in public.get("phases") or []:
        if isinstance(phase, dict):
            phase["duration_seconds"] = _duration_seconds(
                phase.get("started_at"),
                phase.get("ended_at"),
            )
    public["poll_after_ms"] = _execution_poll_after_ms(record)
    return public


def list_metersphere_executions(limit: int = 20) -> List[Dict[str, Any]]:
    try:
        size = max(1, min(100, int(limit)))
    except Exception:
        size = 20
    return [_public_execution(item) for item in _execution_records()[:size]]


def _phase(record: Dict[str, Any], phase_id: str) -> Dict[str, Any]:
    return next((item for item in record.get("phases") or [] if item.get("id") == phase_id), {})


def _set_phase(record: Dict[str, Any], phase_id: str, state: str, summary: str = "") -> None:
    phase = _phase(record, phase_id)
    if not phase:
        return
    now = _now()
    if state == "running" and not phase.get("started_at"):
        phase["started_at"] = now
    if state in {"succeeded", "failed", "skipped"}:
        phase["ended_at"] = now
    phase["state"] = state
    phase["updated_at"] = now
    if summary:
        phase["summary"] = str(summary)
    record["current_phase"] = phase_id


def _skip_remaining_phases(record: Dict[str, Any], failed_phase_id: str) -> None:
    failed_seen = False
    for phase in record.get("phases") or []:
        if phase.get("id") == failed_phase_id:
            failed_seen = True
            continue
        if failed_seen and phase.get("state") == "waiting":
            _set_phase(record, str(phase.get("id") or ""), "skipped", "前序阶段失败，未执行")


def _append_execution_event(
    record: Dict[str, Any],
    phase_id: str,
    state: str,
    summary: str,
    detail: Any = None,
) -> None:
    event = {
        "event_id": unique_millis_id("ms_event"),
        "timestamp": _now(),
        "phase_id": phase_id,
        "state": state,
        "summary": str(summary or ""),
        "execution_id": record.get("execution_id") or "",
        "run_id": record.get("run_id") or "",
    }
    if detail not in (None, "", {}, []):
        event["detail"] = sanitize_metersphere_data(detail)
    record.setdefault("events", []).append(event)
    record["events"] = record["events"][-200:]


def _fail_execution(record: Dict[str, Any], phase_id: str, message: str, detail: Any = None) -> None:
    _set_phase(record, phase_id, "failed", message)
    _append_execution_event(record, phase_id, "failed", message, detail)
    _skip_remaining_phases(record, phase_id)
    record["status"] = "failed"
    record["error"] = str(message or "执行失败")
    record["finished_at"] = _now()
    _save_execution(record)


def _execution_plan(plan_id: str) -> Dict[str, Any]:
    plan = api_test_plan_service.get_api_test_plan(plan_id)
    if not plan:
        raise MeterSphereExecutionValidationError("API 测试计划不存在")
    if plan.get("status") != "confirmed":
        raise MeterSphereExecutionValidationError("执行前必须先确认 API 测试计划")
    if (plan.get("revision_state") or {}).get("state") == "stale":
        raise MeterSphereExecutionValidationError("API 测试计划已过期，请按当前接口版本重新生成")
    readiness = plan.get("execution_readiness") or {}
    if not readiness.get("can_execute") or int(readiness.get("executable_case_count") or 0) <= 0:
        raise MeterSphereExecutionValidationError("已确认计划没有可执行用例")
    return plan


def _active_execution_for_plan(plan_id: str) -> Dict[str, Any]:
    return next((
        item for item in _execution_records()
        if str(item.get("plan_id") or "") == str(plan_id or "")
        and str(item.get("status") or "") not in TERMINAL_EXECUTION_STATES
    ), {})


def start_metersphere_execution(plan_id: str, test_plan_id: str = "") -> Dict[str, Any]:
    selected_plan_id = str(plan_id or "").strip()
    if not selected_plan_id:
        raise MeterSphereExecutionValidationError("plan_id 不能为空")
    plan = _execution_plan(selected_plan_id)
    with _EXECUTION_LOCK:
        active = _active_execution_for_plan(selected_plan_id)
        if active:
            raise MeterSphereExecutionConflict(
                f"计划已有未结束运行：{active.get('execution_id')}"
            )
        execution_id = unique_millis_id("ms_execution")
        record = {
            "execution_id": execution_id,
            "plan_id": selected_plan_id,
            "plan_name": str(plan.get("name") or selected_plan_id),
            "test_plan_id": str(test_plan_id or "").strip(),
            "status": "queued",
            "current_phase": "push_cases",
            "created_at": _now(),
            "started_at": "",
            "updated_at": _now(),
            "finished_at": "",
            "push_id": "",
            "run_id": "",
            "report_id": "",
            "remote_status": "waiting",
            "report_status": "waiting",
            "stats": {"total": 0, "passed": 0, "failed": 0},
            "phases": _new_execution_phases(),
            "events": [],
            "unchanged_polls": 0,
            "status_poll_failures": 0,
            "error": "",
        }
        _append_execution_event(record, "push_cases", "waiting", "执行已排队")
        _save_execution(record)
        _spawn_execution_worker(execution_id)
    return _public_execution(record)


def _spawn_execution_worker(execution_id: str) -> None:
    thread = threading.Thread(
        target=_run_metersphere_execution_guarded,
        args=(execution_id,),
        daemon=True,
        name=f"metersphere-{execution_id}",
    )
    thread.start()


def _run_metersphere_execution_guarded(execution_id: str) -> None:
    try:
        _run_metersphere_execution(execution_id)
    except Exception as exc:
        safe_detail = sanitize_metersphere_data({"error": str(exc)})
        safe_error = str(safe_detail.get("error") or "未知异常")
        with _EXECUTION_LOCK:
            record = _load_execution(execution_id)
            if not record or record.get("status") in TERMINAL_EXECUTION_STATES:
                return
            phase_id = str(record.get("current_phase") or "push_cases")
            if not _phase(record, phase_id):
                phase_id = "push_cases"
            _fail_execution(
                record,
                phase_id,
                f"MeterSphere 执行线程内部异常：{safe_error}",
                safe_detail,
            )


def _run_metersphere_execution(execution_id: str) -> None:
    with _EXECUTION_LOCK:
        record = _load_execution(execution_id)
        if not record or record.get("status") in TERMINAL_EXECUTION_STATES:
            return
        record["status"] = "running"
        record["started_at"] = record.get("started_at") or _now()
        _set_phase(record, "push_cases", "running", "正在校验实时执行条件")
        _append_execution_event(record, "push_cases", "running", "开始校验 MeterSphere 实时执行条件")
        _save_execution(record)

    try:
        context = metersphere_execution_context(force=True)
        readiness = context.get("readiness") or {}
    except Exception as exc:
        context = {}
        readiness = {
            "can_execute": False,
            "missing": [f"MeterSphere 实时校验失败：{exc}"],
        }
    with _EXECUTION_LOCK:
        record = _load_execution(execution_id)
        if readiness.get("can_execute") is not True:
            missing = "、".join(str(item) for item in (readiness.get("missing") or []) if item)
            _fail_execution(
                record,
                "push_cases",
                f"MeterSphere 尚未满足执行条件{f'：{missing}' if missing else ''}",
                {"readiness": readiness, "metadata": context.get("metadata") or {}},
            )
            return
        _set_phase(record, "push_cases", "running", "正在推送确认用例")
        _append_execution_event(record, "push_cases", "running", "实时执行条件校验通过，开始推送确认用例")
        _save_execution(record)

    push_result = push_plan_to_metersphere(str(record.get("plan_id") or ""))
    with _EXECUTION_LOCK:
        record = _load_execution(execution_id)
        if not push_result.get("ok"):
            _fail_execution(
                record,
                "push_cases",
                str(push_result.get("error") or "MeterSphere 用例推送失败"),
                push_result,
            )
            return
        record["push_id"] = str(push_result.get("push_id") or push_result.get("id") or "")
        _set_phase(record, "push_cases", "succeeded", "确认用例已推送")
        _append_execution_event(record, "push_cases", "succeeded", "确认用例推送完成", push_result)
        _set_phase(record, "trigger_plan", "running", "正在触发 MeterSphere 计划")
        _append_execution_event(record, "trigger_plan", "running", "开始触发 MeterSphere 计划")
        _save_execution(record)

    run_result = create_metersphere_run(
        str(record.get("plan_id") or ""),
        str(record.get("test_plan_id") or ""),
    )
    with _EXECUTION_LOCK:
        record = _load_execution(execution_id)
        if not run_result.get("ok"):
            _fail_execution(
                record,
                "trigger_plan",
                str(run_result.get("error") or "MeterSphere 计划触发失败"),
                run_result,
            )
            return
        record["run_id"] = str(
            run_result.get("run_id") or run_result.get("runId") or run_result.get("id") or ""
        )
        _set_phase(record, "trigger_plan", "succeeded", "MeterSphere 计划已触发")
        _append_execution_event(record, "trigger_plan", "succeeded", "MeterSphere 计划触发完成", run_result)
        _set_phase(record, "metersphere_run", "running", "等待 MeterSphere 返回真实执行状态")
        _append_execution_event(record, "metersphere_run", "running", "MeterSphere 正在执行")
        record["remote_status"] = "running"
        record["status"] = "running"
        _save_execution(record)


def _remote_run_payload(result: Dict[str, Any]) -> Dict[str, Any]:
    data = result.get("data")
    return data if isinstance(data, dict) else result


def _remote_run_id(result: Dict[str, Any]) -> str:
    payload = _remote_run_payload(result)
    for source in (result, payload):
        if not isinstance(source, dict):
            continue
        value = source.get("run_id") or source.get("runId") or source.get("id")
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _normalize_remote_run_state(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_")
    if text in {"success", "succeeded", "passed", "completed", "complete", "finished", "done"}:
        return "succeeded"
    if text in {"failed", "failure", "error", "cancelled", "canceled", "stopped", "aborted"}:
        return "failed"
    return "running"


def _remote_run_stats(payload: Dict[str, Any]) -> Dict[str, int]:
    def number(*keys: str) -> int:
        for key in keys:
            if key in payload:
                try:
                    return max(0, int(payload.get(key) or 0))
                except Exception:
                    return 0
        return 0
    return {
        "total": number("total", "totalCount", "caseCount"),
        "passed": number("passed", "passCount", "successCount"),
        "failed": number("failed", "failCount", "errorCount"),
    }


def _refresh_running_execution(record: Dict[str, Any]) -> Dict[str, Any]:
    run_id = str(record.get("run_id") or "").strip()
    cfg = _load_raw_config()
    path = str(cfg.get("run_status_path") or "").strip()
    path = path.replace("{run_id}", clean_id(run_id, "ms_run")).replace("{runId}", clean_id(run_id, "ms_run"))
    if not run_id or not path:
        return record
    result = _request_json("GET", path, timeout=30)
    if not result.get("ok"):
        record["status_poll_failures"] = int(record.get("status_poll_failures") or 0) + 1
        if record["status_poll_failures"] in {1, 3}:
            _append_execution_event(
                record,
                "metersphere_run",
                "running",
                "MeterSphere 状态暂时不可用，保留当前运行状态",
                result,
            )
        _save_execution(record)
        return record
    payload = _remote_run_payload(result)
    state = _normalize_remote_run_state(
        payload.get("status") or payload.get("state") or payload.get("resultStatus")
    )
    previous_state = str(record.get("remote_status") or "")
    record["remote_status"] = state
    record["stats"] = _remote_run_stats(payload)
    record["status_poll_failures"] = 0
    record["unchanged_polls"] = (
        int(record.get("unchanged_polls") or 0) + 1 if state == previous_state else 0
    )
    if state == "running":
        _save_execution(record)
        return record
    if state == "failed":
        _fail_execution(record, "metersphere_run", "MeterSphere 执行失败", payload)
        return record
    _set_phase(record, "metersphere_run", "succeeded", "MeterSphere 执行完成")
    _append_execution_event(record, "metersphere_run", "succeeded", "MeterSphere 返回执行成功", payload)
    _set_phase(record, "sync_report", "running", "正在同步执行报告")
    record["report_status"] = "running"
    _save_execution(record)
    try:
        report_result = pull_metersphere_report(run_id)
    except Exception as exc:
        safe_detail = sanitize_metersphere_data({"error": str(exc)})
        report_result = {
            "ok": False,
            "error": f"MeterSphere 报告同步异常：{safe_detail.get('error') or '未知异常'}",
        }
    record = _load_execution(str(record.get("execution_id") or ""))
    if not report_result.get("ok"):
        record["report_status"] = "failed"
        _fail_execution(
            record,
            "sync_report",
            str(report_result.get("error") or "MeterSphere 报告同步失败"),
            report_result,
        )
        return record
    report = report_result.get("report") if isinstance(report_result.get("report"), dict) else {}
    record["report_id"] = str(report.get("report_id") or report.get("id") or "")
    record["report_status"] = "succeeded"
    _set_phase(record, "sync_report", "succeeded", "执行报告已同步")
    _append_execution_event(record, "sync_report", "succeeded", "MeterSphere 报告同步完成", report)
    record["status"] = "succeeded"
    record["finished_at"] = _now()
    record["error"] = ""
    _save_execution(record)
    return record


def get_metersphere_execution(execution_id: str, refresh: bool = True) -> Dict[str, Any]:
    record = _load_execution(str(execution_id or "").strip())
    if not record:
        raise MeterSphereExecutionNotFound("MeterSphere execution_id 不存在")
    if (
        refresh
        and record.get("status") not in TERMINAL_EXECUTION_STATES
        and _phase(record, "metersphere_run").get("state") == "running"
    ):
        with _EXECUTION_LOCK:
            record = _refresh_running_execution(record)
    return _public_execution(record)


def _meter_payload_for_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _load_raw_config()
    all_cases = [case for case in (plan.get("cases") or []) if isinstance(case, dict)]
    executable_cases = api_test_plan_service.executable_api_cases(plan)
    return {
        "workspaceId": cfg.get("workspace_id"),
        "projectId": cfg.get("project_id"),
        "environmentId": cfg.get("environment_id"),
        "source": "midscene-task-platform",
        "contractVersion": "api_case_contract/v1",
        "planId": plan.get("plan_id"),
        "name": plan.get("name"),
        "totalCaseCount": len(all_cases),
        "executableCaseCount": len(executable_cases),
        "excludedCaseCount": len(all_cases) - len(executable_cases),
        "cases": executable_cases,
    }


def push_plan_to_metersphere(plan_id: str) -> Dict[str, Any]:
    try:
        plan = _execution_plan(plan_id)
    except MeterSphereExecutionValidationError as exc:
        return {
            "ok": False,
            "requires_confirmation": "确认" in str(exc),
            "requires_review": True,
            "error": str(exc),
        }
    cfg = _load_raw_config()
    if not cfg.get("case_push_path"):
        return {"ok": False, "requires_config": True, "error": "MeterSphere 用例推送 API 路径未配置"}
    payload = _meter_payload_for_plan(plan)
    result = sanitize_metersphere_data(
        _request_json("POST", cfg.get("case_push_path"), payload, timeout=60)
    )
    push_id = unique_millis_id("ms_push")
    record = {
        "push_id": push_id,
        "plan_id": plan_id,
        "created_at": _now(),
        "request": {**payload, "cases": [{"case_id": case.get("case_id"), "name": case.get("name")} for case in payload.get("cases", [])]},
        "result": result,
    }
    write_json_file(_push_path(push_id), record)
    result["push_id"] = push_id
    return result


def create_metersphere_run(plan_id: str, test_plan_id: str = "") -> Dict[str, Any]:
    try:
        _execution_plan(plan_id)
    except MeterSphereExecutionValidationError as exc:
        return {"ok": False, "requires_review": True, "error": str(exc), "run_id": ""}
    cfg = _load_raw_config()
    if not cfg.get("plan_run_path"):
        return {"ok": False, "requires_config": True, "error": "MeterSphere 测试计划执行 API 路径未配置"}
    payload = {
        "workspaceId": cfg.get("workspace_id"),
        "projectId": cfg.get("project_id"),
        "environmentId": cfg.get("environment_id"),
        "sourcePlanId": plan_id,
        "testPlanId": test_plan_id,
    }
    result = sanitize_metersphere_data(
        _request_json("POST", cfg.get("plan_run_path"), payload, timeout=60)
    )
    run_id = _remote_run_id(result)
    request_id = run_id or unique_millis_id("ms_run_request")
    record = {
        "request_id": request_id,
        "run_id": run_id,
        "plan_id": plan_id,
        "created_at": _now(),
        "request": payload,
        "result": result,
    }
    write_json_file(_run_path(request_id), record)
    if result.get("ok") and not run_id:
        return {
            **result,
            "ok": False,
            "run_id": "",
            "request_id": request_id,
            "error": "MeterSphere 计划已响应，但未返回真实 run_id",
        }
    result["run_id"] = run_id
    result["request_id"] = request_id
    return result


def pull_metersphere_report(run_id: str, raw_report: Dict[str, Any] | None = None) -> Dict[str, Any]:
    from task_server.services import api_report_service

    raw = sanitize_metersphere_data(raw_report) if isinstance(raw_report, dict) else None
    if raw is None:
        cfg = _load_raw_config()
        report_path = str(cfg.get("report_path") or "").replace("{run_id}", clean_id(run_id, "ms_run"))
        if not report_path:
            return {"ok": False, "requires_config": True, "error": "MeterSphere 报告 API 路径未配置"}
        fetched = _request_json("GET", report_path, timeout=60)
        if not fetched.get("ok"):
            return fetched
        raw = sanitize_metersphere_data(fetched)
    report = api_report_service.normalize_metersphere_report(run_id, raw)
    saved = api_report_service.save_api_report(sanitize_metersphere_data(report))
    return {"ok": True, "report": sanitize_metersphere_data(saved)}


__all__ = [
    "API_TESTING_DIR",
    "EXECUTION_PHASES",
    "MeterSphereExecutionConflict",
    "MeterSphereExecutionNotFound",
    "MeterSphereExecutionValidationError",
    "save_metersphere_config",
    "metersphere_config",
    "metersphere_health",
    "sanitize_metersphere_data",
    "list_metersphere_projects",
    "list_metersphere_environments",
    "metersphere_execution_context",
    "start_metersphere_execution",
    "get_metersphere_execution",
    "list_metersphere_executions",
    "push_plan_to_metersphere",
    "create_metersphere_run",
    "pull_metersphere_report",
]
