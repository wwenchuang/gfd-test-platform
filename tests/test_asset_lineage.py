"""Stable asset identities and exact-content lineage; isolated filesystem only."""
import hashlib
import threading

import pytest

from task_server import config, storage
from task_server.services.operation_attribution import actor_context

A = {"kind": "user", "user_id": "user-a", "username": "alice", "display_name": "Alice"}
B = {"kind": "user", "user_id": "user-b", "username": "bob", "display_name": "Bob"}


@pytest.fixture
def assets(tmp_path, monkeypatch):
    from task_server.services import asset_lineage, yaml_service, job_service
    root = tmp_path / "tasks"
    root.mkdir()
    monkeypatch.setattr(config, "TASK_DIR", str(root))
    monkeypatch.setattr(yaml_service, "TASK_DIR", str(root))
    monkeypatch.setattr(job_service, "TASK_DIR", str(root))
    monkeypatch.setattr(yaml_service, "VERSION_DIR", str(tmp_path / "versions"))
    monkeypatch.setenv("TASK_ASSET_DB", str(tmp_path / "state" / "assets.sqlite3"))
    return asset_lineage, root


def test_create_edit_external_and_snapshot(assets):
    lineage, root = assets
    p = root / "module" / "a.yaml"
    with actor_context(A):
        storage.write_text_file(p, "first")
    first, content = lineage.snapshot(p)
    assert content == b"first"
    assert first["creator"]["user_id"] == "user-a"
    with actor_context(B):
        storage.write_text_file(p, "second")
        second, content = lineage.snapshot(p)
    assert first["asset_id"] == second["asset_id"]
    assert second["creator"] == first["creator"]
    assert second["version"]["author"]["user_id"] == "user-b"
    assert second["version"]["content_sha256"] == hashlib.sha256(content).hexdigest()
    p.write_text("external")
    with actor_context(B):
        external, content = lineage.snapshot(p)
    assert external["creator"] == first["creator"]
    assert external["version"]["author"]["kind"] == "unknown"
    assert external["version"]["source"] == "external"
    assert external["version"]["version_id"] != second["version"]["version_id"]


def test_copy_move_overwrite_delete_and_recreate(assets):
    lineage, root = assets
    from task_server.services.job_service import copy_or_move_task_file
    p, q, r = (root / "M" / name for name in ("a.yaml", "b.yaml", "c.yaml"))
    with actor_context(A):
        storage.write_text_file(p, "a")
    original = lineage.snapshot(p)[0]
    with actor_context(B):
        copy_or_move_task_file("M", "a.yaml", "M", "b.yaml")
        copied = lineage.snapshot(q)[0]
        assert copied["asset_id"] != original["asset_id"]
        assert copied["copied_from"]["asset_id"] == original["asset_id"]
        assert copied["creator"]["user_id"] == "user-b"
        storage.write_text_file(r, "target")
        target = lineage.snapshot(r)[0]
        copy_or_move_task_file("M", "b.yaml", "M", "c.yaml", overwrite=True)
        assert lineage.snapshot(r)[0]["asset_id"] == target["asset_id"]
        copy_or_move_task_file("M", "b.yaml", "M", "c.yaml", move=True, overwrite=True)
        moved = lineage.snapshot(r)[0]
        assert moved["asset_id"] == copied["asset_id"]
        assert moved["replaced_asset_id"] == target["asset_id"]
        assert not q.exists()
        lineage.delete_file(r)
        storage.write_text_file(r, "again")
    assert lineage.snapshot(r)[0]["asset_id"] != moved["asset_id"]


def test_historical_creator_and_outside_writes(assets):
    lineage, root = assets
    p = root / "legacy.yaml"
    p.write_text("legacy")
    with actor_context(B):
        storage.write_text_file(p, "edited")
    row = lineage.snapshot(p)[0]
    assert row["creator"]["kind"] == "unknown"
    assert row["version"]["author"]["user_id"] == "user-b"
    storage.write_text_file(root.parent / "before.yaml", "snapshot")
    assert not lineage.is_asset_path(root.parent / "before.yaml")


def test_existing_nested_lock_completes_and_concurrent_versions_are_true(assets):
    lineage, root = assets
    p = root / "M" / "a.yaml"
    errors = []
    def save(value):
        try:
            with storage.file_mutation_lock(p):
                storage.write_text_file(p, value)
                row, contents = lineage.snapshot(p)
                assert row["version"]["content_sha256"] == hashlib.sha256(contents).hexdigest()
        except Exception as exc:
            errors.append(exc)
    threads = [threading.Thread(target=save, args=(str(i),), daemon=True) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert not any(t.is_alive() for t in threads), "recursive flock deadlocked"
    assert not errors


def test_metadata_failure_before_and_after_file_replace(assets, monkeypatch):
    lineage, root = assets
    p = root / "a.yaml"
    with actor_context(A):
        storage.write_text_file(p, "old")
    old = lineage.snapshot(p)[0]
    original_finish = lineage._finish
    def fail(*args, **kwargs):
        raise OSError("metadata unavailable")
    monkeypatch.setattr(lineage, "_finish", fail)
    with actor_context(B), pytest.raises(OSError):
        storage.write_text_file(p, "new")
    monkeypatch.setattr(lineage, "_finish", original_finish)
    new, content = lineage.snapshot(p)
    assert content == b"new"
    assert new["asset_id"] == old["asset_id"]
    assert new["version"]["author"]["user_id"] == "user-b"
    assert new["version"]["content_sha256"] == hashlib.sha256(content).hexdigest()


class Handler:
    def __init__(self, body=None):
        self.body = body or {}
        self.result = None
        self.status = None
    def _body(self):
        return self.body
    def _json(self, data, status=200):
        self.result, self.status = data, status


@pytest.fixture
def routes(assets, monkeypatch):
    from task_server import router
    monkeypatch.setattr(router, "TASK_DIR", str(assets[1]))
    monkeypatch.setattr(router, "_require_delete_auth", lambda _h: False)
    return router


def bounded_call(fn, *args):
    from contextvars import copy_context
    errors = []
    def invoke():
        try:
            fn(*args)
        except Exception as exc:
            errors.append(exc)
    thread = threading.Thread(target=copy_context().run, args=(invoke,), daemon=True)
    thread.start()
    thread.join(5)
    assert not thread.is_alive(), "actual route transaction deadlocked"
    if errors:
        raise errors[0]


def test_actual_save_restore_author_and_backup_truth(assets, routes):
    lineage, root = assets
    p = root / "M" / "a.yaml"
    with actor_context(A):
        bounded_call(routes._post_file_save, Handler({"module": "M", "file": "a", "content": "first"}), {})
    with actor_context(B):
        bounded_call(routes._post_file_save, Handler({"module": "M", "file": "a", "content": "second"}), {})
    history = routes.list_file_versions("M", "a.yaml")
    assert history[0]["attribution"]["author"]["user_id"] == "user-a"
    assert history[0]["attribution"]["content_sha256"] == hashlib.sha256(b"first").hexdigest()
    before = lineage.snapshot(p)[0]
    h = Handler({"module": "M", "file": "a.yaml", "version": history[0]["id"]})
    with actor_context(B):
        bounded_call(routes._post_file_restore, h, {})
    restored, contents = lineage.snapshot(p)
    assert contents == b"first"
    assert restored["creator"]["user_id"] == "user-a"
    assert restored["version"]["author"]["user_id"] == "user-b"
    assert restored["version"]["restored_from_version_id"] == history[0]["id"]
    assert restored["version"]["version_id"] != before["version"]["version_id"]


def test_module_delete_rejects_empty_root_traversal_and_recreate(assets, routes):
    lineage, root = assets
    p = root / "M" / "a.yaml"
    storage.write_text_file(p, "a")
    original = lineage.snapshot(p)[0]
    for module in ("", ".", "M/..", "../tasks"):
        h = Handler()
        routes._delete_module(h, {"module": module})
        assert h.status == 400
        assert p.exists()
    routes._delete_module(Handler(), {"module": "M"})
    assert not p.exists() and root.exists()
    storage.write_text_file(p, "a")
    assert lineage.snapshot(p)[0]["asset_id"] != original["asset_id"]


def test_scoped_metadata_and_mine_filters(assets, routes):
    from task_server.access_control import MainAccess, AccessDenied
    lineage, root = assets
    with actor_context(A):
        storage.write_text_file(root / "M" / "a.yaml", "a")
    with actor_context(B):
        storage.write_text_file(root / "M" / "b.yaml", "b")
        storage.write_text_file(root / "M" / "a.yaml", "edit")
        h = Handler()
        routes._get_file_attribution(h, {"module": "M", "creator": "mine"})
        assert [x["file"] for x in h.result["assets"]] == ["b.yaml"]
        routes._get_file_attribution(h, {"module": "M", "editor": "mine"})
        assert len(h.result["assets"]) == 2
    policy = MainAccess({"permissions": ["ui.view"], "scope": {"ui_apps": ["app"]}}, [{"package": "app", "modules": ["M"]}])
    policy.check("GET", "/api/file/attribution", {"module": "M"})
    with pytest.raises(AccessDenied):
        policy.check("GET", "/api/file/attribution", {"module": "Other"})
    for query in ({"module": "M", "file": "../outside.yaml"}, {"module": "M", "file": "/tmp/out.yaml"}):
        routes._get_file_attribution(h, query)
        assert h.status == 400


def test_recording_source_requires_verified_owner_and_matching_generated_yaml(assets, routes, monkeypatch):
    from task_server.services import device_recording_service
    lineage, root = assets
    session = {"id": "recording-1", "created_by": "alice", "app_package": "app", "module_name": "M", "generated_result": {"yaml": "recorded"}}
    monkeypatch.setattr(device_recording_service, "get_recording_session", lambda _id: session)
    monkeypatch.setattr(routes, "_authenticated_user", lambda _h: "alice")
    body = {"module": "M", "file": "record.yaml", "content": "recorded", "sourceRecordingID": "recording-1"}
    with actor_context(A):
        routes._post_file_save(Handler(body), {})
    row = lineage.snapshot(root / "M" / "record.yaml")[0]
    assert row["source_recording_id"] == "recording-1"
    monkeypatch.setattr(routes, "_authenticated_user", lambda _h: "bob")
    h = Handler({**body, "file": "forged.yaml"})
    with actor_context(B):
        routes._post_file_save(h, {})
    assert h.status == 403
    assert not (root / "M" / "forged.yaml").exists()


def test_migration_dry_run_backup_and_idempotence(assets):
    lineage, root = assets
    (root / "legacy.yaml").write_text("old")
    database = lineage.db_path()
    report = lineage.migrate()
    assert report["unknown"] == 1 and report["conflicts"] == 0
    assert not database.exists(), "dry run must not create metadata"
    applied = lineage.migrate(apply=True)
    assert applied["applied"] == 1
    assert __import__('pathlib').Path(applied["backup"]).exists()
    before = lineage.snapshot(root / "legacy.yaml")[0]
    again = lineage.migrate(apply=True)
    assert again["applied"] == 0
    assert lineage.snapshot(root / "legacy.yaml")[0] == before


def test_journal_failure_before_write_never_changes_contents_or_author(assets, monkeypatch):
    lineage, root = assets
    p = root / "a.yaml"
    with actor_context(A):
        storage.write_text_file(p, "old")
    before = lineage.snapshot(p)[0]
    def fail(*args, **kwargs):
        raise OSError("disk failure")
    monkeypatch.setattr(storage, "_write_text_file", fail)
    with actor_context(B), pytest.raises(OSError):
        storage.write_text_file(p, "new")
    assert lineage.snapshot(p) == (before, b"old")


def test_concurrent_copy_save_and_move_do_not_invert_locks(assets):
    lineage, root = assets
    from task_server.services.job_service import copy_or_move_task_file
    p, q = root / "M" / "a.yaml", root / "M" / "b.yaml"
    storage.write_text_file(p, "initial")
    errors = []
    def work(copying):
        try:
            for i in range(10):
                if copying:
                    copy_or_move_task_file("M", "a.yaml", "M", "b.yaml", overwrite=True)
                    copy_or_move_task_file("M", "b.yaml", "M", "c.yaml", move=True, overwrite=True)
                else:
                    with storage.file_mutation_lock(p):
                        storage.write_text_file(p, str(i))
        except Exception as e:
            errors.append(e)
    ts = [threading.Thread(target=work, args=(value,), daemon=True) for value in (True, False)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(10)
    assert not any(t.is_alive() for t in ts)
    assert not errors
    for path in (p, root / "M" / "c.yaml"):
        row, data = lineage.snapshot(path)
        assert row["version"]["content_sha256"] == hashlib.sha256(data).hexdigest()


def test_symlink_cannot_read_cross_scope_metadata(assets):
    lineage, root = assets
    p = root / "hidden" / "a.yaml"
    storage.write_text_file(p, "secret")
    (root / "allowed").symlink_to(p.parent, target_is_directory=True)
    with pytest.raises(ValueError):
        lineage.snapshot(root / "allowed" / "a.yaml")


def test_restore_identical_bytes_still_creates_revision(assets):
    lineage, root = assets
    p = root / "a.yaml"
    storage.write_text_file(p, "same")
    before = lineage.snapshot(p)[0]
    with actor_context(B), lineage.source_context(restored_from_version_id="backup-1"):
        storage.write_text_file(p, "same")
    after = lineage.snapshot(p)[0]
    assert after["asset_id"] == before["asset_id"]
    assert after["version"]["version_id"] != before["version"]["version_id"]
    assert after["version"]["author"]["user_id"] == "user-b"


def test_history_tampered_backup_does_not_keep_author(assets, routes):
    from pathlib import Path
    from task_server.services import yaml_service, case_service
    _, root = assets
    p = root / "M" / "a.yaml"
    with actor_context(A):
        storage.write_text_file(p, "original")
    backup = yaml_service.save_file_version("M", "a.yaml")
    path = Path(yaml_service.version_dir_for("M", "a.yaml")) / backup["yaml"]
    path.write_text("externally changed")
    meta, _ = case_service.read_file_version("M", "a.yaml", backup["id"])
    assert meta["attribution"]["author"]["kind"] == "unknown"
    assert meta["attribution"]["content_sha256"] == hashlib.sha256(b"externally changed").hexdigest()


def test_failed_same_content_restore_cannot_publish_author(assets, monkeypatch):
    lineage, root = assets
    p = root / "a.yaml"
    with actor_context(A):
        storage.write_text_file(p, "same")
    before = lineage.snapshot(p)[0]
    def fail(*args, **kwargs):
        raise OSError("write never happened")
    monkeypatch.setattr(storage, "_write_text_file", fail)
    with actor_context(B), lineage.source_context(restored_from_version_id="backup-1"), pytest.raises(OSError):
        storage.write_text_file(p, "same")
    after = lineage.snapshot(p)[0]
    assert after == before, "matching pre-existing bytes cannot prove the intended replacement happened"


def test_save_response_keeps_the_version_written_by_this_request(assets, routes, monkeypatch):
    lineage, root = assets
    p = root / "M" / "a.yaml"
    original_snapshot = lineage.snapshot
    threads = []
    def concurrent_snapshot(path):
        if not threads:
            def competing_save():
                with actor_context(A):
                    storage.write_text_file(p, "later")
            t = threading.Thread(target=competing_save, daemon=True)
            threads.append(t)
            t.start()
            t.join(0.1)
        return original_snapshot(path)
    monkeypatch.setattr(lineage, "snapshot", concurrent_snapshot)
    h = Handler({"module": "M", "file": "a.yaml", "content": "mine"})
    with actor_context(B):
        routes._post_file_save(h, {})
    for t in threads:
        t.join(3)
    assert h.result["attribution"]["version"]["author"]["user_id"] == "user-b"
    assert h.result["attribution"]["version"]["content_sha256"] == hashlib.sha256(b"mine").hexdigest()


def test_pending_intent_recovers_after_process_exit(assets):
    import os
    import subprocess
    import sys
    lineage, root = assets
    p = root / "a.yaml"
    with actor_context(A):
        storage.write_text_file(p, "before crash")
    before = lineage.snapshot(p)[0]
    script = '''
import os, sys
from task_server import storage
from task_server.services import asset_lineage
from task_server.services.operation_attribution import actor_context
asset_lineage._finish = lambda *args: os._exit(17)
with actor_context({"kind":"user","user_id":"user-b","username":"bob","display_name":"Bob"}):
    storage.write_text_file(sys.argv[1], "after crash")
'''
    result = subprocess.run([sys.executable, "-c", script, str(p)],
                            env={**os.environ, "TASK_DIR": str(root)}, timeout=10, capture_output=True)
    assert result.returncode == 17, result.stderr.decode()
    recovered, contents = lineage.snapshot(p)
    assert contents == b"after crash"
    assert recovered["asset_id"] == before["asset_id"]
    assert recovered["creator"] == before["creator"]
    assert recovered["version"]["author"]["user_id"] == "user-b"
    assert "after crash" not in lineage.db_path().read_bytes().decode('latin1')


def test_copy_over_identical_bytes_records_copy_revision(assets):
    lineage, root = assets
    p, q = root / "a.yaml", root / "b.yaml"
    with actor_context(A):
        storage.write_text_file(p, "same")
        storage.write_text_file(q, "same")
    original = lineage.snapshot(q)[0]
    with actor_context(B):
        lineage.copy_or_move(p, q, overwrite=True)
    copied = lineage.snapshot(q)[0]
    assert copied["asset_id"] == original["asset_id"]
    assert copied["version"]["version_id"] != original["version"]["version_id"]
    assert copied["version"]["author"]["user_id"] == "user-b"
    assert copied["copied_from"]["asset_id"] == lineage.snapshot(p)[0]["asset_id"]
