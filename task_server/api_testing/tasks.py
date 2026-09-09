"""Celery entry point for API execution; core behavior remains synchronously testable."""

import logging
import copy
from datetime import datetime
import time
import uuid

from sqlalchemy import select

from celery import Celery
from celery.signals import heartbeat_sent, worker_ready

from .config import ApiTestingSettings
from .db import _session_factory
from .events import EventStream
from .repositories.execution_repository import ExecutionRepository
from .services.execution_service import ExecutionService
from .services.ai_service import AiCaseService, AiFailureAnalyzer
from .services.load_ai_analysis_service import LoadAiAnalysisService
from .services.load_report_service import LoadReportService
from .services.notification_service import NotificationNotConfiguredError, NotificationService
from .models.load_testing import ApiLoadRun
from .services.test_task_service import TestTaskService


settings = ApiTestingSettings.from_env()
logger = logging.getLogger(__name__)
celery_app = Celery("midscene-api-testing")
if settings.enabled:
    celery_app.conf.update(
        broker_url=settings.redis_url,
        result_backend=None,
        task_default_queue=settings.queue,
        task_ignore_result=True,
    )


def _heartbeat_redis():
    import redis

    return redis.Redis.from_url(settings.redis_url, decode_responses=True)


@heartbeat_sent.connect
@worker_ready.connect
def publish_worker_heartbeat(sender=None, **kwargs):
    try:
        _heartbeat_redis().set(
            settings.worker_heartbeat_key,
            "1",
            ex=settings.worker_heartbeat_ttl_seconds,
        )
        _resume_load_monitoring()
    except Exception:
        logger.warning(
            "Unable to publish API testing worker heartbeat",
            exc_info=True,
        )


_monitoring_resume_at = 0.0


def _resume_load_monitoring():
    """Recover dropped scheduling after worker/platform restarts; no business rerun."""
    global _monitoring_resume_at
    if time.monotonic() < _monitoring_resume_at:
        return
    _monitoring_resume_at = time.monotonic() + 30
    factory = _session_factory()
    with factory() as session:
        rows = session.scalars(select(ApiLoadRun).where(
            ApiLoadRun.state.in_(["starting", "running", "stopping", "finished", "failed", "cancelled"]),
            ApiLoadRun.configuration["monitoring"].is_not(None),
            ApiLoadRun.configuration.has_key("monitoring"),
            ApiLoadRun.summary["monitoring"]["terminal"].as_boolean().is_not(True),
        ).order_by(ApiLoadRun.created_at.asc()).limit(100)).all()
        pending = [r.id for r in rows if not ((r.summary or {}).get("monitoring") or {}).get("terminal")]
    for run_id in pending:
        collect_load_monitoring.delay(run_id)


@celery_app.task(name="api_testing.collect_load_monitoring", bind=True, acks_late=True, soft_time_limit=150, time_limit=180)
def collect_load_monitoring(self, run_id):
    from .services.load_monitoring_config_service import LoadMonitoringConfigService
    from .services.load_monitoring_collection_service import LoadMonitoringCollectionService
    redis = _heartbeat_redis()
    key, token = "api-testing:monitoring:" + str(run_id), uuid.uuid4().hex
    if not redis.set(key, token, nx=True, ex=190):
        return "busy"
    try:
        factory = _session_factory()
        result = LoadMonitoringCollectionService(factory, monitoring_service=LoadMonitoringConfigService(factory)).collect(run_id)
        if result.get("terminal"):
            finalize_load_run.delay(run_id)
        else:
            collect_load_monitoring.apply_async(args=[run_id], countdown=15)
        return result.get("state")
    finally:
        redis.eval("if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end", 1, key, token)


def _dispatch_failure_analysis(execution_id, child_id, attempt_id, evidence):
    analyze_api_failure.delay(execution_id, child_id, attempt_id, evidence)


def dispatch_load_analysis(analysis_id):
    analyze_load_report.delay(analysis_id)


@celery_app.task(name="api_testing.execute", bind=True, acks_late=True)
def execute_api_testing(self, execution_id):
    factory = _session_factory()
    redis_client = None
    try:
        import redis

        redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        redis_client = None
    event_stream = EventStream(factory, redis_client)
    result = ExecutionService(
        factory,
        event_stream=event_stream,
        failure_analysis_dispatcher=_dispatch_failure_analysis,
    ).run(execution_id)
    TestTaskService(factory).refresh_for_execution(execution_id)
    if result:
        _notify_execution_if_enabled(factory, event_stream, execution_id)
    return result


@celery_app.task(
    name="api_testing.recover_interrupted_execution",
    bind=True,
    acks_late=True,
)
def recover_interrupted_execution(self, execution_id, stale_before):
    factory = _session_factory()
    try:
        redis_client = _heartbeat_redis()
    except Exception:
        redis_client = None
    event_stream = EventStream(factory, redis_client)
    recovered = ExecutionService(
        factory,
        event_stream=event_stream,
    ).recover_interrupted(execution_id, datetime.fromisoformat(stale_before))
    if not recovered:
        return False
    TestTaskService(factory).refresh_for_execution(execution_id)
    _notify_execution_if_enabled(factory, event_stream, execution_id)
    return True


def _notify_execution_if_enabled(factory, event_stream, execution_id):
    with factory() as session:
        execution = ExecutionRepository(session).get_execution(execution_id)
        if execution is None or execution.state != "DONE":
            return
        if not _should_send_execution_notification(execution):
            return
        actor_id = execution.created_by
    try:
        result = NotificationService(factory).send_execution_report(execution_id, actor_id)
    except NotificationNotConfiguredError as error:
        event_stream.append(
            execution_id,
            "notification_failed",
            {"channel_type": "feishu", "message": str(error)},
        )
    except Exception:
        logger.warning(
            "Unable to send API testing Feishu report",
            exc_info=True,
        )
        event_stream.append(
            execution_id,
            "notification_failed",
            {"channel_type": "feishu", "message": "飞书通知发送失败"},
        )
    else:
        event_stream.append(
            execution_id,
            "notification_sent",
            {"channel_type": result.channel_type, "message": result.message},
        )


def _should_send_execution_notification(execution):
    snapshot = getattr(execution, "request_snapshot", {}) or {}
    task = snapshot.get("task", {}) if isinstance(snapshot, dict) else {}
    if not isinstance(task, dict):
        task = {}
    task_type = str(task.get("type") or "").strip()
    source = str(task.get("source") or "").strip()
    execution_type = str(getattr(execution, "execution_type", "") or "").strip()
    is_scheduled = (
        task_type == "scheduled_job"
        or source == "scheduled_job"
        or execution_type == "scheduled"
    )
    if is_scheduled:
        return task.get("notify_feishu") is True
    return execution_type == "baseline_regression"


@celery_app.task(name="api_testing.analyze_failure", bind=True, acks_late=True)
def analyze_api_failure(self, execution_id, child_id, attempt_id, evidence):
    factory = _session_factory()
    redis_client = None
    try:
        import redis

        redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        redis_client = None
    return ExecutionService(
        factory,
        event_stream=EventStream(factory, redis_client),
        failure_analyzer=AiFailureAnalyzer(),
    ).analyze_failure(execution_id, child_id, attempt_id, evidence)


@celery_app.task(name="api_testing.generate_cases", bind=True, acks_late=True)
def generate_api_cases(self, job_id):
    factory = _session_factory()
    result = AiCaseService(factory).process(job_id)
    TestTaskService(factory).refresh_for_ai_job(job_id)
    return result.state


@celery_app.task(name="api_testing.analyze_load_report", bind=True, acks_late=True)
def analyze_load_report(self, analysis_id):
    factory = _session_factory()
    record = LoadAiAnalysisService(factory).process(analysis_id)
    _notify_load_run_if_enabled(factory, record.run_id)
    return record.state


@celery_app.task(name="api_testing.finalize_load_run", bind=True, acks_late=True)
def finalize_load_run(self, run_id):
    """Freeze the deterministic verdict before optional AI and notification work."""
    factory = _session_factory()
    with factory() as session:
        run = session.get(ApiLoadRun, run_id)
        if run is None or run.state not in {"finished", "failed", "cancelled"}:
            return "ignored"
        actor_id = run.created_by
        if (run.configuration or {}).get("monitoring", {}).get("services") and not ((run.summary or {}).get("monitoring") or {}).get("terminal"):
            collect_load_monitoring.delay(run_id)
            return "monitoring_pending"
    report = LoadReportService(factory).build(run_id, actor_id)
    with factory.begin() as session:
        run = session.scalar(select(ApiLoadRun).where(ApiLoadRun.id == run_id).with_for_update())
        run.verdict = report["verdict"]
        run.summary = {
            **copy.deepcopy(run.summary or {}),
            "deterministic_report": {
                "verdict": report["verdict"],
                "evidence_complete": bool((report.get("evidence") or {}).get("complete")),
            },
        }
    try:
        analysis = LoadAiAnalysisService(factory).request(run_id, actor_id)
        analyze_load_report.delay(analysis.id)
        return analysis.state
    except Exception:
        logger.warning("Unable to queue load-test AI analysis", exc_info=True)
        _notify_load_run_if_enabled(factory, run_id, report=report)
        return "report_completed"


def _notify_load_run_if_enabled(factory, run_id, report=None):
    from .services.load_schedule_service import notify_schedule_run
    if notify_schedule_run(factory, run_id, report=report):
        return
    with factory() as session:
        run = session.get(ApiLoadRun, run_id)
        if run is None:
            return
        actor_id = run.created_by
    try:
        NotificationService(factory).send_load_test_report(run_id, actor_id, report=report)
    except NotificationNotConfiguredError:
        return
    except Exception:
        logger.warning("Unable to send performance-test Feishu report", exc_info=True)


@celery_app.task(name="api_testing.advance_load_schedule", soft_time_limit=80, time_limit=90)
def advance_load_schedule(schedule_id):
    from .services.load_schedule_service import LoadScheduleService
    LoadScheduleService(_session_factory()).advance(schedule_id)


@celery_app.task(name="api_testing.finalize_scheduled_load_run", soft_time_limit=150, time_limit=180)
def finalize_scheduled_load_run(run_id):
    from .models.load_schedule import ApiLoadScheduleOccurrence
    result = finalize_load_run.run(run_id)
    with _session_factory().begin() as session:
        occurrence = session.scalar(select(ApiLoadScheduleOccurrence).where(ApiLoadScheduleOccurrence.run_id == run_id).with_for_update())
        if occurrence is not None:
            occurrence.finalize_state = "pending" if result == "monitoring_pending" else "done"
    return result
