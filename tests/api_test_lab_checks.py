#!/usr/bin/env python3
import hashlib
import os
import sqlite3
import tempfile
import unittest


_ROOT = tempfile.mkdtemp(prefix="api-test-lab-checks-")
os.environ["LEARNING_DIR"] = _ROOT
os.environ["API_TESTING_DIR"] = os.path.join(_ROOT, "api-testing")
os.environ["TEST_LAB_DIR"] = os.path.join(_ROOT, "test-lab")

from task_server.services import test_lab_service  # noqa: E402


def _favorite_openapi(auth_header="Authorization"):
    return {
        "openapi": "3.0.1",
        "info": {"title": "3D", "version": "1.0"},
        "paths": {
            "/print3d/api/v1/favorite/list": {
                "get": {
                    "summary": "我的收藏列表",
                    "tags": ["家用业务/app接口/我的/我的收藏"],
                    "parameters": [
                        {"name": auth_header, "in": "header", "required": True, "schema": {"type": "string"}},
                        {"name": "Biz", "in": "header", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            },
            "/print3d/api/v1/favorite/add": {
                "post": {
                    "summary": "添加收藏",
                    "tags": ["家用业务/app接口/我的/我的收藏"],
                    "parameters": [
                        {"name": auth_header, "in": "header", "required": True, "schema": {"type": "string"}},
                        {"name": "Biz", "in": "header", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            },
            "/print3d/api/v1/favorite/cancel": {
                "post": {
                    "summary": "取消收藏",
                    "tags": ["家用业务/app接口/我的/我的收藏"],
                    "parameters": [
                        {"name": auth_header, "in": "header", "required": True, "schema": {"type": "string"}},
                        {"name": "Biz", "in": "header", "required": True, "schema": {"type": "string"}},
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            },
        },
    }


class ApiTestLabChecks(unittest.TestCase):
    def test_openapi_import_persists_favorite_module_and_environment(self):
        imported = test_lab_service.import_openapi_to_test_lab({
            "name": "3D",
            "base_url": "https://print.wisebeginner3d.com/app",
            "document": _favorite_openapi(),
        })
        source_id = imported["state"]["source"]["source_id"]
        saved = test_lab_service.save_environment({
            "source_id": source_id,
            "base_url": "https://print.wisebeginner3d.com/app",
            "biz": "ZXB",
            "business_token": "token-for-unit-test",
        })
        state = test_lab_service.api_lab_state(source_id, "家用业务/app接口/我的/我的收藏")

        self.assertTrue(saved["ok"])
        self.assertTrue(state["db"]["ready"])
        self.assertEqual(state["endpoint_count"], 3)
        self.assertEqual(state["selected_endpoint_count"], 3)
        self.assertEqual(state["environment"]["base_urls"][0]["base_url"], "https://print.wisebeginner3d.com/app")
        self.assertTrue(any(item["module_path"] == "家用业务/app接口/我的/我的收藏" for item in state["modules"]))
        self.assertTrue(any(item["name"] == "Authorization" and item["configured"] and item["value_preview"] == "已配置" for item in state["environment"]["variables"]))
        self.assertTrue(any(item["name"] == "Biz" and item["value_preview"] == "ZXB" for item in state["environment"]["variables"]))

    def test_business_token_can_bind_project_specific_header(self):
        imported = test_lab_service.import_openapi_to_test_lab({
            "name": "3D ZXB",
            "base_url": "https://print.wisebeginner3d.com/app",
            "document": _favorite_openapi(auth_header="ZXBToken"),
        })
        source_id = imported["state"]["source"]["source_id"]
        imported_variables = imported["state"]["environment"]["variables"]

        self.assertTrue(any(item["name"] == "ZXBToken" and item["sensitive"] for item in imported_variables))
        self.assertTrue(any(item["name"] == "Biz" and not item["sensitive"] for item in imported_variables))

        saved = test_lab_service.save_environment({
            "source_id": source_id,
            "base_url": "https://print.wisebeginner3d.com/app",
            "biz": "ZXB",
            "auth_header_name": "ZXBToken",
            "business_token": "token-for-unit-test",
            "variables": [
                {"name": "ZXBManToken", "scope": "header", "value": "", "sensitive": True},
                {"name": "pageSize", "scope": "query", "value": "20"},
            ],
        })
        state = saved["state"]

        self.assertEqual(state["environment"]["auth"]["header_name"], "ZXBToken")
        self.assertEqual(state["environment"]["auth"]["auth_type"], "api_key")
        self.assertTrue(any(item["name"] == "ZXBToken" and item["configured"] and item["value_preview"] == "已配置" for item in state["environment"]["variables"]))
        self.assertTrue(any(item["name"] == "pageSize" and item["value_preview"] == "20" for item in state["environment"]["variables"]))
        self.assertFalse(any(item["name"].startswith("MTP_API_AUTH_") for item in state["environment"]["variables"]))

        preserved = test_lab_service.save_environment({
            "source_id": source_id,
            "base_url": "https://print.wisebeginner3d.com/app",
            "biz": "ZXB",
            "auth_header_name": "ZXBToken",
            "variables": [
                {"name": "ZXBToken", "scope": "header", "value": "", "sensitive": True, "configured": True},
                {"name": "pageSize", "scope": "query", "value": "50"},
            ],
        })["state"]
        self.assertTrue(any(item["name"] == "ZXBToken" and item["configured"] and item["value_preview"] == "已配置" for item in preserved["environment"]["variables"]))
        self.assertTrue(any(item["name"] == "pageSize" and item["value_preview"] == "50" for item in preserved["environment"]["variables"]))

        original_ai_flag = os.environ.get("API_TESTING_AI_ENABLED")
        os.environ["API_TESTING_AI_ENABLED"] = "0"
        try:
            generated = test_lab_service.generate_cases({
                "source_id": source_id,
                "module_path": "家用业务/app接口/我的/我的收藏",
            })
        finally:
            if original_ai_flag is None:
                os.environ.pop("API_TESTING_AI_ENABLED", None)
            else:
                os.environ["API_TESTING_AI_ENABLED"] = original_ai_flag
        positive_cases = [
            item for item in generated["plan"]["cases"]
            if item.get("type") == "positive"
        ]
        self.assertTrue(positive_cases)
        self.assertEqual(generated["plan"]["auth_binding"]["header_name"], "ZXBToken")
        self.assertTrue(all(item.get("request", {}).get("auth_ref") for item in positive_cases))

    def test_ui_yaml_index_uses_stable_content_hash(self):
        root = tempfile.mkdtemp(prefix="api-test-lab-yaml-")
        yaml_dir = os.path.join(root, "小白学习打印")
        os.makedirs(yaml_dir, exist_ok=True)
        yaml_path = os.path.join(yaml_dir, "我的收藏.yaml")
        content = "tasks:\n  - name: 我的收藏入口\n"
        with open(yaml_path, "w", encoding="utf-8") as file:
            file.write(content)

        indexed = test_lab_service.sync_ui_yaml_index(root)

        self.assertEqual(indexed["indexed"], 1)
        with sqlite3.connect(test_lab_service.TEST_LAB_DB_PATH) as conn:
            row = conn.execute(
                "SELECT content_hash FROM ui_yaml_cases WHERE file_name = ?",
                ("我的收藏.yaml",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], hashlib.sha256(content.encode("utf-8")).hexdigest())

    def test_state_prefers_saved_library_without_refreshing_source(self):
        imported = test_lab_service.import_openapi_to_test_lab({
            "name": "3D 本地库",
            "base_url": "https://print.wisebeginner3d.com/app",
            "document": _favorite_openapi(),
        })
        source_id = imported["state"]["source"]["source_id"]
        original = test_lab_service.mirror_existing_api_data

        def fail_if_called(*_args, **_kwargs):
            raise AssertionError("api_lab_state should read the saved test library without refreshing source data")

        test_lab_service.mirror_existing_api_data = fail_if_called
        try:
            state = test_lab_service.api_lab_state(source_id, "家用业务/app接口/我的/我的收藏")
        finally:
            test_lab_service.mirror_existing_api_data = original

        self.assertEqual(state["source"]["source_id"], source_id)
        self.assertEqual(state["selected_endpoint_count"], 3)


if __name__ == "__main__":
    unittest.main()
