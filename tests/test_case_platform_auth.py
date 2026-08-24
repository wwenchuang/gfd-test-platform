import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO

import pytest


class _FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload, ensure_ascii=False).encode("utf-8")


class _FakeOpener:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        return _FakeResponse(self.payloads.pop(0))


def _request_body(request):
    return json.loads((request.data or b"{}").decode("utf-8"))


def test_generate_totp_uses_rfc6238_sha1_vector():
    from task_server.services.case_platform_auth import generate_totp

    assert generate_totp(
        "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
        timestamp=59,
        digits=8,
    ) == "94287082"


def test_required_auth_rejects_missing_credentials(monkeypatch):
    from task_server.services.case_platform_auth import CasePlatformAuthError, CasePlatformClient

    monkeypatch.setenv("CASE_PLATFORM_AUTH_REQUIRED", "true")
    monkeypatch.delenv("CASE_PLATFORM_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("CASE_PLATFORM_USERNAME", raising=False)
    monkeypatch.delenv("CASE_PLATFORM_PASSWORD", raising=False)
    monkeypatch.delenv("CASE_PLATFORM_TOTP_SECRET", raising=False)

    with pytest.raises(CasePlatformAuthError, match="未配置"):
        CasePlatformClient("http://agiletc.test", 5)


def test_access_token_is_attached_without_login(monkeypatch):
    from task_server.services import case_platform_auth

    opener = _FakeOpener([{"code": 200, "data": {"total": 0, "dataSources": []}}])
    monkeypatch.setenv("CASE_PLATFORM_ACCESS_TOKEN", "case-platform-token")
    monkeypatch.setenv("CASE_PLATFORM_TOKEN_HEADER", "X-Case-Token")
    monkeypatch.setenv("CASE_PLATFORM_TOKEN_PREFIX", "")
    monkeypatch.setattr(case_platform_auth.urllib.request, "build_opener", lambda *_args: opener)

    client = case_platform_auth.CasePlatformClient("https://agiletc.test", 5)
    result = client.request_json("/api/case/list", {"pageNum": 1})

    assert result["code"] == 200
    assert len(opener.requests) == 1
    request, timeout = opener.requests[0]
    assert request.get_header("X-case-token") == "case-platform-token"
    assert timeout == 5


def test_totp_login_is_reused_for_multiple_metadata_requests(monkeypatch):
    from task_server.services import case_platform_auth

    opener = _FakeOpener([
        {
            "code": 200,
            "data": {
                "mfaRequired": True,
                "mfaToken": "single-use-mfa-token",
                "username": "report-reader",
            },
        },
        {"code": 200, "data": {"username": "report-reader"}},
        {"code": 200, "data": {"total": 0, "dataSources": []}},
        {"code": 200, "data": {"id": 3088}},
    ])
    monkeypatch.delenv("CASE_PLATFORM_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("CASE_PLATFORM_USERNAME", "report-reader")
    monkeypatch.setenv("CASE_PLATFORM_PASSWORD", "reader-password")
    monkeypatch.setenv("CASE_PLATFORM_TOTP_SECRET", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")
    monkeypatch.setattr(case_platform_auth, "generate_totp", lambda *_args, **_kwargs: "123456")
    monkeypatch.setattr(case_platform_auth.urllib.request, "build_opener", lambda *_args: opener)

    client = case_platform_auth.CasePlatformClient("https://agiletc.test", 5)
    client.request_json("/api/case/list", {"pageNum": 1})
    client.request_json("/api/case/detail", {"caseId": 3088})

    login_requests = [request for request, _timeout in opener.requests if request.full_url.endswith("/api/user/login")]
    verify_requests = [request for request, _timeout in opener.requests if request.full_url.endswith("/api/user/mfa/verify")]
    assert len(login_requests) == 1
    assert _request_body(login_requests[0]) == {
        "username": "report-reader",
        "password": "reader-password",
    }
    assert len(verify_requests) == 1
    assert _request_body(verify_requests[0]) == {
        "mfaToken": "single-use-mfa-token",
        "code": "123456",
    }


def test_expired_cookie_session_logs_in_again_once(monkeypatch):
    from task_server.services import case_platform_auth

    opener = _FakeOpener([
        {"code": 200, "data": {}},
        {"code": 100011, "msg": "权限认证错误"},
        {"code": 200, "data": {}},
        {"code": 200, "data": {"total": 1, "dataSources": []}},
    ])
    monkeypatch.delenv("CASE_PLATFORM_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("CASE_PLATFORM_USERNAME", "report-reader")
    monkeypatch.setenv("CASE_PLATFORM_PASSWORD", "reader-password")
    monkeypatch.setenv("CASE_PLATFORM_TOTP_SECRET", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")
    monkeypatch.setattr(case_platform_auth, "generate_totp", lambda *_args, **_kwargs: "123456")
    monkeypatch.setattr(case_platform_auth.urllib.request, "build_opener", lambda *_args: opener)

    client = case_platform_auth.CasePlatformClient("https://agiletc.test", 5)
    result = client.request_json("/api/case/list", {"pageNum": 1})

    assert result["data"]["total"] == 1
    login_requests = [request for request, _timeout in opener.requests if request.full_url.endswith("/api/user/login")]
    metadata_requests = [request for request, _timeout in opener.requests if "/api/case/list" in request.full_url]
    assert len(login_requests) == 2
    assert len(metadata_requests) == 2


def test_login_error_does_not_expose_credentials(monkeypatch):
    from task_server.services import case_platform_auth

    opener = _FakeOpener([{
        "code": 10400,
        "msg": "report-reader reader-password GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ invalid",
    }])
    monkeypatch.delenv("CASE_PLATFORM_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("CASE_PLATFORM_USERNAME", "report-reader")
    monkeypatch.setenv("CASE_PLATFORM_PASSWORD", "reader-password")
    monkeypatch.setenv("CASE_PLATFORM_TOTP_SECRET", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")
    monkeypatch.setattr(case_platform_auth, "generate_totp", lambda *_args, **_kwargs: "123456")
    monkeypatch.setattr(case_platform_auth.urllib.request, "build_opener", lambda *_args: opener)

    client = case_platform_auth.CasePlatformClient("https://agiletc.test", 5)
    with pytest.raises(case_platform_auth.CasePlatformAuthError) as exc_info:
        client.request_json("/api/case/list")

    message = str(exc_info.value)
    assert "report-reader" not in message
    assert "reader-password" not in message
    assert "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ" not in message
    assert "123456" not in message


def test_mfa_http_error_does_not_expose_one_time_credentials(monkeypatch):
    from task_server.services import case_platform_auth

    class MfaHttpErrorOpener:
        def open(self, request, timeout):
            if request.full_url.endswith("/api/user/login"):
                return _FakeResponse({
                    "code": 200,
                    "data": {
                        "mfaRequired": True,
                        "mfaToken": "single-use-mfa-token",
                    },
                })
            raise urllib.error.HTTPError(
                request.full_url,
                400,
                "Bad Request",
                {},
                BytesIO(b'single-use-mfa-token 123456 invalid'),
            )

    monkeypatch.delenv("CASE_PLATFORM_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("CASE_PLATFORM_USERNAME", "report-reader")
    monkeypatch.setenv("CASE_PLATFORM_PASSWORD", "reader-password")
    monkeypatch.setenv("CASE_PLATFORM_TOTP_SECRET", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")
    monkeypatch.setattr(case_platform_auth, "generate_totp", lambda *_args, **_kwargs: "123456")
    monkeypatch.setattr(
        case_platform_auth.urllib.request,
        "build_opener",
        lambda *_args: MfaHttpErrorOpener(),
    )

    client = case_platform_auth.CasePlatformClient("https://agiletc.test", 5)
    with pytest.raises(case_platform_auth.CasePlatformRequestError) as exc_info:
        client.request_json("/api/case/list")

    message = str(exc_info.value)
    assert "single-use-mfa-token" not in message
    assert "123456" not in message


def test_startup_env_loads_case_platform_credentials(monkeypatch, tmp_path):
    from task_server import config

    env_path = tmp_path / "midscene.env"
    env_path.write_text("export CASE_PLATFORM_ACCESS_TOKEN='file-token'\n", encoding="utf-8")
    env_path.chmod(0o600)
    monkeypatch.delenv("CASE_PLATFORM_ACCESS_TOKEN", raising=False)

    status = config.load_startup_env(str(env_path))

    assert status["valid"] is True
    assert "CASE_PLATFORM_ACCESS_TOKEN" in status["loaded_keys"]
    assert config.os.environ["CASE_PLATFORM_ACCESS_TOKEN"] == "file-token"


def test_invalid_auth_required_value_is_rejected(monkeypatch):
    from task_server.services.case_platform_auth import CasePlatformAuthError, CasePlatformAuthConfig

    monkeypatch.setenv("CASE_PLATFORM_AUTH_REQUIRED", "treu")

    with pytest.raises(CasePlatformAuthError, match="true 或 false"):
        CasePlatformAuthConfig.from_env()


def test_authenticated_client_rejects_plain_http_by_default(monkeypatch):
    from task_server.services.case_platform_auth import CasePlatformAuthError, CasePlatformClient

    monkeypatch.setenv("CASE_PLATFORM_ACCESS_TOKEN", "case-platform-token")
    with pytest.raises(CasePlatformAuthError, match="HTTPS"):
        CasePlatformClient("http://agiletc.test", 5)


def test_authenticated_client_cannot_enable_plain_http(monkeypatch):
    from task_server.services.case_platform_auth import CasePlatformAuthError, CasePlatformClient

    monkeypatch.setenv("CASE_PLATFORM_ACCESS_TOKEN", "case-platform-token")
    monkeypatch.setenv("CASE_PLATFORM_ALLOW_INSECURE_HTTP", "true")

    with pytest.raises(CasePlatformAuthError, match="HTTPS"):
        CasePlatformClient("http://agiletc.test", 5)


def test_cross_origin_redirect_is_rejected():
    from task_server.services.case_platform_auth import (
        CasePlatformRequestError,
        SameOriginRedirectHandler,
    )

    handler = SameOriginRedirectHandler()
    request = __import__("urllib.request").request.Request("https://agiletc.test/api/case/list")

    with pytest.raises(CasePlatformRequestError, match="跨域重定向"):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://attacker.test/collect",
        )


def test_real_cookie_jar_reuses_login_cookie(monkeypatch):
    from task_server.services import case_platform_auth

    observed_cookies = []
    observed_post_bodies = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length") or 0)
            observed_post_bodies.append((self.path, json.loads(self.rfile.read(length) or b"{}")))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            if self.path == "/api/user/mfa/verify":
                self.send_header("Set-Cookie", "agiletc_session=ready; Path=/; HttpOnly")
            self.end_headers()
            if self.path == "/api/user/login":
                self.wfile.write(b'{"code":200,"data":{"mfaRequired":true,"mfaToken":"mfa-token"}}')
            else:
                self.wfile.write(b'{"code":200,"data":{}}')

        def do_GET(self):
            observed_cookies.append(self.headers.get("Cookie"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"code":200,"data":{"total":0,"dataSources":[]}}')

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.delenv("CASE_PLATFORM_ACCESS_TOKEN", raising=False)
        monkeypatch.setenv("CASE_PLATFORM_USERNAME", "report-reader")
        monkeypatch.setenv("CASE_PLATFORM_PASSWORD", "reader-password")
        monkeypatch.setenv("CASE_PLATFORM_TOTP_SECRET", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")
        monkeypatch.setattr(case_platform_auth, "generate_totp", lambda *_args, **_kwargs: "123456")

        client = case_platform_auth.CasePlatformClient("https://agiletc.test", 5)
        # The local server only verifies CookieJar behavior; production clients
        # cannot construct an authenticated client from an HTTP base URL.
        client.base_url = f"http://127.0.0.1:{server.server_port}"
        client.request_json("/api/case/list")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert observed_post_bodies == [
        (
            "/api/user/login",
            {"username": "report-reader", "password": "reader-password"},
        ),
        (
            "/api/user/mfa/verify",
            {"mfaToken": "mfa-token", "code": "123456"},
        ),
    ]
    assert observed_cookies == ["agiletc_session=ready"]


def test_http_401_reauthenticates_once(monkeypatch):
    from task_server.services import case_platform_auth

    class HttpExpiryOpener:
        def __init__(self):
            self.login_count = 0
            self.metadata_count = 0

        def open(self, request, timeout):
            if request.full_url.endswith("/api/user/login"):
                self.login_count += 1
                return _FakeResponse({"code": 200, "data": {}})
            self.metadata_count += 1
            if self.metadata_count == 1:
                raise urllib.error.HTTPError(
                    request.full_url,
                    401,
                    "Unauthorized",
                    {},
                    BytesIO(b'{"code":100011}'),
                )
            return _FakeResponse({"code": 200, "data": {"total": 0, "dataSources": []}})

    opener = HttpExpiryOpener()
    monkeypatch.delenv("CASE_PLATFORM_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("CASE_PLATFORM_USERNAME", "report-reader")
    monkeypatch.setenv("CASE_PLATFORM_PASSWORD", "reader-password")
    monkeypatch.setenv("CASE_PLATFORM_TOTP_SECRET", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")
    monkeypatch.setattr(case_platform_auth, "generate_totp", lambda *_args, **_kwargs: "123456")
    monkeypatch.setattr(case_platform_auth.urllib.request, "build_opener", lambda *_args: opener)

    client = case_platform_auth.CasePlatformClient("https://agiletc.test", 5)
    result = client.request_json("/api/case/list")

    assert result["code"] == 200
    assert opener.login_count == 2
    assert opener.metadata_count == 2


def test_concurrent_expiry_refreshes_one_session(monkeypatch):
    from task_server.services import case_platform_auth

    class ConcurrentExpiryOpener:
        def __init__(self):
            self.lock = threading.Lock()
            self.barrier = threading.Barrier(2)
            self.login_count = 0
            self.metadata_count = 0

        def open(self, request, timeout):
            if request.full_url.endswith("/api/user/login"):
                with self.lock:
                    self.login_count += 1
                return _FakeResponse({"code": 200, "data": {}})
            with self.lock:
                self.metadata_count += 1
                attempt = self.metadata_count
            if attempt <= 2:
                self.barrier.wait(timeout=2)
                return _FakeResponse({"code": 100011, "msg": "权限认证错误"})
            return _FakeResponse({"code": 200, "data": {"total": 0, "dataSources": []}})

    opener = ConcurrentExpiryOpener()
    monkeypatch.delenv("CASE_PLATFORM_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("CASE_PLATFORM_USERNAME", "report-reader")
    monkeypatch.setenv("CASE_PLATFORM_PASSWORD", "reader-password")
    monkeypatch.setenv("CASE_PLATFORM_TOTP_SECRET", "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ")
    monkeypatch.setattr(case_platform_auth, "generate_totp", lambda *_args, **_kwargs: "123456")
    monkeypatch.setattr(case_platform_auth.urllib.request, "build_opener", lambda *_args: opener)

    client = case_platform_auth.CasePlatformClient("https://agiletc.test", 5)
    results = []
    errors = []

    def run_request():
        try:
            results.append(client.request_json("/api/case/list"))
        except Exception as exc:  # pragma: no cover - assertion captures unexpected failures
            errors.append(exc)

    threads = [threading.Thread(target=run_request) for _index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert errors == []
    assert len(results) == 2
    assert opener.login_count == 2
    assert opener.metadata_count == 4
