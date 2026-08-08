"""Explicit, bounded, non-interactive Apifox export adapter."""

from dataclasses import dataclass
import json
import os
from pathlib import Path
import selectors
import subprocess
import tempfile
import time
from typing import Any, Mapping, Sequence, Tuple


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


def _stop_process(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=1)


def _diagnostic(data: bytes, token: str, limit: int) -> str:
    # Truncate first so untrusted process output never expands an error object.
    truncated = data[:limit]
    return _redact(truncated.decode("utf-8", errors="replace"), token)


def _run_bounded(
    arguments: Sequence[str],
    cwd: str,
    environment: Mapping[str, str],
    timeout_seconds: int,
    max_stdout_bytes: int,
    max_stderr_bytes: int,
    diagnostic_bytes: int,
    token: str,
) -> Tuple[bytes, bytes]:
    process = subprocess.Popen(
        list(arguments),
        cwd=cwd,
        env=dict(environment),
        shell=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,
    )
    stdout = bytearray()
    stderr = bytearray()
    streams = {
        process.stdout: ("stdout", stdout, max_stdout_bytes),
        process.stderr: ("stderr", stderr, max_stderr_bytes),
    }
    selector = selectors.DefaultSelector()
    for stream in streams:
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout_seconds
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _stop_process(process)
                raise ApifoxAdapterError("Apifox command timed out")
            events = selector.select(timeout=min(remaining, 0.25))
            if not events:
                continue
            for key, _ in events:
                stream = key.fileobj
                name, target, limit = streams[stream]
                chunk = os.read(stream.fileno(), min(65536, limit - len(target) + 1))
                if not chunk:
                    selector.unregister(stream)
                    continue
                target.extend(chunk)
                if len(target) > limit:
                    _stop_process(process)
                    detail = _diagnostic(bytes(target), token, diagnostic_bytes)
                    suffix = ": %s" % detail if detail else ""
                    raise ApifoxAdapterError(
                        "Apifox %s exceeded %s bytes%s" % (name, limit, suffix)
                    )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _stop_process(process)
            raise ApifoxAdapterError("Apifox command timed out")
        return_code = process.wait(timeout=remaining)
        if return_code:
            detail = _diagnostic(bytes(stderr or stdout), token, diagnostic_bytes)
            raise ApifoxAdapterError(
                "Apifox command failed%s" % (": %s" % detail if detail else "")
            )
        return bytes(stdout), bytes(stderr)
    except subprocess.TimeoutExpired:
        _stop_process(process)
        raise ApifoxAdapterError("Apifox command timed out") from None
    except OSError as error:
        _stop_process(process)
        raise ApifoxAdapterError(
            "Apifox command could not run: %s" % _redact(error, token)
        ) from None
    finally:
        selector.close()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


def _read_json_file(path: Path, maximum_bytes: int, label: str) -> Any:
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        raise ApifoxAdapterError("Apifox %s was not created" % label) from None
    if size > maximum_bytes:
        raise ApifoxAdapterError(
            "Apifox %s exceeded %s bytes" % (label, maximum_bytes)
        )
    with path.open("rb") as handle:
        payload = handle.read(maximum_bytes + 1)
    if len(payload) > maximum_bytes:
        raise ApifoxAdapterError(
            "Apifox %s exceeded %s bytes" % (label, maximum_bytes)
        )
    return json.loads(payload.decode("utf-8"))


class ApifoxAdapter:
    def __init__(
        self,
        command: Sequence[str],
        timeout_seconds: int = 120,
        max_stdout_bytes: int = 256 * 1024,
        max_stderr_bytes: int = 128 * 1024,
        max_openapi_bytes: int = 20 * 1024 * 1024,
        max_environment_bytes: int = 2 * 1024 * 1024,
        diagnostic_bytes: int = 4096,
    ):
        if not command or any(not isinstance(part, str) or not part for part in command):
            raise ValueError("Apifox command must be a non-empty argument array")
        limits = (
            timeout_seconds,
            max_stdout_bytes,
            max_stderr_bytes,
            max_openapi_bytes,
            max_environment_bytes,
            diagnostic_bytes,
        )
        if any(not isinstance(value, int) or value <= 0 for value in limits):
            raise ValueError("Apifox timeout and byte limits must be positive integers")
        self._command = tuple(command)
        self._timeout_seconds = timeout_seconds
        self._max_stdout_bytes = max_stdout_bytes
        self._max_stderr_bytes = max_stderr_bytes
        self._max_openapi_bytes = max_openapi_bytes
        self._max_environment_bytes = max_environment_bytes
        self._diagnostic_bytes = diagnostic_bytes

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
                _run_bounded(
                    arguments,
                    temporary_directory,
                    environment,
                    self._timeout_seconds,
                    self._max_stdout_bytes,
                    self._max_stderr_bytes,
                    self._diagnostic_bytes,
                    token,
                )
                document = _read_json_file(
                    output_path, self._max_openapi_bytes, "OpenAPI export"
                )
                metadata = (
                    _read_json_file(
                        environment_path,
                        self._max_environment_bytes,
                        "environment export",
                    )
                    if environment_path.exists()
                    else {}
                )
                if _contains_token(document, token) or _contains_token(metadata, token):
                    raise ApifoxAdapterError(
                        "Apifox export contained the access token and was rejected"
                    )
            except ApifoxAdapterError:
                raise
            except (OSError, UnicodeError, ValueError) as error:
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
