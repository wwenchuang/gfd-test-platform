#!/usr/bin/env python3
"""Focused checks for the platform-native API execution path."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from task_server.services import (
    api_execution_service,
    api_report_service,
    api_source_service,
    api_workspace_service,
)


class NativeApiExecutionChecks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="api_native_execution_checks_")
        self.old_source_dir = api_source_service.API_TESTING_DIR
        self.old_workspace_dir = api_workspace_service.API_TESTING_DIR
        self.old_execution_dir = api_execution_service.API_TESTING_DIR
        self.old_report_dir = api_report_service.API_TESTING_DIR
        api_source_service.API_TESTING_DIR = self.temp_dir
        api_workspace_service.API_TESTING_DIR = self.temp_dir
        api_execution_service.API_TESTING_DIR = self.temp_dir
        api_report_service.API_TESTING_DIR = self.temp_dir

    def tearDown(self):
        api_source_service.API_TESTING_DIR = self.old_source_dir
        api_workspace_service.API_TESTING_DIR = self.old_workspace_dir
        api_execution_service.API_TESTING_DIR = self.old_execution_dir
        api_report_service.API_TESTING_DIR = self.old_report_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _source(self):
        return api_source_service.save_api_source({
            "name": "3D",
            "source_type": "apifox",
            "project_id": "5904970",
            "environment_id": "APP测试环境",
            "access_token": "apifox-token",
            "provider_metadata": {
                "project_name": "3D业务",
                "environment_name": "APP测试环境",
            },
            "environment_snapshot": {
                "base_urls": [{"name": "APP测试环境", "url": "https://api.example.test"}],
                "variables": [
                    {"name": "publicFlag", "value": "1"},
                    {"name": "Authorization", "value": "secret", "sensitive": True},
                ],
            },
        })

    def test_source_snapshot_auto_creates_native_execution_context(self):
        source = self._source()

        context = api_execution_service.api_execution_context(source["source_id"])

        self.assertEqual("native_api", context["provider"])
        self.assertEqual("https://api.example.test", context["connection"]["base_url"])
        self.assertEqual("3D业务", context["businesses"][0]["name"])
        self.assertEqual("APP测试环境", context["environments"][0]["name"])
        self.assertEqual("platform", context["metadata"]["source"])

    def test_business_token_is_server_side_secret_with_public_fingerprint(self):
        source = self._source()
        public_auth = api_workspace_service.save_api_auth_binding_metadata(
            source["source_id"],
            auth_type="bearer",
            header_name="Authorization",
            secret_value="business-token",
        )

        self.assertTrue(public_auth["configured"])
        self.assertNotIn("secret", public_auth)
        binding = api_workspace_service.get_api_workspace_binding(source["source_id"])
        self.assertNotIn("secret", binding.get("auth_binding", {}))
        secret = api_workspace_service.get_api_auth_secret(source["source_id"])
        self.assertEqual("business-token", secret["secret"])

    def test_execution_report_contains_environment_and_failure_analysis(self):
        report = api_execution_service._execution_report({
            "execution_id": "api_execution_1",
            "run_id": "api_run_1",
            "plan_id": "api_plan_1",
            "source_id": "api_source_1",
            "base_url": "https://api.example.test",
            "duration_seconds": 3,
            "binding": {
                "binding_id": "binding_1",
                "config_fingerprint": "fp",
                "project_id": "5904970",
                "project_name": "3D业务",
                "environment_id": "APP测试环境",
                "environment_name": "APP测试环境",
                "auth_binding": {"variable_name": "MTP_API_AUTH_123"},
            },
            "results": [
                {
                    "case_id": "API-1",
                    "name": "获取用户信息",
                    "endpoint": "GET /users/me",
                    "status": "passed",
                    "duration_ms": 21,
                    "request": {"method": "GET", "url": "https://api.example.test/users/me"},
                    "response": {"status_code": 200},
                    "assertions": [{"type": "status", "passed": True, "message": "HTTP 200"}],
                },
                {
                    "case_id": "API-2",
                    "name": "创建模型",
                    "endpoint": "POST /models",
                    "status": "failed",
                    "duration_ms": 34,
                    "error": "HTTP 403 forbidden",
                    "request": {"method": "POST", "url": "https://api.example.test/models"},
                    "response": {"status_code": 403},
                    "assertions": [{"type": "status", "passed": False, "message": "HTTP 403"}],
                },
            ],
        })

        self.assertEqual("failed", report["status"])
        self.assertEqual(50.0, report["summary"]["pass_rate"])
        self.assertEqual("https://api.example.test", report["environment"]["base_url"])
        self.assertEqual({"AUTH_ISSUE": 1}, report["failure_analysis"]["by_type"])
        failed = report["results"][1]
        self.assertEqual("AUTH_ISSUE", failed["analysis"]["failure_type"])
        self.assertTrue(failed["analysis"]["evidence"])
        self.assertTrue(failed["analysis"]["suggestions"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
