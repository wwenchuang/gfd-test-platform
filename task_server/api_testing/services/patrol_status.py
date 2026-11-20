"""Read the current local-day regression slot without starting a job or investigation."""
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from .. import access
from ..models.execution import ApiExecution, ApiExecutionEvent
from ..models.project import ApiProject
from ..models.scheduled_job import ApiScheduledJob, ApiScheduledJobRun
from ..repositories.execution_repository import ExecutionRepository
from .scheduled_job_service import ScheduledJobInputError, ScheduledJobService, _next_cron_match, _parse_cron


def _utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _next_slot(cron, local):
    # Dispatch uses the server's local clock. Resolve the offset at the candidate
    # time (including DST) and skip a clock minute that does not exist.
    cursor = local.replace(tzinfo=None)
    while (wall := _next_cron_match(cron, cursor)) is not None:
        slot = wall.astimezone()
        if slot.replace(tzinfo=None) == wall:
            return slot
        cursor = wall
    return None


def _today_slot(cron, now):
    minutes, hours, days, months, weekdays = _parse_cron(cron)
    if now.day not in days or now.month not in months or (now.weekday() + 1) % 7 not in weekdays:
        return None
    day = now.replace(tzinfo=None, second=0, microsecond=0, fold=0)
    for hour in sorted(hours, reverse=True):
        for minute in sorted(minutes, reverse=True):
            wall = day.replace(hour=hour, minute=minute)
            slot = wall.astimezone()
            if slot <= now and slot.replace(tzinfo=None) == wall:
                return slot
    return None


def _run_status(session, run, now, stale_seconds):
    summary = run.summary if isinstance(run.summary, dict) else {}
    state = summary.get("_archived_from_state") if run.state == "ARCHIVED" else run.state
    if state in ("RUNNING", "QUEUED"):
        progress = ExecutionRepository(session).execution_progress_at(run.id)
        if _utc(progress) < now - timedelta(seconds=stale_seconds):
            return "overdue", f"已有 {stale_seconds} 秒未记录新的执行进展"
        if state == "QUEUED":
            return "pending", "已创建执行，等待 Worker 领取"
        return "running", "回归执行中"

    labels = {"passed": "通过", "failed": "失败", "broken": "异常", "skipped": "跳过", "cancelled": "取消"}
    counts = {key: summary[key] for key in labels if type(summary.get(key)) is int and summary[key] >= 0}
    details = "，".join(f"{labels[key]} {value}" for key, value in counts.items())
    if state == "CANCELLED":
        return "failed", "本次回归已取消"
    if state in ("FAILED", "BROKEN") or counts.get("failed") or counts.get("broken"):
        return "failed", details or "本次回归失败"
    if state in ("DONE", "PASSED"):
        return "completed", details or "本次回归已结束"
    return "pending", "执行状态待核对"


def _job_status(session, job, actor, now, stale_seconds):
    scope = (ApiScheduledJobRun.job_id == job.id, ApiScheduledJobRun.trigger_type == "schedule",
             ApiExecution.project_id == job.project_id, access.resource_predicate(actor, ApiExecution))
    inspection = session.execute(
        select(ApiExecutionEvent.created_at, ApiExecutionEvent.execution_id)
        .join(ApiExecution, ApiExecution.id == ApiExecutionEvent.execution_id)
        .join(ApiScheduledJobRun, ApiScheduledJobRun.execution_id == ApiExecution.id)
        .where(*scope, ApiExecutionEvent.event_type == "agent_patrol_notice")
        .order_by(ApiExecutionEvent.created_at.desc(), ApiExecutionEvent.id.desc()).limit(1)
    ).first()
    item = dict(job_id=job.id, job_name=job.name, checked_at=now.isoformat(), expected_at=None,
                inspected_at=_utc(inspection.created_at).isoformat() if inspection else None,
                inspected_execution_id=inspection.execution_id if inspection else None,
                execution_id=None, case_count=None, investigation_status=None,
                state="disabled", summary="定时任务已停用")
    if not job.enabled:
        return item
    cron = ScheduledJobService._effective_cron_expression(job)
    try:
        slot = _today_slot(cron, now)
        expected = slot or _next_slot(cron, now)
    except ScheduledJobInputError:
        expected = None
    if expected is None:
        return {**item, "state": "failed", "summary": "调度规则无可用的计划时间"}
    item["expected_at"] = expected.isoformat()
    if slot is None:
        return {**item, "state": "pending", "summary": "等待下次计划时间"}

    # The scheduler binds the exact local slot via its idempotency key. Ignore
    # manual launches, other days and unrelated executions in the same minute.
    run = session.execute(select(ApiExecution.id, ApiExecution.state, ApiExecution.summary)
        .join(ApiScheduledJobRun, ApiScheduledJobRun.execution_id == ApiExecution.id)
        .where(*scope, ApiExecution.idempotency_key == ScheduledJobService._scheduled_idempotency_key(job.id, slot))
        .limit(1)).first()
    if run:
        item["execution_id"] = run.id
        item["state"], item["summary"] = _run_status(session, run, now, stale_seconds)
        totals = run.summary if isinstance(run.summary, dict) else {}
        if type(totals.get("total")) is int and totals["total"] >= 0:
            item["case_count"] = totals["total"]
        notice = totals.get("agent_patrol")
        status = notice.get("调查状态") if isinstance(notice, dict) else None
        if status == "本次无失败":
            item["investigation_status"] = "not_needed"
        elif status in ("completed", "limited", "failed", "cancelled", "interrupted", "running", "queued"):
            item["investigation_status"] = status
        return item

    end = slot + timedelta(minutes=1)
    updated = _utc(job.updated_at)
    if (job.summary or "").startswith("blocked: ") and slot <= updated < end:
        return {**item, "state": "failed", "summary": "本次定时调度受阻，请核对任务权限和目标配置"}
    if updated > slot:
        upcoming = _next_slot(cron, now)
        return {**item, "state": "pending", "summary": "计划已更新，等待下次调度",
                "expected_at": upcoming.isoformat() if upcoming else None}
    orphan = session.scalar(select(ApiScheduledJobRun.id).where(ApiScheduledJobRun.job_id == job.id,
        ApiScheduledJobRun.trigger_type == "schedule", ApiScheduledJobRun.execution_id.is_(None),
        ApiScheduledJobRun.created_at >= _utc(slot), ApiScheduledJobRun.created_at < _utc(end)).limit(1))
    if orphan:
        late = now >= end + timedelta(seconds=stale_seconds)
        return {**item, "state": "overdue" if late else "pending", "summary": "调度已登记，尚未关联执行记录"}
    if now < end:
        return {**item, "state": "pending", "summary": "处于本次调度时间窗口"}
    return {**item, "state": "missed", "summary": "已过计划时间，未查到本次自动执行记录"}


def current_status(factory, actor, *, stale_seconds, now=None):
    access.require_permission(actor, "api.view")
    job_ids = [part.strip() for part in os.getenv("MIDSCENE_AGENT_PATROL_JOB_IDS", "").split(",") if part.strip()]
    if not job_ids:
        return {"items": []}
    checked_at = (now or datetime.now(timezone.utc)).astimezone()
    with factory() as session:
        jobs = session.scalars(select(ApiScheduledJob).join(ApiProject, ApiProject.id == ApiScheduledJob.project_id)
            .where(ApiScheduledJob.id.in_(job_ids), access.project_predicate(actor),
                   access.resource_predicate(actor, ApiScheduledJob)).order_by(ApiScheduledJob.name, ApiScheduledJob.id))
        return {"items": [_job_status(session, job, actor, checked_at, stale_seconds) for job in jobs]}
