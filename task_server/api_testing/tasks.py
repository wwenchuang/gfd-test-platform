"""Celery entry point for API execution; core behavior remains synchronously testable."""

from celery import Celery

from .config import ApiTestingSettings
from .db import _session_factory
from .events import EventStream
from .services.execution_service import ExecutionService
from .services.ai_service import AiCaseService, AiFailureAnalyzer
from .services.test_task_service import TestTaskService


settings = ApiTestingSettings.from_env()
celery_app = Celery("midscene-api-testing")
if settings.enabled:
    celery_app.conf.update(
        broker_url=settings.redis_url,
        result_backend=None,
        task_default_queue=settings.queue,
        task_ignore_result=True,
    )


def _dispatch_failure_analysis(execution_id, child_id, attempt_id, evidence):
    analyze_api_failure.delay(execution_id, child_id, attempt_id, evidence)


@celery_app.task(name="api_testing.execute", bind=True, acks_late=True)
def execute_api_testing(self, execution_id):
    factory = _session_factory()
    redis_client = None
    try:
        import redis

        redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        redis_client = None
    result = ExecutionService(
        factory,
        event_stream=EventStream(factory, redis_client),
        failure_analysis_dispatcher=_dispatch_failure_analysis,
    ).run(execution_id)
    TestTaskService(factory).refresh_for_execution(execution_id)
    return result


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
