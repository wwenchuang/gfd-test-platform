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

from task_server.services import api_module_service, api_source_service


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
        self.temp_dir = tempfile.mkdtemp(prefix="api_project_workspace_checks_")
        api_source_service.API_TESTING_DIR = self.temp_dir

    def tearDown(self):
        api_source_service.API_TESTING_DIR = self.old_dir
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


if __name__ == "__main__":
    unittest.main()
