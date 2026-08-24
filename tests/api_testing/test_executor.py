import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from task_server.api_testing.executor import (
    ExecutorLimits,
    HostPolicy,
    HttpExecutor,
)


class _TargetHandler(BaseHTTPRequestHandler):
    request_count = 0
    port = 0
    captured_authorization = None
    captured_cookie = None
    workflow_calls = []

    def log_message(self, *_args):
        return

    def _send(self, status, payload, content_type="application/json"):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        type(self).request_count += 1
        type(self).workflow_calls.append(("GET", self.path, None))
        if self.path.startswith("/workflow/setup-fail"):
            self._send(200, {"code": 5001, "message": "no resource"})
        elif self.path.startswith("/workflow/setup"):
            self._send(200, {"code": 0, "data": {"resourceSn": "resource-1"}})
        elif self.path.startswith("/workflow/static-cleanup"):
            self._send(200, {"code": 0})
        elif self.path.startswith("/ok"):
            self._send(200, {"code": 0, "data": [{"id": "favorite-1"}]})
        elif self.path.startswith("/business-fail"):
            self._send(200, {"code": 4009, "message": "not logged in"})
        elif self.path.startswith("/status-500"):
            self._send(500, {"message": "server error"})
        elif self.path.startswith("/text"):
            self._send(200, b"plain response", "text/plain")
        elif self.path.startswith("/large"):
            self._send(200, b"x" * 4096, "text/plain")
        elif self.path.startswith("/slow"):
            time.sleep(0.2)
            self._send(200, {"code": 0})
        elif self.path.startswith("/drip"):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", "4")
            self.end_headers()
            for value in b"slow":
                try:
                    self.wfile.write(bytes([value]))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
                time.sleep(0.06)
        elif self.path.startswith("/token"):
            self._send(
                200,
                {
                    "data": {
                        "access_token": "server-issued-review-secret",
                        "profile": {
                            "password": "server-password-secret",
                            "apiKey": "server-api-key-secret",
                        },
                    }
                },
            )
        elif self.path.startswith("/disconnect"):
            self.connection.shutdown(2)
            self.connection.close()
        elif self.path.startswith("/redirect-private"):
            self.send_response(302)
            self.send_header("Location", "http://169.254.169.254/latest/meta-data")
            self.end_headers()
        elif self.path.startswith("/redirect-loop"):
            self.send_response(302)
            self.send_header("Location", "/redirect-loop")
            self.end_headers()
        elif self.path.startswith("/redirect-slow/"):
            step = int(self.path.rsplit("/", 1)[-1])
            time.sleep(0.04)
            if step < 3:
                self.send_response(302)
                self.send_header("Location", f"/redirect-slow/{step + 1}")
                self.end_headers()
            else:
                self._send(200, {"code": 0})
        elif self.path.startswith("/redirect-cross-origin"):
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{type(self).port}/capture")
            self.end_headers()
        elif self.path.startswith("/capture"):
            type(self).captured_authorization = self.headers.get("Authorization")
            type(self).captured_cookie = self.headers.get("Cookie")
            self._send(200, {"code": 0})
        else:
            self._send(404, {"message": "missing"})

    def do_POST(self):
        type(self).request_count += 1
        length = int(self.headers.get("Content-Length") or 0)
        raw_body = self.rfile.read(length) if length else b""
        body = json.loads(raw_body) if raw_body else None
        type(self).workflow_calls.append(("POST", self.path, body))
        if self.path.startswith("/workflow/print-fail"):
            self._send(
                200,
                {
                    "code": 5002,
                    "message": "print rejected",
                    "data": {"printTaskSn": "print-task-failed"},
                },
            )
        elif self.path.startswith("/workflow/print"):
            self._send(
                200,
                {"code": 0, "data": {"printTaskSn": "print-task-1"}},
            )
        elif self.path.startswith("/workflow/cancel-fail"):
            self._send(200, {"code": 5003, "message": "cancel rejected"})
        elif self.path.startswith("/workflow/cancel"):
            self._send(200, {"code": 0, "data": {"cancelled": True}})
        else:
            self._send(404, {"message": "missing"})


@pytest.fixture(scope="module")
def target_server():
    _TargetHandler.request_count = 0
    server = ThreadingHTTPServer(("127.0.0.1", 0), _TargetHandler)
    _TargetHandler.port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", _TargetHandler
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def _case(
    path="/ok",
    assertions=None,
    extractions=None,
    headers=None,
    data_rows=None,
    *,
    method="GET",
    body=None,
    processing=None,
):
    return SimpleNamespace(
        id="case-version-1",
        endpoint_id="endpoint-1",
        project_id="project-1",
        request={
            "method": method,
            "path": path,
            "service": "default",
            "path_params": {},
            "query": {},
            "headers": headers or {},
            "cookies": {},
            "body": body,
        },
        data_rows=tuple(data_rows or ()),
        assertions=tuple(assertions or []),
        extractions=tuple(extractions or []),
        processing=processing or {"pre": [], "post": []},
    )


class _CaseService:
    def __init__(self, case):
        self.case = case

    def get_version(self, _case_version_id):
        return self.case


class _Runtime:
    def __init__(self, base_url, values):
        self.base_url = base_url
        self.values = values
        self.secrets = {k: v for k, v in values.items() if "token" in k.lower()}
        self.headers = {"Authorization": f"Bearer {values['token']}"} if "token" in values else {}

    def base_url_for(self, _service):
        return self.base_url

    def render(self, value):
        if isinstance(value, str):
            import re

            def replace(match):
                name = match.group(1)
                if name not in self.values:
                    raise ValueError(f"undefined environment variable: {name}")
                return str(self.values[name])

            rendered = re.sub(r"{{([A-Za-z_][A-Za-z0-9_.-]*)}}", replace, value)
            if "{{" in rendered or "}}" in rendered:
                raise ValueError("invalid environment placeholder syntax")
            return rendered
        if isinstance(value, dict):
            return {key: self.render(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.render(item) for item in value]
        return value


class _EnvironmentService:
    def __init__(self, base_url, values=None):
        self.base_url = base_url
        self.values = values or {}

    def resolve_runtime(self, _revision_id, overrides, service_name=None):
        values = dict(self.values)
        values.update(overrides)
        return _Runtime(self.base_url, values)


def _executor(target_server, case, *, values=None, limits=None, cancel=None):
    base_url, _ = target_server
    return HttpExecutor(
        _CaseService(case),
        _EnvironmentService(base_url, values),
        host_policy=HostPolicy(test_only_allowed_hosts=frozenset({"127.0.0.1"})),
        limits=limits or ExecutorLimits(timeout_seconds=1, max_response_bytes=2048),
        cancellation_check=cancel,
    )


def _assertion(assertion_type, operator, expected=None, path=None, name=None):
    return SimpleNamespace(
        type=assertion_type,
        operator=operator,
        expected=expected,
        path=path,
        name=name,
        timeout_ms=0,
        enabled=True,
        sequence=0,
    )


def _extraction(target, extraction_type, path=None, name=None, required=True):
    return SimpleNamespace(
        target=target,
        type=extraction_type,
        path=path,
        name=name,
        required=required,
        default=None,
    )


def _workflow_step(
    name,
    method,
    path,
    *,
    body=None,
    assertions=None,
    extractions=None,
    required_variables=None,
):
    return {
        "name": name,
        "enabled": True,
        "request": {
            "method": method,
            "path": path,
            "service": "default",
            "path_params": {},
            "query": {},
            "headers": {},
            "cookies": {},
            "body": body,
        },
        "assertions": assertions or [],
        "extractions": extractions or [],
        "required_variables": required_variables or [],
    }


def _workflow_processing(*, setup_steps=None, cleanup_steps=None):
    return {
        "pre": [],
        "post": [],
        "setup_steps": setup_steps or [],
        "cleanup_steps": cleanup_steps or [],
    }


def _code_assertion(expected=0):
    return {
        "type": "json_path",
        "operator": "equals",
        "path": "$.code",
        "expected": expected,
        "timeout_ms": 0,
        "enabled": True,
    }


def _json_extraction(target, path):
    return {
        "target": target,
        "type": "json_path",
        "path": path,
        "required": True,
    }


def test_inline_setup_feeds_print_request_and_print_result_feeds_cancel_cleanup(
    target_server,
):
    _, handler = target_server
    handler.workflow_calls = []
    case = _case(
        "/workflow/print",
        method="POST",
        body={"resourceSn": "{{resourceSn}}"},
        assertions=[_assertion("json_path", "equals", 0, "$.code")],
        extractions=[
            _extraction("printTaskSn", "json_path", "$.data.printTaskSn")
        ],
        processing=_workflow_processing(
            setup_steps=[
                _workflow_step(
                    "查询可打印资源",
                    "GET",
                    "/workflow/setup",
                    assertions=[_code_assertion()],
                    extractions=[
                        _json_extraction("resourceSn", "$.data.resourceSn")
                    ],
                )
            ],
            cleanup_steps=[
                _workflow_step(
                    "取消本次打印",
                    "POST",
                    "/workflow/cancel",
                    body={"printTaskSn": "{{printTaskSn}}"},
                    assertions=[_code_assertion()],
                    required_variables=["printTaskSn"],
                )
            ],
        ),
    )

    result = _executor(target_server, case).execute_case(
        "case-version-1", "environment-revision-1", {}
    )

    assert result.status == "PASSED"
    assert [call[1] for call in handler.workflow_calls] == [
        "/workflow/setup",
        "/workflow/print",
        "/workflow/cancel",
    ]
    assert handler.workflow_calls[1][2] == {"resourceSn": "resource-1"}
    assert handler.workflow_calls[2][2] == {"printTaskSn": "print-task-1"}
    workflow = [event for event in result.trace if event["phase"] == "workflow_step"]
    assert [(item["stage"], item["status"]) for item in workflow] == [
        ("setup", "PASSED"),
        ("main", "PASSED"),
        ("cleanup", "PASSED"),
    ]


def test_main_assertion_failure_still_cancels_print(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    case = _case(
        "/workflow/print-fail",
        method="POST",
        body={"resourceSn": "{{resourceSn}}"},
        assertions=[_assertion("json_path", "equals", 0, "$.code")],
        extractions=[
            _extraction("printTaskSn", "json_path", "$.data.printTaskSn")
        ],
        processing=_workflow_processing(
            setup_steps=[
                _workflow_step(
                    "查询可打印资源",
                    "GET",
                    "/workflow/setup",
                    assertions=[_code_assertion()],
                    extractions=[
                        _json_extraction("resourceSn", "$.data.resourceSn")
                    ],
                )
            ],
            cleanup_steps=[
                _workflow_step(
                    "取消本次打印",
                    "POST",
                    "/workflow/cancel",
                    body={"printTaskSn": "{{printTaskSn}}"},
                    assertions=[_code_assertion()],
                    required_variables=["printTaskSn"],
                )
            ],
        ),
    )

    result = _executor(target_server, case).execute_case(
        "case-version-1", "environment-revision-1", {}
    )

    assert result.status == "FAILED"
    assert result.failure_category == "product_assertion"
    assert handler.workflow_calls[-1][1] == "/workflow/cancel"
    assert handler.workflow_calls[-1][2] == {"printTaskSn": "print-task-failed"}


def test_setup_failure_blocks_main_but_runs_static_cleanup(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    case = _case(
        "/workflow/print",
        method="POST",
        processing=_workflow_processing(
            setup_steps=[
                _workflow_step(
                    "查询可打印资源",
                    "GET",
                    "/workflow/setup-fail",
                    assertions=[_code_assertion()],
                )
            ],
            cleanup_steps=[
                _workflow_step(
                    "静态清理",
                    "GET",
                    "/workflow/static-cleanup",
                    assertions=[_code_assertion()],
                )
            ],
        ),
    )

    result = _executor(target_server, case).execute_case(
        "case-version-1", "environment-revision-1", {}
    )

    assert result.status == "FAILED"
    assert result.failure_category == "setup"
    assert [call[1] for call in handler.workflow_calls] == [
        "/workflow/setup-fail",
        "/workflow/static-cleanup",
    ]


def test_cleanup_failure_prevents_otherwise_passing_case(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    case = _case(
        "/workflow/print",
        method="POST",
        assertions=[_assertion("json_path", "equals", 0, "$.code")],
        extractions=[
            _extraction("printTaskSn", "json_path", "$.data.printTaskSn")
        ],
        processing=_workflow_processing(
            cleanup_steps=[
                _workflow_step(
                    "取消本次打印",
                    "POST",
                    "/workflow/cancel-fail",
                    body={"printTaskSn": "{{printTaskSn}}"},
                    assertions=[_code_assertion()],
                    required_variables=["printTaskSn"],
                )
            ]
        ),
    )

    result = _executor(target_server, case).execute_case(
        "case-version-1", "environment-revision-1", {}
    )

    assert result.status == "FAILED"
    assert result.failure_category == "cleanup"
    assert handler.workflow_calls[-1][1] == "/workflow/cancel-fail"


def test_missing_cleanup_variable_skips_request_and_blocks_a_passing_case(
    target_server,
):
    _, handler = target_server
    handler.workflow_calls = []
    case = _case(
        processing=_workflow_processing(
            cleanup_steps=[
                _workflow_step(
                    "取消本次打印",
                    "POST",
                    "/workflow/cancel",
                    body={"printTaskSn": "{{printTaskSn}}"},
                    assertions=[_code_assertion()],
                    required_variables=["printTaskSn"],
                )
            ]
        )
    )

    result = _executor(target_server, case).execute_case(
        "case-version-1", "environment-revision-1", {}
    )

    assert result.status == "FAILED"
    assert result.failure_category == "cleanup"
    assert all(call[1] != "/workflow/cancel" for call in handler.workflow_calls)
    cleanup = [
        event
        for event in result.trace
        if event.get("phase") == "workflow_step"
        and event.get("stage") == "cleanup"
    ][0]
    assert cleanup["status"] == "SKIPPED"
    assert cleanup["missing_variables"] == ["printTaskSn"]


def test_success_and_truthful_product_statuses(target_server):
    passed = _executor(
        target_server,
        _case(assertions=[_assertion("json_path", "equals", 0, "$.code")]),
    ).execute_case("case-version-1", "environment-revision-1", {})
    assertion_failed = _executor(
        target_server,
        _case(
            "/business-fail",
            assertions=[_assertion("json_path", "equals", 0, "$.code")],
        ),
    ).execute_case("case-version-1", "environment-revision-1", {})
    server_failed = _executor(target_server, _case("/status-500")).execute_case(
        "case-version-1", "environment-revision-1", {}
    )

    assert passed.status == "PASSED"
    assert assertion_failed.status == "FAILED"
    assert assertion_failed.failure_category == "product_assertion"
    assert server_failed.status == "FAILED"
    assert server_failed.failure_category == "product_response"


def test_dependency_overrides_take_precedence_over_static_data_rows(target_server):
    case = _case(
        "/{{resourceSn}}",
        data_rows=[SimpleNamespace(enabled=True, values={"resourceSn": "placeholder"})],
    )

    result = _executor(target_server, case).execute_case(
        "case-version-1",
        "environment-revision-1",
        {},
        dependency_overrides={"resourceSn": "ok"},
    )

    assert result.status == "PASSED"
    assert result.sanitized_request["url"].endswith("/ok")


def test_missing_variable_is_broken_before_network(target_server):
    _, handler = target_server
    before = handler.request_count
    result = _executor(
        target_server, _case(headers={"X-Tenant": "{{missing}}"})
    ).execute_case("case-version-1", "environment-revision-1", {})

    assert result.status == "BROKEN"
    assert result.failure_category == "environment"
    assert handler.request_count == before


def test_blank_case_header_does_not_override_environment_default(target_server):
    _, handler = target_server
    handler.captured_authorization = None

    result = _executor(
        target_server,
        _case("/capture", headers={"Authorization": ""}),
        values={"token": "environment-token"},
    ).execute_case("case-version-1", "environment-revision-1", {})

    assert result.status == "PASSED"
    assert handler.captured_authorization == "Bearer environment-token"


@pytest.mark.parametrize(
    "path, limits, category",
    [
        ("/slow", ExecutorLimits(timeout_seconds=0.05, max_response_bytes=2048), "timeout"),
        ("/disconnect", ExecutorLimits(timeout_seconds=1, max_response_bytes=2048), "transport"),
        ("/large", ExecutorLimits(timeout_seconds=1, max_response_bytes=128), "response_limit"),
    ],
)
def test_transport_and_response_limit_failures_are_broken(
    target_server, path, limits, category
):
    result = _executor(target_server, _case(path), limits=limits).execute_case(
        "case-version-1", "environment-revision-1", {}
    )
    assert result.status == "BROKEN"
    assert result.failure_category == category


def test_non_json_is_allowed_until_json_operation_requires_it(target_server):
    plain = _executor(target_server, _case("/text")).execute_case(
        "case-version-1", "environment-revision-1", {}
    )
    parsed = _executor(
        target_server,
        _case(
            "/text",
            assertions=[_assertion("json_path", "equals", 0, "$.code")],
        ),
    ).execute_case("case-version-1", "environment-revision-1", {})
    assert plain.status == "PASSED"
    assert parsed.status == "BROKEN"
    assert parsed.failure_category == "parser"


def test_extraction_runs_before_assertions(target_server):
    result = _executor(
        target_server,
        _case(
            extractions=[_extraction("favoriteId", "json_path", "$.data[0].id")],
            assertions=[_assertion("json_path", "equals", 0, "$.code")],
        ),
    ).execute_case("case-version-1", "environment-revision-1", {})
    assert result.status == "PASSED"
    assert result.extracted_variables == {"favoriteId": "favorite-1"}
    assert [event["phase"] for event in result.trace] == ["request", "response", "extraction", "assertion"]


def test_ssrf_redirects_and_redirect_limit_are_enforced(target_server):
    private_redirect = _executor(
        target_server, _case("/redirect-private")
    ).execute_case("case-version-1", "environment-revision-1", {})
    loop = _executor(
        target_server,
        _case("/redirect-loop"),
        limits=ExecutorLimits(timeout_seconds=1, max_response_bytes=2048, max_redirects=2),
    ).execute_case("case-version-1", "environment-revision-1", {})
    assert private_redirect.status == "BROKEN"
    assert private_redirect.failure_category == "host_policy"
    assert loop.status == "BROKEN"
    assert loop.failure_category == "redirect_limit"


def test_cross_origin_redirect_does_not_forward_credentials(target_server):
    _, handler = target_server
    handler.captured_authorization = None
    handler.captured_cookie = None
    base_url, _ = target_server
    executor = HttpExecutor(
        _CaseService(
            _case(
                "/redirect-cross-origin",
                headers={"Authorization": "Bearer secret", "Cookie": "sid=secret"},
            )
        ),
        _EnvironmentService(base_url),
        host_policy=HostPolicy(
            test_only_allowed_hosts=frozenset({"127.0.0.1", "localhost"})
        ),
    )
    result = executor.execute_case("case-version-1", "environment-revision-1", {})
    assert result.status == "PASSED"
    assert handler.captured_authorization is None
    assert handler.captured_cookie is None


def test_default_host_policy_rejects_loopback_before_request(target_server):
    base_url, handler = target_server
    before = handler.request_count
    executor = HttpExecutor(
        _CaseService(_case()),
        _EnvironmentService(base_url),
        host_policy=HostPolicy(),
    )
    result = executor.execute_case("case-version-1", "environment-revision-1", {})
    assert result.status == "BROKEN"
    assert result.failure_category == "host_policy"
    assert handler.request_count == before


def test_url_credentials_are_rejected_before_request(target_server):
    base_url, handler = target_server
    credential_url = base_url.replace("http://", "http://user:password@")
    before = handler.request_count
    result = HttpExecutor(
        _CaseService(_case()),
        _EnvironmentService(credential_url),
        host_policy=HostPolicy(test_only_allowed_hosts=frozenset({"127.0.0.1"})),
    ).execute_case("case-version-1", "environment-revision-1", {})
    assert result.status == "BROKEN"
    assert result.failure_category == "host_policy"
    assert handler.request_count == before


def test_not_exists_assertion_treats_missing_path_as_product_result(target_server):
    result = _executor(
        target_server,
        _case(
            assertions=[
                _assertion("json_path", "not_exists", None, "$.data[99].missing")
            ]
        ),
    ).execute_case("case-version-1", "environment-revision-1", {})
    assert result.status == "PASSED"


def test_local_fixture_hosts_require_explicit_non_production_opt_in(monkeypatch):
    monkeypatch.setenv("TASK_APP_ENV", "test")
    monkeypatch.setenv("API_TESTING_TEST_ALLOWED_HOSTS", "127.0.0.1, localhost")
    assert HostPolicy.from_environment().test_only_allowed_hosts == frozenset(
        {"127.0.0.1", "localhost"}
    )

    monkeypatch.setenv("TASK_APP_ENV", "prod")
    assert HostPolicy.from_environment().test_only_allowed_hosts == frozenset()


def test_expected_negative_status_passes_only_with_explicit_status_assertion(
    target_server,
):
    expected = _executor(
        target_server,
        _case("/missing", assertions=[_assertion("status_code", "equals", 404)]),
    ).execute_case("case-version-1", "environment-revision-1", {})
    unasserted = _executor(target_server, _case("/missing")).execute_case(
        "case-version-1", "environment-revision-1", {}
    )
    other_assertion_failed = _executor(
        target_server,
        _case(
            "/missing",
            assertions=[
                _assertion("status_code", "equals", 404),
                _assertion("header", "exists", None, name="X-Required"),
            ],
        ),
    ).execute_case("case-version-1", "environment-revision-1", {})
    assert expected.status == "PASSED"
    assert unasserted.status == "FAILED"
    assert unasserted.failure_category == "product_response"
    assert other_assertion_failed.status == "FAILED"
    assert other_assertion_failed.failure_category == "product_assertion"


def test_timeout_is_total_deadline_across_slow_body_and_redirects(target_server):
    for path in ("/drip", "/redirect-slow/0"):
        started = time.monotonic()
        result = _executor(
            target_server,
            _case(path),
            limits=ExecutorLimits(
                timeout_seconds=0.1,
                max_response_bytes=2048,
                max_redirects=5,
            ),
        ).execute_case("case-version-1", "environment-revision-1", {})
        elapsed = time.monotonic() - started
        assert result.status == "BROKEN"
        assert result.failure_category == "timeout"
        assert elapsed < 0.2


def test_server_generated_sensitive_values_are_masked_everywhere(target_server):
    phases = []
    result = HttpExecutor(
        _CaseService(
            _case(
                "/token",
                assertions=[
                    _assertion(
                        "json_path",
                        "equals",
                        "server-issued-review-secret",
                        "$.data.access_token",
                    )
                ],
                extractions=[
                    _extraction("issuedToken", "json_path", "$.data.access_token")
                ],
            )
        ),
        _EnvironmentService(target_server[0]),
        host_policy=HostPolicy(
            test_only_allowed_hosts=frozenset({"127.0.0.1"})
        ),
        phase_callback=lambda phase, payload: phases.append((phase, payload)),
    ).execute_case("case-version-1", "environment-revision-1", {})
    persisted = json.dumps(
        {"result": result.to_dict(), "phases": phases}, ensure_ascii=False
    )
    for secret in (
        "server-issued-review-secret",
        "server-password-secret",
        "server-api-key-secret",
    ):
        assert secret not in persisted
    assert result.status == "PASSED"
    assert {phase for phase, _payload in phases} >= {
        "request",
        "response",
        "extraction",
        "assertion",
    }


def test_workflow_error_masks_values_extracted_before_assertion_failure(target_server):
    case = _case(
        processing=_workflow_processing(
            setup_steps=[
                _workflow_step(
                    "获取临时登录态",
                    "GET",
                    "/token",
                    assertions=[
                        {
                            "type": "unsupported",
                            "operator": "equals",
                            "expected": True,
                            "timeout_ms": 0,
                            "enabled": True,
                        }
                    ],
                    extractions=[
                        _json_extraction(
                            "issuedToken", "$.data.access_token"
                        )
                    ],
                )
            ]
        )
    )

    result = _executor(target_server, case).execute_case(
        "case-version-1", "environment-revision-1", {}
    )
    persisted = json.dumps(result.to_dict(), ensure_ascii=False)

    assert result.status == "BROKEN"
    assert result.failure_category == "setup"
    for secret in (
        "server-issued-review-secret",
        "server-password-secret",
        "server-api-key-secret",
    ):
        assert secret not in persisted


def test_explicit_cancellation_wins_before_and_after_request(target_server):
    before_request = _executor(
        target_server, _case(), cancel=lambda _phase: True
    ).execute_case("case-version-1", "environment-revision-1", {})
    phases = []

    def cancel_after_response(phase):
        phases.append(phase)
        return phase == "after_response"

    after_response = _executor(
        target_server, _case(), cancel=cancel_after_response
    ).execute_case("case-version-1", "environment-revision-1", {})
    assert before_request.status == "CANCELLED"
    assert after_response.status == "CANCELLED"
    assert "after_response" in phases


def test_secrets_are_redacted_from_result_trace_errors_and_repr(target_server):
    secret = "task6-runtime-secret-token"
    result = _executor(
        target_server,
        _case("/business-fail", headers={"X-Api-Token": "{{token}}"}),
        values={"token": secret},
    ).execute_case("case-version-1", "environment-revision-1", {})
    rendered = json.dumps(result.to_dict(), ensure_ascii=False)
    assert secret not in rendered
    assert secret not in repr(result)
    assert "Bearer ***" not in rendered
    assert "***" in rendered
