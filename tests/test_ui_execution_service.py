"""Trusted UI batch creation and read access."""

import hashlib

import pytest

from task_server.services.operation_attribution import actor_context


APP = "com.kfb.model"
SOURCE = "android: {}\ntasks:\n- name: 导航\n  flow:\n  - launch: com.kfb.model\n  - aiTap: 我的\n  - aiAssert: 我的页面\n"
REVISION = hashlib.sha256(SOURCE.encode()).hexdigest()
TARGET = {"runner_id": "mac", "device_id": "iphone", "platform": "ios"}
ACTORS = {
    "a": {"kind": "user", "username": "a", "user_id": "u-a", "display_name": "作者A"},
    "b": {"kind": "user", "username": "b", "user_id": "u-b", "display_name": "执行者B"},
    "admin": {"kind": "user", "username": "admin", "user_id": "u-admin", "display_name": "管理员"},
}


@pytest.fixture
def environment(tmp_path, monkeypatch):
    from task_server import identity
    from task_server.services import business_line_service, case_service, runner_service

    monkeypatch.setenv("TASK_UI_EXECUTION_DB", str(tmp_path / "private" / "ui.sqlite3"))
    monkeypatch.setenv("UI_SIMPLE_FLOW_ENABLED", "1")
    monkeypatch.delenv("UI_BATCH_NOTIFICATIONS_ENABLED", raising=False)
    monkeypatch.setattr(identity, "get_access_profile", lambda name: {
        "username": name, "user_id": ACTORS[name]["user_id"], "display_name": ACTORS[name]["display_name"],
        "status": "active", "permissions": ["ui.view", "ui.execute"],
        "scope": {"ui_apps": [APP]}, "is_superuser": name == "admin", "must_change_password": False,
    } if name in ACTORS else None)
    monkeypatch.setattr(business_line_service, "configured_test_application", lambda app_id: {
        "package": APP, "enabled": True, "platform_mappings": {"android": APP, "ios": "com.kfb.3d"}
    } if app_id == APP else None)
    monkeypatch.setattr(runner_service, "list_runners", lambda: {"mac": {
        "online": True, "capabilities": {"ui_execution_contract": "ui-v1", "ui_platforms": ["ios"],
        "ui_actions": ["launch", "aiTap", "aiWaitFor", "aiAssert"], "job_execution": True},
        "devices": [{"device_id": "iphone", "platform": "ios", "status": "online", "preflight_status": "ready"}],
    }})
    asset = {"case_id": "case-1", "module": "shared", "file": "same.yaml", "task_name": "导航",
             "app_package": APP, "status": "active", "_root": "/private/source"}
    monkeypatch.setattr(case_service, "list_task_case_assets", lambda: [asset])
    monkeypatch.setattr(case_service, "task_case_yaml", lambda row: SOURCE)
    return asset


def request():
    return {"app_id": APP, "cases": [{"case_id": "case-1", "revision": REVISION}], "targets": [TARGET],
            "test_data_refs": [], "notifications": {"enabled": False}}


def test_shared_baseline_uses_executor_identity_and_immutable_compilation(environment):
    from task_server.services.ui_execution_service import create_ui_batch, get_ui_batch

    with actor_context(ACTORS["b"]):
        batch = create_ui_batch(ACTORS["b"], request(), "click-1")
        saved = get_ui_batch(ACTORS["b"], batch["ui_batch_id"])
    assert saved["owner_user_id"] == "u-b"
    assert saved["owner_display_name"] == "执行者B"
    assert saved["snapshots"][0]["metadata"]["file"] == "same.yaml"
    assert "_root" not in str(saved)
    assert "com.kfb.3d" in saved["case_attempts"][0]["compiled_case"]["yaml_content"]
    assert saved["case_attempts"][0]["target_resolution"]["target_package"] == "com.kfb.3d"
    assert len(saved["case_attempts"][0]["target_resolution"]["mapping_revision"]) == 64
    assert saved["status"] == "queued"


def test_forged_tester_and_actor_context_loss_are_denied(environment):
    from task_server.services.ui_execution_service import BatchValidationError, create_ui_batch

    with actor_context(ACTORS["b"]):
        with pytest.raises(BatchValidationError):
            create_ui_batch(ACTORS["b"], {**request(), "tester": "a"}, "forge")
        with pytest.raises(PermissionError):
            create_ui_batch(ACTORS["a"], request(), "forge")
    with pytest.raises(PermissionError):
        create_ui_batch(ACTORS["b"], request(), "context-lost")


def test_revision_drift_rejects_before_write(environment, monkeypatch):
    from task_server.services.ui_execution_service import BatchValidationError, create_ui_batch, list_ui_batches

    with actor_context(ACTORS["b"]):
        bad = request()
        bad["cases"][0]["revision"] = "0" * 64
        with pytest.raises(BatchValidationError):
            create_ui_batch(ACTORS["b"], bad, "stale")
        assert list_ui_batches(ACTORS["b"], {}, "", 20)["items"] == []


def test_owner_or_superuser_can_read_only_with_app_scope(environment, monkeypatch):
    from task_server.services.ui_execution_service import create_ui_batch, get_ui_batch, list_ui_batches

    with actor_context(ACTORS["b"]):
        batch = create_ui_batch(ACTORS["b"], request(), "owned")
    with actor_context(ACTORS["a"]):
        assert get_ui_batch(ACTORS["a"], batch["ui_batch_id"]) is None
        assert list_ui_batches(ACTORS["a"], {}, "", 20)["items"] == []
    with actor_context(ACTORS["admin"]):
        assert get_ui_batch(ACTORS["admin"], batch["ui_batch_id"])["ui_batch_id"] == batch["ui_batch_id"]
        assert list_ui_batches(ACTORS["admin"], {}, "", 20)["items"] == []
        assert list_ui_batches(ACTORS["admin"], {"scope":"all"}, "", 20)["items"][0]["ui_batch_id"] == batch["ui_batch_id"]
    from task_server import identity
    original = identity.get_access_profile
    monkeypatch.setattr(identity, "get_access_profile", lambda name: {
        **original(name), "scope": {"ui_apps": []}
    } if name == "b" else original(name))
    with actor_context(ACTORS["b"]):
        with pytest.raises(PermissionError):
            get_ui_batch(ACTORS["b"], batch["ui_batch_id"])
        assert list_ui_batches(ACTORS["b"], {}, "", 20)["items"] == []
    monkeypatch.setattr(identity, "get_access_profile", lambda name: {
        **original(name), "scope": {"ui_apps": []}
    } if name == "admin" else original(name))
    with actor_context(ACTORS["admin"]):
        with pytest.raises(PermissionError):
            get_ui_batch(ACTORS["admin"], batch["ui_batch_id"])
        assert list_ui_batches(ACTORS["admin"], {}, "", 20)["items"] == []
    monkeypatch.setattr(identity, "get_access_profile", lambda name: {
        **original(name), "scope": {"ui_apps": "*"}
    } if name == "a" else original(name))
    with actor_context(ACTORS["a"]):
        assert get_ui_batch(ACTORS["a"], batch["ui_batch_id"]) is None  # wildcard does not grant ownership
        assert list_ui_batches(ACTORS["a"], {"app_id": APP}, "", 20)["items"] == []


def test_same_name_two_users_do_not_share_batch_or_dispatch_key(environment):
    from task_server.services.ui_execution_service import create_ui_batch

    with actor_context(ACTORS["a"]):
        first = create_ui_batch(ACTORS["a"], request(), "same-click")
    with actor_context(ACTORS["b"]):
        second = create_ui_batch(ACTORS["b"], request(), "same-click")
    assert first["ui_batch_id"] != second["ui_batch_id"]
    assert first["case_attempts"][0]["dispatch_key"] != second["case_attempts"][0]["dispatch_key"]


def test_old_run_without_batch_has_no_inferred_owner(environment):
    from task_server.services.ui_execution_service import get_ui_batch

    with actor_context(ACTORS["b"]):
        assert get_ui_batch(ACTORS["b"], "legacy-run-id") is None


def test_idempotent_retry_reads_frozen_batch_after_source_edit(environment, monkeypatch):
    from task_server.services.ui_execution_service import create_ui_batch
    from task_server.services import case_service

    with actor_context(ACTORS["b"]):
        first = create_ui_batch(ACTORS["b"], request(), "retry-after-edit")
        monkeypatch.setattr(case_service, "task_case_yaml", lambda row: SOURCE.replace("我的页面", "首页"))
        second = create_ui_batch(ACTORS["b"], request(), "retry-after-edit")
    assert first == second


def test_notification_preference_is_frozen_without_accepting_channel_override(environment, monkeypatch):
    from task_server.services.ui_execution_service import BatchValidationError, create_ui_batch

    from task_server import identity
    original = identity.get_access_profile
    monkeypatch.setattr(identity, "get_access_profile", lambda name: {**original(name), "permissions":["ui.view", "ui.execute", "platform.notify"]})
    with actor_context(ACTORS["b"]):
        selected = request()
        selected["notifications"] = {"enabled": True}
        batch = create_ui_batch(ACTORS["b"], selected, "notify-enabled")
        assert batch["selection"]["notifications"]["enabled"] is True
        assert "channel_id" in batch["selection"]["notifications"]
        assert "webhook" not in batch["selection"]["notifications"]
        assert batch["delivery_status"] == "pending"
        selected["notifications"]["channel_id"] = "foreign-channel"
        with pytest.raises(BatchValidationError):
            create_ui_batch(ACTORS["b"], selected, "foreign-channel")


def test_missing_source_returns_stable_error_without_storage(environment, monkeypatch):
    from task_server.services.ui_execution_service import BatchValidationError, create_ui_batch, list_ui_batches
    from task_server.services import case_service

    def missing(_):
        raise FileNotFoundError("/private/source/secret.yaml")

    monkeypatch.setattr(case_service, "task_case_yaml", missing)
    with actor_context(ACTORS["b"]):
        with pytest.raises(BatchValidationError) as error:
            create_ui_batch(ACTORS["b"], request(), "missing-source")
        assert error.value.code == "case_unavailable"
        assert list_ui_batches(ACTORS["b"], {}, "", 20)["items"] == []


def test_request_rejects_duplicate_or_untrusted_target_and_freeform_yaml(environment):
    from task_server.services.ui_execution_service import BatchValidationError, create_ui_batch

    with actor_context(ACTORS["b"]):
        for bad in (
            {**request(), "targets": [TARGET, TARGET]},
            {**request(), "targets": [{**TARGET, "device_id": "other"}]},
            {**request(), "yaml_content": SOURCE},
            {**request(), "test_data_refs": ["../secret"]},
        ):
            with pytest.raises(BatchValidationError):
                create_ui_batch(ACTORS["b"], bad, "bad")


def test_access_policy_grants_ui_execute_and_view_without_platform_configure(environment):
    from task_server.access_control import MainAccess
    from task_server import identity

    profile = identity.get_access_profile("b")
    policy = MainAccess(profile, catalog=[])
    policy.check("POST", "/api/ui-executions", {}, request())
    policy.check("GET", "/api/ui-executions", {}, {})
    policy.check("GET", "/api/ui-executions/uib_" + "1" * 32, {}, {})
    with pytest.raises(PermissionError):
        policy.check("PUT", "/api/ui-executions/uib_" + "1" * 32, {}, {})


def test_route_feature_flag_defaults_closed_and_enabled_route_returns_batch(environment, monkeypatch):
    from task_server import router

    class Handler:
        headers = {"Idempotency-Key": "click-1"}

        def __init__(self):
            self.response = None

        def _body(self):
            return request()

        def _json(self, payload, status=200):
            self.response = payload, status

    handler = Handler()
    monkeypatch.delenv("UI_SIMPLE_FLOW_ENABLED", raising=False)
    with actor_context(ACTORS["b"]):
        router.POST_ROUTES["/api/ui-executions"](handler, {})
        assert handler.response[1] == 404
        monkeypatch.setenv("UI_SIMPLE_FLOW_ENABLED", "1")
        router.POST_ROUTES["/api/ui-executions"](handler, {})
        assert handler.response[1] == 201
        batch_id = handler.response[0]["batch"]["ui_batch_id"]
        router.GET_ROUTES["/api/ui-executions"](handler, {})
        assert handler.response[0]["items"][0]["ui_batch_id"] == batch_id
        get_detail = next(fn for regex, fn in router._GET_REGEX_ROUTES if regex.pattern.startswith("^/api/ui-executions/"))
        import re
        get_detail(handler, {}, re.match(r"^/api/ui-executions/([^/]+)$", "/api/ui-executions/" + batch_id))
        assert handler.response[0]["batch"]["ui_batch_id"] == batch_id


def test_route_hides_database_error(environment, monkeypatch):
    import sqlite3
    from task_server import router
    from task_server.services import ui_execution_service

    monkeypatch.setenv("UI_SIMPLE_FLOW_ENABLED", "1")
    monkeypatch.setattr(ui_execution_service, "_store", lambda: (_ for _ in ()).throw(sqlite3.OperationalError("secret SQL /private/path")))

    class Handler:
        headers = {"Idempotency-Key": "click-1"}

        def _body(self):
            return request()

        def _json(self, payload, status=200):
            self.response = payload, status

    handler = Handler()
    with actor_context(ACTORS["b"]):
        router.POST_ROUTES["/api/ui-executions"](handler, {})
    assert handler.response[1] == 503
    assert "secret" not in str(handler.response) and "private" not in str(handler.response)


def test_team_batch_scope_requires_explicit_administrator(environment):
    from task_server.services.ui_execution_service import list_ui_batches
    with actor_context(ACTORS["a"]):
        with pytest.raises(PermissionError):
            list_ui_batches(None, {"scope":"all"})
        with pytest.raises(ValueError):
            list_ui_batches(None, {"scope":"someone_else"})


def test_creation_freezes_business_report_labels_and_scope(environment, monkeypatch):
    from task_server.services import business_line_service, ui_execution_service as service
    original = business_line_service.configured_test_application
    monkeypatch.setattr(business_line_service, 'configured_test_application', lambda app: {**original(app), 'name':'智小白3D'})
    environment.update(goal='个人中心与首页之间切换', expected='两个入口均正常展示')
    with actor_context(ACTORS['b']):
        batch=service.create_ui_batch(None,request(),'business-report')
        environment.update(goal='随后被修改的场景',expected='随后修改的结果')
        frozen=service.get_ui_batch(None,batch['ui_batch_id'])['snapshots'][0]['metadata']
    assert frozen['app_name']=='智小白3D'
    assert frozen['goal']=='个人中心与首页之间切换'
    assert frozen['expected']=='两个入口均正常展示'


def test_no_notify_actor_rejected_for_create_preflight_and_repeat_but_can_run_opt_out(environment, monkeypatch):
    from task_server import identity
    from task_server.services.ui_execution_service import create_ui_batch, preflight_ui_batch
    selected=request(); selected['notifications']['enabled']=True
    original=identity.get_access_profile
    monkeypatch.setattr(identity,'get_access_profile',lambda name:{**original(name),'permissions':['ui.view','ui.execute','platform.notify']})
    with actor_context(ACTORS['b']): first=create_ui_batch(None,selected,'repeat-after-revoke')
    monkeypatch.setattr(identity,'get_access_profile',original)
    with actor_context(ACTORS['b']):
        for operation in (lambda: create_ui_batch(None,selected,'denied'),
                          lambda: create_ui_batch(None,selected,'repeat-after-revoke'),
                          lambda: preflight_ui_batch(None,selected)):
            with pytest.raises(PermissionError): operation()
        omitted=request(); del omitted['notifications']
        assert create_ui_batch(None,omitted,'omitted')['selection']['notifications']['enabled'] is False
        assert create_ui_batch(None,request(),'false')['selection']['notifications']['enabled'] is False
    assert first['selection']['notifications']['enabled'] is True
