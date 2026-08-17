"""Lightweight scheduler service for API testing scheduled jobs."""

import logging
import os
import signal
import time

from .config import ApiTestingSettings
from .db import _session_factory
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
            _scan_once()
        time.sleep(max(1, interval))
    logger.info("API testing scheduler stopped")


def _scan_once():
    factory = _session_factory()
    dispatched = ScheduledJobService(factory, enqueue=_enqueue_execution).dispatch_due()
    if dispatched:
        logger.info("API testing scheduler dispatched due jobs count=%s", len(dispatched))
    else:
        logger.debug("API testing scheduler found no due jobs")


def _enqueue_execution(execution_id):
    from .tasks import execute_api_testing

    execute_api_testing.delay(execution_id)


if __name__ == "__main__":
    run_forever()
