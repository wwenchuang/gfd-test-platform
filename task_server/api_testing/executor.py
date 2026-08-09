"""Deterministic HTTP case execution with bounded network and secret handling."""

import copy
from dataclasses import asdict, dataclass, is_dataclass
import http.client
import ipaddress
import json
import re
import socket
import ssl
import time
from types import MappingProxyType
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

from .assertions import (
    AssertionDefinitionError,
    JsonPathError,
    evaluate_assertions,
    extract_values,
)
from .services.case_service import CaseService
from .services.environment_service import EnvironmentService


_SENSITIVE_NAME = re.compile(r"(?:authorization|cookie|token|password|passwd|secret|api[-_]?key)", re.I)
_BEARER = re.compile(r"(?i)(bearer\s+)[^\s,;]+")
_PATH_PARAMETER = re.compile(r"{([A-Za-z_][A-Za-z0-9_.-]*)}")
_REDacted = "***"


class HostPolicyError(ValueError):
    pass


class ResponseLimitError(ValueError):
    pass


class RedirectLimitError(ValueError):
    pass


class RequestTimeoutError(TimeoutError):
    pass


class CancelledExecution(Exception):
    pass


@dataclass(frozen=True)
class HostPolicy:
    test_only_allowed_hosts: frozenset = frozenset()

    def resolve(self, url):
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise HostPolicyError("only absolute HTTP and HTTPS URLs are allowed")
        if parsed.username is not None or parsed.password is not None:
            raise HostPolicyError("URL credentials are not allowed")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise HostPolicyError("URL port is invalid") from exc
        hostname = parsed.hostname.rstrip(".").lower()
        try:
            records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise HostPolicyError("host could not be resolved") from exc
        addresses = []
        for record in records:
            address = record[4][0]
            ip = ipaddress.ip_address(address)
            if hostname not in self.test_only_allowed_hosts and not ip.is_global:
                raise HostPolicyError("host resolves to a non-public address")
            addresses.append(address)
        if not addresses:
            raise HostPolicyError("host has no usable address")
        return parsed, port, tuple(dict.fromkeys(addresses))


@dataclass(frozen=True)
class ExecutorLimits:
    timeout_seconds: float = 10.0
    max_response_bytes: int = 5 * 1024 * 1024
    max_redirects: int = 5
    read_chunk_bytes: int = 64 * 1024

    def __post_init__(self):
        if not 0 < self.timeout_seconds <= 60:
            raise ValueError("timeout must be between 0 and 60 seconds")
        if not 0 < self.max_response_bytes <= 50 * 1024 * 1024:
            raise ValueError("response limit is invalid")
        if not 0 <= self.max_redirects <= 10:
            raise ValueError("redirect limit is invalid")


@dataclass(frozen=True)
class HttpResponseData:
    status_code: int
    headers: dict
    cookies: dict
    body_text: str
    json_body: object
    duration_ms: int
    url: str


@dataclass(frozen=True, repr=False)
class CaseExecutionResult:
    status: str
    failure_category: str
    duration_ms: int
    sanitized_request: dict
    sanitized_response: dict
    assertion_results: tuple
    extracted_variables: dict
    error_message: str
    trace: tuple

    def __repr__(self):
        return "CaseExecutionResult(status=%r, failure_category=%r, duration_ms=%r)" % (
            self.status,
            self.failure_category,
            self.duration_ms,
        )

    __str__ = __repr__

    def to_dict(self):
        return {
            "status": self.status,
            "failure_category": self.failure_category,
            "duration_ms": self.duration_ms,
            "request": copy.deepcopy(self.sanitized_request),
            "response": copy.deepcopy(self.sanitized_response),
            "assertions": [asdict(item) if is_dataclass(item) else copy.deepcopy(item) for item in self.assertion_results],
            "extracted_variables": copy.deepcopy(self.extracted_variables),
            "error_message": self.error_message,
            "trace": copy.deepcopy(list(self.trace)),
        }


def discover_sensitive_values(value, key_name="", *, _depth=0, _found=None):
    if _found is None:
        _found = set()
    if _depth > 32 or len(_found) >= 1000:
        return tuple(_found)
    sensitive = bool(_SENSITIVE_NAME.search(str(key_name)))
    if isinstance(value, dict):
        for key, item in value.items():
            if sensitive:
                discover_sensitive_values(
                    item, "secret", _depth=_depth + 1, _found=_found
                )
            discover_sensitive_values(
                item, str(key), _depth=_depth + 1, _found=_found
            )
    elif isinstance(value, (list, tuple)):
        for item in value:
            discover_sensitive_values(
                item, key_name, _depth=_depth + 1, _found=_found
            )
    elif sensitive and isinstance(value, str) and value:
        _found.add(value)
    return tuple(_found)


def redact(value, secret_values=(), key_name=""):
    secrets = tuple(item for item in secret_values if isinstance(item, str) and item)
    if _SENSITIVE_NAME.search(str(key_name)):
        return _REDacted
    if isinstance(value, dict):
        return {str(key): redact(item, secrets, str(key)) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item, secrets) for item in value]
    if isinstance(value, str):
        sanitized = _BEARER.sub(r"\1***", value)
        for secret in sorted(secrets, key=len, reverse=True):
            sanitized = sanitized.replace(secret, _REDacted)
        return sanitized
    return copy.deepcopy(value)


class _PinnedHttpConnection(http.client.HTTPConnection):
    def __init__(self, hostname, port, address, timeout):
        super().__init__(hostname, port, timeout=timeout)
        self._address = address

    def connect(self):
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _PinnedHttpsConnection(http.client.HTTPSConnection):
    def __init__(self, hostname, port, address, timeout):
        super().__init__(hostname, port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self):
        raw = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class HttpExecutor:
    def __init__(
        self,
        case_service,
        environment_service,
        *,
        host_policy=None,
        limits=None,
        cancellation_check=None,
        phase_callback=None,
    ):
        self.case_service = case_service
        self.environment_service = environment_service
        self.host_policy = host_policy or HostPolicy()
        self.limits = limits or ExecutorLimits()
        self.cancellation_check = cancellation_check or (lambda _phase: False)
        self.phase_callback = phase_callback

    def execute_case(
        self,
        case_version_id,
        environment_revision_id,
        overrides,
        *,
        cancellation_check=None,
        phase_callback=None,
    ):
        started = time.monotonic()
        cancel = cancellation_check or self.cancellation_check
        callback = phase_callback or self.phase_callback
        trace = []
        request_view = {}
        response_view = {}
        assertion_results = ()
        extracted = {}
        secrets = ()
        try:
            case = self.case_service.get_version(case_version_id)
            row_values = {}
            for row in case.data_rows:
                if row.enabled:
                    row_values.update(dict(row.values))
                    break
            variables = copy.deepcopy(dict(overrides or {}))
            variables.update(row_values)
            self._apply_processing(case.processing.get("pre", []), variables)
            runtime = self.environment_service.resolve_runtime(
                environment_revision_id, variables, service_name=case.request.get("service", "default")
            )
            secrets = tuple(runtime.secrets.values())
            rendered = {
                key: runtime.render(value) for key, value in dict(case.request).items()
            }
            path = self._render_path(rendered["path"], rendered.get("path_params", {}))
            base_url = runtime.base_url_for(rendered.get("service", "default"))
            url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
            query = rendered.get("query") or {}
            if query:
                parsed = urlsplit(url)
                url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(query, doseq=True), ""))
            headers = dict(runtime.headers)
            headers.update(rendered.get("headers") or {})
            cookies = rendered.get("cookies") or {}
            if cookies:
                headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in cookies.items())
            body = rendered.get("body")
            body_bytes = None
            if body is not None:
                body_bytes = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
            request_view = redact(
                {"method": rendered["method"], "url": url, "headers": headers, "body": body},
                secrets,
            )
            self._check_cancel("before_request", cancel)
            self._emit_phase(callback, trace, "request", {"request": request_view})
            response = self._request(rendered["method"], url, headers, body_bytes)
            response_secrets = tuple(
                dict.fromkeys(
                    secrets
                    + discover_sensitive_values(response.json_body)
                    + discover_sensitive_values(response.headers)
                    + discover_sensitive_values(response.cookies, "cookie")
                )
            )
            sanitized_body = (
                json.dumps(
                    redact(response.json_body, response_secrets),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                if response.json_body is not None
                else redact(response.body_text, response_secrets)
            )
            response_view = redact(
                {
                    "status_code": response.status_code,
                    "headers": response.headers,
                    "body": sanitized_body,
                    "duration_ms": response.duration_ms,
                    "url": response.url,
                },
                response_secrets,
            )
            self._emit_phase(
                callback, trace, "response", {"response": response_view}
            )
            self._check_cancel("after_response", cancel)
            if case.extractions:
                extracted = redact(
                    extract_values(case.extractions, response), response_secrets
                )
                self._emit_phase(
                    callback,
                    trace,
                    "extraction",
                    {"variables": extracted},
                )
            self._check_cancel("before_assertion", cancel)
            if case.assertions:
                raw_assertion_results = evaluate_assertions(case.assertions, response)
                assertion_results = tuple(
                    redact(asdict(item), response_secrets)
                    for item in raw_assertion_results
                )
                self._emit_phase(
                    callback,
                    trace,
                    "assertion",
                    {
                        "count": len(assertion_results),
                        "results": assertion_results,
                    },
                )
            self._apply_processing(case.processing.get("post", []), extracted)
            assertion_failed = any(
                not item["passed"] for item in assertion_results
            )
            has_status_assertion = any(
                item.enabled and item.type == "status_code"
                for item in case.assertions
            )
            if assertion_failed:
                status, category = "FAILED", "product_assertion"
            elif response.status_code >= 400 and not has_status_assertion:
                status, category = "FAILED", "product_response"
            else:
                status, category = "PASSED", ""
            return self._result(started, status, category, request_view, response_view, assertion_results, extracted, "", trace, response_secrets)
        except CancelledExecution:
            return self._failure_result(callback, started, "CANCELLED", "cancelled", request_view, response_view, assertion_results, extracted, "cancelled", trace, secrets)
        except HostPolicyError as exc:
            return self._failure_result(callback, started, "BROKEN", "host_policy", request_view, response_view, (), {}, str(exc), trace, secrets)
        except RedirectLimitError as exc:
            return self._failure_result(callback, started, "BROKEN", "redirect_limit", request_view, response_view, (), {}, str(exc), trace, secrets)
        except ResponseLimitError as exc:
            return self._failure_result(callback, started, "BROKEN", "response_limit", request_view, response_view, (), {}, str(exc), trace, secrets)
        except (socket.timeout, RequestTimeoutError) as exc:
            return self._failure_result(callback, started, "BROKEN", "timeout", request_view, response_view, (), {}, str(exc) or "request deadline exceeded", trace, secrets)
        except (ConnectionError, http.client.HTTPException, OSError) as exc:
            return self._failure_result(callback, started, "BROKEN", "transport", request_view, response_view, (), {}, str(exc), trace, secrets)
        except (JsonPathError, json.JSONDecodeError) as exc:
            return self._failure_result(callback, started, "BROKEN", "parser", request_view, response_view, (), {}, str(exc), trace, secrets)
        except AssertionDefinitionError as exc:
            return self._failure_result(callback, started, "BROKEN", "assertion_definition", request_view, response_view, (), {}, str(exc), trace, secrets)
        except Exception as exc:
            return self._failure_result(callback, started, "BROKEN", "environment", request_view, response_view, (), {}, str(exc), trace, secrets)

    def _request(self, method, initial_url, headers, body):
        network_started = time.monotonic()
        deadline = network_started + self.limits.timeout_seconds
        url = initial_url
        headers = dict(headers)
        for redirect_count in range(self.limits.max_redirects + 1):
            self._remaining(deadline)
            parsed, port, addresses = self.host_policy.resolve(url)
            self._remaining(deadline)
            connection_class = _PinnedHttpsConnection if parsed.scheme == "https" else _PinnedHttpConnection
            connection = None
            connection_error = None
            for address in addresses:
                candidate = connection_class(
                    parsed.hostname, port, address, self._remaining(deadline)
                )
                try:
                    candidate.connect()
                    self._remaining(deadline)
                    connection = candidate
                    break
                except OSError as exc:
                    connection_error = exc
                    candidate.close()
            if connection is None:
                raise connection_error or ConnectionError("host could not be reached")
            transport_socket = connection.sock
            path = urlunsplit(("", "", parsed.path or "/", parsed.query, ""))
            request_headers = dict(headers)
            request_headers.setdefault("Host", parsed.netloc)
            started = time.monotonic()
            try:
                transport_socket.settimeout(self._remaining(deadline))
                connection.request(method, path, body=body, headers=request_headers)
                self._remaining(deadline)
                transport_socket.settimeout(self._remaining(deadline))
                raw = connection.getresponse()
                self._remaining(deadline)
                if raw.status in {301, 302, 303, 307, 308}:
                    location = raw.getheader("Location")
                    raw.read(0)
                    if not location:
                        raise http.client.HTTPException("redirect is missing Location")
                    if redirect_count >= self.limits.max_redirects:
                        raise RedirectLimitError("redirect limit exceeded")
                    redirected_url = urljoin(url, location)
                    if self._origin(url) != self._origin(redirected_url):
                        headers = {
                            name: value
                            for name, value in headers.items()
                            if not _SENSITIVE_NAME.search(name)
                        }
                    url = redirected_url
                    if raw.status == 303:
                        method, body = "GET", None
                    continue
                chunks = []
                size = 0
                while True:
                    transport_socket.settimeout(self._remaining(deadline))
                    chunk = raw.read1(self.limits.read_chunk_bytes)
                    self._remaining(deadline)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.limits.max_response_bytes:
                        raise ResponseLimitError("response body exceeds configured limit")
                    chunks.append(chunk)
                raw_body = b"".join(chunks)
                encoding = "utf-8"
                text = raw_body.decode(encoding, errors="replace")
                content_type = raw.getheader("Content-Type", "")
                parsed_json = None
                if "json" in content_type.lower() or text.lstrip().startswith(("{", "[")):
                    try:
                        parsed_json = json.loads(text)
                    except json.JSONDecodeError:
                        parsed_json = None
                headers_map = {name: value for name, value in raw.getheaders()}
                cookies = {}
                for header_name, header_value in raw.getheaders():
                    if header_name.lower() == "set-cookie":
                        pair = header_value.split(";", 1)[0]
                        if "=" in pair:
                            name, value = pair.split("=", 1)
                            cookies[name] = value
                return HttpResponseData(
                    status_code=raw.status,
                    headers=headers_map,
                    cookies=cookies,
                    body_text=text,
                    json_body=parsed_json,
                    duration_ms=max(
                        0, int((time.monotonic() - network_started) * 1000)
                    ),
                    url=url,
                )
            finally:
                connection.close()
        raise RedirectLimitError("redirect limit exceeded")

    @staticmethod
    def _remaining(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RequestTimeoutError("request deadline exceeded")
        return remaining

    @staticmethod
    def _origin(url):
        parsed = urlsplit(url)
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError:
            port = None
        return parsed.scheme.lower(), (parsed.hostname or "").lower(), port

    @staticmethod
    def _check_cancel(phase, callback):
        if callback(phase):
            raise CancelledExecution()

    @staticmethod
    def _emit_phase(callback, trace, phase, payload):
        event = {"phase": phase, **copy.deepcopy(payload)}
        trace.append(event)
        if callback is not None:
            callback(phase, copy.deepcopy(payload))

    @staticmethod
    def _render_path(path, path_params):
        missing = []

        def replace(match):
            name = match.group(1)
            if name not in path_params:
                missing.append(name)
                return match.group(0)
            from urllib.parse import quote

            return quote(str(path_params[name]), safe="")

        rendered = _PATH_PARAMETER.sub(replace, path)
        if missing or "{" in rendered or "}" in rendered:
            raise ValueError("request path has unresolved parameters")
        return rendered

    @staticmethod
    def _apply_processing(actions, variables):
        for item in actions:
            action = item["action"]
            if action == "set_variable":
                variables[item["name"]] = copy.deepcopy(item["value"])
            elif action == "copy_variable":
                variables[item["target"]] = copy.deepcopy(variables[item["source"]])
            elif action == "remove_variable":
                variables.pop(item["name"], None)
            elif action == "json_encode":
                variables[item["target"]] = json.dumps(variables[item["source"]], ensure_ascii=False)
            elif action == "json_decode":
                variables[item["target"]] = json.loads(variables[item["source"]])
            else:
                raise ValueError("processing action is not supported")

    @staticmethod
    def _result(started, status, category, request, response, assertions, extracted, error, trace, secrets):
        return CaseExecutionResult(
            status=status,
            failure_category=category,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            sanitized_request=redact(request, secrets),
            sanitized_response=redact(response, secrets),
            assertion_results=tuple(assertions),
            extracted_variables=redact(extracted, secrets),
            error_message=redact(str(error), secrets),
            trace=tuple(redact(trace, secrets)),
        )

    @classmethod
    def _failure_result(
        cls,
        callback,
        started,
        status,
        category,
        request,
        response,
        assertions,
        extracted,
        error,
        trace,
        secrets,
    ):
        result = cls._result(
            started,
            status,
            category,
            request,
            response,
            assertions,
            extracted,
            error,
            trace,
            secrets,
        )
        if callback is not None:
            try:
                callback(
                    "failure",
                    {
                        "status": status,
                        "failure_category": category,
                        "error_message": result.error_message,
                    },
                )
            except Exception:
                pass
        return result


def execute_case(case_version_id, environment_revision_id, overrides):
    """Default synchronous entry point used by the Celery task."""
    from .db import _session_factory

    factory = _session_factory()
    return HttpExecutor(
        CaseService(factory), EnvironmentService(factory)
    ).execute_case(case_version_id, environment_revision_id, overrides)
