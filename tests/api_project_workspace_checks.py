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

from task_server.services import (
    api_asset_service,
    api_module_service,
    api_plan_generation_service,
    api_source_service,
    api_test_plan_service,
    api_workspace_service,
    metersphere_service,
)


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

    def test_unbound_source_uses_global_connection_only_for_project_catalog(self):
        self._create_sources(2)
        captured = []
        original_context = metersphere_service._metersphere_execution_context_with_config

        def fake_context(force, source_id, cfg, binding):
            captured.append((source_id, dict(cfg), dict(binding)))
            return {
                "ok": True,
                "source_id": source_id,
                "binding": binding,
                "businesses": [
                    {"id": "ms_project_legacy", "name": "默认项目", "enabled": True},
                    {"id": "ms_project_b", "name": "项目 B", "enabled": True},
                ],
                "environments": [
                    {
                        "id": "ms_env_legacy",
                        "name": "默认环境",
                        "project_id": "ms_project_legacy",
                        "enabled": True,
                    }
                ],
                "selection": {
                    "project_id": cfg.get("project_id"),
                    "environment_id": cfg.get("environment_id"),
                },
                "config": {
                    "project_id": cfg.get("project_id"),
                    "environment_id": cfg.get("environment_id"),
                },
                "readiness": {"state": "ready", "can_execute": True, "missing": []},
            }

        metersphere_service._metersphere_execution_context_with_config = fake_context
        try:
            context = metersphere_service.metersphere_execution_context(
                source_id="api_source_b",
            )
        finally:
            metersphere_service._metersphere_execution_context_with_config = original_context

        self.assertEqual(captured[0][0], "api_source_b")
        self.assertEqual(captured[0][1]["project_id"], "ms_project_legacy")
        self.assertEqual(captured[0][2], {})
        self.assertEqual(context["selection"], {"project_id": "", "environment_id": ""})
        self.assertEqual(context["environments"], [])
        self.assertFalse(context["readiness"]["can_execute"])
        self.assertTrue(
            any(
                "来源执行绑定" in item
                for item in context["readiness"]["missing"]
            )
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
            "",
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

    def test_bearer_header_is_canonical_and_rejects_other_names(self):
        self._create_sources(1)
        api_workspace_service.save_api_workspace_binding(
            "api_source_a", "ms_project_a", "ms_env_a",
        )

        for accepted in ("", "authorization", "AUTHORIZATION"):
            with self.subTest(accepted=accepted):
                normalized = api_workspace_service.normalize_api_auth_header(
                    "bearer",
                    accepted,
                )
                self.assertEqual(normalized, ("bearer", "Authorization"))

        for invalid in (
            "X-Auth",
            "Authorization:Injected",
            " Authorization",
            "Authorization\r\nInjected",
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    api_workspace_service.normalize_api_auth_header(
                        "bearer",
                        invalid,
                    )
                with self.assertRaises(ValueError):
                    api_workspace_service.save_api_auth_binding_metadata(
                        "api_source_a",
                        auth_type="bearer",
                        header_name=invalid,
                    )

    def test_service_rejects_invalid_api_key_header_before_remote_probe(self):
        self._create_sources(1)
        api_workspace_service.save_api_workspace_binding(
            "api_source_a", "ms_project_a", "ms_env_a",
        )
        calls = []

        class Adapter:
            def upsert_environment_variable(self, *_args, **_kwargs):
                calls.append("upsert")
                return {"ok": True, "configured": True}

        old_probe = metersphere_service._v365_adapter_probe

        def fake_probe(_config):
            calls.append("probe")
            return Adapter(), {"version": "v3.6.5-lts"}, True

        metersphere_service._v365_adapter_probe = fake_probe
        try:
            with self.assertRaisesRegex(ValueError, "HTTP field-name"):
                metersphere_service.save_api_auth_binding(
                    "api_source_a",
                    "api_key",
                    "X-API-Key:Injected",
                    "must-not-reach-remote",
                )
        finally:
            metersphere_service._v365_adapter_probe = old_probe

        self.assertEqual(calls, [])

    def test_service_rejects_invalid_bearer_header_before_remote_probe(self):
        self._create_sources(1)
        api_workspace_service.save_api_workspace_binding(
            "api_source_a", "ms_project_a", "ms_env_a",
        )
        calls = []

        class Adapter:
            def upsert_environment_variable(self, *_args, **_kwargs):
                calls.append("upsert")
                return {"ok": True, "configured": True}

        old_probe = metersphere_service._v365_adapter_probe

        def fake_probe(_config):
            calls.append("probe")
            return Adapter(), {"version": "v3.6.5-lts"}, True

        metersphere_service._v365_adapter_probe = fake_probe
        try:
            for invalid in ("X-Auth", "Authorization:Injected"):
                with self.subTest(invalid=invalid):
                    calls.clear()
                    with self.assertRaises(ValueError):
                        metersphere_service.save_api_auth_binding(
                            "api_source_a",
                            "bearer",
                            invalid,
                            "must-not-reach-remote",
                        )
                    self.assertEqual(calls, [])
        finally:
            metersphere_service._v365_adapter_probe = old_probe


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

    def test_execution_binding_reads_environments_for_requested_project(self):
        from task_server import router

        class Handler:
            def __init__(self):
                self.responses = []

            def _authorized(self):
                return True

            def _json(self, payload, status=200):
                self.responses.append((payload, status))

        self.assertTrue(
            hasattr(metersphere_service, "metersphere_project_options"),
            "MeterSphere project option lookup is missing",
        )
        pattern = r"^/api/api-testing/sources/([^/]+)/execution-binding$"
        route = next(fn for matcher, fn in router._GET_REGEX_ROUTES if matcher.pattern == pattern)
        match = re.match(
            pattern,
            "/api/api-testing/sources/api_source_a/execution-binding",
        )
        original_options = metersphere_service.metersphere_project_options
        original_context = metersphere_service.metersphere_execution_context
        calls = []
        metersphere_service.metersphere_project_options = (
            lambda source_id, project_id, force=False: calls.append(
                (source_id, project_id, force)
            )
            or {
                "projects": [
                    {"id": "ms_project_a", "name": "A", "enabled": True},
                    {"id": "ms_project_b", "name": "B", "enabled": True},
                ],
                "environments": [
                    {
                        "id": "ms_env_b",
                        "name": "B测试",
                        "project_id": "ms_project_b",
                        "enabled": True,
                    }
                ],
                "version": "v3.6.5-lts",
            }
        )
        metersphere_service.metersphere_execution_context = (
            lambda **_kwargs: self.fail("project-scoped lookup must not load the bound context")
        )
        try:
            handler = Handler()
            route(
                handler,
                {"project_id": "ms_project_b", "force": "true"},
                match,
            )
        finally:
            metersphere_service.metersphere_project_options = original_options
            metersphere_service.metersphere_execution_context = original_context

        payload, status = handler.responses[0]
        self.assertEqual(status, 200)
        self.assertEqual(
            calls,
            [("api_source_a", "ms_project_b", True)],
        )
        self.assertEqual(payload["selected_project_id"], "ms_project_b")
        self.assertEqual(payload["environments"][0]["id"], "ms_env_b")


def generation_document(endpoint_count=25):
    return {
        "openapi": "3.0.1",
        "info": {"title": "批次项目", "version": "1.0"},
        "paths": {
            f"/items/{index}": {
                "get": {
                    "x-apifox-id": f"item-{index}",
                    "x-apifox-folder": (
                        "家用业务/app接口/目标模块"
                        if index < endpoint_count - 1
                        else "家用业务/app接口/其他模块"
                    ),
                    "summary": f"接口 {index}",
                    "responses": {"200": {"description": "ok"}},
                }
            }
            for index in range(endpoint_count)
        },
    }


class ApiPlanGenerationChecks(unittest.TestCase):
    def setUp(self):
        self.old_dirs = {
            "asset": api_asset_service.API_TESTING_DIR,
            "generation": api_plan_generation_service.API_TESTING_DIR,
            "plan": api_test_plan_service.API_TESTING_DIR,
            "source": api_source_service.API_TESTING_DIR,
            "workspace": api_workspace_service.API_TESTING_DIR,
        }
        self.temp_dir = tempfile.mkdtemp(prefix="api_plan_generation_checks_")
        api_asset_service.API_TESTING_DIR = self.temp_dir
        api_plan_generation_service.API_TESTING_DIR = self.temp_dir
        api_test_plan_service.API_TESTING_DIR = self.temp_dir
        api_source_service.API_TESTING_DIR = self.temp_dir
        api_workspace_service.API_TESTING_DIR = self.temp_dir
        api_source_service.save_api_source({
            "source_id": "api_source_a",
            "name": "项目 A",
            "project_id": "apifox_a",
            "access_token": "token-a",
        })
        api_workspace_service.save_api_workspace_binding(
            "api_source_a", "ms_project_a", "ms_env_a",
        )
        staged = api_asset_service.stage_api_revision(
            "api_source_a",
            "项目 A",
            generation_document(61),
            source_type="apifox",
        )
        api_asset_service.activate_api_revision(staged["asset_id"], staged["revision_id"])
        self.revision_id = staged["revision_id"]
        self.endpoints = staged["revision"]["endpoints"]
        self.active_generators = 0
        self.max_concurrent_generators = 0
        self.generator_calls = []

    def tearDown(self):
        api_asset_service.API_TESTING_DIR = self.old_dirs["asset"]
        api_plan_generation_service.API_TESTING_DIR = self.old_dirs["generation"]
        api_test_plan_service.API_TESTING_DIR = self.old_dirs["plan"]
        api_source_service.API_TESTING_DIR = self.old_dirs["source"]
        api_workspace_service.API_TESTING_DIR = self.old_dirs["workspace"]
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def endpoint_ids(self, count):
        return [item["endpoint_id"] for item in self.endpoints[:count]]

    def recording_generator(self, snapshot_id, endpoint_ids, **kwargs):
        self.active_generators += 1
        self.max_concurrent_generators = max(
            self.max_concurrent_generators,
            self.active_generators,
        )
        try:
            self.generator_calls.append({
                "snapshot_id": snapshot_id,
                "endpoint_ids": list(endpoint_ids),
                **kwargs,
            })
            return {"plan_id": f"generated-plan-{kwargs['batch_index']}"}
        finally:
            self.active_generators -= 1

    def test_twenty_five_endpoints_generate_sequential_12_12_1_batches(self):
        generation = api_plan_generation_service.start_api_plan_generation(
            "api_source_a",
            self.revision_id,
            self.endpoint_ids(25),
            ["家用业务/app接口"],
            spawn=False,
        )

        completed = api_plan_generation_service.run_api_plan_generation(
            generation["generation_id"],
            generate_plan=self.recording_generator,
        )

        self.assertEqual(
            [row["endpoint_count"] for row in completed["batches"]],
            [12, 12, 1],
        )
        self.assertEqual(self.max_concurrent_generators, 1)
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["completed_batches"], 3)
        self.assertEqual(
            [call["batch_index"] for call in self.generator_calls],
            [1, 2, 3],
        )
        self.assertTrue(all(call["use_ai"] for call in self.generator_calls))
        self.assertTrue(all(call["require_ai_success"] for call in self.generator_calls))
        self.assertEqual(
            api_plan_generation_service.get_api_plan_generation(
                generation["generation_id"]
            )["status"],
            "succeeded",
        )

    def test_endpoint_count_bounds_reject_zero_and_sixty_one(self):
        with self.assertRaisesRegex(ValueError, "1-60"):
            api_plan_generation_service.start_api_plan_generation(
                "api_source_a", self.revision_id, [], ["家用业务/app接口"], spawn=False,
            )
        with self.assertRaisesRegex(ValueError, "1-60"):
            api_plan_generation_service.start_api_plan_generation(
                "api_source_a",
                self.revision_id,
                self.endpoint_ids(61),
                ["家用业务/app接口"],
                spawn=False,
            )

    def test_source_revision_endpoint_and_module_ownership_are_enforced(self):
        api_source_service.save_api_source({
            "source_id": "api_source_b",
            "name": "项目 B",
            "project_id": "apifox_b",
            "access_token": "token-b",
        })
        api_workspace_service.save_api_workspace_binding(
            "api_source_b", "ms_project_b", "ms_env_b",
        )
        with self.assertRaisesRegex(ValueError, "不属于"):
            api_plan_generation_service.start_api_plan_generation(
                "api_source_b",
                self.revision_id,
                self.endpoint_ids(1),
                ["家用业务/app接口"],
                spawn=False,
            )
        with self.assertRaisesRegex(ValueError, "接口"):
            api_plan_generation_service.start_api_plan_generation(
                "api_source_a",
                self.revision_id,
                ["missing-endpoint"],
                ["家用业务/app接口"],
                spawn=False,
            )
        with self.assertRaisesRegex(ValueError, "模块"):
            api_plan_generation_service.start_api_plan_generation(
                "api_source_a",
                self.revision_id,
                [self.endpoints[-1]["endpoint_id"]],
                ["家用业务/app接口/目标模块"],
                spawn=False,
            )

    def test_required_ai_failure_is_partial_without_local_plan(self):
        generation = api_plan_generation_service.start_api_plan_generation(
            "api_source_a",
            self.revision_id,
            self.endpoint_ids(25),
            ["家用业务/app接口"],
            spawn=False,
        )

        def fail_second(snapshot_id, endpoint_ids, **kwargs):
            if kwargs["batch_index"] == 2:
                raise ValueError("AI batch unavailable")
            return self.recording_generator(snapshot_id, endpoint_ids, **kwargs)

        completed = api_plan_generation_service.run_api_plan_generation(
            generation["generation_id"],
            generate_plan=fail_second,
        )

        self.assertEqual(completed["status"], "partial")
        self.assertEqual(completed["batches"][1]["status"], "failed")
        self.assertFalse(completed["batches"][1].get("plan_id"))
        self.assertNotEqual(completed["batches"][0].get("plan_id"), "")
        self.assertNotEqual(completed["batches"][2].get("plan_id"), "")

    def test_successful_ai_batch_persists_an_ordinary_scoped_plan(self):
        generation = api_plan_generation_service.start_api_plan_generation(
            "api_source_a",
            self.revision_id,
            self.endpoint_ids(1),
            ["家用业务/app接口"],
            spawn=False,
        )
        original_run_ai_skill = api_test_plan_service.run_ai_skill

        def successful_ai(_skill_name, payload, **kwargs):
            kwargs["runtime_trace"].update({
                "providerId": "qwen_plus",
                "model": "qwen3.8-plus",
            })
            endpoint_id = payload["endpoints"][0]["endpoint_id"]
            return {
                "cases": [{
                    "case_id": "AI-BATCH-1",
                    "endpoint_id": endpoint_id,
                    "name": "AI 批次成功用例",
                    "type": "positive",
                    "steps": ["发送请求"],
                }],
                "review": {},
            }

        api_test_plan_service.run_ai_skill = successful_ai
        try:
            completed = api_plan_generation_service.run_api_plan_generation(
                generation["generation_id"],
            )
        finally:
            api_test_plan_service.run_ai_skill = original_run_ai_skill

        plan_id = completed["batches"][0]["plan_id"]
        plan = api_test_plan_service.get_api_test_plan(
            plan_id,
            source_id="api_source_a",
        )
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(plan["source"], "ai")
        self.assertEqual(plan["generation_id"], generation["generation_id"])
        self.assertEqual(plan["selected_endpoint_keys"], generation["selected_endpoint_keys"])
        self.assertEqual(plan["binding_fingerprint"], generation["binding_fingerprint"])

    def test_retry_runs_only_failed_batches_and_reuses_successful_plan_ids(self):
        generation = api_plan_generation_service.start_api_plan_generation(
            "api_source_a",
            self.revision_id,
            self.endpoint_ids(25),
            ["家用业务/app接口"],
            spawn=False,
        )

        def fail_second(snapshot_id, endpoint_ids, **kwargs):
            if kwargs["batch_index"] == 2:
                raise ValueError("temporary AI failure")
            return self.recording_generator(snapshot_id, endpoint_ids, **kwargs)

        partial = api_plan_generation_service.run_api_plan_generation(
            generation["generation_id"],
            generate_plan=fail_second,
        )
        successful_ids = {
            row["batch_index"]: row["plan_id"]
            for row in partial["batches"]
            if row["status"] == "succeeded"
        }
        retried = api_plan_generation_service.retry_api_plan_generation(
            generation["generation_id"],
            spawn=False,
        )
        retry_calls = []

        def retry_generator(_snapshot_id, _endpoint_ids, **kwargs):
            retry_calls.append(kwargs["batch_index"])
            return {"plan_id": f"retry-plan-{kwargs['batch_index']}"}

        completed = api_plan_generation_service.run_api_plan_generation(
            retried["generation_id"],
            generate_plan=retry_generator,
        )

        self.assertEqual(retry_calls, [2])
        self.assertEqual(completed["status"], "succeeded")
        for batch_index, plan_id in successful_ids.items():
            self.assertEqual(
                completed["batches"][batch_index - 1]["plan_id"],
                plan_id,
            )

    def test_worker_rejects_stale_binding_fingerprint_before_ai(self):
        generation = api_plan_generation_service.start_api_plan_generation(
            "api_source_a",
            self.revision_id,
            self.endpoint_ids(1),
            ["家用业务/app接口"],
            spawn=False,
        )
        api_workspace_service.save_api_workspace_binding(
            "api_source_a", "ms_project_a", "ms_env_changed",
        )
        ai_calls = []
        original_run_ai_skill = api_test_plan_service.run_ai_skill
        api_test_plan_service.run_ai_skill = lambda *_args, **_kwargs: (
            ai_calls.append(True) or {"cases": [], "review": {}}
        )
        try:
            completed = api_plan_generation_service.run_api_plan_generation(
                generation["generation_id"],
            )
        finally:
            api_test_plan_service.run_ai_skill = original_run_ai_skill

        self.assertEqual(completed["status"], "failed")
        self.assertEqual(completed["batches"][0]["status"], "failed")
        self.assertFalse(completed["batches"][0]["plan_id"])
        self.assertIn("快照已过期", completed["batches"][0]["error"])
        self.assertEqual(ai_calls, [])

    def test_binding_change_during_ai_prevents_plan_persistence(self):
        generation = api_plan_generation_service.start_api_plan_generation(
            "api_source_a",
            self.revision_id,
            self.endpoint_ids(1),
            ["家用业务/app接口"],
            spawn=False,
        )
        original_run_ai_skill = api_test_plan_service.run_ai_skill

        def change_binding_then_return(_skill_name, payload, **_kwargs):
            api_workspace_service.save_api_workspace_binding(
                "api_source_a", "ms_project_a", "ms_env_changed_during_ai",
            )
            endpoint_id = payload["endpoints"][0]["endpoint_id"]
            return {
                "cases": [{
                    "case_id": "AI-TOCTOU-1",
                    "endpoint_id": endpoint_id,
                    "name": "AI 成功用例",
                    "type": "positive",
                    "steps": ["发送请求"],
                }],
                "review": {},
            }

        api_test_plan_service.run_ai_skill = change_binding_then_return
        try:
            completed = api_plan_generation_service.run_api_plan_generation(
                generation["generation_id"],
            )
        finally:
            api_test_plan_service.run_ai_skill = original_run_ai_skill

        self.assertEqual(completed["status"], "failed")
        self.assertFalse(completed["batches"][0]["plan_id"])
        plans_dir = Path(self.temp_dir, "plans")
        plan_files = [
            path for path in plans_dir.glob("api_plan*.json")
            if path.name != "index.json"
        ] if plans_dir.exists() else []
        self.assertEqual(plan_files, [])

    def test_generation_routes_require_auth_and_return_202_with_poll_interval(self):
        from task_server import router

        class Handler:
            def __init__(self, authorized, body=None):
                self.authorized = authorized
                self.body = body or {}
                self.responses = []

            def _authorized(self):
                return self.authorized

            def _body(self):
                return self.body

            def _json(self, payload, status=200):
                self.responses.append((payload, status))

        called = []
        original_start = api_plan_generation_service.start_api_plan_generation
        api_plan_generation_service.start_api_plan_generation = lambda *args, **kwargs: (
            called.append((args, kwargs))
            or {
                "generation_id": "api_plan_generation_route",
                "status": "queued",
                "poll_after_ms": 1000,
            }
        )
        try:
            unauthenticated = Handler(False, {"source_id": "api_source_a"})
            router.POST_ROUTES["/api/api-testing/plan-generations"](
                unauthenticated, {},
            )
            authenticated = Handler(True, {
                "source_id": "api_source_a",
                "revision_id": self.revision_id,
                "endpoint_ids": self.endpoint_ids(1),
                "module_paths": ["家用业务/app接口"],
            })
            router.POST_ROUTES["/api/api-testing/plan-generations"](
                authenticated, {},
            )
        finally:
            api_plan_generation_service.start_api_plan_generation = original_start

        self.assertEqual(
            unauthenticated.responses,
            [({"ok": False, "error": "Unauthorized"}, 401)],
        )
        self.assertEqual(called[0][0][0], "api_source_a")
        self.assertEqual(authenticated.responses[0][1], 202)
        self.assertEqual(
            authenticated.responses[0][0]["generation"]["poll_after_ms"],
            1000,
        )


if __name__ == "__main__":
    unittest.main()
