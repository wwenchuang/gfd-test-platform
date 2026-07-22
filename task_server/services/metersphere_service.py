"""MeterSphere adapter for the API testing workspace."""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import time
import urllib.error
import urllib.request
from typing import Any, Dict

from task_server.storage import clean_id, read_json_file, safe_join, unique_millis_id, write_json_file
from task_server.services import api_asset_service, api_test_plan_service


API_TESTING_DIR = api_asset_service.API_TESTING_DIR


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


def _env_config() -> Dict[str, Any]:
    return {
        "base_url": os.getenv("METERSPHERE_BASE_URL", "").strip(),
        "token": os.getenv("METERSPHERE_TOKEN", "").strip(),
        "access_key": os.getenv("METERSPHERE_ACCESS_KEY", "").strip(),
        "secret_key": os.getenv("METERSPHERE_SECRET_KEY", "").strip(),
        "workspace_id": os.getenv("METERSPHERE_WORKSPACE_ID", "").strip(),
        "project_id": os.getenv("METERSPHERE_PROJECT_ID", "").strip(),
        "environment_id": os.getenv("METERSPHERE_ENVIRONMENT_ID", "").strip(),
        "health_path": os.getenv("METERSPHERE_HEALTH_PATH", "/api/health").strip() or "/api/health",
        "case_push_path": os.getenv("METERSPHERE_CASE_PUSH_PATH", "").strip(),
        "plan_run_path": os.getenv("METERSPHERE_PLAN_RUN_PATH", "").strip(),
        "report_path": os.getenv("METERSPHERE_REPORT_PATH", "").strip(),
    }


def _load_raw_config() -> Dict[str, Any]:
    file_config = read_json_file(_config_path(), default={}) or {}
    if not isinstance(file_config, dict):
        file_config = {}
    merged = _env_config()
    merged.update({key: value for key, value in file_config.items() if value not in (None, "")})
    merged["base_url"] = str(merged.get("base_url") or "").strip().rstrip("/")
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
    if masked:
        cfg["token"] = _mask_secret(cfg.get("token"))
        cfg["access_key"] = _mask_secret(cfg.get("access_key"))
        cfg["secret_key"] = _mask_secret(cfg.get("secret_key"))
    return cfg


def _looks_like_masked_secret(value: Any, current: Any) -> bool:
    text = str(value or "").strip()
    return bool(current) and ("***" in text or text == "******")


def save_metersphere_config(payload: Dict[str, Any]) -> Dict[str, Any]:
    current = _load_raw_config()
    allowed = {
        "base_url",
        "token",
        "access_key",
        "secret_key",
        "workspace_id",
        "project_id",
        "environment_id",
        "health_path",
        "case_push_path",
        "plan_run_path",
        "report_path",
    }
    next_cfg: Dict[str, Any] = {}
    for key in allowed:
        value = payload.get(key, current.get(key, ""))
        if key in {"token", "access_key", "secret_key"} and _looks_like_masked_secret(value, current.get(key, "")):
            value = current.get(key, "")
        next_cfg[key] = str(value or "").strip()
    next_cfg["base_url"] = next_cfg.get("base_url", "").rstrip("/")
    if not next_cfg.get("health_path"):
        next_cfg["health_path"] = "/api/health"
    next_cfg["updated_at"] = _now()
    write_json_file(_config_path(), next_cfg)
    return metersphere_config(masked=True)


def _metersphere_auth_headers(
    cfg: Dict[str, Any],
    method: str = "GET",
    path: str = "",
    payload: Dict[str, Any] | None = None,
) -> Dict[str, str]:
    access_key = str(cfg.get("access_key") or "").strip()
    secret_key = str(cfg.get("secret_key") or "").strip()
    if access_key:
        timestamp = str(int(time.time() * 1000))
        normalized_path = "/" + str(path or "").lstrip("/")
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True) if payload is not None else ""
        canonical = "\n".join([str(method or "GET").upper(), normalized_path, access_key, timestamp, body])
        signature = hmac.new(secret_key.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
        return {"accessKey": access_key, "timestamp": timestamp, "signature": signature}
    token = str(cfg.get("token") or "").strip()
    if token:
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
        return parsed
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")[:500]
        except Exception:
            pass
        return {"ok": False, "http_status": exc.code, "error": f"MeterSphere HTTP {exc.code}: {body}"}
    except Exception as exc:
        return {"ok": False, "error": f"MeterSphere 请求失败：{exc}"}


def metersphere_health() -> Dict[str, Any]:
    cfg = _load_raw_config()
    if not cfg.get("base_url"):
        return {"ok": False, "configured": False, "error": "MeterSphere base_url 未配置"}
    health_path = cfg.get("health_path") or "/api/health"
    if cfg.get("access_key") and cfg.get("project_id") and str(health_path).strip("/") == "api/health":
        health_path = f"/project/get/{cfg.get('project_id')}"
    result = _request_json("GET", health_path, timeout=10)
    result["configured"] = True
    result["base_url"] = cfg.get("base_url")
    result["token_configured"] = bool(cfg.get("token"))
    result["access_key_configured"] = bool(cfg.get("access_key"))
    result["secret_key_configured"] = bool(cfg.get("secret_key"))
    return result


def _meter_payload_for_plan(plan: Dict[str, Any]) -> Dict[str, Any]:
    cfg = _load_raw_config()
    return {
        "workspaceId": cfg.get("workspace_id"),
        "projectId": cfg.get("project_id"),
        "environmentId": cfg.get("environment_id"),
        "source": "midscene-task-platform",
        "planId": plan.get("plan_id"),
        "name": plan.get("name"),
        "cases": plan.get("cases") or [],
    }


def push_plan_to_metersphere(plan_id: str) -> Dict[str, Any]:
    plan = api_test_plan_service.get_api_test_plan(plan_id)
    if not plan:
        return {"ok": False, "error": "API 测试计划不存在"}
    if plan.get("status") != "confirmed":
        return {"ok": False, "requires_confirmation": True, "error": "推送 MeterSphere 前必须先确认 API 测试计划"}
    cfg = _load_raw_config()
    if not cfg.get("case_push_path"):
        return {"ok": False, "requires_config": True, "error": "MeterSphere 用例推送 API 路径未配置"}
    payload = _meter_payload_for_plan(plan)
    result = _request_json("POST", cfg.get("case_push_path"), payload, timeout=60)
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
    result = _request_json("POST", cfg.get("plan_run_path"), payload, timeout=60)
    run_id = str(result.get("run_id") or result.get("runId") or result.get("id") or unique_millis_id("ms_run"))
    record = {"run_id": run_id, "plan_id": plan_id, "created_at": _now(), "request": payload, "result": result}
    write_json_file(_run_path(run_id), record)
    result["run_id"] = run_id
    return result


def pull_metersphere_report(run_id: str, raw_report: Dict[str, Any] | None = None) -> Dict[str, Any]:
    from task_server.services import api_report_service

    raw = raw_report if isinstance(raw_report, dict) else None
    if raw is None:
        cfg = _load_raw_config()
        report_path = str(cfg.get("report_path") or "").replace("{run_id}", clean_id(run_id, "ms_run"))
        if not report_path:
            return {"ok": False, "requires_config": True, "error": "MeterSphere 报告 API 路径未配置"}
        fetched = _request_json("GET", report_path, timeout=60)
        if not fetched.get("ok"):
            return fetched
        raw = fetched
    report = api_report_service.normalize_metersphere_report(run_id, raw)
    saved = api_report_service.save_api_report(report)
    return {"ok": True, "report": saved}


__all__ = [
    "API_TESTING_DIR",
    "save_metersphere_config",
    "metersphere_config",
    "metersphere_health",
    "push_plan_to_metersphere",
    "create_metersphere_run",
    "pull_metersphere_report",
]
