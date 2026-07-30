#!/usr/bin/env python3
"""Focused checks for the simplified API testing workbench facade."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from task_server.services import (
    apifox_discovery_service,
    api_asset_service,
    api_execution_service,
    api_plan_generation_service,
    api_report_service,
    api_source_service,
    api_sync_service,
    api_test_plan_service,
    api_workspace_service,
)


class _RouteHandler:
    def __init__(self, body=None, authorized=True):
        self.body = body or {}
        self.authorized = authorized
        self.responses = []

    def _authorized(self):
        return self.authorized

    def _body(self):
        return self.body

    def _json(self, payload, status=200):
        self.responses.append((status, payload))


class ApiWorkbenchChecks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="api_workbench_checks_")
        self.services = [
            api_asset_service,
            api_execution_service,
            api_plan_generation_service,
            api_report_service,
            api_source_service,
            api_sync_service,
            api_test_plan_service,
            api_workspace_service,
        ]
        self.old_dirs = {
            service: service.API_TESTING_DIR
            for service in self.services
            if hasattr(service, "API_TESTING_DIR")
        }
        for service in self.services:
            if hasattr(service, "API_TESTING_DIR"):
                service.API_TESTING_DIR = self.temp_dir

    def tearDown(self):
        for service, old_dir in self.old_dirs.items():
            service.API_TESTING_DIR = old_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _openapi(self):
        return {
            "openapi": "3.0.1",
            "info": {"title": "3D 家用业务", "version": "1.0.0"},
            "paths": {
                "/print3d/api/v1/users/me": {
                    "get": {
                        "tags": ["家用业务/app接口/我的/我的设置"],
                        "summary": "我的资料",
                        "responses": {"200": {"description": "ok"}},
                    }
                },
                "/print3d/api/v1/auth/appleLogin": {
                    "post": {
                        "tags": ["家用业务/app接口/登陆注册"],
                        "summary": "苹果授权登录",
                        "requestBody": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "required": ["code", "phone"],
                                        "properties": {
                                            "code": {"type": "string"},
                                            "phone": {"type": "string"},
                                        },
                                    }
                                }
                            }
                        },
                        "responses": {"200": {"description": "ok"}},
                    }
                },
            },
        }

    def _seed_workbench(self):
        source = api_source_service.save_api_source({
            "source_type": "apifox",
            "name": "3D",
            "project_id": "5904970",
            "environment_id": "33831678",
            "access_token": "secret-apifox-token",
            "provider_metadata": {
                "project_name": "3D",
                "environment_name": "生产环境（新）-腾讯云",
            },
            "environment_snapshot": {
                "base_urls": [{"name": "default", "url": "https://print.wisebeginner3d.com/app"}],
                "variables": [{"name": "Authorization", "value": "secret-runtime-token"}],
            },
            "sync_enabled": False,
        })
        staged = api_asset_service.stage_api_revision(
            source["source_id"],
            "3D",
            self._openapi(),
            source_type="apifox",
        )
        api_asset_service.activate_api_revision(staged["asset_id"], staged["revision_id"])
        revision = api_asset_service.get_api_revision(staged["revision_id"])
        endpoint_ids = [item["endpoint_id"] for item in revision["endpoints"][:1]]
        draft = api_test_plan_service.generate_api_test_plan(
            revision["snapshot_id"],
            endpoint_ids,
            use_ai=False,
            source_id=source["source_id"],
        )
        baseline = api_test_plan_service.generate_api_test_plan(
            revision["snapshot_id"],
            endpoint_ids,
            use_ai=False,
            source_id=source["source_id"],
        )
        confirmed = api_test_plan_service.confirm_api_test_plan(baseline["plan_id"])
        return source, revision, draft, confirmed

    def test_workbench_facade_returns_simplified_payload_without_secrets(self):
        from task_server.services import api_workbench_service

        source, revision, plan, confirmed = self._seed_workbench()

        workbench = api_workbench_service.api_testing_workbench(source["source_id"])
        text = json.dumps(workbench, ensure_ascii=False)

        self.assertTrue(workbench["ok"])
        self.assertEqual(source["source_id"], workbench["source"]["source_id"])
        self.assertEqual("3D", workbench["source"]["name"])
        self.assertEqual(revision["snapshot_id"], workbench["snapshot"]["snapshot_id"])
        self.assertEqual(2, workbench["snapshot"]["endpoint_count"])
        self.assertEqual(2, workbench["scope"]["endpoint_count"])
        self.assertTrue(workbench["scope"]["modules"]["roots"])
        self.assertEqual(plan["plan_id"], workbench["cases"]["drafts"][0]["plan_id"])
        self.assertEqual(confirmed["plan_id"], workbench["cases"]["baselines"][0]["plan_id"])
        self.assertEqual("ready", workbench["execution"]["readiness"]["state"])
        self.assertNotIn("secret-apifox-token", text)
        self.assertNotIn("secret-runtime-token", text)

    def test_workbench_route_is_registered_and_requires_auth(self):
        from task_server import router

        self.assertIn("/api/api-testing/workbench", router.GET_ROUTES)
        self.assertIn("/api/api-testing/snapshots/update", router.POST_ROUTES)
        self.assertIn("/api/api-testing/cases/debug", router.POST_ROUTES)

        denied = _RouteHandler(authorized=False)
        router.GET_ROUTES["/api/api-testing/workbench"](denied, {})
        self.assertEqual(401, denied.responses[-1][0])

        denied_update = _RouteHandler(authorized=False)
        router.POST_ROUTES["/api/api-testing/snapshots/update"](denied_update, {})
        self.assertEqual(401, denied_update.responses[-1][0])

        denied_debug = _RouteHandler(authorized=False)
        router.POST_ROUTES["/api/api-testing/cases/debug"](denied_debug, {})
        self.assertEqual(401, denied_debug.responses[-1][0])

        source, _revision, _plan, _confirmed = self._seed_workbench()
        allowed = _RouteHandler()
        router.GET_ROUTES["/api/api-testing/workbench"](
            allowed,
            {"source_id": source["source_id"]},
        )
        status, payload = allowed.responses[-1]
        self.assertEqual(200, status)
        self.assertTrue(payload["ok"])
        self.assertEqual(source["source_id"], payload["source"]["source_id"])

    def test_workbench_persists_apifox_environment_snapshot_once(self):
        from task_server.services import api_workbench_service

        old_discover = apifox_discovery_service.discover_project_context
        calls = []

        def fake_discover(access_token, project_id, **kwargs):
            calls.append({
                "access_token": access_token,
                "project_id": project_id,
                "preferred_environment_id": kwargs.get("preferred_environment_id"),
            })
            return {
                "project": {"id": project_id, "name": "3D", "team": {"id": "team-1", "name": "功夫豆"}},
                "branches": [{"id": "", "name": "主分支（默认）", "is_default": True}],
                "environments": [{
                    "id": "33831678",
                    "name": "生产环境（新）-腾讯云",
                    "environment_snapshot": {
                        "base_urls": [{"name": "default", "url": "https://print.wisebeginner3d.com/app"}],
                        "variables": [{"name": "Authorization", "value": "secret-token"}],
                    },
                }],
            }

        apifox_discovery_service.discover_project_context = fake_discover
        try:
            source = api_source_service.save_api_source({
                "source_type": "apifox",
                "name": "3D",
                "project_id": "5904970",
                "environment_id": "33831678",
                "access_token": "secret-apifox-token",
                "sync_enabled": False,
                "environment_snapshot": {},
            })

            first = api_workbench_service.api_testing_workbench(source["source_id"])
            raw = api_source_service.get_api_source(source["source_id"], masked=False)
            second = api_workbench_service.api_testing_workbench(source["source_id"])

            self.assertEqual(1, len(calls))
            self.assertEqual("https://print.wisebeginner3d.com/app", raw["environment_snapshot"]["base_urls"][0]["url"])
            self.assertEqual("生产环境（新）-腾讯云", raw["provider_metadata"]["environment_name"])
            self.assertEqual("3D", first["source"]["project_name"])
            self.assertEqual("3D", second["source"]["project_name"])
            self.assertEqual(
                "https://print.wisebeginner3d.com/app",
                second["source"]["environment_snapshot"]["base_urls"][0]["url"],
            )
            self.assertNotIn("secret-apifox-token", json.dumps(second, ensure_ascii=False))
            self.assertNotIn("secret-token", json.dumps(second, ensure_ascii=False))
        finally:
            apifox_discovery_service.discover_project_context = old_discover


if __name__ == "__main__":
    unittest.main(verbosity=2)
