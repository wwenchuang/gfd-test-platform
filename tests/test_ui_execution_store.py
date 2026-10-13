"""Durable batch snapshots and dispatch intents."""

import concurrent.futures
import hashlib
import os
import sqlite3

import pytest


def prepared():
    source = "android: {}\ntasks:\n- name: 导航\n  flow:\n  - launch: app.a\n"
    revision = hashlib.sha256(source.encode()).hexdigest()
    return {
        "owner_user_id": "u-b", "owner_username": "b", "owner_display_name": "B",
        "app_id": "app.a", "request_hash": "hash-one", "idempotency_key": "start-one",
        "selection": {"cases": [{"case_id": "case-1", "revision": revision}]},
        "snapshots": [{"case_id": "case-1", "case_revision": revision,
                       "source_yaml": source, "source_hash": revision,
                       "metadata": {"module": "shared", "file": "same.yaml", "task_name": "导航"}}],
        "attempts": [{"case_id": "case-1", "case_revision": revision,
                      "target": {"runner_id": "r1", "device_id": "d1", "platform": "android"},
                      "target_resolution": {"target_package": "app.a", "platform_mappings": {"android": "app.a"}},
                      "compiled_case": {"yaml_content": source, "compiled_hash": revision,
                                        "step_map": {}, "required_actions": ["launch"]}}],
    }


def test_create_freezes_snapshots_and_intents_atomically(tmp_path):
    from task_server.services.ui_execution_store import UiExecutionStore

    path = tmp_path / "private" / "ui.sqlite3"
    store = UiExecutionStore(path)
    item = prepared()
    batch = store.create_batch(item)
    item["snapshots"][0]["source_yaml"] = "mutated"
    item["attempts"][0]["target"]["device_id"] = "other"
    loaded = UiExecutionStore(path).get_batch(batch["ui_batch_id"])
    assert loaded["owner_user_id"] == "u-b" and loaded["status"] == "queued"
    assert loaded["delivery_status"] == "not_requested"
    assert loaded["snapshots"][0]["source_yaml"].startswith("android:")
    assert loaded["case_attempts"][0]["device_id"] == "d1"
    assert loaded["case_attempts"][0]["job_id"] is None
    assert loaded["case_attempts"][0]["dispatch_key"].startswith("uib_")
    assert store.pending_dispatches()[0]["dispatch_key"] == loaded["case_attempts"][0]["dispatch_key"]
    assert os.stat(path.parent).st_mode & 0o777 == 0o700
    assert os.stat(path).st_mode & 0o777 == 0o600
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {"batches", "case_attempts", "snapshots", "device_leases", "delivery_outbox"} <= tables
        indexes = {row[1] for row in db.execute("PRAGMA index_list(batches)")}
        assert "batches_actor_app_time" in indexes


def test_concurrent_idempotency_survives_restart_and_conflicts(tmp_path):
    from task_server.services.ui_execution_store import BatchConflict, UiExecutionStore

    path = tmp_path / "ui.sqlite3"
    def start(_):
        return UiExecutionStore(path).create_batch(prepared())["ui_batch_id"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(start, range(16)))
    assert len(set(ids)) == 1
    assert len(UiExecutionStore(path).list_batches("u-b")["items"]) == 1
    changed = prepared()
    changed["request_hash"] = "different"
    with pytest.raises(BatchConflict):
        UiExecutionStore(path).create_batch(changed)
    changed["owner_user_id"] = "u-a"
    assert UiExecutionStore(path).create_batch(changed)["ui_batch_id"] != ids[0]


def test_link_job_is_idempotent_and_does_not_change_immutable_snapshot(tmp_path):
    from task_server.services.ui_execution_store import BatchConflict, UiExecutionStore

    store = UiExecutionStore(tmp_path / "ui.sqlite3")
    batch = store.create_batch(prepared())
    attempt = batch["case_attempts"][0]
    assert store.link_job(attempt["dispatch_key"], attempt["dispatch_key"])["job_id"] == attempt["dispatch_key"]
    assert store.link_job(attempt["dispatch_key"], attempt["dispatch_key"])["job_id"] == attempt["dispatch_key"]
    with pytest.raises(BatchConflict):
        store.link_job(attempt["dispatch_key"], "different")
    assert not store.pending_dispatches()
    assert store.get_batch(batch["ui_batch_id"])["snapshots"] == batch["snapshots"]


def test_list_uses_owner_and_app_index_with_stable_cursor(tmp_path):
    from task_server.services.ui_execution_store import UiExecutionStore

    store = UiExecutionStore(tmp_path / "ui.sqlite3")
    for index in range(3):
        item = prepared()
        item["idempotency_key"] = f"key-{index}"
        item["request_hash"] = f"hash-{index}"
        store.create_batch(item)
    page = store.list_batches("u-b", app_id="app.a", limit=2)
    assert len(page["items"]) == 2 and page["next_cursor"]
    next_page = store.list_batches("u-b", app_id="app.a", cursor=page["next_cursor"], limit=2)
    assert len(next_page["items"]) == 1
    assert not ({row["ui_batch_id"] for row in page["items"]} & {row["ui_batch_id"] for row in next_page["items"]})
    assert store.list_batches("u-a")["items"] == []


def test_later_attempt_reuses_frozen_content_and_gets_safe_dispatch_key(tmp_path):
    from task_server.services.ui_execution_store import BatchConflict, UiExecutionStore

    store = UiExecutionStore(tmp_path / "ui.sqlite3")
    batch = store.create_batch(prepared())
    original = batch["case_attempts"][0]
    target = {"runner_id": "r1", "device_id": "d1", "platform": "android"}
    second = store.append_case_attempt(batch["ui_batch_id"], original["case_id"], original["case_revision"], target, original["compiled_case"])
    third = store.append_case_attempt(batch["ui_batch_id"], original["case_id"], original["case_revision"], target, original["compiled_case"])
    assert second["attempt_no"] == 2 and third["attempt_no"] == 3
    assert second["dispatch_key"] == original["dispatch_key"].rsplit("_", 1)[0] + "_2"
    assert third["dispatch_key"] == original["dispatch_key"].rsplit("_", 1)[0] + "_3"
    with pytest.raises(BatchConflict):
        store.append_case_attempt(batch["ui_batch_id"], original["case_id"], original["case_revision"], target, {"yaml_content": "edited"})


def test_retry_uses_case_id_when_two_cases_have_identical_source(tmp_path):
    from task_server.services.ui_execution_store import BatchConflict, UiExecutionStore

    store = UiExecutionStore(tmp_path / "ui.sqlite3")
    item = prepared()
    other_snapshot = {**item["snapshots"][0], "case_id": "case-2", "metadata": {"task_name": "相同内容的另一用例"}}
    other_attempt = {**item["attempts"][0], "case_id": "case-2"}
    item["snapshots"].append(other_snapshot)
    item["attempts"].append(other_attempt)
    batch = store.create_batch(item)
    by_case = {row["case_id"]: row for row in batch["case_attempts"]}
    assert by_case["case-1"]["case_revision"] == by_case["case-2"]["case_revision"]
    target = {"runner_id": "r1", "device_id": "d1", "platform": "android"}
    second = store.append_case_attempt(batch["ui_batch_id"], "case-2", by_case["case-2"]["case_revision"], target,
                                       by_case["case-2"]["compiled_case"])
    first = store.append_case_attempt(batch["ui_batch_id"], "case-1", by_case["case-1"]["case_revision"], target,
                                      by_case["case-1"]["compiled_case"])
    assert second["case_id"] == "case-2" and second["snapshot_id"] == by_case["case-2"]["snapshot_id"]
    assert first["case_id"] == "case-1" and first["snapshot_id"] == by_case["case-1"]["snapshot_id"]
    assert second["dispatch_key"].rsplit("_", 1)[0] == by_case["case-2"]["dispatch_key"].rsplit("_", 1)[0]
    assert first["dispatch_key"].rsplit("_", 1)[0] == by_case["case-1"]["dispatch_key"].rsplit("_", 1)[0]
    with pytest.raises(BatchConflict):
        store.append_case_attempt(batch["ui_batch_id"], "case-3", by_case["case-1"]["case_revision"], target,
                                  by_case["case-1"]["compiled_case"])


def test_duplicate_attempt_rolls_back_batch_and_all_snapshots(tmp_path):
    from task_server.services.ui_execution_store import UiExecutionStore

    store = UiExecutionStore(tmp_path / "ui.sqlite3")
    item = prepared()
    item["attempts"].append(item["attempts"][0])
    with pytest.raises(sqlite3.IntegrityError):
        store.create_batch(item)
    with sqlite3.connect(store.path) as db:
        assert db.execute("SELECT count(*) FROM batches").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM case_attempts").fetchone()[0] == 0
