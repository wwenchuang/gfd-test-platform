"""Celery entry point for API execution; core behavior remains synchronously testable."""

from celery import Celery

from .config import ApiTestingSettings
from .db import _session_factory
from .events import EventStream
from .services.execution_service import ExecutionService
from .services.ai_service import AiCaseService


settings = ApiTestingSettings.from_env()
celery_app = Celery("midscene-api-testing")
if settings.enabled:
    celery_app.conf.update(
        broker_url=settings.redis_url,
        result_backend=None,
        task_default_queue=settings.queue,
        task_ignore_result=True,
    )


@celery_app.task(name="api_testing.execute", bind=True, acks_late=True)
def execute_api_testing(self, execution_id):
    factory = _session_factory()
    redis_client = None
    try:
        import redis

        redis_client = redis.Redis.from_url(settings.redis_url, decode_responses=True)
    except Exception:
        redis_client = None
    return ExecutionService(
        factory, event_stream=EventStream(factory, redis_client)
    ).run(execution_id)


@celery_app.task(name="api_testing.generate_cases", bind=True, acks_late=True)
def generate_api_cases(self, job_id):
    return AiCaseService(_session_factory()).process(job_id).state
