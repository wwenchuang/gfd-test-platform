"""Explicit, non-interactive Apifox export adapter."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable, Mapping, Sequence


_ENVIRONMENT_ALLOWLIST = (
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


class ApifoxAdapterError(RuntimeError):
    """Safe adapter error that never exposes the access token."""


@dataclass(frozen=True)
class ApifoxConnection:
    project_id: str
    branch_id: str = ""
    environment_id: str = ""


@dataclass(frozen=True)
class ApifoxImportResult:
    document: Mapping[str, Any]
    environment_metadata: Mapping[str, Any]
    identifiers: Mapping[str, str]


def _redact(value: Any, token: str) -> str:
    text = str(value or "")
    if token:
        text = text.replace(token, "[REDACTED]")
    return text


def _contains_token(value: Any, token: str) -> bool:
    if isinstance(value, str):
        return token in value
    if isinstance(value, dict):
        return any(
            _contains_token(key, token) or _contains_token(item, token)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_token(item, token) for item in value)
    return False


def _isolated_environment(home: str, token: str) -> Mapping[str, str]:
    environment = {
        key: os.environ[key]
        for key in _ENVIRONMENT_ALLOWLIST
        if os.environ.get(key)
    }
    environment.update(
        {
            "HOME": home,
            "XDG_CONFIG_HOME": str(Path(home) / ".config"),
            "APPDATA": str(Path(home) / ".appdata"),
            "LOCALAPPDATA": str(Path(home) / ".local"),
            "NO_COLOR": "1",
            "APIFOX_CLI_TELEMETRY": "0",
            "APIFOX_ACCESS_TOKEN": token,
        }
    )
    return environment


class ApifoxAdapter:
    def __init__(
        self,
        command: Sequence[str],
        runner: Callable[..., Any] = subprocess.run,
        timeout_seconds: int = 120,
    ):
        if not command or any(not isinstance(part, str) or not part for part in command):
            raise ValueError("Apifox command must be a non-empty argument array")
        if timeout_seconds <= 0:
            raise ValueError("Apifox timeout must be positive")
        self._command = tuple(command)
        self._runner = runner
        self._timeout_seconds = timeout_seconds

    def fetch(self, access_token: str, connection: ApifoxConnection) -> ApifoxImportResult:
        token = str(access_token or "").strip()
        if not token:
            raise ApifoxAdapterError("Apifox access token is required")
        if not str(connection.project_id or "").strip():
            raise ApifoxAdapterError("Apifox project identifier is required")

        with tempfile.TemporaryDirectory(prefix="midscene-apifox-") as temporary_directory:
            os.chmod(temporary_directory, 0o700)
            output_path = Path(temporary_directory) / "openapi.json"
            environment_path = Path(temporary_directory) / "environment.json"
            arguments = list(self._command) + [
                "--project-id",
                connection.project_id,
                "--output",
                str(output_path),
                "--environment-output",
                str(environment_path),
                "--format",
                "openapi-json",
            ]
            if connection.branch_id:
                arguments.extend(["--branch-id", connection.branch_id])
            if connection.environment_id:
                arguments.extend(["--environment-id", connection.environment_id])
            if any(token in argument for argument in arguments):
                raise ApifoxAdapterError("Apifox access token must not be passed in command arguments")
            environment = _isolated_environment(temporary_directory, token)
            try:
                self._runner(
                    arguments,
                    cwd=temporary_directory,
                    env=environment,
                    shell=False,
                    timeout=self._timeout_seconds,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                document = json.loads(output_path.read_text(encoding="utf-8"))
                metadata = (
                    json.loads(environment_path.read_text(encoding="utf-8"))
                    if environment_path.exists()
                    else {}
                )
                if _contains_token(document, token) or _contains_token(metadata, token):
                    raise ApifoxAdapterError(
                        "Apifox export contained the access token and was rejected"
                    )
            except subprocess.TimeoutExpired:
                raise ApifoxAdapterError("Apifox command timed out") from None
            except subprocess.CalledProcessError as error:
                detail = _redact(error.stderr or error.output or "command failed", token)
                raise ApifoxAdapterError("Apifox command failed: %s" % detail) from None
            except ApifoxAdapterError:
                raise
            except (OSError, ValueError) as error:
                raise ApifoxAdapterError(
                    "Apifox export could not be read: %s" % _redact(error, token)
                ) from None

        return ApifoxImportResult(
            document=document,
            environment_metadata=metadata,
            identifiers={
                "project_id": connection.project_id,
                "branch_id": connection.branch_id,
                "environment_id": connection.environment_id,
            },
        )
