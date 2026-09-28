"""Frozen execution input and trusted enqueue provenance, isolated local files."""
import hashlib
import json
import os
from pathlib import Path

import pytest

from task_server import config, access_control, router
from task_server.services import job_service, asset_lineage
from task_server.services.operation_attribution import actor_context, actor_from_trusted_username
from test_identity import identity_db
from test_operation_http import operation_server


@pytest.fixture
def execution_files(tmp_path, monkeypatch, identity_db):
    root = tmp_path / "tasks"
    (root / "A").mkdir(parents=True)
    jobs = tmp_path / "state" / "jobs.json"
    jobs.parent.mkdir()
    monkeypatch.setattr(config, "TASK_DIR", str(root))
    monkeypatch.setattr(job_service, "TASK_DIR", str(root))
    monkeypatch.setattr(router, "TASK_DIR", str(root))
    monkeypatch.setattr(job_service, "JOBS_FILE", str(jobs))
    monkeypatch.setattr(job_service, "TASK_META_FILE", str(tmp_path / "task-meta.json"))
    monkeypatch.setenv("TASK_ASSET_DB", str(tmp_path / "state" / "assets.sqlite3"))
    monkeypatch.setattr(access_control, "application_catalog", lambda: [{"package": "app.a", "modules": ["A"]}])
    actor = actor_from_trusted_username("admin")
    content = "android:\n  launch: app.a\ntasks:\n  - name: 原始任务\n    flow:\n      - aiTap: 首页\n"
    source = root / "A" / "sample.yaml"
    with actor_context(actor):
        asset_lineage.write_text(source, content)
    return root, jobs, source, content.encode(), actor


def test_pending_job_pins_bytes_and_ignores_created_by(execution_files):
    root, jobs, source, content, actor = execution_files
    with actor_context(actor, "request-a"):
        job = job_service.create_pending_job("A", "sample.yaml", created_by="forged")
    assert job["initiator"] == dict(actor)
    assert job["initiator_user_id"] == actor["user_id"]
    assert job["created_by"] == "admin"
    assert job["source_request_id"] == "request-a"
    assert job["input_artifact"]["content_sha256"] == hashlib.sha256(content).hexdigest()
    assert job["task_names"] == ["原始任务"]
    source.write_text("changed after enqueue")
    from task_server.services.execution_provenance import read_job_input
    assert read_job_input(job, task_dir=str(root), jobs_file=str(jobs)) == content
    public = json.dumps(job, ensure_ascii=False)
    assert str(root.parent) not in public
    assert content.decode() not in public


def test_create_job_filters_reserved_fields_and_parent_does_not_inherit(execution_files):
    _, _, _, _, actor = execution_files
    with actor_context(actor):
        job = job_service.create_job({"module": "A", "file": "sample.yaml", "parent_job_id": "foreign",
            "created_by": "forged", "initiator_user_id": "foreign-user", "initiator": {"kind": "user", "user_id": "foreign"},
            "input_artifact": {"asset_id": "forged"}, "input_snapshot": "legacy", "snapshot_path": "/secret",
            "yaml_content": "FORGED SCRIPT", "execution_scope": {"ui_apps": ["foreign"]}, "dispatch_sha256": "forged", "app_package": "foreign"})
    assert job["initiator"] == dict(actor)
    assert job["input_artifact"]["asset_id"] != "forged"
    assert job["execution_scope"] == {"ui_apps": ["app.a"]}
    assert "snapshot_path" not in job and "yaml_content" not in job
    assert "dispatch_sha256" not in job
    assert job.get("app_package") != "foreign"


def test_new_missing_input_never_falls_back_to_later_file(execution_files):
    root, jobs, source, content, actor = execution_files
    source.unlink()
    with actor_context(actor):
        job = job_service.create_pending_job("A", "sample.yaml")
    assert job["input_snapshot"] == "unavailable"
    assert "input_artifact" not in job
    source.write_bytes(content)
    from task_server.services.execution_provenance import read_job_input
    with pytest.raises(FileNotFoundError):
        read_job_input(job, task_dir=str(root), jobs_file=str(jobs))
    # A genuinely historical record has no input marker at all.
    assert read_job_input({"module": "A", "file": "sample.yaml"}, task_dir=str(root), jobs_file=str(jobs)) == content


def test_apk_job_records_actor_without_yaml_snapshot(execution_files):
    _, _, _, _, actor = execution_files
    with actor_context(actor):
        job = job_service.create_job({"module": "missing", "file": "app.apk", "job_type": "apk_install", "app_package": "app.a"})
    assert job["initiator"] == dict(actor)
    assert job["input_snapshot"] == "not_applicable"
    assert "input_artifact" not in job
    assert job["app_package"] == "app.a"


def test_frozen_scope_cannot_follow_module_remapping(execution_files):
    _, _, _, _, actor = execution_files
    with actor_context(actor):
        job = job_service.create_pending_job("A", "sample.yaml")
    def policy(app):
        return access_control.MainAccess({"scope": {"ui_apps": [app]}}, [{"package": "app.b", "modules": ["A"]}])
    assert policy("app.a").visible(job)
    assert not policy("app.b").visible(job)


def test_snapshot_is_exact_returned_bytes_and_symlinks_fail(execution_files, monkeypatch):
    root, jobs, source, content, actor = execution_files
    original = asset_lineage.snapshot
    def snapshot_then_edit(path):
        metadata, data = original(path)
        Path(path).write_text("changed between snapshot and private write")
        return metadata, data
    monkeypatch.setattr(asset_lineage, "snapshot", snapshot_then_edit)
    with actor_context(actor):
        job = job_service.create_pending_job("A", "sample.yaml")
    from task_server.services.execution_provenance import read_job_input
    assert read_job_input(job, task_dir=str(root), jobs_file=str(jobs)) == content
    link = root / "A" / "link.yaml"
    link.symlink_to(source)
    with actor_context(actor), pytest.raises((ValueError, PermissionError)):
        job_service.create_pending_job("A", "link.yaml")


def test_duplicate_job_id_and_persist_failure_do_not_leak_or_replace_inputs(execution_files, monkeypatch):
    root, jobs, source, content, actor = execution_files
    with actor_context(actor):
        first = job_service.create_job({"job_id": "fixed", "module": "A", "file": "sample.yaml"})
    source.write_text("new content")
    with actor_context(actor), pytest.raises((ValueError, FileExistsError)):
        job_service.create_job({"job_id": "fixed", "module": "A", "file": "sample.yaml"})
    from task_server.services.execution_provenance import read_job_input, snapshot_directory
    assert read_job_input(first, task_dir=str(root), jobs_file=str(jobs)) == content
    directory = snapshot_directory(str(jobs))
    before = set(directory.iterdir())
    def fail(*args):
        raise OSError("persistence unavailable")
    monkeypatch.setattr(job_service, "save_jobs", fail)
    with actor_context(actor), pytest.raises(OSError):
        job_service.create_pending_job("A", "sample.yaml")
    assert set(directory.iterdir()) == before
    assert directory.stat().st_mode & 0o777 == 0o700
    assert all(path.stat().st_mode & 0o777 == 0o600 for path in before)


def test_trusted_internal_origin_restores_human_but_manual_actor_wins(execution_files):
    root, jobs, _, _, actor = execution_files
    from task_server.services.execution_provenance import trusted_job_context
    with actor_context(actor, "request-parent"):
        parent = job_service.create_pending_job("A", "sample.yaml")
    with actor_context({"kind": "system", "username": "worker"}), trusted_job_context(parent):
        automatic = job_service.create_pending_job("A", "sample.yaml", parent_job_id=parent["job_id"])
    assert automatic["initiator"] == dict(actor)
    assert automatic["source_request_id"] == "request-parent"
    assert automatic["trigger_type"] == "automatic"
    foreign = {"initiator": {"kind": "user", "user_id": "foreign", "username": "foreign"}}
    with actor_context(actor), trusted_job_context(foreign):
        manual = job_service.create_pending_job("A", "sample.yaml", parent_job_id="foreign")
    assert manual["initiator"] == dict(actor)
    assert manual["trigger_type"] == "manual"


def test_enqueue_rechecks_current_human_acl(execution_files, monkeypatch):
    _, _, _, _, actor = execution_files
    from task_server import identity
    profile = identity.get_access_profile("admin")
    profile = {**profile, "is_superuser": False, "permissions": ["ui.execute"], "scope": {"ui_apps": ["app.b"]}}
    monkeypatch.setattr(identity, "get_access_profile", lambda unused: profile)
    with actor_context(actor), pytest.raises(access_control.AccessDenied):
        job_service.create_pending_job("A", "sample.yaml")


def test_runner_dispatch_uses_frozen_bytes_and_tracks_transformed_hash(execution_files, operation_server, monkeypatch):
    from task_server import router, auth
    from test_operation_http import request
    root, jobs, source, content, actor = execution_files
    server, _ = operation_server
    with actor_context(actor):
        job = job_service.create_pending_job("A", "sample.yaml")
    source.write_text("MUTABLE EDIT MUST NOT DISPATCH")
    monkeypatch.setattr(router, "assign_job", lambda *args: {**job, "started_at": "2026-09-28 10:00:00"})
    monkeypatch.setattr(router, "recover_timed_out_jobs", lambda: None)
    monkeypatch.setattr(router, "update_task_meta", lambda *args: None)
    monkeypatch.setattr(auth, "TOKEN", "trusted-runner")
    status, raw = request(server, "GET", "/api/runner/jobs/next?runner_id=runner-test", extra={"x-token": "trusted-runner"})
    assert status == 200
    dispatched = json.loads(raw)["job"]["yaml_content"]
    assert "原始任务" in dispatched and "MUTABLE EDIT" not in dispatched
    persisted = job_service.get_job(job["job_id"])
    assert persisted["dispatch_sha256"] == hashlib.sha256(dispatched.encode()).hexdigest()
    assert persisted["input_artifact"]["content_sha256"] == hashlib.sha256(content).hexdigest()
    assert persisted["dispatch_version"]
    assert str(root.parent) not in raw.decode()


def test_denied_runner_poll_does_not_recover_or_mutate_jobs(operation_server, monkeypatch):
    from task_server import router
    from test_operation_http import request
    server, _ = operation_server
    recovered = []
    monkeypatch.setattr(router, "recover_timed_out_jobs", lambda: recovered.append(True))
    assert request(server, "GET", "/api/runner/jobs/next", extra={"x-token": "invalid"})[0] == 401
    assert recovered == []


def test_missing_or_corrupt_new_snapshot_fails_without_private_path(execution_files):
    root, jobs, source, _, actor = execution_files
    from task_server.services.execution_provenance import read_job_input, snapshot_directory
    with actor_context(actor):
        job = job_service.create_pending_job("A", "sample.yaml")
    path = next(snapshot_directory(str(jobs)).iterdir())
    path.write_text("corrupt")
    with pytest.raises(ValueError, match="hash mismatch"):
        read_job_input(job, task_dir=str(root), jobs_file=str(jobs))
    path.unlink()
    with pytest.raises(FileNotFoundError) as failure:
        read_job_input(job, task_dir=str(root), jobs_file=str(jobs))
    assert str(root.parent) not in str(failure.value)


def test_runner_route_itself_authorizes_before_recovery(monkeypatch):
    recovered = []
    monkeypatch.setattr(router, "recover_timed_out_jobs", lambda: recovered.append(True))
    class Denied:
        response = None
        def _authorized_runner(self):
            return False
        def _json(self, payload, status=200):
            self.response = status, payload
    handler = Denied()
    router._get_runner_jobs_next(handler, {})
    assert handler.response[0] == 401
    assert recovered == []


def test_exception_after_job_persistence_keeps_referenced_snapshot(execution_files, monkeypatch):
    root, jobs, _, content, actor = execution_files
    original_save = job_service.save_jobs
    def persist_then_fail(rows):
        original_save(rows)
        raise OSError("post-persist failure")
    monkeypatch.setattr(job_service, "save_jobs", persist_then_fail)
    with actor_context(actor), pytest.raises(OSError):
        job_service.create_pending_job("A", "sample.yaml")
    persisted = json.loads(jobs.read_text())[0]
    from task_server.services.execution_provenance import read_job_input
    assert read_job_input(persisted, task_dir=str(root), jobs_file=str(jobs)) == content


def test_snapshot_directory_symlink_is_rejected(execution_files, tmp_path):
    _, jobs, _, _, actor = execution_files
    from task_server.services.execution_provenance import snapshot_directory
    other = tmp_path / "other"
    other.mkdir()
    snapshot_directory(str(jobs)).symlink_to(other, target_is_directory=True)
    with actor_context(actor), pytest.raises(ValueError, match="symlink"):
        job_service.create_pending_job("A", "sample.yaml")
    assert list(other.iterdir()) == []


def test_snapshot_scope_and_authorization_share_one_catalog_view(execution_files, monkeypatch):
    _, _, _, _, actor = execution_files
    from task_server import identity
    profile = {**identity.get_access_profile("admin"), "is_superuser": False,
               "permissions": ["ui.execute"], "scope": {"ui_apps": ["app.b"]}}
    monkeypatch.setattr(identity, "get_access_profile", lambda unused: profile)
    catalogs = iter([[{"package": "app.a", "modules": ["A"]}], [{"package": "app.b", "modules": ["A"]}]])
    monkeypatch.setattr(access_control, "application_catalog", lambda: next(catalogs))
    with actor_context(actor), pytest.raises(access_control.AccessDenied):
        job_service.create_pending_job("A", "sample.yaml")


def test_raw_record_cannot_forge_frozen_job_scope(execution_files):
    policy = access_control.MainAccess({"scope": {"ui_apps": ["app.a"]}}, [{"package": "app.b", "modules": ["B"]}])
    assert not policy.visible({"module": "B", "input_snapshot": "pinned", "provenance_version": 1,
                              "job_id": "fake", "execution_scope": {"ui_apps": ["app.a"]}})


def test_legacy_parent_fields_are_not_trusted_initiator(execution_files):
    _, _, _, _, actor = execution_files
    from task_server.services.execution_provenance import trusted_job_context
    legacy = {"initiator": dict(actor), "source_request_id": "forged-legacy"}
    with actor_context({"kind": "system"}), trusted_job_context(legacy):
        job = job_service.create_pending_job("A", "sample.yaml")
    assert job["initiator"]["kind"] == "unknown"
    assert job["source_request_id"] == ""


def test_http_job_scope_and_detail_stay_bound_after_asset_move(execution_files, operation_server, identity_db, monkeypatch):
    from task_server import auth
    from test_identity import create_member, activate
    from test_operation_http import request
    _, _, source, _, actor = execution_files
    server, _ = operation_server
    _, users = identity_db
    for name, package in (("owner-a", "app.a"), ("viewer-b", "app.b")):
        create_member(users, name, scope={"ui_apps": [package], "api_projects": [], "api_environments": []})
        activate(users, name)
    with actor_context(actor):
        job = job_service.create_pending_job("A", "sample.yaml")
    owner, other = auth.create_session_token("owner-a"), auth.create_session_token("viewer-b")
    moved = source.parent.parent / "B" / source.name
    moved.parent.mkdir()
    with actor_context(actor):
        asset_lineage.copy_or_move(source, moved, move=True)
    monkeypatch.setattr(access_control, "application_catalog", lambda: [{"package": "app.b", "modules": ["A", "B"]}])
    monkeypatch.setattr(router, "recover_timed_out_jobs", lambda: None)
    monkeypatch.setattr(router, "load_runners", lambda: {})
    monkeypatch.setattr(router, "list_generate_jobs", lambda *args: [])
    monkeypatch.setattr(router, "update_task_meta", lambda *args: None)
    status, raw = request(server, "GET", "/api/jobs", owner)
    assert status == 200 and [item["job_id"] for item in json.loads(raw)["jobs"]] == [job["job_id"]]
    assert str(source.parent.parent.parent) not in raw.decode()
    assert json.loads(request(server, "GET", "/api/jobs", other)[1])["jobs"] == []
    # Existing job-specific cancel response is the public detail-bearing path;
    # this repository has no generic GET /api/jobs/{id} detail handler.
    assert request(server, "POST", f"/api/jobs/{job['job_id']}/cancel", other, {})[0] == 403
    status, raw = request(server, "POST", f"/api/jobs/{job['job_id']}/cancel", owner, {})
    assert status == 200
    assert json.loads(raw)["job"]["input_artifact"] == job["input_artifact"]


def test_http_nonjob_resource_cannot_forge_frozen_scope(execution_files, operation_server, identity_db, monkeypatch):
    from task_server import auth
    from test_identity import create_member, activate
    from test_operation_http import request
    server, _ = operation_server
    _, users = identity_db
    create_member(users, "scoped", scope={"ui_apps": ["app.a"], "api_projects": [], "api_environments": []})
    activate(users, "scoped")
    monkeypatch.setattr(access_control, "application_catalog", lambda: [{"package": "app.b", "modules": ["B"]}])
    forged = {"module": "B", "job_id": "forged", "provenance_version": 1, "input_snapshot": "pinned", "execution_scope": {"ui_apps": ["app.a"]}}
    monkeypatch.setitem(router.GET_ROUTES, "/api/cases", lambda handler, qs: handler._json({"ok": True, "cases": [forged]}))
    status, raw = request(server, "GET", "/api/cases", auth.create_session_token("scoped"))
    assert status == 200 and json.loads(raw)["cases"] == []
