"""Secure, read-only Apifox CLI discovery."""

import json
import os
import re
import shutil
import subprocess
import tempfile
from typing import Any

from ..contracts.provider import (
    ApifoxBranch,
    ApifoxEnvironment,
    ApifoxEnvironmentService,
    ApifoxEnvironmentVariable,
    ApifoxProject,
    ApifoxProjectContext,
)


MINIMUM_CLI_VERSION = (2, 2, 6)
DEFAULT_BASE_URL = "https://api.apifox.com"
_VERSION = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_SENSITIVE = re.compile(
    r"token|secret|password|passwd|pwd|authorization|cookie|session|api[_-]?key|private",
    re.IGNORECASE,
)
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
    def __init__(self, code, message, http_status=503):
        self.code = code
        self.http_status = http_status
        self.manual_fallback = True
        super().__init__(message)


def _safe_text(value, limit=500):
    return str(value or "").strip()[:limit]


def _identifier(value, label):
    text = _safe_text(value, 100)
    if not text or not re.fullmatch(r"[A-Za-z0-9._-]+", text):
        raise ApifoxDiscoveryError("INVALID_REQUEST", "%s无效" % label, 400)
    return text


def _isolated_environment(home):
    environment = {
        key: os.environ[key]
        for key in _ENV_ALLOWLIST
        if os.environ.get(key)
    }
    environment.update(
        {
            "HOME": home,
            "XDG_CONFIG_HOME": os.path.join(home, ".config"),
            "APPDATA": os.path.join(home, ".appdata"),
            "LOCALAPPDATA": os.path.join(home, ".local"),
            "NO_COLOR": "1",
            "APIFOX_CLI_TELEMETRY": "0",
        }
    )
    return environment


def _json_payload(output):
    text = str(output or "").strip()
    decoder = json.JSONDecoder()
    candidates = [text]
    candidates.extend(text[index:] for index, char in enumerate(text) if char in "{[")
    for candidate in candidates:
        try:
            payload, _ = decoder.raw_decode(candidate)
        except (TypeError, ValueError):
            continue
        if not isinstance(payload, dict) or "data" not in payload:
            continue
        if payload.get("success") is False:
            raise ApifoxDiscoveryError(
                "DISCOVERY_FAILED", "Apifox 资产读取失败，请重试", 503
            )
        return payload["data"]
    raise ApifoxDiscoveryError(
        "INVALID_RESPONSE", "Apifox CLI 返回了无法识别的数据", 503
    )


def _project(value):
    raw = value if isinstance(value, dict) else {}
    team = raw.get("team") if isinstance(raw.get("team"), dict) else {}
    return ApifoxProject(
        id=_identifier(raw.get("id", raw.get("projectId")), "Apifox 项目 ID"),
        name=_safe_text(raw.get("name"), 200) or "未命名项目",
        description=_safe_text(raw.get("description"), 500),
        team_name=_safe_text(team.get("name", raw.get("teamName")), 200),
    )


def _branch(value):
    raw = value if isinstance(value, dict) else {}
    branch_type = _safe_text(raw.get("type", raw.get("branchType")), 50).upper()
    is_default = bool(
        raw.get("isDefault")
        or raw.get("is_default")
        or branch_type in {"MAIN", "DEFAULT"}
    )
    return ApifoxBranch(
        id=_identifier(raw.get("id", raw.get("branchId")), "Apifox 分支 ID"),
        name=_safe_text(raw.get("name"), 200) or "未命名分支",
        is_default=is_default,
    )


def _first_value(raw, keys):
    for key in keys:
        if key in raw:
            return raw.get(key)
    return None


def _service_rows(raw):
    values = _first_value(
        raw,
        ("services", "serviceList", "servers", "baseUrls", "base_urls"),
    )
    if isinstance(values, dict):
        values = [
            ({"name": key, "url": value} if not isinstance(value, dict) else {"name": key, **value})
            for key, value in values.items()
        ]
    if not isinstance(values, list):
        values = []
    result = []
    for index, item in enumerate(values):
        if isinstance(item, str):
            item = {"name": "default" if index == 0 else "service-%d" % index, "url": item}
        if not isinstance(item, dict):
            continue
        name = _safe_text(item.get("name", item.get("key", item.get("id"))), 200)
        url = _safe_text(
            _first_value(item, ("url", "value", "baseUrl", "base_url", "host")),
            1000,
        )
        result.append(
            ApifoxEnvironmentService(
                name=name or ("default" if index == 0 else "service-%d" % index),
                module_name=_safe_text(item.get("module", item.get("moduleName")), 200)
                or "default",
                base_url=url or None,
                provider_id=_safe_text(item.get("id"), 100),
            )
        )
    if not result:
        base_url = _safe_text(
            _first_value(raw, ("baseUrl", "base_url", "url")), 1000
        )
        if base_url:
            result.append(
                ApifoxEnvironmentService("default", "default", base_url, "")
            )
    return tuple(result)


def _variable_rows(raw):
    values = _first_value(
        raw,
        (
            "variables",
            "variableList",
            "environmentVariables",
            "commonVariables",
            "parameters",
        ),
    )
    if isinstance(values, dict):
        values = [
            ({"name": key, "value": value} if not isinstance(value, dict) else {"name": key, **value})
            for key, value in values.items()
        ]
    if not isinstance(values, list):
        values = []
    result = []
    seen = set()
    for item in values:
        if not isinstance(item, dict):
            continue
        name = _safe_text(
            _first_value(item, ("name", "key", "variableName", "parameterName")),
            200,
        )
        if not name or name in seen:
            continue
        seen.add(name)
        sensitive = bool(
            item.get("sensitive")
            or item.get("secret")
            or item.get("private")
            or item.get("isSensitive")
            or item.get("isSecret")
            or _SENSITIVE.search(name)
        )
        value = _safe_text(
            _first_value(
                item,
                (
                    "value",
                    "currentValue",
                    "localValue",
                    "defaultValue",
                    "initialValue",
                ),
            ),
            2000,
        )
        result.append(
            ApifoxEnvironmentVariable(
                name=name,
                value="" if sensitive else value,
                sensitive=sensitive,
                scope=_safe_text(item.get("scope", item.get("type")), 50)
                or "environment",
            )
        )
    return tuple(result)


def _environment(value):
    raw = value if isinstance(value, dict) else {}
    return ApifoxEnvironment(
        id=_identifier(
            raw.get("id", raw.get("environmentId")), "Apifox 环境 ID"
        ),
        name=_safe_text(raw.get("name"), 200) or "未命名环境",
        services=_service_rows(raw),
        variables=_variable_rows(raw),
    )


class ApifoxDiscoveryAdapter:
    def __init__(
        self,
        cli_bin="apifox",
        base_url=DEFAULT_BASE_URL,
        timeout_seconds=25,
        runner=subprocess.run,
        cli_resolver=shutil.which,
    ):
        self._cli_bin = cli_bin
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._runner = runner
        self._cli_resolver = cli_resolver

    def _run(self, arguments, *, environment, cwd, token, input_text=""):
        try:
            result = self._runner(
                arguments,
                input=input_text,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                cwd=cwd,
                env=environment,
                timeout=self._timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ApifoxDiscoveryError(
                "TIMEOUT", "读取 Apifox 资产超时，请重试", 504
            ) from None
        except OSError:
            raise ApifoxDiscoveryError(
                "CLI_UNAVAILABLE", "服务器无法启动 Apifox CLI", 503
            ) from None
        if result.returncode:
            detail = (str(result.stdout or "") + "\n" + str(result.stderr or ""))
            detail = detail.replace(token, "[REDACTED]").lower()
            if any(marker in detail for marker in ("401", "unauthorized", "invalid token")):
                raise ApifoxDiscoveryError(
                    "AUTH_FAILED", "Apifox 访问令牌无效或已过期", 401
                )
            if any(marker in detail for marker in ("403", "forbidden", "permission")):
                raise ApifoxDiscoveryError(
                    "PERMISSION_DENIED", "当前令牌无权读取该 Apifox 资产", 403
                )
            raise ApifoxDiscoveryError(
                "DISCOVERY_FAILED", "Apifox 资产读取失败，请重试", 503
            )
        return str(result.stdout or "").replace(token, "[REDACTED]")

    def _session(self, token):
        if not isinstance(token, str) or not token.strip():
            raise ApifoxDiscoveryError("AUTH_FAILED", "请先保存 Apifox 访问令牌", 400)
        path = self._cli_resolver(self._cli_bin)
        if not path:
            raise ApifoxDiscoveryError(
                "CLI_UNAVAILABLE", "服务器未安装可用的 Apifox CLI", 503
            )
        temporary = tempfile.TemporaryDirectory(prefix="midscene-apifox-")
        os.chmod(temporary.name, 0o700)
        environment = _isolated_environment(temporary.name)
        version_output = self._run(
            [path, "--version"],
            environment=environment,
            cwd=temporary.name,
            token=token,
        )
        match = _VERSION.search(version_output)
        if not match:
            temporary.cleanup()
            raise ApifoxDiscoveryError(
                "CLI_VERSION_UNSUPPORTED", "无法识别 Apifox CLI 版本", 503
            )
        version_tuple = tuple(int(match.group(index)) for index in range(1, 4))
        if version_tuple < MINIMUM_CLI_VERSION:
            temporary.cleanup()
            raise ApifoxDiscoveryError(
                "CLI_VERSION_UNSUPPORTED", "Apifox CLI 版本过低", 503
            )
        self._run(
            [path, "auth", "login", "--api-base-url", self._base_url],
            environment=environment,
            cwd=temporary.name,
            token=token,
            input_text=token + "\n",
        )
        return temporary, path, environment, ".".join(str(item) for item in version_tuple)

    def list_projects(self, token):
        temporary, path, environment, _ = self._session(token)
        try:
            output = self._run(
                [path, "project", "list", "--api-base-url", self._base_url],
                environment=environment,
                cwd=temporary.name,
                token=token,
            )
            values = _json_payload(output)
            if not isinstance(values, list):
                raise ApifoxDiscoveryError(
                    "INVALID_RESPONSE", "Apifox 项目列表格式无效", 503
                )
            return tuple(sorted((_project(item) for item in values), key=lambda item: (item.name.casefold(), item.id)))
        finally:
            temporary.cleanup()

    def get_context(self, token, project_id, preferred_environment_id=""):
        project_id = _identifier(project_id, "Apifox 项目 ID")
        temporary, path, environment, version = self._session(token)
        try:
            def json_command(arguments):
                return _json_payload(
                    self._run(
                        arguments,
                        environment=environment,
                        cwd=temporary.name,
                        token=token,
                    )
                )

            project_value = json_command(
                [path, "project", "get", project_id, "--api-base-url", self._base_url]
            )
            branch_values = json_command(
                [
                    path,
                    "branch",
                    "list",
                    "--project",
                    project_id,
                    "--type",
                    "all",
                    "--api-base-url",
                    self._base_url,
                ]
            )
            environment_values = json_command(
                [
                    path,
                    "environment",
                    "list",
                    "--project",
                    project_id,
                    "--api-base-url",
                    self._base_url,
                ]
            )
            branches = [ApifoxBranch("", "主分支（默认）", True)]
            for value in branch_values if isinstance(branch_values, list) else []:
                item = _branch(value)
                if not item.is_default:
                    branches.append(item)
            environments = []
            values = environment_values if isinstance(environment_values, list) else []
            for raw in values:
                raw_id = _identifier(
                    (raw if isinstance(raw, dict) else {}).get(
                        "id", (raw if isinstance(raw, dict) else {}).get("environmentId")
                    ),
                    "Apifox 环境 ID",
                )
                detail = raw
                if len(values) <= 30 or raw_id == str(preferred_environment_id or ""):
                    detail_value = json_command(
                        [
                            path,
                            "environment",
                            "get",
                            raw_id,
                            "--project",
                            project_id,
                            "--api-base-url",
                            self._base_url,
                        ]
                    )
                    if isinstance(raw, dict) and isinstance(detail_value, dict):
                        detail = {**raw, **detail_value}
                environments.append(_environment(detail))
            return ApifoxProjectContext(
                project=_project(project_value),
                branches=tuple(branches),
                environments=tuple(environments),
                cli_version=version,
            )
        finally:
            temporary.cleanup()
