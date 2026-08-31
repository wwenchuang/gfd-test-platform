import importlib
import io
import json

import pytest

from task_server import auth
from test_identity import EMPTY_SCOPE, NEW_PASSWORD, PASSWORD, activate, create_member, identity_db


class Handler:
    def __init__(self, token=None, payload=None, headers=None):
        raw = json.dumps({} if payload is None else payload, ensure_ascii=False).encode()
        self.headers = {"Content-Length": str(len(raw)), **(headers or {})}
        if token:
            self.headers["Authorization"] = "Bearer " + token
        self.rfile = io.BytesIO(raw)
        self.client_address = ("127.0.0.1", 4321)
        self.response = None

    def _json(self, body, code=200):
        self.response = (code, body)


def call(method, path, token=None, payload=None, headers=None, qs=None):
    handler = Handler(token, payload, headers)
    module = importlib.import_module("task_server.identity_http")
    handled = module.handle_auth_request(handler, method, "/api/auth" + path, qs or {})
    return handled, handler.response


def test_identity_http_module_exists():
    assert importlib.util.find_spec("task_server.identity_http"), "auth HTTP dispatcher is missing"


@pytest.fixture(autouse=True)
def http_available(request):
    if request.node.name != "test_identity_http_module_exists" and not importlib.util.find_spec("task_server.identity_http"):
        pytest.skip("HTTP module availability has a separate contract assertion")


def test_login_me_logout_and_envelope(identity_db):
    handled, (status, result) = call("POST", "/login", payload={"username": "admin", "password": PASSWORD})
    assert handled and status == 200 and result["ok"] is True
    assert {"user", "token", "expires_in", "profile"} <= result.keys()
    assert result["user"] == "admin" == result["profile"]["username"]
    token = result["token"]
    _, (status, me) = call("GET", "/me", token)
    assert status == 200 and me["expires_at"] == auth.verify_session_token(token)["exp"]
    assert me["profile"]["username"] == "admin"
    assert call("POST", "/logout", token)[1] == (200, {"ok": True})
    assert call("GET", "/me", token)[1][0] == 401


def test_member_creation_first_login_and_password_gate(identity_db):
    _, store = identity_db
    admin = auth.create_session_token()
    _, (status, created) = call("POST", "/users", admin, {"username": "member", "display_name": "王小明", "role_ids": ["test_manager"]})
    assert status == 200 and created["user"]["scope"] == EMPTY_SCOPE
    _, (_, logged) = call("POST", "/login", payload={"username": "member", "password": created["temporary_password"]})
    token = logged["token"]
    for path in ["/users", "/roles", "/permissions", "/audit"]:
        _, (status, error) = call("GET", path, token)
        assert status == 403 and error["code"] == "password_change_required"
    assert call("GET", "/sessions", token)[1][0] == 200
    _, (status, changed) = call("POST", "/change-password", token, {"current_password": created["temporary_password"], "new_password": NEW_PASSWORD})
    assert status == 200 and not changed["profile"]["must_change_password"]
    assert changed["user"] == "member"
    assert auth.verify_session_token(token) is None
    assert auth.verify_session_token(changed["token"])
    assert call("GET", "/users", changed["token"])[1][0] == 403
    assert store.get_access_profile("member")["display_name"] == "王小明"


def test_admin_users_roles_permissions_audit_contract(identity_db):
    admin = auth.create_session_token()
    _, (_, created) = call("POST", "/roles", admin, {"name": "读者", "permissions": ["ui.view"]})
    role = created["role"]
    _, (_, member) = call("POST", "/users", admin, {"username": "reader", "display_name": "李四", "role_ids": [role["id"]], "password": PASSWORD})
    assert "temporary_password" not in member
    assert call("PUT", "/users/reader", admin, {"display_name": "李小四", "scope": {"ui_apps": ["app.a"]}})[1][1]["user"]["display_name"] == "李小四"
    assert call("DELETE", "/roles/" + role["id"], admin)[1][0] == 409
    assert call("PUT", "/roles/" + role["id"], admin, {"name": "观察员", "permissions": ["api.view"]})[1][0] == 200
    assert call("PUT", "/users/reader", admin, {"role_ids": []})[1][0] == 200
    assert call("DELETE", "/roles/" + role["id"], admin)[1][0] == 200
    for path, key in [("/users", "users"), ("/roles", "roles"), ("/permissions", "permissions"), ("/audit", "events")]:
        _, (status, result) = call("GET", path, admin)
        assert status == 200 and isinstance(result[key], list)
        assert "password_hash" not in json.dumps(result)
    permissions = call("GET", "/permissions", admin)[1][1]["permissions"]
    assert all(set(item) == {"id", "label", "group"} for item in permissions)


def test_reset_and_self_only_sessions(identity_db):
    _, store = identity_db
    create_member(store)
    activate(store)
    admin = auth.create_session_token()
    member = auth.create_session_token("member")
    sessions = call("GET", "/sessions", member, qs={"username": "admin"})[1][1]["sessions"]
    assert len(sessions) == 2
    assert all(set(row) <= {"id", "created_at", "expires_at", "is_current"} for row in sessions)
    assert sum(row["is_current"] for row in sessions) == 1
    assert all(type(row["created_at"]) is int and type(row["is_current"]) is bool for row in sessions)
    assert call("POST", "/revoke-sessions", member)[1][0] == 200
    assert auth.verify_session_token(member) is None
    member = auth.create_session_token("member")
    _, (status, reset) = call("POST", "/users/member/reset-password", admin)
    assert status == 200 and len(reset["temporary_password"]) >= 15
    assert auth.verify_session_token(member) is None
    member = auth.create_session_token("member")
    assert call("POST", "/users/member/revoke-sessions", admin)[1][0] == 200
    assert auth.verify_session_token(member) is None
    assert auth.verify_session_token(admin)


def test_runner_and_unknown_routes(identity_db, monkeypatch):
    monkeypatch.setattr(auth, "TOKEN", "runner-only")
    assert call("GET", "/users", headers={"x-token": "runner-only"})[1][0] == 401
    assert call("GET", "/scope-options") == (False, None)
    assert call("GET", "/unknown") == (False, None)
    assert call("POST", "/roles/id/unknown") == (False, None)
    assert call("PATCH", "/users")[1][0] == 405


@pytest.mark.parametrize("payload", [[], "text", 123, {"username": []}, {"username": "missing", "password": {}},
                                      {"username": "admin", "password": PASSWORD, "is_superuser": True}])
def test_malformed_login_payloads_are_safe(identity_db, payload):
    _, (status, error) = call("POST", "/login", payload=payload)
    assert status == 400 and error["ok"] is False


def test_unknown_username_matches_wrong_password_response(identity_db):
    responses = [call("POST", "/login", payload={"username": user, "password": "wrong"})[1] for user in ["admin", "missing"]]
    assert responses[0] == responses[1] == (401, {"ok": False, "error": "账号或密码错误", "code": "invalid_credentials"})


@pytest.mark.parametrize("method,path", [("POST", "/login"), ("POST", "/logout"), ("POST", "/users"),
                                         ("PUT", "/users/admin"), ("POST", "/roles"), ("PUT", "/roles/tester"),
                                         ("POST", "/change-password"), ("POST", "/revoke-sessions"),
                                         ("POST", "/users/admin/reset-password"), ("POST", "/users/admin/revoke-sessions")])
def test_all_body_endpoints_enforce_64kb_before_read(identity_db, method, path):
    handler = Handler(auth.create_session_token(), headers={"Content-Length": str(65537)})
    module = importlib.import_module("task_server.identity_http")
    assert module.handle_auth_request(handler, method, "/api/auth" + path, {})
    assert handler.response[0] == 413
    assert handler.rfile.tell() == 0


def test_cached_body_is_supported_and_size_checked(identity_db):
    module = importlib.import_module("task_server.identity_http")
    handler = Handler()
    handler._parsed_body = {"username": "admin", "password": PASSWORD}
    handler.rfile.read()
    assert module.handle_auth_request(handler, "POST", "/api/auth/login", {})
    assert handler.response[0] == 200
    handler = Handler()
    handler._parsed_body = {"username": "a" * 66000, "password": PASSWORD}
    module.handle_auth_request(handler, "POST", "/api/auth/login", {})
    assert handler.response[0] == 413


def test_http_source_throttling_ignores_forwarded_header(identity_db, monkeypatch):
    identity, _ = identity_db
    monkeypatch.setattr(identity, "SOURCE_ATTEMPTS", 2)
    for index in range(2):
        assert call("POST", "/login", payload={"username": str(index), "password": "wrong"})[1][0] == 401
    assert call("POST", "/login", payload={"username": "another", "password": "wrong"}, headers={"X-Forwarded-For": "8.8.8.8"})[1][0] == 429
