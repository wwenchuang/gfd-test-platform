"""Auth HTTP dispatch, separate from business and machine-token authorization."""

import json
import re
import sqlite3
from urllib.parse import unquote

from . import auth, identity


MAX_AUTH_BODY = 64 * 1024
_ROUTES = {
    "/me": {"GET"}, "/login": {"POST"}, "/logout": {"POST"},
    "/users": {"GET", "POST"}, "/roles": {"GET", "POST"},
    "/permissions": {"GET"}, "/audit": {"GET"}, "/sessions": {"GET"},
    "/sessions/revoke": {"POST"},
    "/revoke-sessions": {"POST"}, "/change-password": {"POST"},
}
_SELF_ROUTES = {"/me", "/logout", "/sessions", "/sessions/revoke", "/revoke-sessions", "/change-password"}


def _body(handler):
    try:
        if handler.headers.get("Transfer-Encoding"):
            raise identity.IdentityError("认证接口不支持分块请求体")
        raw_length = handler.headers.get("Content-Length", "0")
        if not re.fullmatch(r"[0-9]{1,10}", str(raw_length)):
            raise identity.IdentityError("Content-Length 格式无效")
        length = int(raw_length)
        if length > MAX_AUTH_BODY:
            raise identity.IdentityError("请求体不能超过 64KB", 413, "body_too_large")
        if hasattr(handler, "_parsed_body"):
            data = handler._parsed_body
        else:
            raw = handler.rfile.read(length) if length else b""
            if len(raw) != length:
                raise identity.IdentityError("请求体长度与 Content-Length 不一致")
            data = json.loads(raw.decode("utf-8-sig")) if raw else {}
        if len(json.dumps(data, ensure_ascii=False).encode("utf-8")) > MAX_AUTH_BODY:
            raise identity.IdentityError("请求体不能超过 64KB", 413, "body_too_large")
        if not isinstance(data, dict):
            raise identity.IdentityError("请求体必须是 JSON 对象")
        handler._parsed_body = data
        return data
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, identity.IdentityError):
            raise
        raise identity.IdentityError("请求体不是有效的 JSON 对象") from None


def _fields(data, allowed):
    if set(data) - set(allowed):
        raise identity.IdentityError("请求包含不允许修改的字段")


def _credentials(data):
    _fields(data, {"username", "password"})
    username, password = data.get("username"), data.get("password")
    if not isinstance(username, str) or not 0 < len(username.strip()) <= 128 or not isinstance(password, str):
        raise identity.IdentityError("请提供有效的用户名和密码")
    return username.strip(), password


def handle_auth_request(handler, method, path, qs):
    """Return False for unknown paths, including parent-owned /scope-options."""
    if not path.startswith("/api/auth/"):
        return False
    route = path[len("/api/auth"):]
    user_match = re.fullmatch(r"/users/([^/]+)(?:/(reset-password|revoke-sessions))?", route)
    role_match = re.fullmatch(r"/roles/([^/]+)", route)
    methods = _ROUTES.get(route)
    if user_match:
        methods = {"POST"} if user_match[2] else {"PUT"}
    elif role_match:
        methods = {"PUT", "DELETE"}
    if not methods:
        return False
    try:
        if method not in methods:
            raise identity.IdentityError("请求方法不支持", 405, "method_not_allowed")
        token = auth.bearer_token(handler.headers)
        if route != "/login":
            session = auth.verify_session_token(token)
            if not session:
                raise identity.IdentityError("请先登录后再操作", 401, "unauthorized")
            username = session["user"]
            profile = identity.get_access_profile(username)
            if not profile or profile["status"] != "active":
                raise identity.IdentityError("账号已停用，请联系管理员", 401, "unauthorized")
            if route not in _SELF_ROUTES:
                if profile["must_change_password"]:
                    raise identity.IdentityError("请先修改初始密码后再操作", 403, "password_change_required")
                if not identity.has_permission(username, "auth.manage"):
                    raise identity.IdentityError("缺少 auth.manage 权限，请联系管理员授权", 403, "permission_denied")
        data = _body(handler) if method in {"POST", "PUT", "DELETE"} else {}
        store = identity.get_identity_store()
        result = {}
        if route == "/login":
            username, password = _credentials(data)
            address = getattr(handler, "client_address", ("local",))
            result = store.login(username, password, source=address[0])
            if result is None:
                raise identity.IdentityError("账号或密码错误", 401, "invalid_credentials")
        elif route == "/me":
            result = {"user": username, "expires_at": session["exp"], "profile": profile}
        elif route == "/logout":
            _fields(data, {})
            auth.logout(token)
        elif route == "/sessions":
            result = {"sessions": store.list_sessions(username, token)}
        elif route == "/sessions/revoke":
            _fields(data, {"session_id"})
            store.revoke_session(username, data.get("session_id"))
        elif route == "/revoke-sessions":
            _fields(data, {})
            store.revoke_sessions(username)
        elif route == "/change-password":
            _fields(data, {"current_password", "new_password"})
            if not isinstance(data.get("current_password"), str):
                raise identity.IdentityError("请提供当前密码")
            result = store.change_password(username, data["current_password"], data.get("new_password"))
        elif route == "/users":
            result = {"users": store.list_users(username)} if method == "GET" else store.create_user(username, data)
        elif user_match:
            target = unquote(user_match[1])
            action = user_match[2]
            if action == "reset-password":
                _fields(data, {"password"})
                if "password" in data and not isinstance(data["password"], str):
                    raise identity.IdentityError("密码必须是字符串")
                result = store.reset_password(username, target, data.get("password"))
            elif action == "revoke-sessions":
                _fields(data, {})
                store.revoke_sessions(username, target)
            else:
                result = {"user": store.update_user(username, target, data)}
        elif route == "/roles":
            result = {"roles": store.list_roles(username)} if method == "GET" else {"role": store.create_role(username, data)}
        elif role_match:
            target = unquote(role_match[1])
            if method == "DELETE":
                _fields(data, {})
                store.delete_role(username, target)
            else:
                result = {"role": store.update_role(username, target, data)}
        elif route == "/permissions":
            result = {"permissions": identity.PERMISSIONS}
        elif route == "/audit":
            try:
                limit = int(qs.get("limit", 100))
            except (TypeError, ValueError):
                raise identity.IdentityError("记录条数必须是整数") from None
            result = {"events": store.list_audit(username, limit)}
        handler._json({"ok": True, **result})
    except identity.IdentityError as exc:
        if exc.status == 413:
            handler.close_connection = True
        handler._json({"ok": False, "error": str(exc), "code": exc.code}, exc.status)
    except (sqlite3.Error, OSError):
        handler._json({"ok": False, "error": "身份服务暂不可用，请稍后重试或联系管理员", "code": "identity_unavailable"}, 503)
    return True
