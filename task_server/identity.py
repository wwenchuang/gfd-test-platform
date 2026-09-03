"""Local identities, live access policy and persistent revocable browser sessions.

The store is lazy: importing authentication never creates production state.
Mutations authorize and enforce invariants in the same SQLite write transaction.
"""

import hashlib
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from . import config


PERMISSIONS = [
    {"id": key, "label": label, "group": group}
    for group, items in (
        ("平台", [("auth.manage", "管理成员与角色"), ("platform.configure", "配置平台"), ("platform.notify", "发送通知")]),
        ("UI 测试", [("ui.view", "查看 UI 测试"), ("ui.edit", "编辑 UI 测试"), ("ui.execute", "执行 UI 测试"),
                    ("ui.delete", "删除 UI 测试"), ("ui.baseline", "管理 UI 基线")]),
        ("接口测试", [("api.view", "查看接口测试"), ("api.edit", "编辑接口测试"), ("api.execute", "执行接口测试"),
                    ("api.delete", "删除接口测试"), ("api.baseline", "管理接口基线"),
                    ("api.environment", "管理接口环境"), ("api.production", "执行生产环境")]),
        ("性能测试", [("api.loadtest.view", "查看性能测试"), ("api.loadtest.edit", "编辑性能测试"),
                     ("api.loadtest.execute", "执行性能测试"),
                     ("api.loadtest.manage_agents", "管理压测节点")]),
    ) for key, label in items
]
PERMISSION_IDS = frozenset(item["id"] for item in PERMISSIONS)
PERMISSION_PREREQUISITES = {
    "ui.edit": ("ui.view",),
    "ui.execute": ("ui.view",),
    "ui.delete": ("ui.view",),
    "ui.baseline": ("ui.view",),
    "api.edit": ("api.view",),
    "api.execute": ("api.view",),
    "api.delete": ("api.view",),
    "api.baseline": ("api.view",),
    "api.environment": ("api.view",),
    "api.production": ("api.view", "api.execute"),
    "api.loadtest.view": ("api.view",),
    "api.loadtest.edit": ("api.view", "api.loadtest.view"),
    "api.loadtest.execute": ("api.view", "api.execute", "api.loadtest.view"),
    "api.loadtest.manage_agents": ("api.view", "api.loadtest.view"),
}
SCOPE_KINDS = ("ui_apps", "api_projects", "api_environments")
PRESET_ROLES = {
    "super_admin": ("超级管理员", sorted(PERMISSION_IDS)),
    "test_manager": ("测试负责人", sorted(PERMISSION_IDS - {"auth.manage", "platform.configure", "api.production", "api.loadtest.manage_agents"})),
    "tester": ("测试成员", ["ui.view", "ui.edit", "ui.execute", "api.view", "api.edit", "api.execute"]),
    "viewer": ("只读成员", ["ui.view", "api.view"]),
}
MAX_AUDIT_EVENTS = 10000
MAX_RATE_BUCKETS = 2000
ACCOUNT_ATTEMPTS = 8
SOURCE_ATTEMPTS = 50
GLOBAL_ATTEMPTS = 200
RATE_WINDOW_SECONDS = 300
MAX_SESSIONS_PER_USER = 20
MAX_SESSIONS = 10000
_HASHER = PasswordHasher(memory_cost=19456, time_cost=2, parallelism=1, type=Type.ID)
_HASH_SLOTS = threading.BoundedSemaphore(2)
_DUMMY_LOCK = threading.Lock()
_DUMMY_HASH = None
_STORE_LOCK = threading.Lock()
_STORE = None


class IdentityError(ValueError):
    def __init__(self, message, status=400, code="invalid_request"):
        super().__init__(message)
        self.status = status
        self.code = code


@contextmanager
def _hash_slot():
    if not _HASH_SLOTS.acquire(timeout=1):
        raise IdentityError("密码服务繁忙，请稍后重试", 503, "auth_busy")
    try:
        yield
    finally:
        _HASH_SLOTS.release()


def _hash_password(password):
    with _hash_slot():
        return _HASHER.hash(password)


def _dummy_hash():
    global _DUMMY_HASH
    with _DUMMY_LOCK:
        if _DUMMY_HASH is None:
            _DUMMY_HASH = _hash_password(secrets.token_urlsafe(32))
    return _DUMMY_HASH


def _verify_password(encoded, password):
    with _hash_slot():
        try:
            return _HASHER.verify(encoded, password)
        except (VerificationError, InvalidHashError):
            return False


def _validate_password(password):
    if not isinstance(password, str) or not 15 <= len(password) <= 128:
        raise IdentityError("密码长度必须为 15 至 128 个字符")
    return password


def _fields(data, allowed):
    if not isinstance(data, dict) or set(data) - set(allowed):
        raise IdentityError("请求包含不允许修改的字段")


def _name(value, label, identifier=False):
    if not isinstance(value, str) or not value.strip() or len(value) > 128:
        raise IdentityError(f"{label}不能为空且不能超过 128 个字符")
    if identifier and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.@-]{0,127}", value):
        raise IdentityError(f"{label}仅支持字母、数字、点、下划线、@ 和短横线")
    if any(ord(char) < 32 for char in value):
        raise IdentityError(f"{label}不能包含控制字符")
    return value.strip()


def _scope(value):
    if not isinstance(value, dict) or set(value) - set(SCOPE_KINDS):
        raise IdentityError("数据范围类型无效")
    result = {}
    for kind in SCOPE_KINDS:
        ids = value.get(kind, [])
        if ids == "*":
            result[kind] = "*"
        elif isinstance(ids, list) and len(ids) <= 1000 and all(
            isinstance(item, str) and item.strip() == item and 0 < len(item) <= 256
            and item != "*" and not any(ord(char) < 32 for char in item) for item in ids
        ):
            result[kind] = sorted(set(ids))
        else:
            raise IdentityError("数据范围必须是 * 或明确的资源 ID 列表")
    return result


def _permissions(value):
    if not isinstance(value, list) or any(not isinstance(item, str) or item not in PERMISSION_IDS for item in value):
        raise IdentityError("包含未知权限")
    result = set(value)
    for permission in tuple(result):
        result.update(PERMISSION_PREREQUISITES.get(permission, ()))
    return sorted(result)


def default_db_path():
    explicit = os.environ.get("TASK_AUTH_DB")
    if explicit:
        return Path(explicit).expanduser().absolute()
    if config.APP_ENV == "prod":
        return Path("/opt/midscene-auth/identity.sqlite3")
    tasks = Path(config.TASK_DIR).expanduser().absolute()
    return tasks.parent / f".{tasks.name}-auth" / "identity.sqlite3"


class IdentityStore:
    def __init__(self, path=None):
        self.path = Path(path) if path is not None else default_db_path()
        self.path = self.path.expanduser().absolute()
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        parent = self.path.parent.stat()
        if ((hasattr(os, "geteuid") and parent.st_uid != os.geteuid())
                or not os.access(self.path.parent, os.W_OK | os.X_OK)):
            raise IdentityError("身份库目录必须归当前服务账号所有且可写", 503, "identity_permissions")
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if hasattr(os, "geteuid") and os.fstat(fd).st_uid != os.geteuid():
                raise IdentityError("身份库文件必须归当前服务账号所有", 503, "identity_permissions")
            os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, display_name TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','disabled')), password_hash TEXT NOT NULL,
                    must_change_password INTEGER NOT NULL, scope TEXT NOT NULL, auth_version INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS roles (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, permissions TEXT NOT NULL,
                    builtin INTEGER NOT NULL DEFAULT 0, version INTEGER NOT NULL DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS user_roles (
                    user_id TEXT NOT NULL REFERENCES users(user_id), role_id TEXT NOT NULL REFERENCES roles(id),
                    PRIMARY KEY(user_id, role_id)
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, id TEXT NOT NULL UNIQUE, user_id TEXT NOT NULL REFERENCES users(user_id),
                    created_at INTEGER NOT NULL, expires_at INTEGER NOT NULL, revoked_at INTEGER
                );
                CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id, created_at);
                CREATE TABLE IF NOT EXISTS login_attempts (
                    bucket TEXT PRIMARY KEY, started_at INTEGER NOT NULL, attempts INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, created_at INTEGER NOT NULL,
                    actor TEXT NOT NULL, action TEXT NOT NULL, target TEXT NOT NULL, details TEXT NOT NULL
                );
            """)
        with self._transaction() as db:
            if "version" not in {row[1] for row in db.execute("PRAGMA table_info(roles)")}:
                db.execute("ALTER TABLE roles ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
            if not db.execute("SELECT 1 FROM metadata WHERE key='initialized'").fetchone():
                for role_id, (name, permissions) in PRESET_ROLES.items():
                    db.execute("INSERT INTO roles(id,name,permissions,builtin) VALUES (?,?,?,1)", (role_id, name, json.dumps(permissions)))
                legacy = config.TASK_ADMIN_PASSWORD_HASH
                if not legacy and config.TASK_ADMIN_PASSWORD:
                    legacy = hashlib.sha256(config.TASK_ADMIN_PASSWORD.encode("utf-8")).hexdigest()
                user_id = secrets.token_hex(16)
                db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,0)", (
                    user_id, config.TASK_ADMIN_USER, "管理员", "active", "sha256$" + legacy if legacy else "",
                    0, json.dumps(dict.fromkeys(SCOPE_KINDS, "*")),
                ))
                db.execute("INSERT INTO user_roles VALUES (?, 'super_admin')", (user_id,))
                db.execute("INSERT INTO metadata VALUES ('initialized','1')")
            if not db.execute("SELECT 1 FROM metadata WHERE key='load_permissions_v1'").fetchone():
                db.execute(
                    "UPDATE roles SET permissions=?,version=version+1 WHERE id='super_admin'",
                    (json.dumps(sorted(PERMISSION_IDS)),),
                )
                row = db.execute(
                    "SELECT permissions FROM roles WHERE id='test_manager'"
                ).fetchone()
                if row:
                    permissions = set(json.loads(row[0]))
                    permissions.update(
                        {
                            "api.view",
                            "api.execute",
                            "api.loadtest.view",
                            "api.loadtest.edit",
                            "api.loadtest.execute",
                        }
                    )
                    db.execute(
                        "UPDATE roles SET permissions=?,version=version+1 WHERE id='test_manager'",
                        (json.dumps(sorted(permissions)),),
                    )
                db.execute("INSERT INTO metadata VALUES ('load_permissions_v1','1')")
        _dummy_hash()

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _transaction(self, write=True):
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            try:
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    def _profile(self, db, username):
        user = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not user:
            return None
        roles = db.execute("SELECT r.id,r.name,r.permissions FROM roles r JOIN user_roles u ON r.id=u.role_id WHERE u.user_id=? ORDER BY r.id", (user["user_id"],)).fetchall()
        enabled = user["status"] == "active" and not user["must_change_password"]
        role_ids = [role["id"] for role in roles]
        permissions = sorted(set().union(*(set(json.loads(role["permissions"])) for role in roles))) if enabled else []
        return {"username": user["username"], "user_id": user["user_id"], "display_name": user["display_name"],
                "status": user["status"], "role_ids": role_ids, "role_names": [role["name"] for role in roles], "permissions": permissions,
                "scope": json.loads(user["scope"]), "is_superuser": enabled and "super_admin" in role_ids,
                "must_change_password": bool(user["must_change_password"])}

    def get_access_profile(self, username):
        if not isinstance(username, str):
            return None
        with self._transaction(write=False) as db:
            return self._profile(db, username)

    def _manage(self, db, actor, target=None):
        profile = self._profile(db, actor)
        if not profile or "auth.manage" not in profile["permissions"]:
            raise IdentityError("缺少 auth.manage 权限，请联系管理员授权", 403, "permission_denied")
        if target and "super_admin" in target["role_ids"] and not profile["is_superuser"]:
            raise IdentityError("仅超级管理员可以管理超级管理员账号", 403, "permission_denied")
        if target and not profile["is_superuser"]:
            # Pending/disabled accounts still carry grants, even though their effective permissions are empty.
            granted = set()
            for row in db.execute("SELECT r.permissions FROM roles r JOIN user_roles u ON r.id=u.role_id WHERE u.user_id=?", (target["user_id"],)):
                granted.update(json.loads(row[0]))
            self._permission_ceiling(profile, granted)
            self._scope_ceiling(profile, target["scope"])
        return profile

    def _permission_ceiling(self, manager, permissions):
        if not manager["is_superuser"] and not set(permissions).issubset(manager["permissions"]):
            raise IdentityError("只能管理本人权限范围内的角色和成员，请联系超级管理员", 403, "delegation_denied")

    def _scope_ceiling(self, manager, scope):
        if manager["is_superuser"]:
            return
        for kind in SCOPE_KINDS:
            allowed, requested = manager["scope"][kind], scope[kind]
            if allowed != "*" and (requested == "*" or not set(requested).issubset(allowed)):
                raise IdentityError("只能授予本人已获授权的数据范围，请联系超级管理员", 403, "delegation_denied")

    def _user(self, db, username):
        profile = self._profile(db, username)
        if not profile:
            raise IdentityError("成员不存在", 404, "not_found")
        return profile

    def _roles(self, db, values, actor):
        if not isinstance(values, list) or len(values) > 100 or any(not isinstance(item, str) for item in values):
            raise IdentityError("角色必须是角色 ID 列表")
        values = sorted(set(values))
        identity_roles = []
        for role in values:
            row = db.execute("SELECT permissions FROM roles WHERE id=?", (role,)).fetchone()
            if not row:
                raise IdentityError("包含不存在的角色")
            permissions = json.loads(row[0])
            self._permission_ceiling(actor, permissions)
            if role == "super_admin" or role == "viewer" or (permissions and set(permissions).issubset({"ui.view", "api.view"})):
                identity_roles.append(role)
        if len(values) > 1 and identity_roles:
            raise IdentityError("超级管理员或只读身份不能与其他角色同时选择")
        if "super_admin" in values and not actor["is_superuser"]:
            raise IdentityError("仅超级管理员可以授予超级管理员角色", 403, "permission_denied")
        return values

    def _audit(self, db, actor, action, target, details=None):
        # Allow only structured, known metadata; free text and nested request bodies may contain secrets.
        safe = {}
        if isinstance(details, dict):
            if isinstance(details.get("permission"), str) and details["permission"] in PERMISSION_IDS:
                safe["permission"] = details["permission"]
            fields = details.get("changed_fields")
            if isinstance(fields, list):
                safe["changed_fields"] = [field for field in fields if isinstance(field, str) and field in {"display_name", "status", "role_ids", "scope", "name", "permissions"}]
            if details.get("outcome") in ("success", "failure", "denied"):
                safe["outcome"] = details["outcome"]
            if details.get("method") in ("GET", "POST", "PUT", "DELETE", "HEAD"):
                safe["method"] = details["method"]
            if type(details.get("status")) is int and 100 <= details["status"] <= 599:
                safe["status"] = details["status"]
            if type(details.get("ok")) is bool:
                safe["ok"] = details["ok"]
        db.execute("INSERT INTO audit(created_at,actor,action,target,details) VALUES (?,?,?,?,?)", (
            int(time.time()), str(actor or "")[:128], str(action or "")[:128], str(target or "")[:128], json.dumps(safe),
        ))
        db.execute("DELETE FROM audit WHERE id NOT IN (SELECT id FROM audit ORDER BY id DESC LIMIT ?)", (MAX_AUDIT_EVENTS,))

    def audit_event(self, actor, action, target, details=None):
        with self._transaction() as db:
            self._audit(db, actor, action, target, details)

    def create_user(self, actor, data):
        _fields(data, {"username", "display_name", "password", "role_ids", "scope"})
        username = _name(data.get("username"), "用户名", True)
        display_name = _name(data.get("display_name"), "姓名")
        scope = _scope(data.get("scope", {}))
        temporary = secrets.token_urlsafe(18) if "password" not in data else None
        password = _validate_password(temporary if temporary is not None else data["password"])
        with self._transaction(write=False) as db:
            self._manage(db, actor)
        encoded = _hash_password(password)
        with self._transaction() as db:
            manager = self._manage(db, actor)
            roles = self._roles(db, data.get("role_ids", ["tester"]), manager)
            self._scope_ceiling(manager, scope)
            if self._profile(db, username):
                raise IdentityError("用户名已存在", 409, "conflict")
            user_id = secrets.token_hex(16)
            db.execute("INSERT INTO users VALUES (?,?,?,?,?,?,?,0)", (user_id, username, display_name, "active", encoded, 1, json.dumps(scope)))
            db.executemany("INSERT INTO user_roles VALUES (?,?)", [(user_id, role) for role in roles])
            self._audit(db, actor, "user.create", username)
            result = {"user": self._profile(db, username)}
            if temporary is not None:
                result["temporary_password"] = temporary
            return result

    def update_user(self, actor, username, data):
        _fields(data, {"display_name", "status", "role_ids", "scope"})
        with self._transaction() as db:
            self._manage(db, actor)
            user = self._user(db, username)
            manager = self._manage(db, actor, user)
            name = _name(data.get("display_name", user["display_name"]), "姓名")
            status = data.get("status", user["status"])
            if status not in ("active", "disabled"):
                raise IdentityError("账号状态必须是 active 或 disabled")
            roles = self._roles(db, data.get("role_ids", user["role_ids"]), manager)
            scope = _scope(data.get("scope", user["scope"]))
            self._scope_ceiling(manager, scope)
            if user["status"] == "active" and "super_admin" in user["role_ids"] and (status != "active" or "super_admin" not in roles):
                count = db.execute("SELECT count(*) FROM users u JOIN user_roles r ON u.user_id=r.user_id WHERE u.status='active' AND r.role_id='super_admin'").fetchone()[0]
                if count <= 1:
                    raise IdentityError("不能停用或降权最后一个有效超级管理员", 409, "last_super_admin")
            db.execute("UPDATE users SET display_name=?,status=?,scope=? WHERE user_id=?", (name, status, json.dumps(scope), user["user_id"]))
            db.execute("DELETE FROM user_roles WHERE user_id=?", (user["user_id"],))
            db.executemany("INSERT INTO user_roles VALUES (?,?)", [(user["user_id"], role) for role in roles])
            if status == "disabled":
                self._revoke_user(db, user["user_id"])
            self._audit(db, actor, "user.update", username, {"changed_fields": list(data)})
            return self._profile(db, username)

    def list_users(self, actor):
        with self._transaction(write=False) as db:
            self._manage(db, actor)
            return [self._profile(db, row[0]) for row in db.execute("SELECT username FROM users ORDER BY username").fetchall()]

    def _role(self, row):
        return {"id": row["id"], "name": row["name"], "permissions": json.loads(row["permissions"]),
                "builtin": bool(row["builtin"]), "immutable": row["id"] == "super_admin", "version": row["version"]}

    def list_roles(self, actor):
        with self._transaction(write=False) as db:
            self._manage(db, actor)
            return [self._role(row) for row in db.execute("SELECT * FROM roles ORDER BY id")]

    def create_role(self, actor, data):
        _fields(data, {"id", "name", "permissions"})
        role_id = _name(data.get("id", "role-" + secrets.token_hex(8)), "角色 ID", True)
        name = _name(data.get("name"), "角色名称")
        permissions = _permissions(data.get("permissions", []))
        with self._transaction() as db:
            manager = self._manage(db, actor)
            self._permission_ceiling(manager, permissions)
            if db.execute("SELECT 1 FROM roles WHERE id=?", (role_id,)).fetchone():
                raise IdentityError("角色 ID 已存在", 409, "conflict")
            db.execute("INSERT INTO roles(id,name,permissions,builtin) VALUES (?,?,?,0)", (role_id, name, json.dumps(permissions)))
            self._audit(db, actor, "role.create", role_id)
            return self._role(db.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone())

    def _editable_role(self, db, actor, role_id):
        manager = self._manage(db, actor)
        role = db.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone()
        if not role:
            raise IdentityError("角色不存在", 404, "not_found")
        if role_id == "super_admin":
            raise IdentityError("超级管理员角色不可修改或删除", 403, "immutable_role")
        self._permission_ceiling(manager, json.loads(role["permissions"]))
        if not manager["is_superuser"]:
            affected = db.execute("SELECT u.username FROM users u JOIN user_roles r ON u.user_id=r.user_id WHERE r.role_id=?", (role_id,)).fetchall()
            for user in affected:
                self._manage(db, actor, self._user(db, user[0]))
        return role

    def update_role(self, actor, role_id, data):
        _fields(data, {"name", "permissions", "version"})
        with self._transaction() as db:
            role = self._editable_role(db, actor, role_id)
            if "version" in data:
                if type(data["version"]) is not int:
                    raise IdentityError("角色版本必须为整数")
                if data["version"] != role["version"]:
                    raise IdentityError("角色已被其他管理员修改，请刷新后重试", 409, "version_conflict")
            name = _name(data.get("name", role["name"]), "角色名称")
            permissions = _permissions(data.get("permissions", json.loads(role["permissions"])))
            self._permission_ceiling(self._manage(db, actor), permissions)
            db.execute("UPDATE roles SET name=?, permissions=?,version=version+1 WHERE id=?", (name, json.dumps(permissions), role_id))
            self._audit(db, actor, "role.update", role_id, {"changed_fields": list(data)})
            return self._role(db.execute("SELECT * FROM roles WHERE id=?", (role_id,)).fetchone())

    def delete_role(self, actor, role_id):
        with self._transaction() as db:
            self._editable_role(db, actor, role_id)
            if db.execute("SELECT 1 FROM user_roles WHERE role_id=? LIMIT 1", (role_id,)).fetchone():
                raise IdentityError("角色仍被成员使用，请先调整成员角色", 409, "role_in_use")
            db.execute("DELETE FROM roles WHERE id=?", (role_id,))
            self._audit(db, actor, "role.delete", role_id)

    def list_audit(self, actor, limit=100):
        with self._transaction(write=False) as db:
            self._manage(db, actor)
            rows = db.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 500)),)).fetchall()
            return [{**dict(row), "details": json.loads(row["details"])} for row in rows]

    def _rate_limit(self, username, source):
        now = int(time.time())
        account_bucket = "account:" + hashlib.sha256(username.encode("utf-8")).hexdigest()
        buckets = [("global", GLOBAL_ATTEMPTS), (account_bucket, ACCOUNT_ATTEMPTS),
                   ("source:" + hashlib.sha256(source.encode("utf-8")).hexdigest(), SOURCE_ATTEMPTS)]
        with self._transaction() as db:
            db.execute("DELETE FROM login_attempts WHERE started_at<=?", (now - RATE_WINDOW_SECONDS,))
            missing = 0
            for bucket, limit in buckets:
                row = db.execute("SELECT attempts FROM login_attempts WHERE bucket=?", (bucket,)).fetchone()
                if row and row[0] >= limit:
                    raise IdentityError("登录尝试过于频繁，请稍后再试", 429, "rate_limited")
                missing += int(row is None)
            # Never evict a live throttle: flooding unknown accounts must not unlock a victim.
            if db.execute("SELECT count(*) FROM login_attempts").fetchone()[0] + missing > MAX_RATE_BUCKETS:
                raise IdentityError("登录尝试过于频繁，请稍后再试", 429, "rate_limited")
            for bucket, _ in buckets:
                db.execute("INSERT INTO login_attempts VALUES (?,?,1) ON CONFLICT(bucket) DO UPDATE SET attempts=attempts+1", (bucket, now))
        return account_bucket

    def _authenticate(self, username, password, source, issue_session):
        username = username.strip() if isinstance(username, str) else ""
        if not 0 < len(username) <= 128:
            username = ""
        source = str(source or "local")[:256]
        account_bucket = self._rate_limit(username, source)
        valid_input = isinstance(password, str) and 0 < len(password) <= 128
        raw = password if valid_input else "invalid-password"
        with self._transaction(write=False) as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        encoded = row["password_hash"] if row else ""
        legacy = encoded.startswith("sha256$")
        if legacy or not encoded or not row or row["status"] != "active":
            _verify_password(_dummy_hash(), raw)
            valid = bool(legacy and secrets.compare_digest(hashlib.sha256(raw.encode("utf-8")).hexdigest(), encoded[7:]))
        else:
            valid = _verify_password(encoded, raw)
        valid = valid and valid_input and row is not None and row["status"] == "active"
        replacement = _hash_password(raw) if valid and (legacy or _HASHER.check_needs_rehash(encoded)) else None
        with self._transaction() as db:
            current = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not valid or not current or current["status"] != "active" or current["password_hash"] != encoded or current["auth_version"] != row["auth_version"]:
                self._audit(db, "", "login.failure", "", {"outcome": "failure"})
                return None
            if replacement:
                db.execute("UPDATE users SET password_hash=? WHERE user_id=?", (replacement, row["user_id"]))
            db.execute("DELETE FROM login_attempts WHERE bucket=?", (account_bucket,))
            self._audit(db, username, "login.success", username)
            profile = self._profile(db, username)
            if issue_session:
                return self._login_result(db, profile)
            return profile

    def authenticate(self, username, password, source="local"):
        return self._authenticate(username, password, source, False)

    def login(self, username, password, source="local"):
        return self._authenticate(username, password, source, True)

    def _create_session(self, db, user_id):
        now = int(time.time())
        ttl = max(300, config.TASK_SESSION_TTL_SECONDS)
        token = secrets.token_urlsafe(32)
        db.execute("DELETE FROM sessions WHERE expires_at<=? OR revoked_at IS NOT NULL", (now,))
        db.execute("INSERT INTO sessions VALUES (?,?,?,?,?,NULL)", (hashlib.sha256(token.encode()).hexdigest(), secrets.token_hex(16), user_id, now, now + ttl))
        db.execute("DELETE FROM sessions WHERE user_id=? AND token_hash NOT IN (SELECT token_hash FROM sessions WHERE user_id=? ORDER BY created_at DESC,rowid DESC LIMIT ?)", (user_id, user_id, MAX_SESSIONS_PER_USER))
        db.execute("DELETE FROM sessions WHERE token_hash NOT IN (SELECT token_hash FROM sessions ORDER BY created_at DESC,rowid DESC LIMIT ?)", (MAX_SESSIONS,))
        return token

    def _login_result(self, db, profile):
        token = self._create_session(db, profile["user_id"])
        return {"user": profile["username"], "token": token, "profile": profile,
                "expires_in": max(300, config.TASK_SESSION_TTL_SECONDS)}

    def create_session(self, username):
        with self._transaction() as db:
            user = self._user(db, username)
            if user["status"] != "active":
                raise IdentityError("账号已停用，请联系管理员", 403, "account_disabled")
            return self._create_session(db, user["user_id"])

    def verify_session(self, token):
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token):
            return None
        with self._transaction(write=False) as db:
            row = db.execute("SELECT u.username,u.user_id,u.must_change_password,s.expires_at FROM sessions s JOIN users u ON u.user_id=s.user_id WHERE s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>? AND u.status='active'", (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
            return {"user": row["username"], "user_id": row["user_id"], "exp": row["expires_at"],
                    "must_change_password": bool(row["must_change_password"])} if row else None

    def session_is_active(self, username, token_digest):
        """Check the original session behind an SSE ticket without exposing a raw token."""
        if not isinstance(username, str) or not isinstance(token_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", token_digest):
            return False
        with self._transaction(write=False) as db:
            return db.execute("SELECT 1 FROM sessions s JOIN users u ON u.user_id=s.user_id WHERE u.username=? AND u.status='active' AND s.token_hash=? AND s.revoked_at IS NULL AND s.expires_at>?", (username, token_digest, int(time.time()))).fetchone() is not None

    def logout(self, token):
        if not isinstance(token, str) or not token or len(token) > 256:
            return
        with self._transaction() as db:
            digest = hashlib.sha256(token.encode()).hexdigest()
            row = db.execute("SELECT u.username FROM sessions s JOIN users u ON s.user_id=u.user_id WHERE s.token_hash=?", (digest,)).fetchone()
            db.execute("UPDATE sessions SET revoked_at=? WHERE token_hash=?", (int(time.time()), digest))
            if row:
                self._audit(db, row["username"], "session.logout", row["username"])

    def _revoke_user(self, db, user_id):
        db.execute("UPDATE users SET auth_version=auth_version+1 WHERE user_id=?", (user_id,))
        db.execute("UPDATE sessions SET revoked_at=? WHERE user_id=? AND revoked_at IS NULL", (int(time.time()), user_id))

    def revoke_sessions(self, actor, username=None):
        username = actor if username is None else username
        with self._transaction() as db:
            if actor != username:
                self._manage(db, actor)
            user = self._user(db, username)
            if actor != username:
                self._manage(db, actor, user)
            self._revoke_user(db, user["user_id"])
            self._audit(db, actor, "session.revoke_all", username)

    def revoke_session(self, actor, session_id):
        if not isinstance(session_id, str) or not re.fullmatch(r"[a-f0-9]{32}", session_id):
            raise IdentityError("会话编号无效")
        with self._transaction() as db:
            user = self._user(db, actor)
            row = db.execute(
                "SELECT token_hash FROM sessions WHERE id=? AND user_id=? AND revoked_at IS NULL AND expires_at>?",
                (session_id, user["user_id"], int(time.time())),
            ).fetchone()
            if not row:
                raise IdentityError("会话不存在或已失效", 404, "not_found")
            db.execute("UPDATE sessions SET revoked_at=? WHERE id=? AND user_id=?", (int(time.time()), session_id, user["user_id"]))
            self._audit(db, actor, "session.revoke", session_id)

    def list_sessions(self, username, current_token=None):
        digest = hashlib.sha256(current_token.encode()).hexdigest() if isinstance(current_token, str) else ""
        with self._transaction(write=False) as db:
            user = self._user(db, username)
            return [{**dict(row), "is_current": bool(row["is_current"])} for row in db.execute("SELECT id,created_at,expires_at,token_hash=? AS is_current FROM sessions WHERE user_id=? AND revoked_at IS NULL AND expires_at>? ORDER BY created_at DESC", (digest, user["user_id"], int(time.time())))]

    def reset_password(self, actor, username, password=None):
        temporary = secrets.token_urlsafe(18) if password is None else None
        encoded_input = _validate_password(temporary if temporary is not None else password)
        with self._transaction(write=False) as db:
            self._manage(db, actor)
            self._manage(db, actor, self._user(db, username))
        encoded = _hash_password(encoded_input)
        with self._transaction() as db:
            self._manage(db, actor)
            user = self._user(db, username)
            self._manage(db, actor, user)
            self._revoke_user(db, user["user_id"])
            db.execute("UPDATE users SET password_hash=?,must_change_password=1 WHERE user_id=?", (encoded, user["user_id"]))
            self._audit(db, actor, "user.reset_password", username)
            result = {"user": self._profile(db, username)}
            if temporary is not None:
                result["temporary_password"] = temporary
            return result

    def change_password(self, username, current_password, new_password):
        _validate_password(new_password)
        if current_password == new_password:
            raise IdentityError("新密码不能与当前密码相同")
        profile = self.authenticate(username, current_password)
        if not profile:
            raise IdentityError("当前密码错误", 403, "invalid_password")
        # Snapshot before hashing, then compare after hashing to reject concurrent resets.
        with self._transaction(write=False) as db:
            previous = db.execute("SELECT password_hash,auth_version FROM users WHERE user_id=?", (profile["user_id"],)).fetchone()
        if not _verify_password(previous["password_hash"], current_password):
            raise IdentityError("凭据已变更，请重新登录", 401, "session_expired")
        encoded = _hash_password(new_password)
        with self._transaction() as db:
            current = db.execute("SELECT * FROM users WHERE user_id=?", (profile["user_id"],)).fetchone()
            if current["status"] != "active" or current["auth_version"] != previous["auth_version"] or current["password_hash"] != previous["password_hash"]:
                raise IdentityError("凭据已变更，请重新登录", 401, "session_expired")
            self._revoke_user(db, profile["user_id"])
            db.execute("UPDATE users SET password_hash=?,must_change_password=0 WHERE user_id=?", (encoded, profile["user_id"]))
            self._audit(db, username, "user.change_password", username)
            return self._login_result(db, self._profile(db, username))

    def _emergency_reset_admin(self, username, password):
        encoded = _hash_password(_validate_password(password))
        with self._transaction() as db:
            user = self._user(db, username)
            if "super_admin" not in user["role_ids"]:
                raise IdentityError("应急重置仅支持现有超级管理员账号", 403)
            self._revoke_user(db, user["user_id"])
            db.execute("UPDATE users SET password_hash=?,must_change_password=1 WHERE user_id=?", (encoded, user["user_id"]))
            self._audit(db, "local-console", "user.emergency_reset", username)

    def backup(self, output):
        """Use SQLite's online backup API; never overwrite an existing file."""
        destination = Path(output).expanduser().absolute()
        destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        try:
            with self._connect() as source:
                target = sqlite3.connect(destination)
                try:
                    source.backup(target, pages=256)
                finally:
                    target.close()
        except BaseException:
            destination.unlink(missing_ok=True)
            raise
        return destination


def get_identity_store():
    global _STORE
    path = default_db_path()
    with _STORE_LOCK:
        if _STORE is None or _STORE.path != path:
            _STORE = IdentityStore(path)
        return _STORE


def get_access_profile(username):
    return get_identity_store().get_access_profile(username)


def has_permission(username, permission):
    if not isinstance(permission, str) or permission not in PERMISSION_IDS:
        return False
    profile = get_access_profile(username)
    return bool(profile and permission in profile["permissions"])


def scope_allows(username, kind, resource_id):
    if kind not in SCOPE_KINDS or not isinstance(resource_id, str) or not resource_id.strip() or resource_id == "*":
        return False
    profile = get_access_profile(username)
    if not profile or profile["status"] != "active" or profile["must_change_password"]:
        return False
    if profile["is_superuser"]:
        return True
    values = profile["scope"][kind]
    return values == "*" or resource_id in values


def audit_event(actor, action, target, details=None):
    get_identity_store().audit_event(actor, action, target, details)


def session_is_active(username, token_digest):
    return get_identity_store().session_is_active(username, token_digest)


def main(argv=None):
    """Local operator recovery; passwords are accepted only through getpass."""
    import argparse
    import getpass
    import sys

    parser = argparse.ArgumentParser(description="本地身份库维护（以平台服务账号执行）")
    commands = parser.add_subparsers(dest="command", required=True)
    reset = commands.add_parser("reset-admin", help="重置明确指定的现有超级管理员密码并撤销会话")
    reset.add_argument("--username", required=True)
    backup = commands.add_parser("backup", help="在线一致性备份；拒绝覆盖已有文件")
    backup.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        path = default_db_path()
        if not path.is_file():
            raise IdentityError("身份库不存在；维护命令不会创建或引导新账号")
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
            if not db.execute("SELECT 1 FROM metadata WHERE key='initialized'").fetchone():
                raise IdentityError("身份库尚未初始化")
        store = IdentityStore(path)
        if args.command == "backup":
            store.backup(args.output)
            print("身份库备份完成。备份含密码摘要与会话数据，请限制访问。")
        else:
            user = store.get_access_profile(args.username)
            if not user or "super_admin" not in user["role_ids"]:
                raise IdentityError("必须明确指定现有超级管理员用户名")
            password = getpass.getpass("新密码（15 至 128 个字符）: ")
            _validate_password(password)
            if password != getpass.getpass("再次输入新密码: "):
                raise IdentityError("两次输入的密码不一致")
            store._emergency_reset_admin(args.username, password)
            print("管理员密码已重置，旧会话已全部撤销；下次登录需修改密码。账号状态保持不变。")
        return 0
    except IdentityError as exc:
        print(str(exc), file=sys.stderr)
    except (sqlite3.Error, OSError):
        print("身份库操作失败，请检查路径、文件权限及备份文件是否已存在", file=sys.stderr)
    except (EOFError, KeyboardInterrupt):
        print("操作已取消", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
