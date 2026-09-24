"""Request attribution through a real local HTTP server."""

import http.client
import json
import sqlite3
import threading
import time

import pytest

from task_server import app, auth
from task_server.services.operation_attribution import OperationStore
from test_identity import PASSWORD, identity_db, create_member, activate


@pytest.fixture
def operation_server(identity_db, tmp_path, monkeypatch):
    monkeypatch.setenv("TASK_OPERATION_DB", str(tmp_path / "operations.sqlite3"))
    server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.TaskHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, OperationStore(tmp_path / "operations.sqlite3")
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


def request(server, method, path, token=None, body=None, extra=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    headers = dict(extra or {})
    if token:
        headers["Authorization"] = "Bearer " + token
    if body is not None:
        headers["Content-Type"] = "application/json"
        body = json.dumps(body).encode()
    conn.request(method, path, body=body, headers=headers)
    response = conn.getresponse()
    result = response.status, response.read()
    conn.close()
    return result


def events(store, at_least=0):
    profile = __import__("task_server.identity", fromlist=["get_access_profile"]).get_access_profile("admin")
    until = time.monotonic() + 2
    while True:
        rows = store.list_events(profile, all_actors=True)["events"]
        if len(rows) >= at_least or time.monotonic() >= until:
            return rows
        time.sleep(0.01)


def test_http_get_denied_and_login_logout_have_distinct_actors(operation_server):
    server, store = operation_server
    assert request(server, "GET", "/api/tasks")[0] == 401
    status, response = request(server, "POST", "/api/auth/login", body={"username": "admin", "password": PASSWORD})
    assert status == 200
    token = json.loads(response)["token"]
    assert request(server, "GET", "/api/auth/me", token)[0] == 200
    assert request(server, "POST", "/api/auth/logout", token, body={})[0] == 200
    rows = events(store, 4)
    assert len(rows) == 4
    assert [row["result"] for row in reversed(rows)] == ["denied", "success", "success", "success"]
    assert [row["actor"]["kind"] for row in reversed(rows)] == ["anonymous", "user", "user", "user"]
    assert len({row["request_id"] for row in rows}) == 4


def test_http_records_semantic_and_binary_outcomes_without_secret(operation_server, monkeypatch):
    server, store = operation_server
    def fake_get(handler):
        handler.send_response(200)
        handler.end_headers()
        handler.wfile.write(b"binary-secret")
    def fake_post(handler):
        handler._body()
        handler._json({"ok": False, "error": "body-secret"}, 200)
    monkeypatch.setattr(app, "dispatch_get", fake_get)
    monkeypatch.setattr(app, "dispatch_post", fake_post)
    token = auth.create_session_token()
    assert request(server, "GET", "/api/reports/download", token)[0] == 200
    assert request(server, "POST", "/api/cases", token, {"password": "body-secret"})[0] == 200
    rows = events(store)
    assert [row["result"] for row in rows] == ["failed", "success"]
    assert "secret" not in json.dumps(rows)


def test_operations_history_requires_bearer_and_cannot_widen(operation_server):
    server, store = operation_server
    token = auth.create_session_token()
    request(server, "GET", "/api/auth/me", token)
    assert request(server, "GET", "/api/operations")[0] == 401
    status, raw = request(server, "GET", "/api/operations?limit=1&username=other", token)
    assert status == 200
    result = json.loads(raw)
    assert result["ok"] and len(result["events"]) == 1
    assert result["events"][0]["actor"]["username"] == "admin"
    assert "audit" in result


def test_invalid_machine_header_does_not_impersonate_and_human_route_prefers_bearer(operation_server, monkeypatch):
    server, store = operation_server
    monkeypatch.setattr(auth, "TOKEN", "valid-machine")
    token = auth.create_session_token()
    request(server, "GET", "/api/auth/me", token, extra={"x-token": "valid-machine"})
    request(server, "GET", "/api/tasks", extra={"x-token": "invalid-machine"})
    rows = events(store)
    assert rows[1]["actor"]["kind"] == "user"
    assert rows[0]["actor"]["kind"] == "anonymous"


def test_own_history_includes_only_machine_events_initiated_by_user(operation_server, identity_db):
    server, store = operation_server
    identity, users = identity_db
    create_member(users)
    activate(users)
    admin_id = identity.get_access_profile("admin")["user_id"]
    member = identity.get_access_profile("member")
    for initiator in (admin_id, member["user_id"]):
        store.append({"actor": {"kind": "runner", "username": "runner"},
                      "initiator_user_id": initiator, "action": "job.finish",
                      "resource_type": "job", "result": "success"})
    own = store.list_events(member, filters={"actor_id": ""})["events"]
    assert len(own) == 1
    assert own[0]["initiator_user_id"] == member["user_id"]


def test_api_testing_direct_json_partial_items_and_accepted_are_recorded(operation_server, monkeypatch):
    server, store = operation_server
    from task_server.api_testing.http import _send_json
    replies = iter([
        (207, {"ok": True, "data": {"results": [
            {"id": "item_a", "ok": True}, {"id": "item_b", "ok": False, "code": "invalid_case"}]}}),
        (202, {"ok": True, "data": {"status": "queued", "job_id": "job_1"}}),
    ])
    def fake_post(handler):
        handler._body()
        code, data = next(replies)
        _send_json(handler, code, data, "test-id")
    monkeypatch.setattr(app, "dispatch_post", fake_post)
    token = auth.create_session_token()
    assert request(server, "POST", "/api/api-testing/collections", token, {})[0] == 207
    assert request(server, "POST", "/api/api-testing/executions", token, {})[0] == 202
    rows = events(store, 2)
    assert [row["result"] for row in rows] == ["accepted", "partial"]
    assert rows[1]["item_outcomes"] == [
        {"resource_id": "item_a", "result": "success", "reason_code": ""},
        {"resource_id": "item_b", "result": "failed", "reason_code": "invalid_case"},
    ]


def test_malformed_body_and_head_are_recorded(operation_server):
    server, store = operation_server
    token = auth.create_session_token()
    assert request(server, "HEAD", "/api/auth/me", token)[0] == 405
    conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
    conn.request("POST", "/api/auth/login", body=b"{bad", headers={"Content-Type": "application/json"})
    response = conn.getresponse()
    assert response.status == 400
    response.read()
    conn.close()
    rows = events(store, 2)
    assert len(rows) == 2
    assert rows[0]["result"] == "failed"


def test_non_ascii_file_name_has_stable_safe_reference():
    from task_server.operation_http import safe_resource_ref
    first = safe_resource_ref("3D打印基线.yaml")
    assert first.startswith("sha256:")
    assert first == safe_resource_ref("3D打印基线.yaml")
    assert first != safe_resource_ref("另一个.yaml")


def test_real_batch_shape_keeps_successes_and_errors_separate(operation_server, monkeypatch):
    server, store = operation_server
    def fake_post(handler):
        handler._body()
        handler._json({"ok": False, "results": [{"file": "正常.yaml", "targetFile": "正常.yaml"}],
                       "errors": [{"file": "失败.yaml", "error": "private filesystem path"}]}, 207)
    monkeypatch.setattr(app, "dispatch_post", fake_post)
    assert request(server, "POST", "/api/files/op", auth.create_session_token(), {"op": "move"})[0] == 207
    row = events(store)[0]
    assert row["result"] == "partial"
    assert row["action"] == "file.move"
    assert [item["result"] for item in row["item_outcomes"]] == ["success", "failed"]
    assert "正常" not in json.dumps(row)
    assert "private" not in json.dumps(row)


def test_api_testing_resource_and_action_are_specific():
    from task_server.operation_http import _resource
    assert _resource("/api/api-testing/v1/projects/p1/collections/c1/run", "POST", {}, {}, 200)[::2] == ("api_collection", "api_collection.run")


def test_keepalive_eof_does_not_repeat_previous_request(operation_server, monkeypatch):
    _, store = operation_server
    class KeepAlive(app.TaskHTTPHandler):
        protocol_version = "HTTP/1.1"
    def fake_get(handler):
        handler.send_response(200)
        handler.send_header("Content-Length", "0")
        handler.end_headers()
    monkeypatch.setattr(app, "dispatch_get", fake_get)
    server = app.ThreadingHTTPServer(("127.0.0.1", 0), KeepAlive)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        token = auth.create_session_token()
        for _ in range(2):
            conn.request("GET", "/api/jobs", headers={"Authorization": "Bearer " + token})
            response = conn.getresponse()
            assert response.status == 200
            response.read()
        conn.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert len([row for row in events(store) if row["action"] == "job.view"]) == 2
    finally:
        server.server_close()


def test_history_time_boundaries_are_scoped(operation_server):
    server, store = operation_server
    profile = __import__("task_server.identity", fromlist=["get_access_profile"]).get_access_profile("admin")
    now = time.time()
    for stamp in (now - 100, now):
        store.append({"actor": {"kind": "user", "user_id": profile["user_id"], "username": "admin"},
                      "action": "case.view", "resource_type": "case", "result": "success", "timestamp": stamp})
    token = auth.create_session_token()
    status, raw = request(server, "GET", f"/api/operations?from_ts={now - 1}&to_ts={now + 1}", token)
    assert status == 200
    assert len(json.loads(raw)["events"]) == 1
    assert request(server, "GET", "/api/operations?from_ts=bad", token)[0] == 400


def test_spool_fallback_is_exposed_and_replayed_on_recovery(operation_server, monkeypatch):
    server, store = operation_server
    from task_server.operation_http import _store
    live_store = _store()
    original = live_store._insert
    monkeypatch.setattr(live_store, "_insert", lambda event: (_ for _ in ()).throw(sqlite3.OperationalError("offline")))
    token = auth.create_session_token()
    assert request(server, "GET", "/api/auth/me", token)[0] == 200
    assert list(live_store.spool_dir.glob("*.json"))
    monkeypatch.setattr(live_store, "_insert", original)
    status, raw = request(server, "GET", "/api/operations", token)
    assert status == 200
    assert json.loads(raw)["audit"]["storage"] == "spool"
    # The ordinary request finalizer invokes bounded replay on recovery.
    for _ in range(2):
        request(server, "GET", "/api/auth/me", token)
    assert not list(live_store.spool_dir.glob("*.json"))
    assert len(events(store)) >= 2


def test_history_method_filter_and_member_scope(operation_server, identity_db):
    server, store = operation_server
    identity, users = identity_db
    create_member(users)
    activate(users)
    admin_token = auth.create_session_token()
    member_token = auth.create_session_token("member")
    request(server, "GET", "/api/auth/me", admin_token)
    request(server, "GET", "/api/auth/me", member_token)
    request(server, "POST", "/api/auth/logout", member_token, {})
    member_token = auth.create_session_token("member")
    status, raw = request(server, "GET", "/api/operations?method=POST", member_token)
    assert status == 200
    assert len(json.loads(raw)["events"]) == 1
    assert json.loads(raw)["events"][0]["method"] == "POST"
    admin_id = identity.get_access_profile("admin")["user_id"]
    status, raw = request(server, "GET", f"/api/operations?scope=all&actor_id={admin_id}", member_token)
    assert status == 200
    assert json.loads(raw)["events"] == []


def test_run_request_pending_job_is_accepted_not_finished(operation_server, monkeypatch):
    server, store = operation_server
    def fake_post(handler):
        handler._body()
        handler._json({"ok": True, "job": {"id": "job_123", "status": "pending"}}, 200)
    monkeypatch.setattr(app, "dispatch_post", fake_post)
    assert request(server, "POST", "/api/run-request", auth.create_session_token(), {})[0] == 200
    row = events(store)[0]
    assert row["result"] == "accepted"
    assert row["resource_id"] == "job_123"


def test_actor_lookup_outage_does_not_abort_business_response(operation_server, monkeypatch):
    server, store = operation_server
    from task_server import operation_http
    monkeypatch.setattr(operation_http, "_actor_for_request", lambda handler: (_ for _ in ()).throw(OSError("identity offline")))
    monkeypatch.setattr(app, "dispatch_get", lambda handler: handler._json({"ok": True}))
    assert request(server, "GET", "/api/jobs")[0] == 200
    assert events(store, 1)[0]["actor"]["kind"] == "anonymous"


def test_history_identity_outage_returns_503(operation_server, monkeypatch):
    server, _ = operation_server
    from task_server import identity
    token = auth.create_session_token()
    monkeypatch.setattr(identity, "get_access_profile", lambda username: (_ for _ in ()).throw(OSError("offline")))
    assert request(server, "GET", "/api/operations", token)[0] == 503


def test_exception_after_headers_does_not_write_second_response(operation_server, monkeypatch):
    server, store = operation_server
    def fake_get(handler):
        handler.send_response(200)
        handler.end_headers()
        raise ValueError("late failure")
    monkeypatch.setattr(app, "dispatch_get", fake_get)
    status, body = request(server, "GET", "/api/jobs", auth.create_session_token())
    assert status == 200
    assert b"HTTP/1.0 500" not in body
    assert events(store, 1)[0]["result"] == "interrupted"
