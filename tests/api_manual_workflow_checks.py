#!/usr/bin/env python3
"""Contracts for the simplified native API testing workflow."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from task_server.services import api_source_service, api_workspace_service


class ApiManualSourceWorkflowChecks(unittest.TestCase):
    def setUp(self):
        self.old_source_dir = api_source_service.API_TESTING_DIR
        self.old_workspace_dir = api_workspace_service.API_TESTING_DIR
        self.temp_dir = tempfile.mkdtemp(prefix="api_manual_workflow_checks_")
        api_source_service.API_TESTING_DIR = self.temp_dir
        api_workspace_service.API_TESTING_DIR = self.temp_dir

    def tearDown(self):
        api_source_service.API_TESTING_DIR = self.old_source_dir
        api_workspace_service.API_TESTING_DIR = self.old_workspace_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_source(self):
        return api_source_service.save_api_source({
            "source_id": "api_source_a",
            "name": "3D",
            "project_id": "5904970",
            "access_token": "apifox-token",
            "provider_metadata": {
                "project_name": "3D",
                "environment_name": "生产环境（新）-腾讯云",
                "discovery_source": "apifox_cli",
            },
        })

    def test_apifox_sources_default_to_manual_updates(self):
        source = self._create_source()

        self.assertFalse(source["sync_enabled"])
        self.assertEqual("manual", source["sync_schedule"]["mode"])
        self.assertEqual("", source["sync_schedule"]["next_check_at"])

    def test_public_source_masks_historical_auto_sync_when_global_auto_is_disabled(self):
        previous = os.environ.pop("APIFOX_AUTO_SYNC_ENABLED", None)
        try:
            source = api_source_service._public_source({
                "source_type": "apifox",
                "name": "3D",
                "project_id": "5904970",
                "access_token": "apifox-token",
                "sync_enabled": True,
                "sync_interval_minutes": 60,
                "last_success_at": "2026-08-03 18:33:02",
                "last_sync_status": "succeeded",
            })
        finally:
            if previous is not None:
                os.environ["APIFOX_AUTO_SYNC_ENABLED"] = previous

        self.assertFalse(source["sync_enabled"])
        self.assertEqual("manual", source["sync_schedule"]["mode"])
        self.assertEqual("", source["sync_schedule"]["next_check_at"])

    def test_auth_binding_route_accepts_value_token_and_rejects_empty_secret(self):
        from task_server import router

        self._create_source()
        api_workspace_service.save_api_workspace_binding(
            "api_source_a",
            "5904970",
            "33831678",
            project_name="3D",
            environment_name="生产环境（新）-腾讯云",
            connection_identity="platform-native-api",
        )

        class Handler:
            def __init__(self, body):
                self.body = body
                self.responses = []

            def _authorized(self):
                return True

            def _body(self):
                return self.body

            def _json(self, payload, status=200):
                self.responses.append((payload, status))

        pattern = r"^/api/api-testing/sources/([^/]+)/auth-binding$"
        post = next(fn for matcher, fn in router._POST_REGEX_ROUTES if matcher.pattern == pattern)
        match = re.match(pattern, "/api/api-testing/sources/api_source_a/auth-binding")

        empty = Handler({"auth_type": "bearer", "header_name": "Authorization", "value": " "})
        post(empty, {}, match)
        self.assertEqual(400, empty.responses[0][1])
        self.assertIn("token", empty.responses[0][0]["error"].lower())

        configured = Handler({"auth_type": "bearer", "header_name": "Authorization", "value": "runtime-token"})
        post(configured, {}, match)

        self.assertEqual(200, configured.responses[0][1])
        self.assertTrue(configured.responses[0][0]["binding"]["configured"])
        self.assertNotIn("runtime-token", json.dumps(configured.responses, ensure_ascii=False))
        self.assertEqual(
            "runtime-token",
            api_workspace_service.get_api_auth_secret("api_source_a")["secret"],
        )


if __name__ == "__main__":
    unittest.main()
