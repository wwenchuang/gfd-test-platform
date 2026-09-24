import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from task_server import router
from task_server.services import agent_service, job_service


class _Handler:
    def __init__(self, body):
        self.body = body
        self.response = None

    def _body(self):
        return self.body

    def _json(self, payload, status=200):
        self.response = (status, payload)


class JobLifecycleRegressions(unittest.TestCase):
    def test_agent_cancel_does_not_replace_a_child_result_completed_after_snapshot(self):
        with tempfile.TemporaryDirectory(prefix="agent-cancel-race-test-") as directory:
            jobs_file = Path(directory) / "jobs.json"
            jobs_file.write_text(json.dumps([{
                "job_id": "job-racing", "parent_run_id": "agent-racing", "status": "running",
            }]), encoding="utf-8")
            real_load = job_service.load_jobs
            real_update = job_service.update_job

            def complete_after_snapshot(limit=None):
                snapshot = real_load(limit=limit)
                real_update("job-racing", {"status": "success"})
                return snapshot

            with patch.object(job_service, "JOBS_FILE", str(jobs_file)), \
                 patch.object(job_service, "load_jobs", side_effect=complete_after_snapshot):
                cancelled = agent_service._agent_cancel_runner_jobs("agent-racing")
            saved = json.loads(jobs_file.read_text(encoding="utf-8"))
            self.assertEqual(cancelled, [])
            self.assertEqual(saved[0]["status"], "success")

    def test_job_cancel_response_explains_runner_may_continue(self):
        with tempfile.TemporaryDirectory(prefix="job-cancel-notice-test-") as directory:
            jobs_file = Path(directory) / "jobs.json"
            jobs_file.write_text(json.dumps([{"job_id": "job-running", "status": "running", "module": "module", "file": "test.yaml"}]), encoding="utf-8")
            handler = _Handler({"reason": "manual"})
            match = re.match(r"(job-running)", "job-running")
            with patch.object(job_service, "JOBS_FILE", str(jobs_file)), \
                 patch.object(router, "update_task_meta"):
                router._post_job_cancel(handler, {}, match)
            self.assertEqual(handler.response[1]["job"]["status"], "cancelled")
            self.assertIn("可能", handler.response[1]["execution_stop_notice"])

    def test_agent_cancel_response_explains_runner_may_continue(self):
        handler = _Handler({"reason": "manual"})
        match = re.match(r"(agent-running)", "agent-running")
        with patch.object(router, "cancel_agent_run", return_value={"runId": "agent-running", "status": "CANCELLED"}):
            router._post_agent_runs_cancel(handler, {}, match)
        self.assertIn("可能", handler.response[1]["execution_stop_notice"])

    def test_runner_result_keeps_history_beyond_default_list_page(self):
        with tempfile.TemporaryDirectory(prefix="job-history-test-") as directory:
            jobs_file = Path(directory) / "jobs.json"
            jobs_file.write_text(json.dumps([
                {"job_id": f"job-{index:03d}", "status": "success", "created_at": f"2026-09-24 10:{index // 60:02d}:{index % 60:02d}"}
                for index in range(50)
            ] + [{"job_id": "job-050", "status": "running", "created_at": "2026-09-24 10:00:50"}]), encoding="utf-8")
            handler = _Handler({"status": "success"})
            with patch.object(job_service, "JOBS_FILE", str(jobs_file)), \
                 patch.object(router, "LEARNING_DIR", directory), \
                 patch.object(router, "REPORT_DIR", directory), \
                 patch.object(router, "_require_user_auth", return_value=False), \
                 patch.object(router, "update_task_meta"):
                router._handle_runner_job_result(handler, "job-050")
            saved = json.loads(jobs_file.read_text(encoding="utf-8"))
            self.assertEqual(handler.response[0], 200)
            self.assertEqual(len(saved), 51)
            self.assertTrue(any(job["job_id"] == "job-000" for job in saved))

    def test_cancelled_runner_job_ignores_late_success_result(self):
        with tempfile.TemporaryDirectory(prefix="job-cancel-test-") as directory:
            jobs_file = Path(directory) / "jobs.json"
            jobs_file.write_text(json.dumps([{"job_id": "job-cancelled", "status": "cancelled", "cancel_reason": "用户取消"}]), encoding="utf-8")
            handler = _Handler({"status": "success", "stdout": "late output"})
            with patch.object(job_service, "JOBS_FILE", str(jobs_file)), \
                 patch.object(router, "LEARNING_DIR", directory), \
                 patch.object(router, "REPORT_DIR", directory), \
                 patch.object(router, "_require_user_auth", return_value=False), \
                 patch.object(router, "update_task_meta") as update_meta:
                router._handle_runner_job_result(handler, "job-cancelled")
            saved = json.loads(jobs_file.read_text(encoding="utf-8"))
            self.assertEqual(saved[0]["status"], "cancelled")
            self.assertEqual(handler.response[1]["status"], "cancelled")
            update_meta.assert_not_called()

    def test_cancelled_runner_job_skips_late_report_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="job-cancel-report-test-") as directory:
            jobs_file = Path(directory) / "jobs.json"
            jobs_file.write_text(json.dumps([{"job_id": "job-cancelled", "status": "cancelled"}]), encoding="utf-8")
            handler = _Handler({"status": "success", "stdout": "late output", "report_html": "late report"})
            with patch.object(job_service, "JOBS_FILE", str(jobs_file)), \
                 patch.object(router, "LEARNING_DIR", directory), \
                 patch.object(router, "REPORT_DIR", directory), \
                 patch.object(router, "_require_user_auth", return_value=False):
                router._handle_runner_job_result(handler, "job-cancelled")
            self.assertFalse((Path(directory) / "runs" / "job-cancelled" / "stdout.log").exists())
            self.assertFalse((Path(directory) / "job-cancelled.html").exists())

    def test_cancelled_sonic_job_ignores_late_success_result(self):
        with tempfile.TemporaryDirectory(prefix="sonic-cancel-test-") as directory:
            jobs_file = Path(directory) / "jobs.json"
            jobs_file.write_text(json.dumps([{"job_id": "sonic-cancelled", "status": "cancelled", "module": "module", "file": "test.yaml"}]), encoding="utf-8")
            handler = _Handler({"job_id": "sonic-cancelled", "module": "module", "file": "test.yaml", "app_package": "com.example", "status": "success"})
            with patch.object(job_service, "JOBS_FILE", str(jobs_file)), \
                 patch.object(router, "LEARNING_DIR", directory), \
                 patch.object(router, "_require_user_auth", return_value=False), \
                 patch.object(router, "sonic_suite_app_info", return_value={}), \
                 patch.object(router, "update_task_meta") as update_meta, \
                 patch.object(router, "register_sonic_suite_result", return_value="") as register_suite, \
                 patch.object(router, "start_sonic_result_post_actions") as post_actions:
                router._post_sonic_result(handler, {})
            saved = json.loads(jobs_file.read_text(encoding="utf-8"))
            self.assertEqual(saved[0]["status"], "cancelled")
            self.assertEqual(handler.response[1]["status"], "cancelled")
            update_meta.assert_not_called()
            register_suite.assert_not_called()
            post_actions.assert_not_called()
            self.assertFalse((Path(directory) / "runs" / "sonic-cancelled").exists())

    def test_runner_cancel_after_report_write_skips_downstream_processing(self):
        with tempfile.TemporaryDirectory(prefix="job-cancel-race-test-") as directory:
            jobs_file = Path(directory) / "jobs.json"
            jobs_file.write_text(json.dumps([{"job_id": "job-racing", "status": "running", "module": "module", "file": "test.yaml"}]), encoding="utf-8")
            handler = _Handler({"status": "success", "stdout": "result", "report_html": "late report"})
            real_write = router.write_text_file

            def cancel_during_write(path, content):
                real_write(path, content)
                if str(path).endswith("job-racing.html"):
                    job_service.update_job("job-racing", {"status": "cancelled"})

            with patch.object(job_service, "JOBS_FILE", str(jobs_file)), \
                 patch.object(router, "LEARNING_DIR", directory), \
                 patch.object(router, "REPORT_DIR", directory), \
                 patch.object(router, "_require_user_auth", return_value=False), \
                 patch.object(router, "write_text_file", side_effect=cancel_during_write), \
                 patch.object(router, "update_task_meta") as update_meta:
                router._handle_runner_job_result(handler, "job-racing")
            saved = json.loads(jobs_file.read_text(encoding="utf-8"))
            self.assertEqual(saved[0]["status"], "cancelled")
            self.assertEqual(handler.response[1]["status"], "cancelled")
            update_meta.assert_not_called()
            self.assertTrue((Path(directory) / "job-racing.html").exists())


if __name__ == "__main__":
    unittest.main()
