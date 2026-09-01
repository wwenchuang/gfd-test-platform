"""Authorization regressions using the existing isolated PostgreSQL HTTP gate."""

from datetime import datetime
import hashlib
import io
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from task_server.api_testing import access, http
from task_server.api_testing.models.case import ApiBaseline, ApiCase, ApiCaseVersion
from task_server.api_testing.models.environment import ApiEnvironment, ApiEnvironmentRevision, ApiEnvironmentService
from task_server.api_testing.models.execution import ApiExecution, ApiExecutionCase
from task_server.api_testing.models.case import ApiAiJob
from task_server.api_testing.models.scheduled_job import ApiScheduledJob
from task_server.api_testing.models.test_task import ApiTestTask
from task_server.api_testing.services.case_service import CaseService
from task_server.api_testing.services.scheduled_job_service import ScheduledJobService
from task_server.api_testing.services.execution_service import ExecutionService
from tests.api_testing.test_http_contract import api_context, owned_records, http_client, _auth, _audit


@pytest.fixture()
def identities(monkeypatch, owned_records):
    project = owned_records["project"].id
    environment = owned_records["environment"].id
    profiles = {
        actor: dict(username=actor, status="active", must_change_password=False,
                    is_superuser=False, permissions=list(permissions),
                    scope={"api_projects": [project], "api_environments": [environment]})
        for actor, permissions in (
            ("member", ["api.view", "api.edit", "api.execute", "api.delete", "api.baseline", "api.environment"]),
            ("reader", ["api.view"]),
        )
    }
    monkeypatch.setattr(access, "get_access_profile", lambda actor: profiles.get(actor))
    monkeypatch.setattr(http, "verify_session_token", lambda token: {"user": token} if token in profiles else None)
    monkeypatch.setattr("task_server.identity.session_is_active", lambda actor, digest: actor in profiles and digest == hashlib.sha256(actor.encode()).hexdigest())
    monkeypatch.setattr("task_server.identity.audit_event", lambda *_args: None)
    return profiles


def test_shared_project_lists_and_detail_keep_owner(api_context, owned_records, identities, http_client):
    project = owned_records["project"].id
    listed = http_client.get(http.API_PREFIX + "/projects", _auth("member"))
    assert listed.status == 200, listed.body
    assert [item["id"] for item in listed.body["data"]["projects"]] == [project]
    for path in (
        f"/cases?source_revision_id={owned_records['revision'].id}",
        f"/cases/{owned_records['case'].id}",
        f"/executions?project_id={project}",
        f"/executions/{owned_records['execution'].id}",
        f"/environments?project_id={project}",
        "/context-options",
    ):
        assert http_client.get(http.API_PREFIX + path, _auth("reader")).status == 200
    renamed = http_client.put(http.API_PREFIX + f"/projects/{project}", {"name": "Shared"}, _auth("member"))
    assert renamed.status == 200
    with api_context["factory"]() as session:
        record = session.get(type(owned_records["project"]), project)
        assert record.owner_id == record.created_by == "owner-a"
        assert record.updated_by == "member"


@pytest.mark.parametrize("method,path", [
    ("post", "/projects"), ("put", "/projects/id"), ("delete", "/projects/id"),
    ("post", "/sources/preview"), ("post", "/sources/apifox/id/activate"),
    ("post", "/environments/import"), ("post", "/environments/id/revisions"),
    ("post", "/environment-revisions/id/restore"), ("delete", "/environments/id"),
    ("post", "/cases"), ("post", "/cases/id/versions"), ("delete", "/cases/id"),
    ("post", "/case-versions/id/baseline"), ("put", "/baselines/id"),
    ("post", "/baselines/bulk-group"), ("delete", "/baselines/id"),
    ("post", "/baselines/id/assertion-upgrade-draft"),
    ("post", "/executions"), ("post", "/regressions"), ("post", "/executions/id/cancel"),
    ("post", "/executions/archive"), ("post", "/executions/id/notify"),
    ("post", "/workflow-steps/preview"), ("post", "/ai-jobs"),
    ("post", "/tasks"), ("put", "/tasks/id"), ("post", "/tasks/id/run"),
    ("post", "/scheduled-jobs"), ("put", "/scheduled-jobs/id"),
    ("post", "/scheduled-jobs/id/run"), ("delete", "/scheduled-jobs/id"),
    ("put", "/providers/apifox/credential"), ("put", "/notifications/feishu"),
    ("post", "/notifications/feishu/test"),
])
def test_readonly_denies_all_write_routes_before_payload(method, path, identities, http_client):
    response = getattr(http_client, method)(http.API_PREFIX + path, headers=_auth("reader"))
    assert response.status == 403, (method, path, response.body)
    assert response.body["error"]["code"] == "permission_denied"


def test_crossproject_and_environment_ids_fail_closed(owned_records, identities, http_client):
    project = owned_records["project"].id
    assert http_client.put(http.API_PREFIX + f"/projects/{owned_records['other_project'].id}", {"name": "bad"}, _auth("member")).status == 404
    identities["member"]["scope"]["api_environments"] = []
    for path in (f"/environments/{owned_records['environment'].id}",
                 f"/environment-revisions/{owned_records['environment_revision'].id}",
                 f"/executions/{owned_records['execution'].id}"):
        assert http_client.get(http.API_PREFIX + path, _auth("member")).status == 404
    response = http_client.get(http.API_PREFIX + f"/executions?project_id={project}", _auth("member"))
    assert response.body["data"]["executions"] == []
    response = http_client.get(http.API_PREFIX + f"/environments?project_id={project}", _auth("member"))
    assert response.body["data"]["environments"] == []


def test_sse_ticket_rechecks_live_permission_and_scope(owned_records, identities, http_client):
    execution = owned_records["execution"].id
    response = http_client.post(http.API_PREFIX + f"/executions/{execution}/sse-ticket", {}, _auth("reader"))
    assert response.status == 200
    ticket = response.body["data"]["ticket"]
    identities["reader"]["permissions"] = []
    assert http_client.get(http.API_PREFIX + f"/executions/{execution}/events?ticket={ticket}").status == 403
    identities["reader"]["permissions"] = ["api.view"]
    identities["reader"]["scope"]["api_environments"] = []
    assert http_client.get(http.API_PREFIX + f"/executions/{execution}/events?ticket={ticket}").status == 404


def test_production_execution_and_secret_configuration(owned_records, identities, api_context, http_client):
    with api_context["factory"].begin() as session:
        session.get(ApiEnvironment, owned_records["environment"].id).name = "production"
    payload = {"project_id": owned_records["project"].id,
               "source_revision_id": owned_records["revision"].id,
               "environment_revision_id": owned_records["environment_revision"].id,
               "case_version_ids": [owned_records["version"].id], "execution_type": "debug",
               "overrides": {}, "idempotency_key": "access-production"}
    response = http_client.post(http.API_PREFIX + "/executions", payload, _auth("member"))
    assert response.status == 403
    identities["member"]["permissions"].remove("api.environment")
    response = http_client.post(http.API_PREFIX + f"/environments/{owned_records['environment'].id}/revisions",
                                {"secret_updates": {"token": "private-test-value"}}, _auth("member"))
    assert response.status == 403


def test_workspace_is_personal_and_scope_options_are_metadata(owned_records, identities, api_context, http_client):
    context = {"project_id": owned_records["project"].id, "source_revision_id": owned_records["revision"].id,
               "environment_revision_id": owned_records["environment_revision"].id}
    assert http_client.put(http.API_PREFIX + "/workspace", context, _auth("member")).status == 200
    assert http_client.get(http.API_PREFIX + "/workspace", _auth("reader")).body["data"]["workspace"] is None
    result = access.list_identity_scope_options(api_context["factory"])
    assert set(result) == {"api_projects", "api_environments"}
    assert all(set(item) == {"id", "name"} for item in result["api_projects"])
    assert all(set(item) == {"id", "name", "project_id"} for item in result["api_environments"])


def test_shared_task_actor_owner_and_schedule_revocation(owned_records, identities, api_context, http_client):
    records = owned_records
    task = {"project_id": records["project"].id, "source_revision_id": records["revision"].id,
            "environment_revision_id": records["environment_revision"].id,
            "selected_endpoint_ids": [records["endpoint"].id], "name": "Shared task"}
    response = http_client.post(http.API_PREFIX + "/tasks", task, _auth("member"))
    assert response.status == 200, response.body
    payload = {**task, "target_type": "cases", "target_ids": [records["version"].id],
               "schedule_type": "cron", "cron_expression": "* * * * *",
               "environment_strategy": "fixed_revision", "enabled": True}
    response = http_client.post(http.API_PREFIX + "/scheduled-jobs", payload, _auth("member"))
    assert response.status == 200, response.body
    with api_context["factory"]() as session:
        job = session.get(ApiScheduledJob, response.body["data"]["scheduled_job"]["id"])
        assert job.owner_id == "owner-a"
        assert job.created_by == job.updated_by == "member"
    identities["member"]["status"] = "disabled"
    dispatched = []
    ScheduledJobService(api_context["factory"], enqueue=dispatched.append).dispatch_due(now=datetime.now().astimezone())
    assert dispatched == []


def test_unknown_identity_remains_owner_scoped(api_context, owned_records):
    with api_context["factory"]() as session:
        assert session.scalars(select(type(owned_records["project"])).where(access.project_predicate("stranger"))).all() == []
        assert len(session.scalars(select(type(owned_records["project"])).where(access.project_predicate("owner-a"))).all()) == 1


def test_explicit_production_metadata_survives_rename_and_false_revision(owned_records, identities, api_context, http_client):
    env = owned_records["environment"]
    revision = owned_records["environment_revision"]
    with api_context["factory"].begin() as session:
        session.get(ApiEnvironment, env.id).active_revision_id = revision.id
        session.add(ApiEnvironmentService(revision_id=revision.id, service_name="default", base_url="https://api.example.test",
                                          metadata_json={"production": True}, **_audit("owner-a")))
    denied = http_client.post(http.API_PREFIX + f"/environments/{env.id}/revisions", {"name": "test"}, _auth("member"))
    assert denied.status == 403
    with api_context["factory"]() as session:
        with pytest.raises(access.AccessDeniedError):
            access.require_execution_environment(session, revision.id, "member")
    identities["member"]["permissions"].append("api.production")
    changed = http_client.post(http.API_PREFIX + f"/environments/{env.id}/revisions",
                              {"name": "test", "services": {"default": {"base_url": "https://api.example.test", "metadata": {"production": False}}}}, _auth("member"))
    assert changed.status == 200, changed.body
    identities["member"]["permissions"].remove("api.production")
    with api_context["factory"]() as session:
        with pytest.raises(access.AccessDeniedError):
            access.require_execution_environment(session, changed.body["data"]["environment"]["revision_id"], "member")


def _passing_evidence(factory, records):
    with factory.begin() as session:
        child = ApiExecutionCase(execution_id=records["execution"].id, case_version_id=records["version"].id,
                                 endpoint_id=records["endpoint"].id, environment_revision_id=records["environment_revision"].id,
                                 ordinal=0, status="PASSED", sanitized_result={}, **_audit("owner-a"))
        session.add(child)
        session.flush()
        return child.id


def test_execution_case_evidence_rechecks_reader_scope(
    owned_records, identities, api_context, http_client
):
    evidence_id = _passing_evidence(api_context["factory"], owned_records)
    path = (
        http.API_PREFIX
        + f"/executions/{owned_records['execution'].id}/cases/{evidence_id}"
    )

    assert http_client.get(path, _auth("reader")).status == 200
    identities["reader"]["scope"]["api_environments"] = []
    assert http_client.get(path, _auth("reader")).status == 404


def test_shared_baseline_adoption_and_atomic_mixed_batch(owned_records, identities, api_context, http_client):
    evidence_id = _passing_evidence(api_context["factory"], owned_records)
    service = CaseService(api_context["factory"])
    baseline = service.adopt_baseline(owned_records["version"].id, evidence_id, "member")
    assert baseline.adopted_by == "member"
    with api_context["factory"]() as session:
        assert session.get(ApiBaseline, baseline.id).owner_id == "owner-a"
    response = http_client.get(http.API_PREFIX + f"/baselines?project_id={owned_records['project'].id}", _auth("reader"))
    assert [item["id"] for item in response.body["data"]["baselines"]] == [baseline.id]
    response = http_client.post(http.API_PREFIX + "/baselines/bulk-group", {"baseline_ids": [baseline.id, str(uuid4())], "group_name": "bad"}, _auth("member"))
    assert response.status == 404
    assert service.get_baseline(baseline.id).group_name != "bad"
    request = {"project_id": owned_records["project"].id, "source_revision_id": owned_records["revision"].id,
               "environment_revision_id": owned_records["environment_revision"].id,
               "baseline_ids": [baseline.id, str(uuid4())], "idempotency_key": "mixed-baseline-access"}
    response = http_client.post(http.API_PREFIX + "/regressions", request, _auth("member"))
    assert response.status == 404, response.body


def test_latest_ai_job_does_not_leak_revoked_environment(owned_records, identities, api_context, http_client):
    with api_context["factory"].begin() as session:
        session.add(ApiAiJob(project_id=owned_records["project"].id,
                             environment_revision_id=owned_records["environment_revision"].id,
                             endpoint_ids=[owned_records["endpoint"].id], requested_model="test", state="queued",
                             **_audit("owner-a")))
    identities["reader"]["scope"]["api_environments"] = []
    response = http_client.get(http.API_PREFIX + f"/ai-jobs/latest?project_id={owned_records['project'].id}", _auth("reader"))
    assert response.status == 200
    assert response.body["data"]["job"] is None


def test_worker_rechecks_actor_before_execution(owned_records, identities, api_context):
    with api_context["factory"].begin() as session:
        execution = session.get(ApiExecution, owned_records["execution"].id)
        execution.state = "QUEUED"
        execution.created_by = "member"
    identities["member"]["permissions"].remove("api.execute")
    assert ExecutionService(api_context["factory"]).run(owned_records["execution"].id) is False
    with api_context["factory"]() as session:
        assert session.get(ApiExecution, owned_records["execution"].id).state == "CANCELLED"


def test_personal_provider_credentials_do_not_follow_project_owner(owned_records, identities, api_context, http_client, monkeypatch):
    from task_server.api_testing.services.provider_service import ProviderService
    monkeypatch.setenv("API_TESTING_SECRET_KEY", "access-test-key-which-is-not-a-production-secret")
    # A missing credential is sufficient to prove there is no owner fallback.
    response = http_client.get(http.API_PREFIX + "/providers/apifox/credential", _auth("member"))
    assert response.status == 200
    assert response.body["data"]["credential"]["configured"] is False
    with pytest.raises(access.AccessDeniedError):
        ProviderService(api_context["factory"]).save_apifox_credential("owner-a", "test-only", "member")


def test_sse_does_not_emit_events_after_permission_revoked(identities, monkeypatch):
    class Handler:
        headers = {}
        _api_session_digest = hashlib.sha256(b"reader").hexdigest()
        wfile = io.BytesIO()
        def send_response(self, *args): pass
        def _cors(self): pass
        def send_header(self, *args): pass
        def end_headers(self): pass
    def read(*_args):
        identities["reader"]["permissions"] = []
        return [SimpleNamespace(sequence=1, type="execution_finished", payload={"sensitive": "evidence"}, created_at=None)]
    monkeypatch.setattr(http, "_scope_execution", lambda *_args: SimpleNamespace(state="RUNNING"))
    monkeypatch.setattr(http, "_event_stream", lambda *_args: SimpleNamespace(read=read))
    handler = Handler()
    http._stream_events(handler, str(uuid4()), "access-sse", "reader")
    assert handler.wfile.getvalue() == b""


def test_execution_scope_checks_do_not_load_request_evidence(identities, owned_records, api_context):
    statements = []
    engine = api_context["factory"].kw["bind"]
    def capture(_conn, _cursor, statement, _params, _context, _many):
        statements.append(statement.lower())
    event.listen(engine, "before_cursor_execute", capture)
    try:
        http._scope_execution(api_context["factory"], owned_records["execution"].id, "reader")
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert len(statements) == 1
    assert "request_snapshot" not in statements[0]


def test_task_does_not_expose_latest_report_from_revoked_environment(identities, owned_records, api_context, http_client):
    with api_context["factory"].begin() as session:
        hidden = ApiEnvironment(project_id=owned_records["project"].id, name="hidden", **_audit("owner-a"))
        session.add(hidden)
        session.flush()
        revision = ApiEnvironmentRevision(environment_id=hidden.id, revision_number=1, name="hidden", **_audit("owner-a"))
        session.add(revision)
        session.flush()
        execution = session.get(ApiExecution, owned_records["execution"].id)
        execution.environment_revision_id = revision.id
        execution.summary = {"hidden-result": "must not leak"}
        job = ApiAiJob(project_id=owned_records["project"].id, environment_revision_id=revision.id,
                       endpoint_ids=[owned_records["endpoint"].id], **_audit("owner-a"))
        session.add(job)
        session.flush()
        task = ApiTestTask(project_id=owned_records["project"].id, source_revision_id=owned_records["revision"].id,
                           environment_revision_id=owned_records["environment_revision"].id, name="shared",
                           selected_endpoint_ids=[owned_records["endpoint"].id], latest_execution_id=execution.id,
                           latest_ai_job_id=job.id,
                           summary={"hidden-result": "must not leak"}, **_audit("owner-a"))
        session.add(task)
    response = http_client.get(http.API_PREFIX + f"/tasks?project_id={owned_records['project'].id}", _auth("reader"))
    assert response.status == 200
    item = response.body["data"]["tasks"][0]
    assert item["latest_execution_id"] is None
    assert item["latest_ai_job_id"] is None
    assert item["latest_execution_summary"] == item["summary"] == {}


def test_sse_ticket_rechecks_original_session_after_logout(identities, owned_records, http_client, monkeypatch):
    execution = owned_records["execution"].id
    response = http_client.post(http.API_PREFIX + f"/executions/{execution}/sse-ticket", {}, _auth("reader"))
    assert response.status == 200
    ticket = response.body["data"]["ticket"]
    monkeypatch.setattr("task_server.identity.session_is_active", lambda *_args: False)
    response = http_client.get(http.API_PREFIX + f"/executions/{execution}/events?ticket={ticket}")
    assert response.status == 401


@pytest.mark.parametrize("state", ["RUNNING", "PASSED"])
def test_sse_rechecks_session_before_live_and_terminal_batch(identities, monkeypatch, state):
    active = [True]
    monkeypatch.setattr("task_server.identity.session_is_active", lambda *_args: active[0])
    class Handler:
        headers = {}
        _api_session_digest = hashlib.sha256(b"reader").hexdigest()
        wfile = io.BytesIO()
        def send_response(self, *args): pass
        def _cors(self): pass
        def send_header(self, *args): pass
        def end_headers(self): pass
    def read(*_args):
        active[0] = False
        return [SimpleNamespace(sequence=1, type="execution_finished", payload={"sensitive": "evidence"}, created_at=None)]
    monkeypatch.setattr(http, "_scope_execution", lambda *_args: SimpleNamespace(state=state))
    monkeypatch.setattr(http, "_event_stream", lambda *_args: SimpleNamespace(read=read))
    handler = Handler()
    http._stream_events(handler, str(uuid4()), "session-sse", "reader")
    assert handler.wfile.getvalue() == b""


def test_schedule_revoked_baseline_environment_blocks_one_job_only(identities, owned_records, api_context):
    records = owned_records
    factory = api_context["factory"]
    baseline = CaseService(factory).adopt_baseline(records["version"].id, _passing_evidence(factory, records), "member")
    with factory.begin() as session:
        environment = ApiEnvironment(project_id=records["project"].id, name="baseline-only", **_audit("owner-a"))
        session.add(environment)
        session.flush()
        revision = ApiEnvironmentRevision(environment_id=environment.id, revision_number=1, name="baseline-only", **_audit("owner-a"))
        session.add(revision)
        session.flush()
        session.get(ApiBaseline, baseline.id).environment_revision_id = revision.id
    identities["member"]["scope"]["api_environments"].append(environment.id)
    payload = {"project_id": records["project"].id, "source_revision_id": records["revision"].id,
               "environment_revision_id": records["environment_revision"].id, "name": "blocked-target",
               "target_type": "baselines", "target_ids": [baseline.id], "schedule_type": "cron",
               "cron_expression": "* * * * *", "environment_strategy": "fixed_revision", "enabled": True}
    enqueued = []
    service = ScheduledJobService(factory, enqueue=enqueued.append)
    blocked = service.create(payload, "member")
    valid = service.create({**payload, "name": "valid-target", "target_type": "cases", "target_ids": [records["version"].id]}, "member")
    identities["member"]["scope"]["api_environments"].remove(environment.id)
    result = service.dispatch_due()
    assert len(result) == len(enqueued) == 1
    assert service.get(blocked.id, "member").blocked_reason
    assert service.get(valid.id, "member").blocked_reason == ""


def test_api_mutation_and_denied_audit_excludes_payload_and_query(identities, owned_records, http_client, monkeypatch):
    events = []
    monkeypatch.setattr("task_server.identity.audit_event", lambda *args: events.append(args))
    project = owned_records["project"].id
    response = http_client.put(http.API_PREFIX + f"/projects/{project}?secret=query-fixture", {"name": "secret-body-fixture"}, _auth("member"))
    assert response.status == 200
    response = http_client.post(http.API_PREFIX + "/executions?token=query-fixture", {"secret": "body-fixture"}, _auth("reader"))
    assert response.status == 403
    assert len(events) == 2
    assert events[0][0] == "member"
    assert events[0][3] == {"method": "PUT", "status": 200, "ok": True}
    assert events[1][3] == {"method": "POST", "status": 403, "ok": False}
    assert "fixture" not in str(events)
    http_client.get(http.API_PREFIX + "/projects", _auth("reader"))
    assert len(events) == 2


def test_api_audit_failure_does_not_repeat_successful_write(identities, owned_records, http_client, monkeypatch):
    def broken(*_args):
        raise RuntimeError("private-audit-detail")
    monkeypatch.setattr("task_server.identity.audit_event", broken)
    response = http_client.put(http.API_PREFIX + f"/projects/{owned_records['project'].id}", {"name": "updated-once"}, _auth("member"))
    assert response.status == 200
    assert response.body["ok"] is True


def test_unknown_fixture_actor_does_not_write_identity_audit(owned_records, http_client, monkeypatch):
    events = []
    monkeypatch.setattr(access, "get_access_profile", lambda *_args: None)
    monkeypatch.setattr("task_server.identity.audit_event", lambda *args: events.append(args))
    response = http_client.put(http.API_PREFIX + f"/projects/{owned_records['project'].id}", {"name": "legacy-owner"}, _auth("owner-a"))
    assert response.status == 200
    assert events == []


def test_api_audit_context_is_consumed_once(monkeypatch):
    events = []
    monkeypatch.setattr("task_server.identity.audit_event", lambda *args: events.append(args))
    handler = SimpleNamespace(_api_audit_context=("member", "POST", http.API_PREFIX + "/projects"))
    http._audit_api_result(handler, 200)
    http._audit_api_result(handler, 403)
    assert len(events) == 1
    assert handler._api_audit_context is None


def test_reader_cannot_see_historical_literal_headers(identities, owned_records, api_context, http_client):
    revision_id = owned_records["environment_revision"].id
    with api_context["factory"].begin() as session:
        session.get(ApiEnvironmentRevision, revision_id).default_headers = {
            "Authorization": "Bearer historical-private-fixture", "Cookie": "sid=cookie-private-fixture",
            "X-API-Key": "literal-private-fixture", "X-Public": "value",
        }
    response = http_client.get(http.API_PREFIX + f"/environment-revisions/{revision_id}", _auth("reader"))
    assert response.status == 200
    assert "private-fixture" not in str(response.body)
    assert response.body["data"]["environment_revision"]["default_headers"] == {"Authorization": "***", "Cookie": "***", "X-API-Key": "***", "X-Public": "value"}
