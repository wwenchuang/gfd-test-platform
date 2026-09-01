"""Identity security contracts; every database lives under pytest's tmp_path."""

import hashlib
import importlib
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from task_server import auth, config


PASSWORD = "initial-password-for-tests"
NEW_PASSWORD = "replacement-password-for-tests"
EMPTY_SCOPE = {"ui_apps": [], "api_projects": [], "api_environments": []}
ALL_SCOPE = dict.fromkeys(EMPTY_SCOPE, "*")


@pytest.fixture
def identity_db(tmp_path, monkeypatch):
    monkeypatch.setenv("TASK_AUTH_DB", str(tmp_path / "private" / "identity.sqlite3"))
    monkeypatch.setattr(config, "TASK_ADMIN_USER", "admin")
    monkeypatch.setattr(config, "TASK_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setattr(config, "TASK_ADMIN_PASSWORD_HASH", "")
    monkeypatch.setattr(auth, "TASK_ADMIN_USER", "admin")
    # Import only after isolating the database.
    if not importlib.util.find_spec("task_server.identity"):
        pytest.skip("identity module availability is covered by its contract test")
    module = importlib.import_module("task_server.identity")
    return module, module.get_identity_store()


def test_persistent_identity_module_exists():
    assert importlib.util.find_spec("task_server.identity"), "persistent identity store is missing"


def create_member(store, username="member", **changes):
    data = {"username": username, "display_name": "测试成员", "password": PASSWORD,
            "role_ids": ["tester"]}
    data.update(changes)
    return store.create_user("admin", data)


def activate(store, username="member"):
    return store.change_password(username, PASSWORD, NEW_PASSWORD)


def test_profile_and_default_scope(identity_db):
    identity, store = identity_db
    result = create_member(store)
    profile = identity.get_access_profile("member")
    assert result["user"] == profile
    assert set(profile) == {"username", "user_id", "display_name", "status", "role_ids", "role_names",
                            "permissions", "scope", "is_superuser", "must_change_password"}
    assert profile["display_name"] == "测试成员"
    assert profile["role_names"] == ["测试成员"]
    assert profile["scope"] == EMPTY_SCOPE
    assert profile["must_change_password"] is True
    assert not identity.has_permission("member", "ui.execute")
    assert not identity.scope_allows("member", "ui_apps", "app.a")
    assert identity.get_access_profile("unknown") is None
    assert not identity.has_permission("unknown", "ui.view")


def test_bootstrap_migrates_once_and_password_reset_survives_config_changes(identity_db, monkeypatch):
    identity, store = identity_db
    assert auth.login("admin", "wrong") == (False, "账号或密码错误")
    ok, token = auth.login("admin", PASSWORD)
    assert ok
    assert auth.verify_session_token(token)["must_change_password"] is False
    with sqlite3.connect(store.path) as db:
        encoded = db.execute("SELECT password_hash FROM users WHERE username='admin'").fetchone()[0]
    assert encoded.startswith("$argon2id$v=19$m=19456,t=2,p=1$")
    store.reset_password("admin", "admin", NEW_PASSWORD)
    monkeypatch.setattr(config, "TASK_ADMIN_PASSWORD", "changed-environment-password")
    restarted = identity.IdentityStore(store.path)
    assert restarted.authenticate("admin", PASSWORD) is None
    assert restarted.authenticate("admin", NEW_PASSWORD)["must_change_password"] is True
    assert auth.verify_session_token(token) is None


def test_sha256_bootstrap_and_legacy_short_password(identity_db, tmp_path, monkeypatch):
    identity, _ = identity_db
    monkeypatch.setattr(config, "TASK_ADMIN_PASSWORD_HASH", hashlib.sha256(b"legacy-short").hexdigest())
    store = identity.IdentityStore(tmp_path / "sha" / "identity.sqlite3")
    assert store.authenticate("admin", "legacy-short")["username"] == "admin"
    assert store.get_access_profile("admin")["must_change_password"] is False


@pytest.mark.parametrize("password", ["", "a" * 14, "b" * 129, 123, None])
def test_password_validation(identity_db, password):
    identity, store = identity_db
    with pytest.raises(identity.IdentityError):
        create_member(store, password=password)


def test_generated_password_is_returned_once_and_not_stored_plaintext(identity_db):
    _, store = identity_db
    created = store.create_user("admin", {"username": "generated", "display_name": "张三"})
    password = created["temporary_password"]
    assert 15 <= len(password) <= 128
    assert store.authenticate("generated", password)
    assert password not in json.dumps(store.list_users("admin"))
    with sqlite3.connect(store.path) as db:
        assert password not in "\n".join(db.iterdump())


def test_live_permission_and_scope_intersection(identity_db):
    identity, store = identity_db
    create_member(store, scope={"ui_apps": ["app.a"], "api_projects": ["p1"], "api_environments": ["e1"]})
    activate(store)
    ok, token = auth.login("member", NEW_PASSWORD)
    assert ok and auth.verify_session_token(token)
    assert identity.has_permission("member", "api.execute")
    assert identity.scope_allows("member", "api_projects", "p1")
    assert identity.scope_allows("member", "api_environments", "e1")
    assert not identity.scope_allows("member", "api_environments", "e2")
    assert not identity.scope_allows("admin", "unknown", "e1")
    assert not identity.scope_allows("admin", "ui_apps", None)
    assert not identity.has_permission("admin", "not.a.permission")
    store.update_user("admin", "member", {"role_ids": ["viewer"], "scope": EMPTY_SCOPE})
    assert auth.verify_session_token(token)
    assert not identity.has_permission("member", "api.execute")
    assert not identity.scope_allows("member", "api_projects", "p1")


@pytest.mark.parametrize("kind,resource_id", [("ui_apps", "app.a"), ("api_projects", "p1"), ("api_environments", "e1")])
def test_new_super_admin_empty_scope_overrides_only_after_password_change(identity_db, kind, resource_id):
    identity, store = identity_db
    created = create_member(store, role_ids=["super_admin"])
    assert created["user"]["scope"] == EMPTY_SCOPE
    assert not identity.scope_allows("member", kind, resource_id)

    activate(store)
    profile = identity.get_access_profile("member")
    assert profile["is_superuser"] and profile["scope"] == EMPTY_SCOPE
    assert identity.scope_allows("member", kind, resource_id)
    assert not identity.scope_allows("member", "unknown", resource_id)
    for invalid_id in (None, "", " ", "*"):
        assert not identity.scope_allows("member", kind, invalid_id)

    store.update_user("admin", "member", {"status": "disabled"})
    assert not identity.scope_allows("member", kind, resource_id)


def test_opaque_sessions_persist_revocation_and_disable(identity_db):
    identity, store = identity_db
    create_member(store)
    token = auth.create_session_token("member")
    assert len(token) >= 43 and "." not in token
    assert set(auth.verify_session_token(token)) == {"user", "user_id", "exp", "must_change_password"}
    with sqlite3.connect(store.path) as db:
        dump = "\n".join(db.iterdump())
    assert token not in dump
    assert hashlib.sha256(token.encode()).hexdigest() in dump
    reopened = identity.IdentityStore(store.path)
    assert reopened.verify_session(token)
    auth.logout(token)
    assert reopened.verify_session(token) is None
    token2 = auth.create_session_token("member")
    auth.REVOKED_SESSION_TOKENS.add(token2)
    assert reopened.verify_session(token2) is None
    token3 = auth.create_session_token("member")
    store.update_user("admin", "member", {"status": "disabled"})
    assert reopened.verify_session(token3) is None
    store.update_user("admin", "member", {"status": "active"})
    assert reopened.verify_session(token3) is None


def test_member_can_revoke_one_old_session_without_revoking_current(identity_db):
    _, store = identity_db
    create_member(store)
    current = auth.create_session_token("member")
    old = auth.create_session_token("member")
    old_session = next(item for item in store.list_sessions("member", current) if not item["is_current"])
    store.revoke_session("member", old_session["id"])
    assert auth.verify_session_token(current) is not None
    assert auth.verify_session_token(old) is None
    assert len(store.list_sessions("member", current)) == 1


def test_password_change_requires_current_password_and_revokes_all_prior_sessions(identity_db):
    identity, store = identity_db
    create_member(store)
    first = auth.create_session_token("member")
    second = auth.create_session_token("member")
    with pytest.raises(identity.IdentityError):
        store.change_password("member", "wrong", NEW_PASSWORD)
    changed = activate(store)
    assert auth.verify_session_token(first) is None
    assert auth.verify_session_token(second) is None
    assert auth.verify_session_token(changed["token"])["must_change_password"] is False
    assert changed["profile"]["must_change_password"] is False


def test_custom_role_crud_and_in_use_guard(identity_db):
    identity, store = identity_db
    role = store.create_role("admin", {"id": "qa-custom", "name": "接口执行员", "permissions": ["api.view", "api.execute"]})
    assert role["id"] == "qa-custom"
    create_member(store, role_ids=["qa-custom"])
    activate(store)
    assert identity.has_permission("member", "api.execute")
    store.update_role("admin", "qa-custom", {"name": "接口观察员", "permissions": ["api.view"]})
    assert not identity.has_permission("member", "api.execute")
    with pytest.raises(identity.IdentityError):
        store.delete_role("admin", "qa-custom")
    store.update_user("admin", "member", {"role_ids": []})
    store.delete_role("admin", "qa-custom")
    assert "qa-custom" not in {role["id"] for role in store.list_roles("admin")}


def test_custom_role_write_permissions_include_their_view_prerequisite(identity_db):
    _, store = identity_db
    role = store.create_role("admin", {
        "id": "api-runner",
        "name": "接口执行员",
        "permissions": ["api.execute"],
    })
    assert role["permissions"] == ["api.execute", "api.view"]
    updated = store.update_role("admin", "api-runner", {
        "permissions": ["ui.edit", "api.environment"],
    })
    assert updated["permissions"] == ["api.environment", "api.view", "ui.edit", "ui.view"]


@pytest.mark.parametrize("role_ids", [["super_admin", "tester"], ["viewer", "tester"]])
def test_member_role_identity_conflicts_are_rejected_by_store(identity_db, role_ids):
    identity, store = identity_db
    with pytest.raises(identity.IdentityError, match="不能与其他角色同时选择"):
        create_member(store, role_ids=role_ids)


@pytest.mark.parametrize("changes", [{"username": "renamed"}, {"is_superuser": True}, {"must_change_password": False},
                                    {"password_hash": "injected"}, {"role_ids": ["unknown"]},
                                    {"status": "anything"}, {"scope": {"unknown": "*"}},
                                    {"scope": {"ui_apps": ["*"]}}])
def test_user_updates_reject_invalid_or_privileged_fields(identity_db, changes):
    identity, store = identity_db
    create_member(store)
    with pytest.raises(identity.IdentityError):
        store.update_user("admin", "member", changes)


def test_only_superuser_can_grant_super_admin_and_role_is_immutable(identity_db):
    identity, store = identity_db
    store.create_role("admin", {"id": "members", "name": "成员管理员", "permissions": ["auth.manage"]})
    create_member(store, role_ids=["members"])
    activate(store)
    with pytest.raises(identity.IdentityError):
        store.update_user("member", "member", {"role_ids": ["super_admin"]})
    with pytest.raises(identity.IdentityError):
        store.create_user("member", {"username": "root2", "display_name": "超管", "role_ids": ["super_admin"]})
    with pytest.raises(identity.IdentityError):
        store.update_role("admin", "super_admin", {"permissions": []})
    with pytest.raises(identity.IdentityError):
        store.reset_password("member", "admin", NEW_PASSWORD)


@pytest.mark.parametrize("changes", [{"status": "disabled"}, {"role_ids": ["viewer"]}])
def test_last_active_super_admin_cannot_be_removed_even_concurrently(identity_db, changes):
    identity, store = identity_db
    create_member(store, username="admin2", role_ids=["super_admin"], scope=ALL_SCOPE)
    activate(store, "admin2")

    def remove(username):
        try:
            identity.IdentityStore(store.path).update_user(username, username, changes)
            return True
        except identity.IdentityError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(remove, ["admin", "admin2"]))
    assert sorted(results) == [False, True]
    assert sum(store.get_access_profile(user)["is_superuser"] for user in ["admin", "admin2"]) == 1


def test_audit_is_bounded_and_drops_untrusted_details(identity_db, monkeypatch):
    identity, store = identity_db
    monkeypatch.setattr(identity, "MAX_AUDIT_EVENTS", 5)
    for _ in range(10):
        identity.audit_event("admin", "custom", "member", {"password": PASSWORD, "nested": {"token": "secret"}, "note": PASSWORD})
    events = store.list_audit("admin")
    assert len(events) == 5
    serialized = json.dumps(events)
    assert PASSWORD not in serialized and "secret" not in serialized


def test_account_and_source_login_limits_persist_and_are_bounded(identity_db, monkeypatch):
    identity, store = identity_db
    monkeypatch.setattr(identity, "MAX_RATE_BUCKETS", 12)
    monkeypatch.setattr(identity, "ACCOUNT_ATTEMPTS", 3)
    for _ in range(3):
        assert store.authenticate("admin", "wrong", source="127.0.0.1") is None
    with pytest.raises(identity.IdentityError) as error:
        identity.IdentityStore(store.path).authenticate("admin", PASSWORD, source="127.0.0.2")
    assert error.value.status == 429
    for index in range(30):
        try:
            store.authenticate(f"unknown{index}", "wrong", source="127.0.0.3")
        except identity.IdentityError:
            pass
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM login_attempts").fetchone()[0] <= 12


def test_database_location_and_permissions(identity_db, monkeypatch, tmp_path):
    identity, store = identity_db
    assert store.path.stat().st_mode & 0o777 == 0o600
    assert store.path.parent.stat().st_mode & 0o777 == 0o700
    monkeypatch.delenv("TASK_AUTH_DB")
    monkeypatch.setattr(config, "APP_ENV", "prod")
    assert str(identity.default_db_path()) == "/opt/midscene-auth/identity.sqlite3"
    monkeypatch.setattr(config, "APP_ENV", "dev")
    monkeypatch.setattr(config, "TASK_DIR", str(tmp_path / "tasks"))
    assert identity.default_db_path() == tmp_path / ".tasks-auth" / "identity.sqlite3"


def test_custom_database_does_not_chmod_existing_parent(identity_db, tmp_path):
    identity, _ = identity_db
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    original = shared.stat().st_mode
    identity.IdentityStore(shared / "custom.sqlite3")
    assert shared.stat().st_mode == original


def test_role_version_conflicts(identity_db):
    identity, store = identity_db
    role = store.create_role("admin", {"name": "版本角色", "permissions": []})
    updated = store.update_role("admin", role["id"], {"name": "新名称", "version": role["version"]})
    assert updated["version"] == role["version"] + 1
    with pytest.raises(identity.IdentityError) as error:
        store.update_role("admin", role["id"], {"name": "过期名称", "version": role["version"]})
    assert error.value.status == 409


def test_emergency_reset_cli_requires_existing_admin_and_revokes(identity_db, monkeypatch, capsys):
    identity, store = identity_db
    create_member(store)
    token = auth.create_session_token()
    answers = iter([NEW_PASSWORD, NEW_PASSWORD])
    monkeypatch.setattr("getpass.getpass", lambda prompt: next(answers))
    assert identity.main(["reset-admin", "--username", "admin"]) == 0
    assert auth.verify_session_token(token) is None
    assert store.authenticate("admin", PASSWORD) is None
    assert store.authenticate("admin", NEW_PASSWORD)
    assert NEW_PASSWORD not in capsys.readouterr().out
    assert identity.main(["reset-admin", "--username", "missing"]) == 1
    assert identity.main(["reset-admin", "--username", "member"]) == 1


def test_online_backup_cli_copies_committed_state_and_refuses_overwrite(identity_db, tmp_path):
    identity, store = identity_db
    create_member(store)
    destination = tmp_path / "backups" / "identity.sqlite3"
    assert identity.main(["backup", "--output", str(destination)]) == 0
    assert destination.stat().st_mode & 0o777 == 0o600
    assert identity.IdentityStore(destination).get_access_profile("member")["username"] == "member"
    assert identity.main(["backup", "--output", str(destination)]) == 1


def test_cli_does_not_bootstrap_missing_database(identity_db, tmp_path, monkeypatch):
    identity, _ = identity_db
    missing = tmp_path / "missing" / "identity.sqlite3"
    monkeypatch.setenv("TASK_AUTH_DB", str(missing))
    assert identity.main(["reset-admin", "--username", "admin"]) == 1
    assert identity.main(["backup", "--output", str(tmp_path / "backup.sqlite3")]) == 1
    assert not missing.exists()


def test_unwritable_or_unowned_parent_rejected_without_modifying_it(identity_db, tmp_path, monkeypatch):
    identity, _ = identity_db
    parent = tmp_path / "shared"
    parent.mkdir()
    monkeypatch.setattr(identity.os, "access", lambda *args: False)
    with pytest.raises(identity.IdentityError):
        identity.IdentityStore(parent / "db.sqlite3")
    assert not (parent / "db.sqlite3").exists()


def limited_manager(store):
    permissions = ["auth.manage", "ui.view", "ui.edit"]
    store.create_role("admin", {"id": "limited-manager", "name": "有限管理员", "permissions": permissions})
    scope = {"ui_apps": ["app.a"], "api_projects": [], "api_environments": []}
    create_member(store, "manager", role_ids=["limited-manager"], scope=scope)
    activate(store, "manager")
    return scope


def test_delegation_ceiling_prevents_self_escalation_without_super_role(identity_db):
    identity, store = identity_db
    limited_manager(store)
    attempts = [
        lambda: store.create_role("manager", {"name": "升级权限", "permissions": ["api.production"]}),
        lambda: store.update_role("manager", "limited-manager", {"permissions": ["auth.manage", "platform.configure"]}),
        lambda: store.update_role("manager", "test_manager", {"permissions": ["ui.view"]}),
        lambda: store.update_user("manager", "manager", {"scope": ALL_SCOPE}),
        lambda: store.update_user("manager", "manager", {"role_ids": ["test_manager"]}),
        lambda: store.create_user("manager", {"username": "elevated", "display_name": "越权用户", "role_ids": ["test_manager"]}),
        lambda: store.create_user("manager", {"username": "elevated", "display_name": "越权用户", "role_ids": [], "scope": {"ui_apps": "*"}}),
    ]
    for attempt in attempts:
        with pytest.raises(identity.IdentityError) as error:
            attempt()
        assert error.value.status == 403
    assert not identity.has_permission("manager", "platform.configure")
    assert not identity.has_permission("manager", "api.production")
    assert not identity.scope_allows("manager", "ui_apps", "app.b")


def test_limited_manager_can_delegate_only_subset_and_cannot_take_over_larger_user(identity_db):
    identity, store = identity_db
    scope = limited_manager(store)
    role = store.create_role("manager", {"id": "reader", "name": "受限读者", "permissions": ["ui.view"]})
    created = store.create_user("manager", {"username": "reader", "display_name": "读者", "role_ids": [role["id"]], "scope": scope})
    assert created["user"]["scope"] == scope
    store.update_user("manager", "reader", {"display_name": "新名称"})
    create_member(store, "outside", role_ids=["reader"], scope={"ui_apps": ["app.b"]})
    create_member(store, "powerful", role_ids=["test_manager"])
    for username in ["outside", "powerful"]:
        for action in [lambda: store.update_user("manager", username, {"role_ids": []}),
                       lambda: store.reset_password("manager", username, NEW_PASSWORD),
                       lambda: store.revoke_sessions("manager", username)]:
            with pytest.raises(identity.IdentityError) as error:
                action()
            assert error.value.status == 403
    with pytest.raises(identity.IdentityError):
        store.update_role("manager", "reader", {"permissions": ["ui.view", "ui.edit"]})


def test_delegation_rechecks_actor_after_hashing(identity_db, monkeypatch):
    identity, store = identity_db
    limited_manager(store)
    original = identity._hash_password

    def revoke_then_hash(password):
        store.update_user("admin", "manager", {"role_ids": ["viewer"]})
        return original(password)

    monkeypatch.setattr(identity, "_hash_password", revoke_then_hash)
    with pytest.raises(identity.IdentityError) as error:
        store.create_user("manager", {"username": "new", "display_name": "新成员", "role_ids": []})
    assert error.value.status == 403
    assert store.get_access_profile("new") is None


def test_audit_preserves_only_safe_request_metadata(identity_db):
    identity, store = identity_db
    identity.audit_event("admin", "operation.result", "/api/users", {"method": "PUT", "status": 403, "ok": False, "password": PASSWORD})
    assert store.list_audit("admin")[0]["details"] == {"method": "PUT", "status": 403, "ok": False}
    identity.audit_event("admin", "operation.result", "/api/users", {"permission": [], "method": {"bad": "value"}, "status": True, "ok": "secret"})
    assert store.list_audit("admin")[0]["details"] == {}


def test_long_username_cannot_alias_existing_username(identity_db):
    _, store = identity_db
    create_member(store, "a" * 128)
    assert store.authenticate("a" * 129, PASSWORD) is None


def test_unknown_and_disabled_accounts_run_same_argon_verification(identity_db, monkeypatch):
    identity, store = identity_db
    store.authenticate("admin", PASSWORD)
    create_member(store)
    store.update_user("admin", "member", {"status": "disabled"})
    hashes = []
    original = identity._verify_password

    def track(encoded, password):
        hashes.append(encoded)
        return original(encoded, password)

    monkeypatch.setattr(identity, "_verify_password", track)
    for username in ["admin", "missing", "member"]:
        assert store.authenticate(username, "wrong") is None
    assert len(hashes) == 3
    assert all(encoded.startswith("$argon2id$v=19$m=19456,t=2,p=1$") for encoded in hashes)


def test_hash_and_verify_share_two_concurrency_slots(identity_db, monkeypatch):
    identity, _ = identity_db
    encoded = identity._hash_password(PASSWORD)
    original = identity._HASHER
    lock = threading.Lock()
    active = 0
    peak = 0

    class Tracker:
        def run(self, function, *args):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(active, peak)
            try:
                time.sleep(0.02)
                return function(*args)
            finally:
                with lock:
                    active -= 1

        def hash(self, password):
            return self.run(original.hash, password)

        def verify(self, encoded, password):
            return self.run(original.verify, encoded, password)

    monkeypatch.setattr(identity, "_HASHER", Tracker())
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda index: identity._hash_password(PASSWORD) if index % 2 else identity._verify_password(encoded, PASSWORD), range(8)))
    assert len(results) == 8 and peak == 2


def test_login_cannot_issue_session_after_concurrent_reset(identity_db, monkeypatch):
    identity, store = identity_db
    store.authenticate("admin", PASSWORD)
    original = identity._verify_password

    def reset_after_verification(encoded, password):
        result = original(encoded, password)
        store._emergency_reset_admin("admin", NEW_PASSWORD)
        return result

    monkeypatch.setattr(identity, "_verify_password", reset_after_verification)
    assert store.login("admin", PASSWORD) is None
    assert store.list_sessions("admin") == []


def test_session_expiry_and_retention(identity_db, monkeypatch):
    identity, store = identity_db
    monkeypatch.setattr(identity, "MAX_SESSIONS_PER_USER", 2)
    first = auth.create_session_token()
    second = auth.create_session_token()
    third = auth.create_session_token()
    assert auth.verify_session_token(first) is None
    assert auth.verify_session_token(second) and auth.verify_session_token(third)
    expiry = auth.verify_session_token(third)["exp"]
    monkeypatch.setattr(identity.time, "time", lambda: expiry)
    assert auth.verify_session_token(third) is None


def test_empty_machine_credentials_never_authorize(identity_db, monkeypatch):
    monkeypatch.setattr(auth, "TOKEN", "")
    monkeypatch.setattr(auth, "SONIC_CALLBACK_TOKEN", "")
    monkeypatch.setattr(auth, "ALLOW_QUERY_TOKEN", True)
    assert not auth.is_user_authorized({})
    assert not auth.is_runner_authorized({})
    assert not auth.is_sonic_callback_authorized({})
    assert not auth.is_authorized_with_query({}, {})
    assert auth.bearer_token({"Authorization": None}) == ""


def test_role_names_remain_aligned_for_combined_write_roles(identity_db):
    identity, store = identity_db
    create_member(store, role_ids=["test_manager", "tester"])
    activate(store)
    profile = identity.get_access_profile("member")
    assert list(zip(profile["role_ids"], profile["role_names"])) == [("test_manager", "测试负责人"), ("tester", "测试成员")]


def test_real_cli_help_is_clean_and_does_not_create_database(tmp_path):
    path = tmp_path / "private" / "identity.sqlite3"
    result = subprocess.run([sys.executable, "-m", "task_server.identity", "--help"],
                            env=dict(os.environ, TASK_AUTH_DB=str(path)), capture_output=True, text=True)
    assert result.returncode == 0
    assert result.stderr == ""
    assert not path.exists()


def test_digest_session_check_binds_user_and_observes_logout_reset_disable_expiry(identity_db, monkeypatch):
    identity, store = identity_db
    create_member(store)

    def session():
        token = auth.create_session_token("member")
        return token, hashlib.sha256(token.encode()).hexdigest()

    token, digest = session()
    assert identity.session_is_active("member", digest)
    assert not identity.session_is_active("admin", digest)
    assert not identity.session_is_active("missing", digest)
    assert not identity.session_is_active("member", token)
    assert not identity.session_is_active("member", None)
    auth.logout(token)
    assert not identity.session_is_active("member", digest)
    _, digest = session()
    store.revoke_sessions("member")
    assert not identity.session_is_active("member", digest)
    _, digest = session()
    store.reset_password("admin", "member", NEW_PASSWORD)
    assert not identity.session_is_active("member", digest)
    _, digest = session()
    store.update_user("admin", "member", {"status": "disabled"})
    assert not identity.session_is_active("member", digest)
    store.update_user("admin", "member", {"status": "active"})
    assert not identity.session_is_active("member", digest)
    token, digest = session()
    expires = auth.verify_session_token(token)["exp"]
    monkeypatch.setattr(identity.time, "time", lambda: expires)
    assert not identity.session_is_active("member", digest)
