"""Celery entry point for API execution; core behavior remains synchronously testable."""

import logging
import copy

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
    except Exception:
        logger.warning(
            "Unable to publish API testing worker heartbeat",
            exc_info=True,
        )


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
