"""Lightweight scheduler service placeholder for API testing scheduled jobs."""

import logging
import os
import signal
import time

from sqlalchemy import select

from .config import ApiTestingSettings
from .db import _session_factory
from .models.scheduled_job import ApiScheduledJob


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
    with factory() as session:
        enabled_count = session.scalar(
            select(ApiScheduledJob)
            .where(ApiScheduledJob.enabled.is_(True))
            .limit(1)
            .exists()
            .select()
        )
    if enabled_count:
        logger.debug("API testing scheduler found enabled jobs; due-time dispatch is reserved")


if __name__ == "__main__":
    run_forever()
