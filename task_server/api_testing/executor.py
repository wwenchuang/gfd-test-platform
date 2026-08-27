"""Deterministic HTTP case execution with bounded network and secret handling."""

import copy
from dataclasses import asdict, dataclass, is_dataclass
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import time
from types import MappingProxyType, SimpleNamespace
from typing import Optional
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit

from .assertions import (
    AssertionDefinitionError,
    JsonPathError,
    evaluate_assertions,
    evaluate_business_response,
    extract_values,
)
from .services.case_service import CaseService
from .services.environment_service import EnvironmentService


_SENSITIVE_NAME = re.compile(
    r"(?:authorization|cookie|token|password|passwd|secret|api[-_]?key|"
    r"access[-_]?key[-_]?id|access[-_]?id|policy|signature)",
    re.I,
)
_BEARER = re.compile(r"(?i)(bearer\s+)[^\s,;]+")
_PATH_PARAMETER = re.compile(r"{([A-Za-z_][A-Za-z0-9_.-]*)}")
_REDacted = "***"
_POLL_RETRYABLE_CATEGORIES = frozenset(
    {"product_assertion", "product_response", "parser", "timeout", "transport"}
)


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

    @classmethod
    def from_environment(cls):
        app_env = os.getenv("TASK_APP_ENV", "prod").strip().lower()
        if app_env not in {"test", "dev"}:
            return cls()
        hosts = frozenset(
            item.strip().rstrip(".").lower()
            for item in os.getenv("API_TESTING_TEST_ALLOWED_HOSTS", "").split(",")
            if item.strip()
        )
        return cls(test_only_allowed_hosts=hosts)

    def resolve(self, url):
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise HostPolicyError("only absolute HTTP and HTTPS URLs are allowed")
        if parsed.username is not None or parsed.password is not None:
            raise HostPolicyError("服务地址不允许包含账号密码")
        try:
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
        except ValueError as exc:
            raise HostPolicyError("服务地址端口无效") from exc
        hostname = parsed.hostname.rstrip(".").lower()
        try:
            records = socket.getaddrinfo(hostname, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise HostPolicyError("无法解析服务主机") from exc
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
    timeout_seconds: float = 30.0
    max_response_bytes: int = 5 * 1024 * 1024
    max_redirects: int = 5
    read_chunk_bytes: int = 64 * 1024

    def __post_init__(self):
        if not 0 < self.timeout_seconds <= 60:
            raise ValueError("超时时间必须在 0 到 60 秒之间")
        if not 0 < self.max_response_bytes <= 50 * 1024 * 1024:
            raise ValueError("响应大小限制无效")
        if not 0 <= self.max_redirects <= 10:
            raise ValueError("重定向次数限制无效")


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
class _HttpStepOutcome:
    status: str
    failure_category: str
    request: dict
    response: dict
    assertions: tuple
    extracted: dict
    error: str
    secrets: tuple
    raw_response: Optional[dict] = None


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
        self.host_policy = host_policy or HostPolicy.from_environment()
        self.limits = limits or ExecutorLimits()
        self.cancellation_check = cancellation_check or (lambda _phase: False)
        self.phase_callback = phase_callback

    def execute_case(
        self,
        case_version_id,
        environment_revision_id,
        overrides,
        *,
        dependency_overrides=None,
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
            variables.update(copy.deepcopy(dict(dependency_overrides or {})))
            self._apply_processing(case.processing.get("pre", []), variables)
            setup_steps = tuple(case.processing.get("setup_steps", []))
            cleanup_steps = tuple(case.processing.get("cleanup_steps", []))
            has_workflow = bool(setup_steps or cleanup_steps)
            setup_failure = None
            all_secrets = []

            for index, step in enumerate(setup_steps):
                if not step.get("enabled", True):
                    continue
                missing = self._missing_step_variables(step, variables)
                if missing:
                    self._emit_skipped_step(
                        callback, trace, "setup", index, step, missing
                    )
                    setup_failure = _HttpStepOutcome(
                        "BROKEN",
                        "environment",
                        {},
                        {},
                        (),
                        {},
                        "缺少流程必需变量："
                        + ", ".join(missing),
                        (),
                    )
                    break
                outcome, step_secrets = self._execute_inline_step(
                    "setup",
                    index,
                    step,
                    environment_revision_id,
                    variables,
                    cancel,
                    callback,
                    trace,
                )
                all_secrets.extend(step_secrets)
                if outcome.status != "PASSED":
                    setup_failure = outcome
                    break
                variables.update(copy.deepcopy(outcome.extracted))

            main_outcome = None
            if setup_failure is None:
                main_outcome = self._execute_http_step(
                    case.request,
                    case.assertions,
                    case.extractions,
                    environment_revision_id,
                    variables,
                    cancel,
                    callback,
                    trace,
                    record_main_phases=True,
                )
                all_secrets.extend(main_outcome.secrets)
                variables.update(copy.deepcopy(main_outcome.extracted))
                extracted = copy.deepcopy(main_outcome.extracted)
                self._apply_processing(case.processing.get("post", []), extracted)
                request_view = main_outcome.request
                response_view = main_outcome.response
                assertion_results = main_outcome.assertions
                if has_workflow:
                    self._emit_workflow_step(
                        callback,
                        trace,
                        "main",
                        0,
                        {"name": "主体请求"},
                        main_outcome,
                    )

            cleanup_problem = None
            for index, step in enumerate(cleanup_steps):
                if not step.get("enabled", True):
                    continue
                missing = self._missing_step_variables(step, variables)
                if missing:
                    self._emit_skipped_step(
                        callback, trace, "cleanup", index, step, missing
                    )
                    if cleanup_problem is None:
                        cleanup_problem = _HttpStepOutcome(
                            "FAILED",
                            "cleanup",
                            {},
                            {},
                            (),
                            {},
                            "缺少清理步骤必需变量："
                            + ", ".join(missing),
                            (),
                        )
                    continue
                outcome, step_secrets = self._execute_inline_step(
                    "cleanup",
                    index,
                    step,
                    environment_revision_id,
                    variables,
                    lambda _phase: False,
                    callback,
                    trace,
                )
                all_secrets.extend(step_secrets)
                if outcome.status == "PASSED":
                    variables.update(copy.deepcopy(outcome.extracted))
                if outcome.status != "PASSED" and cleanup_problem is None:
                    cleanup_problem = outcome

            secrets = tuple(dict.fromkeys(all_secrets))
            if setup_failure is not None:
                if setup_failure.status == "CANCELLED":
                    status, category = "CANCELLED", "cancelled"
                else:
                    status = "FAILED" if setup_failure.status == "FAILED" else "BROKEN"
                    category = "setup"
                error = setup_failure.error
                if not request_view:
                    request_view = setup_failure.request
                    response_view = setup_failure.response
            elif main_outcome is not None:
                status = main_outcome.status
                category = main_outcome.failure_category
                error = main_outcome.error
            else:
                status, category, error = "BROKEN", "setup", "主体请求未执行"
            if cleanup_problem is not None and status == "PASSED":
                status, category, error = "FAILED", "cleanup", cleanup_problem.error
            return self._result(
                started,
                status,
                category,
                request_view,
                response_view,
                assertion_results,
                extracted,
                error,
                trace,
                secrets,
            )
        except Exception as exc:
            return self._failure_result(callback, started, "BROKEN", "environment", request_view, response_view, (), {}, str(exc), trace, secrets)

    def preview_setup_steps(
        self,
        environment_revision_id,
        setup_steps,
        target_index,
        *,
        initial_variables=None,
        processing_pre=None,
        extraction_overrides=None,
    ):
        """Execute an enabled setup prefix without persisting an execution."""
        steps = tuple(copy.deepcopy(list(setup_steps or [])))
        if not isinstance(target_index, int) or isinstance(target_index, bool):
            raise ValueError("目标步骤序号必须是整数")
        if target_index < 0 or target_index >= len(steps):
            raise ValueError("目标步骤序号超出前置步骤范围")
        if not steps[target_index].get("enabled", True):
            raise ValueError("目标前置步骤已停用")

        variables = copy.deepcopy(dict(initial_variables or {}))
        self._apply_processing(processing_pre or [], variables)
        overrides = copy.deepcopy(dict(extraction_overrides or {}))
        trace = []
        target_outcome = None
        missing_variables = []
        executed_index = None

        for index, step in enumerate(steps[: target_index + 1]):
            if not step.get("enabled", True):
                continue
            missing = self._missing_step_variables(step, variables)
            if missing:
                missing_variables = list(missing)
                self._emit_skipped_step(
                    None, trace, "setup", index, step, missing
                )
                target_outcome = _HttpStepOutcome(
                    "BROKEN",
                    "missing_variables",
                    {},
                    {},
                    (),
                    {},
                    "缺少流程必需变量："
                    + ", ".join(missing),
                    (),
                )
                break
            outcome, _step_secrets = self._execute_inline_step(
                "setup",
                index,
                step,
                environment_revision_id,
                variables,
                lambda _phase: False,
                None,
                trace,
            )
            executed_index = index
            target_outcome = outcome
            if outcome.status != "PASSED":
                break
            variables.update(copy.deepcopy(outcome.extracted))
            for name in outcome.extracted:
                if name in overrides:
                    variables[name] = copy.deepcopy(overrides[name])

        if target_outcome is None:
            raise ValueError("没有可执行的前置步骤")
        return {
            "status": target_outcome.status,
            "failure_category": target_outcome.failure_category,
            "error_message": redact(target_outcome.error, target_outcome.secrets),
            "trace": copy.deepcopy(trace),
            "response": copy.deepcopy(target_outcome.raw_response or {}),
            "target_index": target_index,
            "executed_index": executed_index,
            "target_reached": executed_index == target_index,
            "available_variables": sorted(variables),
            "missing_variables": missing_variables,
        }

    def _execute_inline_step(
        self,
        stage,
        index,
        step,
        environment_revision_id,
        variables,
        cancel,
        callback,
        trace,
    ):
        polling = step.get("polling") or {}
        max_attempts = int(polling.get("max_attempts", 1))
        interval_ms = int(polling.get("interval_ms", 0))
        all_secrets = []
        outcome = None
        for attempt in range(1, max_attempts + 1):
            outcome = self._execute_http_step(
                step["request"],
                step.get("assertions", []),
                step.get("extractions", []),
                environment_revision_id,
                variables,
                cancel,
                callback,
                trace,
                record_main_phases=False,
            )
            all_secrets.extend(outcome.secrets)
            self._emit_workflow_step(
                callback,
                trace,
                stage,
                index,
                step,
                outcome,
                attempt=attempt,
                max_attempts=max_attempts,
            )
            if (
                outcome.status == "PASSED"
                or outcome.status == "CANCELLED"
                or outcome.failure_category not in _POLL_RETRYABLE_CATEGORIES
                or attempt == max_attempts
            ):
                break
            try:
                self._wait_for_poll(interval_ms, cancel)
            except CancelledExecution:
                outcome = _HttpStepOutcome(
                    "CANCELLED",
                    "cancelled",
                    outcome.request,
                    outcome.response,
                    outcome.assertions,
                    {},
                    "cancelled",
                    tuple(dict.fromkeys(all_secrets)),
                )
                break
        return outcome, tuple(dict.fromkeys(all_secrets))

    def _execute_http_step(
        self,
        request,
        assertions,
        extractions,
        environment_revision_id,
        variables,
        cancel,
        callback,
        trace,
        *,
        record_main_phases,
    ):
        request_view = {}
        response_view = {}
        assertion_results = ()
        extracted = {}
        secrets = ()
        try:
            request = dict(request)
            assertion_views = self._object_views(assertions)
            extraction_views = self._object_views(extractions)
            runtime = self.environment_service.resolve_runtime(
                environment_revision_id,
                variables,
                service_name=request.get("service", "default"),
            )
            assertion_views = tuple(
                self._render_assertion_expected(assertion, runtime)
                for assertion in assertion_views
            )
            secrets = tuple(runtime.secrets.values())
            rendered = {key: runtime.render(value) for key, value in request.items()}
            path = self._render_path(
                rendered["path"], rendered.get("path_params", {})
            )
            base_url = runtime.base_url_for(rendered.get("service", "default"))
            url = urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
            query = rendered.get("query") or {}
            if query:
                parsed = urlsplit(url)
                url = urlunsplit(
                    (
                        parsed.scheme,
                        parsed.netloc,
                        parsed.path,
                        urlencode(query, doseq=True),
                        "",
                    )
                )
            headers = dict(runtime.headers)
            headers.update(
                {
                    key: value
                    for key, value in (rendered.get("headers") or {}).items()
                    if value is not None
                    and (not isinstance(value, str) or value.strip())
                }
            )
            cookies = rendered.get("cookies") or {}
            if cookies:
                headers["Cookie"] = "; ".join(
                    f"{key}={value}" for key, value in cookies.items()
                )
            body = rendered.get("body")
            body_bytes = None
            if body is not None:
                body_bytes = json.dumps(
                    body, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                headers.setdefault("Content-Type", "application/json")
            request_view = redact(
                {
                    "method": rendered["method"],
                    "url": url,
                    "headers": headers,
                    "body": body,
                },
                secrets,
            )
            self._check_cancel("before_request", cancel)
            if record_main_phases:
                self._emit_phase(
                    callback, trace, "request", {"request": request_view}
                )
            response = self._request(rendered["method"], url, headers, body_bytes)
            response_secrets = tuple(
                dict.fromkeys(
                    secrets
                    + discover_sensitive_values(response.json_body)
                    + discover_sensitive_values(response.headers)
                    + discover_sensitive_values(response.cookies, "cookie")
                )
            )
            secrets = response_secrets
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
            raw_response = {
                "status_code": response.status_code,
                "headers": copy.deepcopy(response.headers),
                "cookies": copy.deepcopy(response.cookies),
                "body": copy.deepcopy(
                    response.json_body
                    if response.json_body is not None
                    else response.body_text
                ),
                "duration_ms": response.duration_ms,
                "url": response.url,
            }
            if record_main_phases:
                self._emit_phase(
                    callback, trace, "response", {"response": response_view}
                )
            self._check_cancel("after_response", cancel)
            if extraction_views:
                extracted = extract_values(extraction_views, response)
                if record_main_phases:
                    self._emit_phase(
                        callback,
                        trace,
                        "extraction",
                        {"variables": redact(extracted, response_secrets)},
                    )
            self._check_cancel("before_assertion", cancel)
            if assertion_views:
                raw_assertion_results = list(evaluate_assertions(assertion_views, response))
            else:
                raw_assertion_results = []
            business_result = evaluate_business_response(assertion_views, response)
            if business_result is not None:
                raw_assertion_results.append(business_result)
            assertion_results = tuple(
                redact(asdict(item), response_secrets)
                for item in raw_assertion_results
            )
            if record_main_phases and assertion_results:
                self._emit_phase(
                    callback,
                    trace,
                    "assertion",
                    {
                        "count": len(assertion_results),
                        "results": assertion_results,
                    },
                )
            assertion_failed = any(
                not item["passed"] for item in assertion_results
            )
            has_status_assertion = any(
                item.enabled and item.type == "status_code"
                for item in assertion_views
            )
            if business_result is not None:
                status, category = "FAILED", "business_response"
            elif assertion_failed:
                status, category = "FAILED", "product_assertion"
            elif response.status_code >= 400 and not has_status_assertion:
                status, category = "FAILED", "product_response"
            else:
                status, category = "PASSED", ""
            return _HttpStepOutcome(
                status,
                category,
                request_view,
                response_view,
                assertion_results,
                extracted,
                "",
                response_secrets,
                raw_response,
            )
        except Exception as exc:
            status, category, error = self._exception_details(exc)
            return _HttpStepOutcome(
                status,
                category,
                request_view,
                response_view,
                assertion_results,
                extracted,
                error,
                secrets,
            )

    @staticmethod
    def _object_views(items):
        return tuple(
            item
            if hasattr(item, "type")
            else SimpleNamespace(**copy.deepcopy(dict(item)))
            for item in items
        )

    @staticmethod
    def _render_assertion_expected(assertion, runtime):
        return SimpleNamespace(
            type=assertion.type,
            operator=assertion.operator,
            expected=runtime.render(assertion.expected),
            path=getattr(assertion, "path", None),
            name=getattr(assertion, "name", None),
            timeout_ms=getattr(assertion, "timeout_ms", 0),
            enabled=getattr(assertion, "enabled", True),
            sequence=getattr(assertion, "sequence", 0),
        )

    @staticmethod
    def _missing_step_variables(step, variables):
        return sorted(
            name
            for name in step.get("required_variables", [])
            if name not in variables
        )

    @classmethod
    def _emit_workflow_step(
        cls,
        callback,
        trace,
        stage,
        index,
        step,
        outcome,
        *,
        attempt=1,
        max_attempts=1,
    ):
        attempt_details = (
            {"attempt": attempt, "max_attempts": max_attempts}
            if max_attempts > 1
            else {}
        )
        cls._emit_phase(
            callback,
            trace,
            "workflow_step",
            {
                "stage": stage,
                "index": index,
                "name": step.get("name", "主体请求"),
                "status": outcome.status,
                "failure_category": outcome.failure_category,
                "request": copy.deepcopy(outcome.request),
                "response": copy.deepcopy(outcome.response),
                "assertions": copy.deepcopy(list(outcome.assertions)),
                "extracted_variables": redact(
                    outcome.extracted, outcome.secrets
                ),
                "error_message": redact(outcome.error, outcome.secrets),
                **attempt_details,
            },
        )

    @classmethod
    def _emit_skipped_step(cls, callback, trace, stage, index, step, missing):
        cls._emit_phase(
            callback,
            trace,
            "workflow_step",
            {
                "stage": stage,
                "index": index,
                "name": step.get("name", "未命名步骤"),
                "status": "SKIPPED",
                "failure_category": "missing_variables",
                "missing_variables": list(missing),
                "request": {},
                "response": {},
                "assertions": [],
                "extracted_variables": {},
                "error_message": "缺少流程必需变量",
            },
        )

    @staticmethod
    def _exception_details(exc):
        if isinstance(exc, CancelledExecution):
            return "CANCELLED", "cancelled", "执行已取消"
        if isinstance(exc, HostPolicyError):
            return "BROKEN", "host_policy", str(exc)
        if isinstance(exc, RedirectLimitError):
            return "BROKEN", "redirect_limit", str(exc)
        if isinstance(exc, ResponseLimitError):
            return "BROKEN", "response_limit", str(exc)
        if isinstance(exc, (socket.timeout, RequestTimeoutError)):
            return "BROKEN", "timeout", str(exc) or "请求超时"
        if isinstance(exc, (ConnectionError, http.client.HTTPException, OSError)):
            return "BROKEN", "transport", str(exc)
        if isinstance(exc, (JsonPathError, json.JSONDecodeError)):
            return "BROKEN", "parser", str(exc)
        if isinstance(exc, AssertionDefinitionError):
            return "BROKEN", "assertion_definition", str(exc)
        return "BROKEN", "environment", str(exc)

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
                raise connection_error or ConnectionError("无法连接服务主机")
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
                transport_socket = self._response_socket(raw, connection)
                self._remaining(deadline)
                if raw.status in {301, 302, 303, 307, 308}:
                    location = raw.getheader("Location")
                    raw.read(0)
                    if not location:
                        raise http.client.HTTPException("重定向响应缺少 Location 请求头")
                    if redirect_count >= self.limits.max_redirects:
                        raise RedirectLimitError("重定向次数超过限制")
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
                    if raw.isclosed():
                        break
                    transport_socket.settimeout(self._remaining(deadline))
                    chunk = raw.read1(self.limits.read_chunk_bytes)
                    self._remaining(deadline)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > self.limits.max_response_bytes:
                        raise ResponseLimitError("响应体大小超过配置限制")
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
        raise RedirectLimitError("重定向次数超过限制")

    @staticmethod
    def _remaining(deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RequestTimeoutError("请求超时")
        return remaining

    @staticmethod
    def _response_socket(response, connection):
        stream = getattr(response, "fp", None)
        buffered = getattr(stream, "raw", None)
        response_socket = getattr(buffered, "_sock", None)
        if response_socket is not None and response_socket.fileno() >= 0:
            return response_socket
        if connection.sock is not None and connection.sock.fileno() >= 0:
            return connection.sock
        raise ConnectionError("响应传输通道不可用")

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

    @classmethod
    def _wait_for_poll(cls, interval_ms, callback):
        deadline = time.monotonic() + (interval_ms / 1000)
        while True:
            cls._check_cancel("poll_wait", callback)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.1))

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
                raise ValueError("不支持该数据处理动作")

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
