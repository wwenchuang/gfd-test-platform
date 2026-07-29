"""Secure read-only discovery through the official Apifox CLI."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
from typing import Any, Dict, List, Optional, Sequence, Tuple


MINIMUM_CLI_VERSION = (2, 2, 6)
MINIMUM_CLI_VERSION_TEXT = ".".join(str(item) for item in MINIMUM_CLI_VERSION)
DEFAULT_CLI_BIN = "apifox"
DEFAULT_BASE_URL = "https://api.apifox.com"
_VERSION_PATTERN = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_ENV_ALLOWLIST = (
    "PATH",
    "LANG",
    "LC_ALL",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "NODE_EXTRA_CA_CERTS",
)


class ApifoxDiscoveryError(RuntimeError):
    """Stable, token-safe failure returned by the discovery boundary."""

    def __init__(self, code: str, message: str, http_status: int):
        super().__init__(message)
        self.code = str(code or "DISCOVERY_FAILED")
        self.http_status = int(http_status or 503)
        self.manual_fallback = True

    def as_dict(self) -> Dict[str, Any]:
        return {
            "ok": False,
            "code": self.code,
            "error": str(self),
            "manual_fallback": True,
        }


def _safe_text(value: Any, limit: int = 500) -> str:
    return str(value or "").strip()[:limit]


def _safe_error_text(value: Any, token: str = "") -> str:
    text = str(value or "")
    if token:
        text = text.replace(token, "<redacted>")
    return text[:4000]


def _normalize_base_url(value: Any) -> str:
    base_url = _safe_text(value or DEFAULT_BASE_URL, 500).rstrip("/")
    parsed = urllib.parse.urlparse(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ApifoxDiscoveryError("DISCOVERY_FAILED", "Apifox 服务地址无效", 400)
    return base_url


def _resolve_cli(cli_bin: Optional[str] = None) -> str:
    requested = _safe_text(cli_bin or os.getenv("APIFOX_CLI_BIN") or DEFAULT_CLI_BIN, 1000)
    if os.path.isabs(requested) and os.path.isfile(requested) and os.access(requested, os.X_OK):
        return requested
    resolved = shutil.which(requested)
    if not resolved:
        raise ApifoxDiscoveryError(
            "CLI_UNAVAILABLE",
            "服务器未安装可用的 Apifox CLI，请使用手动连接",
            503,
        )
    return resolved


def _version_tuple(value: str) -> Tuple[int, int, int]:
    match = _VERSION_PATTERN.search(str(value or ""))
    if not match:
        raise ApifoxDiscoveryError(
            "CLI_VERSION_UNSUPPORTED",
            "无法识别 Apifox CLI 版本，请使用手动连接",
            503,
        )
    return tuple(int(match.group(index)) for index in range(1, 4))


def _remaining_seconds(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ApifoxDiscoveryError("TIMEOUT", "读取 Apifox 资产超时，请重试或使用手动连接", 504)
    return max(0.05, remaining)


def _isolated_environment(home: str) -> Dict[str, str]:
    env = {
        key: value
        for key in _ENV_ALLOWLIST
        for value in [os.environ.get(key)]
        if value
    }
    config_home = os.path.join(home, ".config")
    env.update({
        "HOME": home,
        "XDG_CONFIG_HOME": config_home,
        "APPDATA": os.path.join(home, ".appdata"),
        "LOCALAPPDATA": os.path.join(home, ".local"),
        "NO_COLOR": "1",
        "APIFOX_CLI_TELEMETRY": "0",
    })
    return env


def _raise_process_error(stdout: str, stderr: str, token: str, *, missing_project: bool = False) -> None:
    detail = _safe_error_text(f"{stdout}\n{stderr}", token).lower()
    if any(marker in detail for marker in ("authentication_failed", "invalid access token", "unauthorized", "401")):
        raise ApifoxDiscoveryError("AUTH_FAILED", "Apifox 访问令牌无效或已过期", 401)
    if any(marker in detail for marker in ("permission_denied", "permission denied", "forbidden", "403")):
        raise ApifoxDiscoveryError("PERMISSION_DENIED", "当前令牌无权读取该 Apifox 资产", 403)
    if missing_project and any(marker in detail for marker in ("not_found", "not found", "404")):
        raise ApifoxDiscoveryError("PROJECT_NOT_FOUND", "Apifox 项目不存在或已不可访问", 404)
    raise ApifoxDiscoveryError(
        "DISCOVERY_FAILED",
        "Apifox 资产读取失败，请重试或使用手动连接",
        503,
    )


def _run_cli(
    args: Sequence[str],
    *,
    env: Dict[str, str],
    deadline: float,
    token: str,
    input_text: str = "",
    missing_project: bool = False,
) -> str:
    try:
        result = subprocess.run(
            list(args),
            input=input_text,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            cwd=env["HOME"],
            env=env,
            timeout=_remaining_seconds(deadline),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ApifoxDiscoveryError(
            "TIMEOUT",
            "读取 Apifox 资产超时，请重试或使用手动连接",
            504,
        ) from exc
    except OSError as exc:
        raise ApifoxDiscoveryError(
            "CLI_UNAVAILABLE",
            "服务器无法启动 Apifox CLI，请使用手动连接",
            503,
        ) from exc
    if result.returncode != 0:
        _raise_process_error(
            result.stdout,
            result.stderr,
            token,
            missing_project=missing_project,
        )
    return _safe_error_text(result.stdout, token)


def _run_json_cli(
    args: Sequence[str],
    *,
    env: Dict[str, str],
    deadline: float,
    token: str,
    missing_project: bool = False,
) -> Any:
    output = _run_cli(
        args,
        env=env,
        deadline=deadline,
        token=token,
        missing_project=missing_project,
    )
    try:
        payload = json.loads(output)
    except (TypeError, ValueError) as exc:
        raise ApifoxDiscoveryError(
            "INVALID_RESPONSE",
            "Apifox CLI 返回了无法识别的数据，请使用手动连接",
            503,
        ) from exc
    if not isinstance(payload, dict):
        raise ApifoxDiscoveryError(
            "INVALID_RESPONSE",
            "Apifox CLI 返回了无法识别的数据，请使用手动连接",
            503,
        )
    if payload.get("success") is False:
        _raise_process_error(
            json.dumps(payload, ensure_ascii=False),
            "",
            token,
            missing_project=missing_project,
        )
    if "data" not in payload:
        raise ApifoxDiscoveryError(
            "INVALID_RESPONSE",
            "Apifox CLI 响应缺少资产数据，请使用手动连接",
            503,
        )
    return payload.get("data")


def get_cli_capability(
    cli_bin: Optional[str] = None,
    *,
    timeout_seconds: float = 5.0,
) -> Dict[str, Any]:
    cli_path = _resolve_cli(cli_bin)
    try:
        result = subprocess.run(
            [cli_path, "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            timeout=max(0.1, float(timeout_seconds or 5.0)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ApifoxDiscoveryError("TIMEOUT", "检查 Apifox CLI 版本超时", 504) from exc
    except OSError as exc:
        raise ApifoxDiscoveryError(
            "CLI_UNAVAILABLE",
            "服务器无法启动 Apifox CLI，请使用手动连接",
            503,
        ) from exc
    raw_version = f"{result.stdout}\n{result.stderr}".strip()
    if result.returncode != 0:
        raise ApifoxDiscoveryError(
            "CLI_UNAVAILABLE",
            "服务器无法检查 Apifox CLI 版本，请使用手动连接",
            503,
        )
    parsed = _version_tuple(raw_version)
    version = ".".join(str(item) for item in parsed)
    if parsed < MINIMUM_CLI_VERSION:
        raise ApifoxDiscoveryError(
            "CLI_VERSION_UNSUPPORTED",
            f"Apifox CLI {version} 版本过低，需要 {MINIMUM_CLI_VERSION_TEXT} 或更高版本",
            503,
        )
    return {
        "available": True,
        "version": version,
        "minimum_version": MINIMUM_CLI_VERSION_TEXT,
        "_cli_path": cli_path,
    }


def _project(value: Any) -> Dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    project_id = _safe_text(raw.get("id", raw.get("projectId")), 100)
    if not project_id:
        return {}
    team_value = raw.get("team")
    team = team_value if isinstance(team_value, dict) else {}
    return {
        "id": project_id,
        "name": _safe_text(raw.get("name"), 200) or "未命名项目",
        "description": _safe_text(raw.get("description"), 500),
        "team": {
            "id": _safe_text(team.get("id", raw.get("teamId")), 100),
            "name": _safe_text(team.get("name", raw.get("teamName")), 200),
        },
    }


def _is_default_branch(raw: Dict[str, Any]) -> bool:
    if raw.get("isDefault") is True or raw.get("is_default") is True:
        return True
    branch_type = _safe_text(raw.get("type", raw.get("branchType")), 50).upper()
    return branch_type in {"MAIN", "DEFAULT"}


def _named_options(values: Any, *, kind: str) -> List[Dict[str, Any]]:
    rows = values if isinstance(values, list) else []
    result: List[Dict[str, Any]] = []
    seen = set()
    for item in rows:
        raw = item if isinstance(item, dict) else {}
        item_id = _safe_text(raw.get("id", raw.get(f"{kind}Id")), 100)
        if not item_id or item_id in seen:
            continue
        if kind == "branch" and _is_default_branch(raw):
            continue
        seen.add(item_id)
        result.append({
            "id": item_id,
            "name": _safe_text(raw.get("name"), 200) or f"未命名{'分支' if kind == 'branch' else '环境'}",
            "is_default": False,
        })
    return result


def _discovery_session(
    access_token: str,
    base_url: str,
    timeout_seconds: float,
    cli_bin: Optional[str],
) -> Tuple[Dict[str, Any], str, str, Dict[str, str], float, tempfile.TemporaryDirectory]:
    token = _safe_text(access_token, 10000)
    if not token:
        raise ApifoxDiscoveryError("AUTH_FAILED", "请输入 Apifox 访问令牌", 400)
    normalized_base_url = _normalize_base_url(base_url)
    deadline = time.monotonic() + max(0.1, float(timeout_seconds or 0))
    capability = get_cli_capability(
        cli_bin,
        timeout_seconds=_remaining_seconds(deadline),
    )
    cli_path = str(capability.pop("_cli_path"))
    temp_home = tempfile.TemporaryDirectory(prefix="midscene-apifox-")
    os.chmod(temp_home.name, 0o700)
    env = _isolated_environment(temp_home.name)
    try:
        _run_cli(
            [cli_path, "auth", "login", "--api-base-url", normalized_base_url],
            input_text=f"{token}\n",
            env=env,
            deadline=deadline,
            token=token,
        )
    except Exception:
        temp_home.cleanup()
        raise
    return capability, cli_path, token, env, deadline, temp_home


def discover_projects(
    access_token: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout_seconds: float = 20.0,
    cli_bin: Optional[str] = None,
) -> Dict[str, Any]:
    capability, cli_path, token, env, deadline, temp_home = _discovery_session(
        access_token,
        base_url,
        timeout_seconds,
        cli_bin,
    )
    try:
        values = _run_json_cli(
            [cli_path, "project", "list", "--api-base-url", _normalize_base_url(base_url)],
            env=env,
            deadline=deadline,
            token=token,
        )
        if not isinstance(values, list):
            raise ApifoxDiscoveryError(
                "INVALID_RESPONSE",
                "Apifox CLI 项目列表格式无效，请使用手动连接",
                503,
            )
        projects = [project for item in values for project in [_project(item)] if project]
        projects.sort(key=lambda item: (item["name"].casefold(), item["id"]))
        return {"capability": capability, "projects": projects}
    finally:
        temp_home.cleanup()


def discover_project_context(
    access_token: str,
    project_id: str,
    *,
    base_url: str = DEFAULT_BASE_URL,
    timeout_seconds: float = 25.0,
    cli_bin: Optional[str] = None,
) -> Dict[str, Any]:
    target_project_id = _safe_text(project_id, 100)
    if not target_project_id:
        raise ApifoxDiscoveryError("PROJECT_NOT_FOUND", "请选择 Apifox 项目", 400)
    capability, cli_path, token, env, deadline, temp_home = _discovery_session(
        access_token,
        base_url,
        timeout_seconds,
        cli_bin,
    )
    normalized_base_url = _normalize_base_url(base_url)
    common = ["--project", target_project_id, "--api-base-url", normalized_base_url]
    try:
        project_value = _run_json_cli(
            [cli_path, "project", "get", target_project_id, "--api-base-url", normalized_base_url],
            env=env,
            deadline=deadline,
            token=token,
            missing_project=True,
        )
        project = _project(project_value)
        if not project:
            raise ApifoxDiscoveryError(
                "PROJECT_NOT_FOUND",
                "Apifox 项目不存在或已不可访问",
                404,
            )
        branch_values = _run_json_cli(
            [cli_path, "branch", "list", *common],
            env=env,
            deadline=deadline,
            token=token,
            missing_project=True,
        )
        environment_values = _run_json_cli(
            [cli_path, "environment", "list", *common],
            env=env,
            deadline=deadline,
            token=token,
            missing_project=True,
        )
        branches = [
            {"id": "", "name": "主分支（默认）", "is_default": True},
            *_named_options(branch_values, kind="branch"),
        ]
        environments = [
            {"id": "", "name": "不绑定环境", "is_default": True},
            *_named_options(environment_values, kind="environment"),
        ]
        return {
            "capability": capability,
            "project": project,
            "branches": branches,
            "environments": environments,
        }
    finally:
        temp_home.cleanup()


__all__ = [
    "ApifoxDiscoveryError",
    "DEFAULT_BASE_URL",
    "MINIMUM_CLI_VERSION_TEXT",
    "discover_project_context",
    "discover_projects",
    "get_cli_capability",
]
