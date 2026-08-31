import pytest


@pytest.fixture
def policy():
    from task_server.access_control import MainAccess

    return MainAccess({
        "username": "member", "is_superuser": False,
        "permissions": ["ui.view", "ui.edit", "ui.execute"],
        "scope": {"ui_apps": ["app.a"]},
    }, [{"package": "app.a", "modules": ["A"]}, {"package": "app.b", "modules": ["B"]}])


def test_readonly_cannot_write_even_with_all_data():
    from task_server.access_control import AccessDenied, MainAccess
    access = MainAccess({"permissions": ["ui.view"], "scope": {"ui_apps": "*"}}, [])
    with pytest.raises(AccessDenied, match="ui.edit"):
        access.check("POST", "/api/file", body={"module": "A"})


def test_file_scope_uses_persisted_module_not_claimed_app(policy):
    from task_server.access_control import AccessDenied
    policy.check("GET", "/api/file", {"module": "A", "file": "case.yaml"})
    with pytest.raises(AccessDenied, match="数据范围"):
        policy.check("POST", "/api/file", body={"module": "B", "app_package": "app.a"})
    with pytest.raises(AccessDenied):
        policy.check("POST", "/api/file", body={"module": "unknown", "app_package": "app.a"})


def test_batch_copy_checks_every_source_and_destination(policy):
    from task_server.access_control import AccessDenied
    policy.check("POST", "/api/files/op", body={"items": [{"module": "A"}], "targetModule": "A", "op": "copy"})
    with pytest.raises(AccessDenied):
        policy.check("POST", "/api/files/op", body={"items": [{"module": "A"}, {"module": "B"}], "targetModule": "A", "op": "copy"})
    with pytest.raises(AccessDenied):
        policy.check("POST", "/api/file/op", body={"module": "A", "targetModule": "B", "op": "copy"})


def test_move_requires_delete_permission(policy):
    from task_server.access_control import AccessDenied
    with pytest.raises(AccessDenied, match="ui.delete"):
        policy.check("POST", "/api/file/op", body={"module": "A", "op": "rename"})


def test_collections_are_filtered_without_returning_foreign_totals(policy):
    assert policy.filter("/api/modules", {"A": ["a.yaml"], "B": ["b.yaml"]}) == {"A": ["a.yaml"]}
    data = policy.filter("/api/jobs", {
        "ok": True, "jobs": [{"module": "A"}, {"module": "B"}, {}],
        "background_jobs": [{"app_package": "app.b"}], "history_scope": {"runner_returned": 3},
    })
    assert data["jobs"] == [{"module": "A"}]
    assert data["background_jobs"] == []
    assert data["history_scope"]["runner_returned"] == 1
    assert policy.filter("/api/task-meta", {"ok": True, "meta": {"A/a.yaml": {"module": "A"}, "B/b.yaml": {"module": "B"}}})["meta"] == {"A/a.yaml": {"module": "A"}}


def test_catalog_does_not_expose_webhook_to_member(policy):
    result = policy.filter("/api/task-apps", {"apps": [{"package": "app.a", "modules": ["A"], "feishu_webhook": "secret"}, {"package": "app.b"}]})
    assert len(result["apps"]) == 1
    assert "feishu_webhook" not in result["apps"][0]


def test_detail_uses_saved_record_not_request_claim(policy, monkeypatch):
    from task_server import access_control as ac
    monkeypatch.setattr(ac, "load_access_record", lambda kind, key: {"app_package": "app.b"})
    with pytest.raises(ac.AccessDenied):
        policy.check("POST", "/api/agent-runs/run-1/cancel", body={"app_package": "app.a"})
    with pytest.raises(ac.AccessDenied):
        policy.check("GET", "/api/ui/generate-status", {"job_id": "job-1", "app_package": "app.a"})


def test_password_change_is_mandatory_even_for_superuser():
    from task_server.access_control import AccessDenied, MainAccess
    access = MainAccess({"is_superuser": True, "must_change_password": True}, [])
    with pytest.raises(AccessDenied, match="修改密码"):
        access.check("GET", "/api/modules")


def test_unknown_or_global_routes_do_not_inherit_view_permission(policy):
    from task_server.access_control import AccessDenied
    for method, path in [("POST", "/api/new-admin-action"), ("GET", "/api/debug/traces"), ("POST", "/api/sonic/publish-batch")]:
        with pytest.raises(AccessDenied):
            policy.check(method, path)


def test_ambiguous_module_and_path_traversal_fail_closed(policy):
    from task_server.access_control import AccessDenied
    policy.module_apps["shared"] = {"app.a", "app.b"}
    for module in ("shared", "A/../B", "../A", "A\\B"):
        with pytest.raises(AccessDenied):
            policy.check("GET", "/api/file", {"module": module, "file": "x.yaml"})


def test_agent_creation_requires_explicit_authorized_application(policy):
    from task_server.access_control import AccessDenied
    # Autonomous tools can inspect shared history; app-scoped permission is insufficient.
    with pytest.raises(AccessDenied, match="platform.configure"):
        policy.check("POST", "/api/agent-runs/start", body={"app_package": "app.a"})
    with pytest.raises(AccessDenied):
        policy.check("POST", "/api/agent-runs/start", body={"appName": "some allowed sounding name"})


def test_generation_cannot_write_foreign_module_using_allowed_summary(policy, monkeypatch):
    from task_server import access_control as ac
    monkeypatch.setattr(ac, "load_access_record", lambda kind, key: {"app_package": "app.a", "module": "A"})
    with pytest.raises(ac.AccessDenied):
        policy.check("POST", "/api/cases/ui-designs", body={"case_set_id": "allowed", "module": "B"})


def test_combined_generate_and_run_requires_execute_even_with_all_scope():
    from task_server.access_control import AccessDenied, MainAccess
    access = MainAccess({"permissions": ["ui.view", "ui.edit"], "scope": {"ui_apps": "*"}}, [])
    with pytest.raises(AccessDenied, match="ui.execute"):
        access.check("POST", "/api/ui/generate-yaml-async", body={"createJob": True})
    with pytest.raises(AccessDenied, match="ui.execute"):
        access.check("POST", "/api/jobs/existing/repair", body={})


def test_generation_references_are_not_authorized_by_outer_application(policy, monkeypatch):
    from task_server import access_control as ac
    monkeypatch.setattr(ac, "load_access_record", lambda kind, key: {"app_package": "app.b"})
    with pytest.raises(ac.AccessDenied):
        policy.check("POST", "/api/ui/generate-yaml-async", body={"app_package": "app.a", "module": "A", "case_set_id": "foreign"})


def test_generation_records_include_persisted_request_data_scope(policy):
    assert policy.visible({"request_data": {"module": "A", "app_package": "app.a"}})
    assert not policy.visible({"app_package": "app.a", "request_data": {"module": "B"}})


def test_scoped_members_cannot_use_global_baseline_retrieval(policy):
    from task_server.access_control import AccessDenied
    with pytest.raises(AccessDenied, match="共用基线库"):
        policy.check("POST", "/api/ui/generate-yaml-async", body={"app_package": "app.a", "module": "A"})


def test_execution_permission_does_not_allow_promoting_ui_baselines(policy):
    from task_server.access_control import AccessDenied
    with pytest.raises(AccessDenied, match="ui.baseline"):
        policy.check("POST", "/api/run-request", body={"module": "A", "run_mode": "baseline"})


def test_read_only_get_cannot_trigger_cache_rebuild(policy):
    from task_server.access_control import AccessDenied
    with pytest.raises(AccessDenied):
        policy.check("GET", "/api/yaml/baseline-cache/status", {"force": "true"})


def test_authorization_and_handler_share_one_parsed_body():
    import io
    from task_server.response import ResponseMixin

    class Handler(ResponseMixin):
        headers = {"Content-Length": "14"}
        path = "/api/file"
        rfile = io.BytesIO(b'{"module":"A"}')

    handler = Handler()
    first = handler._body()
    assert first == {"module": "A"}
    assert handler._body() is first


def test_installer_preserves_identity_outside_release_directory():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    installer = (root / "deploy/install-server.sh").read_text()
    assert 'ensure_env_default "TASK_AUTH_DB" "/opt/midscene-auth/identity.sqlite3"' in installer
    assert '身份数据库不能放在发布目录内' in installer
    assert '账号与权限数据保存在' in installer
    assert 'realpath -m -- "${identity_db}"' in installer


def test_gateway_auth_is_packaged_and_started_with_backend_port():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    installer = (root / "deploy/install-server.sh").read_text()
    packager = (root / "deploy/package-server.sh").read_text()
    assert 'gateway-auth.js' in installer
    assert 'gateway-auth.js' in packager
    assert 'cp "${SRC_DIR}/requirements-api-testing.txt" "${pkg_dir}/"' in packager
    assert 'cp "${SRC_DIR}/requirements-api-testing-dev.txt" "${pkg_dir}/"' in packager
    assert 'AI_GATEWAY_AUTH_BASE_URL:-http://127.0.0.1:${PORT}' in installer
    assert 'AI_GATEWAY_AUTH_BASE_URL="${AI_GATEWAY_AUTH_BASE_URL}" pm2' in installer


def test_delegated_admin_scope_picker_does_not_disclose_foreign_names():
    from task_server.access_control import filter_scope_options
    profile = {"scope": {"ui_apps": ["app.a"], "api_projects": ["p1"], "api_environments": ["e1", "e2"]}}
    options = {"ui_apps": [{"id": "app.a"}, {"id": "app.b"}], "api_projects": [{"id": "p1"}, {"id": "p2"}],
               "api_environments": [{"id": "e1", "project_id": "p1"}, {"id": "e2", "project_id": "p2"}]}
    result = filter_scope_options(profile, options)
    assert result == {"ui_apps": [{"id": "app.a"}], "api_projects": [{"id": "p1"}], "api_environments": [{"id": "e1", "project_id": "p1"}]}


def test_case_pagination_counts_authorized_rows_before_slicing(policy, monkeypatch):
    from task_server import router
    from task_server.services import case_service
    rows = [{"module": "B", "file": "foreign.yaml"}] + [{"module": "A", "file": f"case-{i}.yaml"} for i in range(3)]
    monkeypatch.setattr(case_service, "list_all_cases", lambda: rows)

    class Handler:
        _main_access = policy
        def _json(self, payload):
            self.payload = policy.filter("/api/cases", payload)

    handler = Handler()
    router._get_cases_list(handler, {"pageSize": "2"})
    assert [row["file"] for row in handler.payload["cases"]] == ["case-0.yaml", "case-1.yaml"]
    assert handler.payload["total"] == 3
    assert handler.payload["hasMore"] is True


def test_unavailable_scope_catalog_returns_retriable_error_without_empty_options(monkeypatch):
    from types import SimpleNamespace
    from task_server import access_control as ac, auth, identity
    from task_server.api_testing import access
    from task_server.api_testing.config import ApiTestingSettings

    profile = {"username": "manager", "permissions": ["auth.manage"], "scope": {"ui_apps": "*"}}
    monkeypatch.setattr(auth, "verify_session_token", lambda token: {"user": "manager"})
    monkeypatch.setattr(identity, "get_access_profile", lambda username: profile)
    monkeypatch.setattr(ac, "application_catalog", lambda: [])
    monkeypatch.setattr(ApiTestingSettings, "from_env", lambda: SimpleNamespace(enabled=True))

    def unavailable():
        raise RuntimeError("database connection details must not be exposed")

    monkeypatch.setattr(access, "list_identity_scope_options", unavailable)

    class Handler:
        headers = {"Authorization": "Bearer fixture"}

        def _json(self, payload, code=200):
            self.payload, self.code = payload, code

    handler = Handler()
    assert ac.prepare_request_access(handler, "GET", "/api/auth/scope-options", {})
    assert handler.code == 503
    assert handler.payload["code"] == "scope_options_unavailable"
    assert "原授权未变" in handler.payload["error"]
    assert "api_projects" not in handler.payload
    assert "database" not in handler.payload["error"]


@pytest.mark.parametrize("path", ["/api/runner/heartbeat", "/api/runner/jobs/job-1/progress", "/api/runner/jobs/job-1/result", "/api/sonic/result", "/report"])
def test_interactive_member_cannot_impersonate_machine_callback(monkeypatch, path):
    from task_server import access_control as ac, auth, identity
    profile = {"username": "viewer", "permissions": ["ui.view"], "scope": {"ui_apps": "*"}}
    monkeypatch.setattr(auth, "verify_session_token", lambda token: {"user": "viewer"} if token else None)
    monkeypatch.setattr(identity, "get_access_profile", lambda username: profile)
    monkeypatch.setattr(identity, "audit_event", lambda *args: None)
    monkeypatch.setattr(ac, "application_catalog", lambda: [])

    class Handler:
        headers = {"Authorization": "Bearer fixture", "x-token": "invalid-machine-token"}

        def _json(self, payload, code=200):
            self.payload, self.code = payload, code

    handler = Handler()
    assert ac.prepare_request_access(handler, "POST", path, {})
    assert handler.code == 403
    assert "Runner" in handler.payload["error"]


@pytest.mark.parametrize("path", ["/api/sonic/runtime-env", "/api/sonic/case", "/api/sonic/case-yaml", "/api/sonic/bridge-groovy", "/api/modules", "/api/runners"])
def test_existing_machine_reads_keep_dedicated_token_channel(monkeypatch, path):
    from task_server import access_control as ac, auth
    monkeypatch.setattr(auth, "TOKEN", "isolated-runner-token")
    monkeypatch.setattr(auth, "verify_session_token", lambda token: None)

    class Handler:
        headers = {"x-token": "isolated-runner-token"}

        def _json(self, payload, code=200):
            self.payload, self.code = payload, code

    assert ac.prepare_request_access(Handler(), "GET", path, {}) is False


def test_machine_token_does_not_open_human_management(monkeypatch):
    from task_server import access_control as ac, auth
    monkeypatch.setattr(auth, "TOKEN", "isolated-runner-token")
    monkeypatch.setattr(auth, "verify_session_token", lambda token: None)

    class Handler:
        headers = {"x-token": "isolated-runner-token"}

        def _json(self, payload, code=200):
            self.payload, self.code = payload, code

    handler = Handler()
    assert ac.prepare_request_access(handler, "POST", "/api/task-app", {})
    assert handler.code == 401


@pytest.mark.parametrize("action", ["retry", "repair"])
def test_job_actions_check_inherited_baseline_mode(monkeypatch, action):
    from task_server import access_control as ac
    policy = ac.MainAccess({"permissions": ["ui.edit", "ui.execute"], "scope": {"ui_apps": "*"}}, [])
    monkeypatch.setattr(ac, "load_access_record", lambda kind, key: {"module": "A", "run_mode": "baseline"})
    with pytest.raises(ac.AccessDenied, match="ui.baseline"):
        policy.check("POST", f"/api/jobs/saved/{action}", body={})


def test_scoped_retry_cannot_inherit_shared_automatic_repair(policy, monkeypatch):
    from task_server import access_control as ac
    policy.permissions.add("ui.baseline")
    monkeypatch.setattr(ac, "load_access_record", lambda kind, key: {"module": "A", "run_mode": "test", "auto_optimize": True})
    with pytest.raises(ac.AccessDenied, match="共用基线"):
        policy.check("POST", "/api/jobs/saved/retry", body={"run_mode": "test", "autoOptimize": False})


@pytest.mark.parametrize("suffix", ["repair-latest", "repair-task-latest", "repair-latest-async", "repair-task-latest-async"])
def test_file_repairs_default_to_rerun_and_may_promote_baseline(suffix):
    from task_server import access_control as ac
    policy = ac.MainAccess({"permissions": ["ui.edit"], "scope": {"ui_apps": "*"}}, [])
    with pytest.raises(ac.AccessDenied, match="ui.execute"):
        policy.check("POST", "/api/file/" + suffix, body={})
    policy.permissions.add("ui.execute")
    with pytest.raises(ac.AccessDenied, match="ui.baseline"):
        policy.check("POST", "/api/file/" + suffix, body={})
    policy.check("POST", "/api/file/" + suffix, body={"createJob": False})


def test_generation_retry_reauthorizes_persisted_execution_flags(monkeypatch):
    from task_server import access_control as ac
    policy = ac.MainAccess({"permissions": ["ui.edit"], "scope": {"ui_apps": "*"}}, [])
    monkeypatch.setattr(ac, "load_access_record", lambda kind, key: {"request_data": {"createJob": True, "run_mode": "baseline"}})
    with pytest.raises(ac.AccessDenied, match="ui.execute"):
        policy.check("POST", "/api/ui/generate-jobs/saved/retry", body={"createJob": False})
    policy.permissions.add("ui.execute")
    with pytest.raises(ac.AccessDenied, match="ui.baseline"):
        policy.check("POST", "/api/ui/generate-jobs/saved/retry", body={"run_mode": "test"})


@pytest.mark.parametrize("file", ["../B/private.yaml", "/B/private.yaml", "..\\B\\private.yaml", "sub/../../B/private.yaml"])
def test_scoped_file_names_cannot_escape_module(policy, file):
    from task_server.access_control import AccessDenied
    with pytest.raises(AccessDenied):
        policy.check("GET", "/api/file", {"module": "A", "file": file})
    with pytest.raises(AccessDenied):
        policy.check("POST", "/api/file/op", body={"module": "A", "file": "a.yaml", "targetModule": "A", "targetFile": file, "op": "copy"})
    with pytest.raises(AccessDenied):
        policy.check("POST", "/api/files/op", body={"items": [{"module": "A", "file": file}], "targetModule": "A", "op": "copy"})


def test_scoped_symlink_cannot_escape_module(policy, monkeypatch, tmp_path):
    from task_server import config
    from task_server.access_control import AccessDenied
    monkeypatch.setattr(config, "TASK_DIR", str(tmp_path))
    (tmp_path / "A").mkdir()
    (tmp_path / "B").mkdir()
    (tmp_path / "B/private.yaml").write_text("private")
    (tmp_path / "A/link.yaml").symlink_to(tmp_path / "B/private.yaml")
    with pytest.raises(AccessDenied):
        policy.check("GET", "/api/file", {"module": "A", "file": "link.yaml"})


def test_run_mode_alias_uses_same_precedence_as_handler(policy):
    from task_server.access_control import AccessDenied
    with pytest.raises(AccessDenied, match="ui.baseline"):
        policy.check("POST", "/api/run-request", body={"module": "A", "file": "a.yaml", "run_mode": "", "runMode": "baseline"})


def test_smoke_rerun_checks_mod_alias_even_with_authorized_summary(policy, monkeypatch):
    from task_server import access_control as ac
    monkeypatch.setattr(ac, "load_access_record", lambda kind, key: {"module": "A", "app_package": "app.a"})
    with pytest.raises(ac.AccessDenied):
        policy.check("POST", "/api/cases/rerun-smoke", body={"case_set_id": "summary-A", "mod": "B"})


def test_business_response_is_not_rewritten_after_audit_storage_failure(policy, monkeypatch):
    from types import SimpleNamespace
    from task_server import identity
    from task_server.access_control import filter_access_response

    def audit_failure(*args):
        raise OSError("private database location")

    monkeypatch.setattr(identity, "audit_event", audit_failure)
    handler = SimpleNamespace(_main_access=policy, _access_path="/api/file", _access_method="POST")
    assert filter_access_response(handler, {"ok": True}, 200) == {"ok": True}
