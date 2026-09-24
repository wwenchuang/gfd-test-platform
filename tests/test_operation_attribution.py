"""Attribution contracts use only isolated identity and operation databases."""

import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from task_server import auth, config, identity
from task_server.services import operation_attribution as oa


@pytest.fixture
def identities(tmp_path, monkeypatch):
    monkeypatch.setenv("TASK_AUTH_DB", str(tmp_path / "identity" / "identity.sqlite3"))
    monkeypatch.setenv("TASK_OPERATION_DB", str(tmp_path / "operation" / "events.sqlite3"))
    monkeypatch.setattr(config, "TASK_ADMIN_PASSWORD", "test-password-12345")
    monkeypatch.setattr(config, "TASK_ADMIN_PASSWORD_HASH", "")
    monkeypatch.setattr(config, "TASK_ADMIN_USER", "admin")
    store = identity.get_identity_store()
    for name in ("alice", "bob"):
        store.create_user("admin", {"username": name, "display_name": name.title(), "password": "test-password-12345", "role_ids": ["tester"]})
        store.change_password(name, "test-password-12345", "changed-password-12345")
    return store


def test_verified_actor_ignores_spoof_and_revoked_session(identities):
    token = auth.create_session_token("alice")
    actor = oa.resolve_request_actor({"Authorization": f"Bearer {token}"}, body={"user_id": "forged", "created_by": "admin"})
    assert actor["kind"] == "user"
    assert actor["user_id"] == identity.get_access_profile("alice")["user_id"]
    assert actor["username"] == "alice"
    with pytest.raises(TypeError):
        actor["username"] = "admin"
    auth.logout(token)
    assert oa.resolve_request_actor({"Authorization": f"Bearer {token}"})["kind"] == "anonymous"
    token = auth.create_session_token("alice")
    identities.update_user("admin", "alice", {"status": "disabled"})
    assert oa.resolve_request_actor({"Authorization": f"Bearer {token}"})["kind"] == "anonymous"
    assert oa.actor_from_trusted_username("missing")["kind"] == "unknown"


def test_machine_actor_is_distinct_and_context_resets(identities, monkeypatch):
    monkeypatch.setattr(auth, "TOKEN", "runner-secret")
    monkeypatch.setattr(auth, "SONIC_CALLBACK_TOKEN", "sonic-secret")
    assert oa.resolve_request_actor({"x-token": "runner-secret"})["kind"] == "runner"
    assert oa.resolve_request_actor({"x-token": "sonic-secret"})["kind"] == "sonic"
    assert oa.resolve_request_actor({"x-token": "wrong"})["kind"] == "anonymous"
    actor = oa.actor_from_trusted_username("bob")
    with oa.actor_context(actor, "request-1"):
        assert oa.current_actor()["user_id"] == actor["user_id"]
        assert oa.current_request_id() == "request-1"
    assert oa.current_actor()["kind"] == "unknown"
    assert oa.current_request_id() is None


def test_concurrent_append_pagination_and_restart(identities, tmp_path):
    store = oa.OperationStore()
    alice = oa.actor_from_trusted_username("alice")
    profile = identity.get_access_profile("alice")
    def append(i):
        return store.append({"event_id": f"event-{i}", "actor": alice, "action": "case.save", "resource_type": "case", "resource_id": str(i), "result": "success"})
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert all(result["stored"] for result in pool.map(append, range(40)))
    assert store.append({"event_id": "event-0", "actor": alice, "action": "case.save", "resource_type": "case", "resource_id": "0", "result": "success"})["duplicate"]
    reopened = oa.OperationStore(store.path)
    page1 = reopened.list_events(profile, limit=15)
    page2 = reopened.list_events(profile, limit=15, cursor=page1["next_cursor"])
    assert page1["total"] == page2["total"] == 40
    assert len(page1["events"]) == len(page2["events"]) == 15
    assert not ({x["event_id"] for x in page1["events"]} & {x["event_id"] for x in page2["events"]})
    assert reopened.list_events(profile, filters={"action": "case.save", "resource_type": "case", "result": "success"})["total"] == 40


def test_foreign_reads_denied_even_with_actor_filter(identities):
    store = oa.OperationStore()
    store.append({"event_id": "bob-event", "actor": oa.actor_from_trusted_username("bob"), "action": "case.save", "resource_type": "case", "result": "success"})
    alice = identity.get_access_profile("alice")
    bob = identity.get_access_profile("bob")
    assert store.list_events(alice, filters={"actor_id": bob["user_id"]})["total"] == 0
    assert store.list_events(alice, all_actors=True)["total"] == 0
    assert store.list_events(identity.get_access_profile("admin"), all_actors=True)["total"] == 1
    identities.update_user("admin", "bob", {"status": "disabled"})
    assert store.list_events(identity.get_access_profile("admin"), all_actors=True)["events"][0]["actor"]["user_id"] == bob["user_id"]


def test_allowlist_redacts_and_bounds_payload(identities):
    store = oa.OperationStore()
    secret = "ultra-secret-token"
    result = store.append({"event_id": "redacted", "actor": oa.actor_from_trusted_username("alice"), "action": "case.save", "resource_type": "case", "result": "partial", "password": secret, "body": {"token": secret}, "summary": {"changed_fields": ["name", "password"], "error": secret}, "item_outcomes": [{"resource_id": "1", "result": "success", "error": secret}] * 100})
    assert result["stored"]
    with sqlite3.connect(store.path) as db:
        dump = "\n".join(db.iterdump())
    assert secret not in dump
    event = store.list_events(identity.get_access_profile("alice"))["events"][0]
    assert event["truncated"] is True
    assert len(event["item_outcomes"]) <= 25
    assert "password" not in event["summary"].get("changed_fields", [])


def test_async_executor_retains_separate_human_initiator_and_safe_sources(identities):
    store = oa.OperationStore()
    alice = oa.actor_from_trusted_username("alice")
    event = {"event_id": "async-1", "actor": {"kind": "system", "username": "scheduler"},
             "initiator_user_id": alice["user_id"], "action": "job.execute", "resource_type": "job",
             "resource_id": "job-1", "result": "success", "source_job_id": "job-0",
             "source_run_id": "run-0", "source_recording_id": "recording-0",
             "version_id": "version-1", "content_sha256": "a" * 64,
             "untrusted": "sensitive-body"}
    assert store.append(event)["stored"]
    result = store.list_events(identity.get_access_profile("admin"), all_actors=True)["events"][0]
    assert result["actor"]["kind"] == "system"
    assert result["initiator_user_id"] == alice["user_id"]
    assert result["source_job_id"] == "job-0"
    assert result["content_sha256"] == "a" * 64
    assert "sensitive-body" not in json.dumps(result)


def test_spool_replay_dedupes_and_explicit_total_failure(identities, monkeypatch):
    store = oa.OperationStore()
    actor = oa.actor_from_trusted_username("alice")
    original = store._insert
    monkeypatch.setattr(store, "_insert", lambda event: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    result = store.append({"event_id": "spooled", "actor": actor, "action": "case.save", "resource_type": "case", "result": "accepted"})
    assert result["stored"] and result["storage"] == "spool"
    monkeypatch.setattr(store, "_insert", original)
    assert store.replay_spool()["replayed"] == 1
    assert store.replay_spool()["replayed"] == 0
    assert store.list_events(identity.get_access_profile("alice"))["total"] == 1
    monkeypatch.setattr(store, "_insert", lambda event: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    monkeypatch.setattr(store, "_spool", lambda event: (_ for _ in ()).throw(OSError("full")))
    failure = store.append({"event_id": "failed", "actor": actor, "action": "case.save", "resource_type": "case", "result": "failed"})
    assert failure == {"stored": False, "storage": "failed", "duplicate": False}


def test_startup_sqlite_outage_still_spools_and_recovers(identities, monkeypatch):
    original = sqlite3.connect
    db_path = str(oa.default_db_path())
    def unavailable(path, *args, **kwargs):
        if str(path) == db_path:
            raise sqlite3.OperationalError("unavailable")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(sqlite3, "connect", unavailable)
    store = oa.OperationStore()
    assert store.append({"event_id": "outage", "actor": oa.actor_from_trusted_username("alice"),
                         "action": "case.save", "resource_type": "case", "result": "accepted"})["storage"] == "spool"
    monkeypatch.setattr(sqlite3, "connect", original)
    assert store.replay_spool()["replayed"] == 1
    assert store.list_events(identity.get_access_profile("alice"))["total"] == 1


def test_spooled_truncated_batch_stays_truncated_after_replay(identities, monkeypatch):
    store = oa.OperationStore()
    original_insert = store._insert
    monkeypatch.setattr(store, "_insert", lambda event: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
    event = {"event_id": "large-batch", "actor": oa.actor_from_trusted_username("alice"),
             "action": "case.move", "resource_type": "case", "result": "partial",
             "item_outcomes": [{"resource_id": str(index), "result": "success"} for index in range(30)]}
    assert store.append(event)["storage"] == "spool"
    monkeypatch.setattr(store, "_insert", original_insert)
    assert store.replay_spool()["replayed"] == 1
    saved = store.list_events(identity.get_access_profile("alice"))["events"][0]
    assert len(saved["item_outcomes"]) == 25
    assert saved["truncated"] is True


def test_stale_profile_cannot_read_after_password_reset(identities):
    store = oa.OperationStore()
    old_profile = identity.get_access_profile("alice")
    store.append({"event_id": "before-reset", "actor": oa.actor_from_trusted_username("alice"),
                  "action": "case.save", "resource_type": "case", "result": "success"})
    identities.reset_password("admin", "alice", "another-password-12345")
    assert identity.get_access_profile("alice")["must_change_password"] is True
    with pytest.raises(PermissionError):
        store.list_events(old_profile)
