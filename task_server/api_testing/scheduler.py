"""Lightweight scheduler service for API testing scheduled jobs."""

import logging
import os
import signal
import time
from datetime import datetime, timedelta, timezone

from .config import ApiTestingSettings
from .db import _session_factory
from .services.execution_service import ExecutionService
from .services.scheduled_job_service import ScheduledJobService


logger = logging.getLogger(__name__)


def run_forever(interval_seconds=None):
    settings = ApiTestingSettings.from_env()
    interval = int(interval_seconds or os.getenv("API_TESTING_SCHEDULER_INTERVAL_SECONDS", "30"))
    stop = {"requested": False}

    def request_stop(_signum, _frame):
        stop["requested"] = True

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    logging.basicConfig(level=os.getenv("API_TESTING_SCHEDULER_LOG_LEVEL", "INFO"))
    logger.info("API testing scheduler started interval_seconds=%s enabled=%s", interval, settings.enabled)
    while not stop["requested"]:
        if settings.enabled:
            _scan_once(settings=settings)
        time.sleep(max(1, interval))
    logger.info("API testing scheduler stopped")


def _scan_once(*, settings=None):
    factory = _session_factory()
    current_settings = settings or ApiTestingSettings.from_env()
    _recover_interrupted_executions(factory, current_settings)
    from .services.load_schedule_service import LoadScheduleService
    from .tasks import advance_load_schedule
    for schedule_id in LoadScheduleService(factory).dispatch_due():
        advance_load_schedule.delay(schedule_id)
    dispatched = ScheduledJobService(factory, enqueue=_enqueue_execution).dispatch_due()
    if dispatched:
        logger.info("API testing scheduler dispatched due jobs count=%s", len(dispatched))
    else:
        logger.debug("API testing scheduler found no due jobs")


def _recover_interrupted_executions(
    factory,
    settings,
    *,
    now=None,
    enqueue=None,
):
    current_time = now or datetime.now(timezone.utc)
    stale_before = current_time - timedelta(
        seconds=settings.execution_stale_seconds
    )
    execution_ids = ExecutionService(factory).stale_running_execution_ids(
        stale_before
    )
    enqueue_recovery = enqueue or _enqueue_recovery
    cutoff = stale_before.isoformat()
    for execution_id in execution_ids:
        enqueue_recovery(execution_id, cutoff)
    if execution_ids:
        logger.warning(
            "API testing scheduler queued interrupted execution recovery count=%s",
            len(execution_ids),
        )
    return execution_ids


def _enqueue_execution(execution_id):
    from .tasks import execute_api_testing

    execute_api_testing.delay(execution_id)


def _enqueue_recovery(execution_id, stale_before):
    from .tasks import recover_interrupted_execution

    recover_interrupted_execution.delay(execution_id, stale_before)


if __name__ == "__main__":
    run_forever()
