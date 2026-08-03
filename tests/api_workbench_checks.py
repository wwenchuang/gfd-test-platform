#!/usr/bin/env python3
"""Focused checks for the simplified API testing workbench facade."""

from __future__ import annotations

import json
import re
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

    def _generated_openapi(self, endpoint_count=25):
        return {
            "openapi": "3.0.1",
            "info": {"title": "3D 家用业务", "version": "1.0.0"},
            "paths": {
                f"/print3d/api/v1/generated/{index}": {
                    "get": {
                        "tags": ["家用业务/app接口/生成模块"],
                        "summary": f"生成接口 {index}",
                        "responses": {"200": {"description": "ok"}},
                    }
                }
                for index in range(endpoint_count)
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
        api_source_service.save_apifox_credential({
            "access_token": "secret-global-apifox-token",
            "base_url": "https://api.apifox.com",
        })

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
        self.assertEqual(2, workbench["metrics"]["total_endpoints"])
        self.assertEqual(1, workbench["metrics"]["covered_endpoints"])
        self.assertEqual(50, workbench["metrics"]["coverage_rate"])
        self.assertEqual(0, workbench["metrics"]["pending_changes"])
        self.assertEqual(0, workbench["metrics"]["today_executions"])
        self.assertEqual("API测试任务", workbench["task"]["kind"])
        self.assertEqual("3D 接口测试任务", workbench["task"]["name"])
        self.assertEqual("生产环境（新）-腾讯云", workbench["task"]["environment"]["name"])
        self.assertEqual("33831678", workbench["task"]["environment"]["id"])
        self.assertEqual(2, workbench["task"]["summary"]["interface_count"])
        self.assertEqual(1, workbench["task"]["summary"]["baseline_case_count"])
        self.assertIn(workbench["task"]["status"], {"ready", "running", "draft"})
        self.assertTrue(workbench["task"]["steps"])
        self.assertIn("自动回归", [step["title"] for step in workbench["task"]["steps"]])
        self.assertTrue(workbench["apifox_credential"]["credential_configured"])
        self.assertEqual("3D", workbench["sync_state"]["project"])
        self.assertEqual(2, workbench["sync_state"]["interface_count"])
        self.assertIn(workbench["sync_state"]["status"], {"待更新", "已就绪", "更新完成", "未连接"})
        self.assertEqual("手动更新 Apifox", workbench["sync_state"]["action_label"])
        self.assertIsInstance(workbench["pending_changes"], list)
        self.assertNotIn("刷新接口状态", text)
        self.assertNotIn("待同步", text)
        self.assertNotIn("secret-apifox-token", text)
        self.assertNotIn("secret-global-apifox-token", text)
        self.assertNotIn("secret-runtime-token", text)

    def test_workbench_missing_snapshot_uses_manual_update_copy(self):
        from task_server.services import api_workbench_service

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
            },
        })

        workbench = api_workbench_service.api_testing_workbench(source["source_id"])
        text = json.dumps(workbench, ensure_ascii=False)

        self.assertEqual("update_needed", workbench["task"]["status"])
        self.assertEqual("待更新", workbench["sync_state"]["status"])
        self.assertEqual("手动更新 Apifox", workbench["sync_state"]["action_label"])
        self.assertIn("先手动更新 Apifox 接口", text)
        self.assertNotIn("刷新接口状态", text)
        self.assertNotIn("待同步", text)

    def test_workbench_route_is_registered_and_requires_auth(self):
        from task_server import router

        self.assertIn("/api/api-testing/workbench", router.GET_ROUTES)
        self.assertIn("/api/api-testing/snapshots/update", router.POST_ROUTES)
        self.assertIn("/api/api-testing/cases/debug", router.POST_ROUTES)
        self.assertTrue(any(
            pattern.pattern == r"^/api/api-testing/sources/([^/]+)/environment-snapshot$"
            for pattern, _fn in router._POST_REGEX_ROUTES
        ))

        denied = _RouteHandler(authorized=False)
        router.GET_ROUTES["/api/api-testing/workbench"](denied, {})
        self.assertEqual(401, denied.responses[-1][0])

        denied_update = _RouteHandler(authorized=False)
        router.POST_ROUTES["/api/api-testing/snapshots/update"](denied_update, {})
        self.assertEqual(401, denied_update.responses[-1][0])

        denied_debug = _RouteHandler(authorized=False)
        router.POST_ROUTES["/api/api-testing/cases/debug"](denied_debug, {})
        self.assertEqual(401, denied_debug.responses[-1][0])

        self.assertIn("/api/api-testing/cases/debug-batch", router.POST_ROUTES)
        denied_batch_debug = _RouteHandler(authorized=False)
        router.POST_ROUTES["/api/api-testing/cases/debug-batch"](denied_batch_debug, {})
        self.assertEqual(401, denied_batch_debug.responses[-1][0])

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

    def test_confirm_route_requires_successful_batch_debug_for_draft_plan(self):
        from task_server import router

        source, _revision, draft, _confirmed = self._seed_workbench()
        confirm_handler = _RouteHandler({"plan_id": draft["plan_id"]})
        router.POST_ROUTES["/api/api-testing/plans/confirm"](confirm_handler, {})

        status, payload = confirm_handler.responses[-1]
        self.assertEqual(400, status)
        self.assertIn("批量调试", payload["error"])

        original_execute_case = api_execution_service._execute_case

        def fake_execute_case(_source_id, _base_url, case):
            return {
                "case_id": case["case_id"],
                "name": case["name"],
                "endpoint": case["endpoint"],
                "status": "passed",
                "duration_ms": 7,
                "request": {"method": "GET", "url": "https://api.example.test/users/me"},
                "response": {"status_code": 200, "body": {"ok": True}},
                "assertions": [{"type": "status", "passed": True, "message": "HTTP 200"}],
            }

        api_execution_service._execute_case = fake_execute_case
        try:
            case_ids = [case["case_id"] for case in draft["cases"] if (case.get("readiness") or {}).get("state") == "executable"]
            execution = api_execution_service.start_api_cases_debug(
                draft["plan_id"],
                case_ids,
                spawn=False,
            )
        finally:
            api_execution_service._execute_case = original_execute_case

        self.assertEqual("succeeded", execution["status"])

        confirm_after_debug = _RouteHandler({"plan_id": draft["plan_id"]})
        router.POST_ROUTES["/api/api-testing/plans/confirm"](confirm_after_debug, {})

        confirmed_status, confirmed_payload = confirm_after_debug.responses[-1]
        self.assertEqual(200, confirmed_status)
        self.assertEqual("confirmed", confirmed_payload["plan"]["status"])

    def test_source_environment_snapshot_route_updates_local_execution_copy(self):
        from task_server import router

        source, _revision, _plan, _confirmed = self._seed_workbench()
        route = next(
            fn for pattern, fn in router._POST_REGEX_ROUTES
            if pattern.pattern == r"^/api/api-testing/sources/([^/]+)/environment-snapshot$"
        )
        handler = _RouteHandler({
            "environment_snapshot": {
                "base_urls": [
                    {"name": "APP测试环境", "url": "https://api.example.test/app"},
                ],
                "variables": [
                    {"name": "tenant", "value": "3d", "scope": "environment"},
                    {"name": "Authorization", "value": "Bearer secret", "scope": "environment"},
                ],
            }
        })

        route(
            handler,
            {},
            re.match(
                r"^/api/api-testing/sources/([^/]+)/environment-snapshot$",
                f"/api/api-testing/sources/{source['source_id']}/environment-snapshot",
            ),
        )

        status, payload = handler.responses[-1]
        self.assertEqual(200, status)
        self.assertTrue(payload["ok"])
        snapshot = payload["source"]["environment_snapshot"]
        self.assertEqual(
            [{"name": "APP测试环境", "url": "https://api.example.test/app"}],
            snapshot["base_urls"],
        )
        self.assertEqual("tenant", snapshot["variables"][0]["name"])
        self.assertEqual("3d", snapshot["variables"][0]["value"])
        self.assertEqual("Authorization", snapshot["variables"][1]["name"])
        self.assertEqual("", snapshot["variables"][1]["value"])
        self.assertTrue(snapshot["variables"][1]["sensitive"])

        raw = api_source_service.get_api_source(source["source_id"], masked=False)
        self.assertEqual(
            "https://api.example.test/app",
            raw["environment_snapshot"]["base_urls"][0]["url"],
        )

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

    def test_workbench_keeps_saved_environment_snapshot_without_auto_refreshing_placeholders(self):
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
                        "variables": [
                            {"name": "Authorization", "value": "", "sensitive": True, "scope": "header"},
                            {"name": "Biz", "value": "ZXB", "scope": "header"},
                            {"name": "ZXBToken", "value": "", "sensitive": True, "scope": "body"},
                        ],
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
                "environment_snapshot": {
                    "base_urls": [{"name": "default", "url": "https://print.wisebeginner3d.com/app"}],
                    "variables": [
                        {"name": "cookie", "value": ""},
                        {"name": "query", "value": ""},
                        {"name": "header", "value": ""},
                        {"name": "body", "value": ""},
                    ],
                },
            })

            workbench = api_workbench_service.api_testing_workbench(source["source_id"])
            variable_names = [
                item["name"]
                for item in workbench["source"]["environment_snapshot"]["variables"]
            ]

            self.assertEqual(0, len(calls))
            self.assertEqual(["cookie", "query", "header", "body"], variable_names)
            refreshed = api_workbench_service.refresh_apifox_environment_snapshot(
                source["source_id"],
                force=True,
            )
            refreshed_names = [
                item["name"]
                for item in refreshed["environment_snapshot"]["variables"]
            ]
            self.assertEqual(1, len(calls))
            self.assertEqual(["Authorization", "Biz", "ZXBToken"], refreshed_names)
            self.assertEqual(
                ["Authorization", "Biz", "ZXBToken"],
                [
                    item["name"]
                    for item in api_source_service.get_api_source(source["source_id"], masked=True)["environment_snapshot"]["variables"]
                ],
            )
        finally:
            apifox_discovery_service.discover_project_context = old_discover

    def test_plan_generation_detail_includes_generated_plan_summaries(self):
        source = api_source_service.save_api_source({
            "source_type": "apifox",
            "name": "3D",
            "project_id": "5904970",
            "environment_id": "33831678",
            "access_token": "secret-apifox-token",
            "sync_enabled": False,
        })
        api_workspace_service.save_api_workspace_binding(
            source["source_id"],
            "5904970",
            "33831678",
            project_name="3D",
            environment_name="生产环境（新）-腾讯云",
            connection_identity="platform-native-api",
        )
        staged = api_asset_service.stage_api_revision(
            source["source_id"],
            "3D",
            self._generated_openapi(1),
            source_type="apifox",
        )
        api_asset_service.activate_api_revision(
            staged["asset_id"],
            staged["revision_id"],
        )
        endpoint_id = staged["revision"]["endpoints"][0]["endpoint_id"]
        generation = api_plan_generation_service.start_api_plan_generation(
            source["source_id"],
            staged["revision_id"],
            [endpoint_id],
            [],
            spawn=False,
        )
        old_run_ai_skill = api_test_plan_service.run_ai_skill

        def fake_run_ai_skill(_skill_name, payload, **kwargs):
            kwargs["runtime_trace"].update({"model": "qwen3.7-plus"})
            return {
                "cases": [{
                    "case_id": "API-AI-001",
                    "endpoint_id": payload["endpoints"][0]["endpoint_id"],
                    "name": "AI 生成成功用例",
                    "type": "positive",
                    "steps": ["发送请求"],
                }],
                "review": {},
            }

        api_test_plan_service.run_ai_skill = fake_run_ai_skill
        try:
            completed = api_plan_generation_service.run_api_plan_generation(
                generation["generation_id"],
            )
        finally:
            api_test_plan_service.run_ai_skill = old_run_ai_skill

        plan_id = completed["batches"][0]["plan_id"]
        current = api_plan_generation_service.get_api_plan_generation(
            generation["generation_id"],
        )

        self.assertEqual("succeeded", current["status"])
        self.assertEqual([plan_id], [item["plan_id"] for item in current["plans"]])
        self.assertEqual("draft", current["plans"][0]["status"])
        self.assertEqual(1, current["plans"][0]["case_count"])
        self.assertEqual(1, current["plans"][0]["endpoint_count"])

    def test_plan_generation_summary_tolerates_legacy_non_numeric_counts(self):
        old_get_plan = api_test_plan_service.get_api_test_plan

        def fake_get_plan(_plan_id, source_id=""):
            return {
                "plan_id": "api_plan_legacy",
                "name": "旧计划",
                "status": "draft",
                "endpoint_count": "-",
                "case_count": "unknown",
                "executable_case_count": "",
                "needs_review_case_count": None,
                "module_paths": ["我的收藏"],
                "selected_endpoint_keys": ["api_1"],
            }

        api_test_plan_service.get_api_test_plan = fake_get_plan
        try:
            record = {
                "generation_id": "api_plan_generation_legacy",
                "source_id": "api_source_legacy",
                "batches": [{"plan_id": "api_plan_legacy"}],
            }
            current = api_plan_generation_service._attach_plan_summaries(record)
        finally:
            api_test_plan_service.get_api_test_plan = old_get_plan

        self.assertEqual(0, current["plans"][0]["endpoint_count"])
        self.assertEqual(0, current["plans"][0]["case_count"])

    def test_workbench_module_summary_uses_server_counts_and_endpoint_ids(self):
        from task_server.services import api_workbench_service

        source, revision, _plan, _confirmed = self._seed_workbench()

        workbench = api_workbench_service.api_testing_workbench(source["source_id"])
        modules = workbench["scope"]["modules"]["roots"]
        household = next(item for item in modules if item["path"] == "家用业务")

        self.assertEqual(2, household["endpoint_count"])
        self.assertEqual(
            sorted(item["endpoint_id"] for item in revision["endpoints"]),
            sorted(household["endpoint_ids"]),
        )
        self.assertLessEqual(len(household["endpoint_ids"]), 60)

    def test_stale_running_batch_becomes_retryable_without_losing_successes(self):
        source = api_source_service.save_api_source({
            "source_type": "apifox",
            "name": "3D",
            "project_id": "5904970",
            "access_token": "secret-apifox-token",
            "sync_enabled": False,
        })
        api_workspace_service.save_api_workspace_binding(
            source["source_id"],
            "api_project_3d",
            "api_env_dev",
        )
        staged = api_asset_service.stage_api_revision(
            source["source_id"],
            "3D",
            self._generated_openapi(25),
            source_type="apifox",
        )
        api_asset_service.activate_api_revision(
            staged["asset_id"],
            staged["revision_id"],
        )
        endpoint_ids = [
            item["endpoint_id"]
            for item in staged["revision"]["endpoints"]
        ]
        generation = api_plan_generation_service.start_api_plan_generation(
            source["source_id"],
            staged["revision_id"],
            endpoint_ids,
            ["家用业务/app接口"],
            spawn=False,
        )
        record = api_plan_generation_service.get_api_plan_generation(
            generation["generation_id"]
        )
        record["status"] = "running"
        record["running_batch_timeout_seconds"] = 300
        record["started_at"] = "2026-07-30 10:00:00"
        record["updated_at"] = "2026-07-30 10:01:00"
        record["batches"][0]["status"] = "succeeded"
        record["batches"][0]["plan_id"] = "existing-plan-1"
        record["batches"][0]["started_at"] = "2026-07-30 10:00:00"
        record["batches"][0]["finished_at"] = "2026-07-30 10:01:00"
        record["batches"][1]["status"] = "running"
        record["batches"][1]["started_at"] = "2026-07-30 10:01:00"
        record["batches"][2]["status"] = "queued"
        api_plan_generation_service._write_generation(record)
        original_now = api_plan_generation_service._now
        api_plan_generation_service._now = lambda: "2026-07-30 10:20:00"
        try:
            recovered = api_plan_generation_service.get_api_plan_generation(
                generation["generation_id"]
            )
        finally:
            api_plan_generation_service._now = original_now

        self.assertEqual("partial", recovered["status"])
        self.assertEqual("succeeded", recovered["batches"][0]["status"])
        self.assertEqual("existing-plan-1", recovered["batches"][0]["plan_id"])
        self.assertEqual("failed", recovered["batches"][1]["status"])
        self.assertTrue(recovered["batches"][1]["recoverable"])
        self.assertEqual("queued", recovered["batches"][2]["status"])
        self.assertIn("超时", recovered["batches"][1]["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
