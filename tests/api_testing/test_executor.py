import json
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from task_server.api_testing.executor import (
    ExecutorLimits,
    HostPolicy,
    HostPolicyError,
    HttpExecutor,
)
from task_server.api_testing.contracts.case import AssertionView
from task_server.api_testing.services.execution_service import ExecutionService


class _TargetHandler(BaseHTTPRequestHandler):
    request_count = 0
    port = 0
    captured_authorization = None
    captured_cookie = None
    captured_preview_token = None
    workflow_calls = []
    workflow_poll_attempts = 0

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
        if self.path.startswith("/workflow/ambiguous-list"):
            self._send(200, {"code": 0, "records": [{"sn": "resource-1", "id": "12"}, {"sn": "resource-1", "id": "34"}]})
        elif self.path.startswith("/workflow/missing-list"):
            self._send(200, {"code": 0, "records": [{"sn": "other", "id": "12"}]})
        elif self.path.startswith("/workflow/owned-list"):
            self._send(200, {"code": 0, "records": [{"sn": "other", "id": "12"}, {"sn": "resource-1", "id": "34"}]})
        elif self.path.startswith("/workflow/setup-fail"):
            self._send(200, {"code": 5001, "message": "no resource"})
        elif self.path.startswith("/workflow/ownership-mismatch"):
            self._send(200, {"code": 0, "data": {"resourceSn": "wrong-resource"}})
        elif self.path.startswith("/workflow/cleanup-source-fail"):
            self._send(
                200,
                {
                    "code": 5001,
                    "message": "unexpected resource",
                    "data": {"resourceSn": "wrong-resource"},
                },
            )
        elif self.path.startswith("/workflow/poll"):
            type(self).workflow_poll_attempts += 1
            if type(self).workflow_poll_attempts < 3:
                self._send(200, {"code": 60102005, "data": None})
            else:
                self._send(
                    200,
                    {"code": 0, "data": {"resourceSn": "resource-polled"}},
                )
        elif self.path.startswith("/workflow/setup"):
            self._send(200, {"code": 0, "data": {"resourceSn": "resource-1"}})
        elif self.path.startswith("/workflow/static-cleanup"):
            self._send(200, {"code": 0})
        elif self.path.startswith("/ok"):
            self._send(200, {"code": 0, "data": [{"id": "favorite-1"}]})
        elif self.path.startswith("/nullable"):
            self._send(200, {"code": 0, "data": None})
        elif self.path.startswith("/rendered-schema"):
            self._send(200, {"value": "replacement"})
        elif self.path.startswith("/json-null"):
            self._send(200, None)
        elif self.path.startswith("/business-fail"):
            self._send(200, {"code": 4009, "message": "not logged in"})
        elif self.path.startswith("/business-line-fail"):
            self._send(200, {
                "code": 4009,
                "msg": "业务线未知！",
                "data": {"resourceSn": "cleanup-resource-1"},
            })
        elif self.path.startswith("/boolean-code"):
            self._send(200, {"code": False, "message": "业务处理失败"})
        elif self.path.startswith("/status-500"):
            self._send(500, {"message": "server error"})
        elif self.path.startswith("/status-400"):
            self._send(400, {"code": 4001, "message": "invalid request"})
        elif self.path.startswith("/text"):
            self._send(200, b"plain response", "text/plain")
        elif self.path.startswith("/malformed-json"):
            self._send(200, b'{"code":', "application/json")
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
                            "accessid": "server-upload-access-id",
                            "policy": "server-upload-policy",
                            "signature": "server-upload-signature",
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
        elif self.path.startswith("/qa/redirect-outside"):
            self.send_response(302)
            self.send_header("Location", "/capture")
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
            type(self).captured_preview_token = self.headers.get("X-Preview-Token")
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


def test_slow_main_request_is_advisory_and_preserves_passing_result(target_server):
    case = _case('/slow', processing={'pre': [], 'post': [], 'response_time_warning_ms': 10})
    result = _executor(target_server, case).execute_case('case-version-1', 'environment-revision-1', {})
    assert result.status == 'PASSED'
    assert result.to_dict()['response_time_warning']['status'] == 'slow'
    assert result.to_dict()['response_time_warning']['duration_ms'] >= 200


class _CaseService:
    def __init__(self, case):
        self.case = case

    def get_version(self, _case_version_id):
        return self.case


class _Runtime:
    def __init__(self, base_url, values):
        self.service_base_url_references = {'default': frozenset()}
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


def test_default_executor_timeout_covers_slow_ai_endpoints():
    assert ExecutorLimits().timeout_seconds == 30


def test_disabled_schema_draft_cannot_break_http_execution_by_rendering_literals(target_server):
    rule = _assertion('schema', 'equals', {'type': 'string', 'const': '{{recipient}}'})
    rule.enabled = False
    result = _executor(target_server, _case('/ok', assertions=[rule])).execute_case(
        'case-version-1', 'environment-revision-1', {})
    assert result.status == 'PASSED'
    assert result.sanitized_response['status_code'] == 200


def test_enabled_schema_preserves_existing_variable_rendering(target_server):
    rule = _assertion('schema', 'equals', {'type': 'object', 'required': ['value'],
        'properties': {'value': {'const': '{{recipient}}'}}})
    result = _executor(target_server, _case('/rendered-schema', assertions=[rule]),
        values={'recipient': 'replacement'}).execute_case('case-version-1', 'environment-revision-1', {})
    assert result.status == 'PASSED'
    assert result.assertion_results[0]['expected']['properties']['value']['const'] == 'replacement'


def test_top_level_null_draft_is_not_installed_and_real_http_still_runs(target_server):
    from task_server.api_testing.services.response_schema_draft import response_schema_draft
    operation = {'responses': {'200': {'content': {'application/json': {
        'schema': {'anyOf': [{'type': 'string'}, {'type': 'null'}]},
    }}}}}
    draft, explanation = response_schema_draft(operation, '3.1.0')
    assert draft is None
    assert '顶层 JSON null' in explanation
    rule = _assertion('status_code', 'equals', 200)
    result = _executor(target_server, _case('/json-null', assertions=[rule])).execute_case(
        'case-version-1', 'environment-revision-1', {})
    assert result.status == 'PASSED'
    assert result.sanitized_response['status_code'] == 200


@pytest.mark.parametrize("stage", ["main", "setup_steps", "cleanup_steps"])
def test_invalid_historical_schema_blocks_entire_chain_before_http(target_server, stage):
    rule = _assertion("schema", "equals", {"properties": {"data": {"format": "email"}}})
    case = _case(processing={"pre": [], "post": [], "setup_steps": [], "cleanup_steps": []})
    if stage == "main":
        case.assertions = (rule,)
    else:
        case.processing[stage] = [_workflow_step("历史步骤", "GET", "/ok", assertions=[vars(rule)])]
    before = target_server[1].request_count
    result = _executor(target_server, case).execute_case("case-version-1", "environment-revision-1", {})
    assert target_server[1].request_count == before
    assert result.status == "BROKEN"
    assert result.failure_category == "assertion_definition"


def test_setup_preview_returns_raw_target_response_but_keeps_trace_sanitized(
    target_server,
):
    executor = _executor(target_server, _case())

    result = executor.preview_setup_steps(
        "environment-revision-1",
        [
            _workflow_step(
                "获取访问令牌",
                "GET",
                "/token",
                extractions=[
                    _json_extraction("accessToken", "$.data.access_token")
                ],
            )
        ],
        0,
        initial_variables={},
        processing_pre=[],
        extraction_overrides={},
    )

    assert result["status"] == "PASSED"
    assert result["response"]["body"]["data"]["access_token"] == (
        "server-issued-review-secret"
    )
    assert "server-issued-review-secret" not in json.dumps(
        result["trace"], ensure_ascii=False
    )
    assert result["available_variables"] == ["accessToken"]


def test_setup_preview_applies_session_override_before_the_next_step(
    target_server,
):
    _, handler = target_server
    handler.captured_preview_token = None
    executor = _executor(target_server, _case())

    result = executor.preview_setup_steps(
        "environment-revision-1",
        [
            _workflow_step(
                "获取访问令牌",
                "GET",
                "/token",
                extractions=[
                    _json_extraction("accessToken", "$.data.access_token")
                ],
            ),
            {
                **_workflow_step(
                    "校验替换令牌",
                    "GET",
                    "/capture",
                    assertions=[_code_assertion()],
                    required_variables=["accessToken"],
                ),
                "request": {
                    **_workflow_step("unused", "GET", "/capture")["request"],
                    "headers": {"X-Preview-Token": "{{accessToken}}"},
                },
            },
        ],
        1,
        initial_variables={},
        processing_pre=[],
        extraction_overrides={"accessToken": "manually-replaced-token"},
    )

    assert result["status"] == "PASSED"
    assert handler.captured_preview_token == "manually-replaced-token"
    assert result["available_variables"] == ["accessToken"]


def test_setup_preview_reports_when_a_failed_prefix_prevents_the_target_step(
    target_server,
):
    executor = _executor(target_server, _case())

    result = executor.preview_setup_steps(
        "environment-revision-1",
        [
            _workflow_step(
                "失败的资源查询",
                "GET",
                "/workflow/setup-fail",
                assertions=[_code_assertion()],
            ),
            _workflow_step("不会执行", "GET", "/workflow/setup"),
        ],
        1,
        initial_variables={},
        processing_pre=[],
        extraction_overrides={},
    )

    assert result["status"] == "FAILED"
    assert result["target_index"] == 1
    assert result["executed_index"] == 0
    assert result["target_reached"] is False


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
    polling=None,
):
    step = {
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
    if polling is not None:
        step["polling"] = polling
    return step


def _workflow_processing(*, setup_steps=None, cleanup_steps=None):
    return {
        "pre": [],
        "post": [],
        "setup_steps": setup_steps or [],
        "cleanup_steps": cleanup_steps or [],
    }


class _CleanupOwnershipRisk(AssertionError):
    """The local fixture observed a cleanup using a previous run's value."""


def _assert_no_stale_cleanup(handler):
    cleanups = [call for call in handler.workflow_calls if call[1] == "/workflow/cancel"]
    if cleanups:
        # A different failure must fail the test, not be hidden as the known risk.
        assert cleanups == [("POST", "/workflow/cancel", {"target": "another-run"})]
        raise _CleanupOwnershipRisk("cleanup sent an identifier from a previous run")


@pytest.mark.parametrize(("action", "initial"), [
    ("copy_variable", "another-run"),
    ("json_encode", "another-run"),
    ("json_decode", '"another-run"'),
])
def test_cleanup_rejects_stale_resource_derived_before_a_lost_response(target_server, action, initial):
    _, handler = target_server
    handler.workflow_calls = []
    cleanup = _workflow_step("清理", "POST", "/workflow/cancel", body={"target": "{{alias}}"})
    processing = _workflow_processing(cleanup_steps=[cleanup])
    processing["pre"] = [{"action": action, "source": "resourceSn", "target": "alias"}]
    case = _case("/disconnect", extractions=[_extraction("resourceSn", "json_path", "$.data.resourceSn")], processing=processing)
    result = _executor(target_server, case).execute_case("case", "env", {"resourceSn": initial})
    assert result.status == "BROKEN"
    assert handler.workflow_calls[0][1] == "/disconnect"
    if action == "json_encode":
        # Encoding preserves the same stale identifier as a JSON string.
        cleanups = [call for call in handler.workflow_calls if call[1] == "/workflow/cancel"]
        if cleanups:
            assert cleanups == [("POST", "/workflow/cancel", {"target": '"another-run"'})]
            raise _CleanupOwnershipRisk("cleanup sent an encoded previous-run identifier")
    else:
        _assert_no_stale_cleanup(handler)


def test_cleanup_rejects_data_row_id_after_optional_dependency_fails(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    dependency = {"case_version_id": "setup", "required": False, "exports": ["resourceSn"]}
    dependency_result = SimpleNamespace(status="FAILED", extracted_variables={})
    overrides, blocked = ExecutionService._dependency_overrides(
        {"dependencies": [dependency]}, {"setup": dependency_result},
    )
    assert overrides == {}
    assert blocked == ""
    row = SimpleNamespace(enabled=True, values={"resourceSn": "another-run"})
    cleanup = _workflow_step("清理", "POST", "/workflow/cancel", body={"target": "{{resourceSn}}"})
    case = _case("/ok", data_rows=[row], processing=_workflow_processing(cleanup_steps=[cleanup]))
    unavailable = ExecutionService._unavailable_dependency_exports(
        {"dependencies": [dependency]}, {"setup": dependency_result},
    )
    result = _executor(target_server, case).execute_case(
        "case", "env", {}, dependency_overrides=overrides,
        unavailable_dependency_exports=unavailable,
    )
    assert result.status == "FAILED"
    assert result.failure_category == "cleanup"
    assert handler.workflow_calls[0][1] == "/ok"
    _assert_no_stale_cleanup(handler)


def test_cleanup_blocks_transitive_pre_alias_even_after_main_succeeds(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    cleanup = _workflow_step("清理", "POST", "/workflow/cancel", body={"target": "{{second}}"})
    processing = _workflow_processing(cleanup_steps=[cleanup])
    processing["pre"] = [
        {"action": "copy_variable", "source": "resourceSn", "target": "first"},
        {"action": "copy_variable", "source": "first", "target": "second"},
    ]
    case = _case("/workflow/setup", extractions=[_extraction("resourceSn", "json_path", "$.data.resourceSn")], processing=processing)
    result = _executor(target_server, case).execute_case("case", "env", {"resourceSn": "another-run"})
    assert result.status == "FAILED"
    assert not any(call[1] == "/workflow/cancel" for call in handler.workflow_calls)


def test_optional_dependency_missing_export_is_unavailable():
    dependencies = {"dependencies": [{"case_version_id": "setup", "required": False, "exports": ["resourceSn"]}]}
    result = SimpleNamespace(status="PASSED", extracted_variables={})
    assert ExecutionService._unavailable_dependency_exports(dependencies, {"setup": result}) == {"resourceSn"}


def test_main_current_response_can_replace_unavailable_dependency_output(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    cleanup = _workflow_step("清理", "POST", "/workflow/cancel", body={"target": "{{resourceSn}}"})
    case = _case("/workflow/setup", extractions=[_extraction("resourceSn", "json_path", "$.data.resourceSn")], processing=_workflow_processing(cleanup_steps=[cleanup]))
    result = _executor(target_server, case).execute_case(
        "case", "env", {"resourceSn": "another-run"},
        unavailable_dependency_exports={"resourceSn"},
    )
    assert result.status == "PASSED"
    assert ("POST", "/workflow/cancel", {"target": "resource-1"}) in handler.workflow_calls


def test_cleanup_is_stopped_when_main_output_is_missing_with_stale_direct_value(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    cleanup = _workflow_step("清理", "POST", "/workflow/cancel", body={"target": "{{resourceSn}}"})
    case = _case("/disconnect", extractions=[_extraction("resourceSn", "json_path", "$.data.resourceSn")], processing=_workflow_processing(cleanup_steps=[cleanup]))
    result = _executor(target_server, case).execute_case("case", "env", {"resourceSn": "another-run"})
    assert result.status == "BROKEN"
    assert not any(call[1] == "/workflow/cancel" for call in handler.workflow_calls)


def test_unavailable_output_does_not_change_a_case_without_cleanup(target_server):
    case = _case("/ok")
    result = _executor(target_server, case).execute_case(
        "case", "env", {}, unavailable_dependency_exports={"resourceSn"},
    )
    assert result.status == "PASSED"


def test_skipped_later_setup_does_not_reuse_an_earlier_setup_output(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    first = _workflow_step("前置一", "GET", "/workflow/setup", extractions=[_json_extraction("resourceSn", "$.data.resourceSn")])
    second = _workflow_step("前置二", "GET", "/workflow/setup", extractions=[_json_extraction("resourceSn", "$.data.resourceSn")])
    second["only_if_variable"] = "notAvailable"
    cleanup = _workflow_step("清理", "POST", "/workflow/cancel", body={"target": "{{resourceSn}}"})
    case = _case("/ok", processing=_workflow_processing(setup_steps=[first, second], cleanup_steps=[cleanup]))
    result = _executor(target_server, case).execute_case("case", "env", {})
    assert result.status == "FAILED"
    assert not any(call[1] == "/workflow/cancel" for call in handler.workflow_calls)


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


@pytest.mark.parametrize("conditional", [False, True])
def test_inline_setup_feeds_print_request_and_print_result_feeds_cancel_cleanup(
    target_server, conditional,
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

    if conditional:
        case.processing["cleanup_steps"][0]["only_if_variable"] = "printTaskSn"

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


def test_inline_step_assertion_expected_value_uses_extracted_variable(target_server):
    case = _case(
        "/workflow/print",
        method="POST",
        body={"resourceSn": "{{resourceSn}}"},
        assertions=[_code_assertion()],
        processing=_workflow_processing(
            setup_steps=[
                _workflow_step(
                    "提取资源",
                    "GET",
                    "/workflow/setup",
                    assertions=[_code_assertion()],
                    extractions=[
                        _json_extraction("resourceSn", "$.data.resourceSn")
                    ],
                ),
                _workflow_step(
                    "确认同一资源",
                    "GET",
                    "/workflow/setup",
                    assertions=[
                        _code_assertion(),
                        AssertionView(
                            type="json_path",
                            operator="equals",
                            path="$.data.resourceSn",
                            expected="{{resourceSn}}",
                            timeout_ms=0,
                            enabled=True,
                            name=None,
                            sequence=1,
                        ),
                    ],
                    required_variables=["resourceSn"],
                ),
            ]
        ),
    )

    result = _executor(target_server, case).execute_case(
        "case-version-1", "environment-revision-1", {}
    )

    assert result.status == "PASSED"
    confirmation = next(
        event
        for event in result.trace
        if event.get("phase") == "workflow_step"
        and event.get("name") == "确认同一资源"
    )
    assert confirmation["assertions"][1]["expected"] == "resource-1"


def test_setup_polling_retries_until_assertions_and_extractions_pass(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    handler.workflow_poll_attempts = 0
    case = _case(
        "/workflow/print",
        method="POST",
        body={"resourceSn": "{{resourceSn}}"},
        assertions=[_code_assertion()],
        processing=_workflow_processing(
            setup_steps=[
                _workflow_step(
                    "等待资源就绪",
                    "GET",
                    "/workflow/poll",
                    assertions=[_code_assertion()],
                    extractions=[
                        _json_extraction("resourceSn", "$.data.resourceSn")
                    ],
                    polling={"max_attempts": 3, "interval_ms": 1},
                )
            ]
        ),
    )

    result = _executor(target_server, case).execute_case(
        "case-version-1", "environment-revision-1", {}
    )

    assert result.status == "PASSED"
    assert handler.workflow_poll_attempts == 3
    assert handler.workflow_calls[-1] == (
        "POST",
        "/workflow/print",
        {"resourceSn": "resource-polled"},
    )
    attempts = [
        event
        for event in result.trace
        if event.get("phase") == "workflow_step"
        and event.get("name") == "等待资源就绪"
    ]
    assert [(item["attempt"], item["max_attempts"]) for item in attempts] == [
        (1, 3),
        (2, 3),
        (3, 3),
    ]


def test_setup_polling_stops_at_limit_and_does_not_run_main(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    handler.workflow_poll_attempts = 0
    case = _case(
        "/workflow/print",
        method="POST",
        assertions=[_code_assertion()],
        processing=_workflow_processing(
            setup_steps=[
                _workflow_step(
                    "等待资源就绪",
                    "GET",
                    "/workflow/poll",
                    assertions=[_code_assertion()],
                    polling={"max_attempts": 2, "interval_ms": 1},
                )
            ]
        ),
    )

    result = _executor(target_server, case).execute_case(
        "case-version-1", "environment-revision-1", {}
    )

    assert result.status == "FAILED"
    assert result.failure_category == "setup"
    assert handler.workflow_poll_attempts == 2
    assert [call[0] for call in handler.workflow_calls] == ["GET", "GET"]


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
    assert result.failure_category == "business_response"
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


def test_failed_setup_step_does_not_feed_extractions_to_cleanup(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    case = _case(
        processing=_workflow_processing(
            setup_steps=[
                _workflow_step(
                    "定位主体资源",
                    "GET",
                    "/workflow/cleanup-source-fail",
                    assertions=[_code_assertion()],
                    extractions=[
                        _json_extraction("resourceSn", "$.data.resourceSn")
                    ],
                )
            ],
            cleanup_steps=[
                _workflow_step(
                    "删除前置定位资源",
                    "POST",
                    "/workflow/cancel",
                    body={"resourceSn": "{{resourceSn}}"},
                    assertions=[_code_assertion()],
                    required_variables=["resourceSn"],
                )
            ],
        )
    )

    result = _executor(target_server, case).execute_case(
        "case-version-1", "environment-revision-1", {}
    )

    assert result.status == "FAILED"
    assert result.failure_category == "setup"
    assert [call[1] for call in handler.workflow_calls] == [
        "/workflow/cleanup-source-fail"
    ]
    cleanup = [
        event
        for event in result.trace
        if event.get("phase") == "workflow_step"
        and event.get("stage") == "cleanup"
    ]
    assert cleanup[0]["status"] == "SKIPPED"
    assert cleanup[0]["missing_variables"] == ["resourceSn"]


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


def test_failed_cleanup_step_does_not_feed_extractions_to_later_cleanup(
    target_server,
):
    _, handler = target_server
    handler.workflow_calls = []
    case = _case(
        processing=_workflow_processing(
            cleanup_steps=[
                _workflow_step(
                    "定位待清理资源",
                    "GET",
                    "/workflow/cleanup-source-fail",
                    assertions=[_code_assertion()],
                    extractions=[
                        _json_extraction("resourceSn", "$.data.resourceSn")
                    ],
                ),
                _workflow_step(
                    "删除定位到的资源",
                    "POST",
                    "/workflow/cancel",
                    body={"resourceSn": "{{resourceSn}}"},
                    assertions=[_code_assertion()],
                    required_variables=["resourceSn"],
                ),
            ]
        )
    )

    result = _executor(target_server, case).execute_case(
        "case-version-1", "environment-revision-1", {}
    )

    assert result.status == "FAILED"
    assert result.failure_category == "cleanup"
    assert [call[1] for call in handler.workflow_calls] == [
        "/ok",
        "/workflow/cleanup-source-fail",
    ]
    cleanup = [
        event
        for event in result.trace
        if event.get("phase") == "workflow_step"
        and event.get("stage") == "cleanup"
    ]
    assert [(event["status"], event.get("failure_category")) for event in cleanup] == [
        ("FAILED", "business_response"),
        ("SKIPPED", "cleanup_dependency"),
    ]


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
    assert assertion_failed.failure_category == "business_response"
    assert server_failed.status == "FAILED"
    assert server_failed.failure_category == "product_response"


def test_expected_http_400_requires_and_records_the_exact_business_code(target_server):
    expected = _case("/status-400", assertions=[
        _assertion("status_code", "equals", 400),
        _assertion("json_path", "equals", 4001, "$.code"),
    ])
    result = _executor(target_server, expected).execute_case("v", "env", {}).to_dict()

    assert result["status"] == "PASSED"
    assert result["response"]["status_code"] == 400
    assert [(item["actual"], item["passed"]) for item in result["assertions"]] == [
        (400, True), (4001, True),
    ]


def test_unexpected_business_code_on_http_400_is_not_reported_as_http_success(target_server):
    case = _case("/status-400", assertions=[_assertion("status_code", "equals", 400)])
    result = _executor(target_server, case).execute_case("v", "env", {}).to_dict()

    assert result["status"] == "FAILED"
    assert result["failure_category"] == "business_response"
    assert result["response"]["status_code"] == 400
    assert result["assertions"][0]["passed"] is True
    assert result["assertions"][1]["actual"] == 4001
    assert result["assertions"][1]["passed"] is False
    assert "HTTP 400" in result["error_message"]
    assert "HTTP 请求成功" not in result["error_message"]


def test_explicit_business_code_alone_does_not_accept_http_400(target_server):
    case = _case("/status-400", assertions=[_assertion("json_path", "equals", 4001, "$.code")])
    result = _executor(target_server, case).execute_case("v", "env", {}).to_dict()

    assert result["status"] == "FAILED"
    assert result["failure_category"] == "product_response"
    assert result["response"]["status_code"] == 400
    assert result["assertions"][0]["passed"] is True
    assert "HTTP 400" in result["error_message"]


def test_business_failure_cannot_pass_with_broad_negative_assertion(target_server):
    result = _executor(
        target_server,
        _case(
            "/business-fail",
            assertions=[
                _assertion("status_code", "equals", 200),
                _assertion("json_path", "not_equals", 0, "$.code"),
            ],
        ),
    ).execute_case("case-version-1", "environment-revision-1", {})

    assert result.status == "FAILED"
    assert result.failure_category == "business_response"
    business_guard = next(item for item in result.assertion_results if item["type"] == "business_code")
    assert business_guard["actual"] == 4009
    assert business_guard["passed"] is False


def test_business_assertion_failure_is_reported_before_required_extraction(
    target_server,
):
    result = _executor(
        target_server,
        _case(
            "/business-line-fail",
            assertions=[_assertion("json_path", "equals", 0, "$.code")],
            extractions=[
                _extraction(
                    "cleanupResourceSn",
                    "json_path",
                    "$.data.resourceSn",
                ),
                _extraction(
                    "resourceSn",
                    "json_path",
                    "$.data.records[0].sn",
                )
            ],
        ),
    ).execute_case("case-version-1", "environment-revision-1", {})

    assert result.status == "FAILED"
    assert result.failure_category == "business_response"
    assert result.extracted_variables == {
        "cleanupResourceSn": "cleanup-resource-1",
    }
    assert result.assertion_results[0]["actual"] == 4009
    assert "业务码 4009" in result.error_message
    assert "业务线未知" in result.error_message
    assert "期望 0" in result.error_message
    assert "JSON 路径未找到" not in result.error_message
    assert [event["phase"] for event in result.trace] == [
        "request",
        "response",
        "assertion",
        "extraction",
    ]


def test_boolean_business_code_does_not_equal_numeric_zero(target_server):
    result = _executor(
        target_server,
        _case(
            "/boolean-code",
            assertions=[_assertion("json_path", "equals", 0, "$.code")],
        ),
    ).execute_case("case-version-1", "environment-revision-1", {})

    assert result.status == "FAILED"
    assert result.failure_category == "business_response"
    assert result.assertion_results[0]["actual"] is False
    assert result.assertion_results[0]["passed"] is False
    assert result.assertion_results[1]["type"] == "business_code"
    assert result.assertion_results[1]["passed"] is False


def test_non_null_assertion_failure_explains_the_operator_and_path(target_server):
    result = _executor(
        target_server,
        _case(
            "/nullable",
            assertions=[
                _assertion("json_path", "not_equals", None, "$.data"),
            ],
        ),
    ).execute_case("case-version-1", "environment-revision-1", {})

    assert result.status == "FAILED"
    assert result.failure_category == "product_assertion"
    assert result.assertion_results[0]["message"] == "字段 $.data 必须非空，实际为 null"
    assert "字段 $.data 必须非空，实际为 null" in result.error_message
    assert "断言期望 null，实际 null" not in result.error_message


def test_exact_expected_business_failure_code_can_pass(target_server):
    result = _executor(
        target_server,
        _case(
            "/business-fail",
            assertions=[
                _assertion("status_code", "equals", 200),
                _assertion("json_path", "equals", 4009, "$.code"),
            ],
        ),
    ).execute_case("case-version-1", "environment-revision-1", {})

    assert result.status == "PASSED"


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


def test_non_latin1_header_is_rejected_with_safe_chinese_guidance(target_server):
    _, handler = target_server
    before = handler.request_count

    result = _executor(
        target_server,
        _case(headers={"Authorization": "Bearer 中文占位令牌"}),
    ).execute_case("case-version-1", "environment-revision-1", {})

    assert result.status == "BROKEN"
    assert result.failure_category == "environment"
    assert "Authorization" in result.error_message
    assert "HTTP" in result.error_message
    assert "中文占位令牌" not in result.error_message
    assert handler.request_count == before


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


def test_malformed_json_content_is_a_parser_error_when_json_asserted(target_server):
    result = _executor(
        target_server,
        _case('/malformed-json', assertions=[_assertion('json_path', 'equals', 0, '$.code')]),
    ).execute_case('case-version-1', 'environment-revision-1', {})

    assert result.status == 'BROKEN'
    assert result.failure_category == 'parser'
    assert result.sanitized_response['status_code'] == 200


def test_extraction_runs_only_after_assertions_pass(target_server):
    result = _executor(
        target_server,
        _case(
            extractions=[_extraction("favoriteId", "json_path", "$.data[0].id")],
            assertions=[_assertion("json_path", "equals", 0, "$.code")],
        ),
    ).execute_case("case-version-1", "environment-revision-1", {})
    assert result.status == "PASSED"
    assert result.extracted_variables == {"favoriteId": "favorite-1"}
    assert [event["phase"] for event in result.trace] == ["request", "response", "assertion", "extraction"]


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
    before = handler.request_count
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
    assert result.status == "BROKEN"
    assert result.failure_category == "host_policy"
    assert handler.request_count == before + 1
    assert handler.captured_authorization is None
    assert handler.captured_cookie is None


@pytest.mark.parametrize("path", ["absolute", "../capture", "%2e%2e/capture", "%252e%252e/capture", "..\\capture"])
def test_preview_destination_cannot_escape_authorized_service(target_server, path):
    base_url, handler = target_server
    before = handler.request_count
    if path == "absolute":
        path = base_url.replace("127.0.0.1", "localhost") + "/capture"
    executor = HttpExecutor(
        _CaseService(_case()), _EnvironmentService(base_url + "/qa", {"token": "private-preview-token"}),
        host_policy=HostPolicy(test_only_allowed_hosts=frozenset({"127.0.0.1", "localhost"})),
    )
    result = executor.preview_setup_steps("revision", [_workflow_step("preview", "GET", path)], 0,
                                          initial_variables={}, processing_pre=[], extraction_overrides={})
    assert result["status"] == "BROKEN"
    assert result["failure_category"] == "host_policy"
    assert handler.request_count == before


def test_absolute_url_requires_matching_selected_service(target_server):
    base_url, _ = target_server
    result = _executor(target_server, _case(base_url + "/ok")).execute_case("case", "revision", {})
    assert result.status == "PASSED"


def test_same_origin_redirect_cannot_escape_service_base(target_server):
    base_url, handler = target_server
    before = handler.request_count
    executor = HttpExecutor(
        _CaseService(_case("/redirect-outside")), _EnvironmentService(base_url + "/qa", {"token": "private-preview-token"}),
        host_policy=HostPolicy(test_only_allowed_hosts=frozenset({"127.0.0.1"})),
    )
    result = executor.execute_case("case", "revision", {})
    assert result.status == "BROKEN"
    assert result.failure_category == "host_policy"
    assert handler.request_count == before + 1


def test_absolute_domain_requires_explicit_service_selection(target_server):
    base_url, _ = target_server
    other_base = base_url.replace("127.0.0.1", "localhost")
    class Environment:
        def resolve_runtime(self, _revision, _values, service_name=None):
            runtime = _Runtime(base_url, {"token": "service-fixture"})
            runtime.base_url_for = lambda name: {"default": base_url, "other": other_base}[name]
            return runtime
    executor = HttpExecutor(_CaseService(_case()), Environment(),
                            host_policy=HostPolicy(test_only_allowed_hosts=frozenset({"127.0.0.1", "localhost"})))
    step = _workflow_step("other", "GET", other_base + "/ok")
    result = executor.preview_setup_steps("revision", [step], 0, initial_variables={}, processing_pre=[], extraction_overrides={})
    assert result["failure_category"] == "host_policy"
    step["request"]["service"] = "other"
    result = executor.preview_setup_steps("revision", [step], 0, initial_variables={}, processing_pre=[], extraction_overrides={})
    assert result["status"] == "PASSED"


def test_host_header_cannot_override_authorized_service(target_server):
    _, handler = target_server
    before = handler.request_count
    result = _executor(target_server, _case(headers={"hOsT": "production.example.test"})).execute_case("case", "revision", {})
    assert result.status == "BROKEN"
    assert result.failure_category == "host_policy"
    assert handler.request_count == before


def test_destination_policy_errors_have_chinese_recovery_guidance():
    messages = {
        "Host header must match the configured service": ("Host", "删除"),
        "Request destination requires a matching configured service binding": ("授权服务", "管理员"),
        "Request destination must not include credentials": ("账号密码", "移除"),
        "Request destination is outside the configured service base": ("基础路径", "修改"),
        "Request destination contains an ambiguous path": ("歧义字符", "移除"),
        "Request destination must not traverse the service base": ("上级目录", "修改"),
        "Request destination contains excessive URL encoding": ("重复编码", "单次编码"),
    }
    for internal_message, expected_terms in messages.items():
        error = HostPolicyError(internal_message)
        state, category, message = HttpExecutor._exception_details(error)
        assert (state, category) == ("BROKEN", "host_policy")
        assert str(error) == internal_message
        assert "请" in message
        assert all(term in message for term in expected_terms)
    assert HttpExecutor._exception_details(HostPolicyError("host has no usable address"))[2] == "host has no usable address"


def test_preview_destination_error_is_chinese_without_sending_request(monkeypatch):
    executor = HttpExecutor(_CaseService(_case()), _EnvironmentService("https://qa.example.test/api"))
    monkeypatch.setattr(executor, "_request", lambda *_args, **_kwargs: pytest.fail("blocked preview must not send a request"))
    step = _workflow_step("preview", "GET", "https://other.example.test/collect")
    result = executor.preview_setup_steps("revision", [step], 0, initial_variables={}, processing_pre=[], extraction_overrides={})
    assert result["status"] == "BROKEN"
    assert result["failure_category"] == "host_policy"
    assert "授权服务" in result["error_message"]
    assert "请" in result["error_message"]
    assert "管理员" in result["error_message"]


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
    assert "环境配置" in result.error_message
    assert "云元数据地址" in result.error_message
    assert handler.request_count == before


def test_host_policy_allows_only_rfc1918_when_service_explicitly_authorizes_private_network(monkeypatch):
    def resolve(host, port, *, type):  # noqa: A002 - mirrors socket API
        addresses = {
            "service.internal": "10.9.0.2",
            "metadata.internal": "169.254.169.254",
            "loopback.internal": "127.0.0.1",
            "benchmark.internal": "198.18.0.1",
        }
        address = addresses[host]
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    parsed, port, addresses = HostPolicy().resolve(
        "http://service.internal:8080/health",
        allow_private_network=True,
    )
    assert parsed.hostname == "service.internal"
    assert port == 8080
    assert addresses == ("10.9.0.2",)

    with pytest.raises(HostPolicyError, match="non-public"):
        HostPolicy().resolve(
            "http://metadata.internal/latest",
            allow_private_network=True,
        )
    with pytest.raises(HostPolicyError, match="non-public"):
        HostPolicy().resolve(
            "http://loopback.internal/admin",
            allow_private_network=True,
        )
    with pytest.raises(HostPolicyError, match="non-public"):
        HostPolicy().resolve(
            "http://benchmark.internal/test",
            allow_private_network=True,
        )


def test_executor_scopes_private_network_authorization_to_selected_environment_service(target_server):
    base_url, _ = target_server

    class Environment:
        def resolve_runtime(self, _revision, _values, service_name=None):
            runtime = _Runtime(base_url, {})
            runtime.service_metadata = {
                "default": {"allow_private_network": service_name == "default"},
            }
            return runtime

    class RecordingPolicy:
        def __init__(self):
            self.allow_private_network = None

        def resolve(self, url, *, allow_private_network=False):
            self.allow_private_network = allow_private_network
            return HostPolicy(
                test_only_allowed_hosts=frozenset({"127.0.0.1"})
            ).resolve(url)

    policy = RecordingPolicy()
    result = HttpExecutor(
        _CaseService(_case()),
        Environment(),
        host_policy=policy,
    ).execute_case("case-version-1", "environment-revision-1", {})

    assert result.status == "PASSED"
    assert policy.allow_private_network is True


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
        "server-upload-access-id",
        "server-upload-policy",
        "server-upload-signature",
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


@pytest.mark.parametrize('stage', ['setup_steps', 'cleanup_steps'])
@pytest.mark.parametrize('value', [None, '', [], {}])
def test_empty_condition_skips_request_without_failing_case(target_server, stage, value):
    step = _workflow_step('有任务才取消', 'GET', '/workflow/setup-fail', required_variables=['missing'])
    step['only_if_variable'] = 'task'
    case = _case(processing=_workflow_processing(**{stage: [step]}))
    target_server[1].workflow_calls = []
    result = _executor(target_server, case).execute_case('v', 'env', {'task': value})
    assert result.status == 'PASSED'
    assert [call[1] for call in target_server[1].workflow_calls] == ['/ok']
    assert any(event.get('failure_category') == 'condition_not_met' for event in result.trace)


@pytest.mark.parametrize('value', ['task-1', 0, False])
def test_nonempty_condition_preserves_failure_and_blocks_main(target_server, value):
    step = _workflow_step('有任务才取消', 'GET', '/workflow/setup-fail', assertions=[_code_assertion()])
    step['only_if_variable'] = 'task'
    case = _case(processing=_workflow_processing(setup_steps=[step]))
    target_server[1].workflow_calls = []
    result = _executor(target_server, case).execute_case('v', 'env', {'task': value})
    assert result.status == 'FAILED'
    assert [call[1] for call in target_server[1].workflow_calls] == ['/workflow/setup-fail']


def test_preview_conditional_target_skipped_and_later_target_runs(target_server):
    step = _workflow_step('有任务才取消', 'GET', '/workflow/setup-fail')
    step['only_if_variable'] = 'task'
    executor = _executor(target_server, _case())
    target_server[1].workflow_calls = []
    result = executor.preview_setup_steps('env', [step], 0)
    assert result['status'] == 'SKIPPED'
    assert result['target_reached'] is True
    assert target_server[1].workflow_calls == []
    result = executor.preview_setup_steps('env', [step, _workflow_step('验证恢复', 'GET', '/ok')], 1)
    assert result['status'] == 'PASSED'
    assert result['target_reached'] is True
    assert [call[1] for call in target_server[1].workflow_calls] == ['/ok']


def test_request_specific_timeout_allows_slow_response_without_retry(target_server):
    case = _case('/slow')
    case.request['timeout_seconds'] = 1
    server = target_server[1]
    before = server.request_count
    result = _executor(target_server, case, limits=ExecutorLimits(timeout_seconds=0.05)).execute_case('case-version-1', 'environment-revision-1', {})
    assert result.status == 'PASSED'
    assert server.request_count == before + 1


def test_cleanup_ownership_failure_never_cancels_unverified_task(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    verify = _workflow_step(
        '确认任务归属', 'GET', '/workflow/ownership-mismatch',
        assertions=[_code_assertion(), {
            'type': 'json_path', 'operator': 'equals', 'path': '$.data.resourceSn',
            'expected': 'owned-resource', 'timeout_ms': 0, 'enabled': True,
        }],
        extractions=[_json_extraction('verifiedTaskSn', '$.data.resourceSn')],
    )
    cancel = _workflow_step(
        '仅取消确认归属的任务', 'POST', '/workflow/cancel',
        body={'taskSn': '{{verifiedTaskSn}}'},
        assertions=[_code_assertion()], required_variables=['verifiedTaskSn'],
    )
    cancel['only_if_variable'] = 'verifiedTaskSn'
    case = _case(processing=_workflow_processing(cleanup_steps=[verify, cancel]))

    result = _executor(target_server, case).execute_case('v', 'env', {})

    assert result.status == 'FAILED'
    assert result.failure_category == 'cleanup'
    assert [call[1] for call in handler.workflow_calls] == ['/ok', '/workflow/ownership-mismatch']
    assert any(event.get('failure_category') == 'cleanup_dependency' for event in result.trace)


def test_owned_record_selector_drives_cleanup_when_other_record_is_first(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    create = _workflow_step('本轮任务', 'GET', '/workflow/setup', assertions=[_code_assertion()], extractions=[_json_extraction('task', '$.data.resourceSn')])
    select = _workflow_step('查找本轮记录', 'GET', '/workflow/owned-list', assertions=[_code_assertion()], extractions=[_json_extraction('record', '$.records[?(@.sn=="{{task}}")].id')], required_variables=['task'])
    cleanup = _workflow_step('清理本轮记录', 'POST', '/workflow/cancel', body={'taskSn': '{{record}}'}, assertions=[_code_assertion()], required_variables=['record'])
    cleanup['only_if_variable'] = 'record'
    case = _case(processing=_workflow_processing(setup_steps=[create, select], cleanup_steps=[cleanup]))
    result = _executor(target_server, case).execute_case('v', 'env', {})
    assert result.status == 'PASSED'
    assert ('POST', '/workflow/cancel', {'taskSn': '34'}) in handler.workflow_calls


@pytest.mark.parametrize('path', ['/workflow/ambiguous-list', '/workflow/missing-list'])
def test_owned_record_selector_does_not_cleanup_when_not_unique(target_server, path):
    _, handler = target_server
    handler.workflow_calls = []
    create = _workflow_step('本轮任务', 'GET', '/workflow/setup', assertions=[_code_assertion()], extractions=[_json_extraction('task', '$.data.resourceSn')])
    select = _workflow_step('查找本轮记录', 'GET', path, assertions=[_code_assertion()], extractions=[_json_extraction('record', '$.records[?(@.sn=="{{task}}")].id')], required_variables=['task'])
    cleanup = _workflow_step('清理本轮记录', 'POST', '/workflow/cancel', body={'taskSn': '{{record}}'}, assertions=[_code_assertion()], required_variables=['record'])
    cleanup['only_if_variable'] = 'record'
    result = _executor(target_server, _case(processing=_workflow_processing(setup_steps=[create, select], cleanup_steps=[cleanup]))).execute_case('v', 'env', {})
    assert result.status != 'PASSED'
    assert all(call[1] != '/workflow/cancel' for call in handler.workflow_calls)

@pytest.mark.parametrize('outcome', ['failed', 'skipped', 'missing_optional'])
def test_cleanup_verification_never_reuses_earlier_output_when_no_fresh_value(target_server, outcome):
    _, handler = target_server
    handler.workflow_calls = []
    verify = _workflow_step(
        '确认资源归属', 'GET', '/workflow/ownership-mismatch',
        assertions=[_code_assertion(), {'type': 'json_path', 'operator': 'equals', 'path': '$.data.resourceSn', 'expected': 'owned-resource', 'timeout_ms': 0, 'enabled': True}],
        extractions=[_json_extraction('verifiedTaskSn', '$.data.resourceSn')],
    )
    if outcome == 'skipped':
        verify['only_if_variable'] = 'allowVerify'
    if outcome == 'missing_optional':
        verify['assertions'] = [_code_assertion()]
        verify['extractions'] = [{**_json_extraction('verifiedTaskSn', '$.data.missing'), 'required': False}]
    cancel = _workflow_step('清理已核实任务', 'POST', '/workflow/cancel', body={'taskSn': '{{verifiedTaskSn}}'}, required_variables=['verifiedTaskSn'])
    cancel['only_if_variable'] = 'verifiedTaskSn'
    case = _case(processing=_workflow_processing(cleanup_steps=[verify, cancel]))

    result = _executor(target_server, case).execute_case('v', 'env', {'verifiedTaskSn': 'another-run'})

    assert result.status == 'FAILED'
    assert any(event.get('failure_category') == 'cleanup_dependency' for event in result.trace)
    assert not any(call[1] == '/workflow/cancel' for call in handler.workflow_calls)


@pytest.mark.parametrize(('action', 'initial'), [('copy_variable', 'another-run'), ('json_encode', 'another-run'), ('json_decode', '"another-run"')])
def test_failed_cleanup_verification_invalidates_old_preprocessing_aliases(target_server, action, initial):
    _, handler = target_server
    handler.workflow_calls = []
    verify = _workflow_step('确认资源归属', 'GET', '/workflow/ownership-mismatch', assertions=[_code_assertion(), {'type': 'json_path', 'operator': 'equals', 'path': '$.data.resourceSn', 'expected': 'owned-resource', 'timeout_ms': 0, 'enabled': True}], extractions=[_json_extraction('verifiedTaskSn', '$.data.resourceSn')])
    cancel = _workflow_step('清理已核实任务', 'POST', '/workflow/cancel', body={'taskSn': '{{alias}}'}, required_variables=['alias'])
    cancel['only_if_variable'] = 'alias'
    processing = _workflow_processing(cleanup_steps=[verify, cancel])
    processing['pre'] = [{'action': action, 'source': 'verifiedTaskSn', 'target': 'alias'}]

    result = _executor(target_server, _case(processing=processing)).execute_case('v', 'env', {'verifiedTaskSn': initial})

    assert result.status == 'FAILED'
    assert any(event.get('failure_category') == 'cleanup_dependency' for event in result.trace)
    assert not any(call[1] == '/workflow/cancel' for call in handler.workflow_calls)


def test_successful_cleanup_verification_overrides_stale_value_before_downstream(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    verify = _workflow_step('确认资源归属', 'GET', '/workflow/setup', assertions=[_code_assertion()], extractions=[_json_extraction('verifiedTaskSn', '$.data.resourceSn')])
    cancel = _workflow_step('清理已核实任务', 'POST', '/workflow/cancel', body={'taskSn': '{{verifiedTaskSn}}'}, required_variables=['verifiedTaskSn'])
    cancel['only_if_variable'] = 'verifiedTaskSn'
    result = _executor(target_server, _case(processing=_workflow_processing(cleanup_steps=[verify, cancel]))).execute_case('v', 'env', {'verifiedTaskSn': 'another-run'})
    assert result.status == 'PASSED'
    assert ('POST', '/workflow/cancel', {'taskSn': 'resource-1'}) in handler.workflow_calls


def test_failed_cleanup_without_outputs_does_not_block_independent_cleanup(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    first = _workflow_step('清理失败', 'GET', '/workflow/setup-fail', assertions=[_code_assertion()])
    second = _workflow_step('后续独立清理', 'GET', '/workflow/static-cleanup', assertions=[_code_assertion()])
    result = _executor(target_server, _case(processing=_workflow_processing(cleanup_steps=[first, second]))).execute_case('v', 'env', {})
    assert result.status == 'FAILED'
    assert [call[1] for call in handler.workflow_calls] == ['/ok', '/workflow/setup-fail', '/workflow/static-cleanup']


def test_missing_optional_output_on_last_cleanup_keeps_existing_behavior(target_server):
    last = _workflow_step('最后清理', 'GET', '/workflow/ownership-mismatch', assertions=[_code_assertion()], extractions=[{**_json_extraction('optional', '$.data.missing'), 'required': False}])
    result = _executor(target_server, _case(processing=_workflow_processing(cleanup_steps=[last]))).execute_case('v', 'env', {})
    assert result.status == 'PASSED'


def test_missing_cleanup_prerequisite_blocks_subsequent_cleanup(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    verify = _workflow_step('核查资源', 'GET', '/workflow/setup', required_variables=['needed'], extractions=[_json_extraction('verifiedTaskSn', '$.data.resourceSn')])
    cancel = _workflow_step('清理', 'POST', '/workflow/cancel', body={'taskSn': '{{verifiedTaskSn}}'})
    result = _executor(target_server, _case(processing=_workflow_processing(cleanup_steps=[verify, cancel]))).execute_case('v', 'env', {'verifiedTaskSn': 'another-run'})
    assert result.status == 'FAILED'
    assert [call[1] for call in handler.workflow_calls] == ['/ok']
    assert any(event.get('failure_category') == 'cleanup_dependency' for event in result.trace)


def test_failed_cleanup_verification_blocks_environment_fallback_alias(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    verify = _workflow_step('核查资源', 'GET', '/workflow/ownership-mismatch', assertions=[{'type': 'json_path', 'operator': 'equals', 'path': '$.data.resourceSn', 'expected': 'owned-resource', 'timeout_ms': 0, 'enabled': True}], extractions=[_json_extraction('verifiedTaskSn', '$.data.resourceSn')])
    cancel = _workflow_step('清理', 'POST', '/workflow/cancel', body={'taskSn': '{{environmentAlias}}'})
    case = _case(processing=_workflow_processing(cleanup_steps=[verify, cancel]))
    result = _executor(target_server, case, values={'environmentAlias': 'another-run'}).execute_case('v', 'env', {})
    assert result.status == 'FAILED'
    assert [call[1] for call in handler.workflow_calls] == ['/ok', '/workflow/ownership-mismatch']
    assert any(event.get('failure_category') == 'cleanup_dependency' for event in result.trace)


@pytest.mark.parametrize('valid_value', [0, False])
def test_cleanup_output_zero_and_false_are_not_treated_as_missing(valid_value):
    step = {'extractions': [{'target': 'out'}]}
    outcome = SimpleNamespace(status='PASSED', extracted={'out': valid_value}, response_outputs=frozenset({'out'}))
    assert HttpExecutor._cleanup_outputs_available(step, outcome)


def test_cleanup_verification_rejects_default_as_proof_of_response(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    verification = _workflow_step(
        '核查归属', 'GET', '/workflow/ownership-mismatch', assertions=[_code_assertion()],
        extractions=[{**_json_extraction('verifiedTaskSn', '$.data.absent'), 'required': False, 'default': 'another-run'}],
    )
    cancel = _workflow_step('取消任务', 'POST', '/workflow/cancel', body={'taskSn': '{{verifiedTaskSn}}'})
    result = _executor(target_server, _case(processing=_workflow_processing(cleanup_steps=[verification, cancel]))).execute_case('v', 'env', {})
    assert result.status == 'FAILED'
    assert any(event.get('failure_category') == 'cleanup_dependency' for event in result.trace)
    assert [call[1] for call in handler.workflow_calls] == ['/ok', '/workflow/ownership-mismatch']


@pytest.mark.parametrize(('action', 'initial'), [('copy_variable', 'another-run'), ('json_encode', 'another-run'), ('json_decode', '"another-run"')])
def test_successful_cleanup_verification_rejects_old_preprocessing_alias(target_server, action, initial):
    _, handler = target_server
    handler.workflow_calls = []
    verification = _workflow_step('核查归属', 'GET', '/workflow/setup', assertions=[_code_assertion()], extractions=[_json_extraction('verifiedTaskSn', '$.data.resourceSn')])
    cancel = _workflow_step('取消任务', 'POST', '/workflow/cancel', body={'taskSn': '{{oldAlias}}'})
    processing = _workflow_processing(cleanup_steps=[verification, cancel])
    processing['pre'] = [{'action': action, 'source': 'verifiedTaskSn', 'target': 'oldAlias'}]
    result = _executor(target_server, _case(processing=processing)).execute_case('v', 'env', {'verifiedTaskSn': initial})
    assert result.status == 'FAILED'
    assert any(event.get('failure_category') == 'cleanup_dependency' for event in result.trace)
    assert [call[1] for call in handler.workflow_calls] == ['/ok', '/workflow/setup']


def test_cleanup_verification_accepts_actual_response_even_with_optional_default(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    verification = _workflow_step('核查归属', 'GET', '/workflow/setup', assertions=[_code_assertion()], extractions=[{**_json_extraction('verifiedTaskSn', '$.data.resourceSn'), 'required': False, 'default': 'another-run'}])
    cancel = _workflow_step('取消任务', 'POST', '/workflow/cancel', body={'taskSn': '{{verifiedTaskSn}}'})
    result = _executor(target_server, _case(processing=_workflow_processing(cleanup_steps=[verification, cancel]))).execute_case('v', 'env', {})
    assert result.status == 'PASSED'
    assert ('POST', '/workflow/cancel', {'taskSn': 'resource-1'}) in handler.workflow_calls


def test_cleanup_verification_rejects_explicit_json_null_without_using_default(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    verification = _workflow_step('核查归属', 'GET', '/nullable', assertions=[_code_assertion()], extractions=[{**_json_extraction('verifiedTaskSn', '$.data'), 'required': False, 'default': 'another-run'}])
    cancel = _workflow_step('取消任务', 'POST', '/workflow/cancel', body={'taskSn': '{{verifiedTaskSn}}'})
    result = _executor(target_server, _case(processing=_workflow_processing(cleanup_steps=[verification, cancel]))).execute_case('v', 'env', {})
    assert result.status == 'FAILED'
    assert [call[1] for call in handler.workflow_calls] == ['/ok', '/nullable']
    assert any(event.get('failure_category') == 'cleanup_dependency' for event in result.trace)


def test_cleanup_verification_rejects_multiple_hops_of_stale_aliases(target_server):
    _, handler = target_server
    handler.workflow_calls = []
    verification = _workflow_step('核查归属', 'GET', '/workflow/setup', assertions=[_code_assertion()], extractions=[_json_extraction('verifiedTaskSn', '$.data.resourceSn')])
    cancel = _workflow_step('取消任务', 'POST', '/workflow/cancel', body={'taskSn': '{{lastAlias}}'})
    processing = _workflow_processing(cleanup_steps=[verification, cancel])
    processing['pre'] = [
        {'action': 'copy_variable', 'source': 'verifiedTaskSn', 'target': 'firstAlias'},
        {'action': 'json_encode', 'source': 'firstAlias', 'target': 'encodedAlias'},
        {'action': 'json_decode', 'source': 'encodedAlias', 'target': 'lastAlias'},
        {'action': 'remove_variable', 'name': 'firstAlias'},
        {'action': 'remove_variable', 'name': 'encodedAlias'},
    ]
    result = _executor(target_server, _case(processing=processing)).execute_case('v', 'env', {'verifiedTaskSn': 'another-run'})
    assert result.status == 'FAILED'
    assert [call[1] for call in handler.workflow_calls] == ['/ok', '/workflow/setup']
    assert any(event.get('failure_category') == 'cleanup_dependency' for event in result.trace)

@pytest.mark.parametrize('required_dependency', [True, False])
@pytest.mark.parametrize('source', ['default', 'post_override'])
@pytest.mark.parametrize('stage', ['setup', 'main', 'cleanup'])
def test_dependency_untrusted_output_never_reaches_requests(target_server, required_dependency, source, stage):
    _, handler = target_server
    extraction = _extraction('resourceSn', 'json_path', '$.data.resourceSn', required=source != 'default')
    extraction.default = 'another-run'
    processing = _workflow_processing()
    if source == 'post_override':
        processing['post'] = [{'action': 'set_variable', 'name': 'resourceSn', 'value': 'another-run'}]
    upstream = _case('/ok' if source == 'default' else '/workflow/setup', extractions=[extraction], processing=processing)
    result = _executor(target_server, upstream).execute_case('upstream', 'env', {})
    assert result.status == 'PASSED'
    assert result.extracted_variables['resourceSn'] == 'another-run'
    outcome = ExecutionService._dependency_outcome(result, {'resourceSn'})
    snapshot = {'dependencies': [{'case_version_id': 'upstream', 'required': required_dependency, 'exports': ['resourceSn']}]}
    overrides, blocked = ExecutionService._dependency_overrides(snapshot, {'upstream': outcome})
    assert not blocked
    unavailable = ExecutionService._unavailable_dependency_exports(snapshot, {'upstream': outcome})
    operation = _workflow_step('危险请求', 'POST', '/workflow/cancel', body={'target': '{{resourceSn}}'})
    processing = _workflow_processing(
        setup_steps=[operation] if stage == 'setup' else [],
        cleanup_steps=[operation] if stage == 'cleanup' else [],
    )
    downstream = _case('/workflow/cancel' if stage == 'main' else '/ok',
        method='POST' if stage == 'main' else 'GET',
        body={'target': '{{resourceSn}}'} if stage == 'main' else None,
        processing=processing)
    handler.workflow_calls = []
    _executor(target_server, downstream).execute_case('downstream', 'env', {},
        dependency_overrides=overrides, unavailable_dependency_exports=unavailable)
    assert not any(call[1] == '/workflow/cancel' for call in handler.workflow_calls)


def test_dependency_untrusted_output_does_not_block_unrelated_cleanup(target_server):
    _, handler = target_server
    extraction = _extraction('resourceSn', 'json_path', '$.data.resourceSn', required=False)
    extraction.default = 'another-run'
    upstream = _executor(target_server, _case('/ok', extractions=[extraction])).execute_case('upstream', 'env', {})
    outcome = ExecutionService._dependency_outcome(upstream, {'resourceSn'})
    snapshot = {'dependencies': [{'case_version_id': 'upstream', 'required': False, 'exports': ['resourceSn']}]}
    overrides, _ = ExecutionService._dependency_overrides(snapshot, {'upstream': outcome})
    unavailable = ExecutionService._unavailable_dependency_exports(snapshot, {'upstream': outcome})
    unrelated = _workflow_step('独立清理', 'POST', '/workflow/cancel', body={'target': 'fixed-independent-target'})
    handler.workflow_calls = []
    downstream = _case('/ok', processing=_workflow_processing(cleanup_steps=[unrelated]))
    result = _executor(target_server, downstream).execute_case('downstream', 'env', {},
        dependency_overrides=overrides, unavailable_dependency_exports=unavailable)
    assert result.status == 'PASSED'
    assert ('POST', '/workflow/cancel', {'target': 'fixed-independent-target'}) in handler.workflow_calls

@pytest.mark.parametrize('action', ['copy_variable', 'json_encode', 'json_decode'])
def test_untrusted_dependency_cannot_escape_through_pre_alias(target_server, action):
    _, handler = target_server
    default = '"another-run"' if action == 'json_decode' else 'another-run'
    extraction = _extraction('resourceSn', 'json_path', '$.data.resourceSn', required=False)
    extraction.default = default
    upstream = _executor(target_server, _case('/ok', extractions=[extraction])).execute_case('upstream', 'env', {})
    outcome = ExecutionService._dependency_outcome(upstream, {'resourceSn'})
    snapshot = {'dependencies': [{'case_version_id': 'upstream', 'required': False, 'exports': ['resourceSn']}]}
    overrides, _ = ExecutionService._dependency_overrides(snapshot, {'upstream': outcome})
    unavailable = ExecutionService._unavailable_dependency_exports(snapshot, {'upstream': outcome})
    processing = _workflow_processing(cleanup_steps=[_workflow_step('清理', 'POST', '/workflow/cancel', body={'target': '{{alias}}'})])
    processing['pre'] = [{'action': action, 'source': 'resourceSn', 'target': 'alias'}]
    handler.workflow_calls = []
    _executor(target_server, _case('/ok', processing=processing)).execute_case('downstream', 'env', {}, dependency_overrides=overrides, unavailable_dependency_exports=unavailable)
    assert not any(call[1] == '/workflow/cancel' for call in handler.workflow_calls)


def test_old_dependency_outcome_with_unknown_provenance_cannot_authorize_cleanup(target_server):
    _, handler = target_server
    old = SimpleNamespace(status='PASSED', extracted_variables={'resourceSn': 'another-run'})
    snapshot = {'dependencies': [{'case_version_id': 'upstream', 'required': True, 'exports': ['resourceSn']}]}
    overrides, _ = ExecutionService._dependency_overrides(snapshot, {'upstream': old})
    unavailable = ExecutionService._unavailable_dependency_exports(snapshot, {'upstream': old})
    cleanup = _workflow_step('清理', 'POST', '/workflow/cancel', body={'target': '{{resourceSn}}'})
    handler.workflow_calls = []
    _executor(target_server, _case('/ok', processing=_workflow_processing(cleanup_steps=[cleanup]))).execute_case('downstream', 'env', {}, dependency_overrides=overrides, unavailable_dependency_exports=unavailable)
    assert not any(call[1] == '/workflow/cancel' for call in handler.workflow_calls)


def test_environment_placeholder_alias_preserves_request_dependency():
    from task_server.api_testing.services.environment_service import _PlaceholderResolver
    resolver = _PlaceholderResolver({'alias': {'inner': '{{resourceSn}}'}, 'resourceSn': 'another-run'})
    assert resolver.referenced_variables({'body': '{{alias}}'}) == {'alias', 'resourceSn'}


def test_later_default_overwrites_actual_extraction_provenance(target_server):
    _, handler = target_server
    first = _extraction('resourceSn', 'json_path', '$.data.resourceSn')
    second = _extraction('resourceSn', 'json_path', '$.data.missing', required=False)
    second.default = 'another-run'
    upstream = _executor(target_server, _case('/workflow/setup', extractions=[first, second])).execute_case('upstream', 'env', {})
    assert upstream.extracted_variables['resourceSn'] == 'another-run'
    outcome = ExecutionService._dependency_outcome(upstream, {'resourceSn'})
    snapshot = {'dependencies': [{'case_version_id': 'upstream', 'required': True, 'exports': ['resourceSn']}]}
    overrides, _ = ExecutionService._dependency_overrides(snapshot, {'upstream': outcome})
    unavailable = ExecutionService._unavailable_dependency_exports(snapshot, {'upstream': outcome})
    cleanup = _workflow_step('清理', 'POST', '/workflow/cancel', body={'target': '{{resourceSn}}'})
    handler.workflow_calls = []
    _executor(target_server, _case('/ok', processing=_workflow_processing(cleanup_steps=[cleanup]))).execute_case('downstream', 'env', {}, dependency_overrides=overrides, unavailable_dependency_exports=unavailable)
    assert not any(call[1] == '/workflow/cancel' for call in handler.workflow_calls)


def test_untrusted_dependency_cannot_control_conditional_cleanup(target_server):
    _, handler = target_server
    operation = _workflow_step('有条件清理', 'POST', '/workflow/cancel', body={'target': 'fixed-target'})
    operation['only_if_variable'] = 'resourceSn'
    handler.workflow_calls = []
    _executor(target_server, _case('/ok', processing=_workflow_processing(cleanup_steps=[operation]))).execute_case(
        'downstream', 'env', {}, dependency_overrides={'resourceSn': 'another-run'}, unavailable_dependency_exports={'resourceSn'})
    assert not any(call[1] == '/workflow/cancel' for call in handler.workflow_calls)


def test_actual_optional_response_output_can_flow_across_two_dependencies(target_server):
    _, handler = target_server
    def dependency(version, result, name):
        outcome = ExecutionService._dependency_outcome(result, {name})
        snapshot = {'dependencies': [{'case_version_id': version, 'required': False, 'exports': [name]}]}
        overrides, blocked = ExecutionService._dependency_overrides(snapshot, {version: outcome})
        assert not blocked
        return overrides, ExecutionService._unavailable_dependency_exports(snapshot, {version: outcome})
    first = _extraction('resourceSn', 'json_path', '$.data.resourceSn', required=False)
    a = _executor(target_server, _case('/workflow/setup', extractions=[first])).execute_case('a', 'env', {})
    overrides, unavailable = dependency('a', a, 'resourceSn')
    assert not unavailable
    second = _extraction('otherSn', 'json_path', '$.data.resourceSn')
    b = _executor(target_server, _case('/workflow/setup', extractions=[second])).execute_case('b', 'env', {}, dependency_overrides=overrides, unavailable_dependency_exports=unavailable)
    overrides, unavailable = dependency('b', b, 'otherSn')
    assert not unavailable
    cleanup = _workflow_step('清理', 'POST', '/workflow/cancel', body={'target': '{{otherSn}}'})
    handler.workflow_calls = []
    result = _executor(target_server, _case('/ok', processing=_workflow_processing(cleanup_steps=[cleanup]))).execute_case('c', 'env', {}, dependency_overrides=overrides, unavailable_dependency_exports=unavailable)
    assert result.status == 'PASSED'
    assert ('POST', '/workflow/cancel', {'target': 'resource-1'}) in handler.workflow_calls


def test_untrusted_dependency_cannot_drive_extraction_selector(target_server):
    _, handler = target_server
    extraction = _extraction('selectedId', 'json_path', '$.records[{{itemIndex}}].id')
    handler.workflow_calls = []
    result = _executor(target_server, _case('/workflow/owned-list', extractions=[extraction])).execute_case(
        'middle', 'env', {}, dependency_overrides={'itemIndex': '0'}, unavailable_dependency_exports={'itemIndex'})
    assert result.status != 'PASSED'
    assert not any(call[1] == '/workflow/owned-list' for call in handler.workflow_calls)


def test_untrusted_dependency_is_blocked_through_default_header_alias(target_server):
    from task_server.api_testing.contracts.environment import ResolvedEnvironment
    from task_server.api_testing.services.environment_service import _PlaceholderResolver
    base_url, handler = target_server
    class EnvironmentWithHeaderAlias:
        def resolve_runtime(self, _revision, overrides, service_name=None):
            resolver = _PlaceholderResolver({'resourceSn': overrides['resourceSn'], 'nested': '{{resourceSn}}'})
            headers = {'X-Resource': '{{nested}}'}
            return ResolvedEnvironment(
                revision_id='env', environment_id='env', name='test', base_urls={'default': base_url},
                public_variables=resolver.resolve_all(), secrets={}, headers=resolver.render(headers),
                service_metadata={}, unresolved_services=(), _renderer=resolver.render,
                _reference_finder=resolver.referenced_variables,
                default_header_references=frozenset(resolver.referenced_variables(headers)),
            )
    executor = _executor(target_server, _case('/ok'))
    executor.environment_service = EnvironmentWithHeaderAlias()
    handler.workflow_calls = []
    result = executor.execute_case('case', 'env', {}, dependency_overrides={'resourceSn': 'another-run'}, unavailable_dependency_exports={'resourceSn'})
    assert result.status == 'BROKEN'
    assert not handler.workflow_calls


def test_untrusted_middle_dependency_stays_blocked_across_three_cases(target_server):
    _, handler = target_server
    fallback = _extraction('resourceSn', 'json_path', '$.data.resourceSn', required=False)
    fallback.default = 'another-run'
    first = _executor(target_server, _case('/ok', extractions=[fallback])).execute_case('a', 'env', {})
    outcomes = {'a': ExecutionService._dependency_outcome(first, {'resourceSn'})}
    middle_snapshot = {'dependencies': [{'case_version_id': 'a', 'required': True, 'exports': ['resourceSn']}]}
    overrides, _ = ExecutionService._dependency_overrides(middle_snapshot, outcomes)
    unavailable = ExecutionService._unavailable_dependency_exports(middle_snapshot, outcomes)
    middle = _case('/workflow/cancel', method='POST', body={'target': '{{resourceSn}}'})
    handler.workflow_calls = []
    result = _executor(target_server, middle).execute_case('b', 'env', {}, dependency_overrides=overrides, unavailable_dependency_exports=unavailable)
    assert result.status == 'BROKEN'
    assert not handler.workflow_calls
    outcomes['b'] = ExecutionService._dependency_outcome(result, {'resourceSn'})
    last_snapshot = {'dependencies': [{'case_version_id': 'b', 'required': True, 'exports': ['resourceSn']}]}
    _, blocked = ExecutionService._dependency_overrides(last_snapshot, outcomes)
    assert blocked


def test_cleanup_fresh_output_replaces_failed_main_extraction_for_later_cleanup(target_server):
    _, handler = target_server
    verify = _workflow_step('重新核查', 'GET', '/workflow/setup', extractions=[_json_extraction('resourceSn', '$.data.resourceSn')])
    cleanup = _workflow_step('清理', 'POST', '/workflow/cancel', body={'target': '{{resourceSn}}'}, required_variables=['resourceSn'])
    cleanup['only_if_variable'] = 'resourceSn'
    case = _case('/disconnect', extractions=[_extraction('resourceSn', 'json_path', '$.data.resourceSn')], processing=_workflow_processing(cleanup_steps=[verify, cleanup]))
    handler.workflow_calls = []
    result = _executor(target_server, case).execute_case('case', 'env', {'resourceSn': 'another-run'})
    assert result.status == 'BROKEN'
    assert ('POST', '/workflow/cancel', {'target': 'resource-1'}) in handler.workflow_calls


@pytest.mark.parametrize('good_last', [True, False])
def test_conflicting_dependency_provenance_follows_value_overwrite_order(target_server, good_last):
    _, handler = target_server
    bad = SimpleNamespace(status='PASSED', extracted_variables={'resourceSn': 'another-run'}, response_outputs=frozenset())
    good = SimpleNamespace(status='PASSED', extracted_variables={'resourceSn': 'resource-1'}, response_outputs=frozenset({'resourceSn'}))
    ordered = ['bad', 'good'] if good_last else ['good', 'bad']
    snapshot = {'dependencies': [{'case_version_id': name, 'required': True, 'exports': ['resourceSn']} for name in ordered]}
    outcomes = {'good': good, 'bad': bad}
    overrides, blocked = ExecutionService._dependency_overrides(snapshot, outcomes)
    unavailable = ExecutionService._unavailable_dependency_exports(snapshot, outcomes)
    assert not blocked
    cleanup = _workflow_step('清理', 'POST', '/workflow/cancel', body={'target': '{{resourceSn}}'})
    handler.workflow_calls = []
    _executor(target_server, _case('/ok', processing=_workflow_processing(cleanup_steps=[cleanup]))).execute_case('c', 'env', {}, dependency_overrides=overrides, unavailable_dependency_exports=unavailable)
    cancels = [call for call in handler.workflow_calls if call[1] == '/workflow/cancel']
    assert cancels == ([('POST', '/workflow/cancel', {'target': 'resource-1'})] if good_last else [])


def test_failed_cleanup_output_allows_only_literal_independent_followup(target_server):
    _, handler = target_server
    failed = _workflow_step('核查失败', 'GET', '/workflow/setup-fail', assertions=[_code_assertion()], extractions=[_json_extraction('verified', '$.data.id')])
    independent = _workflow_step('独立清理', 'GET', '/workflow/static-cleanup', assertions=[_code_assertion()])
    unsafe = _workflow_step('动态别名不可确认', 'POST', '/workflow/cancel', body={'target': '{{environmentAlias}}'})
    case = _case(processing=_workflow_processing(cleanup_steps=[failed, independent, unsafe]))
    handler.workflow_calls = []
    result = _executor(target_server, case, values={'environmentAlias': 'another-run'}).execute_case('v', 'env', {})
    assert result.status == 'FAILED'
    assert [call[1] for call in handler.workflow_calls] == ['/ok', '/workflow/setup-fail', '/workflow/static-cleanup']
    assert any(event.get('failure_category') == 'cleanup_dependency' for event in result.trace)


@pytest.mark.parametrize("input_id", ["existing-resource", "another-run"])
def test_existing_resource_input_same_as_response_target_remains_allowed_without_ownership_contract(target_server, input_id):
    _, handler = target_server
    case = _case('/workflow/cancel', method='POST', body={'target': '{{resourceSn}}'}, extractions=[_extraction('resourceSn', 'json_path', '$.data.cancelled')])
    handler.workflow_calls = []
    result = _executor(target_server, case).execute_case('v', 'env', {'resourceSn': input_id})
    assert result.status == 'PASSED'
    assert ('POST', '/workflow/cancel', {'target': input_id}) in handler.workflow_calls


def test_failed_main_output_does_not_authorize_unrelated_environment_alias(target_server):
    _, handler = target_server
    independent = _workflow_step('独立清理', 'GET', '/workflow/static-cleanup')
    cancel = _workflow_step('动态别名不可确认', 'POST', '/workflow/cancel', body={'target': '{{environmentAlias}}'})
    case = _case('/disconnect', extractions=[_extraction('resourceSn', 'json_path', '$.data.resourceSn')], processing=_workflow_processing(cleanup_steps=[independent, cancel]))
    handler.workflow_calls = []
    result = _executor(target_server, case, values={'environmentAlias': 'another-run'}).execute_case('v', 'env', {})
    assert result.status == 'BROKEN'
    assert [call[1] for call in handler.workflow_calls] == ['/disconnect', '/workflow/static-cleanup']


def test_pre_alias_of_later_cleanup_output_cannot_drive_earlier_cleanup(target_server):
    _, handler = target_server
    cancel = _workflow_step('旧别名', 'POST', '/workflow/cancel', body={'target': '{{oldAlias}}'})
    verify = _workflow_step('稍后核查', 'GET', '/workflow/setup', extractions=[_json_extraction('resourceSn', '$.data.resourceSn')])
    processing = _workflow_processing(cleanup_steps=[cancel, verify])
    processing['pre'] = [{'action': 'copy_variable', 'source': 'resourceSn', 'target': 'oldAlias'}]
    handler.workflow_calls = []
    _executor(target_server, _case('/ok', processing=processing)).execute_case('v', 'env', {'resourceSn': 'another-run'})
    assert not any(call[1] == '/workflow/cancel' for call in handler.workflow_calls)


def test_stale_pre_alias_of_fresh_cleanup_output_keeps_unrelated_dynamic_alias_blocked(target_server):
    _, handler = target_server
    verify = _workflow_step('核查', 'GET', '/workflow/setup', extractions=[_json_extraction('resourceSn', '$.data.resourceSn')])
    cancel = _workflow_step('环境别名', 'POST', '/workflow/cancel', body={'target': '{{environmentAlias}}'})
    processing = _workflow_processing(cleanup_steps=[verify, cancel])
    processing['pre'] = [{'action': 'copy_variable', 'source': 'resourceSn', 'target': 'oldAlias'}]
    handler.workflow_calls = []
    _executor(target_server, _case('/ok', processing=processing), values={'environmentAlias': 'another-run'}).execute_case('v', 'env', {'resourceSn': 'another-run'})
    assert not any(call[1] == '/workflow/cancel' for call in handler.workflow_calls)


@pytest.mark.parametrize('missing_last', [True, False])
def test_missing_optional_dependency_does_not_override_earlier_actual_export(missing_last):
    good = SimpleNamespace(status='PASSED', extracted_variables={'resourceSn': 'resource-1'}, response_outputs=frozenset({'resourceSn'}))
    failed = SimpleNamespace(status='FAILED', extracted_variables={}, response_outputs=frozenset())
    names = ['good', 'missing'] if missing_last else ['missing', 'good']
    snapshot = {'dependencies': [{'case_version_id': name, 'required': False, 'exports': ['resourceSn']} for name in names]}
    outcomes = {'good': good, 'missing': failed}
    values, blocked = ExecutionService._dependency_overrides(snapshot, outcomes)
    assert not blocked and values == {'resourceSn': 'resource-1'}
    assert ExecutionService._unavailable_dependency_exports(snapshot, outcomes) == set()


def test_unscoped_environment_alias_of_future_cleanup_output_is_not_yet_guarded(target_server):
    from task_server.api_testing.contracts.environment import ResolvedEnvironment
    from task_server.api_testing.services.environment_service import _PlaceholderResolver
    base_url, handler = target_server
    class NestedEnvironment:
        def resolve_runtime(self, _revision, overrides, service_name=None):
            resolver = _PlaceholderResolver({'environmentAlias': '{{resourceSn}}', **overrides})
            return ResolvedEnvironment(
                revision_id='env', environment_id='env', name='test', base_urls={'default': base_url},
                public_variables=resolver.resolve_all(), secrets={}, headers={}, service_metadata={},
                unresolved_services=(), _renderer=resolver.render,
                _reference_finder=resolver.referenced_variables,
                service_base_url_references={'default': frozenset()},
            )
    cancel = _workflow_step('先操作别名', 'POST', '/workflow/cancel', body={'target': '{{environmentAlias}}'})
    verify = _workflow_step('稍后取得输出', 'GET', '/workflow/setup', extractions=[_json_extraction('resourceSn', '$.data.resourceSn')])
    executor = _executor(target_server, _case('/ok', processing=_workflow_processing(cleanup_steps=[cancel, verify])))
    executor.environment_service = NestedEnvironment()
    handler.workflow_calls = []
    executor.execute_case('v', 'env', {'resourceSn': 'another-run'})
    assert not any(call[1] == '/workflow/cancel' for call in handler.workflow_calls)

@pytest.mark.parametrize('producer', ['main', 'earlier_cleanup'])
def test_future_cleanup_output_allows_previously_produced_response_value(target_server, producer):
    from task_server.api_testing.contracts.environment import ResolvedEnvironment
    from task_server.api_testing.services.environment_service import _PlaceholderResolver

    base_url, handler = target_server
    class NestedEnvironment:
        def resolve_runtime(self, _revision, overrides, service_name=None):
            resolver = _PlaceholderResolver({'environmentAlias': '{{resourceSn}}', **overrides})
            return ResolvedEnvironment(
                revision_id='env', environment_id='env', name='test', base_urls={'default': base_url},
                public_variables=resolver.resolve_all(), secrets={}, headers={}, service_metadata={},
                unresolved_services=(), _renderer=resolver.render,
                _reference_finder=resolver.referenced_variables,
                service_base_url_references={'default': frozenset()},
            )
    extraction = _json_extraction('resourceSn', '$.data.resourceSn')
    cancel = _workflow_step('使用已有本轮响应', 'POST', '/workflow/cancel', body={'target': '{{environmentAlias}}'})
    later = _workflow_step('后续再次读取', 'GET', '/workflow/setup', extractions=[extraction])
    steps = [cancel, later]
    main_extractions = []
    main_path = '/ok'
    if producer == 'main':
        main_path = '/workflow/setup'
        main_extractions = [_extraction('resourceSn', 'json_path', '$.data.resourceSn')]
    else:
        steps.insert(0, _workflow_step('先读取本轮响应', 'GET', '/workflow/setup', extractions=[extraction]))
    executor = _executor(target_server, _case(main_path, extractions=main_extractions, processing=_workflow_processing(cleanup_steps=steps)))
    executor.environment_service = NestedEnvironment()
    handler.workflow_calls = []
    result = executor.execute_case('v', 'env', {'resourceSn': 'another-run'})
    assert result.status == 'PASSED'
    assert ('POST', '/workflow/cancel', {'target': 'resource-1'}) in handler.workflow_calls


def test_future_cleanup_output_cannot_trust_setup_default_overwriting_earlier_response(target_server):
    _, handler = target_server
    fresh = _workflow_step('本轮输出', 'GET', '/workflow/setup', extractions=[_json_extraction('resourceSn', '$.data.resourceSn')])
    defaulted = _workflow_step('缺失时默认旧值', 'GET', '/ok', extractions=[{**_json_extraction('resourceSn', '$.data.missing'), 'required': False, 'default': 'another-run'}])
    cancel = _workflow_step('错误清理', 'POST', '/workflow/cancel', body={'target': '{{resourceSn}}'})
    later = _workflow_step('后续重新读取', 'GET', '/workflow/setup', extractions=[_json_extraction('resourceSn', '$.data.resourceSn')])
    case = _case('/ok', processing=_workflow_processing(setup_steps=[fresh, defaulted], cleanup_steps=[cancel, later]))
    handler.workflow_calls = []
    _executor(target_server, case).execute_case('v', 'env', {})
    assert not any(call[1] == '/workflow/cancel' for call in handler.workflow_calls)


@pytest.mark.parametrize('control', ['only_if_variable', 'required_variables'])
@pytest.mark.parametrize('use_alias', [False, True])
def test_future_cleanup_output_cannot_authorize_earlier_control(target_server, control, use_alias):
    _, handler = target_server
    first = _workflow_step('旧条件触发清理', 'POST', '/workflow/cancel', body={'target': 'unrelated-fixed-id'})
    control_name = 'oldAlias' if use_alias else 'resourceSn'
    first[control] = [control_name] if control == 'required_variables' else control_name
    later = _workflow_step('之后才获得输出', 'GET', '/workflow/setup', extractions=[_json_extraction('resourceSn', '$.data.resourceSn')])
    processing = _workflow_processing(cleanup_steps=[first, later])
    if use_alias:
        processing['pre'] = [{'action': 'copy_variable', 'source': 'resourceSn', 'target': 'oldAlias'}]
    handler.workflow_calls = []
    result = _executor(target_server, _case('/ok', processing=processing)).execute_case('v', 'env', {'resourceSn': 'another-run'})
    assert result.status == 'FAILED'
    assert not any(call[1] == '/workflow/cancel' for call in handler.workflow_calls)


def test_missing_unrelated_optional_cleanup_output_does_not_block_owned_cleanup(target_server):
    _, handler = target_server
    optional = {**_json_extraction('unusedOptional', '$.data.nonexistent'), 'required': False, 'default': None}
    first = _workflow_step('不相关可选提取', 'GET', '/workflow/static-cleanup', extractions=[optional])
    second = _workflow_step('清理响应中获得的资源', 'POST', '/workflow/cancel', body={'target': '{{resourceSn}}'}, required_variables=['resourceSn'])
    case = _case('/workflow/setup', extractions=[_extraction('resourceSn', 'json_path', '$.data.resourceSn')], processing=_workflow_processing(cleanup_steps=[first, second]))
    handler.workflow_calls = []
    result = _executor(target_server, case).execute_case('v', 'env', {})
    assert result.status == 'PASSED'
    assert ('POST', '/workflow/cancel', {'target': 'resource-1'}) in handler.workflow_calls


@pytest.mark.parametrize('source', ['unavailable_dependency', 'fresh_main'])
def test_configured_service_url_cannot_use_stale_same_name_output(target_server, source):
    from task_server.api_testing.contracts.environment import ResolvedEnvironment
    from task_server.api_testing.services.environment_service import _PlaceholderResolver
    base_url, handler = target_server
    class ConfiguredService:
        def resolve_runtime(self, _revision, overrides, service_name=None):
            runtime_resolver = _PlaceholderResolver({'resourceSn': 'another-run', **overrides})
            configured_resolver = _PlaceholderResolver({'resourceSn': 'another-run'})
            runtime = ResolvedEnvironment(
                revision_id='env', environment_id='env', name='test',
                base_urls={'default': base_url, 'danger': configured_resolver.render(base_url + '/workflow/{{resourceSn}}')},
                public_variables=runtime_resolver.resolve_all(), secrets={}, headers={}, service_metadata={},
                unresolved_services=(), _renderer=runtime_resolver.render,
                _reference_finder=runtime_resolver.referenced_variables,
            )
            object.__setattr__(runtime, 'service_base_url_references', {'default': frozenset(), 'danger': frozenset({'resourceSn'})})
            return runtime
    step = _workflow_step('基址携带旧资源', 'POST', 'cancel', body={'operation': 'delete'})
    step['request']['service'] = 'danger'
    case = _case('/workflow/setup' if source == 'fresh_main' else '/ok',
        extractions=[_extraction('resourceSn', 'json_path', '$.data.resourceSn')] if source == 'fresh_main' else [],
        processing=_workflow_processing(cleanup_steps=[step]))
    executor = _executor(target_server, case)
    executor.environment_service = ConfiguredService()
    handler.workflow_calls = []
    result = executor.execute_case('v', 'env', {}, unavailable_dependency_exports={'resourceSn'} if source == 'unavailable_dependency' else ())
    assert result.status == 'FAILED'
    assert not any('/another-run/' in call[1] for call in handler.workflow_calls)


def test_unknown_service_url_provenance_fails_closed_when_output_unavailable(target_server):
    from task_server.api_testing.contracts.environment import ResolvedEnvironment
    from task_server.api_testing.services.environment_service import _PlaceholderResolver
    base_url, handler = target_server
    class UnknownConfiguredService:
        def resolve_runtime(self, _revision, overrides, service_name=None):
            resolver = _PlaceholderResolver({'resourceSn': 'another-run', **overrides})
            return ResolvedEnvironment(revision_id='env', environment_id='env', name='test',
                base_urls={'default': base_url + '/workflow/another-run'}, public_variables=resolver.resolve_all(),
                secrets={}, headers={}, service_metadata={}, unresolved_services=(), _renderer=resolver.render,
                _reference_finder=resolver.referenced_variables)
    step = _workflow_step('基址来源未知', 'POST', 'cancel', body={'operation': 'delete'})
    executor = _executor(target_server, _case('/ok', processing=_workflow_processing(cleanup_steps=[step])))
    executor.environment_service = UnknownConfiguredService()
    handler.workflow_calls = []
    executor.execute_case('v', 'env', {}, unavailable_dependency_exports={'resourceSn'})
    assert not any('/another-run/' in call[1] for call in handler.workflow_calls)


@pytest.mark.parametrize('control', ['only_if_variable', 'required_variables'])
def test_future_cleanup_output_cannot_authorize_nested_override_control(target_server, control):
    _, handler = target_server
    first = _workflow_step('旧别名控制清理', 'POST', '/workflow/cancel', body={'target': 'unrelated-fixed-id'})
    first[control] = ['oldAlias'] if control == 'required_variables' else 'oldAlias'
    later = _workflow_step('之后才获得输出', 'GET', '/workflow/setup', extractions=[_json_extraction('resourceSn', '$.data.resourceSn')])
    handler.workflow_calls = []
    result = _executor(target_server, _case('/ok', processing=_workflow_processing(cleanup_steps=[first, later]))).execute_case('v', 'env', {'resourceSn': 'another-run', 'oldAlias': '{{resourceSn}}'})
    assert result.status == 'FAILED'
    assert not any(call[1] == '/workflow/cancel' for call in handler.workflow_calls)
