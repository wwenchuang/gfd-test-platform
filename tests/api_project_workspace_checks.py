#!/usr/bin/env python3
"""Focused contracts for API project modules and scoped synchronization."""

from __future__ import annotations

import json
import re
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

    def test_auth_secret_is_forwarded_but_never_persisted(self):
        self._create_sources(1)
        api_workspace_service.save_api_workspace_binding(
            "api_source_a", "ms_project_a", "ms_env_a",
        )

        class Adapter:
            def __init__(self):
                self.calls = []

            def upsert_environment_variable(self, environment_id, key, value, description):
                self.calls.append((environment_id, key, value, description))
                return {"ok": True, "configured": True, "environment_id": environment_id, "variable_name": key}

            def delete_environment_variable(self, environment_id, key):
                return {"ok": True, "configured": False, "environment_id": environment_id, "variable_name": key}

        adapter = Adapter()
        old_probe = metersphere_service._v365_adapter_probe
        metersphere_service._v365_adapter_probe = lambda config: (adapter, {"version": "v3.6.5-lts"}, True)
        try:
            result = metersphere_service.save_api_auth_binding(
                "api_source_a", "bearer", "Authorization", "runtime-secret",
            )
            cleared = metersphere_service.clear_api_auth_binding("api_source_a")
        finally:
            metersphere_service._v365_adapter_probe = old_probe

        self.assertTrue(result["configured"])
        self.assertEqual(adapter.calls[0][0], "ms_env_a")
        self.assertEqual(adapter.calls[0][2], "runtime-secret")
        self.assertEqual(result["header_name"], "Authorization")
        self.assertFalse(cleared["configured"])
        binding = api_workspace_service.get_api_workspace_binding("api_source_a", allow_legacy=False)
        self.assertNotIn("auth_binding", binding)
        self.assertNotIn("runtime-secret", json.dumps(binding, ensure_ascii=False))
        local_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in Path(self.temp_dir).rglob("*")
            if path.is_file()
        )
        self.assertNotIn("runtime-secret", local_text)

    def test_workspace_binding_public_read_filters_unexpected_auth_secret_field(self):
        self._create_sources(1)
        api_workspace_service.save_api_workspace_binding(
            "api_source_a", "ms_project_a", "ms_env_a",
        )
        api_workspace_service.save_api_auth_binding_metadata(
            "api_source_a",
            auth_type="bearer",
            header_name="Authorization",
        )
        path = Path(api_workspace_service._binding_path("api_source_a"))
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["auth_binding"]["secret"] = "unexpected-secret"
        path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

        public_binding = api_workspace_service.get_api_workspace_binding("api_source_a", allow_legacy=False)

        self.assertNotIn("unexpected-secret", json.dumps(public_binding, ensure_ascii=False))

    def test_api_key_header_requires_rfc_tchar_in_service_and_metadata(self):
        self._create_sources(1)
        api_workspace_service.save_api_workspace_binding(
            "api_source_a", "ms_project_a", "ms_env_a",
        )

        for invalid in (
            "X API Key",
            "X-API-Key:Injected",
            "X-API-Key\r\nInjected",
            "X-API-密钥",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "HTTP field-name"):
                    api_workspace_service.normalize_api_auth_header("api_key", invalid)
                with self.assertRaisesRegex(ValueError, "HTTP field-name"):
                    api_workspace_service.save_api_auth_binding_metadata(
                        "api_source_a",
                        auth_type="api_key",
                        header_name=invalid,
                    )

        normalized_type, normalized_header = api_workspace_service.normalize_api_auth_header(
            "api_key",
            "X-Auth!#$%&'*+.^_`|~-09",
        )
        self.assertEqual(normalized_type, "api_key")
        self.assertEqual(normalized_header, "X-Auth!#$%&'*+.^_`|~-09")


class ApiWorkspaceRouteAuthChecks(unittest.TestCase):
    def setUp(self):
        self.old_source_dir = api_source_service.API_TESTING_DIR
        self.temp_dir = tempfile.mkdtemp(prefix="api_workspace_route_auth_checks_")
        api_source_service.API_TESTING_DIR = self.temp_dir
        api_source_service.save_api_source({
            "source_id": "api_source_a",
            "name": "项目 A",
            "project_id": "apifox_a",
            "access_token": "token-a",
        })

    def tearDown(self):
        api_source_service.API_TESTING_DIR = self.old_source_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_execution_context_rejects_unauthenticated_request(self):
        from task_server import router

        class Handler:
            def __init__(self):
                self.responses = []

            def _authorized(self):
                return False

            def _json(self, payload, status=200):
                self.responses.append((payload, status))

        handler = Handler()
        original_context = metersphere_service.metersphere_execution_context
        called = []
        metersphere_service.metersphere_execution_context = lambda **_kwargs: called.append(True) or {}
        try:
            router.GET_ROUTES["/api/api-testing/metersphere/execution-context"](handler, {})
        finally:
            metersphere_service.metersphere_execution_context = original_context

        self.assertEqual(called, [])
        self.assertEqual(handler.responses, [({"ok": False, "error": "Unauthorized"}, 401)])

    def test_auth_binding_routes_require_auth_and_never_return_secret(self):
        from task_server import router

        class Handler:
            def __init__(self, authorized, body):
                self.authorized = authorized
                self.body = body
                self.responses = []

            def _authorized(self):
                return self.authorized

            def _body(self):
                return self.body

            def _json(self, payload, status=200):
                self.responses.append((payload, status))

        pattern = r"^/api/api-testing/sources/([^/]+)/auth-binding$"
        post = next(fn for matcher, fn in router._POST_REGEX_ROUTES if matcher.pattern == pattern)
        delete = next(fn for matcher, fn in router._DELETE_REGEX_ROUTES if matcher.pattern == pattern)
        match = re.match(pattern, "/api/api-testing/sources/api_source_a/auth-binding")
        unauthenticated = Handler(False, {"secret": "runtime-secret"})
        post(unauthenticated, {}, match)
        self.assertEqual(unauthenticated.responses, [({"ok": False, "error": "Unauthorized"}, 401)])

        original_save = metersphere_service.save_api_auth_binding
        original_clear = metersphere_service.clear_api_auth_binding
        metersphere_service.save_api_auth_binding = lambda *_args: {
            "auth_ref": "api_auth_a", "configured": True, "variable_name": "MTP_API_AUTH_A",
        }
        metersphere_service.clear_api_auth_binding = lambda *_args: {
            "auth_ref": "api_auth_a", "configured": False, "variable_name": "MTP_API_AUTH_A",
        }
        try:
            authenticated = Handler(True, {
                "auth_type": "bearer", "header_name": "Authorization", "secret": "runtime-secret",
            })
            post(authenticated, {}, match)
            deleted = Handler(True, {})
            delete(deleted, {}, match)
        finally:
            metersphere_service.save_api_auth_binding = original_save
            metersphere_service.clear_api_auth_binding = original_clear

        self.assertTrue(authenticated.responses[0][0]["binding"]["configured"])
        self.assertFalse(deleted.responses[0][0]["binding"]["configured"])
        self.assertNotIn("runtime-secret", json.dumps(authenticated.responses, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
