"""Future-only job audit contracts; never exercise production files or devices."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from task_server.services import job_service as jobs
from task_server.services import operation_attribution as oa
from test_operation_attribution import identities
from test_identity import identity_db, create_member, activate, ALL_SCOPE
from test_operation_http import operation_server, request


@pytest.fixture
def isolated(identities, tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "JOBS_FILE", str(tmp_path / "jobs.json"))
    monkeypatch.setattr(jobs, "TASK_DIR", str(tmp_path / "tasks"))
    monkeypatch.setattr(jobs, "_update_task_meta", lambda *args: None)
    return oa.OperationStore()


def rows(store):
    with store._connect() as db:
        return [json.loads(row[0]) for row in db.execute("SELECT data FROM events ORDER BY seq")]


@pytest.mark.parametrize("pending", [False, True])
def test_creation_records_context_not_job_metadata(isolated, pending):
    actor = oa.actor_from_trusted_username("alice")
    with oa.actor_context(actor, "request-new"):
        if pending:
            job = jobs.create_pending_job("module", "case.yaml", created_by="bob")
        else:
            job = jobs.create_job({"created_by": "bob", "initiator_user_id": "forged",
                                   "provenance_version": 1, "execution_scope": {"ui_app": "other"}})
    event, = rows(isolated)
    assert event["actor"]["user_id"] == actor["user_id"]
    assert event["request_id"] == "request-new"
    assert event["resource_id"] == job["job_id"]
    assert event["action"] == "job.created"
    assert event["result"] == "success"
    assert event["scope_refs"] == {}
    assert "forged" not in json.dumps(event)


def test_history_read_does_not_backfill_or_mutate(isolated):
    old = {"job_id": "old", "created_by": "alice", "provenance_version": 1}
    jobs.save_jobs([old])
    before = open(jobs.JOBS_FILE, "rb").read()
    jobs.load_jobs(limit=None)
    jobs.get_job("old")
    assert open(jobs.JOBS_FILE, "rb").read() == before
    assert rows(isolated) == []


def test_duplicate_new_id_is_rejected_without_extra_event(isolated):
    jobs.create_job({"job_id": "same"})
    with pytest.raises(ValueError, match="already exists"):
        jobs.create_job({"job_id": "same"})
    assert len(jobs.load_jobs(limit=None)) == 1
    assert len(rows(isolated)) == 1


def test_parallel_creators_do_not_cross_accounts(isolated):
    actors = [oa.actor_from_trusted_username(name) for name in ("alice", "bob")]
    def create(i):
        with oa.actor_context(actors[i % 2], f"req-{i}"):
            return jobs.create_job({"job_id": f"job-{i}"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(create, range(12)))
    assert len(rows(isolated)) == 12
    for event in rows(isolated):
        i = int(event["resource_id"].split("-")[1])
        assert event["actor"]["user_id"] == actors[i % 2]["user_id"]


@pytest.mark.parametrize("historical", [False, True])
def test_dispatch_links_only_audit_creator_and_hashes_exact_text(isolated, historical):
    from task_server.services.job_audit import record_dispatch
    actor = oa.actor_from_trusted_username("alice")
    job = {"job_id": "dispatch", "created_by": "alice", "initiator_user_id": actor["user_id"]}
    if not historical:
        with oa.actor_context(actor):
            jobs.create_job(job)
    text = "android: {}\ntasks: []\n# secret sentinel 秘密\n"
    with oa.actor_context({"kind": "runner", "username": "runner"}):
        record_dispatch(job["job_id"], text)
    event = rows(isolated)[-1]
    assert event["action"] == "job.dispatch_prepared"
    assert event["result"] == "accepted"
    assert event["actor"]["kind"] == "runner"
    assert event["initiator_user_id"] == ("" if historical else actor["user_id"])
    assert event["content_sha256"] == hashlib.sha256(text.encode()).hexdigest()
    assert "sentinel" not in json.dumps(event)


def test_unknown_and_ambiguous_creation_never_guess_initiator(isolated):
    from task_server.services.job_audit import record_dispatch
    jobs.create_job({"job_id": "unknown", "created_by": "alice"})
    record_dispatch("unknown", "yaml")
    assert rows(isolated)[-1]["initiator_user_id"] == ""
    for name in ("alice", "bob"):
        isolated.append({"actor": oa.actor_from_trusted_username(name), "action": "job.created",
                         "resource_type": "job", "resource_id": "ambiguous", "result": "success"})
    record_dispatch("ambiguous", "yaml")
    assert rows(isolated)[-1]["initiator_user_id"] == ""


def test_audit_outage_does_not_duplicate_or_block_job(isolated, monkeypatch, caplog):
    monkeypatch.setattr(oa.OperationStore, "append", lambda *args: {"stored": False})
    job = jobs.create_job({"job_id": "outage"})
    assert jobs.get_job(job["job_id"])
    assert "job audit unavailable" in caplog.text


def test_persistence_failure_does_not_claim_creation(isolated, monkeypatch):
    def fail(*args):
        raise OSError("disk unavailable")
    monkeypatch.setattr(jobs, "save_jobs", fail)
    with pytest.raises(OSError):
        jobs.create_job({"job_id": "failed"})
    assert rows(isolated) == []


def test_sqlite_outage_keeps_dispatch_in_durable_spool(isolated, monkeypatch):
    import sqlite3
    from task_server.services.job_audit import record_dispatch
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("locked")
    monkeypatch.setattr(oa.OperationStore, "_connect", fail)
    record_dispatch("offline", "exact yaml")
    saved = list(isolated.spool_dir.glob("*.json"))
    assert len(saved) == 1
    event = json.loads(saved[0].read_text())
    assert event["content_sha256"] == hashlib.sha256(b"exact yaml").hexdigest()
    assert event["initiator_user_id"] == ""


def test_real_http_create_dispatch_and_new_run_account_isolation(operation_server, identity_db, tmp_path, monkeypatch):
    from task_server import auth, config, identity, router
    _, user_store = identity_db
    for name in ("alice", "bob"):
        create_member(user_store, name, scope=ALL_SCOPE)
        activate(user_store, name)
    server, store = operation_server
    root = tmp_path / "tasks"
    (root / "module").mkdir(parents=True)
    (root / "module" / "case.yaml").write_text("android: {}\ntasks: []\n", encoding="utf-8")
    for module in (router, jobs, config):
        monkeypatch.setattr(module, "TASK_DIR", str(root))
    monkeypatch.setattr(jobs, "JOBS_FILE", str(tmp_path / "jobs.json"))
    monkeypatch.setattr(jobs, "_update_task_meta", lambda *args: None)
    monkeypatch.setattr(router, "update_task_meta", lambda *args: None)
    monkeypatch.setattr(router, "recover_timed_out_jobs", lambda: None)
    monkeypatch.setattr(auth, "TOKEN", "isolated-runner-secret")
    for name in ("alice", "bob"):
        token = auth.create_session_token(name)
        status, body = request(server, "POST", "/api/run-request", token, {
            "module": "module", "file": "case.yaml", "device_id": "fake-device",
            "created_by": "admin", "initiator_user_id": "forged"})
        assert status == 200, body
        job = json.loads(body)["job"]
        job["started_at"] = "now"
        monkeypatch.setattr(router, "assign_job", lambda *args: job)
        status, body = request(server, "GET", "/api/runner/jobs/next", extra={"x-token": "isolated-runner-secret"})
        assert status == 200, body
        sent = json.loads(body)["job"]["yaml_content"]
        status, body = request(server, "GET", "/api/operations?resource_type=job", token)
        assert status == 200, body
        mine = json.loads(body)["events"]
        dispatch = next(event for event in mine if event["action"] == "job.dispatch_prepared")
        assert dispatch["resource_id"] == job["job_id"]
        assert dispatch["actor"]["kind"] == "runner"
        assert dispatch["initiator_user_id"] == identity.get_access_profile(name)["user_id"]
        assert dispatch["content_sha256"] == hashlib.sha256(sent.encode()).hexdigest()
        assert all(event["resource_id"] == job["job_id"] for event in mine if event["action"] == "job.dispatch_prepared")


@pytest.mark.parametrize("mode", ["ready", "empty", "failed"])
def test_runner_route_audits_after_transform_only(isolated, tmp_path, monkeypatch, mode):
    from task_server import router
    path = tmp_path / "input.yaml"
    path.write_text("tasks: []\n", encoding="utf-8")
    selected = {"job_id": "route", "module": "module", "file": "input.yaml", "started_at": "now"}
    monkeypatch.setattr(router, "recover_timed_out_jobs", lambda: None)
    monkeypatch.setattr(router, "_require_runner_auth", lambda _: False)
    monkeypatch.setattr(router, "assign_job", lambda *args: None if mode == "empty" else selected)
    monkeypatch.setattr(router, "update_task_meta", lambda *args: None)
    monkeypatch.setattr(router, "safe_join", lambda *args: str(path if mode != "failed" else tmp_path / "missing"))
    monkeypatch.setattr(router, "midscene_cli_dispatch_yaml_text", lambda *args, **kwargs: "transformed YAML")
    monkeypatch.setattr(router, "load_jobs", lambda **kwargs: [])
    monkeypatch.setattr(router, "save_jobs", lambda *args: None)
    class Handler:
        def _json(self, payload, status=200):
            self.payload, self.status = payload, status
    handler = Handler()
    router._get_runner_jobs_next(handler, {})
    if mode == "ready":
        event, = rows(isolated)
        sent = handler.payload["job"]["yaml_content"]
        assert sent == "transformed YAML"
        assert event["content_sha256"] == hashlib.sha256(sent.encode()).hexdigest()
    else:
        assert rows(isolated) == []
        assert handler.status == (500 if mode == "failed" else 200)
