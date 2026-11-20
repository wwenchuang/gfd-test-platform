from datetime import datetime, timedelta, timezone
import json
import time
from types import SimpleNamespace

import pytest

from task_server.api_testing import access, http
from task_server.api_testing.models.execution import ApiExecutionCase, ApiExecutionEvent
from task_server.api_testing.models.scheduled_job import ApiScheduledJob, ApiScheduledJobRun
from task_server.api_testing.services.scheduled_job_service import ScheduledJobService
from tests.api_testing.test_scheduled_job_service import scheduled_factory, scheduled_records
from tests.api_testing.test_http_contract import http_client
from tests.test_failure_analysis_sources import api

UTC = timezone.utc
NOW = datetime(2026, 11, 20, 2, 15, tzinfo=UTC)  # 10:15 in the scheduler's zone
SLOT = NOW.replace(minute=0)


@pytest.fixture
def patrol(api, monkeypatch):
    with monkeypatch.context() as zone:
        zone.setenv("TZ", "Asia/Shanghai")
        time.tzset()
        try:
            yield _setup(api, monkeypatch)
        finally:
            zone.undo()
            time.tzset()


def _setup(api, monkeypatch):
    factory = api.factory
    engine = factory.kw["bind"]
    with engine.begin() as db:
        for model in (ApiScheduledJob, ApiScheduledJobRun, ApiExecutionEvent):
            columns = ", ".join(f'"{col.name}" {("TEXT" if col.key != "enabled" else "INTEGER")}'
                                for col in model.__table__.columns)
            db.exec_driver_sql(f"CREATE TABLE {model.__tablename__} ({columns})")
        db.connection.driver_connection.create_function("greatest", -1, max)

    def job(key="target", updated=None, created=None, **changes):
        audit = dict(created_by="owner", updated_by="owner", owner_id="owner", row_version=1,
                     updated_at=updated or NOW - timedelta(days=2), created_at=created or NOW - timedelta(days=2))
        record = dict(id=key, project_id=api.project, environment_revision_id=api.revision,
                      environment_id=api.profile["scope"]["api_environments"][0],
                      name=key, enabled=True, cron_expression="0 10 * * *", schedule_type="daily", summary="")
        with factory.begin() as db:
            db.add(ApiScheduledJob(**{**record, **changes}, **audit))
        return key

    def run(key, *, slot=SLOT, trigger="schedule", state="DONE", summary=None, updated=SLOT, **changes):
        rid, _ = api.add(run_changes=dict(state=state, updated_at=updated, summary=summary or {},
                        idempotency_key=ScheduledJobService._scheduled_idempotency_key(key, slot.astimezone()),
                        **changes), child_changes={"updated_at": updated})
        with factory.begin() as db:
            db.add(ApiScheduledJobRun(id=rid, execution_id=rid, job_id=key, trigger_type=trigger, state="queued",
                     created_at=slot, updated_at=slot, owner_id="owner", created_by="owner", updated_by="owner"))
        return rid

    def status(now=NOW):
        from task_server.api_testing.services.patrol_status import current_status
        return current_status(factory, "reader", now=now, stale_seconds=300)["items"]

    monkeypatch.setenv("MIDSCENE_AGENT_PATROL_JOB_IDS", "target, paused,edited,new,hidden,other-env")
    monkeypatch.setattr(http, "_factory", lambda: factory)
    return SimpleNamespace(job=job, run=run, status=status, factory=factory, api=api)


def test_route_read_permission_and_scope(patrol):
    patrol.job()
    patrol.job("hidden", project_id="other")
    patrol.job("other-env", environment_id="outside")
    patrol.job("unconfigured")
    data, status = http._route("GET", ("patrol", "status"), {}, None, "reader",
                               SimpleNamespace(execution_stale_seconds=300))
    assert status == 200
    assert [item["job_id"] for item in data["items"]] == ["target"]
    patrol.api.profile["permissions"] = []
    with pytest.raises(access.AccessDeniedError):
        http._route("GET", ("patrol", "status"), {}, None, "reader", SimpleNamespace())


def test_today_absent_even_with_manual_and_previous_runs(patrol):
    patrol.job()
    patrol.run("target", slot=SLOT-timedelta(days=1))
    patrol.run("target", trigger="manual")
    item, = patrol.status()
    assert (item["state"], item["execution_id"], item["expected_at"]) == (
        "missed", None, "2026-11-20T10:00:00+08:00")


def test_disabled_edited_new_and_current_minute(patrol):
    patrol.job("paused", enabled=False)
    patrol.job("edited", updated=NOW-timedelta(minutes=1))
    patrol.job("new", created=NOW-timedelta(minutes=2), updated=NOW-timedelta(minutes=2))
    patrol.job()
    by_job = {row["job_id"]: row for row in patrol.status()}
    assert by_job["paused"]["state"] == "disabled"
    for key in ("edited", "new"):
        assert (by_job[key]["state"], by_job[key]["expected_at"]) == ("pending", "2026-11-21T10:00:00+08:00")
    current = next(item for item in patrol.status(SLOT + timedelta(seconds=51)) if item["job_id"] == "target")
    assert current["state"] == "pending"


@pytest.mark.parametrize("cron,expected", [("20,40 10 * * *", "2026-11-20T10:20:00+08:00"),
                                           ("0 9 * * 1", "2026-11-23T09:00:00+08:00")])
def test_next_actual_cron_before_slot(patrol, cron, expected):
    patrol.job(cron_expression=cron)
    item, = patrol.status()
    assert (item["state"], item["expected_at"]) == ("pending", expected)


@pytest.mark.parametrize("state,summary,expected", [
    ("DONE", {"passed": 3, "failed": 0}, "completed"),
    ("DONE", {"passed": 2, "failed": 1, "agent_patrol": {"调查状态": "limited", "message": "secret-text"}}, "failed"),
    ("CANCELLED", {}, "failed"), ("RUNNING", {}, "overdue"), ("QUEUED", {}, "overdue")])
def test_current_execution_states_and_safe_projection(patrol, state, summary, expected):
    patrol.job()
    rid = patrol.run("target", state=state, summary={**summary, "raw": "secret-text"})
    row, = patrol.status()
    assert (row["state"], row["execution_id"]) == (expected, rid)
    assert "secret-text" not in json.dumps(row, ensure_ascii=False)
    if "agent_patrol" in summary:
        assert row["investigation_status"] == "limited"


def test_recent_case_progress_keeps_long_run_running(patrol):
    patrol.job()
    rid = patrol.run("target", state="RUNNING")
    with patrol.factory.begin() as db:
        case = db.query(ApiExecutionCase).filter_by(execution_id=rid).one()
        case.updated_at = NOW-timedelta(seconds=3)
    item, = patrol.status()
    assert (item["state"], item["execution_id"]) == ("running", rid)


@pytest.mark.parametrize("age,state", [(5, "pending"), (900, "overdue")])
def test_dispatch_record_without_execution_distinguishes_stalled_submit(patrol, age, state):
    patrol.job()
    with patrol.factory.begin() as db:
        db.add(ApiScheduledJobRun(id="orphan", execution_id=None, job_id="target", trigger_type="schedule",
            state="queued", created_at=SLOT, updated_at=SLOT, owner_id="owner", created_by="owner", updated_by="owner"))
    row, = patrol.status(SLOT + timedelta(seconds=age))
    assert row["state"] == state and row["execution_id"] is None


def test_current_slot_blocked_reason_never_discloses_job_error(patrol):
    patrol.job(updated=SLOT+timedelta(seconds=5), summary="blocked: do-not-disclose")
    row, = patrol.status()
    assert row["state"] == "failed"
    assert "do-not-disclose" not in str(row)


def test_invalid_cron_does_not_hide_other_jobs(patrol):
    patrol.job(cron_expression="invalid")
    patrol.job("paused", enabled=False)
    assert {row["job_id"]: row["state"] for row in patrol.status()} == {"target": "failed", "paused": "disabled"}


def test_multiple_slots_per_day_use_most_recent_slot(patrol):
    patrol.job(cron_expression="0,10,30 10 * * *")
    patrol.run("target")
    row, = patrol.status()
    assert (row["state"], row["expected_at"]) == ("missed", "2026-11-20T10:10:00+08:00")


def test_prior_inspection_is_visible_before_today_and_is_not_a_read_timestamp(patrol):
    patrol.job()
    previous = SLOT - timedelta(days=1)
    rid = patrol.run("target", slot=previous)
    recorded = previous + timedelta(minutes=20)
    with patrol.factory.begin() as db:
        db.add(ApiExecutionEvent(id="notice", execution_id=rid, event_type="agent_patrol_notice",
            sequence=1, payload={"调查状态": "本次无失败"}, owner_id="owner", created_by="owner",
            updated_by="owner", created_at=recorded, updated_at=recorded))
    row, = patrol.status(SLOT - timedelta(minutes=10))
    assert (row["state"], row["investigation_status"]) == ("pending", None)
    assert row["inspected_execution_id"] == rid and row["inspected_at"] == recorded.isoformat()
    assert row["inspected_at"] != row["checked_at"]


def test_clean_inspection_has_no_model_investigation(patrol):
    patrol.job()
    run = patrol.run("target", summary={"agent_patrol": {"调查状态": "本次无失败"}})
    row, = patrol.status()
    assert row["execution_id"] == run and row["investigation_status"] == "not_needed"


def test_uninspected_report_has_no_inspection_timestamp(patrol):
    patrol.job()
    patrol.run("target", summary={"passed": 1})
    row, = patrol.status()
    assert row["state"] == "completed"
    assert row["inspected_at"] is None and row["investigation_status"] is None


def test_current_slot_and_durable_inspection_through_http(scheduled_factory, scheduled_records, http_client, monkeypatch):
    from task_server.api_testing.models.execution import ApiExecution
    from task_server.api_testing.models.base import new_uuid

    actor = "owner-a"
    service = ScheduledJobService(scheduled_factory)
    job = service.create({
        "project_id": scheduled_records["project"].id, "name": "巡检 HTTP 验证",
        "schedule_type": "cron", "cron_expression": "* * * * *", "target_type": "cases",
        "target_ids": [scheduled_records["case_version"].id], "environment_strategy": "fixed_revision",
        "environment_revision_id": scheduled_records["environment_revision"].id,
    }, actor)
    now = datetime.now(UTC).astimezone()
    run_id = service.run_once(job.id, actor, trigger_type="schedule",
        idempotency_key=service._scheduled_idempotency_key(job.id, now)).id
    with scheduled_factory.begin() as db:
        run = db.get(ApiExecution, run_id)
        run.state, run.summary = "DONE", {"total": 1, "passed": 1, "agent_patrol": {"调查状态": "本次无失败"}}
        db.add(ApiExecutionEvent(id=new_uuid(), execution_id=run_id, event_type="agent_patrol_notice",
            sequence=100, payload={"调查状态": "本次无失败"}, created_by=actor, updated_by=actor, owner_id=actor))
    monkeypatch.setenv("MIDSCENE_AGENT_PATROL_JOB_IDS", job.id)
    monkeypatch.setattr(http, "_factory", lambda: scheduled_factory)
    monkeypatch.setattr(http, "verify_session_token", lambda token: {"user": actor} if token == "test-reader" else None)
    monkeypatch.setattr(http.ApiTestingSettings, "from_env", lambda: SimpleNamespace(enabled=True, execution_stale_seconds=300))
    from task_server.api_testing.services import patrol_status
    class FixedTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)
    monkeypatch.setattr(patrol_status, "datetime", FixedTime)
    path = http.API_PREFIX + "/patrol/status"
    assert http_client.get(path).status == 401
    response = http_client.get(path, headers={"Authorization": "Bearer test-reader"})
    assert response.status == 200 and response.body["ok"] is True
    row, = response.body["data"]["items"]
    assert (row["execution_id"], row["inspected_execution_id"]) == (run_id, run_id)
    assert (row["state"], row["case_count"], row["investigation_status"]) == ("completed", 1, "not_needed")
    assert datetime.fromisoformat(row["inspected_at"]).tzinfo is not None
