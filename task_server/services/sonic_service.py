"""Sonic 集成服务层 — 全量迁移自 ``midscene-upload.py``（Task 54）。

模块职责
---------
- Sonic API URL / 鉴权 / Token 缓存与登录
- 项目 / 测试套 / 执行结果（带 TTL 缓存）的只读查询
- 同步状态文件（``sonic-sync.json``）的读写
- 单条 / 批量用例发布的统一入口
- Sonic 回调与测试套完成事件的归一化处理
- 套件结果收集、汇总报告、飞书通知
- Groovy 桥接脚本生成与步骤同步
- 用例扫描、迁移、状态检查

设计要点
---------
- 所有路径常量、锁、Token 缓存文件来自 :mod:`task_server.config`
- I/O 全部走 :mod:`task_server.storage` 的原子写与 TTL 读
- 任何对外返回的对象都不会包含 ``token`` / ``password`` 等敏感字段。
"""

from __future__ import annotations

import base64
import html as html_lib
import json
import os
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

from .. import config as cfg
from ..config import JOB_LOCK, SONIC_NOTIFY_LOG_FILE, SONIC_SUITE_LOCK
from ..storage import (
    invalidate_json_cache,
    read_json_cached,
    read_json_file,
    read_text_file,
    safe_join,
    unique_millis_id,
    write_json_file,
    write_text_file,
)


# ---------------------------------------------------------------------------
# 通用工具函数（自包含，不依赖 midscene-upload.py）
# ---------------------------------------------------------------------------

def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(str(value).strip())
    except Exception:
        return default


def _safe_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on", "是", "开启"):
        return True
    if text in ("0", "false", "no", "n", "off", "否", "关闭"):
        return False
    return default


def _parse_time(value: str) -> int:
    """将时间字符串解析为 epoch 秒。"""
    if not value:
        return 0
    try:
        return int(time.mktime(time.strptime(value, "%Y-%m-%d %H:%M:%S")))
    except Exception:
        return 0


def _extract_page_items(data: Any) -> list:
    """从 Sonic 分页响应中提取列表项。"""
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("records", "content", "list", "items"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def _env_key_for_package(prefix: str, package: str) -> str:
    return prefix + re.sub(r"[^A-Z0-9]", "_", (package or "").upper())


def _dedupe_keep_order(items: list) -> list:
    result = []
    seen = set()
    for item in items or []:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


# ---------------------------------------------------------------------------
# 内存缓存（项目 / 测试套 / 单条 result）
# ---------------------------------------------------------------------------

_PROJECT_CACHE_TTL = 60
_SUITE_CACHE_TTL = 30
_RESULT_CACHE_TTL = 5

_MEM_CACHE_LOCK = threading.Lock()
_MEM_CACHE: Dict[str, Tuple[float, Any]] = {}

# 登录状态
SONIC_LOGIN_STATE: Dict[str, Any] = {
    "attempted_at": "",
    "ok": None,
    "error": "",
}

# Groovy 桥接脚本 runner token：优先从环境变量读取，与服务端认证 token 保持一致
_BRIDGE_RUNNER_TOKEN = (os.getenv("MIDSCENE_RUNNER_TOKEN") or "midscene2026").strip()


def _cache_get(key: str, ttl: float) -> Optional[Any]:
    now = time.time()
    with _MEM_CACHE_LOCK:
        entry = _MEM_CACHE.get(key)
        if entry and (now - entry[0]) < ttl:
            return entry[1]
    return None


def _cache_set(key: str, value: Any) -> None:
    with _MEM_CACHE_LOCK:
        _MEM_CACHE[key] = (time.time(), value)


def _cache_invalidate(prefix: str = "") -> None:
    with _MEM_CACHE_LOCK:
        if not prefix:
            _MEM_CACHE.clear()
            return
        stale = [k for k in _MEM_CACHE if k.startswith(prefix)]
        for k in stale:
            _MEM_CACHE.pop(k, None)


# ---------------------------------------------------------------------------
# URL / 基础地址
# ---------------------------------------------------------------------------

def sonic_base_url() -> str:
    return (
        os.getenv("SONIC_BASE_URL")
        or os.getenv("SONIC_URL")
        or "http://101.34.197.12:3000"
    ).rstrip("/")


def sonic_api_prefix() -> str:
    return os.getenv("SONIC_API_PREFIX", "/server/api/controller").rstrip("/")


def sonic_url(path: str, params: Optional[Dict[str, Any]] = None) -> str:
    path = "/" + str(path or "").lstrip("/")
    url = sonic_base_url() + sonic_api_prefix() + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    return url


def sonic_result_url(base_url: str, project_id: Any, result_id: Any) -> str:
    """构建 Sonic 结果详情页 URL。"""
    base = str(base_url or "").rstrip("/")
    if not base or not project_id or not result_id:
        return ""
    return f"{base}/Home/{project_id}/ResultDetail/{result_id}"


# ---------------------------------------------------------------------------
# Token 与登录
# ---------------------------------------------------------------------------

def _jwt_expire_ts(token: str) -> int:
    try:
        parts = str(token or "").split(".")
        if len(parts) < 2:
            return 0
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = base64.urlsafe_b64decode(payload.encode("utf-8")).decode(
            "utf-8", errors="replace"
        )
        data = json.loads(decoded)
        return _safe_int(data.get("exp"), 0)
    except Exception:
        return 0


def _sonic_env_token() -> str:
    for key in ("SONIC_TOKEN", "SONIC_TOKEN_2_7_2", "SONICTOKEN", "SONIC_JWT"):
        value = os.getenv(key)
        if value:
            return value.strip().strip("\"'")
    return ""


def sonic_env_token() -> str:
    """公共别名 → :func:`_sonic_env_token`。"""
    return _sonic_env_token()


def _sonic_login_credentials() -> Tuple[str, str]:
    username = (
        os.getenv("SONIC_USERNAME")
        or os.getenv("SONIC_USER")
        or os.getenv("SONIC_LOGIN_USER")
        or ""
    ).strip().strip("\"'")
    password = (
        os.getenv("SONIC_PASSWORD")
        or os.getenv("SONIC_PASS")
        or os.getenv("SONIC_LOGIN_PASSWORD")
        or ""
    ).strip().strip("\"'")
    return username, password


def sonic_login_credentials() -> Tuple[str, str]:
    """公共别名 → :func:`_sonic_login_credentials`。"""
    return _sonic_login_credentials()


def _sonic_cached_token(expected_username: str = "") -> str:
    try:
        data = read_json_file(cfg.SONIC_TOKEN_CACHE_FILE, default={}) or {}
        token = str(data.get("token") or "").strip()
        if not token:
            return ""
        cached_username = str(data.get("username") or "").strip()
        if expected_username and cached_username and cached_username != expected_username:
            return ""
        exp = _safe_int(data.get("exp") or _jwt_expire_ts(token), 0)
        if exp and exp <= int(time.time()) + 120:
            return ""
        return token
    except Exception:
        return ""


def sonic_cached_token(expected_username: str = "") -> str:
    """公共别名 → :func:`_sonic_cached_token`。"""
    return _sonic_cached_token(expected_username=expected_username)


def sonic_login() -> str:
    """使用 ``SONIC_USERNAME/SONIC_PASSWORD`` 登录获取 Token，并写入缓存文件。"""
    username, password = _sonic_login_credentials()
    if not username or not password:
        SONIC_LOGIN_STATE.update({
            "attempted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ok": False,
            "error": "未配置 SONIC_USERNAME/SONIC_PASSWORD",
        })
        return ""
    body = json.dumps(
        {"userName": username, "password": password}, ensure_ascii=False
    ).encode("utf-8")
    req = urllib.request.Request(
        sonic_url("/users/login"),
        data=body,
        headers={
            "Accept": "*/*",
            "Accept-Language": "zh_CN",
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": "Mozilla/5.0 (MidsceneTaskManager Sonic integration)",
        },
        method="POST",
    )
    with cfg.SONIC_LOCK:
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw else {}
            token = ""
            if isinstance(parsed, dict):
                token = str(parsed.get("data") or "").strip()
                if parsed.get("code") not in (None, 2000) or not token:
                    raise RuntimeError(
                        sonic_response_error_message(parsed) or "Sonic 登录失败"
                    )
            exp = _jwt_expire_ts(token)
            try:
                os.makedirs(cfg.LEARNING_DIR, exist_ok=True)
                write_text_file(
                    cfg.SONIC_TOKEN_CACHE_FILE,
                    json.dumps(
                        {
                            "token": token,
                            "username": username,
                            "exp": exp,
                            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                )
            except Exception:
                pass
            SONIC_LOGIN_STATE.update({
                "attempted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "ok": True,
                "error": "",
            })
            return token
        except Exception as exc:
            SONIC_LOGIN_STATE.update({
                "attempted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "ok": False,
                "error": str(exc),
            })
            _append_notify_log(
                "sonic_login_failed", {"token_source": "login"}, error=str(exc)
            )
            return ""


def sonic_login_token() -> str:
    """使用账号密码登录获取 Token，失败时抛出异常（不吞错误）。

    与 :func:`sonic_login` 类似，但不会捕获异常——适合需要区分“登录失败”
    与“登录成功但返回空”的调用方。
    """
    username, password = _sonic_login_credentials()
    if not username or not password:
        return ""
    body = json.dumps(
        {"userName": username, "password": password}, ensure_ascii=False
    ).encode("utf-8")
    req = urllib.request.Request(
        sonic_url("/users/login"),
        data=body,
        headers={
            "Accept": "*/*",
            "Accept-Language": "zh_CN",
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": "Mozilla/5.0 (MidsceneTaskManager Sonic integration)",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    parsed = json.loads(raw) if raw else {}
    token = ""
    if isinstance(parsed, dict):
        token = str(parsed.get("data") or "").strip()
        if parsed.get("code") not in (None, 2000) or not token:
            raise RuntimeError(
                sonic_response_error_message(parsed) or "Sonic 登录失败"
            )
    exp = _jwt_expire_ts(token)
    try:
        os.makedirs(cfg.LEARNING_DIR, exist_ok=True)
        write_text_file(
            cfg.SONIC_TOKEN_CACHE_FILE,
            json.dumps(
                {
                    "token": token,
                    "username": username,
                    "exp": exp,
                    "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
    except Exception:
        pass
    SONIC_LOGIN_STATE.update({
        "attempted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ok": True,
        "error": "",
    })
    return token


def sonic_get_token(force: bool = False) -> str:
    """获取可用的 Sonic Token；优先级：登录缓存 → 环境变量 → 文件缓存 → 重新登录。"""
    username, password = _sonic_login_credentials()
    login_configured = bool(username and password)

    if login_configured:
        if not force:
            cached = _sonic_cached_token(expected_username=username)
            if cached:
                return cached
        try:
            token = sonic_login()
            if token:
                return token
        except Exception as e:
            SONIC_LOGIN_STATE.update({
                "attempted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "ok": False,
                "error": str(e),
            })
            try:
                _append_notify_log("sonic_login_failed", {"token_source": "login"}, error=str(e))
            except Exception:
                pass
            # Compatibility fallback
            if not force:
                env_token = _sonic_env_token()
                if env_token:
                    exp = _jwt_expire_ts(env_token)
                    if not exp or exp > int(time.time()) + 120:
                        return env_token
            return ""

    if not force:
        env_token = _sonic_env_token()
        if env_token:
            exp = _jwt_expire_ts(env_token)
            if not exp or exp > int(time.time()) + 120:
                return env_token
        cached = _sonic_cached_token()
        if cached:
            return cached
    try:
        return sonic_login()
    except Exception as e:
        SONIC_LOGIN_STATE.update({
            "attempted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ok": False,
            "error": str(e),
        })
        try:
            _append_notify_log("sonic_login_failed", {"token_source": _sonic_token_source(include_login=False)}, error=str(e))
        except Exception:
            pass
        return ""


def sonic_token(force_refresh: bool = False) -> str:
    """公共别名 → :func:`sonic_get_token`。"""
    return sonic_get_token(force=force_refresh)


def _sonic_token_source(include_login: bool = True) -> str:
    username, password = _sonic_login_credentials()
    if include_login and username and password:
        if SONIC_LOGIN_STATE.get("ok") is False and _sonic_env_token():
            return "static_token_fallback"
        if _sonic_cached_token(expected_username=username):
            return "login_cache"
        return "login"
    for key in ("SONIC_TOKEN", "SONIC_TOKEN_2_7_2", "SONICTOKEN", "SONIC_JWT"):
        value = os.getenv(key)
        if value and value.strip().strip("\"'"):
            return key
    if include_login and _sonic_cached_token():
        return "cache"
    return ""


def sonic_token_source(include_login: bool = True) -> str:
    """公共别名 → :func:`_sonic_token_source`。"""
    return _sonic_token_source(include_login=include_login)


def sonic_auth_preview() -> Dict[str, Any]:
    """返回前端可见的认证状态预览，**不暴露** token / password 原文。"""
    username, password = _sonic_login_credentials()
    login_configured = bool(username and password)
    static_token_configured = bool(_sonic_env_token())
    return {
        "login_configured": login_configured,
        "static_token_configured": static_token_configured,
        "preferred_source": (
            "login" if login_configured else ("static_token" if static_token_configured else "")
        ),
        "active_source": _sonic_token_source(),
        "login_attempted_at": SONIC_LOGIN_STATE.get("attempted_at", ""),
        "login_ok": SONIC_LOGIN_STATE.get("ok"),
        "login_error": SONIC_LOGIN_STATE.get("error", ""),
    }


def sonic_token_fingerprint() -> str:
    token = sonic_get_token()
    if not token:
        return ""
    return f"{token[:8]}...{token[-8:]} len={len(token)}"


# ---------------------------------------------------------------------------
# HTTP 请求（带 token 失效自动重试）
# ---------------------------------------------------------------------------

def _sonic_referer(path: str, params: Optional[Dict[str, Any]] = None) -> str:
    params = params or {}
    project_id = _safe_int(params.get("projectId") or params.get("project_id"), 0)
    if not project_id:
        return ""
    path = "/" + str(path or "").lstrip("/")
    if path.startswith("/results/"):
        page = "Results"
    elif path.startswith("/testCases/") or path.startswith("/steps/") or path.startswith("/modules/"):
        page = "TestCase"
    else:
        page = "Results"
    return f"{sonic_base_url()}/Home/{project_id}/{page}"


def sonic_referer_for_request(path: str, params: Optional[Dict[str, Any]] = None) -> str:
    """公共别名 → :func:`_sonic_referer`。"""
    return _sonic_referer(path, params)


def _sonic_headers(
    has_body: bool = False,
    path: str = "",
    params: Optional[Dict[str, Any]] = None,
    extra: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    headers = {
        "Accept": "*/*",
        "Accept-Language": "zh_CN",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "User-Agent": "Mozilla/5.0 (MidsceneTaskManager Sonic integration)",
    }
    token = sonic_get_token()
    if token:
        headers["SonicToken"] = token
    referer = _sonic_referer(path, params)
    if referer:
        headers["Referer"] = referer
    if has_body:
        headers["Content-Type"] = "application/json;charset=UTF-8"
    if extra:
        headers.update(extra)
    return headers


def sonic_headers(extra: Optional[Dict[str, str]] = None, has_body: bool = False, path: str = "", params: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    """公共别名 → :func:`_sonic_headers`。"""
    return _sonic_headers(has_body=has_body, path=path, params=params, extra=extra)


def _sonic_response_auth_status(resp: Any) -> str:
    if not isinstance(resp, dict):
        return "unknown"
    code = resp.get("code")
    message = str(resp.get("message") or resp.get("msg") or "").lower()
    if code in (None, 2000):
        return "ok"
    if code == 1001 or message == "unauthorized":
        return "token_invalid"
    if code == 1003 or "permission" in message or "暂无权限" in message:
        return "permission_denied"
    if code == 1004 or "resource" in message or "uri" in message:
        return "resource_not_found"
    return "error"


def sonic_response_auth_status(resp: Any) -> str:
    """公共别名 → :func:`_sonic_response_auth_status`。"""
    return _sonic_response_auth_status(resp)


def sonic_response_error_message(resp: Any) -> str:
    if not isinstance(resp, dict):
        return str(resp)
    code = resp.get("code")
    message = resp.get("message") or resp.get("msg") or ""
    if code == 1001 or str(message).lower() == "unauthorized":
        return _sonic_auth_failure_message()
    if code == 1003 or "暂无权限" in str(message) or "permission" in str(message).lower():
        return "Sonic token 有效，但当前账号角色没有该接口资源权限"
    if code == 1004 or "resource" in str(message).lower() or "uri" in str(message).lower():
        return "Sonic 资源表未找到该接口，请在 Sonic 资源管理中同步资源"
    return message or f"Sonic 返回异常：{resp}"


def _sonic_auth_failure_message() -> str:
    auth = sonic_auth_preview()
    if auth.get("login_configured") and auth.get("login_error"):
        detail = re.sub(r"\s+", " ", str(auth.get("login_error") or "")).strip()
        return f"Sonic 自动登录失败：{detail[:240]}；请确认服务进程已加载 SONIC_USERNAME/SONIC_PASSWORD 且 Sonic 登录网关可访问"
    if auth.get("login_configured"):
        return "Sonic 鉴权失败：已配置账号密码自动登录，但生成的 Token 未通过校验，请运行 Sonic 诊断查看登录结果"
    return "Sonic 鉴权失败：未检测到可用自动登录配置，请在服务进程配置 SONIC_USERNAME/SONIC_PASSWORD"


def sonic_auth_failure_message() -> str:
    """公共别名 → :func:`_sonic_auth_failure_message`。"""
    return _sonic_auth_failure_message()


def _sonic_response_data(resp: Any) -> Any:
    if not isinstance(resp, dict):
        return None
    if resp.get("code") not in (None, 2000):
        raise RuntimeError(sonic_response_error_message(resp))
    return resp.get("data")


def sonic_response_data(resp: Any) -> Any:
    """公共别名 → :func:`_sonic_response_data`。"""
    return _sonic_response_data(resp)


def sonic_request(
    method: str,
    path: str,
    params: Optional[Dict[str, Any]] = None,
    body: Optional[Dict[str, Any]] = None,
    timeout: float = 20,
) -> Any:
    """对 Sonic 发起请求；token 失效自动刷新一次后重试。"""
    if not sonic_base_url():
        raise ValueError("未配置 SONIC_BASE_URL")
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    header_params = dict(params or {})
    if isinstance(body, dict):
        for key in ("projectId", "project_id"):
            if key in body and key not in header_params:
                header_params[key] = body.get(key)
    headers = _sonic_headers(has_body=body is not None, path=path, params=header_params)
    req = urllib.request.Request(
        sonic_url(path, params=params),
        data=data,
        headers=headers,
        method=method.upper(),
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw) if raw else {}
        if _sonic_response_auth_status(parsed) == "token_invalid":
            refreshed = sonic_get_token(force=True)
            if refreshed:
                retry_headers = _sonic_headers(
                    has_body=body is not None, path=path, params=header_params
                )
                retry_headers["SonicToken"] = refreshed
                retry_req = urllib.request.Request(
                    sonic_url(path, params=params),
                    data=data,
                    headers=retry_headers,
                    method=method.upper(),
                )
                with urllib.request.urlopen(retry_req, timeout=timeout) as retry_resp:
                    retry_raw = retry_resp.read().decode("utf-8", errors="replace")
                return json.loads(retry_raw) if retry_raw else {}
        return parsed
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace") if exc.fp else ""
        raise RuntimeError(f"Sonic HTTP {exc.code} {path}: {body_text[:1000]}")
    except Exception as exc:
        raise RuntimeError(f"Sonic 请求失败 {path}：{exc}")


def sonic_safe_request_shape(path: str, params: Optional[Dict[str, Any]] = None, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    headers = _sonic_headers(has_body=body is not None, path=path, params=params or {})
    return {
        "url": sonic_url(path, params=params),
        "header_names": sorted(headers.keys()),
        "has_token": bool(headers.get("SonicToken")),
        "token_source": _sonic_token_source(),
        "token_fingerprint": sonic_token_fingerprint(),
    }


# ---------------------------------------------------------------------------
# 健康检查 / 诊断
# ---------------------------------------------------------------------------

def sonic_health() -> Dict[str, Any]:
    """连接健康检查：拉取项目列表第一页，记录耗时与认证状态。"""
    started = time.time()
    info: Dict[str, Any] = {
        "ok": False,
        "base_url": sonic_base_url(),
        "auth": sonic_auth_preview(),
    }
    try:
        resp = sonic_request("GET", "/projects/list", timeout=10)
        data = _sonic_response_data(resp) or []
        if isinstance(data, dict):
            data = data.get("data") or []
        info["ok"] = True
        info["project_count"] = len(data) if isinstance(data, list) else 0
    except Exception as exc:
        info["error"] = str(exc)
    info["elapsed_ms"] = int((time.time() - started) * 1000)
    return info


def sonic_probe_token() -> Dict[str, Any]:
    path = "/users"
    try:
        resp = sonic_request("GET", path, timeout=10)
        status = _sonic_response_auth_status(resp)
        data = resp.get("data") if isinstance(resp, dict) else None
        user = {}
        if isinstance(data, dict):
            user = {
                "id": data.get("id"),
                "userName": data.get("userName"),
                "role": data.get("role"),
                "roleName": data.get("roleName"),
            }
        return {
            "path": path,
            "ok": status == "ok",
            "code": resp.get("code") if isinstance(resp, dict) else None,
            "message": resp.get("message") if isinstance(resp, dict) else "",
            "auth_status": status,
            "error": "" if status == "ok" else sonic_response_error_message(resp),
            "user": user,
            "request": sonic_safe_request_shape(path),
        }
    except Exception as e:
        return {
            "path": path,
            "ok": False,
            "auth_status": "request_error",
            "error": str(e),
            "request": sonic_safe_request_shape(path),
        }


def sonic_probe_endpoint(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    params = params or {}
    try:
        resp = sonic_request("GET", path, params=params, timeout=10)
        status = _sonic_response_auth_status(resp)
        data = resp.get("data") if isinstance(resp, dict) else None
        if status != "ok":
            return {
                "path": path,
                "params": params,
                "ok": False,
                "code": resp.get("code") if isinstance(resp, dict) else None,
                "message": resp.get("message") if isinstance(resp, dict) else "",
                "auth_status": status,
                "error": sonic_response_error_message(resp),
                "request": sonic_safe_request_shape(path, params=params),
            }
        count = 0
        if isinstance(data, list):
            count = len(data)
        elif isinstance(data, dict):
            count = _safe_int(data.get("total") or data.get("totalElements") or data.get("totalCount"), 0)
            if not count:
                count = len(_extract_page_items(data))
        return {
            "path": path,
            "params": params,
            "ok": True,
            "code": resp.get("code"),
            "message": resp.get("message") or resp.get("msg") or "",
            "auth_status": status,
            "data_type": type(data).__name__,
            "count": count,
            "request": sonic_safe_request_shape(path, params=params),
        }
    except Exception as e:
        return {
            "path": path,
            "params": params,
            "ok": False,
            "auth_status": "request_error",
            "error": str(e),
            "request": sonic_safe_request_shape(path, params=params),
        }


# ---------------------------------------------------------------------------
# 项目 / 测试套 / 结果（带缓存）
# ---------------------------------------------------------------------------

def _safe_project_dto(project: Any) -> Dict[str, Any]:
    if not isinstance(project, dict):
        return {}
    return {
        "id": project.get("id"),
        "name": project.get("projectName") or project.get("name") or "",
        "description": project.get("projectDes") or project.get("description") or "",
    }


def _safe_suite_dto(suite: Any) -> Dict[str, Any]:
    if not isinstance(suite, dict):
        return {}
    return {
        "id": suite.get("id"),
        "name": suite.get("name") or "",
        "projectId": suite.get("projectId"),
        "platform": suite.get("platform"),
        "caseCount": len(suite.get("testCases") or []),
    }


def sonic_list_projects(force: bool = False) -> Dict[str, Any]:
    """Sonic 项目列表（缓存 60 秒）。"""
    cache_key = "projects"
    if not force:
        cached = _cache_get(cache_key, _PROJECT_CACHE_TTL)
        if cached is not None:
            return cached
    try:
        resp = sonic_request("GET", "/projects/list", timeout=15)
        data = _sonic_response_data(resp) or []
        if isinstance(data, dict):
            data = data.get("data") or []
        projects = [_safe_project_dto(item) for item in (data or []) if isinstance(item, dict)]
        result = {"ok": True, "projects": projects, "total": len(projects), "cached_at": int(time.time())}
    except Exception as exc:
        result = {"ok": False, "projects": [], "total": 0, "error": str(exc)}
    _cache_set(cache_key, result)
    return result


def sonic_list_suites(project_id: Any = None, force: bool = False) -> Dict[str, Any]:
    """Sonic 测试套列表（缓存 30 秒）。"""
    pid = _safe_int(project_id, 0)
    cache_key = f"suites:{pid}"
    if not force:
        cached = _cache_get(cache_key, _SUITE_CACHE_TTL)
        if cached is not None:
            return cached
    try:
        params = {"projectId": pid} if pid else None
        resp = sonic_request("GET", "/testSuites/list", params=params, timeout=15)
        data = _sonic_response_data(resp) or []
        if isinstance(data, dict):
            data = data.get("content") or data.get("data") or []
        suites = [_safe_suite_dto(item) for item in (data or []) if isinstance(item, dict)]
        result = {"ok": True, "suites": suites, "total": len(suites), "cached_at": int(time.time())}
    except Exception as exc:
        result = {"ok": False, "suites": [], "total": 0, "error": str(exc)}
    _cache_set(cache_key, result)
    return result


def sonic_read_result(result_id: Any) -> Dict[str, Any]:
    """读取 Sonic 测试套执行结果（短缓存 5 秒）。"""
    rid = _safe_int(result_id, 0)
    if not rid:
        return {"ok": False, "error": "result_id 不能为空"}
    cache_key = f"result:{rid}"
    cached = _cache_get(cache_key, _RESULT_CACHE_TTL)
    if cached is not None:
        return cached
    try:
        resp = sonic_request("GET", "/results", params={"id": rid}, timeout=15)
        data = _sonic_response_data(resp) or {}
        if isinstance(data, dict):
            data = {
                k: v for k, v in data.items()
                if str(k).lower() not in ("token", "sonictoken", "password", "secret")
            }
        result = {"ok": True, "result": data, "cached_at": int(time.time())}
    except Exception as exc:
        result = {"ok": False, "error": str(exc)}
    _cache_set(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# Sonic 原始 API 查询（不缓存）
# ---------------------------------------------------------------------------

def sonic_list_projects_raw() -> list:
    """Sonic 原始项目列表（直接返回 API 数据）。"""
    return _sonic_response_data(sonic_request("GET", "/projects/list")) or []


def sonic_list_modules(project_id: int) -> list:
    return _sonic_response_data(sonic_request("GET", "/modules/list", params={"projectId": project_id})) or []


def sonic_list_cases(project_id: int, platform: int = 1, name: str = "") -> list:
    result = []
    page_size = 200
    for page in range(1, 21):
        params = {
            "projectId": project_id,
            "platform": platform,
            "name": name or "",
            "page": page,
            "pageSize": page_size,
            "editTimeSort": "desc",
        }
        data = _sonic_response_data(sonic_request("GET", "/testCases/list", params=params)) or {}
        if isinstance(data, list):
            return data
        items = _extract_page_items(data)
        result.extend(items)
        total = 0
        if isinstance(data, dict):
            total = _safe_int(data.get("total") or data.get("totalElements") or data.get("totalCount"), 0)
        if not items:
            break
        if total:
            if len(result) >= total:
                break
        elif len(items) < page_size:
            break
    return result


def sonic_list_steps(case_id: int) -> list:
    return _sonic_response_data(sonic_request("GET", "/steps/listAll", params={"caseId": case_id})) or []


def sonic_list_results(project_id: int, page: int = 1, page_size: int = 15) -> list:
    data = _sonic_response_data(sonic_request(
        "GET",
        "/results/list",
        params={"projectId": project_id, "page": page, "pageSize": page_size},
        timeout=10,
    )) or {}
    return _extract_page_items(data)


# ---------------------------------------------------------------------------
# 项目 / 测试套 查找
# ---------------------------------------------------------------------------

def sonic_project_id_for_app(app: dict) -> int:
    value = app.get("sonic_project_id") or app.get("sonicProjectId") or app.get("project_id")
    try:
        return int(value)
    except Exception:
        return 0


def sonic_project_name_for_app(app: dict) -> str:
    return (app.get("sonic_project_name") or app.get("sonicProjectName") or app.get("name") or "").strip()


def sonic_find_project_id(app: dict) -> int:
    configured = sonic_project_id_for_app(app)
    if configured:
        return configured
    target = sonic_project_name_for_app(app)
    if not target:
        return 0
    for item in sonic_list_projects_raw():
        if not isinstance(item, dict):
            continue
        name = item.get("projectName") or item.get("name") or ""
        if name == target:
            return _safe_int(item.get("id"), 0)
    return 0


def sonic_suite_id_for_app(app: dict) -> int:
    return _safe_int((app or {}).get("sonic_suite_id") or (app or {}).get("sonicSuiteId"), 0)


def sonic_suite_name_for_app(app: dict) -> str:
    return str((app or {}).get("sonic_suite_name") or (app or {}).get("sonicSuiteName") or "").strip()


def sonic_project_id_for_package(package: str) -> int:
    package = (package or "").strip()
    if not package:
        return 0
    env_key = "SONIC_PROJECT_ID_" + re.sub(r"[^A-Za-z0-9]+", "_", package).upper().strip("_")
    env_project_id = _safe_int(os.getenv(env_key), 0)
    if env_project_id:
        return env_project_id
    for app in sonic_notify_known_apps():
        if app.get("package") == package:
            project_id = sonic_project_id_for_app(app)
            if project_id:
                return project_id
    try:
        return sonic_find_project_id({"package": package, "name": package})
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# 模块 / 用例 / 步骤
# ---------------------------------------------------------------------------

def sonic_ensure_module(project_id: int, module_name: str) -> int:
    module_name = (module_name or "默认模块").strip()
    modules = sonic_list_modules(project_id)
    for item in modules:
        if isinstance(item, dict) and item.get("name") == module_name:
            return _safe_int(item.get("id"), 0)
    sonic_request("PUT", "/modules", body={"projectId": project_id, "name": module_name})
    modules = sonic_list_modules(project_id)
    for item in modules:
        if isinstance(item, dict) and item.get("name") == module_name:
            return _safe_int(item.get("id"), 0)
    raise RuntimeError(f"Sonic 模块创建后未找到：{module_name}")


def sonic_case_marker(case_id: str, module: str, file: str, task_name: str) -> str:
    return "\n".join([
        "[MidsceneSync]",
        f"case_id={case_id}",
        f"module={module}",
        f"file={_clean_filename(file)}",
        f"task={task_name}",
    ])


def sonic_managed_case(case_obj: dict, case_id: str = "") -> bool:
    des = str((case_obj or {}).get("des") or "")
    return "[MidsceneSync]" in des and (not case_id or f"case_id={case_id}" in des)


def sonic_case_marker_info(case_obj: dict) -> dict:
    des = str((case_obj or {}).get("des") or "")
    info = {}
    if "[MidsceneSync]" not in des:
        return info
    for line in des.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            info[key] = value
    return info


def sonic_midscene_step(step: dict, case_id: str = "") -> bool:
    if (step or {}).get("stepType") != "runScript":
        return False
    content = str((step or {}).get("content") or "")
    lower = content.lower()
    markers = (
        "midscene sonic bridge",
        "taskserver",
        "taskmodule",
        "taskname",
        "midscenecaseid",
        "/api/sonic/case",
        "/api/sonic/bridge-groovy",
        "midscene \"",
        "midscene '",
        'midscene \\"',
    )
    legacy_feishu_markers = (
        "midscene 自动化测试报告",
        "sendfeishu",
        "feishu_payload",
        "open.feishu.cn/open-apis/bot",
    )
    if case_id and f"case_id: {case_id}" in content:
        return True
    return any(marker in lower for marker in markers) or any(marker in lower for marker in legacy_feishu_markers)


def sonic_case_has_midscene_step(sonic_case_id: int) -> bool:
    try:
        return any(sonic_midscene_step(step) for step in sonic_list_steps(sonic_case_id))
    except Exception:
        return False


def sonic_bridge_step(step: dict) -> bool:
    content = str((step or {}).get("content") or "")
    return "Midscene Sonic Bridge" in content or "/api/sonic/bridge-groovy" in content


def sonic_step_state(steps: list, case_id: str = "") -> dict:
    midscene_steps = [step for step in (steps or []) if sonic_midscene_step(step, case_id)]
    if not midscene_steps:
        return {
            "state": "missing",
            "label": "未发现 Midscene 脚本步骤",
            "step_id": 0,
            "sort": 0,
            "step_ids": [],
            "step_count": 0,
            "bridge_count": 0,
            "legacy_count": 0,
        }
    bridge_steps = [step for step in midscene_steps if sonic_bridge_step(step)]
    legacy_steps = [step for step in midscene_steps if not sonic_bridge_step(step)]
    step = bridge_steps[0] if bridge_steps else midscene_steps[0]
    step_ids = [_safe_int(item.get("id"), 0) for item in midscene_steps if _safe_int(item.get("id"), 0)]
    if len(midscene_steps) > 1:
        if bridge_steps and legacy_steps:
            label = f"新桥接与旧模板并存（{len(midscene_steps)} 个步骤），需重新同步清理"
        else:
            label = f"重复 Midscene 步骤（{len(midscene_steps)} 个），需重新同步清理"
        state = "mixed"
    else:
        state = "bridge" if bridge_steps else "legacy"
        label = "新桥接脚本" if bridge_steps else "旧模板脚本"
    return {
        "state": state,
        "label": label,
        "step_id": _safe_int(step.get("id"), 0),
        "sort": _safe_int(step.get("sort"), 0),
        "step_ids": step_ids,
        "step_count": len(midscene_steps),
        "bridge_count": len(bridge_steps),
        "legacy_count": len(legacy_steps),
    }


def sonic_find_case(project_id: int, platform: int, case_name: str, case_id: str = "") -> Optional[dict]:
    if case_id:
        for item in sonic_list_cases(project_id, platform=platform, name=""):
            if isinstance(item, dict) and sonic_managed_case(item, case_id):
                return item
    cases = sonic_list_cases(project_id, platform=platform, name=case_name)
    exact = [item for item in cases if isinstance(item, dict) and item.get("name") == case_name]
    return exact[0] if exact else None


# ---------------------------------------------------------------------------
# Groovy 桥接脚本生成
# ---------------------------------------------------------------------------

def sonic_bridge_version() -> str:
    bridge_path = os.getenv("SONIC_BRIDGE_GROOVY_PATH", "/opt/sonic-midscene-task-runner.groovy")
    text = read_text_file(bridge_path, "") or read_text_file(os.path.join(os.getcwd(), "sonic-midscene-task-runner.groovy"), "")
    match = re.search(r'bridgeVersion\s*=\s*"([^"]+)"', text or "")
    return match.group(1) if match else "unknown"


def sonic_bridge_step_script(case_id: str) -> str:
    task_server = os.getenv("MIDSCENE_PUBLIC_BASE_URL") or os.getenv("TASK_PUBLIC_BASE_URL") or "http://101.34.197.12:8088"
    task_server = task_server.rstrip("/")
    escaped_case_id = str(case_id or "").replace("\\", "\\\\").replace('"', '\\"')
    escaped_server = task_server.replace("\\", "\\\\").replace('"', '\\"')
    bridge_version = sonic_bridge_version()
    return f'''// Midscene Sonic Bridge - managed by Task Platform
// case_id: {escaped_case_id}
// bridgeVersion: {bridge_version}
def midsceneCaseId = "{escaped_case_id}"
def taskServer = "{escaped_server}"
def runnerToken = "{_BRIDGE_RUNNER_TOKEN}"
def bridgeUrl = taskServer + "/api/sonic/bridge-groovy?case_id=" + java.net.URLEncoder.encode(midsceneCaseId, "UTF-8")
def conn = new URL(bridgeUrl).openConnection()
conn.setRequestProperty("x-token", runnerToken)
conn.setConnectTimeout(15000)
conn.setReadTimeout(30000)
def bridgeCode = conn.inputStream.getText("UTF-8")
binding.setVariable("midsceneCaseId", midsceneCaseId)
binding.setVariable("taskServer", taskServer)
binding.setVariable("runnerToken", runnerToken)
evaluate(bridgeCode)
'''


def sonic_upsert_bridge_step(project_id: int, platform: int, sonic_case_id: int, case_id: str) -> dict:
    steps = sonic_list_steps(sonic_case_id)
    bridge_content = sonic_bridge_step_script(case_id)
    midscene_steps = [item for item in steps if isinstance(item, dict) and sonic_midscene_step(item, case_id)]
    bridge_steps = [item for item in midscene_steps if isinstance(item, dict) and sonic_bridge_step(item)]
    target = bridge_steps[0] if bridge_steps else (midscene_steps[0] if midscene_steps else None)
    max_sort = 0
    for item in steps:
        if not isinstance(item, dict):
            continue
        max_sort = max(max_sort, _safe_int(item.get("sort"), 0))
    payload = {
        "id": target.get("id") if target else None,
        "caseId": sonic_case_id,
        "parentId": 0,
        "projectId": project_id,
        "platform": platform,
        "stepType": "runScript",
        "text": "Groovy",
        "content": bridge_content,
        "sort": _safe_int(target.get("sort"), 1) if target else max_sort + 1,
        "error": 3,
        "conditionType": 0,
        "disabled": 0,
        "elements": [],
    }
    _sonic_response_data(sonic_request("PUT", "/steps", body=payload))
    kept_step_id = _safe_int(target.get("id"), 0) if target else 0
    removed_step_ids = []
    for item in midscene_steps:
        step_id = _safe_int(item.get("id"), 0)
        if not step_id or step_id == kept_step_id:
            continue
        _sonic_response_data(sonic_request("DELETE", "/steps", params={"id": step_id}))
        removed_step_ids.append(step_id)
    verified_state = {}
    for attempt in range(3):
        verified_state = sonic_step_state(sonic_list_steps(sonic_case_id), case_id)
        if verified_state.get("state") == "bridge" and verified_state.get("step_count") == 1:
            break
        if attempt < 2:
            time.sleep(0.2)
    if verified_state.get("state") != "bridge" or verified_state.get("step_count") != 1:
        raise RuntimeError(
            "Sonic 步骤同步后复检未通过：仍存在旧模板或重复 Midscene 步骤，"
            "已停止标记为同步成功，请重新执行清理检查"
        )
    payload["removed_step_ids"] = removed_step_ids
    payload["cleaned_duplicate_steps"] = len(removed_step_ids)
    payload["verified_state"] = verified_state.get("state")
    payload["verified_step_count"] = verified_state.get("step_count", 0)
    return payload


# ---------------------------------------------------------------------------
# 同步状态文件
# ---------------------------------------------------------------------------

def load_sonic_sync_state() -> Dict[str, Any]:
    """加载 ``sonic-sync.json``；保证返回的结构含 ``cases`` 字典。"""
    data = read_json_cached(cfg.SONIC_SYNC_FILE, ttl_seconds=3, default={"cases": {}})
    if not isinstance(data, dict):
        data = {"cases": {}}
    cases = data.get("cases") if isinstance(data.get("cases"), dict) else {}
    data["cases"] = cases
    return data


def save_sonic_sync_state(state: Dict[str, Any]) -> None:
    """原子写入 ``sonic-sync.json`` 并刷新缓存。"""
    if not isinstance(state, dict):
        raise TypeError("sonic sync state 必须是 dict")
    cases = state.get("cases") if isinstance(state.get("cases"), dict) else {}
    state["cases"] = cases
    with cfg.SONIC_LOCK:
        write_json_file(cfg.SONIC_SYNC_FILE, state)
    invalidate_json_cache(str(cfg.SONIC_SYNC_FILE))


def load_sonic_suite_results() -> Dict[str, Any]:
    """加载 ``sonic-suite-results.json``。"""
    data = read_json_file(
        cfg.SONIC_SUITE_RESULTS_FILE, default={"suites": {}, "active": {}}
    )
    if not isinstance(data, dict):
        data = {"suites": {}, "active": {}}
    if not isinstance(data.get("suites"), dict):
        data["suites"] = {}
    if not isinstance(data.get("active"), dict):
        data["active"] = {}
    return data


def save_sonic_suite_results(state: Dict[str, Any]) -> None:
    with cfg.SONIC_SUITE_LOCK:
        write_json_file(cfg.SONIC_SUITE_RESULTS_FILE, state)


# ---------------------------------------------------------------------------
# 用例发布
# ---------------------------------------------------------------------------

def _strip_secrets(payload: Any) -> Any:
    """递归剔除敏感字段。"""
    sensitive = {"token", "sonictoken", "password", "secret", "sign", "signature"}
    if isinstance(payload, dict):
        return {
            k: _strip_secrets(v)
            for k, v in payload.items()
            if str(k).lower() not in sensitive
        }
    if isinstance(payload, list):
        return [_strip_secrets(item) for item in payload]
    return payload


def _clean_filename(name: str, default: str = "task.yaml") -> str:
    """Sanitise a name and ensure it ends with ``.yaml``."""
    import re as _re
    name = str(name or "").strip()
    name = name.replace("/", "_").replace("\\", "_")
    name = _re.sub(r'[\\:*?"<>|]+', "_", name).strip()
    base = _re.sub(r"\.(yaml|yml)$", "", name, flags=_re.I).strip(" ._\t\r\n")
    if not base:
        name = default
    elif name.startswith("."):
        name = base
    if not name.endswith((".yaml", ".yml")):
        name += ".yaml"
    return name


def _clean_id(value: str, default: str = "page") -> str:
    import re as _re
    value = (value or default).strip()
    value = _re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value)
    return value.strip("._")[:80] or default


# ---------------------------------------------------------------------------
# 已知应用配置
# ---------------------------------------------------------------------------

def builtin_task_apps() -> list:
    return [
        {
            "package": "com.kfb.model",
            "name": "3D 打印",
            "sonic_project_name": "3D 打印",
            "sonic_project_id": os.getenv("SONIC_PROJECT_ID_COM_KFB_MODEL", "3"),
            "sonic_suite_id": os.getenv("SONIC_SUITE_ID_COM_KFB_MODEL", "8"),
            "sonic_suite_name": "3D测试自动",
            "aliases": ["3D测试自动", "3D打印基线", "3D打印基线回归"],
        },
        {
            "package": "com.xbxxhz.box",
            "name": "小白学习打印",
            "sonic_project_name": "小白学习打印",
            "sonic_project_id": os.getenv("SONIC_PROJECT_ID_COM_XBXXHZ_BOX", "2"),
            "sonic_suite_id": os.getenv("SONIC_SUITE_ID_COM_XBXXHZ_BOX", "4"),
            "sonic_suite_name": "基线测试",
            "aliases": ["小白学习", "小白学习基线", "小白学习打印基线"],
        },
    ]


def _merge_task_app_defaults(app: dict, defaults: Optional[dict]) -> dict:
    merged = dict(defaults or {})
    merged.update(app or {})
    for key, value in (defaults or {}).items():
        if merged.get(key) in (None, ""):
            merged[key] = value
    if (app or {}).get("modules") is not None:
        merged["modules"] = app.get("modules") or []
    aliases = []
    for source in ((defaults or {}).get("aliases") or [], (app or {}).get("aliases") or []):
        for item in source:
            if item and item not in aliases:
                aliases.append(item)
    if aliases:
        merged["aliases"] = aliases
    return merged


def sonic_notify_known_apps() -> list:
    builtin_apps = builtin_task_apps()
    builtin_by_package = {
        (app.get("package") or "").strip(): app
        for app in builtin_apps
        if (app.get("package") or "").strip()
    }
    loaded = _load_task_apps()
    configured = (loaded.get("apps") or []) if isinstance(loaded, dict) else (loaded if isinstance(loaded, list) else [])
    apps = []
    seen = set()
    for app in configured:
        package = app.get("package", "")
        key = package or app.get("name", "")
        if key in seen:
            continue
        seen.add(key)
        apps.append(_merge_task_app_defaults(app, builtin_by_package.get(package)))
    for app in builtin_apps:
        package = app.get("package", "")
        key = package or app.get("name", "")
        if key in seen:
            continue
        seen.add(key)
        apps.append(app)
    return apps


def _load_task_apps() -> dict:
    data = read_json_file(cfg.TASK_APPS_FILE, default={})
    if isinstance(data, list):
        return {"apps": data}
    return data if isinstance(data, dict) else {}


def _task_app_map_by_package() -> dict:
    result = {}
    try:
        for app in sonic_notify_known_apps():
            package = (app.get("package") or "").strip()
            if package:
                result[package] = app
    except Exception:
        pass
    return result


def _app_package_for_module(module: str) -> str:
    try:
        for app in sonic_notify_known_apps():
            if module in (app.get("modules") or []):
                package = (app.get("package") or "").strip()
                if package:
                    return package
    except Exception:
        pass
    return ""


def _resolve_app_package(module: str = "", file: str = "", yaml_text: str = "", explicit: str = "", allow_default: bool = False) -> str:
    """解析 APP 包名。"""
    if explicit:
        return explicit.strip()
    for app in sonic_notify_known_apps():
        if module and module in (app.get("modules") or []):
            return (app.get("package") or "").strip()
    # 从 yaml_text 中提取 launch/force-stop 包名
    if yaml_text:
        for pattern in (r"launch:\s*([\w.]+)", r"force-stop:\s*([\w.]+)"):
            m = re.search(pattern, yaml_text)
            if m:
                return m.group(1).strip()
    if allow_default:
        return (os.getenv("APP_PACKAGE") or "").strip()
    return ""


# ---------------------------------------------------------------------------
# 飞书通知相关
# ---------------------------------------------------------------------------

def _validate_feishu_webhook(webhook: str) -> str:
    value = str(webhook or "").strip()
    if not value:
        return ""
    if any(marker in value for marker in ("\r", "\n", "\t", "export ", "export\t")):
        raise ValueError("飞书 Webhook 配置异常：只能填写单行机器人地址，不能包含换行或 export 配置")
    if value[:1] in "\"'""''" or value[-1:] in "\"'""''":
        raise ValueError("飞书 Webhook 配置异常：请去掉地址外层引号，尤其不要使用中文引号")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("飞书 Webhook 配置异常：地址格式不合法")
    return value


def _default_feishu_webhook_for_package(package: str) -> str:
    return (
        os.getenv(_env_key_for_package("FEISHU_WEBHOOK_", package))
        or os.getenv("FEISHU_WEBHOOK_DEFAULT", "")
        or ""
    )


def _task_app_feishu_webhook(app: Optional[dict]) -> str:
    if not app:
        return _validate_feishu_webhook(os.getenv("FEISHU_WEBHOOK_DEFAULT", ""))
    return _validate_feishu_webhook(
        app.get("feishu_webhook")
        or app.get("feishuWebhook")
        or _default_feishu_webhook_for_package(app.get("package", ""))
        or ""
    )


def _post_feishu_card(webhook: str, card: dict) -> dict:
    webhook = _validate_feishu_webhook(webhook)
    if not webhook:
        raise ValueError("未配置应用对应的飞书机器人 Webhook")
    data = json.dumps(card, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {"ok": True}


# ---------------------------------------------------------------------------
# 通知日志
# ---------------------------------------------------------------------------

def _append_notify_log(event: str, payload: Any = None, result: Any = None, error: str = "") -> None:
    """写入 ``SONIC_NOTIFY_LOG_FILE``。"""
    try:
        os.makedirs(cfg.LEARNING_DIR, exist_ok=True)
        safe_payload = payload if isinstance(payload, dict) else {"payload": payload}
        safe_payload = {
            k: v for k, v in safe_payload.items()
            if str(k).lower() not in ("token", "x-token", "secret", "sign", "signature", "password")
        }
        row = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": event,
            "payload": safe_payload,
            "result": result if result is not None else {},
            "error": error or "",
        }
        with open(cfg.SONIC_NOTIFY_LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as exc:
        print(f"sonic notify log failed: {exc}", flush=True)


# ---------------------------------------------------------------------------
# 文本处理 / 通知辅助
# ---------------------------------------------------------------------------

MOJIBAKE_MARKERS = (
    "锛", "涓", "褰", "妗", "鐣", "杈", "閬", "鎵", "鏍", "淇", "缃", "绔",
    "锟", "斤拷", "烫烫", "屯屯", "�",
)


def sonic_text_score(text: str) -> int:
    text = str(text or "")
    if not text:
        return 0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    ascii_letters = len(re.findall(r"[A-Za-z0-9]", text))
    common = len(re.findall(r"[的一是在有和不为页面按钮点击任务提示根据内容出现失败成功执行用例报告模块]", text))
    bad = text.count("�") * 8
    bad += sum(text.count(marker) * 10 for marker in MOJIBAKE_MARKERS if marker != "�")
    return cjk * 2 + common * 4 + ascii_letters - bad


def sonic_text_looks_mojibake(text: str) -> bool:
    text = str(text or "")
    if not text:
        return False
    if any(marker in text for marker in MOJIBAKE_MARKERS):
        return True
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    if cjk > 20 and len(re.findall(r"[的一是在有和不为页面按钮点击]", text)) < max(2, cjk // 18):
        return True
    return False


def sonic_recover_text_encoding(text: str) -> str:
    text = str(text or "")
    if not text or not sonic_text_looks_mojibake(text):
        return text
    candidates = [text]
    for source_encoding in ("gb18030", "gbk", "cp936", "latin1"):
        try:
            candidates.append(text.encode(source_encoding, errors="ignore").decode("utf-8", errors="replace"))
        except Exception:
            pass
    best = max(candidates, key=sonic_text_score)
    return best if sonic_text_score(best) > sonic_text_score(text) else text


def sonic_notify_clean_text(text: str, fallback: str = "日志编码异常，请查看报告") -> str:
    raw_text = str(text or "")
    if any(marker in raw_text for marker in ("锟", "斤拷", "烫烫", "屯屯")):
        return fallback
    text = sonic_recover_text_encoding(text)
    text = str(text or "")
    text = text.replace("\x00", " ")
    text = re.sub(r"[\u0001-\u0008\u000b\u000c\u000e-\u001f]+", " ", text)
    text = re.sub(r"[\u4e00-\u9fff]?\d�\d", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    if any(marker in text for marker in ("锟", "斤拷", "烫烫", "屯屯")):
        return fallback
    replacement_count = text.count("�")
    if replacement_count:
        return fallback
    if sonic_text_looks_mojibake(text) and sonic_text_score(text) < 20:
        return fallback
    return text


def sonic_notify_compact(text: str, limit: int = 220) -> str:
    text = sonic_notify_clean_text(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def sonic_notify_display_value(value: Any, default: str = "") -> str:
    text = re.sub(r"\s+", " ", sonic_notify_clean_text(value)).strip()
    if not text or re.fullmatch(r"[#$]\{[^{}]+\}", text):
        return default
    return text


def sonic_notify_pretty_title_text(value: str) -> str:
    text = sonic_notify_display_value(value)
    text = re.sub(r"(?i)3D\s*UI", "3D UI", text)
    text = re.sub(r"(?i)UI\s*3D", "UI 3D", text)
    return text


# ---------------------------------------------------------------------------
# Sonic 回调解码与状态
# ---------------------------------------------------------------------------

def decode_sonic_callback_body(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    raw = raw or b""
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin1", errors="replace")


def normalize_sonic_suite_status(raw_status: str = "", passed: int = 0, failed: int = 0, warning: int = 0, text: str = "") -> str:
    status_text = str(raw_status or "").strip().lower()
    full_text = f"{raw_status or ''}\n{text or ''}".lower()
    interrupted_markers = (
        "interrupted", "interrupt", "aborted", "abort", "cancelled", "canceled",
        "cancel", "stopped", "stop", "terminated", "terminate",
        "中断", "终止", "取消", "已停止", "停止", "手动停止", "强制停止",
    )
    if any(marker in full_text for marker in interrupted_markers):
        return "interrupted"
    if failed or status_text in ("failed", "fail", "失败"):
        return "failed"
    if warning or status_text in ("warning", "warn", "异常", "告警"):
        return "warning"
    if passed or status_text in ("success", "passed", "pass", "成功", "通过"):
        return "success"
    return "warning"


def sonic_suite_status_meta(status: str) -> dict:
    if status == "failed":
        return {"text": "失败", "class": "fail", "color": "red", "icon": "❌"}
    if status == "interrupted":
        return {"text": "中断", "class": "warn", "color": "orange", "icon": "⏸️"}
    if status == "warning":
        return {"text": "告警", "class": "warn", "color": "orange", "icon": "⚠️"}
    return {"text": "通过", "class": "pass", "color": "green", "icon": "✅"}


def parse_sonic_suite_completion_payload(raw: Any, content_type: str = "") -> dict:
    text = decode_sonic_callback_body(raw).strip()
    data = {}
    if text.startswith("{"):
        try:
            loaded = json.loads(text)
            data = loaded if isinstance(loaded, dict) else {}
        except Exception:
            data = {}

    def value(*names):
        for name in names:
            if data.get(name) not in (None, ""):
                return data.get(name)
        return ""

    def match_text(*labels):
        for label in labels:
            match = re.search(rf"{re.escape(label)}\s*[：:]\s*([^\r\n]+)", text)
            if match:
                return match.group(1).strip()
        return ""

    report_url = str(value("sonicReportUrl", "sonic_report_url", "reportUrl", "report_url", "url") or "").strip()
    if not report_url:
        url_match = re.search(r"https?://[^\s\"'<>]+", text)
        report_url = url_match.group(0).rstrip("，,。)") if url_match else ""
    detail_match = re.search(r"/Home/(\d+)/ResultDetail/(\d+)", report_url)
    project_id = _safe_int(value("projectId", "project_id"), 0)
    result_id = _safe_int(value("resultId", "result_id"), 0)
    if detail_match:
        project_id = project_id or _safe_int(detail_match.group(1), 0)
        result_id = result_id or _safe_int(detail_match.group(2), 0)
    suite_name = sonic_notify_clean_text(
        value("suiteName", "suite_name", "name") or match_text("测试套件", "套件"),
        fallback="",
    )
    suite_name = re.sub(r"\s*(运行完毕|执行完毕|执行完成|运行完成)[！!。.]?\s*$", "", suite_name).strip()
    passed = _safe_int(value("passed", "pass") or match_text("通过数", "通过"), 0)
    failed = _safe_int(value("failed", "fail") or match_text("失败数", "失败"), 0)
    warning = _safe_int(value("abnormal", "warn", "warning") or match_text("异常数", "告警数", "告警"), 0)
    total = _safe_int(value("total", "totalCount", "caseCount", "case_count") or match_text("总数", "用例数"), 0)
    if not total:
        total = passed + failed + warning
    raw_status = str(value("status", "result") or match_text("运行状态", "状态")).strip()
    status = normalize_sonic_suite_status(raw_status, passed, failed, warning, text)
    return {
        "app_name": sonic_notify_clean_text(value("appName", "app_name"), fallback=""),
        "app_package": str(value("appPackage", "app_package") or "").strip(),
        "project_id": project_id,
        "result_id": result_id,
        "suite_id": _safe_int(value("suiteId", "suite_id"), 0),
        "suite_name": suite_name,
        "status": status,
        "passed": passed,
        "failed": failed,
        "warning": warning,
        "total": total,
        "duration": sonic_notify_clean_text(value("duration") or match_text("耗时"), fallback=""),
        "createTime": sonic_notify_clean_text(value("createTime", "create_time", "startTime", "start_time") or match_text("创建时间", "开始时间"), fallback=""),
        "endTime": sonic_notify_clean_text(value("endTime", "end_time", "finishTime", "finish_time") or match_text("结束时间", "完成时间"), fallback=""),
        "report_url": report_url,
        "received_text": sonic_notify_clean_text(text, fallback="Sonic 已回传测试套结束事件"),
        "content_type": content_type or "",
    }


# ---------------------------------------------------------------------------
# 套件事件 / 结果管理
# ---------------------------------------------------------------------------

def sonic_result_suite_key(project_id: Any, result_id: Any) -> str:
    project_id = _safe_int(project_id, 0)
    result_id = _safe_int(result_id, 0)
    return f"sonic_result_{project_id}_{result_id}" if project_id and result_id else ""


def sonic_suite_bound_result_id(suite: dict) -> int:
    suite = suite or {}
    return _safe_int(suite.get("sonic_result_id") or (suite.get("sonic_result_meta") or {}).get("result_id"), 0)


def sonic_suite_is_legacy_mixed_completion(suite_key: str, suite: dict) -> bool:
    suite = suite or {}
    expected_key = sonic_result_suite_key(suite.get("sonic_project_id"), sonic_suite_bound_result_id(suite))
    return bool(expected_key and suite.get("completion_received") and suite_key != expected_key)


def sonic_suite_matches_completion(suite: dict, event: dict) -> bool:
    result_id = _safe_int(event.get("result_id"), 0)
    if not result_id or result_id != sonic_suite_bound_result_id(suite):
        return False
    event_project_id = _safe_int(event.get("project_id"), 0)
    suite_project_id = _safe_int((suite or {}).get("sonic_project_id"), 0)
    return not (event_project_id and suite_project_id and event_project_id != suite_project_id)


def sonic_suite_key_for_completion_event(event: dict, app: dict, state: dict, now_ts: int) -> str:
    result_key = sonic_result_suite_key(event.get("project_id"), event.get("result_id"))
    if result_key:
        return result_key
    event_suite = re.sub(r"\s+", "", str(event.get("suite_name") or ""))
    event_package = str((app or {}).get("package") or event.get("app_package") or "").strip()
    candidates = []
    for suite_key in set((state.get("active") or {}).values()):
        suite = (state.get("suites") or {}).get(suite_key) or {}
        if suite.get("completion_received") or (suite.get("sent_at") and not suite.get("send_error")):
            continue
        last_ts = _safe_int(suite.get("last_update_ts") or suite.get("created_ts"), 0)
        if last_ts and now_ts - last_ts > sonic_suite_reopen_seconds():
            continue
        suite_package = str(suite.get("app_package") or ((suite.get("app") or {}).get("package")) or "").strip()
        if event_package and suite_package and event_package != suite_package:
            continue
        suite_name = re.sub(r"\s+", "", str(suite.get("sonic_suite_name") or ""))
        if event_suite and suite_name and not (
            event_suite == suite_name or event_suite in suite_name or suite_name in event_suite
        ):
            continue
        if not (suite.get("results") or suite.get("last_running_job_id")):
            continue
        candidates.append((last_ts, suite_key))
    if candidates:
        return sorted(candidates, reverse=True)[0][1]
    return unique_millis_id("sonic_suite")


# ---------------------------------------------------------------------------
# 套件时间参数
# ---------------------------------------------------------------------------

def sonic_suite_quiet_seconds() -> int:
    return max(20, _env_int("SONIC_SUITE_SUMMARY_QUIET_SECONDS", 45))


def sonic_suite_max_wait_seconds() -> int:
    return max(60, _env_int("SONIC_SUITE_MAX_WAIT_SECONDS", 600))


def sonic_suite_running_check_delay_seconds() -> int:
    return max(30, _env_int("SONIC_SUITE_RUNNING_CHECK_DELAY_SECONDS", 30))


def sonic_suite_reopen_seconds() -> int:
    return max(300, _env_int("SONIC_SUITE_REOPEN_SECONDS", 1800))


def sonic_suite_waits_for_completion_event(suite_or_job: dict) -> bool:
    return bool(
        cfg.SONIC_NOTIFY_ON_SUITE_COMPLETION_ONLY
        and (suite_or_job or {}).get("source", "sonic") == "sonic"
        and (suite_or_job or {}).get("run_mode", "baseline") == "baseline"
    )


def sonic_report_lookup_retries() -> int:
    return max(1, _env_int("SONIC_REPORT_LOOKUP_RETRIES", 6))


def sonic_report_lookup_interval() -> int:
    return max(2, _env_int("SONIC_REPORT_LOOKUP_INTERVAL_SECONDS", 5))


def sonic_midscene_report_grace_seconds() -> int:
    return max(0, _env_int("MIDSCENE_REPORT_UPLOAD_GRACE_SECONDS", 30))


def sonic_midscene_report_check_delay_seconds() -> int:
    return max(1, _env_int("MIDSCENE_REPORT_UPLOAD_CHECK_DELAY_SECONDS", 3))


def sonic_task_callback_grace_seconds() -> int:
    return max(0, _env_int("SONIC_TASK_CALLBACK_GRACE_SECONDS", 180))


def sonic_suite_pending_midscene_reports(suite: dict) -> int:
    return len([
        item for item in ((suite or {}).get("results") or [])
        if _safe_bool(item.get("report_upload_pending"))
    ])


def sonic_suite_can_wait_for_pending_midscene_reports(suite: dict, now_ts: Optional[int] = None) -> bool:
    if not sonic_suite_pending_midscene_reports(suite):
        return False
    grace_seconds = sonic_midscene_report_grace_seconds()
    if grace_seconds <= 0:
        return False
    now_ts = now_ts or int(time.time())
    reference_ts = (
        _safe_int((suite or {}).get("completion_ts"), 0)
        or _safe_int((suite or {}).get("last_update_ts"), 0)
        or now_ts
    )
    return now_ts - reference_ts < grace_seconds


def sonic_suite_completion_reference_ts(suite: dict, now_ts: Optional[int] = None) -> int:
    suite = suite or {}
    now_ts = now_ts or int(time.time())
    for value in (
        suite.get("completion_ts"),
        ((suite.get("sonic_completion") or {}).get("endTime")),
        ((suite.get("sonic_completion") or {}).get("end_time")),
        ((suite.get("sonic_result_meta") or {}).get("end_time")),
        ((suite.get("sonic_result_meta") or {}).get("endTime")),
        suite.get("last_update_ts"),
        suite.get("created_ts"),
    ):
        parsed = _safe_int(value, 0)
        if parsed:
            return parsed
        parsed = _parse_time(str(value or ""))
        if parsed:
            return parsed
    return now_ts


def sonic_suite_missing_task_callbacks(suite: dict) -> int:
    stats = sonic_suite_display_stats(suite)
    return max(0, _safe_int(stats.get("missing_task_callbacks") or stats.get("pending"), 0))


def sonic_suite_can_wait_for_missing_task_callbacks(suite: dict, now_ts: Optional[int] = None) -> bool:
    if not sonic_suite_ready_for_final_summary(suite):
        return False
    if not sonic_suite_missing_task_callbacks(suite):
        return False
    grace_seconds = sonic_task_callback_grace_seconds()
    if grace_seconds <= 0:
        return False
    now_ts = now_ts or int(time.time())
    reference_ts = sonic_suite_completion_reference_ts(suite, now_ts)
    return now_ts - reference_ts < grace_seconds


def sonic_report_window_before_seconds() -> int:
    return max(60, _env_int("SONIC_REPORT_WINDOW_BEFORE_SECONDS", 900))


def sonic_report_window_after_seconds() -> int:
    return max(120, _env_int("SONIC_REPORT_WINDOW_AFTER_SECONDS", 1800))


# ---------------------------------------------------------------------------
# 套件应用识别
# ---------------------------------------------------------------------------

def sonic_suite_app_info(package: str = "", module: str = "") -> dict:
    package = (package or "").strip()
    try:
        for app in sonic_notify_known_apps():
            app_package = (app.get("package") or "").strip()
            if package and app_package == package:
                return app
            if module and module in (app.get("modules") or []):
                return app
    except Exception:
        pass
    for app in sonic_notify_known_apps():
        if package and app.get("package") == package:
            return app
    return {"package": package, "name": package or "Sonic"}


def sonic_suite_app_for_completion(event: dict) -> dict:
    package = str((event or {}).get("app_package") or "").strip()
    if package:
        return sonic_suite_app_info(package, "")
    project_id = _safe_int((event or {}).get("project_id"), 0)
    suite_id = _safe_int((event or {}).get("suite_id"), 0)
    suite_name = re.sub(r"\s+", "", str((event or {}).get("suite_name") or ""))
    for app in sonic_notify_known_apps():
        if project_id and sonic_project_id_for_app(app) == project_id:
            return app
        if suite_id and sonic_suite_id_for_app(app) == suite_id:
            return app
        configured_name = re.sub(r"\s+", "", sonic_suite_name_for_app(app))
        if suite_name and configured_name and (suite_name == configured_name or suite_name in configured_name or configured_name in suite_name):
            return app
    return sonic_suite_app_info(package, "")


# ---------------------------------------------------------------------------
# 套件结果统计
# ---------------------------------------------------------------------------

def sonic_suite_summary_status(results: list) -> str:
    if not results:
        return "warning"
    failed = len([item for item in results if item.get("status") == "failed"])
    warning = len([item for item in results if item.get("status") not in ("success", "failed")])
    if failed:
        return "failed"
    if warning:
        return "warning"
    return "success"


def _sonic_status_text_matches(text: str, markers: tuple) -> bool:
    normalized = str(text or "").strip().lower()
    return bool(normalized and any(marker in normalized for marker in markers))


def sonic_completion_indicates_success(completion: dict) -> bool:
    completion = completion or {}
    if not completion.get("finished"):
        return False
    status = str(completion.get("status") or "").strip().lower()
    passed = _safe_int(completion.get("passed"), 0)
    failed = _safe_int(completion.get("failed"), 0)
    warning = _safe_int(completion.get("warning") or completion.get("abnormal"), 0)
    total = _safe_int(completion.get("total"), 0)
    if status in ("success", "passed", "pass", "ok", "通过", "成功", "测试通过"):
        return not failed and not warning
    if total and passed >= total and not failed and not warning:
        return True
    return False


def sonic_result_meta_indicates_success(meta: dict) -> bool:
    meta = meta or {}
    if not meta.get("finished"):
        return False
    status = _safe_int(meta.get("status"), 0)
    status_text = str(meta.get("status_text") or meta.get("statusText") or "").strip()
    if status == 1:
        return True
    if _sonic_status_text_matches(status_text, ("success", "passed", "pass", "通过", "成功", "测试通过")):
        return True
    return False


def sonic_suite_sonic_result_indicates_success(suite: dict) -> bool:
    suite = suite or {}
    return (
        sonic_completion_indicates_success(suite.get("sonic_completion") or {})
        or sonic_result_meta_indicates_success(suite.get("sonic_result_meta") or {})
    )


def sonic_suite_completion_stats(suite: dict) -> Optional[dict]:
    completion = (suite or {}).get("sonic_completion") or {}
    if not completion or not completion.get("finished"):
        meta = (suite or {}).get("sonic_result_meta") or {}
        if not meta or not meta.get("finished"):
            return None
        total = _safe_int(
            meta.get("expected_total_count")
            or meta.get("send_msg_count")
            or meta.get("sendMsgCount"),
            0,
        )
        if not total:
            return None
        actual = sonic_suite_case_stats((suite or {}).get("results") or [])
        actual_total = actual.get("total", 0)
        status = _safe_int(meta.get("status"), 0)
        status_text = str(meta.get("status_text") or meta.get("statusText") or "").lower()
        meta_success = sonic_result_meta_indicates_success(meta)
        if actual_total:
            actual_failed = actual.get("failed", 0)
            actual_warning = actual.get("warning", 0)
            if meta_success and not actual_failed and not actual_warning:
                passed, failed_count, warning_count = total, 0, 0
            else:
                passed = min(actual.get("passed", 0), total)
                failed_count = min(actual_failed, max(0, total - passed))
                warning_count = min(actual_warning, max(0, total - passed - failed_count))
            if (status == 3 or any(word in status_text for word in ("fail", "失败"))) and failed_count == 0:
                failed_count = 1
                if passed + warning_count + failed_count > total:
                    passed = max(0, total - failed_count - warning_count)
        elif meta_success or status == 1 or any(word in status_text for word in ("success", "pass", "通过", "成功")):
            passed, failed_count, warning_count = total, 0, 0
        elif status == 3 or any(word in status_text for word in ("fail", "失败")):
            passed, failed_count, warning_count = max(0, total - 1), 1, 0
        elif status == 2 or any(word in status_text for word in ("warn", "warning", "异常", "告警")):
            passed, failed_count, warning_count = 0, 0, total
        else:
            return None
        missing_callbacks = max(0, total - actual_total)
        return {
            "total": total,
            "passed": passed,
            "failed": failed_count,
            "warning": warning_count,
            "actual_total": actual_total,
            "expected_total": total,
            "pending": 0 if meta_success and not failed_count and not warning_count else missing_callbacks,
            "missing_task_callbacks": missing_callbacks,
            "missing_task_callbacks_ignored_by_sonic_success": bool(
                missing_callbacks and meta_success and not failed_count and not warning_count
            ),
        }
    total = _safe_int(completion.get("total"), 0)
    passed = _safe_int(completion.get("passed"), 0)
    failed_count = _safe_int(completion.get("failed"), 0)
    warning_count = _safe_int(completion.get("warning") or completion.get("abnormal"), 0)
    actual = sonic_suite_case_stats((suite or {}).get("results") or [])
    actual_total = actual.get("total", 0)
    completion_success = sonic_completion_indicates_success(completion)
    if not total:
        total = passed + failed_count + warning_count
    if total and not (passed or failed_count or warning_count):
        status = completion.get("status") or ""
        if status == "success":
            passed = total
        elif status == "failed":
            failed_count = total
        elif status == "interrupted":
            warning_count = total
        else:
            warning_count = total
    if total > passed + failed_count + warning_count:
        warning_count += total - passed - failed_count - warning_count
    missing_callbacks = max(0, total - actual_total) if total else 0
    if total and actual_total:
        actual_failed = actual.get("failed", 0)
        actual_warning = actual.get("warning", 0)
        if completion_success and not actual_failed and not actual_warning:
            passed, failed_count, warning_count = total, 0, 0
        else:
            passed = min(actual.get("passed", 0), total)
            failed_count = min(actual_failed, max(0, total - passed))
            warning_count = min(actual_warning, max(0, total - passed - failed_count))
        if completion.get("status") == "failed" and failed_count == 0:
            failed_count = 1
            if passed + warning_count + failed_count > total:
                passed = max(0, total - failed_count - warning_count)
    return {
        "total": total,
        "passed": passed,
        "failed": failed_count,
        "warning": warning_count,
        "actual_total": actual_total or total,
        "expected_total": total,
        "pending": 0 if completion_success and not failed_count and not warning_count else missing_callbacks,
        "missing_task_callbacks": missing_callbacks,
        "missing_task_callbacks_ignored_by_sonic_success": bool(
            missing_callbacks and completion_success and not failed_count and not warning_count
        ),
    }


def sonic_suite_expected_total(suite: dict) -> int:
    if not suite:
        return 0
    values = [
        suite.get("expected_total_count"),
        suite.get("suite_expected_total"),
        suite.get("expected_case_count"),
        suite.get("suite_total"),
        suite.get("total_count"),
    ]
    detail = suite.get("sonic_report_lookup") or {}
    values.extend([
        detail.get("send_msg_count"),
        detail.get("sendMsgCount"),
        detail.get("case_count"),
        detail.get("total_count"),
    ])
    meta = suite.get("sonic_result_meta") or suite.get("sonic_suite_definition") or {}
    values.extend([
        meta.get("send_msg_count"),
        meta.get("sendMsgCount"),
        meta.get("case_count"),
        meta.get("total_count"),
        meta.get("expected_total_count"),
    ])
    definition = suite.get("sonic_suite_definition") or {}
    values.extend([
        definition.get("case_count"),
        definition.get("expected_total_count"),
    ])
    return max([_safe_int(value, 0) for value in values] or [0])


def sonic_suite_case_units(item: dict) -> int:
    total = _safe_int(item.get("total_task_count") or item.get("totalTaskCount"), 0)
    return max(1, total)


def sonic_suite_result_identity(item: dict) -> str:
    item = item or {}
    for key in ("case_id", "file", "target_task_name", "job_id"):
        value = str(item.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    return ""


def sonic_suite_job_identity(job: dict) -> str:
    job = job or {}
    for key in ("case_id", "file", "target_task_name", "job_id"):
        value = str(job.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    return ""


def sonic_suite_unique_result_count(suite: dict) -> int:
    identities = set()
    fallback_count = 0
    for item in (suite or {}).get("results") or []:
        identity = sonic_suite_result_identity(item)
        if identity:
            identities.add(identity)
        else:
            fallback_count += 1
    return len(identities) + fallback_count


def sonic_suite_contains_job_identity(suite: dict, job: dict) -> bool:
    identity = sonic_suite_job_identity(job)
    if not identity:
        return False
    return any(sonic_suite_result_identity(item) == identity for item in (suite or {}).get("results") or [])


def sonic_suite_has_complete_result_cycle(suite: dict) -> bool:
    expected = sonic_suite_expected_total(suite)
    return bool(expected and sonic_suite_unique_result_count(suite) >= expected)


def sonic_suite_case_stats(results: list) -> dict:
    stats = {"total": 0, "passed": 0, "failed": 0, "warning": 0}
    for item in results or []:
        total = sonic_suite_case_units(item)
        completed = _safe_int(item.get("completed_task_count") or item.get("completedTaskCount"), 0)
        status = item.get("status") or ""
        stats["total"] += total
        if status == "success":
            stats["passed"] += total
        elif status == "failed":
            passed = max(0, min(total - 1, completed))
            stats["passed"] += passed
            stats["failed"] += max(1, total - passed)
        else:
            stats["warning"] += total
    return stats


def sonic_suite_display_stats(suite: dict) -> dict:
    completion_stats = sonic_suite_completion_stats(suite)
    if completion_stats:
        return completion_stats
    stats = sonic_suite_case_stats((suite or {}).get("results") or [])
    actual_total = stats["total"]
    expected_total = max(actual_total, sonic_suite_expected_total(suite))
    pending = max(0, expected_total - actual_total)
    stats["warning"] += pending
    stats["actual_total"] = actual_total
    stats["expected_total"] = expected_total
    stats["pending"] = pending
    stats["missing_task_callbacks"] = pending
    stats["total"] = expected_total
    return stats


def sonic_suite_effective_status(suite: dict) -> str:
    completion = (suite or {}).get("sonic_completion") or {}
    if completion.get("finished") and completion.get("status") == "interrupted":
        return "interrupted"
    stats = sonic_suite_display_stats(suite)
    if stats.get("failed"):
        return "failed"
    missing_affects_status = bool(
        stats.get("missing_task_callbacks")
        and not stats.get("missing_task_callbacks_ignored_by_sonic_success")
    )
    if stats.get("warning") or stats.get("pending") or missing_affects_status:
        return "warning"
    if stats.get("total"):
        return "success"
    if completion.get("finished"):
        return "success"
    results = list((suite or {}).get("results") or [])
    status = sonic_suite_summary_status(results)
    if status == "success" and stats.get("pending"):
        return "warning"
    return status


def sonic_suite_finished_in_sonic(suite: dict) -> bool:
    return bool(
        ((suite or {}).get("sonic_completion") or {}).get("finished")
        or ((suite or {}).get("sonic_result_meta") or {}).get("finished")
    )


def sonic_suite_ready_for_final_summary(suite: dict) -> bool:
    """Return true when a completion-only suite has Sonic's authoritative finish signal."""
    if not sonic_suite_waits_for_completion_event(suite):
        return True
    return bool((suite or {}).get("completion_received") or sonic_suite_finished_in_sonic(suite))


def sonic_suite_result_line(job: dict) -> str:
    title = sonic_notify_display_value(job.get("target_task_name") or job.get("current_task_name") or job.get("file") or job.get("case_id") or "-", "-")
    module = sonic_notify_display_value(job.get("module") or "-", "-")
    return f"{module} / {title}"


# ---------------------------------------------------------------------------
# 套件 URL / 报告查找
# ---------------------------------------------------------------------------

def sonic_result_detail_url(project_id: Any, result_id: Any) -> str:
    project_id = _safe_int(project_id, 0)
    result_id = _safe_int(result_id, 0)
    if not project_id or not result_id:
        return ""
    return f"{sonic_base_url()}/Home/{project_id}/ResultDetail/{result_id}"


def sonic_suite_fixed_report_url(suite: Optional[dict] = None, project_id: Any = 0, result_id: Any = 0, suite_key: str = "") -> str:
    suite = suite or {}
    project_id = _safe_int(
        project_id
        or suite.get("sonic_project_id")
        or suite.get("project_id")
        or suite.get("projectId")
        or (suite.get("sonic_result_meta") or {}).get("project_id")
        or (suite.get("sonic_result_meta") or {}).get("projectId"),
        0,
    )
    result_id = _safe_int(
        result_id
        or suite.get("sonic_result_id")
        or suite.get("result_id")
        or suite.get("resultId")
        or (suite.get("sonic_result_meta") or {}).get("result_id")
        or (suite.get("sonic_result_meta") or {}).get("resultId"),
        0,
    )
    if not project_id or not result_id:
        key = str(suite_key or suite.get("suite_key") or "").strip()
        m = re.search(r"sonic_result_(\d+)_(\d+)", key)
        if m:
            project_id = project_id or _safe_int(m.group(1), 0)
            result_id = result_id or _safe_int(m.group(2), 0)
    return sonic_result_detail_url(project_id, result_id)


def ensure_sonic_suite_report_url(suite: dict) -> str:
    if not isinstance(suite, dict):
        return ""
    existing = str(suite.get("sonic_report_url") or suite.get("report_url") or "").strip()
    if existing.startswith("http"):
        suite["sonic_report_url"] = existing
        return existing
    fixed = sonic_suite_fixed_report_url(suite)
    if fixed:
        suite["sonic_report_url"] = fixed
        suite["sonic_report_lookup_error"] = ""
        return fixed
    return ""


def sonic_suite_report_lookup_message(suite: dict) -> str:
    if ensure_sonic_suite_report_url(suite):
        return ""
    detail = (suite or {}).get("sonic_report_lookup") or {}
    error = (suite or {}).get("sonic_report_lookup_error") or detail.get("error") or ""
    attempt = _safe_int(detail.get("attempt"), 0)
    max_attempt = _safe_int(detail.get("max_attempt"), 0)
    waited = f"已查询 {attempt}/{max_attempt} 次" if attempt and max_attempt else "已查询"
    if error:
        return f"Sonic 报告未附加：{waited}，{sonic_notify_compact(error, 80)}"
    if detail:
        return f"Sonic 报告未附加：{waited}，未匹配到时间窗口内的已完成结果"
    return ""


def sonic_error_is_unauthorized(error: Any) -> bool:
    text = str(error or "").lower()
    return "unauthorized" in text or "401" in text or "403" in text or "无权限" in text or "未授权" in text


def sonic_results_permission_error(project_id: Any, error: Any) -> dict:
    token_probe = sonic_probe_token()
    if token_probe.get("auth_status") != "ok":
        return {
            "project_id": project_id,
            "error": token_probe.get("error") or token_probe.get("message") or "Sonic token 未通过鉴权",
            "raw_error": str(error),
            "token_probe": token_probe,
        }
    try:
        sonic_list_projects_raw()
        return {
            "project_id": project_id,
            "error": "Sonic token 有效，但当前账号角色没有 /results/list（查询测试结果列表）资源权限；请在 Sonic 权限配置里给该角色增加该资源权限",
            "raw_error": str(error),
            "token_probe": token_probe,
        }
    except Exception as project_error:
        return {
            "project_id": project_id,
            "error": _sonic_auth_failure_message(),
            "raw_error": str(error),
            "project_check_error": str(project_error),
        }


# ---------------------------------------------------------------------------
# 套件定义查找
# ---------------------------------------------------------------------------

def sonic_suite_config_id(suite: dict) -> int:
    app = (suite or {}).get("app") or sonic_suite_app_info((suite or {}).get("app_package", ""), "")
    return _safe_int(
        (suite or {}).get("sonic_suite_id")
        or (suite or {}).get("sonicSuiteId")
        or app.get("sonic_suite_id")
        or app.get("sonicSuiteId"),
        0,
    )


def sonic_suite_config_name(suite: dict) -> str:
    app = (suite or {}).get("app") or sonic_suite_app_info((suite or {}).get("app_package", ""), "")
    return str(
        (suite or {}).get("sonic_suite_name")
        or (suite or {}).get("suite_name")
        or app.get("sonic_suite_name")
        or app.get("sonicSuiteName")
        or ""
    ).strip()


def sonic_count_suite_cases(dto: dict) -> int:
    if not isinstance(dto, dict):
        return 0
    for key in ("testCases", "test_cases", "cases", "caseList", "case_list"):
        value = dto.get(key)
        if isinstance(value, list):
            return len(value)
    for key in ("caseIds", "case_ids", "testCaseIds", "test_case_ids"):
        value = dto.get(key)
        if isinstance(value, list):
            return len(value)
        if isinstance(value, str):
            return len([item for item in re.split(r"[,;\s]+", value) if item.strip()])
    return _safe_int(dto.get("caseCount") or dto.get("case_count") or dto.get("totalCase") or dto.get("total_case"), 0)


def sonic_suite_definition_meta_from_dto(dto: dict, source: str = "") -> dict:
    count = sonic_count_suite_cases(dto)
    return {
        "source": source,
        "suite_id": _safe_int(dto.get("id"), 0) if isinstance(dto, dict) else 0,
        "suite_name": dto.get("name", "") if isinstance(dto, dict) else "",
        "expected_total_count": count,
        "case_count": count,
    }


def lookup_sonic_suite_definition_for_suite(suite: dict) -> dict:
    app = suite.get("app") or sonic_suite_app_info(suite.get("app_package", ""), "")
    package = suite.get("app_package") or app.get("package", "")
    project_id = sonic_project_id_for_app(app) or sonic_project_id_for_package(package)
    if not project_id:
        return {"error": "未找到 Sonic 项目 ID"}
    suite_id = sonic_suite_config_id(suite)
    suite_name = sonic_suite_config_name(suite)

    if suite_id:
        try:
            data = _sonic_response_data(sonic_request("GET", "/testSuites", params={"id": suite_id}, timeout=10)) or {}
            if isinstance(data, dict):
                meta = sonic_suite_definition_meta_from_dto(data, "/testSuites?id")
                if meta.get("expected_total_count"):
                    meta["project_id"] = project_id
                    return meta
        except Exception as e:
            return {"project_id": project_id, "suite_id": suite_id, "error": str(e), "source": "/testSuites?id"}

    try:
        data = _sonic_response_data(sonic_request("GET", "/testSuites/listAll", params={"projectId": project_id}, timeout=10)) or []
        suites = data if isinstance(data, list) else _extract_page_items(data)
    except Exception as e:
        return {"project_id": project_id, "error": str(e), "source": "/testSuites/listAll"}

    normalized_name = re.sub(r"\s+", "", suite_name)
    best = None
    for item in suites:
        if not isinstance(item, dict):
            continue
        if suite_id and _safe_int(item.get("id"), 0) == suite_id:
            best = item
            break
        item_name = re.sub(r"\s+", "", str(item.get("name") or ""))
        if normalized_name and (item_name == normalized_name or normalized_name in item_name or item_name in normalized_name):
            best = item
            break

    if not best and suite_name:
        try:
            data = _sonic_response_data(sonic_request(
                "GET", "/testSuites/list",
                params={"projectId": project_id, "name": suite_name, "page": 1, "pageSize": 20},
                timeout=10,
            )) or {}
            items = data if isinstance(data, list) else _extract_page_items(data)
            for item in items:
                if isinstance(item, dict):
                    best = item
                    break
        except Exception:
            pass

    if best:
        meta = sonic_suite_definition_meta_from_dto(best, "/testSuites/listAll")
        meta["project_id"] = project_id
        if not meta.get("expected_total_count") and meta.get("suite_id"):
            try:
                detail = _sonic_response_data(sonic_request("GET", "/testSuites", params={"id": meta["suite_id"]}, timeout=10)) or {}
                if isinstance(detail, dict):
                    detail_meta = sonic_suite_definition_meta_from_dto(detail, "/testSuites?id")
                    if detail_meta.get("expected_total_count"):
                        detail_meta["project_id"] = project_id
                        return detail_meta
            except Exception as e:
                meta["detail_error"] = str(e)
        return meta

    return {
        "project_id": project_id,
        "suite_id": suite_id,
        "suite_name": suite_name,
        "error": "未在 Sonic 测试套列表中匹配到当前测试套",
        "source": "/testSuites/listAll",
    }


# ---------------------------------------------------------------------------
# 结果元数据查找
# ---------------------------------------------------------------------------

def sonic_suite_expected_name(suite: dict) -> list:
    values = []
    values.append(suite.get("sonic_suite_name") or suite.get("suite_name") or "")
    for item in suite.get("results") or []:
        values.extend([
            item.get("sonic_suite_name") or "",
            item.get("module") or "",
        ])
    seen = []
    for value in values:
        value = re.sub(r"\s+", "", str(value or ""))
        if value and value not in seen:
            seen.append(value)
    return seen


def sonic_result_timestamp(result: dict) -> int:
    return (
        _parse_time(result.get("endTime") or result.get("end_time"))
        or _parse_time(result.get("createTime") or result.get("create_time"))
        or 0
    )


def _format_duration_seconds(seconds: int) -> str:
    seconds = _safe_int(seconds, 0)
    if seconds <= 0:
        return ""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remain = seconds % 60
    if hours:
        return f"{hours}小时{minutes}分{remain}秒"
    if minutes:
        return f"{minutes}分{remain}秒"
    return f"{remain}秒"


def sonic_suite_time_range(suite: dict) -> Tuple[int, int, str]:
    suite = suite or {}
    meta = suite.get("sonic_result_meta") or {}
    start = _parse_time(meta.get("createTime") or meta.get("create_time") or meta.get("started_at"))
    end = _parse_time(meta.get("endTime") or meta.get("end_time") or meta.get("finished_at"))
    if start and end and end >= start:
        return start, end, "sonic_result_meta"
    completion = suite.get("sonic_completion") or {}
    start = _parse_time(completion.get("createTime") or completion.get("create_time") or completion.get("started_at"))
    end = _parse_time(completion.get("endTime") or completion.get("end_time") or completion.get("finished_at"))
    if start and end and end >= start:
        return start, end, "sonic_completion"
    results = list(suite.get("results") or [])
    starts = [
        _parse_time(item.get("started_at") or item.get("created_at"))
        for item in results
        if _parse_time(item.get("started_at") or item.get("created_at"))
    ]
    ends = [
        _parse_time(item.get("finished_at") or item.get("created_at"))
        for item in results
        if _parse_time(item.get("finished_at") or item.get("created_at"))
    ]
    if starts and ends and max(ends) >= min(starts):
        return min(starts), max(ends), "task_callbacks"
    return 0, 0, ""


def sonic_suite_duration_text(suite: dict) -> str:
    start, end, _ = sonic_suite_time_range(suite)
    if start and end and end >= start:
        return _format_duration_seconds(int(end - start))
    duration = str(((suite or {}).get("sonic_completion") or {}).get("duration") or "").strip()
    return sonic_notify_clean_text(duration, fallback="") if duration else ""


def sonic_result_status_text(result: dict) -> str:
    if not isinstance(result, dict):
        return ""
    parts = []
    for key in (
        "statusName", "status_name", "statusText", "status_text",
        "stateName", "state_name", "stateText", "state_text",
        "runStatus", "run_status", "result", "message", "msg", "remark",
    ):
        value = result.get(key)
        if value not in (None, ""):
            parts.append(str(value))
    status = result.get("status")
    if isinstance(status, str):
        parts.append(status)
    return "\n".join(parts).strip()


def sonic_result_is_finished(result: dict) -> bool:
    send_count = _safe_int(result.get("sendMsgCount") or result.get("send_msg_count"), 0)
    receive_count = _safe_int(result.get("receiveMsgCount") or result.get("receive_msg_count"), 0)
    return bool(send_count and receive_count >= send_count)


def sonic_result_time_score(result: dict, suite: dict) -> int:
    result_ts = sonic_result_timestamp(result)
    suite_start = _safe_int(suite.get("created_ts"), 0)
    suite_end = _safe_int(suite.get("last_update_ts"), 0) or int(time.time())
    if not result_ts:
        return -1
    if suite_start and suite_end:
        window_start = suite_start - sonic_report_window_before_seconds()
        window_end = suite_end + sonic_report_window_after_seconds()
        if not (window_start <= result_ts <= window_end):
            return -1
        return 5000 - min(abs(result_ts - suite_end), 5000)
    if suite_end:
        return max(0, 1200 - abs(result_ts - suite_end))
    return -1


def sonic_score_result_for_suite(result: dict, suite: dict, project_id: int) -> int:
    result_id = _safe_int(result.get("id"), 0)
    if not result_id:
        return -1
    expected_suite_id = sonic_suite_config_id(suite)
    result_suite_id = _safe_int(result.get("suiteId") or result.get("suite_id"), 0)
    if expected_suite_id and result_suite_id and expected_suite_id != result_suite_id:
        return -1
    if not sonic_result_is_finished(result):
        return -1
    time_score = sonic_result_time_score(result, suite)
    if time_score < 0:
        return -1
    score = 0
    if _safe_int(result.get("projectId") or result.get("project_id"), project_id) == project_id:
        score += 100
    if expected_suite_id and result_suite_id == expected_suite_id:
        score += 3000
    suite_names = sonic_suite_expected_name(suite)
    result_suite_name = re.sub(r"\s+", "", str(result.get("suiteName") or result.get("suite_name") or ""))
    if result_suite_name:
        if result_suite_name in suite_names:
            score += 2000
        elif any(name and (name in result_suite_name or result_suite_name in name) for name in suite_names):
            score += 600
    status = _safe_int(result.get("status"), 0)
    if status:
        score += 50
    else:
        score -= 1000
    send_count = _safe_int(result.get("sendMsgCount") or result.get("send_msg_count"), 0)
    receive_count = _safe_int(result.get("receiveMsgCount") or result.get("receive_msg_count"), 0)
    if send_count and receive_count >= send_count:
        score += 120
    score += time_score
    return score


def sonic_score_result_meta_for_suite(result: dict, suite: dict, project_id: int) -> int:
    result_id = _safe_int(result.get("id"), 0)
    if not result_id:
        return -1
    expected_suite_id = sonic_suite_config_id(suite)
    result_suite_id = _safe_int(result.get("suiteId") or result.get("suite_id"), 0)
    if expected_suite_id and result_suite_id and expected_suite_id != result_suite_id:
        return -1
    time_score = sonic_result_time_score(result, suite)
    if time_score < 0:
        return -1
    score = time_score
    if _safe_int(result.get("projectId") or result.get("project_id"), project_id) == project_id:
        score += 100
    if expected_suite_id and result_suite_id == expected_suite_id:
        score += 3000
    suite_names = sonic_suite_expected_name(suite)
    result_suite_name = re.sub(r"\s+", "", str(result.get("suiteName") or result.get("suite_name") or ""))
    if result_suite_name:
        if result_suite_name in suite_names:
            score += 2000
        elif any(name and (name in result_suite_name or result_suite_name in name) for name in suite_names):
            score += 600
    elif suite_names:
        score -= 120
    send_count = _safe_int(result.get("sendMsgCount") or result.get("send_msg_count"), 0)
    receive_count = _safe_int(result.get("receiveMsgCount") or result.get("receive_msg_count"), 0)
    if send_count:
        score += 260
    if receive_count:
        score += min(receive_count, 50)
    if sonic_result_is_finished(result):
        score += 80
    return score


def lookup_sonic_result_meta_for_suite(suite: dict) -> dict:
    app = suite.get("app") or sonic_suite_app_info(suite.get("app_package", ""), "")
    package = suite.get("app_package") or app.get("package", "")
    project_id = sonic_project_id_for_app(app) or sonic_project_id_for_package(package)
    if not project_id:
        return {"error": "未找到 Sonic 项目 ID"}
    best = None
    best_score = -1
    candidates = []
    for page in range(1, 4):
        try:
            items = sonic_list_results(project_id, page=page, page_size=15)
        except Exception as e:
            if sonic_error_is_unauthorized(e):
                return sonic_results_permission_error(project_id, e)
            return {"project_id": project_id, "error": str(e)}
        if not items:
            break
        for item in items:
            score = sonic_score_result_meta_for_suite(item, suite, project_id)
            item_id = _safe_int(item.get("id"), 0)
            if item_id:
                candidates.append({
                    "id": item_id,
                    "suiteId": item.get("suiteId") or item.get("suite_id"),
                    "suiteName": item.get("suiteName") or item.get("suite_name") or "",
                    "status": item.get("status"),
                    "statusText": sonic_result_status_text(item),
                    "sendMsgCount": item.get("sendMsgCount") or item.get("send_msg_count"),
                    "receiveMsgCount": item.get("receiveMsgCount") or item.get("receive_msg_count"),
                    "createTime": item.get("createTime") or item.get("create_time") or "",
                    "endTime": item.get("endTime") or item.get("end_time") or "",
                    "finished": sonic_result_is_finished(item),
                    "score": score,
                })
            if score > best_score:
                best = item
                best_score = score
    if best and best_score > 0:
        result_id = _safe_int(best.get("id"), 0)
        send_count = _safe_int(best.get("sendMsgCount") or best.get("send_msg_count"), 0)
        receive_count = _safe_int(best.get("receiveMsgCount") or best.get("receive_msg_count"), 0)
        return {
            "project_id": project_id,
            "result_id": result_id,
            "suite_id": _safe_int(best.get("suiteId") or best.get("suite_id"), 0),
            "score": best_score,
            "suite_name": best.get("suiteName") or best.get("suite_name") or "",
            "send_msg_count": send_count,
            "receive_msg_count": receive_count,
            "expected_total_count": send_count,
            "sonic_report_url": sonic_result_detail_url(project_id, result_id),
            "status": best.get("status"),
            "status_text": sonic_result_status_text(best),
            "finished": sonic_result_is_finished(best),
            "createTime": best.get("createTime") or best.get("create_time") or "",
            "endTime": best.get("endTime") or best.get("end_time") or "",
            "candidates": candidates[:8],
        }
    return {"project_id": project_id, "error": "未匹配到 Sonic 测试结果", "candidates": candidates[:8]}


def lookup_sonic_report_for_suite(suite: dict) -> Tuple[str, dict]:
    app = suite.get("app") or sonic_suite_app_info(suite.get("app_package", ""), "")
    package = suite.get("app_package") or app.get("package", "")
    project_id = sonic_project_id_for_app(app) or sonic_project_id_for_package(package)
    if not project_id:
        return "", {"error": "未找到 Sonic 项目 ID"}
    best = None
    best_score = -1
    candidates = []
    for page in range(1, 4):
        try:
            items = sonic_list_results(project_id, page=page, page_size=15)
        except Exception as e:
            if sonic_error_is_unauthorized(e):
                return "", sonic_results_permission_error(project_id, e)
            raise
        if not items:
            break
        for item in items:
            score = sonic_score_result_for_suite(item, suite, project_id)
            item_id = _safe_int(item.get("id"), 0)
            if item_id:
                candidates.append({
                    "id": item_id,
                    "suiteId": item.get("suiteId") or item.get("suite_id"),
                    "suiteName": item.get("suiteName") or item.get("suite_name") or "",
                    "status": item.get("status"),
                    "sendMsgCount": item.get("sendMsgCount") or item.get("send_msg_count"),
                    "receiveMsgCount": item.get("receiveMsgCount") or item.get("receive_msg_count"),
                    "createTime": item.get("createTime") or item.get("create_time") or "",
                    "endTime": item.get("endTime") or item.get("end_time") or "",
                    "finished": sonic_result_is_finished(item),
                    "score": score,
                })
            if score > best_score:
                best = item
                best_score = score
    if best and best_score > 0:
        result_id = _safe_int(best.get("id"), 0)
        return sonic_result_detail_url(project_id, result_id), {
            "project_id": project_id,
            "result_id": result_id,
            "suite_id": _safe_int(best.get("suiteId") or best.get("suite_id"), 0),
            "score": best_score,
            "suite_name": best.get("suiteName") or best.get("suite_name") or "",
            "send_msg_count": _safe_int(best.get("sendMsgCount") or best.get("send_msg_count"), 0),
            "receive_msg_count": _safe_int(best.get("receiveMsgCount") or best.get("receive_msg_count"), 0),
            "createTime": best.get("createTime") or best.get("create_time") or "",
            "endTime": best.get("endTime") or best.get("end_time") or "",
            "candidates": candidates[:8],
        }
    return "", {"project_id": project_id, "error": "未匹配到 Sonic 测试结果", "candidates": candidates[:8]}


# ---------------------------------------------------------------------------
# 套件结果合并 / 迁移
# ---------------------------------------------------------------------------

def merge_sonic_suite_result_items(*groups: list) -> list:
    merged = []
    index = {}
    for items in groups:
        for item in items or []:
            row = dict(item or {})
            identity = (
                str(row.get("job_id") or "").strip()
                or "|".join(str(row.get(key) or "").strip() for key in (
                    "case_id", "module", "file", "target_task_name", "started_at"
                ))
            )
            if identity and identity in index:
                current = merged[index[identity]]
                current.update({
                    key: value for key, value in row.items()
                    if value not in ("", None)
                })
            else:
                if identity:
                    index[identity] = len(merged)
                merged.append(row)
    return merged


def merge_sonic_suite_results(left: dict, right: dict) -> list:
    merged = []
    seen = set()
    for item in list((left or {}).get("results") or []) + list((right or {}).get("results") or []):
        identity = sonic_suite_result_identity(item) or f"anon:{len(merged)}"
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(item)
    return merged


def sonic_suite_result_key_from_meta(meta: dict) -> str:
    meta = meta or {}
    project_id = _safe_int(meta.get("project_id") or meta.get("projectId"), 0)
    result_id = _safe_int(meta.get("result_id") or meta.get("resultId") or meta.get("id"), 0)
    if project_id and result_id:
        return f"sonic_result_{project_id}_{result_id}"
    return ""


def migrate_sonic_suite_to_result_key(state: dict, suite_key: str, suite: dict) -> Tuple[str, dict]:
    canonical_key = sonic_suite_result_key_from_meta((suite or {}).get("sonic_result_meta") or {})
    if not canonical_key or canonical_key == suite_key:
        return suite_key, suite
    suites = state.setdefault("suites", {})
    existing = suites.get(canonical_key)
    if existing and existing is not suite:
        merged = dict(existing)
        for key, value in suite.items():
            if key == "results":
                continue
            if value not in ("", None, [], {}):
                if key in ("sent_at", "send_error", "completion_final_sent", "feishu") and existing.get(key):
                    continue
                merged[key] = value
        merged["suite_key"] = canonical_key
        merged["results"] = merge_sonic_suite_results(existing, suite)
        suites[canonical_key] = merged
        suite = merged
    else:
        suite["suite_key"] = canonical_key
        suites[canonical_key] = suite
    if suite_key in suites and suite_key != canonical_key:
        suites.pop(suite_key, None)
        try:
            old_timer = cfg.SONIC_SUITE_TIMERS.pop(suite_key, None)
            if old_timer:
                old_timer.cancel()
        except Exception:
            pass
        _append_notify_log("suite_summary_migrated_to_result_key", {
            "old_suite_key": suite_key,
            "suite_key": canonical_key,
        })
    return canonical_key, suite


def sonic_suite_same_result_already_sent(state: dict, suite_key: str, suite: dict) -> str:
    suite = suite or {}
    project_id = _safe_int(
        suite.get("sonic_project_id")
        or (suite.get("sonic_result_meta") or {}).get("project_id")
        or (suite.get("sonic_result_meta") or {}).get("projectId"),
        0,
    )
    result_id = _safe_int(
        suite.get("sonic_result_id")
        or (suite.get("sonic_result_meta") or {}).get("result_id")
        or (suite.get("sonic_result_meta") or {}).get("resultId"),
        0,
    )
    if not project_id or not result_id:
        return ""
    for key, other in (state.get("suites") or {}).items():
        if key == suite_key:
            continue
        other_project_id = _safe_int(
            other.get("sonic_project_id")
            or (other.get("sonic_result_meta") or {}).get("project_id")
            or (other.get("sonic_result_meta") or {}).get("projectId"),
            0,
        )
        other_result_id = _safe_int(
            other.get("sonic_result_id")
            or (other.get("sonic_result_meta") or {}).get("result_id")
            or (other.get("sonic_result_meta") or {}).get("resultId"),
            0,
        )
        if (
            other_project_id == project_id
            and other_result_id == result_id
            and other.get("sent_at")
            and not other.get("send_error")
        ):
            return key
    return ""


def mark_sonic_suite_completed_from_result_meta(suite: dict) -> dict:
    meta = (suite or {}).get("sonic_result_meta") or {}
    if not meta.get("finished"):
        return suite
    suite["completion_received"] = True
    suite["completion_source"] = "sonic_results_api"
    suite["completion_ts"] = suite.get("completion_ts") or time.strftime("%Y-%m-%d %H:%M:%S")
    project_id = _safe_int(meta.get("project_id"), 0)
    result_id = _safe_int(meta.get("result_id"), 0)
    if project_id:
        suite["sonic_project_id"] = project_id
    if result_id:
        suite["sonic_result_id"] = result_id
    if meta.get("suite_id") and not suite.get("sonic_suite_id"):
        suite["sonic_suite_id"] = str(meta.get("suite_id"))
    if meta.get("suite_name") and not suite.get("sonic_suite_name"):
        suite["sonic_suite_name"] = meta.get("suite_name")
    if meta.get("sonic_report_url"):
        suite["sonic_report_url"] = meta.get("sonic_report_url")
    expected = _safe_int(meta.get("expected_total_count") or meta.get("send_msg_count"), 0)
    if expected:
        suite["expected_total_count"] = max(_safe_int(suite.get("expected_total_count"), 0), expected)
    return suite


# ---------------------------------------------------------------------------
# 报告 HTML 生成
# ---------------------------------------------------------------------------

def _public_report_url(filename: str) -> str:
    base = os.getenv("TASK_PUBLIC_BASE_URL") or os.getenv("MIDSCENE_PUBLIC_BASE_URL") or "http://101.34.197.12:8088"
    base = base.rstrip("/")
    return f"{base}/reports/{urllib.parse.quote(filename)}"


def write_sonic_suite_summary_report(suite: dict) -> str:
    results = list((suite or {}).get("results") or [])
    app = suite.get("app") or sonic_suite_app_info(suite.get("app_package", ""), "")
    app_name = sonic_notify_pretty_title_text(app.get("name") or suite.get("app_name") or app.get("package") or "Sonic")
    run_mode = suite.get("run_mode") or "baseline"
    mode_label = "基线回归" if run_mode == "baseline" else "测试执行"
    status = sonic_suite_effective_status(suite)
    status_meta = sonic_suite_status_meta(status)
    status_text = status_meta["text"]
    status_class = status_meta["class"]
    stats = sonic_suite_display_stats(suite)
    total = stats["total"]
    passed = stats["passed"]
    failed = stats["failed"]
    warning = stats["warning"]
    pending = stats.get("pending", 0)
    missing_task_callbacks = stats.get("missing_task_callbacks", pending)
    suite_key = _clean_id(suite.get("suite_key") or unique_millis_id("sonic_suite"), "sonic_suite")
    sonic_report_url = ensure_sonic_suite_report_url(suite) or suite.get("report_url") or ""
    sonic_lookup_message = sonic_suite_report_lookup_message(suite)
    result_modules = sorted({item.get("module") for item in results if item.get("module")})
    pending_module = result_modules[0] if result_modules else suite.get("module")
    duration = sonic_suite_duration_text(suite)
    started_ts, finished_ts, time_source = sonic_suite_time_range(suite)
    started_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started_ts)) if started_ts else ""
    finished_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(finished_ts)) if finished_ts else ""

    def h(value):
        return html_lib.escape(str(value or ""), quote=True)

    rows = []
    for idx, item in enumerate(results, start=1):
        item_status = item.get("status") or "-"
        cls = "pass" if item_status == "success" else ("fail" if item_status == "failed" else "warn")
        label = "通过" if item_status == "success" else ("失败" if item_status == "failed" else item_status)
        midscene_url = item.get("report_url") or ""
        report_pending = _safe_bool(item.get("report_upload_pending"))
        report_error = item.get("report_upload_error")
        if str(midscene_url).startswith("http") and report_pending:
            midscene_link = f'<a href="{h(midscene_url)}" target="_blank">Midscene 报告（上传中）</a>'
        elif str(midscene_url).startswith("http") and report_error:
            midscene_link = f'<a href="{h(midscene_url)}" target="_blank">Midscene 报告（上传失败）</a>'
        elif str(midscene_url).startswith("http"):
            midscene_link = f'<a href="{h(midscene_url)}" target="_blank">Midscene 报告</a>'
        elif report_pending:
            midscene_link = '<span class="muted">后台上传中</span>'
        elif report_error:
            midscene_link = '<span class="muted">上传失败</span>'
        else:
            midscene_link = '<span class="muted">无</span>'
        reason = sonic_notify_compact(item.get("error") or item.get("stderr_tail") or item.get("progress_message") or "", 300)
        rows.append(
            "<tr>"
            f"<td>{idx}</td>"
            f"<td><span class='badge {cls}'>{h(label)}</span></td>"
            f"<td>{h(sonic_notify_display_value(item.get('module')))}</td>"
            f"<td>{h(sonic_notify_display_value(item.get('target_task_name') or item.get('current_task_name') or item.get('file')))}</td>"
            f"<td>{h(item.get('device_id'))}</td>"
            f"<td>{midscene_link}</td>"
            f"<td class='reason'>{h(reason)}</td>"
            "</tr>"
        )
    if missing_task_callbacks:
        missing_ignored = _safe_bool(stats.get("missing_task_callbacks_ignored_by_sonic_success"))
        if missing_ignored:
            missing_label = "按 Sonic 通过"
            missing_cls = "pass"
            missing_reason = "Task 平台未收到该用例桥接回传，但 Sonic 原始报告已通过且未返回失败内容，已按 Sonic 结果汇总。"
        else:
            missing_label = "未回传"
            missing_cls = "warn"
            missing_reason = (
                "Sonic 原始报告已结束，但 Task 平台未收到该用例的桥接回传；请以 Sonic 原始报告为准，或检查 Groovy 桥接脚本/接口权限。"
                if sonic_suite_finished_in_sonic(suite)
                else "仍在等待 Sonic Agent 回传结果。"
            )
        for offset in range(1, missing_task_callbacks + 1):
            rows.append(
                "<tr>"
                f"<td>{len(rows) + 1}</td>"
                f"<td><span class='badge {missing_cls}'>{h(missing_label)}</span></td>"
                f"<td>{h(sonic_notify_display_value(pending_module, '-'))}</td>"
                f"<td>未回传用例 {offset}</td>"
                f"<td>{h(sonic_notify_display_value(suite.get('device_id'), '-'))}</td>"
                "<td><span class='muted'>请查看 Sonic 原始报告</span></td>"
                f"<td class='reason'>{h(missing_reason)}</td>"
                "</tr>"
            )
    if not rows:
        rows.append("<tr><td colspan='7' class='empty'>暂无用例结果</td></tr>")

    sonic_link = (
        f'<a class="button" href="{h(sonic_report_url)}" target="_blank">查看 Sonic 原始报告</a>'
        if str(sonic_report_url).startswith("http")
        else ""
    )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{h(app_name)} {h(mode_label)}{h(status_text)}</title>
  <style>
    :root {{ color-scheme: light; --bg:#f6f8fb; --card:#fff; --text:#162033; --muted:#667085; --line:#e4e7ec; --pass:#12b76a; --fail:#f04438; --warn:#f79009; }}
    body {{ margin:0; padding:28px; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .wrap {{ max-width:1180px; margin:0 auto; }}
    .hero {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:24px; box-shadow:0 8px 24px rgba(16,24,40,.06); }}
    h1 {{ margin:0 0 14px; font-size:24px; }}
    .meta {{ display:flex; flex-wrap:wrap; gap:10px; color:var(--muted); font-size:14px; }}
    .pill {{ border:1px solid var(--line); border-radius:999px; padding:6px 10px; background:#fff; }}
    .badge {{ display:inline-flex; min-width:44px; justify-content:center; border-radius:999px; padding:4px 10px; color:#fff; font-weight:700; font-size:12px; }}
    .pass {{ background:var(--pass); }} .fail {{ background:var(--fail); }} .warn {{ background:var(--warn); }}
    .actions {{ margin-top:18px; display:flex; gap:10px; flex-wrap:wrap; }}
    .button {{ display:inline-block; border-radius:8px; padding:9px 14px; background:#155eef; color:#fff; text-decoration:none; font-weight:700; }}
    table {{ width:100%; border-collapse:separate; border-spacing:0; margin-top:18px; background:var(--card); border:1px solid var(--line); border-radius:12px; overflow:hidden; }}
    th, td {{ padding:12px 14px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:14px; }}
    th {{ background:#f2f4f7; color:#475467; font-weight:700; }}
    tr:last-child td {{ border-bottom:0; }}
    a {{ color:#155eef; font-weight:700; }}
    .reason {{ max-width:420px; color:#475467; line-height:1.55; }}
    .muted, .empty {{ color:var(--muted); }}
    @media (max-width:760px) {{ body {{ padding:14px; }} table {{ display:block; overflow-x:auto; }} .hero {{ padding:18px; }} }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <h1>{h(app_name)}｜{h(mode_label)}{h(status_text)}</h1>
      <div class="meta">
        <span class="pill">结论：<b class="{status_class}" style="background:transparent;color:var(--{status_class})">{h(status_text)}</b></span>
        <span class="pill">总数：{total}</span>
        <span class="pill">通过：{passed}</span>
        <span class="pill">失败：{failed}</span>
        <span class="pill">告警：{warning}</span>
        {f'<span class="pill">待回传：{pending}</span>' if pending else ''}
        {f'<span class="pill">开始：{h(started_text)}</span>' if started_text else ''}
        {f'<span class="pill">结束：{h(finished_text)}</span>' if finished_text else ''}
        {f'<span class="pill">耗时：{h(duration)}</span>' if duration else ''}
        <span class="pill">生成时间：{h(time.strftime("%Y-%m-%d %H:%M:%S"))}</span>
        {f'<span class="pill">时间来源：{h(time_source)}</span>' if time_source else ''}
        {f'<span class="pill">{h(sonic_lookup_message)}</span>' if sonic_lookup_message else ''}
      </div>
      <div class="actions">{sonic_link}</div>
    </section>
    <table>
      <thead><tr><th>#</th><th>状态</th><th>模块</th><th>用例</th><th>设备</th><th>报告</th><th>失败/备注</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </main>
</body>
</html>
"""
    filename = f"{suite_key}-summary.html"
    write_text_file(os.path.join(cfg.REPORT_DIR, filename), html)
    return _public_report_url(filename)


# ---------------------------------------------------------------------------
# 飞书卡片构建
# ---------------------------------------------------------------------------

def build_sonic_suite_summary_card(suite: dict) -> dict:
    results = list((suite or {}).get("results") or [])
    app = suite.get("app") or sonic_suite_app_info(suite.get("app_package", ""), "")
    app_name = sonic_notify_pretty_title_text(app.get("name") or suite.get("app_name") or app.get("package") or "Sonic")
    run_mode = suite.get("run_mode") or "baseline"
    mode_label = "基线回归" if run_mode == "baseline" else "测试执行"
    status = sonic_suite_effective_status(suite)
    status_meta = sonic_suite_status_meta(status)
    color = status_meta["color"]
    icon = status_meta["icon"]
    status_label = status_meta["text"]
    stats = sonic_suite_display_stats(suite)
    total = stats["total"]
    passed = stats["passed"]
    failed = stats["failed"]
    warning = stats["warning"]
    duration = sonic_suite_duration_text(suite)
    devices = sorted({item.get("device_id") for item in results if item.get("device_id")})
    modules = sorted({item.get("module") for item in results if item.get("module")})
    elements = [
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**结论：** <font color='{color}'>{icon} {status_label}</font>"}},
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**应用：** {app_name}"}},
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**范围：** {mode_label} · {total} 条用例"}},
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**统计：** 通过 {passed} / 失败 {failed} / 告警 {warning}"}},
    ]
    if stats.get("pending"):
        pending_text = (
            f"{stats.get('pending')} 条用例在 Sonic 已结束后仍未回传 Task 平台，请检查桥接脚本或接口权限"
            if sonic_suite_finished_in_sonic(suite)
            else f"{stats.get('pending')} 条用例仍未收到结果，已按等待上限生成当前汇总"
        )
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**待回传：** {pending_text}"}})
    elif stats.get("missing_task_callbacks_ignored_by_sonic_success"):
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": (
                    f"**回传提示：** {stats.get('missing_task_callbacks')} 条 Task 桥接回调未收到，"
                    "但 Sonic 原始报告已通过且未返回失败内容，已按 Sonic 结果汇总"
                ),
            },
        })
    extra = []
    if modules:
        extra.append("模块：" + "、".join(modules[:4]) + (" 等" if len(modules) > 4 else ""))
    if devices:
        extra.append("设备：" + "、".join(devices[:3]) + (" 等" if len(devices) > 3 else ""))
    if duration:
        extra.append("耗时：" + duration)
    if extra:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "**补充：** " + sonic_notify_clean_text(" · ".join(extra))}})
    sonic_report_url = ensure_sonic_suite_report_url(suite)
    lookup_message = sonic_suite_report_lookup_message(suite)
    if lookup_message:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**Sonic 报告：** {lookup_message}"}})
    pending_reports = sonic_suite_pending_midscene_reports(suite)
    if pending_reports:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**Midscene 报告：** {pending_reports} 份仍在后台上传，汇总报告会自动补充链接",
            }
        })
    failed_items = [item for item in results if item.get("status") == "failed"]
    if failed_items:
        lines = []
        for item in failed_items[:5]:
            reason = sonic_notify_compact(item.get("error") or item.get("stderr_tail") or item.get("progress_message") or "请查看报告", 80)
            lines.append(f"- {sonic_suite_result_line(item)}：{reason}")
        if len(failed_items) > 5:
            lines.append(f"- 还有 {len(failed_items) - 5} 条失败，请在 Task 平台执行中心查看")
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "**失败明细：**\n" + "\n".join(lines)}})
    sonic_report_urls = [
        sonic_report_url,
        suite.get("sonic_report_url"),
        suite.get("report_url"),
    ] + [
        item.get("sonic_report_url")
        for item in results
        if str(item.get("sonic_report_url") or "").startswith("http")
    ]
    sonic_report_urls = [url for url in sonic_report_urls if str(url or "").startswith("http")]
    report_urls = [
        item.get("report_url")
        for item in results
        if str(item.get("report_url") or "").startswith("http")
    ]
    suite_report_url = suite.get("suite_report_url") or suite.get("summary_report_url") or ""
    actions = []
    if str(suite_report_url).startswith("http"):
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看汇总报告"},
            "url": suite_report_url,
            "type": "primary",
        })
    if sonic_report_urls:
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看 Sonic 报告"},
            "url": sonic_report_urls[0],
            "type": "default" if actions else "primary",
        })
    if report_urls and not actions:
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看 Midscene 报告"},
            "url": report_urls[0],
            "type": "default" if sonic_report_urls else "primary",
        })
    if actions:
        elements.append({"tag": "hr"})
        elements.append({"tag": "action", "actions": actions})
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": color,
                "title": {"tag": "plain_text", "content": f"{icon} {app_name}｜{mode_label}{status_label}"},
            },
            "elements": elements,
        },
    }


# ---------------------------------------------------------------------------
# 套件完成事件注册
# ---------------------------------------------------------------------------

def register_sonic_suite_completion(event: dict) -> dict:
    now_ts = int(time.time())
    app = sonic_suite_app_for_completion(event)
    with cfg.SONIC_SUITE_LOCK:
        state = load_sonic_suite_results()
        suites = state.setdefault("suites", {})
        result_id = _safe_int(event.get("result_id"), 0)
        project_id = _safe_int(event.get("project_id"), 0)
        matched_key = sonic_suite_key_for_completion_event(event, app, state, now_ts)
        suite = suites.get(matched_key) or {
            "suite_key": matched_key,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "created_ts": now_ts,
            "results": [],
        }
        same_finished_event = bool(
            suite.get("completion_final_sent")
            and suite.get("sent_at")
            and not suite.get("send_error")
            and _safe_int(suite.get("sonic_result_id"), 0)
            and _safe_int(suite.get("sonic_result_id"), 0) == _safe_int(event.get("result_id"), 0)
        )
        fixed_report_url = sonic_suite_fixed_report_url(
            suite,
            project_id=project_id,
            result_id=result_id,
            suite_key=matched_key,
        )
        suite.update({
            "last_update_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_update_ts": now_ts,
            "completion_received": True,
            "completion_source": "sonic_callback",
            "completion_ts": now_ts,
            "app": app,
            "app_package": app.get("package") or event.get("app_package") or suite.get("app_package", ""),
            "app_name": app.get("name") or event.get("app_name") or suite.get("app_name", ""),
            "sonic_suite_id": str(event.get("suite_id") or suite.get("sonic_suite_id", "")),
            "sonic_suite_name": event.get("suite_name") or suite.get("sonic_suite_name", ""),
            "sonic_result_id": _safe_int(event.get("result_id"), 0) or suite.get("sonic_result_id", 0),
            "sonic_project_id": _safe_int(event.get("project_id"), 0) or suite.get("sonic_project_id", 0),
            "sonic_report_url": event.get("report_url") or suite.get("sonic_report_url", "") or fixed_report_url,
            "expected_total_count": max(_safe_int(suite.get("expected_total_count"), 0), _safe_int(event.get("total"), 0)),
            "run_mode": suite.get("run_mode") or "baseline",
            "sonic_completion": {
                "finished": True,
                "status": event.get("status") or "warning",
                "total": _safe_int(event.get("total"), 0),
                "passed": _safe_int(event.get("passed"), 0),
                "failed": _safe_int(event.get("failed"), 0),
                "warning": _safe_int(event.get("warning"), 0),
                "interrupted": event.get("status") == "interrupted",
                "duration": event.get("duration") or "",
                "createTime": event.get("createTime") or "",
                "endTime": event.get("endTime") or "",
            },
            "notification_mode": "suite_completion",
        })
        suites[matched_key] = suite
        save_sonic_suite_results(state)
    _append_notify_log("sonic_suite_completion_received", {
        "suite_key": matched_key,
        "project_id": event.get("project_id"),
        "result_id": event.get("result_id"),
        "suite_name": event.get("suite_name"),
        "total": event.get("total"),
        "status": event.get("status"),
        "duplicate": same_finished_event,
    })
    if not same_finished_event:
        schedule_sonic_suite_summary(matched_key, delay=5)
    return {"suite_key": matched_key, "duplicate": same_finished_event, "suite": suite}


# ---------------------------------------------------------------------------
# 套件结果注册（用例回调）
# ---------------------------------------------------------------------------

def sonic_suite_natural_key(job: dict) -> str:
    return "|".join([
        job.get("app_package") or _resolve_app_package(job.get("module", ""), job.get("file", ""), "", allow_default=False) or "",
        job.get("sonic_suite_id") or job.get("sonicSuiteId") or "",
        job.get("sonic_suite_name") or "",
        job.get("suite_started_at") or "",
        job.get("runner_id") or "sonic",
        job.get("device_id") or "",
        job.get("run_mode") or "baseline",
    ])


def sonic_suite_key_for_job(job: dict, state: dict, now_ts: int) -> str:
    explicit = job.get("suite_run_id") or job.get("suiteRunId")
    if explicit:
        return str(explicit)
    natural = sonic_suite_natural_key(job)
    active_key = (state.get("active") or {}).get(natural)
    if active_key:
        suite = (state.get("suites") or {}).get(active_key) or {}
        last_ts = _safe_int(suite.get("last_update_ts") or suite.get("created_ts"), 0)
        if sonic_suite_has_complete_result_cycle(suite) and sonic_suite_contains_job_identity(suite, job):
            suite["closed_at"] = suite.get("closed_at") or time.strftime("%Y-%m-%d %H:%M:%S")
            suite["closed_reason"] = suite.get("closed_reason") or "下一轮 Sonic 测试套已开始，停止追加上一轮结果"
            state.setdefault("suites", {})[active_key] = suite
            state.setdefault("active", {}).pop(natural, None)
        elif suite.get("sent_at") and not suite.get("send_error"):
            state.setdefault("active", {}).pop(natural, None)
        elif not last_ts or now_ts - last_ts <= sonic_suite_reopen_seconds():
            return active_key
    active_key = (state.get("active") or {}).get(natural)
    if active_key:
        suite = (state.get("suites") or {}).get(active_key) or {}
        last_ts = _safe_int(suite.get("last_update_ts") or suite.get("created_ts"), 0)
        if (
            not suite.get("sent_at")
            and (not last_ts or now_ts - last_ts <= sonic_suite_reopen_seconds())
        ):
            return active_key
    suite_key = unique_millis_id("sonic_suite")
    state.setdefault("active", {})[natural] = suite_key
    return suite_key


def sonic_job_matches_suite(job: dict, suite: dict) -> bool:
    """判断 job 是否属于给定的 suite。"""
    if not job or job.get("source") != "sonic":
        return False
    suite_run_id = suite.get("suite_run_id") or suite.get("suiteRunId")
    job_suite_run_id = job.get("suite_run_id") or job.get("suiteRunId")
    if suite_run_id and job_suite_run_id:
        return str(suite_run_id) == str(job_suite_run_id)
    suite_key = suite.get("natural_key") or ""
    return bool(suite_key and sonic_suite_natural_key(job) == suite_key)


def sonic_suite_has_running_jobs(suite: dict) -> bool:
    """检查 suite 是否仍有 pending/running 的 job。"""
    try:
        from .job_service import load_jobs
    except ImportError:
        return False
    for job in load_jobs():
        if job.get("status") not in ("pending", "running"):
            continue
        if sonic_job_matches_suite(job, suite):
            return True
    return False


def sonic_suite_can_wait_for_running_jobs(suite: dict, now_ts: Optional[int] = None) -> bool:
    """判断是否仍在允许等待 running job 的时间窗口内。"""
    now_ts = now_ts or int(time.time())
    created_ts = _safe_int(suite.get("created_ts"), 0) or now_ts
    return now_ts - created_ts < sonic_suite_max_wait_seconds()


def register_sonic_suite_result(job: dict) -> str:
    """注册 Sonic 用例回调结果到套件。"""
    if not job or job.get("source") != "sonic" or job.get("status") in ("pending", "running"):
        return ""
    now_ts = int(time.time())
    updated_suite_for_summary_refresh = None
    already_notified_final = False
    with cfg.SONIC_SUITE_LOCK:
        state = load_sonic_suite_results()
        suite_key = sonic_suite_key_for_job(job, state, now_ts)
        suite = (state.get("suites") or {}).get(suite_key) or {
            "suite_key": suite_key,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "created_ts": now_ts,
            "results": [],
        }
        app = sonic_suite_app_info(job.get("app_package", ""), job.get("module", ""))
        suite_started_at = job.get("suite_started_at") or job.get("suiteStartedAt") or suite.get("suite_started_at", "")
        suite_start_ts = _parse_time(suite_started_at)
        expected_total = max(
            _safe_int(suite.get("expected_total_count"), 0),
            _safe_int(job.get("suite_expected_total") or job.get("suiteExpectedTotal"), 0),
        )
        suite.update({
            "last_update_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_update_ts": now_ts,
            "natural_key": sonic_suite_natural_key(job),
            "suite_run_id": job.get("suite_run_id") or job.get("suiteRunId") or suite.get("suite_run_id", ""),
            "sonic_suite_id": job.get("sonic_suite_id") or job.get("sonicSuiteId") or app.get("sonic_suite_id") or app.get("sonicSuiteId") or suite.get("sonic_suite_id", ""),
            "app_package": app.get("package") or job.get("app_package", ""),
            "app_name": app.get("name") or job.get("app_name", ""),
            "app": app,
            "sonic_suite_name": job.get("sonic_suite_name") or app.get("sonic_suite_name") or app.get("sonicSuiteName") or suite.get("sonic_suite_name", ""),
            "suite_started_at": suite_started_at,
            "expected_total_count": expected_total,
            "run_mode": job.get("run_mode") or "baseline",
            "runner_id": job.get("runner_id") or "sonic",
            "device_id": job.get("device_id") or "",
        })
        if suite_start_ts:
            existing_created_ts = _safe_int(suite.get("created_ts"), 0)
            suite["created_ts"] = min(existing_created_ts, suite_start_ts) if existing_created_ts else suite_start_ts
            suite["created_at"] = suite_started_at
        result = {
            "job_id": job.get("job_id", ""),
            "case_id": job.get("case_id", ""),
            "module": sonic_notify_clean_text(job.get("module", "")),
            "file": sonic_notify_clean_text(job.get("file", "")),
            "target_task_name": sonic_notify_clean_text(job.get("target_task_name", "")),
            "current_task_name": sonic_notify_clean_text(job.get("current_task_name", "")),
            "status": job.get("status", ""),
            "run_mode": job.get("run_mode", ""),
            "runner_id": job.get("runner_id", ""),
            "device_id": job.get("device_id", ""),
            "report_url": job.get("report_url", ""),
            "report_upload_pending": _safe_bool(job.get("report_upload_pending")),
            "report_upload_error": job.get("report_upload_error", ""),
            "sonic_report_url": job.get("sonic_report_url", ""),
            "sonic_suite_id": job.get("sonic_suite_id", ""),
            "sonic_suite_name": job.get("sonic_suite_name", ""),
            "suite_started_at": job.get("suite_started_at", ""),
            "suite_expected_total": _safe_int(job.get("suite_expected_total") or job.get("suiteExpectedTotal"), 0),
            "error": sonic_notify_clean_text(job.get("error", "") or job.get("stderr_tail", "")),
            "stderr_tail": sonic_notify_clean_text(job.get("stderr_tail", "")),
            "progress_message": sonic_notify_clean_text(job.get("progress_message", "")),
            "completed_task_count": _safe_int(job.get("completed_task_count") or job.get("completedTaskCount"), 0),
            "total_task_count": _safe_int(job.get("total_task_count") or job.get("totalTaskCount"), 0),
            "created_at": job.get("created_at", ""),
            "started_at": job.get("started_at", ""),
            "finished_at": job.get("finished_at", ""),
        }
        results = [item for item in (suite.get("results") or []) if item.get("job_id") != result["job_id"]]
        results.append(result)
        suite["results"] = results
        completion_only = sonic_suite_waits_for_completion_event(job)
        suite["notification_mode"] = "suite_completion" if completion_only else "case_quiet_period"
        if not completion_only:
            suite["sent_at"] = ""
        already_notified_final = bool(
            completion_only
            and suite.get("completion_final_sent")
            and suite.get("sent_at")
            and not suite.get("send_error")
        )
        state.setdefault("suites", {})[suite_key] = suite
        save_sonic_suite_results(state)
        if suite.get("suite_report_url"):
            updated_suite_for_summary_refresh = dict(suite)
    if updated_suite_for_summary_refresh:
        try:
            write_sonic_suite_summary_report(updated_suite_for_summary_refresh)
        except Exception as e:
            _append_notify_log("suite_result_summary_refresh_error", {
                "suite_key": suite_key,
                "job_id": job.get("job_id", ""),
            }, error=str(e))
    if sonic_suite_waits_for_completion_event(job):
        if not already_notified_final:
            schedule_sonic_suite_summary(suite_key, delay=sonic_suite_running_check_delay_seconds())
        _append_notify_log("sonic_case_result_recorded_waiting_suite_complete", {
            "suite_key": suite_key,
            "job_id": job.get("job_id", ""),
            "case_name": job.get("target_task_name") or job.get("current_task_name") or job.get("file", ""),
            "already_notified_final": already_notified_final,
            "summary_scheduled": not already_notified_final,
        })
    else:
        schedule_sonic_suite_summary(suite_key)
    return suite_key


# ---------------------------------------------------------------------------
# 套件定时器调度
# ---------------------------------------------------------------------------

def schedule_sonic_suite_summary(suite_key: str, delay: Optional[int] = None) -> None:
    quiet = max(1, int(delay)) if delay is not None else sonic_suite_quiet_seconds()
    with cfg.SONIC_SUITE_LOCK:
        old = cfg.SONIC_SUITE_TIMERS.get(suite_key)
        if old:
            try:
                old.cancel()
            except Exception:
                pass
        timer = threading.Timer(quiet, send_sonic_suite_summary_if_quiet, args=(suite_key,))
        timer.daemon = True
        cfg.SONIC_SUITE_TIMERS[suite_key] = timer
        timer.start()


def restore_pending_sonic_suite_summary_timers() -> None:
    state = load_sonic_suite_results()
    pending_keys = []
    suppressed_keys = []
    state_changed = False
    now_ts = int(time.time())
    for suite_key, suite in (state.get("suites") or {}).items():
        if suite.get("send_in_progress"):
            suite["send_in_progress"] = False
            suite["send_started_ts"] = 0
            state_changed = True
        if sonic_suite_is_legacy_mixed_completion(suite_key, suite) and not suite.get("sent_at"):
            suite["notification_suppressed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            suite["notification_suppressed_reason"] = "历史套件结果与 Sonic resultId 混绑，已停止发送；等待按 resultId 生成的最终汇总"
            state_changed = True
            suppressed_keys.append(suite_key)
            continue
        if suite.get("sent_at") and not suite.get("send_error"):
            continue
        if not suite.get("results") and not suite.get("completion_received"):
            continue
        if sonic_suite_waits_for_completion_event(suite) and not suite.get("completion_received"):
            last_ts = _safe_int(suite.get("last_update_ts") or suite.get("created_ts"), 0)
            if last_ts and now_ts - last_ts > max(sonic_suite_max_wait_seconds() * 2, 3600):
                continue
        pending_keys.append(suite_key)
    if state_changed:
        save_sonic_suite_results(state)
    for suite_key in pending_keys:
        schedule_sonic_suite_summary(suite_key, delay=5)
    if pending_keys:
        _append_notify_log("suite_summary_timers_restored", {
            "count": len(pending_keys),
            "suite_keys": pending_keys[:20],
        })
    if suppressed_keys:
        _append_notify_log("suite_summary_legacy_mixed_completion_suppressed", {
            "count": len(suppressed_keys),
            "suite_keys": suppressed_keys[:20],
        })


# ---------------------------------------------------------------------------
# 套件摘要发送
# ---------------------------------------------------------------------------

def _attach_sonic_suite_definition_from_api(suite_key: str, suite: dict) -> dict:
    try:
        detail = lookup_sonic_suite_definition_for_suite(suite) or {}
    except Exception as e:
        detail = {"error": str(e)}
    if detail.get("error"):
        suite["sonic_suite_definition_error"] = detail.get("error", "")
        _append_notify_log("sonic_suite_definition_lookup_missed", {"suite_key": suite_key, **detail})
        return suite
    suite["sonic_suite_definition"] = detail
    expected = _safe_int(detail.get("expected_total_count") or detail.get("case_count"), 0)
    if expected:
        suite["expected_total_count"] = max(_safe_int(suite.get("expected_total_count"), 0), expected)
    if detail.get("suite_id") and not suite.get("sonic_suite_id"):
        suite["sonic_suite_id"] = str(detail.get("suite_id"))
    if detail.get("suite_name") and not suite.get("sonic_suite_name"):
        suite["sonic_suite_name"] = detail.get("suite_name")
    suite["sonic_suite_definition_error"] = ""
    _append_notify_log("sonic_suite_definition_attached", {"suite_key": suite_key, **detail})
    return suite


def _attach_sonic_result_meta_from_api(suite_key: str, suite: dict) -> dict:
    try:
        detail = lookup_sonic_result_meta_for_suite(suite) or {}
    except Exception as e:
        detail = {"error": str(e)}
    if detail.get("error"):
        suite["sonic_result_meta_error"] = detail.get("error", "")
        _append_notify_log("sonic_result_meta_lookup_missed", {"suite_key": suite_key, **detail})
        return suite
    suite["sonic_result_meta"] = detail
    expected = _safe_int(detail.get("expected_total_count") or detail.get("send_msg_count"), 0)
    if expected:
        suite["expected_total_count"] = max(_safe_int(suite.get("expected_total_count"), 0), expected)
    if detail.get("suite_id") and not suite.get("sonic_suite_id"):
        suite["sonic_suite_id"] = str(detail.get("suite_id"))
    if detail.get("finished") and detail.get("sonic_report_url") and not suite.get("sonic_report_url"):
        suite["sonic_report_url"] = detail.get("sonic_report_url")
    suite["sonic_result_meta_error"] = ""
    _append_notify_log("sonic_result_meta_attached", {"suite_key": suite_key, **detail})
    return suite


def _attach_sonic_report_from_api(suite_key: str, suite: dict) -> dict:
    fixed_report_url = ensure_sonic_suite_report_url(suite)
    if fixed_report_url:
        _append_notify_log("sonic_report_fixed_url_attached", {
            "suite_key": suite_key,
            "sonic_report_url": fixed_report_url,
        })
        return suite
    if suite.get("sonic_report_url"):
        return suite
    attempts = sonic_report_lookup_retries()
    interval = sonic_report_lookup_interval()
    last_detail = {}
    for attempt in range(1, attempts + 1):
        try:
            report_url, detail = lookup_sonic_report_for_suite(suite)
            detail = detail or {}
            detail["attempt"] = attempt
            detail["max_attempt"] = attempts
            last_detail = detail
            if report_url:
                suite["sonic_report_url"] = report_url
                suite["sonic_report_lookup"] = detail
                _append_notify_log("sonic_report_lookup_attached", {"suite_key": suite_key, **detail})
                return suite
            _append_notify_log("sonic_report_lookup_pending", {"suite_key": suite_key, **detail})
        except Exception as e:
            last_detail = {"attempt": attempt, "max_attempt": attempts, "error": str(e)}
            _append_notify_log("sonic_report_lookup_error", {"suite_key": suite_key, "attempt": attempt, "max_attempt": attempts}, error=str(e))
            if "401" in str(e) or "403" in str(e) or "Unauthorized" in str(e):
                break
        if attempt < attempts:
            time.sleep(interval)
    suite["sonic_report_lookup"] = last_detail
    if last_detail.get("error"):
        suite["sonic_report_lookup_error"] = last_detail.get("error", "")
    _append_notify_log("sonic_report_lookup_missed", {"suite_key": suite_key, **last_detail})
    return suite


def send_sonic_suite_summary_if_quiet(suite_key: str) -> None:
    """定时器回调：当套件结果安静期过后发送飞书汇总卡片。"""
    quiet = sonic_suite_quiet_seconds()
    with cfg.SONIC_SUITE_LOCK:
        state = load_sonic_suite_results()
        suite = (state.get("suites") or {}).get(suite_key)
        if not suite:
            cfg.SONIC_SUITE_TIMERS.pop(suite_key, None)
            return
        now_ts = int(time.time())
        if suite.get("superseded_by"):
            cfg.SONIC_SUITE_TIMERS.pop(suite_key, None)
            _append_notify_log("suite_summary_superseded", {
                "suite_key": suite_key,
                "superseded_by": suite.get("superseded_by"),
            })
            return
        if sonic_suite_is_legacy_mixed_completion(suite_key, suite) and not suite.get("sent_at"):
            suite["notification_suppressed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            suite["notification_suppressed_reason"] = "历史套件结果与 Sonic resultId 混绑，已停止发送；等待按 resultId 生成的最终汇总"
            state.setdefault("suites", {})[suite_key] = suite
            save_sonic_suite_results(state)
            cfg.SONIC_SUITE_TIMERS.pop(suite_key, None)
            _append_notify_log("suite_summary_legacy_mixed_completion_suppressed", {
                "suite_key": suite_key,
                "count": len(suite.get("results") or []),
                "sonic_result_id": suite.get("sonic_result_id") or "",
            })
            return
        if suite.get("completion_final_sent") and suite.get("sent_at") and not suite.get("send_error"):
            cfg.SONIC_SUITE_TIMERS.pop(suite_key, None)
            _append_notify_log("suite_summary_already_sent_final_refresh_only", {
                "suite_key": suite_key,
                "count": len(suite.get("results") or []),
                "sent_count": _safe_int(suite.get("sent_count"), 0),
            })
            return
        if suite.get("sent_at") and not suite.get("send_error"):
            cfg.SONIC_SUITE_TIMERS.pop(suite_key, None)
            _append_notify_log("suite_summary_already_sent_once", {
                "suite_key": suite_key,
                "count": len(suite.get("results") or []),
                "sent_count": _safe_int(suite.get("sent_count"), 0),
                "completion_received": bool(suite.get("completion_received")),
            })
            return
        last_ts = _safe_int(suite.get("last_update_ts"), 0)
        if not suite.get("completion_received") and last_ts and now_ts - last_ts < quiet:
            delay = max(10, quiet - (now_ts - last_ts))
            timer = threading.Timer(delay, send_sonic_suite_summary_if_quiet, args=(suite_key,))
            timer.daemon = True
            cfg.SONIC_SUITE_TIMERS[suite_key] = timer
            timer.start()
            return
        # Check running jobs - delegate to caller to manage
        if suite.get("sent_at") and not suite.get("send_error") and not sonic_suite_waits_for_completion_event(suite):
            cfg.SONIC_SUITE_TIMERS.pop(suite_key, None)
            return
        send_started_ts = _safe_int(suite.get("send_started_ts"), 0)
        if suite.get("send_in_progress") and now_ts - send_started_ts < 120:
            cfg.SONIC_SUITE_TIMERS.pop(suite_key, None)
            _append_notify_log("suite_summary_send_already_in_progress", {
                "suite_key": suite_key,
                "count": len(suite.get("results") or []),
            })
            return
        app = suite.get("app") or sonic_suite_app_info(suite.get("app_package", ""), "")
        try:
            webhook = _task_app_feishu_webhook(app)
        except ValueError as e:
            webhook = ""
            suite["send_error"] = str(e)
            state["suites"][suite_key] = suite
            save_sonic_suite_results(state)
            cfg.SONIC_SUITE_TIMERS.pop(suite_key, None)
            _append_notify_log(
                "suite_summary_error",
                {"suite_key": suite_key, "app_package": suite.get("app_package", ""), "app_name": suite.get("app_name", ""), "count": len(suite.get("results") or [])},
                error=suite["send_error"],
            )
            return
        if not webhook:
            suite["send_error"] = "未配置应用飞书机器人 Webhook"
            state["suites"][suite_key] = suite
            save_sonic_suite_results(state)
            cfg.SONIC_SUITE_TIMERS.pop(suite_key, None)
            _append_notify_log(
                "suite_summary_error",
                {"suite_key": suite_key, "app_package": suite.get("app_package", ""), "app_name": suite.get("app_name", ""), "count": len(suite.get("results") or [])},
                error=suite["send_error"],
            )
            return
        suite["send_in_progress"] = True
        suite["send_started_ts"] = now_ts
        state.setdefault("suites", {})[suite_key] = suite
        save_sonic_suite_results(state)

    suite_report_url = ""
    suite_report_error = ""
    sonic_report_url = ""
    sonic_report_lookup = {}
    sonic_report_lookup_error = ""
    sonic_result_meta = {}
    sonic_result_meta_error = ""
    sonic_suite_definition = {}
    sonic_suite_definition_error = ""
    expected_total_count = 0
    try:
        suite = _attach_sonic_suite_definition_from_api(suite_key, suite)
        sonic_suite_definition = suite.get("sonic_suite_definition") or {}
        sonic_suite_definition_error = suite.get("sonic_suite_definition_error") or ""
        expected_total_count = max(expected_total_count, _safe_int(suite.get("expected_total_count"), 0))
        suite = _attach_sonic_result_meta_from_api(suite_key, suite)
        suite = mark_sonic_suite_completed_from_result_meta(suite)
        sonic_result_meta = suite.get("sonic_result_meta") or {}
        sonic_result_meta_error = suite.get("sonic_result_meta_error") or ""
        expected_total_count = max(expected_total_count, _safe_int(suite.get("expected_total_count"), 0))
        with cfg.SONIC_SUITE_LOCK:
            state = load_sonic_suite_results()
            latest = (state.get("suites") or {}).get(suite_key) or suite
            latest.update({
                "sonic_result_meta": sonic_result_meta or latest.get("sonic_result_meta", {}),
                "sonic_result_meta_error": sonic_result_meta_error,
                "expected_total_count": max(_safe_int(latest.get("expected_total_count"), 0), _safe_int(suite.get("expected_total_count"), 0)),
                "completion_received": bool(suite.get("completion_received") or latest.get("completion_received")),
                "completion_source": suite.get("completion_source") or latest.get("completion_source", ""),
                "completion_ts": suite.get("completion_ts") or latest.get("completion_ts", ""),
                "sonic_project_id": suite.get("sonic_project_id") or latest.get("sonic_project_id", ""),
                "sonic_result_id": suite.get("sonic_result_id") or latest.get("sonic_result_id", ""),
                "sonic_report_url": suite.get("sonic_report_url") or latest.get("sonic_report_url", ""),
            })
            state.setdefault("suites", {})[suite_key] = latest
            suite_key, latest = migrate_sonic_suite_to_result_key(state, suite_key, latest)
            suite = latest
            save_sonic_suite_results(state)
        if not sonic_suite_ready_for_final_summary(suite):
            now_ts = int(time.time())
            created_ts = _safe_int(suite.get("created_ts") or suite.get("last_update_ts"), 0) or now_ts
            waited_seconds = max(0, now_ts - created_ts)
            wait_limit = max(sonic_suite_max_wait_seconds() * 3, 1800)
            waiting_reason = "等待 Sonic 测试套完成事件或 /results/list finished 状态，禁止发送中间汇总"
            with cfg.SONIC_SUITE_LOCK:
                state = load_sonic_suite_results()
                latest = (state.get("suites") or {}).get(suite_key) or suite
                latest.update({
                    "send_in_progress": False,
                    "send_started_ts": 0,
                    "notification_waiting_for_completion_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "notification_waiting_for_completion_reason": waiting_reason,
                    "sonic_result_meta": sonic_result_meta or latest.get("sonic_result_meta", {}),
                    "sonic_result_meta_error": sonic_result_meta_error,
                    "expected_total_count": max(
                        _safe_int(latest.get("expected_total_count"), 0),
                        _safe_int(suite.get("expected_total_count"), 0),
                    ),
                })
                state.setdefault("suites", {})[suite_key] = latest
                save_sonic_suite_results(state)
                cfg.SONIC_SUITE_TIMERS.pop(suite_key, None)
            _append_notify_log("suite_summary_waiting_for_sonic_completion", {
                "suite_key": suite_key,
                "count": len((suite or {}).get("results") or []),
                "expected_total_count": _safe_int((suite or {}).get("expected_total_count"), 0),
                "waited_seconds": waited_seconds,
                "wait_limit": wait_limit,
                "sonic_result_meta_error": sonic_result_meta_error,
            })
            if waited_seconds <= wait_limit:
                schedule_sonic_suite_summary(suite_key, delay=sonic_suite_running_check_delay_seconds())
            else:
                with cfg.SONIC_SUITE_LOCK:
                    state = load_sonic_suite_results()
                    latest = (state.get("suites") or {}).get(suite_key) or suite
                    latest["notification_suppressed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    latest["notification_suppressed_reason"] = (
                        "等待 Sonic 完成事件超时，已停止发送用例回调阶段的中间汇总；"
                        "请检查 Sonic suite-complete 回调或 results/list 权限"
                    )
                    state.setdefault("suites", {})[suite_key] = latest
                    save_sonic_suite_results(state)
                _append_notify_log("suite_summary_waiting_for_sonic_completion_timeout", {
                    "suite_key": suite_key,
                    "count": len((suite or {}).get("results") or []),
                    "waited_seconds": waited_seconds,
                })
            return
        now_ts = int(time.time())
        if sonic_suite_can_wait_for_missing_task_callbacks(suite, now_ts):
            delay = min(sonic_suite_running_check_delay_seconds(), max(3, sonic_task_callback_grace_seconds()))
            stats = sonic_suite_display_stats(suite)
            with cfg.SONIC_SUITE_LOCK:
                state = load_sonic_suite_results()
                latest = (state.get("suites") or {}).get(suite_key) or suite
                latest.update({
                    "send_in_progress": False,
                    "send_started_ts": 0,
                    "notification_waiting_for_task_callback_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "notification_waiting_for_task_callback_reason": (
                        f"Sonic 已结束，但 Task 平台仍差 {stats.get('missing_task_callbacks') or stats.get('pending')} 条用例回调；"
                        "等待回调收齐后再发送唯一飞书汇总"
                    ),
                })
                state.setdefault("suites", {})[suite_key] = latest
                save_sonic_suite_results(state)
                cfg.SONIC_SUITE_TIMERS.pop(suite_key, None)
            _append_notify_log("suite_summary_waiting_for_task_callbacks", {
                "suite_key": suite_key,
                "missing_task_callbacks": stats.get("missing_task_callbacks") or stats.get("pending"),
                "delay": delay,
                "grace_seconds": sonic_task_callback_grace_seconds(),
            })
            schedule_sonic_suite_summary(suite_key, delay=delay)
            return
        if sonic_suite_can_wait_for_pending_midscene_reports(suite, now_ts):
            delay = sonic_midscene_report_check_delay_seconds()
            with cfg.SONIC_SUITE_LOCK:
                state = load_sonic_suite_results()
                latest = (state.get("suites") or {}).get(suite_key) or suite
                latest.update({
                    "send_in_progress": False,
                    "send_started_ts": 0,
                    "notification_waiting_for_midscene_report_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "notification_waiting_for_midscene_report_reason": "等待 Midscene 报告回传后再发送唯一套件汇总，避免重复飞书消息",
                })
                state.setdefault("suites", {})[suite_key] = latest
                save_sonic_suite_results(state)
                cfg.SONIC_SUITE_TIMERS.pop(suite_key, None)
            _append_notify_log("suite_summary_waiting_for_midscene_report_upload", {
                "suite_key": suite_key,
                "pending_reports": sonic_suite_pending_midscene_reports(suite),
                "delay": delay,
                "grace_seconds": sonic_midscene_report_grace_seconds(),
            })
            schedule_sonic_suite_summary(suite_key, delay=delay)
            return
        suite = _attach_sonic_report_from_api(suite_key, suite)
        sonic_report_url = suite.get("sonic_report_url") or ""
        sonic_report_lookup = suite.get("sonic_report_lookup") or {}
        sonic_report_lookup_error = suite.get("sonic_report_lookup_error") or ""
        sonic_result_meta = suite.get("sonic_result_meta") or sonic_result_meta
        sonic_result_meta_error = suite.get("sonic_result_meta_error") or sonic_result_meta_error
        sonic_suite_definition = suite.get("sonic_suite_definition") or sonic_suite_definition
        sonic_suite_definition_error = suite.get("sonic_suite_definition_error") or sonic_suite_definition_error
        expected_total_count = max(expected_total_count, _safe_int(suite.get("expected_total_count"), 0))
        with cfg.SONIC_SUITE_LOCK:
            state = load_sonic_suite_results()
            already_sent_key = sonic_suite_same_result_already_sent(state, suite_key, suite)
            if already_sent_key:
                latest = (state.get("suites") or {}).get(suite_key) or suite
                latest.update({
                    "send_in_progress": False,
                    "send_started_ts": 0,
                    "notification_suppressed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "notification_suppressed_reason": f"同一 Sonic resultId 已由 {already_sent_key} 发送飞书汇总，跳过重复发送",
                    "duplicate_of": already_sent_key,
                })
                state.setdefault("suites", {})[suite_key] = latest
                save_sonic_suite_results(state)
                cfg.SONIC_SUITE_TIMERS.pop(suite_key, None)
                _append_notify_log("suite_summary_duplicate_result_suppressed", {
                    "suite_key": suite_key,
                    "duplicate_of": already_sent_key,
                    "sonic_project_id": suite.get("sonic_project_id"),
                    "sonic_result_id": suite.get("sonic_result_id"),
                })
                return
        try:
            suite_report_url = write_sonic_suite_summary_report(suite)
            suite["suite_report_url"] = suite_report_url
            suite["suite_report_error"] = ""
        except Exception as e:
            suite_report_error = str(e)
            suite["suite_report_error"] = suite_report_error
        card = build_sonic_suite_summary_card(suite)
        resp = _post_feishu_card(webhook, card)
        send_error = ""
    except Exception as e:
        resp = {}
        send_error = str(e)
    with cfg.SONIC_SUITE_LOCK:
        state = load_sonic_suite_results()
        suite = (state.get("suites") or {}).get(suite_key) or suite
        if not send_error:
            suite["sent_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            suite["sent_count"] = len(suite.get("results") or [])
        suite["send_error"] = send_error
        suite["send_in_progress"] = False
        suite["send_started_ts"] = 0
        if suite_report_url:
            suite["suite_report_url"] = suite_report_url
        if suite_report_error:
            suite["suite_report_error"] = suite_report_error
        if sonic_report_url:
            suite["sonic_report_url"] = sonic_report_url
        if sonic_report_lookup:
            suite["sonic_report_lookup"] = sonic_report_lookup
        if sonic_report_lookup_error:
            suite["sonic_report_lookup_error"] = sonic_report_lookup_error
        if sonic_result_meta:
            suite["sonic_result_meta"] = sonic_result_meta
        if sonic_result_meta_error:
            suite["sonic_result_meta_error"] = sonic_result_meta_error
        if sonic_suite_definition:
            suite["sonic_suite_definition"] = sonic_suite_definition
        if sonic_suite_definition_error:
            suite["sonic_suite_definition_error"] = sonic_suite_definition_error
        if expected_total_count:
            suite["expected_total_count"] = max(_safe_int(suite.get("expected_total_count"), 0), expected_total_count)
        if (
            not send_error
            and sonic_suite_ready_for_final_summary(suite)
            and sonic_suite_waits_for_completion_event(suite)
        ):
            suite["completion_final_sent"] = True
        suite["feishu"] = resp
        state.setdefault("suites", {})[suite_key] = suite
        save_sonic_suite_results(state)
        cfg.SONIC_SUITE_TIMERS.pop(suite_key, None)
    _append_notify_log(
        "suite_summary_sent" if not send_error else "suite_summary_error",
        {"suite_key": suite_key, "count": len(suite.get("results") or [])},
        result=resp,
        error=send_error,
    )


# ---------------------------------------------------------------------------
# Sonic 执行
# ---------------------------------------------------------------------------

def sonic_force_run_suite(suite_id: Any) -> dict:
    """触发 Sonic 测试套强制执行，返回 resultId。"""
    if not suite_id:
        return {"ok": False, "error": "suiteId 为空"}
    try:
        resp = sonic_request("GET", "/testSuites/runSuite", params={"id": suite_id}, timeout=30)
        data = _sonic_response_data(resp)
        if data and isinstance(data, dict):
            result_id = data.get("id") or data.get("resultId") or data.get("result_id")
            return {"ok": True, "resultId": result_id, "data": data}
        if isinstance(data, (int, str)) and data:
            return {"ok": True, "resultId": data}
        return {"ok": True, "resultId": None, "raw": resp}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}


def _sonic_suite_detail(suite_id: Any) -> dict:
    suite_id = _safe_int(suite_id, 0)
    if not suite_id:
        return {}
    detail = _sonic_response_data(sonic_request("GET", "/testSuites", params={"id": suite_id}, timeout=15)) or {}
    if isinstance(detail, list):
        detail = detail[0] if detail else {}
    return detail if isinstance(detail, dict) else {}


def _sonic_find_suite_by_name(project_id: int, name: str) -> dict:
    if not project_id or not name:
        return {}
    payload = _sonic_response_data(sonic_request("GET", "/testSuites/listAll", params={"projectId": project_id}, timeout=15)) or []
    if isinstance(payload, dict):
        payload = _extract_page_items(payload)
    if not isinstance(payload, list):
        return {}
    matches = [item for item in payload if isinstance(item, dict) and item.get("name") == name]
    if not matches:
        return {}
    matches.sort(key=lambda item: _safe_int(item.get("id"), 0), reverse=True)
    return matches[0]


def sonic_run_single_case(data: dict) -> dict:
    """已下线：单条/多条调试统一走本地 Runner。

    Sonic 官方执行模型以测试套为单位。平台曾尝试通过临时测试套承载
    “单条执行”，但这会在 Sonic 平台制造额外套件和结果，容易干扰真实
    基线回归。因此当前策略是：

    - 单条/多条/新脚本调试：POST /api/run-request，由 Windows/Mac Runner 执行。
    - Sonic：只用于已入库/基线 YAML 的正式测试套回归、桥接脚本同步和汇总通知。
    """
    return {
        "ok": False,
        "deprecated": True,
        "mode": "RUNNER_JOB_ONLY",
        "error": "Sonic 单条临时测试套执行已下线；单条/多条调试请走本地 Windows/Mac Runner。",
        "runnerEndpoint": "/api/run-request",
        "suggestion": "在页面点击 Runner 单条调试；Sonic 只保留正式测试套回归，避免临时套污染 Sonic 数据。"
    }


# ---------------------------------------------------------------------------
# 回调与测试套完成事件（对外接口）
# ---------------------------------------------------------------------------

def sonic_handle_callback(path: str, payload: Any, headers: Any) -> Dict[str, Any]:
    """统一入口：根据 ``path`` 分发到 case 回调或测试套完成事件处理。"""
    if path in cfg.SONIC_SUITE_COMPLETION_PATHS:
        return sonic_handle_suite_completion(payload)
    # 默认行为：仅记录日志
    _append_notify_log("sonic_case_callback_received", payload)
    return {"ok": True, "message": "已记录 Sonic 用例回调"}


def sonic_handle_suite_completion(payload: Any) -> Dict[str, Any]:
    """处理 Sonic 测试套完成事件。"""
    parsed = payload
    if not isinstance(payload, dict):
        try:
            if isinstance(payload, (bytes, bytearray)):
                payload = payload.decode("utf-8", errors="replace")
            parsed = json.loads(payload) if isinstance(payload, str) else {}
        except Exception:
            parsed = {}

    if not isinstance(parsed, dict):
        parsed = {}

    if not parsed.get("suite_name") and not parsed.get("result_id") and not parsed.get("total"):
        return {"ok": False, "error": "未识别到 Sonic 测试套结束信息"}

    try:
        outcome = register_sonic_suite_completion(parsed) or {}
    except Exception as exc:
        _append_notify_log("sonic_suite_completion_failed", parsed, error=str(exc))
        return {"ok": False, "error": str(exc)}
    outcome = _strip_secrets(outcome)
    return {
        "ok": True,
        "suite_key": outcome.get("suite_key"),
        "duplicate": outcome.get("duplicate", False),
        "status": parsed.get("status"),
        "total": parsed.get("total"),
        "message": "已接收 Sonic 测试套结束事件",
    }


# ---------------------------------------------------------------------------
# 用例发布（对外接口）
# ---------------------------------------------------------------------------

def sonic_sync_case_to_configured_suite(app: dict, project_id: int, saved_case: dict) -> dict:
    """Add a published managed case to the app's explicitly bound Sonic suite."""
    suite_id = sonic_suite_id_for_app(app)
    if not suite_id:
        return {
            "state": "not_configured",
            "label": "应用未绑定 Sonic 测试套，已仅同步用例",
            "suite_id": 0,
            "suite_name": sonic_suite_name_for_app(app),
            "case_count": 0,
        }
    detail = _sonic_response_data(sonic_request("GET", "/testSuites", params={"id": suite_id}, timeout=15)) or {}
    if isinstance(detail, list):
        detail = detail[0] if detail else {}
    if not isinstance(detail, dict) or not detail:
        raise RuntimeError(f"Sonic 测试套不存在：{suite_id}")
    suite_project_id = _safe_int(detail.get("projectId"), 0)
    if suite_project_id and suite_project_id != _safe_int(project_id, 0):
        raise RuntimeError(f"Sonic 测试套 {suite_id} 不属于当前应用项目，请重新绑定")
    cases = [item for item in (detail.get("testCases") or []) if isinstance(item, dict)]
    sonic_case_id = _safe_int((saved_case or {}).get("id"), 0)
    already_linked = any(_safe_int(item.get("id"), 0) == sonic_case_id for item in cases)
    if not already_linked:
        updated = dict(detail)
        updated["testCases"] = cases + [saved_case]
        updated["devices"] = detail.get("devices") or []
        _sonic_response_data(sonic_request("PUT", "/testSuites", body=updated, timeout=20))
        cases = updated["testCases"]
    return {
        "state": "linked" if not already_linked else "already_linked",
        "label": "已加入 Sonic 测试套" if not already_linked else "已在 Sonic 测试套内",
        "suite_id": suite_id,
        "suite_name": detail.get("name") or sonic_suite_name_for_app(app),
        "case_count": len(cases),
    }


def sonic_upsert_case(case_info: dict, force: bool = False) -> dict:
    """创建或更新 Sonic 用例，含桥接步骤同步与测试套绑定。"""
    app = _task_app_map_by_package().get(case_info.get("app_package") or "") or {}
    project_id = sonic_find_project_id(app)
    if not project_id:
        raise ValueError(f"应用「{app.get('name') or case_info.get('app_package') or '未绑定'}」未绑定 Sonic 项目 ID/名称")
    platform = 1 if case_info.get("platform", "android") == "android" else 2
    module_id = sonic_ensure_module(project_id, case_info.get("module") or "默认模块")
    case_name = case_info.get("task_name") or re.sub(r"\.(yaml|yml)$", "", case_info.get("file", ""), flags=re.I)
    existing = sonic_find_case(project_id, platform, case_name, case_info.get("case_id"))
    existing_id = _safe_int(existing.get("id"), 0) if existing else 0
    legacy_midscene = bool(existing_id and sonic_case_has_midscene_step(existing_id))
    if existing and not sonic_managed_case(existing, case_info.get("case_id")) and not legacy_midscene and not force:
        raise RuntimeError(f"Sonic 已存在同名非 Midscene 托管用例「{case_name}」，为避免覆盖请先改名或勾选 force")
    body = {
        "id": existing_id if existing else None,
        "name": case_name,
        "platform": platform,
        "projectId": project_id,
        "moduleId": module_id,
        "version": case_info.get("version") or "Midscene",
        "des": sonic_case_marker(case_info.get("case_id"), case_info.get("module"), case_info.get("file"), case_info.get("task_name")),
    }
    sonic_request("PUT", "/testCases", body=body)
    saved = sonic_find_case(project_id, platform, case_name, case_info.get("case_id"))
    if not saved:
        raise RuntimeError("Sonic 用例保存后未能查回，请检查 Sonic 接口返回")
    sonic_case_id = _safe_int(saved.get("id"), 0)
    step_payload = sonic_upsert_bridge_step(project_id, platform, sonic_case_id, case_info.get("case_id"))
    suite_sync = sonic_sync_case_to_configured_suite(app, project_id, saved)
    warning = ""
    try:
        steps = sonic_list_steps(sonic_case_id)
        expected_version = sonic_bridge_version()
        bridge_content = "\n".join(str((step or {}).get("content") or "") for step in steps if sonic_bridge_step(step))
        bridge_ok = (
            "/api/sonic/bridge-groovy" in bridge_content
            and 'setRequestProperty("x-token"' in bridge_content
            and (expected_version == "unknown" or f"bridgeVersion: {expected_version}" in bridge_content)
        )
        if not bridge_ok:
            warning = "Sonic 桥接脚本可能是旧版，请执行刷新桥接脚本。"
    except Exception as exc:
        warning = f"Sonic 桥接脚本校验失败：{str(exc)[:120]}"
    return {
        "project_id": project_id,
        "project_name": sonic_project_name_for_app(app),
        "module_id": module_id,
        "sonic_case_id": sonic_case_id,
        "sonic_case_name": case_name,
        "sonic_suite_id": app.get("sonic_suite_id") or app.get("sonicSuiteId") or "",
        "sonic_suite_name": app.get("sonic_suite_name") or app.get("sonicSuiteName") or "",
        "app_package": app.get("package") or case_info.get("app_package") or "",
        "app_name": app.get("name") or case_info.get("app_name") or "",
        "suite_sync": suite_sync,
        "step_sort": step_payload.get("sort"),
        "removed_step_ids": step_payload.get("removed_step_ids", []),
        "cleaned_duplicate_steps": step_payload.get("cleaned_duplicate_steps", 0),
        "verified_state": step_payload.get("verified_state", ""),
        "verified_step_count": step_payload.get("verified_step_count", 0),
        "legacy_midscene_migrated": legacy_midscene,
        "warning": warning,
    }


def sonic_project_apps(app_package_filter: str = "") -> list:
    apps = []
    for app in sonic_notify_known_apps():
        if app_package_filter and app.get("package") != app_package_filter:
            continue
        project_id = sonic_find_project_id(app)
        row = dict(app)
        row["sonic_project_id_resolved"] = project_id
        apps.append(row)
    return apps


def sonic_live_case_status(case_info: dict) -> dict:
    """查询用例在 Sonic 中的实时同步状态。"""
    app = _task_app_map_by_package().get(case_info.get("app_package") or "") or {}
    project_id = sonic_find_project_id(app)
    row = {
        **case_info,
        "sonic_project_id": project_id,
        "sonic_project_name": sonic_project_name_for_app(app),
        "sonic_found": False,
        "sonic_case_id": 0,
        "sonic_case_name": "",
        "step_state": "project_missing" if not project_id else "missing",
        "step_label": "应用未绑定 Sonic 项目" if not project_id else "未同步",
        "sync": load_sonic_sync_state().get("cases", {}).get(case_info.get("case_id"), {}),
    }
    if not project_id:
        return row
    case_name = case_info.get("task_name") or ""
    existing = sonic_find_case(project_id, 1 if case_info.get("platform") == "android" else 2, case_name, case_info.get("case_id"))
    if not existing:
        row["step_state"] = "not_published"
        row["step_label"] = "Sonic 未找到用例"
        return row
    steps = sonic_list_steps(_safe_int(existing.get("id"), 0))
    state = sonic_step_state(steps, case_info.get("case_id"))
    row.update({
        "sonic_found": True,
        "sonic_case_id": _safe_int(existing.get("id"), 0),
        "sonic_case_name": existing.get("name") or "",
        "step_state": state["state"],
        "step_label": state["label"],
        "step_id": state["step_id"],
        "step_sort": state["sort"],
        "step_count": state.get("step_count", 0),
        "bridge_count": state.get("bridge_count", 0),
        "legacy_count": state.get("legacy_count", 0),
    })
    return row


def sonic_scan_midscene_cases(app_package: str = "", module: str = "", file: str = "", include_current: bool = False) -> list:
    """扫描 Sonic 中需要迁移的 Midscene 脚本用例。"""
    app_filter = app_package or ""
    if module and not app_filter:
        app_filter = _app_package_for_module(module)
    _cases, by_id, by_app_name = sonic_case_indexes(module, file)
    rows = []
    for app in sonic_project_apps(app_filter):
        app_package_row = app.get("package") or ""
        project_id = app.get("sonic_project_id_resolved") or 0
        if not project_id:
            rows.append({
                "app_package": app_package_row,
                "app_name": app.get("name") or app_package_row,
                "project_id": 0,
                "project_name": sonic_project_name_for_app(app),
                "status": "project_missing",
                "reason": "应用未绑定 Sonic 项目",
                "matched_case": None,
            })
            continue
        for sonic_case in sonic_list_cases(project_id, platform=1, name=""):
            sonic_case_id = _safe_int(sonic_case.get("id"), 0)
            steps = sonic_list_steps(sonic_case_id)
            state = sonic_step_state(steps)
            if state["state"] == "missing":
                continue
            if state["state"] == "bridge" and not include_current:
                continue
            matched, match_type = _sonic_match_task_case(sonic_case, app_package_row, by_id, by_app_name)
            action = "skip"
            reason = "新桥接脚本，无需迁移" if state["state"] == "bridge" else "旧模板脚本"
            if state["state"] in ("legacy", "mixed"):
                if matched:
                    action = "migrate"
                    reason = f"可按 {match_type} 匹配到 Task 用例并清理旧步骤" if state["state"] == "mixed" else f"可按 {match_type} 匹配到 Task 用例"
                elif match_type == "ambiguous":
                    action = "manual"
                    reason = "同名 Task 用例不唯一，需要人工确认"
                else:
                    action = "manual"
                    reason = "未匹配到 Task 平台 YAML 用例；请先在用例资产中同步/新建 YAML，或重命名后重新扫描"
            rows.append({
                "app_package": app_package_row,
                "app_name": app.get("name") or app_package_row,
                "project_id": project_id,
                "project_name": sonic_project_name_for_app(app),
                "sonic_case_id": sonic_case_id,
                "sonic_case_name": sonic_case.get("name") or "",
                "step_id": state.get("step_id", 0),
                "step_sort": state.get("sort", 0),
                "step_count": state.get("step_count", 0),
                "bridge_count": state.get("bridge_count", 0),
                "legacy_count": state.get("legacy_count", 0),
                "step_state": state["state"],
                "step_label": state["label"],
                "action": action,
                "reason": reason,
                "match_type": match_type,
                "matched_case": matched,
            })
    return rows


def _sonic_name_aliases(value: str) -> List[str]:
    """Return tolerant name aliases for matching Sonic cases back to Task YAML tasks."""
    text = str(value or "").strip()
    if not text:
        return []
    without_ext = re.sub(r"\.(ya?ml)$", "", text, flags=re.I).strip()
    candidates = [text, without_ext]
    for part in re.split(r"[/\\|｜>＞:：_-]+", without_ext):
        part = part.strip()
        if part:
            candidates.append(part)
    normalized = []
    for item in candidates:
        key = re.sub(r"\.(ya?ml)$", "", str(item or ""), flags=re.I)
        key = re.sub(r"[\s`'\"“”‘’（）()\[\]【】{}]+", "", key)
        key = re.sub(r"[/\\|｜>＞:：_-]+", "", key)
        key = key.strip().lower()
        if key and key not in normalized:
            normalized.append(key)
    return normalized


def _append_case_index(index: dict, key: Tuple[str, str], case: dict) -> None:
    if not key[1]:
        return
    rows = index.setdefault(key, [])
    case_id = case.get("case_id") or ""
    if case_id and any(item.get("case_id") == case_id for item in rows):
        return
    rows.append(case)


def _sonic_match_task_case(sonic_case: dict, app_package: str, by_id: dict, by_app_name: dict) -> Tuple[Optional[dict], str]:
    marker = sonic_case_marker_info(sonic_case)
    marker_case_id = marker.get("case_id") or marker.get("caseId")
    if marker_case_id and marker_case_id in by_id:
        return by_id[marker_case_id], "case_id"
    name = sonic_case.get("name") or ""
    candidates = []
    for key in [name] + _sonic_name_aliases(name):
        for item in by_app_name.get((app_package or "", key), []):
            if item not in candidates:
                candidates.append(item)
    if len(candidates) == 1:
        match_type = "name" if candidates and candidates[0].get("task_name") == name else "name_rule"
        return candidates[0], match_type
    if len(candidates) > 1:
        return None, "ambiguous"
    return None, "none"


def sonic_match_task_case(sonic_case: dict, app_package: str, by_id: dict, by_app_name: dict) -> Tuple[Optional[dict], str]:
    """公共别名 → :func:`_sonic_match_task_case`。"""
    return _sonic_match_task_case(sonic_case, app_package, by_id, by_app_name)


def sonic_case_indexes(module_filter: str = "", file_filter: str = "") -> Tuple[list, dict, dict]:
    """构建用例索引：返回 (cases, by_id, by_app_name)。"""
    cases = _list_task_case_assets(module_filter, file_filter)
    by_id = {}
    by_app_name = {}
    for case in cases:
        if case.get("error"):
            continue
        case_id = case.get("case_id")
        if case_id:
            by_id[case_id] = case
        app_package = case.get("app_package") or ""
        task_name = case.get("task_name") or ""
        file_stem = re.sub(r"\.(ya?ml)$", "", case.get("file") or "", flags=re.I)
        module_name = case.get("module") or ""
        names = [
            task_name,
            file_stem,
            f"{module_name}-{task_name}" if module_name and task_name else "",
            f"{file_stem}-{task_name}" if file_stem and task_name and file_stem != task_name else "",
        ]
        for name in names:
            if not name:
                continue
            _append_case_index(by_app_name, (app_package, name), case)
            for alias in _sonic_name_aliases(name):
                _append_case_index(by_app_name, (app_package, alias), case)
    return cases, by_id, by_app_name


def sonic_migrate_midscene_cases(data: dict) -> dict:
    """迁移 Sonic 中的旧 Midscene 脚本用例。"""
    app_package = data.get("app_package") or data.get("appPackage") or ""
    module = data.get("module", "")
    file = _clean_filename(data.get("file", "")) if data.get("file") else ""
    dry_run = _safe_bool(data.get("dryRun", data.get("dry_run")))
    rows = sonic_scan_midscene_cases(app_package=app_package, module=module, file=file, include_current=False)
    results = []
    with cfg.SONIC_LOCK:
        sync = load_sonic_sync_state()
        sync_cases = sync.setdefault("cases", {})
        for row in rows:
            matched = row.get("matched_case") or {}
            if row.get("action") != "migrate" or not matched:
                results.append({**row, "migrated": False})
                continue
            record = {
                **matched,
                "sync_requested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "dry_run": dry_run,
                "legacy_sonic_case_id": row.get("sonic_case_id"),
                "legacy_step_id": row.get("step_id"),
            }
            if dry_run:
                record.update({
                    "status": "ready",
                    "message": "dry-run：将收敛为唯一桥接步骤并清理旧/重复 Midscene 步骤",
                    "bridge_step_preview": sonic_bridge_step_script(matched.get("case_id", ""))[:1200],
                })
                results.append({**row, "migrated": False, "publish": record})
            else:
                try:
                    publish_result = sonic_upsert_case(matched, force=False)
                    record.update({
                        "status": "published",
                        "published_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        **publish_result,
                    })
                    sync_cases[matched.get("case_id", "")] = record
                    results.append({**row, "migrated": True, "publish": record})
                except Exception as e:
                    results.append({**row, "migrated": False, "publish": {"error": str(e)}})
        if not dry_run:
            save_sonic_sync_state(sync)
    return {
        "ok": True,
        "dryRun": dry_run,
        "total": len(rows),
        "migratable": len([row for row in rows if row.get("action") == "migrate"]),
        "migrated": len([row for row in results if row.get("migrated")]),
        "manual": len([row for row in rows if row.get("action") == "manual"]),
        "results": results,
    }


def sonic_refresh_bridge_scripts(data: dict) -> dict:
    """批量刷新 Sonic 中已托管用例的桥接脚本。

    这个动作只更新 Sonic 里保存的 Groovy 引导脚本，让它携带当前服务端
    ``MIDSCENE_RUNNER_TOKEN`` 并拉取最新版桥接逻辑；不会修改 Task YAML，
    也不会改动 Sonic 用例名称、模块或测试套绑定。
    """
    app_package = data.get("app_package") or data.get("appPackage") or ""
    module = data.get("module", "")
    file = _clean_filename(data.get("file", "")) if data.get("file") else ""
    dry_run = _safe_bool(data.get("dryRun", data.get("dry_run")))
    include_current = True
    rows = sonic_scan_midscene_cases(
        app_package=app_package,
        module=module,
        file=file,
        include_current=include_current,
    )
    results: List[Dict[str, Any]] = []
    refreshed = 0
    failed = 0
    skipped = 0
    with cfg.SONIC_LOCK:
        sync = load_sonic_sync_state()
        sync_cases = sync.setdefault("cases", {})
        for row in rows:
            matched = row.get("matched_case") or {}
            case_id = matched.get("case_id") or row.get("case_id") or ""
            sonic_case_id = _safe_int(row.get("sonic_case_id"), 0)
            project_id = _safe_int(row.get("project_id"), 0)
            platform = 1 if (matched.get("platform") or "android") == "android" else 2
            result = {
                **row,
                "case_id": case_id,
                "module": matched.get("module") or row.get("module") or "",
                "file": matched.get("file") or row.get("file") or "",
                "task_name": matched.get("task_name") or row.get("task_name") or row.get("sonic_case_name") or "",
                "dry_run": dry_run,
                "refreshed": False,
            }
            if not matched:
                skipped += 1
                result.update({
                    "status": "skipped",
                    "reason": row.get("reason") or "未匹配到 Task 用例，不能安全刷新桥接脚本",
                })
                results.append(result)
                continue
            if not project_id or not sonic_case_id or not case_id:
                failed += 1
                result.update({
                    "status": "failed",
                    "error": "缺少 project_id / sonic_case_id / case_id，无法刷新桥接脚本",
                })
                results.append(result)
                continue
            if dry_run:
                result.update({
                    "status": "ready",
                    "reason": "dry-run：将刷新 Sonic 中保存的桥接脚本和 runner token",
                    "bridge_step_preview": sonic_bridge_step_script(case_id)[:1200],
                })
                results.append(result)
                continue
            try:
                step_payload = sonic_upsert_bridge_step(project_id, platform, sonic_case_id, case_id)
                refreshed += 1
                record = {
                    **matched,
                    "status": "bridge_refreshed",
                    "refreshed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "project_id": project_id,
                    "project_name": row.get("project_name") or row.get("sonic_project_name") or "",
                    "sonic_case_id": sonic_case_id,
                    "sonic_case_name": row.get("sonic_case_name") or matched.get("task_name") or "",
                    "step_sort": step_payload.get("sort"),
                    "removed_step_ids": step_payload.get("removed_step_ids", []),
                    "cleaned_duplicate_steps": step_payload.get("cleaned_duplicate_steps", 0),
                    "verified_state": step_payload.get("verified_state", ""),
                    "verified_step_count": step_payload.get("verified_step_count", 0),
                }
                sync_cases[case_id] = {**(sync_cases.get(case_id) or {}), **record}
                result.update({"status": "bridge_refreshed", "refreshed": True, **record})
            except Exception as e:
                failed += 1
                result.update({"status": "failed", "error": str(e)})
            results.append(result)
        if not dry_run:
            save_sonic_sync_state(sync)
    return {
        "ok": failed == 0,
        "dryRun": dry_run,
        "total": len(rows),
        "matched": len([row for row in rows if row.get("matched_case")]),
        "refreshed": refreshed,
        "failed": failed,
        "skipped": skipped,
        "results": results,
        "message": "刷新桥接脚本只更新 Sonic Groovy 引导，不修改 YAML 或基线内容",
    }


def sonic_publish_precheck(data: dict) -> dict:
    """同步前预检。"""
    mod = data.get("module", "")
    raw_file = data.get("file", "")
    file = _clean_filename(raw_file) if raw_file else ""
    task_name = data.get("taskName") or data.get("task_name") or ""
    blockers = []
    warnings = []
    fixes = []
    cases = []
    if not mod or not file:
        blockers.append("module 和 file 不能为空")
        return {"ok": False, "canPublish": False, "blockers": blockers, "warnings": warnings, "fixes": fixes, "cases": cases}
    try:
        fpath = safe_join(cfg.TASK_DIR, mod, file)
    except ValueError:
        blockers.append("非法路径")
        return {"ok": False, "canPublish": False, "blockers": blockers, "warnings": warnings, "fixes": fixes, "cases": cases}
    if not os.path.exists(fpath):
        blockers.append("YAML 文件不存在")
        return {"ok": False, "canPublish": False, "blockers": blockers, "warnings": warnings, "fixes": fixes, "cases": cases}
    yaml_text_value = read_text_file(fpath)
    if not yaml_text_value.strip():
        blockers.append("YAML 内容为空")
    if "tasks:" not in yaml_text_value:
        blockers.append("YAML 缺少 tasks")
    # YAML 验证 - 委托给外部
    yaml_warnings = []
    try:
        from .yaml_service import validate_midscene_yaml
        result = validate_midscene_yaml(yaml_text_value)
        if isinstance(result, list):
            # validate_yaml 返回 List[str] 警告列表
            yaml_warnings = result
        elif isinstance(result, dict):
            if not result.get("ok"):
                yaml_warnings = result.get("warnings", [])
            else:
                warnings.extend((result.get("warnings") or [])[:3])
    except ImportError:
        pass
    if yaml_warnings:
        blockers.extend(yaml_warnings[:5])
    # 状态检查
    meta = {}
    try:
        meta_data = read_json_file(cfg.TASK_META_FILE, default={})
        task_k = f"{mod}::{_clean_filename(file)}"
        legacy_task_k = f"{mod}/{file}"
        meta = meta_data.get(task_k, {}) or meta_data.get(legacy_task_k, {}) or {}
    except Exception:
        pass
    status = meta.get("status") or "draft"
    if status not in ("active", "baseline"):
        blockers.append(f"当前状态是「{status}」，请先标记为已入库或基线")
    app_package = _resolve_app_package(mod, file, yaml_text_value, allow_default=False)
    if not app_package:
        blockers.append("未识别到 APP 包名，请先绑定模块应用或在 YAML 中包含 launch/force-stop 包名")
    app = _task_app_map_by_package().get(app_package or "") or {}
    project_id = sonic_find_project_id(app) if app else 0
    suite_binding = {}
    if not project_id:
        blockers.append(f"应用「{app.get('name') or app_package or mod}」未绑定 Sonic 项目")
    elif sonic_suite_id_for_app(app):
        try:
            suite_id = sonic_suite_id_for_app(app)
            suite = _sonic_response_data(sonic_request("GET", "/testSuites", params={"id": suite_id}, timeout=15)) or {}
            if isinstance(suite, list):
                suite = suite[0] if suite else {}
            if not isinstance(suite, dict) or not suite:
                blockers.append(f"绑定的 Sonic 测试套不存在：{suite_id}")
            elif _safe_int(suite.get("projectId"), 0) not in (0, project_id):
                blockers.append(f"绑定的 Sonic 测试套 {suite_id} 不属于应用项目")
            else:
                suite_binding = sonic_suite_definition_meta_from_dto(suite, "/testSuites?id")
                suite_binding["project_id"] = project_id
        except Exception as e:
            blockers.append(f"Sonic 测试套校验失败：{e}")
    else:
        warnings.append("当前应用未绑定 Sonic 测试套；同步后需要在 Sonic 中手动加入测试套，或先在配置页绑定测试套")
    # 用例列表
    try:
        all_cases = _list_task_case_assets(mod, file)
        if task_name:
            all_cases = [item for item in all_cases if item.get("task_name") == task_name]
        if not all_cases:
            blockers.append("没有解析到可同步的 tasks[].name")
        for case in all_cases:
            if not case.get("case_id"):
                warnings.append(f"用例「{case.get('task_name')}」缺少 case_id，同步时会自动固化")
                fixes.append("自动写入 baseline.case_id")
        cases = all_cases
    except Exception as e:
        blockers.append(str(e))
    sonic_rows = []
    if project_id and cases:
        try:
            sonic_rows = [sonic_live_case_status(case) for case in cases if not case.get("error")]
            legacy = [row for row in sonic_rows if row.get("step_state") == "legacy"]
            if legacy:
                warnings.append(f"Sonic 中有 {len(legacy)} 条旧模板，同步会自动替换为桥接脚本")
            mixed = [row for row in sonic_rows if row.get("step_state") == "mixed"]
            if mixed:
                warnings.append(f"Sonic 中有 {len(mixed)} 条新旧脚本并存，同步会自动保留桥接并清理重复旧步骤")
        except Exception as e:
            warnings.append(f"Sonic 状态读取失败：{e}")
    return {
        "ok": True,
        "canPublish": not blockers,
        "blockers": blockers,
        "warnings": _dedupe_keep_order(warnings),
        "fixes": _dedupe_keep_order(fixes),
        "status": status,
        "app_package": app_package,
        "app_name": app.get("name") or app_package,
        "project_id": project_id,
        "suite": suite_binding,
        "cases": cases,
        "sonic": sonic_rows,
        "yamlCheck": {"ok": len(yaml_warnings) == 0, "warnings": yaml_warnings},
    }


def _list_task_case_assets(module_filter: str = "", file_filter: str = "") -> list:
    """列出任务用例资产（从 YAML 文件中解析 tasks）。"""
    try:
        from .yaml_service import list_task_case_assets
        return list_task_case_assets(module_filter, file_filter)
    except ImportError:
        pass
    # 简化实现：从 TASK_DIR 读取 YAML 文件
    cases = []
    if not module_filter:
        return cases
    try:
        module_dir = safe_join(cfg.TASK_DIR, module_filter)
        if not os.path.isdir(module_dir):
            return cases
        import yaml
        yaml_files = [f for f in sorted(os.listdir(module_dir)) if f.endswith((".yaml", ".yml"))]
        if file_filter:
            yaml_files = [f for f in yaml_files if f == file_filter]
        for yf in yaml_files:
            fpath = safe_join(module_dir, yf)
            text = read_text_file(fpath)
            if not text or "tasks:" not in text:
                continue
            try:
                parsed = yaml.safe_load(text) or {}
            except Exception:
                continue
            try:
                from .yaml_service import extract_midscene_tasks
                _, tasks = extract_midscene_tasks(parsed)
            except Exception:
                tasks = []
            for task in (tasks or []):
                if not isinstance(task, dict):
                    continue
                task_name = task.get("name", "")
                case_id = task.get("case_id", "") or f"{module_filter}/{yf}::{task_name}"
                cases.append({
                    "module": module_filter,
                    "file": yf,
                    "task_name": task_name,
                    "case_id": case_id,
                    "app_package": _resolve_app_package(module_filter, yf, text, allow_default=False),
                })
    except Exception:
        pass
    return cases


def sonic_publish_yaml(data: dict) -> dict:
    """发布 YAML 用例到 Sonic。"""
    mod = data.get("module", "")
    raw_file = data.get("file", "")
    file = _clean_filename(raw_file) if raw_file else ""
    task_name = data.get("taskName") or data.get("task_name") or ""
    case_id = data.get("case_id") or data.get("caseId") or ""
    dry_run = _safe_bool(data.get("dryRun", data.get("dry_run")))
    force = _safe_bool(data.get("force"))
    if not mod or not file:
        return {"ok": False, "error": "module 和 file 不能为空", "results": []}

    precheck = sonic_publish_precheck({"module": mod, "file": file, "taskName": task_name})
    if not precheck.get("canPublish") and not force:
        return {
            "ok": False,
            "error": "同步前检查未通过",
            "precheck": precheck,
            "results": [],
        }
    if not precheck.get("canPublish") and force:
        hard_blockers = [
            item for item in (precheck.get("blockers") or [])
            if not str(item).startswith("当前状态是")
        ]
        if hard_blockers:
            return {
                "ok": False,
                "error": "同步前检查存在硬阻断项",
                "precheck": precheck,
                "results": [],
            }

    # 确保 YAML 中的 case_id 已初始化
    case_id_changes = []
    try:
        from .case_service import ensure_yaml_case_ids
        _, case_id_changes = ensure_yaml_case_ids(mod, file)
    except Exception:
        case_id_changes = []

    cases = _list_task_case_assets(mod, file)
    if task_name:
        cases = [item for item in cases if item.get("task_name") == task_name]
    if case_id:
        cases = [item for item in cases if item.get("case_id") == case_id]
    if not cases:
        return {"ok": False, "error": "未找到可同步的 YAML 用例", "results": []}

    results = []
    with cfg.SONIC_LOCK:
        sync = load_sonic_sync_state()
        sync_cases = sync.setdefault("cases", {})
        for case in cases:
            record = {
                **case,
                "sync_requested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "dry_run": dry_run,
            }
            if dry_run:
                record.update({
                    "status": "ready",
                    "message": "dry-run：已生成 Sonic 桥接脚本，未调用 Sonic 接口",
                    "bridge_step_preview": sonic_bridge_step_script(case.get("case_id", ""))[:1200],
                })
            else:
                try:
                    publish_result = sonic_upsert_case(case, force=force)
                    record.update({
                        "status": "published",
                        "published_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                        **publish_result,
                    })
                except Exception as e:
                    record.update({
                        "status": "failed",
                        "error": str(e),
                    })
            sync_cases[case.get("case_id", "")] = record
            results.append(record)
        save_sonic_sync_state(sync)
    return {"ok": True, "results": results, "precheck": precheck, "caseIdChanges": case_id_changes}


def sonic_publish_case(module: str, file: str, task_name: str = "") -> Dict[str, Any]:
    """发布单条用例到 Sonic。"""
    if not module or not file:
        return {"ok": False, "error": "module 和 file 不能为空", "results": []}
    return sonic_publish_yaml({"module": module, "file": file, "taskName": task_name})


def sonic_publish_batch(items: Any) -> Dict[str, Any]:
    """批量发布用例。"""
    force = False
    if isinstance(items, dict):
        module = items.get("module") or ""
        force = _safe_bool(items.get("force"))
        explicit_items = items.get("items")
        if isinstance(explicit_items, list):
            items = [
                {
                    "module": item.get("module") or module,
                    "file": item.get("file") or item.get("filename") or "",
                    "taskName": item.get("taskName") or item.get("task_name") or items.get("taskName") or items.get("task_name") or "",
                    "force": _safe_bool(item.get("force")) or force,
                }
                for item in explicit_items
                if isinstance(item, dict)
            ]
        else:
            files = items.get("files") or []
            if isinstance(files, str):
                files = [files]
            if not files and module:
                try:
                    module_dir = safe_join(cfg.TASK_DIR, module)
                    files = [
                        name for name in sorted(os.listdir(module_dir))
                        if name.endswith((".yaml", ".yml"))
                    ] if os.path.isdir(module_dir) else []
                except Exception:
                    files = []
            task_name = items.get("taskName") or items.get("task_name") or ""
            items = [
                {
                    "module": module,
                    "file": file,
                    "taskName": task_name,
                    "force": force,
                }
                for file in files
                if isinstance(file, str) and file.strip()
            ]
    if not isinstance(items, list) or not items:
        return {
            "ok": False,
            "error": "批量发布参数为空",
            "total": 0,
            "total_files": 0,
            "total_cases": 0,
            "failed": 0,
            "results": [],
        }
    results: List[Dict[str, Any]] = []
    failed = 0
    total_cases = 0
    total_synced = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        module = item.get("module") or ""
        file = item.get("file") or ""
        task_name = item.get("taskName") or item.get("task_name") or ""
        row = sonic_publish_yaml({
            "module": module,
            "file": file,
            "taskName": task_name,
            "force": _safe_bool(item.get("force")) or force,
        })
        case_rows = row.get("results") or []
        case_count = len(case_rows)
        case_failed = sum(1 for case in case_rows if case.get("status") == "failed")
        total_cases += case_count
        total_synced += max(0, case_count - case_failed)
        if (not row.get("ok")) or case_failed:
            failed += 1
        status = "success" if row.get("ok") and not case_failed else "failed"
        message = row.get("error") or (f"同步 {case_count - case_failed}/{case_count} 条用例" if case_count else "未同步任何用例")
        results.append({
            "module": module,
            "file": file,
            "status": status,
            "ok": status == "success",
            "message": message,
            "error": row.get("error", ""),
            "case_count": case_count,
            "failed_cases": case_failed,
            "synced_cases": max(0, case_count - case_failed),
            "result": row,
        })
    return {
        "ok": failed == 0,
        "total": len(results),
        "total_files": len(results),
        "total_cases": total_cases,
        "synced_cases": total_synced,
        "failed": failed,
        "results": results,
    }


# ---------------------------------------------------------------------------
# 路由层快捷调用
# ---------------------------------------------------------------------------

def publish_case(
    module: str,
    file: str,
    task_name: Optional[str] = None,
    user: Optional[str] = None,
) -> Dict[str, Any]:
    """发布单条用例到 Sonic。"""
    task_name = task_name or ""
    result = sonic_publish_case(module=module, file=file, task_name=task_name)
    if user and result.get("ok"):
        try:
            state = load_sonic_sync_state()
            case_key = f"{module}/{file}"
            cases = state.setdefault("cases", {})
            if case_key in cases:
                cases[case_key].setdefault("published_by", user)
                cases[case_key].setdefault("published_at", time.strftime("%Y-%m-%d %H:%M:%S"))
            save_sonic_sync_state(state)
        except Exception:
            pass
    _append_notify_log(
        "sonic_publish_case",
        {"module": module, "file": file, "taskName": task_name, "user": user or ""},
        result=result,
        error=result.get("error", "") if isinstance(result, dict) else "",
    )
    return result


def publish_batch(
    items: Any,
    confirm: bool = False,
    user: Optional[str] = None,
) -> Dict[str, Any]:
    """批量发布到 Sonic。"""
    if not confirm:
        preview_items = items if isinstance(items, list) else [items]
        return {
            "ok": False,
            "status": "PREVIEW",
            "message": "批量发布需确认（confirm=True），当前为预览模式",
            "total": len(preview_items),
            "items": _strip_secrets(preview_items),
        }
    result = sonic_publish_batch(items)
    _append_notify_log(
        "sonic_publish_batch",
        {"user": user or "", "confirm": confirm},
        result=result,
        error=result.get("error", "") if isinstance(result, dict) else "",
    )
    return result


def handle_suite_complete(payload: Any) -> Dict[str, Any]:
    """处理 Sonic 测试套完成回调。"""
    result = sonic_handle_suite_completion(payload)
    _append_notify_log(
        "sonic_suite_complete",
        payload if isinstance(payload, dict) else {},
        result=result,
        error=result.get("error", "") if isinstance(result, dict) else "",
    )
    return result


def handle_suite_report(payload: Any) -> Dict[str, Any]:
    """处理 Sonic 测试套报告回调。"""
    result = sonic_handle_suite_completion(payload)
    _append_notify_log(
        "sonic_suite_report",
        payload if isinstance(payload, dict) else {},
        result=result,
        error=result.get("error", "") if isinstance(result, dict) else "",
    )
    return result


# ---------------------------------------------------------------------------
# 废弃的 legacy 委托接口（保持向后兼容）
# ---------------------------------------------------------------------------

def register_legacy_handlers(**hooks: Any) -> None:
    """注入 legacy 实现。仅 ``midscene-upload.py`` 启动期调用。"""
    # 已废弃，保留空壳以兼容旧调用
    pass


def process_sonic_result_post_actions(job, stdout, stderr):
    """Persist a minimal failure review after a failed Midscene runner job.

    The legacy monolith also attempted automatic repair here. In the split
    server that flow lives behind explicit Agent/repair actions, so this
    post-action must stay lightweight and never break report upload.
    """
    if not job or job.get("status") == "success":
        return
    from .job_service import find_job, save_jobs
    job_id = job.get("job_id", "")
    reason = sonic_notify_compact(stderr or stdout or job.get("error") or "请查看执行日志", 500)
    failure_review = {
        "category": "unknown",
        "confidence": 0,
        "reason": reason,
        "evidence": [],
        "suggested_action": "人工查看日志或进入 AI 修复工作台分析",
        "can_auto_repair": False,
    }
    with JOB_LOCK:
        target, jobs = find_job(job_id)
        if target:
            target["failure_review"] = failure_review
            save_jobs(jobs)


__all__ = [
    # URL & Base
    "sonic_base_url",
    "sonic_api_prefix",
    "sonic_url",
    "sonic_result_url",
    "sonic_result_detail_url",
    # Auth & Token
    "sonic_login",
    "sonic_get_token",
    "sonic_auth_preview",
    "sonic_token_fingerprint",
    "SONIC_LOGIN_STATE",
    # HTTP
    "sonic_request",
    "sonic_safe_request_shape",
    "sonic_response_error_message",
    # Health & Probe
    "sonic_health",
    "sonic_probe_token",
    "sonic_probe_endpoint",
    # Projects / Suites / Results (cached)
    "sonic_list_projects",
    "sonic_list_suites",
    "sonic_read_result",
    # Raw API
    "sonic_list_projects_raw",
    "sonic_list_modules",
    "sonic_list_cases",
    "sonic_list_steps",
    "sonic_list_results",
    # Project / Suite lookup
    "sonic_project_id_for_app",
    "sonic_project_name_for_app",
    "sonic_find_project_id",
    "sonic_suite_id_for_app",
    "sonic_suite_name_for_app",
    "sonic_project_id_for_package",
    # Module / Case / Step
    "sonic_ensure_module",
    "sonic_case_marker",
    "sonic_managed_case",
    "sonic_case_marker_info",
    "sonic_midscene_step",
    "sonic_case_has_midscene_step",
    "sonic_bridge_step",
    "sonic_step_state",
    "sonic_find_case",
    # Bridge
    "sonic_bridge_step_script",
    "sonic_upsert_bridge_step",
    "sonic_refresh_bridge_scripts",
    # Sync state
    "load_sonic_sync_state",
    "save_sonic_sync_state",
    "load_sonic_suite_results",
    "save_sonic_suite_results",
    # Apps
    "builtin_task_apps",
    "sonic_notify_known_apps",
    # Text processing
    "sonic_notify_clean_text",
    "sonic_notify_compact",
    "sonic_notify_display_value",
    "sonic_notify_pretty_title_text",
    "sonic_text_score",
    "sonic_text_looks_mojibake",
    "sonic_recover_text_encoding",
    # Callback
    "decode_sonic_callback_body",
    "normalize_sonic_suite_status",
    "sonic_suite_status_meta",
    "parse_sonic_suite_completion_payload",
    # Suite events
    "sonic_result_suite_key",
    "sonic_suite_bound_result_id",
    "sonic_suite_is_legacy_mixed_completion",
    "sonic_suite_matches_completion",
    "sonic_suite_key_for_completion_event",
    "sonic_suite_app_info",
    "sonic_suite_app_for_completion",
    # Suite timing
    "sonic_suite_quiet_seconds",
    "sonic_suite_max_wait_seconds",
    "sonic_suite_running_check_delay_seconds",
    "sonic_suite_reopen_seconds",
    "sonic_suite_waits_for_completion_event",
    "sonic_report_lookup_retries",
    "sonic_report_lookup_interval",
    "sonic_midscene_report_grace_seconds",
    "sonic_midscene_report_check_delay_seconds",
    "sonic_suite_pending_midscene_reports",
    "sonic_suite_can_wait_for_pending_midscene_reports",
    "sonic_report_window_before_seconds",
    "sonic_report_window_after_seconds",
    # Suite stats
    "sonic_suite_summary_status",
    "sonic_suite_completion_stats",
    "sonic_suite_expected_total",
    "sonic_suite_case_units",
    "sonic_suite_result_identity",
    "sonic_suite_job_identity",
    "sonic_suite_unique_result_count",
    "sonic_suite_contains_job_identity",
    "sonic_suite_has_complete_result_cycle",
    "sonic_suite_case_stats",
    "sonic_suite_display_stats",
    "sonic_suite_effective_status",
    "sonic_suite_finished_in_sonic",
    "sonic_suite_result_line",
    # Report URL
    "sonic_suite_fixed_report_url",
    "ensure_sonic_suite_report_url",
    "sonic_suite_report_lookup_message",
    "sonic_error_is_unauthorized",
    "sonic_results_permission_error",
    # Suite definition
    "sonic_suite_config_id",
    "sonic_suite_config_name",
    "sonic_count_suite_cases",
    "sonic_suite_definition_meta_from_dto",
    "lookup_sonic_suite_definition_for_suite",
    # Result metadata
    "sonic_suite_expected_name",
    "sonic_result_timestamp",
    "sonic_result_status_text",
    "sonic_result_is_finished",
    "sonic_result_time_score",
    "sonic_score_result_for_suite",
    "sonic_score_result_meta_for_suite",
    "lookup_sonic_result_meta_for_suite",
    "lookup_sonic_report_for_suite",
    # Suite merge / migrate
    "merge_sonic_suite_result_items",
    "merge_sonic_suite_results",
    "sonic_suite_result_key_from_meta",
    "migrate_sonic_suite_to_result_key",
    "mark_sonic_suite_completed_from_result_meta",
    # Report
    "write_sonic_suite_summary_report",
    "build_sonic_suite_summary_card",
    # Registration
    "register_sonic_suite_completion",
    "sonic_suite_natural_key",
    "sonic_suite_key_for_job",
    "register_sonic_suite_result",
    # Timers
    "schedule_sonic_suite_summary",
    "restore_pending_sonic_suite_summary_timers",
    "send_sonic_suite_summary_if_quiet",
    # Execution
    "sonic_force_run_suite",
    "sonic_run_single_case",
    # Callback handlers
    "sonic_handle_callback",
    "sonic_handle_suite_completion",
    # Publish
    "sonic_publish_case",
    "sonic_publish_batch",
    "publish_case",
    "publish_batch",
    "handle_suite_complete",
    "handle_suite_report",
    # Backward compat
    "register_legacy_handlers",
    # Duration
    "sonic_suite_duration_text",
    "sonic_suite_time_range",
    # Public aliases for internal helpers
    "sonic_env_token",
    "sonic_login_credentials",
    "sonic_cached_token",
    "sonic_login_token",
    "sonic_token",
    "sonic_token_source",
    "sonic_referer_for_request",
    "sonic_headers",
    "sonic_response_auth_status",
    "sonic_auth_failure_message",
    "sonic_response_data",
    "sonic_match_task_case",
    "sonic_case_indexes",
    "sonic_job_matches_suite",
    "sonic_suite_has_running_jobs",
    "sonic_suite_can_wait_for_running_jobs",
]




# ---------------------------------------------------------------------------
# Migrated from midscene-upload.py
# ---------------------------------------------------------------------------

def start_sonic_result_post_actions(job, stdout, stderr):
    if not job or job.get("status") == "success":
        return False
    worker = threading.Thread(
        target=process_sonic_result_post_actions,
        args=(dict(job), stdout or "", stderr or ""),
        daemon=True
    )
    worker.start()
    return True



def attach_sonic_background_report(job_id, report_url="", local_report_path="", report_upload_error=""):
    from .job_service import find_job, save_jobs, update_task_meta

    job_id = str(job_id or "").strip()
    report_url = str(report_url or "").strip()
    local_report_path = str(local_report_path or "").strip()
    report_upload_error = sonic_notify_clean_text(report_upload_error or "", fallback="报告后台上传失败")
    if not job_id:
        raise ValueError("job_id 不能为空")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    updated_job = None
    suite_key = ""
    with JOB_LOCK:
        target, jobs = find_job(job_id)
        if not target:
            raise ValueError("执行记录不存在")
        if report_url:
            target["report_url"] = report_url
            target["report_upload_error"] = ""
        else:
            target["report_upload_error"] = report_upload_error or "后台上传未返回报告地址"
        if local_report_path:
            target["local_report_path"] = local_report_path
        target["report_upload_pending"] = False
        target["report_uploaded_at"] = now if report_url else ""
        suite_key = target.get("sonic_suite_key") or ""
        updated_job = dict(target)
        save_jobs(jobs)
    if updated_job.get("module") and updated_job.get("file") and report_url:
        update_task_meta(updated_job["module"], updated_job["file"], {
            "last_report_url": report_url
        })
    updated_suite = None
    if suite_key:
        with SONIC_SUITE_LOCK:
            state = load_sonic_suite_results()
            suite = (state.get("suites") or {}).get(suite_key)
            if suite:
                for item in suite.get("results") or []:
                    if item.get("job_id") == job_id:
                        if report_url:
                            item["report_url"] = report_url
                            item["report_upload_error"] = ""
                        else:
                            item["report_upload_error"] = report_upload_error or "后台上传未返回报告地址"
                        item["report_upload_pending"] = False
                        break
                state.setdefault("suites", {})[suite_key] = suite
                save_sonic_suite_results(state)
                updated_suite = dict(suite)
    if updated_suite and updated_suite.get("suite_report_url"):
        try:
            write_sonic_suite_summary_report(updated_suite)
        except Exception as e:
            append_sonic_notify_log("background_report_summary_refresh_error", {
                "suite_key": suite_key,
                "job_id": job_id,
            }, error=str(e))
    append_sonic_notify_log("background_midscene_report_attached" if report_url else "background_midscene_report_failed", {
        "suite_key": suite_key,
        "job_id": job_id,
        "report_url": report_url,
    }, error="" if report_url else report_upload_error)
    return updated_job



def append_sonic_notify_log(event, payload=None, result=None, error=""):
    try:
        os.makedirs(cfg.LEARNING_DIR, exist_ok=True)
        safe_payload = payload if isinstance(payload, dict) else {"payload": payload}
        safe_payload = {
            k: v for k, v in safe_payload.items()
            if str(k).lower() not in ("token", "x-token", "secret", "sign", "signature", "password")
        }
        row = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": event,
            "payload": safe_payload,
            "result": result if result is not None else {},
            "error": error or ""
        }
        with open(SONIC_NOTIFY_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"append_sonic_notify_log failed: {e}", flush=True)



def load_sonic_sync():
    data = read_json_file(cfg.SONIC_SYNC_FILE, default={"cases": {}})
    if not isinstance(data, dict):
        data = {"cases": {}}
    cases = data.get("cases") if isinstance(data.get("cases"), dict) else {}
    data["cases"] = cases
    return data



def save_sonic_sync(data):
    write_json_file(cfg.SONIC_SYNC_FILE, data)



def touch_sonic_suite_activity(job):
    if not job or job.get("source") != "sonic":
        return ""
    now_ts = int(time.time())
    should_schedule = False
    with SONIC_SUITE_LOCK:
        state = load_sonic_suite_results()
        suite_key = sonic_suite_key_for_job(job, state, now_ts)
        suite = (state.get("suites") or {}).get(suite_key) or {
            "suite_key": suite_key,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "created_ts": now_ts,
            "results": [],
        }
        app = sonic_suite_app_info(job.get("app_package", ""), job.get("module", ""))
        suite_started_at = job.get("suite_started_at") or job.get("suiteStartedAt") or suite.get("suite_started_at", "")
        suite_start_ts = _parse_time(suite_started_at)
        expected_total = max(
            _safe_int(suite.get("expected_total_count"), 0),
            _safe_int(job.get("suite_expected_total") or job.get("suiteExpectedTotal"), 0),
        )
        suite.update({
            "last_update_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_update_ts": now_ts,
            "natural_key": sonic_suite_natural_key(job),
            "suite_run_id": job.get("suite_run_id") or job.get("suiteRunId") or suite.get("suite_run_id", ""),
            "sonic_suite_id": job.get("sonic_suite_id") or job.get("sonicSuiteId") or app.get("sonic_suite_id") or app.get("sonicSuiteId") or suite.get("sonic_suite_id", ""),
            "app_package": app.get("package") or job.get("app_package", ""),
            "app_name": app.get("name") or job.get("app_name", ""),
            "app": app,
            "sonic_suite_name": job.get("sonic_suite_name") or app.get("sonic_suite_name") or app.get("sonicSuiteName") or suite.get("sonic_suite_name", ""),
            "suite_started_at": suite_started_at,
            "expected_total_count": expected_total,
            "run_mode": job.get("run_mode") or "baseline",
            "runner_id": job.get("runner_id") or "sonic",
            "device_id": job.get("device_id") or "",
            "last_running_job_id": job.get("job_id", ""),
            "last_running_case": job.get("target_task_name") or job.get("current_task_name") or job.get("file", ""),
        })
        if suite_start_ts:
            existing_created_ts = _safe_int(suite.get("created_ts"), 0)
            suite["created_ts"] = min(existing_created_ts, suite_start_ts) if existing_created_ts else suite_start_ts
            suite["created_at"] = suite_started_at
        should_schedule = bool(suite.get("results")) and not sonic_suite_waits_for_completion_event(job)
        if sonic_suite_waits_for_completion_event(job):
            suite["notification_mode"] = "suite_completion"
        state.setdefault("suites", {})[suite_key] = suite
        save_sonic_suite_results(state)
    if should_schedule:
        schedule_sonic_suite_summary(suite_key)
    return suite_key



def task_case_sonic_context(case_info):
    """Execution context inherited from the app binding; bridge runners should not need manual suite params."""
    app = _task_app_map_by_package().get(case_info.get("app_package") or "") or {}
    context = {
        "app_package": app.get("package") or case_info.get("app_package") or "",
        "app_name": app.get("name") or case_info.get("app_name") or "",
        "sonic_project_id": sonic_project_id_for_app(app),
        "sonic_project_name": sonic_project_name_for_app(app),
        "sonic_suite_id": str(sonic_suite_id_for_app(app) or ""),
        "sonic_suite_name": sonic_suite_name_for_app(app),
        "suite_expected_total": 0,
    }
    if context["sonic_suite_id"]:
        try:
            detail = sonic_response_data(sonic_request("GET", "/testSuites", params={"id": int(context["sonic_suite_id"])}, timeout=10)) or {}
            if isinstance(detail, dict):
                context["sonic_suite_name"] = detail.get("name") or context["sonic_suite_name"]
                context["suite_expected_total"] = sonic_count_suite_cases(detail)
        except Exception as e:
            context["suite_lookup_error"] = str(e)
    return context



def resolve_task_app_sonic_binding(app):
    """Resolve user-friendly Sonic names to stable ids and reject cross-project suite binding."""
    app = dict(app or {})
    if not (app.get("sonic_project_id") or app.get("sonic_project_name")):
        return app
    project_id = sonic_find_project_id(app)
    if not project_id:
        raise ValueError(f"未在 Sonic 找到项目「{app.get('sonic_project_name') or app.get('sonic_project_id')}」")
    app["sonic_project_id"] = str(project_id)
    for project in sonic_list_projects():
        if _safe_int(project.get("id"), 0) == project_id:
            app["sonic_project_name"] = project.get("projectName") or project.get("name") or app.get("sonic_project_name", "")
            break
    suite_id = sonic_suite_id_for_app(app)
    suite_name = sonic_suite_name_for_app(app)
    suites = []
    if suite_id:
        detail = sonic_response_data(sonic_request("GET", "/testSuites", params={"id": suite_id}, timeout=15))
        if isinstance(detail, dict) and detail:
            suites = [detail]
        elif isinstance(detail, list):
            suites = [item for item in detail if isinstance(item, dict)]
        if not suites:
            data = sonic_response_data(sonic_request("GET", "/testSuites/listAll", params={"projectId": project_id}, timeout=15)) or []
            rows = data if isinstance(data, list) else _extract_page_items(data)
            suites = [row for row in rows if isinstance(row, dict) and _safe_int(row.get("id"), 0) == suite_id]
    if not suites and suite_name:
        data = sonic_response_data(sonic_request("GET", "/testSuites/listAll", params={"projectId": project_id}, timeout=15)) or []
        rows = data if isinstance(data, list) else _extract_page_items(data)
        exact_name = re.sub(r"\s+", "", suite_name)
        suites = [
            row for row in rows if isinstance(row, dict)
            and re.sub(r"\s+", "", str(row.get("name") or "")) == exact_name
        ]
        if not suites:
            raise ValueError(f"项目「{app.get('sonic_project_name')}」下未找到测试套「{suite_name}」")
        if len(suites) > 1:
            raise ValueError(f"项目内存在多个同名测试套「{suite_name}」，请填写测试套 ID")
    if suite_id and not suites:
        raise ValueError(f"未在 Sonic 找到测试套 ID：{suite_id}")
    if suites:
        suite = suites[0]
        if _safe_int(suite.get("projectId"), 0) not in (0, project_id):
            raise ValueError("Sonic 测试套不属于当前应用绑定的项目")
        app["sonic_suite_id"] = str(_safe_int(suite.get("id"), 0))
        app["sonic_suite_name"] = suite.get("name") or suite_name
        app["sonic_suite_case_count"] = sonic_count_suite_cases(suite)
    return app
