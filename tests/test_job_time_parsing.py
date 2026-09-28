"""Time parsing compatibility and canonical job-list hot-path regression tests."""
import os
import time
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from task_server.services import job_service


def legacy_parse(value):
    if not value:
        return 0.0
    try:
        return time.mktime(time.strptime(value, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0.0


class JobTimeParsingTests(unittest.TestCase):
    def test_canonical_history_does_not_enter_python_strptime_hot_loop(self):
        values = [(datetime(2026, 9, 24) + timedelta(seconds=i)).strftime("%Y-%m-%d %H:%M:%S")
                  for i in range(1000)]
        self.assertEqual(len(set(values)), 1000)
        expected = [legacy_parse(value) for value in values]
        with patch.object(job_service.time, "strptime", wraps=time.strptime) as slow_parse:
            actual = [job_service._parse_time(value) for value in values]
        self.assertEqual(actual, expected)
        self.assertEqual(slow_parse.call_count, 0)

    def test_accepted_legacy_and_invalid_values_retain_results(self):
        values = [None, "", 0, 1, True, [], {}, b"2026-09-24 00:00:00",
                  "2026-09-24 00:00:00", "2024-02-29 23:59:59", "0001-01-01 00:00:00",
                  "9999-12-31 23:59:59", "2026-9-4 1:2:3", "2026-09-24\t00:00:00",
                  "2026-09-24  00:00:00", "2026-09-24 23:59:60", "2026-09-24 23:59:61",
                  "2026-02-29 00:00:00", "2026-13-24 00:00:00", "2026-09-24 24:00:00",
                  "2026-09-24T00:00:00", "2026-09-24 00:00:00.0", "2026-09-24 00:00:00Z",
                  "2026-09-24 00:00:00\n", "２０２６-09-24 00:00:00"]
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(job_service._parse_time(value), legacy_parse(value))

    @unittest.skipUnless(hasattr(time, "tzset"), "requires local timezone control")
    def test_timezone_changes_dst_gap_and_fold_match_legacy_mktime(self):
        old_tz = os.environ.get("TZ")
        try:
            for tz in ("UTC0", "EST5EDT,M3.2.0/2,M11.1.0/2", "Asia/Shanghai"):
                os.environ["TZ"] = tz
                time.tzset()
                for value in ("2026-03-08 01:59:59", "2026-03-08 02:30:00",
                              "2026-03-08 03:00:00", "2026-11-01 01:30:00",
                              "2026-07-01 12:00:00", "2026-01-01 12:00:00"):
                    with self.subTest(tz=tz, value=value):
                        self.assertEqual(job_service._parse_time(value), legacy_parse(value))
        finally:
            if old_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = old_tz
            time.tzset()

    def test_history_sort_filter_and_truncation_match_legacy_keys(self):
        jobs = [{"job_id": str(i), "status": "success" if i % 2 else "failed", "created_at": value}
                for i, value in enumerate(("2026-09-24 00:00:03", "invalid", "2026-9-24 0:0:2",
                                           "2026-09-24 00:00:01", "2026-09-24 00:00:03"))]
        for status in (None, "success"):
            selected = [job for job in jobs if status is None or job["status"] == status]
            expected = sorted(selected, key=lambda job: legacy_parse(job["created_at"]), reverse=True)
            for limit in (None, 2, 0):
                with self.subTest(status=status, limit=limit), patch.object(job_service, "_read_jobs_raw", return_value=list(jobs)):
                    actual = job_service.load_jobs(limit=limit, status=status)
                    target = expected[:limit] if limit else expected
                    self.assertEqual([job["job_id"] for job in actual], [job["job_id"] for job in target])

    def test_timeout_threshold_and_invalid_start_fallback_retain_results(self):
        for started_at, created_at in (("2026-09-24 00:00:00", "invalid"),
                                       ("2026-9-24 0:0:0", "invalid"),
                                       ("invalid", "2026-09-24 00:00:00"),
                                       ("invalid", "invalid")):
            start = legacy_parse(started_at) or legacy_parse(created_at)
            job = {"status": "running", "started_at": started_at, "created_at": created_at}
            for delta in (-1, 0, 1):
                now = start + job_service.JOB_TIMEOUT_SECONDS + delta
                with self.subTest(started_at=started_at, created_at=created_at, delta=delta), \
                     patch.object(job_service.time, "time", return_value=now):
                    self.assertEqual(job_service.check_job_timeout(job), bool(start and delta > 0))
            job["status"] = "success"
            self.assertFalse(job_service.check_job_timeout(job))
