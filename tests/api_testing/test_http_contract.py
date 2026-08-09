import json
from http.client import HTTPConnection
from pathlib import Path
from threading import Thread

import pytest

from task_server.app import TaskHTTPHandler, ThreadingHTTPServer


class HttpResponse:
    def __init__(self, response):
        self.status = response.status
        self.headers = dict(response.getheaders())
        raw = response.read()
        self.body = json.loads(raw.decode("utf-8")) if raw else None


class HttpClient:
    def __init__(self, port):
        self.port = port

    def get(self, path, headers=None):
        return self.request("GET", path, headers=headers)

    def post(self, path, payload=None, headers=None):
        body = json.dumps(payload or {}).encode("utf-8")
        request_headers = {"Content-Type": "application/json", **(headers or {})}
        return self.request("POST", path, body, request_headers)

    def request(self, method, path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", self.port, timeout=2)
        connection.request(method, path, body=body, headers=headers or {})
        return HttpResponse(connection.getresponse())


@pytest.fixture()
def http_client():
    server = ThreadingHTTPServer(("127.0.0.1", 0), TaskHTTPHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield HttpClient(server.server_port)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_api_routes_require_existing_session(http_client):
    response = http_client.get("/api/api-testing/v1/projects")

    assert response.status == 401
    assert response.body == {
        "ok": False,
        "error": {
            "code": "unauthorized",
            "message": "Authentication is required",
            "details": {},
        },
        "request_id": response.headers["X-Request-Id"],
    }


def test_api_routes_reject_unauthorized_post_before_reading_body(http_client, monkeypatch):
    def body_must_not_be_read(_handler):
        raise AssertionError("unauthenticated API request read its body")

    monkeypatch.setattr(TaskHTTPHandler, "_body", body_must_not_be_read)
    response = http_client.post(
        "/api/api-testing/v1/executions",
        {"token": "must-not-be-parsed"},
    )

    assert response.status == 401
    assert response.body["error"]["code"] == "unauthorized"
    assert "must-not-be-parsed" not in json.dumps(response.body)


def test_router_registers_only_one_api_testing_prefix():
    source = Path("task_server/router.py").read_text(encoding="utf-8")

    assert source.count("register_api_testing_routes(") == 1
    assert "api_testing.services" not in source
