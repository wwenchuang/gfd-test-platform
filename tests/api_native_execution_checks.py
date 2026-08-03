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
    api_asset_service,
    api_execution_service,
    api_report_service,
    api_source_service,
    api_test_plan_service,
    api_workspace_service,
)


class NativeApiExecutionChecks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="api_native_execution_checks_")
        self.old_source_dir = api_source_service.API_TESTING_DIR
        self.old_workspace_dir = api_workspace_service.API_TESTING_DIR
        self.old_execution_dir = api_execution_service.API_TESTING_DIR
        self.old_report_dir = api_report_service.API_TESTING_DIR
        self.old_plan_dir = api_test_plan_service.API_TESTING_DIR
        self.old_asset_dir = api_asset_service.API_TESTING_DIR
        api_source_service.API_TESTING_DIR = self.temp_dir
        api_workspace_service.API_TESTING_DIR = self.temp_dir
        api_execution_service.API_TESTING_DIR = self.temp_dir
        api_report_service.API_TESTING_DIR = self.temp_dir
        api_test_plan_service.API_TESTING_DIR = self.temp_dir
        api_asset_service.API_TESTING_DIR = self.temp_dir

    def tearDown(self):
        api_source_service.API_TESTING_DIR = self.old_source_dir
        api_workspace_service.API_TESTING_DIR = self.old_workspace_dir
        api_execution_service.API_TESTING_DIR = self.old_execution_dir
        api_report_service.API_TESTING_DIR = self.old_report_dir
        api_test_plan_service.API_TESTING_DIR = self.old_plan_dir
        api_asset_service.API_TESTING_DIR = self.old_asset_dir
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

    def test_single_apifox_default_base_url_uses_environment_name_and_id(self):
        source = api_source_service.save_api_source({
            "name": "3D",
            "source_type": "apifox",
            "project_id": "5904970",
            "environment_id": "33831678",
            "access_token": "apifox-token",
            "provider_metadata": {
                "project_name": "3D业务",
                "environment_name": "生产环境（新）-腾讯云",
            },
            "environment_snapshot": {
                "base_urls": [{"name": "default", "url": "https://print.wisebeginner3d.com/app"}],
                "variables": [],
            },
        })
        api_workspace_service.save_api_workspace_binding(
            source["source_id"],
            "5904970",
            "default",
            project_name="3D业务",
            environment_name="default",
            connection_identity="platform-native-api",
        )

        context = api_execution_service.api_execution_context(source["source_id"])

        self.assertEqual("33831678", context["environments"][0]["id"])
        self.assertEqual("生产环境（新）-腾讯云", context["environments"][0]["name"])
        self.assertEqual("https://print.wisebeginner3d.com/app", context["connection"]["base_url"])
        self.assertEqual("33831678", context["selection"]["environment_id"])

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

    def test_execution_events_show_request_response_assert_and_report_without_secrets(self):
        source = self._source()
        api_workspace_service.save_api_auth_binding_metadata(
            source["source_id"],
            auth_type="bearer",
            header_name="Authorization",
            secret_value="source-secret",
        )
        plan = {
            "plan_id": "api_plan_local",
            "name": "本地执行日志计划",
            "source_id": source["source_id"],
        }
        case = {
            "case_id": "API-LOG-1",
            "name": "查询当前用户",
            "endpoint": "GET /users/me",
            "request": {
                "method": "GET",
                "path": "/users/me",
                "auth_ref": "MTP_API_AUTH_TEST",
            },
            "assertions": [{"type": "status", "expected": 200}],
        }
        original_execute_case = api_execution_service._execute_case

        def fake_execute_case(_source_id, _base_url, _case):
            return {
                "case_id": "API-LOG-1",
                "name": "查询当前用户",
                "endpoint": "GET /users/me",
                "status": "passed",
                "duration_ms": 12,
                "request": {
                    "method": "GET",
                    "url": "https://api.example.test/users/me",
                    "headers": {"Authorization": "Bearer source-secret"},
                },
                "response": {"status_code": 200, "body": {"ok": True}},
                "assertions": [{"type": "status", "passed": True, "message": "HTTP 200"}],
            }

        api_execution_service._execute_case = fake_execute_case
        try:
            execution_id = "api_execution_log_test"
            execution = {
                "execution_id": execution_id,
                "run_id": "api_run_log_test",
                "run_mode": "baseline",
                "provider": "native_api",
                "plan_id": plan["plan_id"],
                "plan_name": plan["name"],
                "source_id": source["source_id"],
                "status": "queued",
                "report_status": "pending",
                "current_phase": "prepare",
                "created_at": "2026-07-31 09:00:00",
                "updated_at": "2026-07-31 09:00:00",
                "duration_seconds": 0,
                "stats": {"total": 1, "passed": 0, "failed": 0, "completed": 0},
                "phases": api_execution_service._phases(),
                "events": [],
                "cases": [case],
                "plan_snapshot": plan,
                "poll_after_ms": 1500,
            }
            api_execution_service._save_execution(execution)
            api_execution_service._run_execution(execution_id)
            current = api_execution_service.get_api_execution(execution_id)
        finally:
            api_execution_service._execute_case = original_execute_case

        summaries = [event.get("summary", "") for event in current.get("events", [])]
        self.assertTrue(any("发送请求" in summary for summary in summaries))
        self.assertTrue(any("收到响应" in summary for summary in summaries))
        self.assertTrue(any("断言通过" in summary for summary in summaries))
        self.assertTrue(any("生成报告" in summary for summary in summaries))
        text = str(current)
        self.assertNotIn("source-secret", text)
        self.assertIn("Bearer ***", text)

    def test_positive_case_business_code_failure_is_not_counted_as_pass(self):
        rows = api_execution_service._assertions(
            {
                "case_id": "API-BIZ-1",
                "name": "我的收藏列表-成功响应",
                "type": "positive",
                "assertions": [
                    {"type": "status", "operator": "in", "expected": [200]},
                    {"type": "schema", "schema_ref": "response:2xx"},
                ],
            },
            200,
            {"code": 4009, "msg": "用户未登录！", "data": None},
            "",
        )

        self.assertTrue(any(row["type"] == "status" and row["passed"] for row in rows))
        business = next(row for row in rows if row["type"] == "business_code")
        self.assertFalse(business["passed"])
        self.assertIn("4009", business["message"])
        self.assertIn("用户未登录", business["message"])

    def test_auth_case_can_assert_unauthorized_business_code_without_success_gate(self):
        rows = api_execution_service._assertions(
            {
                "case_id": "API-AUTH-1",
                "name": "我的收藏列表-未授权访问校验",
                "type": "auth",
                "assertions": [
                    {"type": "status", "operator": "in", "expected": [200, 401, 403]},
                ],
            },
            200,
            {"code": 4009, "msg": "用户未登录！"},
            "",
        )

        self.assertFalse(any(row["type"] == "business_code" for row in rows))
        self.assertTrue(all(row["passed"] for row in rows))

    def test_execution_resolves_local_environment_variables_before_request(self):
        source = api_source_service.save_api_source({
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
                    {"name": "Biz", "value": "ZXB", "scope": "header"},
                    {"name": "userId", "value": "135", "scope": "path"},
                    {"name": "Authorization", "value": "Bearer runtime-token", "scope": "header", "sensitive": True},
                    {"name": "ZXBToken", "value": "jwt-runtime-token", "scope": "body", "sensitive": True},
                ],
            },
        })
        plan = {
            "plan_id": "api_plan_env_vars",
            "name": "环境变量调试计划",
            "source_id": source["source_id"],
        }
        case = {
            "case_id": "API-ENV-1",
            "name": "更新用户资料",
            "endpoint": "POST /users/{userId}",
            "request": {
                "method": "POST",
                "path": "/users/{{userId}}",
                "query": {"biz": "{{Biz}}"},
                "headers": {
                    "Authorization": "{{Authorization}}",
                    "X-Biz": "{{Biz}}",
                    "X-ZXB-Token": "{{ZXBToken}}",
                },
                "body": {"biz": "{{Biz}}", "token": "{{ZXBToken}}"},
            },
            "assertions": [{"type": "status", "expected": 200}],
        }
        original_execute_case = api_execution_service._execute_case
        captured = {}

        def fake_execute_case(_source_id, _base_url, _case):
            captured["case"] = _case
            request = _case["request"]
            self.assertEqual("/users/135", request["path"])
            self.assertEqual({"biz": "ZXB"}, request["query"])
            self.assertEqual("Bearer runtime-token", request["headers"]["Authorization"])
            self.assertEqual("ZXB", request["headers"]["X-Biz"])
            self.assertEqual("jwt-runtime-token", request["headers"]["X-ZXB-Token"])
            self.assertEqual({"biz": "ZXB", "token": "jwt-runtime-token"}, request["body"])
            return {
                "case_id": "API-ENV-1",
                "name": "更新用户资料",
                "endpoint": "POST /users/{userId}",
                "status": "passed",
                "duration_ms": 15,
                "request": {
                    "method": "POST",
                    "url": "https://api.example.test/users/135?biz=ZXB",
                    "headers": request["headers"],
                    "body": request["body"],
                },
                "response": {"status_code": 200, "body": {"ok": True}},
                "assertions": [{"type": "status", "passed": True, "message": "HTTP 200"}],
            }

        api_execution_service._execute_case = fake_execute_case
        try:
            execution_id = "api_execution_env_vars"
            execution = {
                "execution_id": execution_id,
                "run_id": "api_run_env_vars",
                "run_mode": "debug_batch",
                "provider": "native_api",
                "plan_id": plan["plan_id"],
                "plan_name": plan["name"],
                "source_id": source["source_id"],
                "status": "queued",
                "report_status": "pending",
                "current_phase": "prepare",
                "created_at": "2026-08-01 10:00:00",
                "updated_at": "2026-08-01 10:00:00",
                "duration_seconds": 0,
                "stats": {"total": 1, "passed": 0, "failed": 0, "completed": 0},
                "phases": api_execution_service._phases(),
                "events": [],
                "cases": [case],
                "plan_snapshot": plan,
                "poll_after_ms": 1500,
            }
            api_execution_service._save_execution(execution)
            api_execution_service._run_execution(execution_id)
            current = api_execution_service.get_api_execution(execution_id)
        finally:
            api_execution_service._execute_case = original_execute_case

        self.assertIn("case", captured)
        self.assertEqual("succeeded", current["status"])
        self.assertNotIn("runtime-token", str(current))
        self.assertNotIn("jwt-runtime-token", str(current))
        self.assertIn("Bearer ***", str(current))

    def test_batch_debug_records_plan_validation_summary(self):
        source = self._source()
        staged = api_asset_service.stage_api_revision(source["source_id"], "3D", {
            "openapi": "3.0.1",
            "info": {"title": "3D", "version": "1.0.0"},
            "paths": {
                "/print3d/api/v1/users/me": {
                    "get": {
                        "tags": ["家用业务/app接口/我的/我的设置"],
                        "summary": "我的资料",
                        "responses": {"200": {"description": "ok"}},
                    }
                },
                "/print3d/api/v1/region/provinces": {
                    "get": {
                        "tags": ["家用业务/app接口/我的/我的设置"],
                        "summary": "省接口",
                        "responses": {"200": {"description": "ok"}},
                    }
                },
            },
        }, source_type="apifox")
        api_asset_service.activate_api_revision(staged["asset_id"], staged["revision_id"])
        revision = api_asset_service.get_api_revision(staged["revision_id"])
        endpoint_ids = [endpoint["endpoint_id"] for endpoint in revision["endpoints"]]
        plan = api_test_plan_service.generate_api_test_plan(
            revision["snapshot_id"],
            endpoint_ids,
            use_ai=False,
            source_id=source["source_id"],
        )
        case_ids = [case["case_id"] for case in plan["cases"][:2]]
        original_execute_case = api_execution_service._execute_case

        def fake_execute_case(_source_id, _base_url, case):
            return {
                "case_id": case["case_id"],
                "name": case["name"],
                "endpoint": case["endpoint"],
                "status": "passed",
                "duration_ms": 9,
                "request": {"method": "GET", "url": "https://api.example.test/demo"},
                "response": {"status_code": 200, "body": {"ok": True}},
                "assertions": [{"type": "status", "passed": True, "message": "HTTP 200"}],
            }

        api_execution_service._execute_case = fake_execute_case
        try:
            execution = api_execution_service.start_api_cases_debug(
                plan["plan_id"],
                case_ids,
                spawn=False,
            )
        finally:
            api_execution_service._execute_case = original_execute_case

        self.assertEqual("debug_batch", execution["run_mode"])
        self.assertEqual("succeeded", execution["status"])
        self.assertEqual({"total": 2, "passed": 2, "failed": 0, "completed": 2}, execution["stats"])
        updated = api_test_plan_service.get_api_test_plan(plan["plan_id"], source_id=source["source_id"])
        debug_validation = updated["debug_validation"]
        self.assertEqual("passed", debug_validation["state"])
        self.assertEqual(case_ids, debug_validation["case_ids"])
        self.assertEqual(execution["execution_id"], debug_validation["execution_id"])
        self.assertTrue(updated["execution_readiness"]["debug_passed"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
