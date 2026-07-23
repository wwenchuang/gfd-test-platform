#!/usr/bin/env python3
"""Restart recovery and source ownership checks for API testing runtimes."""

from __future__ import annotations

import inspect
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from task_server import app
from task_server.services import (
    api_plan_generation_service,
    api_report_service,
    api_test_plan_service,
    metersphere_service,
)


def generation_record(generation_id: str, status: str, batches):
    return {
        "generation_id": generation_id,
        "status": status,
        "created_at": "2026-07-23 10:00:00",
        "updated_at": "2026-07-23 10:00:00",
        "started_at": "2026-07-23 10:00:01" if status == "running" else "",
        "finished_at": "",
        "completed_batches": 0,
        "failed_batches": 0,
        "error": "",
        "events": [],
        "batches": batches,
    }


def execution_record(
    execution_id: str,
    status: str,
    *,
    current_phase: str = "push_cases",
    run_id: str = "",
):
    phases = metersphere_service._new_execution_phases()
    for phase in phases:
        if phase["id"] == current_phase:
            phase["state"] = "running" if status == "running" else "waiting"
            phase["started_at"] = (
                "2026-07-23 10:00:01" if status == "running" else ""
            )
    return {
        "execution_id": execution_id,
        "plan_id": f"plan-{execution_id}",
        "source_id": "source-a",
        "binding_id": "binding-a",
        "binding_fingerprint": "binding-fp-a",
        "connection_fingerprint": "connection-fp-a",
        "project_id": "project-a",
        "environment_id": "environment-a",
        "status": status,
        "current_phase": current_phase,
        "created_at": "2026-07-23 10:00:00",
        "started_at": "2026-07-23 10:00:01" if status == "running" else "",
        "updated_at": "2026-07-23 10:00:01",
        "finished_at": "",
        "run_id": run_id,
        "report_id": "",
        "remote_status": "running" if run_id else "waiting",
        "report_status": "waiting",
        "phases": phases,
        "events": [],
        "error": "",
    }


class ApiRuntimeRecoveryChecks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="api_runtime_recovery_checks_")
        self.old_generation_dir = api_plan_generation_service.API_TESTING_DIR
        self.old_metersphere_dir = metersphere_service.API_TESTING_DIR
        self.old_report_dir = api_report_service.API_TESTING_DIR
        api_plan_generation_service.API_TESTING_DIR = self.temp_dir
        metersphere_service.API_TESTING_DIR = self.temp_dir
        api_report_service.API_TESTING_DIR = self.temp_dir
        api_plan_generation_service._RUNNING_GENERATIONS.clear()

    def tearDown(self):
        api_plan_generation_service.API_TESTING_DIR = self.old_generation_dir
        metersphere_service.API_TESTING_DIR = self.old_metersphere_dir
        api_report_service.API_TESTING_DIR = self.old_report_dir
        api_plan_generation_service._RUNNING_GENERATIONS.clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_startup_registers_generation_and_execution_recovery(self):
        source = inspect.getsource(app.start_background_jobs)

        self.assertIn("recover_api_plan_generations", source)
        self.assertIn("recover_metersphere_executions", source)

    def test_queued_generation_restarts_worker(self):
        record = generation_record(
            "generation-queued",
            "queued",
            [{"batch_index": 1, "status": "queued", "plan_id": "", "error": ""}],
        )
        api_plan_generation_service._write_generation(record)
        spawned = []

        with mock.patch.object(
            api_plan_generation_service,
            "_spawn_generation_worker",
            side_effect=spawned.append,
        ):
            result = api_plan_generation_service.recover_api_plan_generations()

        self.assertEqual(["generation-queued"], spawned)
        self.assertEqual(1, result["resumed_queued"])
        self.assertEqual(
            "queued",
            api_plan_generation_service.get_api_plan_generation(
                "generation-queued"
            )["status"],
        )

    def test_running_generation_becomes_retryable_without_repeating_success(self):
        record = generation_record(
            "generation-running",
            "running",
            [
                {
                    "batch_index": 1,
                    "status": "succeeded",
                    "plan_id": "plan-success",
                    "error": "",
                },
                {
                    "batch_index": 2,
                    "status": "running",
                    "plan_id": "",
                    "error": "",
                },
                {
                    "batch_index": 3,
                    "status": "queued",
                    "plan_id": "",
                    "error": "",
                },
            ],
        )
        api_plan_generation_service._write_generation(record)

        result = api_plan_generation_service.recover_api_plan_generations()
        recovered = api_plan_generation_service.get_api_plan_generation(
            "generation-running"
        )

        self.assertEqual(1, result["interrupted_running"])
        self.assertEqual("partial", recovered["status"])
        self.assertEqual("succeeded", recovered["batches"][0]["status"])
        self.assertEqual("plan-success", recovered["batches"][0]["plan_id"])
        self.assertEqual(
            ["failed", "failed"],
            [item["status"] for item in recovered["batches"][1:]],
        )
        self.assertTrue(
            all(
                item["error_code"] == "restart_interrupted"
                for item in recovered["batches"][1:]
            )
        )

        retried = api_plan_generation_service.retry_api_plan_generation(
            "generation-running",
            spawn=False,
        )

        self.assertEqual("succeeded", retried["batches"][0]["status"])
        self.assertEqual("plan-success", retried["batches"][0]["plan_id"])
        self.assertEqual(
            ["queued", "queued"],
            [item["status"] for item in retried["batches"][1:]],
        )

    def test_queued_execution_restarts_worker(self):
        record = execution_record("execution-queued", "queued")
        metersphere_service._save_execution(record)
        spawned = []

        with mock.patch.object(
            metersphere_service,
            "_spawn_execution_worker",
            side_effect=spawned.append,
        ):
            result = metersphere_service.recover_metersphere_executions()

        self.assertEqual(["execution-queued"], spawned)
        self.assertEqual(1, result["resumed_queued"])

    def test_execution_with_remote_run_resumes_polling_only(self):
        record = execution_record(
            "execution-remote",
            "running",
            current_phase="metersphere_run",
            run_id="remote-run-1",
        )
        metersphere_service._save_execution(record)
        polled = []
        restarted = []

        with (
            mock.patch.object(
                metersphere_service,
                "_spawn_execution_poll_worker",
                side_effect=polled.append,
            ),
            mock.patch.object(
                metersphere_service,
                "_spawn_execution_worker",
                side_effect=restarted.append,
            ),
        ):
            result = metersphere_service.recover_metersphere_executions()

        self.assertEqual(["execution-remote"], polled)
        self.assertEqual([], restarted)
        self.assertEqual(1, result["resumed_remote"])
        self.assertEqual(
            "running",
            metersphere_service._load_execution("execution-remote")["status"],
        )

    def test_uncertain_execution_without_remote_run_fails_closed(self):
        record = execution_record(
            "execution-uncertain",
            "running",
            current_phase="trigger_plan",
        )
        metersphere_service._save_execution(record)

        result = metersphere_service.recover_metersphere_executions()
        recovered = metersphere_service._load_execution("execution-uncertain")

        self.assertEqual(1, result["interrupted_uncertain"])
        self.assertEqual("failed", recovered["status"])
        self.assertEqual("restart_interrupted", recovered["error_code"])
        self.assertEqual(
            {},
            metersphere_service._active_execution_for_plan(
                "plan-execution-uncertain"
            ),
        )


class ApiReportOwnershipChecks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="api_report_ownership_checks_")
        self.old_report_dir = api_report_service.API_TESTING_DIR
        api_report_service.API_TESTING_DIR = self.temp_dir

    def tearDown(self):
        api_report_service.API_TESTING_DIR = self.old_report_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_report_persists_execution_and_binding_context(self):
        report = api_report_service.normalize_metersphere_report(
            "remote-run-1",
            {"results": [{"id": "case-1", "status": "passed"}]},
            plan_id="plan-a",
            source_id="source-a",
            execution_id="execution-a",
            binding_id="binding-a",
            binding_fingerprint="binding-fp-a",
            project_id="project-a",
            environment_id="environment-a",
        )

        with mock.patch.object(
            api_test_plan_service,
            "get_api_test_plan",
            return_value={"plan_id": "plan-a", "source_id": "source-a"},
        ):
            saved = api_report_service.save_api_report(report)
            source_reports = api_report_service.list_api_reports(
                source_id="source-a"
            )

        self.assertEqual("source-a", saved["source_id"])
        self.assertEqual("execution-a", saved["execution_id"])
        self.assertEqual("binding-a", saved["binding_id"])
        self.assertEqual("binding-fp-a", saved["binding_fingerprint"])
        self.assertEqual("project-a", saved["project_id"])
        self.assertEqual("environment-a", saved["environment_id"])
        self.assertEqual("execution-a", source_reports[0]["execution_id"])
        self.assertEqual(
            {},
            api_report_service.get_api_report(
                saved["report_id"],
                source_id="source-b",
            ),
        )

    def test_legacy_report_is_visible_only_when_plan_source_is_unambiguous(self):
        legacy = api_report_service.normalize_metersphere_report(
            "legacy-run",
            {"results": []},
            plan_id="legacy-plan",
        )
        api_report_service.save_api_report(legacy)

        with mock.patch.object(
            api_test_plan_service,
            "get_api_test_plan",
            return_value={
                "plan_id": "legacy-plan",
                "source_id": "source-a",
                "source_id_derived": True,
            },
        ):
            self.assertEqual(
                1,
                len(api_report_service.list_api_reports(source_id="source-a")),
            )
            self.assertEqual(
                0,
                len(api_report_service.list_api_reports(source_id="source-b")),
            )

        with mock.patch.object(
            api_test_plan_service,
            "get_api_test_plan",
            return_value={},
        ):
            self.assertEqual(
                [],
                api_report_service.list_api_reports(source_id="source-a"),
            )
            self.assertEqual(
                {},
                api_report_service.get_api_report(
                    legacy["report_id"],
                    source_id="source-a",
                ),
            )

    def test_report_rejects_conflicting_plan_and_source_ownership(self):
        report = api_report_service.normalize_metersphere_report(
            "remote-run-conflict",
            {"results": []},
            plan_id="plan-b",
            source_id="source-a",
        )

        with mock.patch.object(
            api_test_plan_service,
            "get_api_test_plan",
            return_value={"plan_id": "plan-b", "source_id": "source-b"},
        ):
            with self.assertRaisesRegex(ValueError, "不属于"):
                api_report_service.save_api_report(report)

    def test_metersphere_pull_adds_execution_snapshot_to_report(self):
        execution = execution_record(
            "execution-report",
            "running",
            current_phase="sync_report",
            run_id="remote-run-report",
        )

        with mock.patch.object(
            api_test_plan_service,
            "get_api_test_plan",
            return_value={
                "plan_id": execution["plan_id"],
                "source_id": "source-a",
            },
        ):
            result = metersphere_service._pull_metersphere_report_with_config(
                "remote-run-report",
                {"results": [{"id": "case-1", "status": "passed"}]},
                {},
                request_config={},
                execution=execution,
            )

        self.assertTrue(result["ok"])
        report = result["report"]
        self.assertEqual("source-a", report["source_id"])
        self.assertEqual("execution-report", report["execution_id"])
        self.assertEqual("binding-a", report["binding_id"])
        self.assertEqual("project-a", report["project_id"])
        self.assertEqual("environment-a", report["environment_id"])

    def test_reports_route_forwards_source_filter(self):
        from task_server import router

        class Handler:
            def __init__(self):
                self.responses = []

            def _json(self, payload, status=200):
                self.responses.append((payload, status))

        calls = []
        route = router.GET_ROUTES["/api/api-testing/reports"]
        with mock.patch.object(
            api_report_service,
            "list_api_reports",
            side_effect=lambda limit, source_id="": calls.append(
                (limit, source_id)
            )
            or [],
        ):
            handler = Handler()
            route(handler, {"limit": "7", "source_id": "source-a"})

        self.assertEqual([(7, "source-a")], calls)
        self.assertEqual(({"ok": True, "reports": []}, 200), handler.responses[0])


if __name__ == "__main__":
    unittest.main()
