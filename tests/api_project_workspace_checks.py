#!/usr/bin/env python3
"""Focused contracts for API project modules and scoped synchronization."""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from task_server.services import api_module_service, api_source_service, api_workspace_service, metersphere_service


def sample_document():
    def operation(folder):
        return {
            "x-apifox-folder": folder,
            "responses": {"200": {"description": "ok"}},
        }

    return {
        "openapi": "3.0.1",
        "info": {"title": "Project workspace"},
        "paths": {
            "/download": {"get": operation("家用业务/app接口/我的/我的下载")},
            "/favorites": {"get": operation("家用业务/app接口/我的/我的收藏")},
            "/nested": {"get": operation("A/B/C")},
            "/sibling": {"get": operation("A/BB")},
            "/root": {"get": operation("A/B")},
        },
    }


class ApiModuleScopeChecks(unittest.TestCase):
    def setUp(self):
        self.old_dir = api_source_service.API_TESTING_DIR
        self.old_workspace_dir = api_workspace_service.API_TESTING_DIR
        self.old_metersphere_dir = metersphere_service.API_TESTING_DIR
        self.temp_dir = tempfile.mkdtemp(prefix="api_project_workspace_checks_")
        api_source_service.API_TESTING_DIR = self.temp_dir
        api_workspace_service.API_TESTING_DIR = self.temp_dir
        metersphere_service.API_TESTING_DIR = self.temp_dir

    def tearDown(self):
        api_source_service.API_TESTING_DIR = self.old_dir
        api_workspace_service.API_TESTING_DIR = self.old_workspace_dir
        metersphere_service.API_TESTING_DIR = self.old_metersphere_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_parent_module_matches_descendants_but_not_prefix_siblings(self):
        filtered = api_module_service.filter_document(
            sample_document(),
            ["家用业务/app接口/我的"],
        )

        operations = api_module_service.module_catalog(filtered)

        self.assertEqual(
            [item["path"] for item in operations if item["endpoint_count"]],
            [
                "家用业务/app接口/我的/我的下载",
                "家用业务/app接口/我的/我的收藏",
            ],
        )

    def test_module_selection_honors_folder_boundaries(self):
        filtered = api_module_service.filter_document(sample_document(), ["A/B"])

        self.assertEqual(["/nested", "/root"], sorted(filtered["paths"]))

    def test_selected_scope_requires_a_module(self):
        with self.assertRaisesRegex(ValueError, "至少选择一个模块"):
            api_source_service.save_api_source({
                "name": "项目 B",
                "project_id": "2",
                "access_token": "secret",
                "sync_scope": {"mode": "selected", "module_paths": []},
            })

    def test_public_source_never_returns_token(self):
        source = api_source_service.save_api_source({
            "name": "项目 A",
            "project_id": "1",
            "access_token": "secret",
        })

        self.assertNotIn("access_token", source)
        self.assertTrue(source["credential_configured"])
        self.assertEqual("all", source["sync_scope"]["mode"])


class ApiWorkspaceBindingChecks(unittest.TestCase):
    def setUp(self):
        self.old_source_dir = api_source_service.API_TESTING_DIR
        self.old_workspace_dir = api_workspace_service.API_TESTING_DIR
        self.old_metersphere_dir = metersphere_service.API_TESTING_DIR
        self.temp_dir = tempfile.mkdtemp(prefix="api_workspace_binding_checks_")
        api_source_service.API_TESTING_DIR = self.temp_dir
        api_workspace_service.API_TESTING_DIR = self.temp_dir
        metersphere_service.API_TESTING_DIR = self.temp_dir
        metersphere_service.save_metersphere_config({
            "base_url": "http://metersphere.example.test",
            "auth_mode": "token",
            "token": "test-token",
            "workspace_id": "org-global",
            "project_id": "ms_project_legacy",
            "environment_id": "ms_env_legacy",
        })

    def tearDown(self):
        api_source_service.API_TESTING_DIR = self.old_source_dir
        api_workspace_service.API_TESTING_DIR = self.old_workspace_dir
        metersphere_service.API_TESTING_DIR = self.old_metersphere_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _create_sources(self, count):
        for suffix in ("a", "b")[:count]:
            api_source_service.save_api_source({
                "source_id": f"api_source_{suffix}",
                "name": f"项目 {suffix.upper()}",
                "project_id": f"apifox_{suffix}",
                "access_token": f"token-{suffix}",
            })

    def test_two_sources_keep_independent_metersphere_bindings(self):
        self._create_sources(2)
        first = api_workspace_service.save_api_workspace_binding(
            "api_source_a", "ms_project_a", "ms_env_a",
            project_name="A", environment_name="A测试",
        )
        second = api_workspace_service.save_api_workspace_binding(
            "api_source_b", "ms_project_b", "ms_env_b",
            project_name="B", environment_name="B测试",
        )

        self.assertNotEqual(first["binding_id"], second["binding_id"])
        self.assertEqual(
            api_workspace_service.get_api_workspace_binding("api_source_a")["project_id"],
            "ms_project_a",
        )
        self.assertNotIn("token", first)
        self.assertNotIn("access_key", first)
        self.assertNotEqual(first["config_fingerprint"], second["config_fingerprint"])

    def test_single_source_may_adopt_legacy_global_selection(self):
        self._create_sources(1)

        binding = api_workspace_service.get_api_workspace_binding(
            "api_source_a", allow_legacy=True
        )

        self.assertEqual(binding["project_id"], "ms_project_legacy")
        self.assertEqual(binding["environment_id"], "ms_env_legacy")
        self.assertTrue(binding["binding_id"].startswith("api_execution_binding_"))

    def test_second_source_never_inherits_legacy_global_selection(self):
        self._create_sources(2)

        self.assertEqual(
            api_workspace_service.get_api_workspace_binding(
                "api_source_b", allow_legacy=True
            ),
            {},
        )


if __name__ == "__main__":
    unittest.main()
