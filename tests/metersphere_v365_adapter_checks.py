#!/usr/bin/env python3
"""Focused contract checks for the MeterSphere 3.6.5 adapter."""

from __future__ import annotations

import base64
import copy
import io
import inspect
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from task_server.services import metersphere_v365_adapter
except ImportError:
    metersphere_v365_adapter = None

from task_server.services import metersphere_service
from task_server.services import api_report_service, api_source_service, api_test_plan_service, api_workspace_service


class MeterSphereV365AuthChecks(unittest.TestCase):
    def test_access_key_signature_matches_v365_aes_contract(self):
        self.assertIsNotNone(
            metersphere_v365_adapter,
            "MeterSphere 3.6.5 adapter module must exist",
        )
        headers = metersphere_v365_adapter.build_v365_auth_headers(
            "1234567890abcdef",
            "abcdef1234567890",
            now_ms=1700000000000,
            nonce="fixed-nonce",
        )

        self.assertEqual(set(headers), {"accessKey", "signature"})
        self.assertEqual(headers["accessKey"], "1234567890abcdef")

        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
        from cryptography.hazmat.primitives.padding import PKCS7

        decryptor = Cipher(
            algorithms.AES(b"abcdef1234567890"),
            modes.CBC(b"1234567890abcdef"),
        ).decryptor()
        padded = decryptor.update(base64.b64decode(headers["signature"])) + decryptor.finalize()
        unpadder = PKCS7(128).unpadder()
        plaintext = unpadder.update(padded) + unpadder.finalize()
        self.assertEqual(
            plaintext.decode("utf-8"),
            "1234567890abcdef|fixed-nonce|1700000000000",
        )

    def test_nested_sensitive_key_value_records_are_removed(self):
        sanitized = metersphere_service.sanitize_metersphere_data({
            "headers": [
                {"key": "Authorization", "value": "Bearer should-not-leak"},
                {"name": "Cookie", "value": "session=should-not-leak"},
                {"key": "X-Trace", "value": "trace-visible"},
            ],
            "variables": [
                {"name": "access_token", "value": "should-not-leak"},
                {"name": "region", "value": "cn-east"},
            ],
        })

        self.assertEqual(sanitized, {
            "headers": [{"key": "X-Trace", "value": "trace-visible"}],
            "variables": [{"name": "region", "value": "cn-east"}],
        })

    def test_api_key_variants_are_recursively_redacted(self):
        sanitized = metersphere_service.sanitize_metersphere_data({
            "headers": [
                {"key": "X-API-Key", "value": "header-api-key-secret"},
                {"name": "apiKey", "defaultValue": "default-api-key-secret"},
            ],
            "nested": {"api_key": "direct-api-key-secret"},
        })

        serialized = json.dumps(sanitized, ensure_ascii=False)
        self.assertNotIn("api-key-secret", serialized)
        self.assertNotIn("direct-api-key-secret", serialized)

    def test_nested_sensitive_records_are_removed_across_key_casing(self):
        sanitized = metersphere_service.sanitize_metersphere_data({
            "headers": [
                {"Key": "Authorization", "Value": "Bearer should-not-leak"},
                {"Header-Name": "X-Api-Token", "Default_Value": "should-not-leak"},
                {"Key": "X-Trace", "Value": "trace-visible"},
            ],
        })

        self.assertEqual(
            sanitized,
            {"headers": [{"Key": "X-Trace", "Value": "trace-visible"}]},
        )

    def test_service_request_sends_project_and_organization_context(self):
        captured = {}
        old_config = metersphere_service._load_raw_config
        old_urlopen = urllib.request.urlopen

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"id":"project-a"}'

        def fake_urlopen(request, timeout=30):
            captured.update(dict(request.header_items()))
            return Response()

        metersphere_service._load_raw_config = lambda: {
            "base_url": "http://metersphere.example.test",
            "auth_mode": "access_key",
            "access_key": "1234567890abcdef",
            "secret_key": "abcdef1234567890",
            "project_id": "project-a",
            "workspace_id": "organization-a",
        }
        urllib.request.urlopen = fake_urlopen
        try:
            result = metersphere_service._request_json("GET", "/project/get/project-a")
        finally:
            metersphere_service._load_raw_config = old_config
            urllib.request.urlopen = old_urlopen

        normalized = {key.lower(): value for key, value in captured.items()}
        self.assertTrue(result["ok"])
        self.assertEqual(normalized.get("project"), "project-a")
        self.assertEqual(normalized.get("organization"), "organization-a")

    def test_adapter_request_uses_bound_project_not_global_project(self):
        captured = {}
        old_config = metersphere_service._load_raw_config
        old_urlopen = urllib.request.urlopen

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"data":[]}'

        def fake_urlopen(request, timeout=30):
            captured.update(dict(request.header_items()))
            return Response()

        metersphere_service._load_raw_config = lambda: {
            "base_url": "http://metersphere.example.test",
            "auth_mode": "access_key",
            "access_key": "1234567890abcdef",
            "secret_key": "abcdef1234567890",
            "project_id": "project-global",
            "workspace_id": "organization-global",
        }
        urllib.request.urlopen = fake_urlopen
        try:
            adapter = metersphere_v365_adapter.MeterSphereV365Adapter(
                {
                    "base_url": "http://metersphere.example.test",
                    "auth_mode": "access_key",
                    "access_key": "1234567890abcdef",
                    "secret_key": "abcdef1234567890",
                    "project_id": "project-bound",
                    "workspace_id": "organization-bound",
                },
                metersphere_service._request_json,
                request_supports_config=True,
            )
            result = adapter._request("GET", "/project/list/options/organization-bound")
        finally:
            metersphere_service._load_raw_config = old_config
            urllib.request.urlopen = old_urlopen

        normalized = {key.lower(): value for key, value in captured.items()}
        self.assertTrue(result["ok"])
        self.assertEqual(normalized.get("project"), "project-bound")
        self.assertEqual(normalized.get("organization"), "organization-bound")

    def test_legacy_execution_refresh_uses_snapshot_project_header(self):
        captured = {}
        old_config = metersphere_service._load_raw_config
        old_urlopen = urllib.request.urlopen
        old_save = metersphere_service._save_execution
        connection_config = {
            "base_url": "http://metersphere.example.test",
            "auth_mode": "token",
            "token": "server-token",
            "workspace_id": "organization-global",
            "project_id": "project-global",
            "environment_id": "env-global",
            "run_status_path": "/runs/{run_id}",
        }

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"data":{"status":"RUNNING"}}'

        def fake_urlopen(request, timeout=30):
            captured.update(dict(request.header_items()))
            return Response()

        metersphere_service._load_raw_config = lambda: dict(connection_config)
        metersphere_service._save_execution = lambda _record: None
        urllib.request.urlopen = fake_urlopen
        try:
            metersphere_service._refresh_running_execution({
                "execution_id": "execution-snapshot",
                "source_id": "api_source_bound",
                "project_id": "project-snapshot",
                "environment_id": "env-snapshot",
                "connection_fingerprint": metersphere_service._connection_fingerprint(connection_config),
                "run_id": "run-1",
                "adapter": "legacy",
                "remote_status": "running",
                "status_poll_failures": 0,
                "unchanged_polls": 0,
            })
        finally:
            metersphere_service._load_raw_config = old_config
            metersphere_service._save_execution = old_save
            urllib.request.urlopen = old_urlopen

        headers = {key.lower(): value for key, value in captured.items()}
        self.assertEqual(headers.get("project"), "project-snapshot")
        self.assertNotEqual(headers.get("project"), "project-global")

    def test_source_context_legacy_metadata_uses_bound_project_header(self):
        captured = []
        temp_dir = tempfile.mkdtemp(prefix="metersphere_bound_metadata_")
        old_dir = metersphere_service.API_TESTING_DIR
        old_config = metersphere_service._load_raw_config
        old_binding_config = metersphere_service._binding_config
        old_urlopen = urllib.request.urlopen
        old_plans = api_test_plan_service.list_api_test_plans

        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return json.dumps(self.payload).encode("utf-8")

        global_cfg = {
            "base_url": "http://metersphere.example.test",
            "auth_mode": "token",
            "token": "server-token",
            "workspace_id": "organization-global",
            "project_id": "project-global",
            "environment_id": "env-global",
            "health_path": "/health",
            "project_list_path": "/projects",
            "environment_list_path": "/environments/{project_id}",
        }
        bound_cfg = {
            **global_cfg,
            "workspace_id": "organization-bound",
            "project_id": "project-bound",
            "environment_id": "env-bound",
        }

        def fake_urlopen(request, timeout=30):
            captured.append({key.lower(): value for key, value in request.header_items()})
            if request.full_url.endswith("/projects"):
                return Response({"data": [{"id": "project-bound", "name": "绑定业务"}]})
            if request.full_url.endswith("/environments/project-bound"):
                return Response({"data": [{"id": "env-bound", "name": "绑定环境", "projectId": "project-bound"}]})
            return Response({"data": {}})

        metersphere_service.API_TESTING_DIR = temp_dir
        metersphere_service._load_raw_config = lambda: dict(global_cfg)
        metersphere_service._binding_config = lambda _source_id, allow_legacy=True: (
            dict(bound_cfg),
            {"project_id": "project-bound", "environment_id": "env-bound"},
        )
        api_test_plan_service.list_api_test_plans = lambda limit=20: []
        urllib.request.urlopen = fake_urlopen
        try:
            context = metersphere_service.metersphere_execution_context(
                force=True,
                source_id="api_source_second",
            )
        finally:
            metersphere_service.API_TESTING_DIR = old_dir
            metersphere_service._load_raw_config = old_config
            metersphere_service._binding_config = old_binding_config
            api_test_plan_service.list_api_test_plans = old_plans
            urllib.request.urlopen = old_urlopen
            shutil.rmtree(temp_dir, ignore_errors=True)

        self.assertEqual(context["businesses"][0]["id"], "project-bound")
        self.assertEqual(context["environments"][0]["id"], "env-bound")
        self.assertTrue(captured)
        self.assertTrue(all(item.get("project") == "project-bound" for item in captured))

    def test_source_scoped_project_metadata_does_not_reuse_another_project_cache(self):
        calls = []
        temp_dir = tempfile.mkdtemp(prefix="metersphere_project_metadata_cache_")
        old_dir = metersphere_service.API_TESTING_DIR
        old_request = metersphere_service._request_json

        def request(method, path, payload=None, timeout=30, *, config=None):
            calls.append((method, path, config))
            return {"ok": True, "data": [{
                "id": config["project_id"],
                "name": config["project_id"],
            }]}

        config_a = {"project_id": "project-a", "project_list_path": "/projects"}
        config_b = {"project_id": "project-b", "project_list_path": "/projects"}
        metersphere_service.API_TESTING_DIR = temp_dir
        metersphere_service._request_json = request
        try:
            first = metersphere_service.list_metersphere_projects(config=config_a)
            second = metersphere_service.list_metersphere_projects(config=config_b)
        finally:
            metersphere_service.API_TESTING_DIR = old_dir
            metersphere_service._request_json = old_request
            shutil.rmtree(temp_dir, ignore_errors=True)

        self.assertEqual(first["items"][0]["id"], "project-a")
        self.assertEqual(second["items"][0]["id"], "project-b")
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[-1][2]["project_id"], "project-b")

    def test_service_request_fails_closed_before_network_on_invalid_access_key(self):
        called = False
        old_config = metersphere_service._load_raw_config
        old_urlopen = urllib.request.urlopen

        def fake_urlopen(request, timeout=30):
            nonlocal called
            called = True
            raise AssertionError("network must not be called for invalid access key")

        metersphere_service._load_raw_config = lambda: {
            "base_url": "http://metersphere.example.test",
            "auth_mode": "access_key",
            "access_key": "too-short",
            "secret_key": "abcdef1234567890",
            "project_id": "project-a",
            "workspace_id": "organization-a",
        }
        urllib.request.urlopen = fake_urlopen
        try:
            result = metersphere_service._request_json("GET", "/system/version/current")
        finally:
            metersphere_service._load_raw_config = old_config
            urllib.request.urlopen = old_urlopen

        self.assertFalse(result["ok"])
        self.assertFalse(called)
        self.assertIn("认证", result["error"])
        self.assertNotIn("too-short", json.dumps(result, ensure_ascii=False))

    def test_http_error_json_body_is_redacted_before_return(self):
        old_config = metersphere_service._load_raw_config
        old_urlopen = urllib.request.urlopen

        def fake_urlopen(request, timeout=30):
            raise urllib.error.HTTPError(
                request.full_url,
                400,
                "bad request",
                hdrs=None,
                fp=io.BytesIO(
                    b'{"password":"must-not-leak","message":"invalid request"}'
                ),
            )

        metersphere_service._load_raw_config = lambda: {
            "base_url": "http://metersphere.example.test",
            "auth_mode": "access_key",
            "access_key": "1234567890abcdef",
            "secret_key": "abcdef1234567890",
            "project_id": "project-a",
            "workspace_id": "organization-a",
        }
        urllib.request.urlopen = fake_urlopen
        try:
            result = metersphere_service._request_json("GET", "/system/version/current")
        finally:
            metersphere_service._load_raw_config = old_config
            urllib.request.urlopen = old_urlopen

        serialized = json.dumps(result, ensure_ascii=False)
        self.assertFalse(result["ok"])
        self.assertNotIn("must-not-leak", serialized)
        self.assertIn("invalid request", serialized)


class _ProbeRemote:
    def __init__(self, version="v3.6.5-lts-f043cdd2"):
        self.version = version
        self.calls = []

    def request(self, method, path, payload=None, timeout=30):
        self.calls.append((method, path, payload))
        if path == "/system/version/current":
            return {"ok": True, "data": self.version}
        if path == "/project/get/project-a":
            return {
                "ok": True,
                "id": "project-a",
                "name": "业务A",
                "moduleSetting": ["apiTest"],
            }
        if path == "/project/list/options/org-a":
            return {
                "ok": True,
                "data": [{"id": "project-a", "name": "业务A", "enable": True}],
            }
        if path == "/api/test/env-list/project-a":
            return {
                "ok": True,
                "data": [
                    {"id": "env-a", "name": "测试环境", "projectId": "project-a"},
                    {"id": "env-b", "name": "备用环境", "projectId": "project-a"},
                ],
            }
        if path in {"/api/definition/page", "/api/case/page", "/api/report/scenario/page"}:
            page_size = int((payload or {}).get("pageSize") or 0)
            if page_size < 5 or page_size > 500:
                return {"ok": False, "http_status": 400, "error": "invalid pageSize"}
            return {"ok": True, "list": [], "total": 0}
        if path == "/api/scenario/module/tree":
            return {"ok": True, "data": []}
        return {"ok": False, "error": f"unexpected {method} {path}"}


@unittest.skipIf(metersphere_v365_adapter is None, "adapter module not available")
class MeterSphereV365ProbeChecks(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            hasattr(metersphere_v365_adapter, "MeterSphereV365Adapter"),
            "MeterSphereV365Adapter must implement the exact runtime contract",
        )

    def test_exact_build_and_live_metadata_enable_capabilities(self):
        remote = _ProbeRemote()
        adapter = metersphere_v365_adapter.MeterSphereV365Adapter(
            {
                "project_id": "project-a",
                "environment_id": "env-a",
                "workspace_id": "org-a",
                "case_push_path": "/guessed/case/path",
            },
            remote.request,
        )

        probe = adapter.probe()

        self.assertEqual(probe["version"], "v3.6.5-lts-f043cdd2")
        self.assertEqual(probe["project"], {"id": "project-a", "name": "业务A"})
        self.assertEqual(probe["selected_environment"]["id"], "env-a")
        self.assertEqual(
            probe["environments"],
            [
                {"id": "env-a", "name": "测试环境", "project_id": "project-a", "enabled": True},
                {"id": "env-b", "name": "备用环境", "project_id": "project-a", "enabled": True},
            ],
        )
        self.assertEqual(probe["capabilities"]["missing"], [])
        self.assertTrue(probe["capabilities"]["can_read_assets"])
        self.assertTrue(probe["capabilities"]["can_push"])
        self.assertTrue(probe["capabilities"]["can_run"])
        self.assertTrue(probe["capabilities"]["can_query_run"])
        self.assertTrue(probe["capabilities"]["can_pull_report"])
        self.assertTrue(probe["capabilities"]["ready"])
        self.assertNotIn(
            ("GET", "/guessed/case/path", None),
            remote.calls,
            "configured legacy paths must not prove v3.6.5 capability",
        )

    def test_unsupported_version_fails_closed_before_write_capability(self):
        remote = _ProbeRemote(version="v3.6.6-lts-other")
        adapter = metersphere_v365_adapter.MeterSphereV365Adapter(
            {"project_id": "project-a", "environment_id": "env-a"},
            remote.request,
        )

        probe = adapter.probe()

        self.assertFalse(probe["capabilities"]["ready"])
        self.assertFalse(probe["capabilities"]["can_push"])
        self.assertFalse(probe["capabilities"]["can_run"])
        self.assertIn("受支持的 MeterSphere 版本", probe["capabilities"]["missing"])

    def test_missing_selected_environment_blocks_all_write_capabilities(self):
        remote = _ProbeRemote()
        adapter = metersphere_v365_adapter.MeterSphereV365Adapter(
            {"project_id": "project-a", "environment_id": "env-missing"},
            remote.request,
        )

        probe = adapter.probe()

        self.assertFalse(probe["capabilities"]["ready"])
        self.assertFalse(probe["capabilities"]["can_push"])
        self.assertIn("有效环境", probe["capabilities"]["missing"])

    def test_project_and_environment_options_use_exact_v365_endpoints(self):
        calls = []

        def request(method, path, payload=None, timeout=30, *, config=None):
            calls.append((method, path, payload, config))
            if path == "/project/list/options/org-a":
                return {"ok": True, "data": [
                    {"id": "project-a", "name": "业务 A", "enable": True},
                    {"id": "project-disabled", "name": "停用业务", "enable": False},
                ]}
            if path == "/api/test/env-list/project-a":
                return {"ok": True, "data": [
                    {"id": "env-a", "name": "测试环境", "projectId": "project-a", "enable": True},
                    {"id": "env-other", "name": "其他业务", "projectId": "project-other", "enable": True},
                    {"id": "env-disabled", "name": "停用环境", "projectId": "project-a", "enable": False},
                ]}
            return {"ok": False, "error": f"unexpected {method} {path}"}

        adapter = metersphere_v365_adapter.MeterSphereV365Adapter(
            {"workspace_id": "org-a", "project_id": "project-a"},
            request,
            request_supports_config=True,
        )

        projects = adapter.list_projects()
        environments = adapter.list_environments("project-a")

        self.assertEqual(projects, [{"id": "project-a", "name": "业务 A", "enabled": True}])
        self.assertEqual(environments, [{
            "id": "env-a", "name": "测试环境", "project_id": "project-a", "enabled": True,
        }])
        self.assertEqual([call[:2] for call in calls], [
            ("GET", "/project/list/options/org-a"),
            ("GET", "/api/test/env-list/project-a"),
        ])
        self.assertTrue(all(call[3]["project_id"] == "project-a" for call in calls))

    def test_request_does_not_retry_callback_type_error_that_is_not_config_signature(self):
        calls = []

        def request(method, path, payload=None, timeout=30, *, config=None):
            calls.append((method, path, config))
            raise TypeError("config decoder failed")

        adapter = metersphere_v365_adapter.MeterSphereV365Adapter(
            {"project_id": "project-a"}, request,
            request_supports_config=True,
        )

        with self.assertRaisesRegex(TypeError, "config decoder failed"):
            adapter._request("GET", "/system/version/current")

        self.assertEqual(len(calls), 1)

    def test_config_capable_callback_with_side_effecting_type_error_is_not_retried(self):
        calls = []

        def request(method, path, payload=None, timeout=30, *, config=None):
            calls.append((method, path, config))
            raise TypeError("unexpected keyword argument 'config'")

        adapter = metersphere_v365_adapter.MeterSphereV365Adapter(
            {"project_id": "project-a"},
            request,
            request_supports_config=True,
        )

        with self.assertRaisesRegex(TypeError, "unexpected keyword argument 'config'"):
            adapter._request("POST", "/api/case/add", {"name": "side effect"})

        self.assertEqual(len(calls), 1)

    def test_legacy_callback_is_called_once_without_config_keyword(self):
        calls = []

        def request(method, path, payload=None, timeout=30):
            calls.append((method, path, payload))
            return {"ok": True, "data": {}}

        adapter = metersphere_v365_adapter.MeterSphereV365Adapter(
            {"project_id": "project-a"}, request,
            request_supports_config=False,
        )

        result = adapter._request("GET", "/system/version/current")

        self.assertTrue(result["ok"])
        self.assertEqual(calls, [("GET", "/system/version/current", None)])


@unittest.skipIf(metersphere_v365_adapter is None, "adapter module not available")
class MeterSphereV365EnvironmentVariableChecks(unittest.TestCase):
    def test_environment_variable_upsert_verify_and_delete_use_exact_contract(self):
        class Remote:
            def __init__(self):
                self.calls = []
                self.environment = {
                    "id": "env-a",
                    "projectId": "project-a",
                    "name": "测试环境",
                    "description": "",
                    "config": {"commonVariables": [{
                        "key": "REGION", "value": "cn", "paramType": "CONSTANT", "enable": True,
                    }]},
                }

            def request(self, method, path, payload=None, timeout=30, **_kwargs):
                self.calls.append((method, path, payload))
                if method == "GET" and path == "/project/environment/get/env-a":
                    return {"ok": True, "data": copy.deepcopy(self.environment)}
                return {"ok": False, "error": f"unexpected {method} {path}"}

            def multipart(self, method, path, request, timeout=30, **_kwargs):
                self.calls.append((method, path, copy.deepcopy(request)))
                if method == "POST" and path == "/project/environment/update":
                    self.environment = copy.deepcopy(request)
                    return {"ok": True, "data": {"id": "env-a"}}
                return {"ok": False, "error": f"unexpected multipart {method} {path}"}

        remote = Remote()
        adapter = metersphere_v365_adapter.MeterSphereV365Adapter(
            {"project_id": "project-a", "environment_id": "env-a"},
            remote.request,
            request_multipart=remote.multipart,
        )

        saved = adapter.upsert_environment_variable(
            "env-a", "MTP_API_AUTH_EXACT", "runtime-secret", "Midscene API authentication",
        )

        self.assertTrue(saved["configured"])
        self.assertNotIn("runtime-secret", json.dumps(saved, ensure_ascii=False))
        self.assertEqual(
            [call[:2] for call in remote.calls],
            [
                ("GET", "/project/environment/get/env-a"),
                ("POST", "/project/environment/update"),
                ("GET", "/project/environment/get/env-a"),
            ],
        )
        self.assertEqual(
            remote.environment["config"]["commonVariables"][-1]["key"],
            "MTP_API_AUTH_EXACT",
        )
        self.assertTrue(adapter.verify_environment_variable("env-a", "MTP_API_AUTH_EXACT")["configured"])

        cleared = adapter.delete_environment_variable("env-a", "MTP_API_AUTH_EXACT")

        self.assertFalse(cleared["configured"])
        self.assertNotIn(
            "MTP_API_AUTH_EXACT",
            [item["key"] for item in remote.environment["config"]["commonVariables"]],
        )

    def test_environment_detail_from_another_project_is_rejected(self):
        class Remote:
            def request(self, method, path, payload=None, timeout=30, **_kwargs):
                return {"ok": True, "data": {
                    "id": "env-a", "projectId": "project-other", "name": "其他项目环境", "config": {},
                }}

        adapter = metersphere_v365_adapter.MeterSphereV365Adapter(
            {"project_id": "project-a", "environment_id": "env-a"}, Remote().request,
        )

        with self.assertRaisesRegex(metersphere_v365_adapter.MeterSphereV365ContractError, "当前项目"):
            adapter.get_environment_detail("env-a")

    def test_verify_requires_enabled_nonempty_variable_value(self):
        class Remote:
            def request(self, method, path, payload=None, timeout=30, **_kwargs):
                return {"ok": True, "data": {
                    "id": "env-a", "projectId": "project-a", "name": "测试环境",
                    "config": {"commonVariables": [
                        {"key": "MTP_API_AUTH_EMPTY", "value": "", "enable": True},
                        {"key": "MTP_API_AUTH_DISABLED", "value": "present", "enable": False},
                    ]},
                }}

        adapter = metersphere_v365_adapter.MeterSphereV365Adapter(
            {"project_id": "project-a", "environment_id": "env-a"}, Remote().request,
        )

        self.assertFalse(adapter.verify_environment_variable("env-a", "MTP_API_AUTH_EMPTY")["configured"])
        self.assertFalse(adapter.verify_environment_variable("env-a", "MTP_API_AUTH_DISABLED")["configured"])

    def test_same_environment_mutations_are_serialized_in_process(self):
        class Remote:
            def __init__(self):
                self.lock = threading.Lock()
                self.active_updates = 0
                self.max_active_updates = 0
                self.environment = {
                    "id": "env-a", "projectId": "project-a", "name": "测试环境",
                    "config": {"commonVariables": []},
                }

            def request(self, method, path, payload=None, timeout=30, **_kwargs):
                with self.lock:
                    return {"ok": True, "data": copy.deepcopy(self.environment)}

            def multipart(self, method, path, request, timeout=30, **_kwargs):
                with self.lock:
                    self.active_updates += 1
                    self.max_active_updates = max(self.max_active_updates, self.active_updates)
                time.sleep(0.03)
                with self.lock:
                    self.environment = copy.deepcopy(request)
                    self.active_updates -= 1
                return {"ok": True, "data": {"id": "env-a"}}

        remote = Remote()
        adapters = [
            metersphere_v365_adapter.MeterSphereV365Adapter(
                {"project_id": "project-a", "environment_id": "env-a"},
                remote.request,
                request_multipart=remote.multipart,
            )
            for _index in range(2)
        ]
        threads = [
            threading.Thread(
                target=adapter.upsert_environment_variable,
                args=("env-a", f"MTP_API_AUTH_{index}", f"secret-{index}", "test"),
            )
            for index, adapter in enumerate(adapters)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)

        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(remote.max_active_updates, 1)
        self.assertEqual(
            {item["key"] for item in remote.environment["config"]["commonVariables"]},
            {"MTP_API_AUTH_0", "MTP_API_AUTH_1"},
        )


def _case_plan():
    endpoint = {
        "endpoint_id": "endpoint-pet-update",
        "endpoint_key": "apifox:pet-update",
        "asset_revision_id": "revision-1",
        "method": "POST",
        "path": "/pets/{petId}",
        "response_schema": {
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "string"}},
        },
    }
    case = {
        "contract_version": "api_case_contract/v1",
        "case_id": "API-001",
        "endpoint_id": endpoint["endpoint_id"],
        "name": "更新宠物成功",
        "type": "positive",
        "priority": "P0",
        "request": {
            "method": "POST",
            "path": "/pets/{petId}",
            "path_params": {"petId": "pet-001"},
            "query": {"notify": True},
            "headers": {"X-Trace": "trace-001"},
            "body": {"name": "豆豆"},
            "auth_ref": "",
        },
        "assertions": [
            {"type": "status", "operator": "in", "expected": [200, 201]},
            {"type": "schema", "schema_ref": "response:2xx"},
        ],
        "variables": [],
        "dependencies": [],
        "readiness": {"state": "executable", "missing": [], "issues": []},
    }
    return {
        "plan_id": "api-plan-pets",
        "name": "宠物接口回归",
        "status": "confirmed",
        "revision_state": {"state": "fresh"},
        "execution_readiness": {"can_execute": True, "executable_case_count": 1},
        "endpoints": [endpoint],
        "cases": [case],
    }


class _CaseRemote(_ProbeRemote):
    def __init__(self, definitions=None):
        super().__init__()
        self.definitions = definitions or [{
            "id": "definition-1",
            "name": "更新宠物",
            "protocol": "HTTP",
            "method": "POST",
            "path": "/pets/{petId}",
            "projectId": "project-a",
        }]
        self.cases = {}
        self.case_counter = 0
        self.case_add_calls = 0
        self.case_update_calls = 0

    def request(self, method, path, payload=None, timeout=30):
        self.calls.append((method, path, payload))
        if path == "/api/definition/page":
            page_size = int((payload or {}).get("pageSize") or 0)
            if page_size < 5 or page_size > 500:
                return {"ok": False, "http_status": 400, "error": "invalid pageSize"}
            current = int((payload or {}).get("current") or 1)
            start = (current - 1) * page_size
            rows = self.definitions[start:start + page_size]
            return {"ok": True, "list": copy.deepcopy(rows), "total": len(self.definitions)}
        if path.startswith("/api/definition/get-detail/"):
            definition_id = path.rsplit("/", 1)[-1]
            definition = next((item for item in self.definitions if item["id"] == definition_id), None)
            if not definition:
                return {"ok": False, "error": "not found"}
            return {
                "ok": True,
                **copy.deepcopy(definition),
                "request": {
                    "polymorphicName": "MsHTTPElement",
                    "method": definition["method"],
                    "path": definition["path"],
                    "headers": [
                        {"key": "Authorization", "value": "must-not-copy", "enable": True},
                    ],
                },
            }
        if path == "/api/case/page":
            keyword = str((payload or {}).get("keyword") or "")
            rows = [
                copy.deepcopy(item)
                for item in self.cases.values()
                if not keyword or keyword in str(item.get("name") or "")
            ]
            return {"ok": True, "list": rows, "total": len(rows)}
        if path.startswith("/api/case/get-detail/"):
            case_id = path.rsplit("/", 1)[-1]
            case = self.cases.get(case_id)
            return {"ok": True, **copy.deepcopy(case)} if case else {"ok": False, "error": "not found"}
        if path == "/api/case/add":
            self.case_add_calls += 1
            self.case_counter += 1
            case_id = f"remote-case-{self.case_counter}"
            self.cases[case_id] = {
                "id": case_id,
                "projectId": "project-a",
                **copy.deepcopy(payload or {}),
            }
            return {"ok": True, **copy.deepcopy(self.cases[case_id])}
        if path == "/api/case/update":
            self.case_update_calls += 1
            case_id = str((payload or {}).get("id") or "")
            if case_id not in self.cases:
                return {"ok": False, "error": "not found"}
            self.cases[case_id].update(copy.deepcopy(payload or {}))
            return {"ok": True, **copy.deepcopy(self.cases[case_id])}
        return super().request(method, path, payload, timeout)


@unittest.skipIf(metersphere_v365_adapter is None, "adapter module not available")
class MeterSphereV365CaseUpsertChecks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="metersphere_v365_cases_")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _adapter(self, remote):
        self.assertIn(
            "bindings_dir",
            inspect.signature(metersphere_v365_adapter.MeterSphereV365Adapter).parameters,
            "adapter must accept an explicit binding storage root",
        )
        adapter = metersphere_v365_adapter.MeterSphereV365Adapter(
            {
                "project_id": "project-a",
                "environment_id": "env-a",
                "workspace_id": "org-a",
            },
            remote.request,
            bindings_dir=self.temp_dir,
        )
        self.assertTrue(
            hasattr(adapter, "upsert_plan_cases"),
            "adapter must implement idempotent API case upsert",
        )
        return adapter

    def test_case_upsert_maps_request_assertions_and_is_idempotent(self):
        remote = _CaseRemote()
        adapter = self._adapter(remote)
        plan = _case_plan()

        first = adapter.upsert_plan_cases(plan)
        second = adapter.upsert_plan_cases(plan)

        self.assertTrue(first["ok"])
        self.assertEqual(first["created"], 1)
        self.assertEqual(first["updated"], 0)
        self.assertEqual(second["created"], 0)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(first["remote_case_ids"], second["remote_case_ids"])
        self.assertEqual(remote.case_add_calls, 1)
        self.assertEqual(remote.case_update_calls, 0)

        remote_case = remote.cases[first["remote_case_ids"]["API-001"]]
        request = remote_case["request"]
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["path"], "/pets/{petId}")
        self.assertEqual(request["rest"][0]["key"], "petId")
        self.assertEqual(request["query"][0]["value"], "true")
        self.assertEqual(request["body"]["bodyType"], "JSON")
        self.assertEqual(json.loads(request["body"]["jsonBody"]["jsonValue"]), {"name": "豆豆"})
        self.assertNotIn("must-not-copy", json.dumps(request, ensure_ascii=False))
        assertions = request["children"][0]["assertionConfig"]["assertions"]
        self.assertEqual(assertions[0]["assertionType"], "RESPONSE_CODE")
        self.assertEqual(assertions[0]["condition"], "REGEX")
        self.assertEqual(assertions[0]["expectedValue"], "^(?:200|201)$")
        schema_assertion = assertions[1]["jsonPathAssertion"]["assertions"][0]
        self.assertEqual(schema_assertion["expression"], "$.id")
        self.assertEqual(schema_assertion["condition"], "NOT_EMPTY")

        binding_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in Path(self.temp_dir).rglob("*.json")
        )
        self.assertNotIn("pet-001", binding_text)
        self.assertNotIn("豆豆", binding_text)
        self.assertNotIn("must-not-copy", binding_text)

    def test_changed_contract_updates_only_owned_remote_case(self):
        remote = _CaseRemote()
        adapter = self._adapter(remote)
        plan = _case_plan()
        first = adapter.upsert_plan_cases(plan)
        changed = copy.deepcopy(plan)
        changed["cases"][0]["request"]["body"]["name"] = "花花"

        result = adapter.upsert_plan_cases(changed)

        self.assertTrue(result["ok"])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(remote.case_add_calls, 1)
        self.assertEqual(remote.case_update_calls, 1)
        remote_id = first["remote_case_ids"]["API-001"]
        body = json.loads(remote.cases[remote_id]["request"]["body"]["jsonBody"]["jsonValue"])
        self.assertEqual(body, {"name": "花花"})

    def test_provider_read_metadata_does_not_force_case_update(self):
        remote = _CaseRemote()
        adapter = self._adapter(remote)
        plan = _case_plan()
        first = adapter.upsert_plan_cases(plan)
        remote_case = remote.cases[first["remote_case_ids"]["API-001"]]
        remote_case["request"].update({
            "moduleId": "provider-module",
            "num": 0,
            "parent": None,
            "stepId": None,
        })
        remote_case["request"]["authConfig"]["basicAuth"]["valid"] = False
        remote_case["request"]["authConfig"]["digestAuth"]["valid"] = False
        remote_case["request"]["children"][0].update({
            "csvIds": None,
            "parent": None,
            "resourceId": None,
        })
        for assertion in remote_case["request"]["children"][0]["assertionConfig"]["assertions"]:
            assertion.update({"id": None, "projectId": None})

        second = adapter.upsert_plan_cases(plan)

        self.assertTrue(second["ok"])
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(second["updated"], 0)
        self.assertEqual(remote.case_update_calls, 0)

    def test_missing_binding_recovers_by_exact_marker_without_duplicate(self):
        remote = _CaseRemote()
        adapter = self._adapter(remote)
        plan = _case_plan()
        first = adapter.upsert_plan_cases(plan)
        for path in Path(self.temp_dir).rglob("*.json"):
            path.unlink()

        recovered = self._adapter(remote).upsert_plan_cases(plan)

        self.assertTrue(recovered["ok"])
        self.assertEqual(recovered["recovered"], 1)
        self.assertEqual(recovered["created"], 0)
        self.assertEqual(remote.case_add_calls, 1)
        self.assertEqual(recovered["remote_case_ids"], first["remote_case_ids"])

    def test_ambiguous_definition_blocks_case_without_remote_write(self):
        duplicate = {
            "id": "definition-2",
            "name": "重复接口",
            "protocol": "HTTP",
            "method": "POST",
            "path": "/pets/{petId}/",
            "projectId": "project-a",
        }
        remote = _CaseRemote(definitions=_CaseRemote().definitions + [duplicate])
        result = self._adapter(remote).upsert_plan_cases(_case_plan())

        self.assertFalse(result["ok"])
        self.assertEqual(result["created"], 0)
        self.assertEqual(remote.case_add_calls, 0)
        self.assertEqual(result["blocked"][0]["reason"], "ambiguous_definition")

    def test_definition_match_reads_all_pages_with_v365_page_bounds(self):
        filler = [{
            "id": f"definition-filler-{index}",
            "name": f"填充接口 {index}",
            "protocol": "HTTP",
            "method": "GET",
            "path": f"/filler/{index}",
            "projectId": "project-a",
        } for index in range(500)]
        target = _CaseRemote().definitions[0]
        remote = _CaseRemote(definitions=filler + [target])

        result = self._adapter(remote).upsert_plan_cases(_case_plan())

        self.assertTrue(result["ok"])
        definition_calls = [
            payload for method, path, payload in remote.calls
            if method == "POST" and path == "/api/definition/page"
        ]
        self.assertGreaterEqual(len(definition_calls), 2)
        self.assertTrue(all(5 <= payload["pageSize"] <= 500 for payload in definition_calls))

    def test_sensitive_auth_header_contract_is_rejected_before_remote_write(self):
        remote = _CaseRemote()
        plan = _case_plan()
        plan["cases"][0]["request"]["headers"] = {
            "X-API-Key": "must-not-leave-platform",
        }

        result = self._adapter(remote).upsert_plan_cases(plan)

        self.assertFalse(result["ok"])
        self.assertEqual(result["created"], 0)
        self.assertEqual(remote.case_add_calls, 0)
        self.assertEqual(result["blocked"][0]["reason"], "sensitive_header_in_contract")

    def test_literal_auth_header_is_rejected_before_remote_write(self):
        remote = _CaseRemote()
        plan = _case_plan()
        plan["cases"][0]["request"]["headers"] = {
            "Auth": "literal-auth-secret",
        }

        result = self._adapter(remote).upsert_plan_cases(plan)

        self.assertFalse(result["ok"])
        self.assertEqual(result["created"], 0)
        self.assertEqual(remote.case_add_calls, 0)
        self.assertEqual(result["blocked"][0]["reason"], "sensitive_header_in_contract")

    def test_exact_auth_reference_is_the_only_sensitive_header_allowed(self):
        remote = _CaseRemote()
        adapter = self._adapter(remote)
        plan = _case_plan()
        plan["auth_binding"] = {
            "auth_ref": "api_auth_exact",
            "auth_type": "bearer",
            "header_name": "Authorization",
            "variable_name": "MTP_API_AUTH_EXACT",
            "environment_id": "env-a",
            "configured": True,
        }
        plan["cases"][0]["request"]["auth_ref"] = "api_auth_exact"

        payload, _evidence = adapter._materialize_case(
            plan, plan["cases"][0], plan["endpoints"][0], remote.definitions[0],
        )

        headers = {item["key"]: item["value"] for item in payload["request"]["headers"]}
        self.assertEqual(headers["Authorization"], "Bearer ${MTP_API_AUTH_EXACT}")

        wrong_auth = copy.deepcopy(plan)
        wrong_auth["cases"][0]["request"]["auth_ref"] = "api_auth_other"
        with self.assertRaisesRegex(metersphere_v365_adapter.MeterSphereV365ContractError, "认证引用"):
            adapter._materialize_case(
                wrong_auth, wrong_auth["cases"][0], wrong_auth["endpoints"][0], remote.definitions[0],
            )

        wrong_environment = self._adapter(remote)
        wrong_environment.config["environment_id"] = "env-b"
        with self.assertRaisesRegex(metersphere_v365_adapter.MeterSphereV365ContractError, "环境"):
            wrong_environment._materialize_case(
                plan, plan["cases"][0], plan["endpoints"][0], remote.definitions[0],
            )


class _ScenarioRemote(_CaseRemote):
    def __init__(self):
        super().__init__()
        self.modules = []
        self.scenarios = {}
        self.scenario_counter = 0
        self.scenario_add_calls = 0
        self.scenario_update_calls = 0
        self.trigger_has_report_id = True
        self.last_trigger_report_id = ""
        self.last_trigger_payload = {}
        self.report = {
            "id": "remote-report-1",
            "name": "宠物接口回归",
            "execStatus": "RUNNING",
            "status": "",
            "requestTotal": 1,
            "stepSuccessCount": 0,
            "stepErrorCount": 0,
            "stepPendingCount": 1,
            "children": [{
                "stepId": "step-1",
                "name": "更新宠物成功",
                "status": "PENDING",
                "requestTime": 0,
            }],
        }

    def request(self, method, path, payload=None, timeout=30):
        if path == "/api/scenario/module/tree":
            return {"ok": True, "data": copy.deepcopy(self.modules)}
        if path == "/api/scenario/module/add":
            module_id = f"module-{len(self.modules) + 1}"
            self.modules.append({
                "id": module_id,
                "name": str((payload or {}).get("name") or ""),
                "parentId": str((payload or {}).get("parentId") or ""),
                "projectId": "project-a",
                "children": [],
            })
            return {"ok": True, "data": module_id}
        if path == "/api/scenario/page":
            keyword = str((payload or {}).get("keyword") or "")
            rows = [
                copy.deepcopy(item)
                for item in self.scenarios.values()
                if not keyword or keyword in str(item.get("name") or "")
            ]
            return {"ok": True, "list": rows, "total": len(rows)}
        if path.startswith("/api/scenario/get/"):
            scenario_id = path.rsplit("/", 1)[-1]
            scenario = self.scenarios.get(scenario_id)
            return {"ok": True, **copy.deepcopy(scenario)} if scenario else {"ok": False, "error": "not found"}
        if path == "/api/scenario/add":
            self.scenario_add_calls += 1
            self.scenario_counter += 1
            scenario_id = f"remote-scenario-{self.scenario_counter}"
            self.scenarios[scenario_id] = {
                "id": scenario_id,
                **copy.deepcopy(payload or {}),
            }
            return {"ok": True, **copy.deepcopy(self.scenarios[scenario_id])}
        if path == "/api/scenario/update":
            self.scenario_update_calls += 1
            scenario_id = str((payload or {}).get("id") or "")
            if scenario_id not in self.scenarios:
                return {"ok": False, "error": "not found"}
            self.scenarios[scenario_id].update(copy.deepcopy(payload or {}))
            return {"ok": True, **copy.deepcopy(self.scenarios[scenario_id])}
        if method == "POST" and path == "/api/scenario/run":
            self.last_trigger_payload = copy.deepcopy(payload or {})
            report_id = str((payload or {}).get("reportId") or "")
            self.last_trigger_report_id = report_id
            scenario_id = str((payload or {}).get("id") or "")
            task_item = {"id": "task-item-1", "resourceId": scenario_id}
            if self.trigger_has_report_id and report_id:
                task_item["reportId"] = report_id
                self.report["id"] = report_id
            return {"ok": True, "taskInfo": {"taskId": "task-1"}, "taskItem": task_item}
        if path.startswith("/api/report/scenario/get/"):
            report_id = path.rsplit("/", 1)[-1]
            if report_id != str(self.report.get("id") or ""):
                return {"ok": False, "error": "not found"}
            return {"ok": True, **copy.deepcopy(self.report)}
        return super().request(method, path, payload, timeout)


@unittest.skipIf(metersphere_v365_adapter is None, "adapter module not available")
class MeterSphereV365ScenarioChecks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="metersphere_v365_scenario_")
        self.remote = _ScenarioRemote()
        self.adapter = metersphere_v365_adapter.MeterSphereV365Adapter(
            {
                "project_id": "project-a",
                "environment_id": "env-a",
                "workspace_id": "org-a",
            },
            self.remote.request,
            bindings_dir=self.temp_dir,
        )
        self.plan = _case_plan()
        case_result = self.adapter.upsert_plan_cases(self.plan)
        self.assertTrue(case_result["ok"])

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _require_scenario_contract(self):
        for method in (
            "upsert_plan_scenario",
            "trigger_plan",
            "get_run",
            "get_report",
        ):
            self.assertTrue(
                hasattr(self.adapter, method),
                f"adapter must implement {method}",
            )

    def test_scenario_upsert_uses_stable_case_refs_and_is_idempotent(self):
        self._require_scenario_contract()

        first = self.adapter.upsert_plan_scenario(self.plan)
        second = self.adapter.upsert_plan_scenario(self.plan)

        self.assertTrue(first["ok"])
        self.assertEqual(first["created"], 1)
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(first["scenario_id"], second["scenario_id"])
        self.assertEqual(self.remote.scenario_add_calls, 1)
        self.assertEqual(self.remote.scenario_update_calls, 0)
        scenario = self.remote.scenarios[first["scenario_id"]]
        self.assertEqual(scenario["environmentId"], "env-a")
        self.assertEqual(len(scenario["steps"]), 1)
        self.assertEqual(scenario["steps"][0]["stepType"], "API_CASE")
        self.assertEqual(scenario["steps"][0]["refType"], "REF")
        self.assertEqual(scenario["steps"][0]["resourceId"], "remote-case-1")
        self.assertEqual(scenario["stepDetails"], {})

    def test_changed_plan_name_updates_owned_scenario(self):
        self._require_scenario_contract()
        first = self.adapter.upsert_plan_scenario(self.plan)
        changed = copy.deepcopy(self.plan)
        changed["name"] = "宠物接口每日回归"

        result = self.adapter.upsert_plan_scenario(changed)

        self.assertTrue(result["ok"])
        self.assertEqual(result["updated"], 1)
        self.assertEqual(self.remote.scenario_add_calls, 1)
        self.assertEqual(self.remote.scenario_update_calls, 1)
        self.assertIn("宠物接口每日回归", self.remote.scenarios[first["scenario_id"]]["name"])

    def test_provider_read_metadata_does_not_force_scenario_update(self):
        first = self.adapter.upsert_plan_scenario(self.plan)
        remote = self.remote.scenarios[first["scenario_id"]]
        remote["stepDetails"] = None
        remote["steps"][0].update({
            "children": None,
            "csvIds": None,
            "uniqueId": None,
            "parentId": None,
            "resourceNum": None,
            "scenarioId": first["scenario_id"],
            "sort": 1,
            "versionId": None,
        })

        second = self.adapter.upsert_plan_scenario(self.plan)

        self.assertTrue(second["ok"])
        self.assertEqual(second["unchanged"], 1)
        self.assertEqual(second["updated"], 0)
        self.assertEqual(self.remote.scenario_update_calls, 0)

    def test_trigger_requires_real_report_id_and_poll_uses_report_contract(self):
        self._require_scenario_contract()
        scenario = self.adapter.upsert_plan_scenario(self.plan)
        stable_step_id = next(iter(
            self.adapter.load_binding(self.plan["plan_id"])["scenario"]["step_case_ids"]
        ))
        self.remote.scenarios[scenario["scenario_id"]]["steps"][0]["uniqueId"] = None

        trigger = self.adapter.trigger_plan(self.plan["plan_id"])
        running = self.adapter.get_run(trigger["run_id"])
        self.remote.report.update({
            "execStatus": "COMPLETED",
            "status": "SUCCESS",
            "stepSuccessCount": 1,
            "stepPendingCount": 0,
            "children": [{
                "stepId": stable_step_id,
                "name": "更新宠物成功",
                "status": "SUCCESS",
                "requestTime": 86,
            }],
        })
        completed = self.adapter.get_run(trigger["run_id"])
        report = self.adapter.get_report(trigger["run_id"])

        self.assertTrue(scenario["ok"])
        self.assertTrue(trigger["ok"])
        self.assertEqual(trigger["adapter"], "metersphere_v3.6.5")
        self.assertEqual(trigger["scenario_id"], scenario["scenario_id"])
        self.assertEqual(trigger["status"], "running")
        self.assertEqual(trigger["run_id"], self.remote.last_trigger_report_id)
        self.assertRegex(trigger["run_id"], r"^[0-9a-f-]{36}$")
        self.assertEqual(self.remote.last_trigger_payload["id"], scenario["scenario_id"])
        self.assertEqual(self.remote.last_trigger_payload["projectId"], "project-a")
        self.assertEqual(self.remote.last_trigger_payload["environmentId"], "env-a")
        self.assertEqual(
            self.remote.last_trigger_payload["steps"][0]["uniqueId"],
            stable_step_id,
        )
        self.assertFalse(any(
            method == "GET" and path.startswith("/api/scenario/run/")
            for method, path, _payload in self.remote.calls
        ))
        self.assertEqual(running["status"], "running")
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["stats"], {"total": 1, "passed": 1, "failed": 0})
        self.assertEqual(report["id"], trigger["run_id"])
        self.assertEqual(report["results"][0]["case_id"], "API-001")
        self.assertEqual(report["results"][0]["status"], "passed")
        self.assertEqual(report["results"][0]["duration_ms"], 86)

        self.remote.trigger_has_report_id = False
        missing = self.adapter.trigger_plan(self.plan["plan_id"])
        self.assertFalse(missing["ok"])
        self.assertEqual(missing["run_id"], "")
        self.assertIn("真实 reportId", missing["error"])

    def test_completed_steps_without_provider_terminal_state_fail_closed(self):
        self.adapter.upsert_plan_scenario(self.plan)
        trigger = self.adapter.trigger_plan(self.plan["plan_id"])
        stable_step_id = next(iter(
            self.adapter.load_binding(self.plan["plan_id"])["scenario"]["step_case_ids"]
        ))
        self.remote.report.update({
            "execStatus": "PENDING",
            "status": "",
            "startTime": 1,
            "endTime": None,
            "requestTotal": 0,
            "stepSuccessCount": 1,
            "stepErrorCount": 0,
            "stepPendingCount": 0,
            "children": [{
                "stepId": stable_step_id,
                "stepType": "API_CASE",
                "name": "更新宠物成功",
                "status": "SUCCESS",
                "requestTime": 86,
                "code": "200",
            }],
        })

        stalled = self.adapter.get_run(trigger["run_id"])
        report = self.adapter.get_report(trigger["run_id"])

        self.assertEqual(stalled["status"], "failed")
        self.assertEqual(stalled["failure_reason"], "provider_terminal_state_missing")
        self.assertIn("未回写终态", stalled["error"])
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["failure_reason"], "provider_terminal_state_missing")
        self.assertEqual(report["results"][0]["case_id"], "API-001")

    def test_report_excludes_non_request_container_steps(self):
        self.adapter.upsert_plan_scenario(self.plan)
        trigger = self.adapter.trigger_plan(self.plan["plan_id"])
        stable_step_id = next(iter(
            self.adapter.load_binding(self.plan["plan_id"])["scenario"]["step_case_ids"]
        ))
        self.remote.report.update({
            "execStatus": "COMPLETED",
            "status": "SUCCESS",
            "requestTotal": 1,
            "stepSuccessCount": 1,
            "stepPendingCount": 0,
            "children": [{
                "stepId": "group-1",
                "stepType": "GROUP",
                "name": "接口分组",
                "status": "SUCCESS",
                "children": [{
                    "stepId": stable_step_id,
                    "stepType": "API_CASE",
                    "name": "更新宠物成功",
                    "status": "SUCCESS",
                    "requestTime": 86,
                }],
            }],
        })

        report = self.adapter.get_report(trigger["run_id"])

        self.assertEqual(len(report["results"]), 1)
        self.assertEqual(report["results"][0]["case_id"], "API-001")


@unittest.skipIf(metersphere_v365_adapter is None, "adapter module not available")
class MeterSphereV365ServiceIntegrationChecks(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="metersphere_v365_service_")
        self.old_service_dir = metersphere_service.API_TESTING_DIR
        self.old_report_dir = api_report_service.API_TESTING_DIR
        self.old_source_dir = api_source_service.API_TESTING_DIR
        self.old_workspace_dir = api_workspace_service.API_TESTING_DIR
        self.old_request = metersphere_service._request_json
        self.old_get_plan = api_test_plan_service.get_api_test_plan
        self.old_list_plans = api_test_plan_service.list_api_test_plans
        metersphere_service.API_TESTING_DIR = self.temp_dir
        api_report_service.API_TESTING_DIR = self.temp_dir
        api_source_service.API_TESTING_DIR = self.temp_dir
        api_workspace_service.API_TESTING_DIR = self.temp_dir
        self.remote = _ScenarioRemote()
        metersphere_service._request_json = self.remote.request
        self.plan = _case_plan()
        api_test_plan_service.get_api_test_plan = lambda plan_id: (
            copy.deepcopy(self.plan) if plan_id == self.plan["plan_id"] else {}
        )
        api_test_plan_service.list_api_test_plans = lambda limit=20: [{
            "plan_id": self.plan["plan_id"],
            "name": self.plan["name"],
            "status": "confirmed",
            "case_count": 1,
            "endpoint_count": 1,
            "executable_case_count": 1,
            "needs_review_case_count": 0,
            "revision_state": {"state": "fresh"},
            "execution_readiness": {"can_execute": True, "executable_case_count": 1},
        }]
        metersphere_service.save_metersphere_config({
            "base_url": "http://metersphere.example.test",
            "auth_mode": "access_key",
            "access_key": "1234567890abcdef",
            "secret_key": "abcdef1234567890",
            "workspace_id": "org-a",
            "project_id": "project-a",
            "environment_id": "env-a",
            "health_path": "/guessed/health",
            "project_list_path": "/guessed/projects",
            "environment_list_path": "/guessed/environments",
            "case_push_path": "/guessed/cases",
            "plan_run_path": "/guessed/run",
            "run_status_path": "/guessed/status/{run_id}",
            "report_path": "/guessed/report/{run_id}",
        })

    def tearDown(self):
        metersphere_service.API_TESTING_DIR = self.old_service_dir
        api_report_service.API_TESTING_DIR = self.old_report_dir
        api_source_service.API_TESTING_DIR = self.old_source_dir
        api_workspace_service.API_TESTING_DIR = self.old_workspace_dir
        metersphere_service._request_json = self.old_request
        api_test_plan_service.get_api_test_plan = self.old_get_plan
        api_test_plan_service.list_api_test_plans = self.old_list_plans
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _token_connection(self, **overrides):
        config = {
            "base_url": "http://metersphere.example.test",
            "auth_mode": "token",
            "token": "connection-token-a",
            "access_key": "",
            "secret_key": "",
            "clear_secrets": ["access_key", "secret_key"],
            "workspace_id": "org-a",
            "project_id": "project-global",
            "environment_id": "env-global",
            "health_path": "/health",
            "project_list_path": "/projects",
            "environment_list_path": "/environments/{project_id}",
            "case_push_path": "/cases",
            "plan_run_path": "/runs",
            "run_status_path": "/runs/{run_id}",
            "report_path": "/reports/{run_id}",
        }
        config.update(overrides)
        return config

    def _start_source_execution(self, run_id="run-source-a"):
        api_source_service.save_api_source({
            "source_id": "api_source_pets",
            "name": "宠物项目",
            "project_id": "apifox-pets",
            "access_token": "test-token",
        })
        binding = api_workspace_service.save_api_workspace_binding(
            "api_source_pets", "project-a", "env-a",
            project_name="业务A", environment_name="测试环境",
        )
        self.plan["source_id"] = "api_source_pets"
        old_spawn = metersphere_service._spawn_execution_worker
        metersphere_service._spawn_execution_worker = lambda _execution_id: None
        try:
            execution = metersphere_service.start_metersphere_execution(self.plan["plan_id"])
        finally:
            metersphere_service._spawn_execution_worker = old_spawn
        record = metersphere_service._load_execution(execution["execution_id"])
        record["run_id"] = run_id
        record["adapter"] = "legacy"
        record["status"] = "running"
        record["remote_status"] = "running"
        for phase in record.get("phases") or []:
            if phase.get("id") == "metersphere_run":
                phase["state"] = "running"
        metersphere_service._save_execution(record)
        return record, binding

    def _run_worker_during_connection_change(self, **changes):
        execution, _binding = self._start_source_execution()
        baseline = metersphere_service._load_raw_config()
        drifted = {**baseline, **changes}
        config_reads = []
        network_calls = []
        old_load_config = metersphere_service._load_raw_config
        old_request = metersphere_service._request_json

        def changing_config():
            config_reads.append(len(config_reads) + 1)
            return dict(drifted)

        def unexpected_request(method, path, payload=None, timeout=30, *, config=None):
            network_calls.append((method, path, config))
            return {"ok": False, "error": "network must not be reached after connection drift"}

        metersphere_service._load_raw_config = changing_config
        metersphere_service._request_json = unexpected_request
        try:
            metersphere_service._run_metersphere_execution(execution["execution_id"])
        finally:
            metersphere_service._load_raw_config = old_load_config
            metersphere_service._request_json = old_request
        return (
            metersphere_service._load_execution(execution["execution_id"]),
            config_reads,
            network_calls,
        )

    def _run_source_phase_during_config_save(
        self,
        operation,
        remote_request,
        updated_config,
    ):
        remote_entered = threading.Event()
        release_remote = threading.Event()
        save_started = threading.Event()
        save_finished = threading.Event()
        captured_configs = []
        operation_result = {}
        operation_errors = []
        save_errors = []
        old_request = metersphere_service._request_json

        def blocking_request(method, path, payload=None, timeout=30, *, config=None):
            captured_configs.append(copy.deepcopy(config or {}))
            remote_entered.set()
            if not release_remote.wait(2):
                return {"ok": False, "error": "test remote release timed out"}
            return remote_request(method, path, payload, timeout)

        def run_operation():
            try:
                operation_result.update(operation())
            except Exception as exc:
                operation_errors.append(exc)

        def save_config():
            save_started.set()
            try:
                metersphere_service.save_metersphere_config(updated_config)
            except Exception as exc:
                save_errors.append(exc)
            finally:
                save_finished.set()

        metersphere_service._request_json = blocking_request
        operation_thread = threading.Thread(target=run_operation)
        save_thread = threading.Thread(target=save_config)
        try:
            operation_thread.start()
            entered = remote_entered.wait(2)
            if entered:
                save_thread.start()
                save_started.wait(1)
                saved_while_remote_blocked = save_finished.wait(0.1)
            else:
                saved_while_remote_blocked = False
        finally:
            release_remote.set()
            operation_thread.join(2)
            if save_thread.ident is not None:
                save_thread.join(2)
            metersphere_service._request_json = old_request

        return {
            "entered": entered,
            "saved_while_remote_blocked": saved_while_remote_blocked,
            "save_finished": save_finished.is_set(),
            "operation_alive": operation_thread.is_alive(),
            "save_alive": save_thread.is_alive(),
            "operation_result": operation_result,
            "operation_errors": operation_errors,
            "save_errors": save_errors,
            "captured_configs": captured_configs,
        }

    def test_service_auto_selects_v365_for_context_push_run_and_report(self):
        context = metersphere_service.metersphere_execution_context(force=True)
        first_push = metersphere_service.push_plan_to_metersphere(self.plan["plan_id"])
        second_push = metersphere_service.push_plan_to_metersphere(self.plan["plan_id"])
        run = metersphere_service.create_metersphere_run(self.plan["plan_id"])
        self.remote.report.update({
            "execStatus": "COMPLETED",
            "status": "SUCCESS",
            "stepSuccessCount": 1,
            "stepPendingCount": 0,
            "children": [{
                "stepId": str(uuid.uuid5(uuid.NAMESPACE_URL, "midscene:api-plan-pets:API-001")),
                "name": "更新宠物成功",
                "status": "SUCCESS",
                "requestTime": 91,
            }],
        })
        report = metersphere_service.pull_metersphere_report(run["run_id"])

        self.assertEqual(context["connection"]["state"], "connected")
        self.assertEqual(context["capabilities"]["adapter"], "metersphere_v3.6.5")
        self.assertTrue(context["capabilities"]["ready"])
        self.assertTrue(context["readiness"]["can_execute"])
        self.assertEqual(context["businesses"], [{"id": "project-a", "name": "业务A", "enabled": True}])
        self.assertEqual(context["environments"][0]["id"], "env-a")
        self.assertTrue(first_push["ok"])
        self.assertEqual(first_push["adapter"], "metersphere_v3.6.5")
        self.assertEqual(first_push["case_result"]["created"], 1)
        self.assertEqual(first_push["scenario_result"]["created"], 1)
        self.assertEqual(second_push["case_result"]["unchanged"], 1)
        self.assertEqual(second_push["scenario_result"]["unchanged"], 1)
        self.assertEqual(run["run_id"], self.remote.last_trigger_report_id)
        self.assertEqual(run["adapter"], "metersphere_v3.6.5")
        self.assertTrue(report["ok"])
        self.assertEqual(report["report"]["plan_id"], self.plan["plan_id"])
        self.assertEqual(report["report"]["summary"], {"total": 1, "passed": 1, "failed": 0})
        guessed_paths = {
            "/guessed/health",
            "/guessed/projects",
            "/guessed/environments",
            "/guessed/cases",
            "/guessed/run",
            "/guessed/status/remote-report-1",
            "/guessed/report/remote-report-1",
        }
        self.assertFalse(any(path in guessed_paths for _method, path, _payload in self.remote.calls))

    def test_execution_snapshots_source_specific_binding_before_worker_starts(self):
        execution, binding = self._start_source_execution()

        self.assertEqual(execution["source_id"], "api_source_pets")
        self.assertEqual(execution["binding_id"], binding["binding_id"])
        self.assertEqual(execution["project_id"], "project-a")
        self.assertEqual(execution["environment_id"], "env-a")
        self.assertEqual(execution["binding_fingerprint"], binding["config_fingerprint"])
        self.assertRegex(execution.get("connection_fingerprint") or "", r"^[0-9a-f]{64}$")
        serialized = json.dumps(execution, ensure_ascii=False)
        self.assertNotIn("1234567890abcdef", serialized)
        self.assertNotIn("abcdef1234567890", serialized)

    def test_worker_rejects_preexisting_base_url_drift_before_remote_read(self):
        failed, config_reads, network_calls = self._run_worker_during_connection_change(
            base_url="http://other-metersphere.example.test",
        )

        self.assertEqual(len(config_reads), 1)
        self.assertEqual(network_calls, [])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("连接配置已变更", failed["error"])

    def test_worker_rejects_preexisting_access_key_drift_before_remote_read(self):
        failed, config_reads, network_calls = self._run_worker_during_connection_change(
            access_key="fedcba0987654321",
        )

        self.assertEqual(len(config_reads), 1)
        self.assertEqual(network_calls, [])
        self.assertEqual(failed["status"], "failed")
        self.assertIn("连接配置已变更", failed["error"])

    def test_worker_makes_no_requests_after_reviewer_fifth_read_drift(self):
        execution, _binding = self._start_source_execution()
        api_test_plan_service.list_api_test_plans = lambda limit=50: [{
            "plan_id": self.plan["plan_id"],
            "name": self.plan["name"],
            "status": "confirmed",
            "source_id": self.plan["source_id"],
            "execution_readiness": {"can_execute": True, "executable_case_count": 1},
        }]
        baseline = metersphere_service._load_raw_config()
        drifted = {
            **baseline,
            "base_url": "http://rotated-metersphere.example.test",
            "access_key": "fedcba0987654321",
            "secret_key": "0123456789abcdef",
        }
        config_reads = []
        post_drift_requests = []
        drift_active = threading.Event()
        old_load_config = metersphere_service._load_raw_config
        old_request = metersphere_service._request_json

        def changing_config():
            config_reads.append(len(config_reads) + 1)
            if len(config_reads) >= 5:
                drift_active.set()
                return dict(drifted)
            return dict(baseline)

        def tracked_request(method, path, payload=None, timeout=30, *, config=None):
            if drift_active.is_set():
                post_drift_requests.append((method, path, copy.deepcopy(config or {})))
            return self.remote.request(method, path, payload, timeout)

        metersphere_service._load_raw_config = changing_config
        metersphere_service._request_json = tracked_request
        try:
            metersphere_service._run_metersphere_execution_guarded(
                execution["execution_id"],
            )
            while len(config_reads) < 5:
                changing_config()
        finally:
            metersphere_service._load_raw_config = old_load_config
            metersphere_service._request_json = old_request

        record = metersphere_service._load_execution(execution["execution_id"])
        self.assertTrue(drift_active.is_set())
        self.assertEqual(post_drift_requests, [])
        self.assertTrue(
            record.get("run_id") or "连接配置已变更" in str(record.get("error") or ""),
        )
        serialized = json.dumps(record, ensure_ascii=False)
        for secret in (
            baseline["access_key"],
            baseline["secret_key"],
            drifted["access_key"],
            drifted["secret_key"],
        ):
            self.assertNotIn(secret, serialized)

    def test_source_v365_push_serializes_connection_save(self):
        execution, _binding = self._start_source_execution()
        baseline = metersphere_service._load_raw_config()
        updated = {
            **baseline,
            "base_url": "http://rotated-metersphere.example.test",
            "access_key": "fedcba0987654321",
            "secret_key": "0123456789abcdef",
        }

        observed = self._run_source_phase_during_config_save(
            lambda: metersphere_service.push_plan_to_metersphere(
                self.plan["plan_id"],
            ),
            self.remote.request,
            updated,
        )

        self.assertTrue(observed["entered"])
        self.assertFalse(observed["saved_while_remote_blocked"])
        self.assertTrue(observed["save_finished"])
        self.assertFalse(observed["operation_alive"])
        self.assertFalse(observed["save_alive"])
        self.assertEqual(observed["operation_errors"], [])
        self.assertEqual(observed["save_errors"], [])
        self.assertTrue(observed["operation_result"].get("ok"))
        self.assertTrue(observed["captured_configs"])
        self.assertTrue(all(
            cfg.get("base_url") == baseline["base_url"]
            and cfg.get("access_key") == baseline["access_key"]
            and cfg.get("secret_key") == baseline["secret_key"]
            and cfg.get("project_id") == execution["project_id"]
            and cfg.get("environment_id") == execution["environment_id"]
            for cfg in observed["captured_configs"]
        ))
        serialized = json.dumps({
            "execution": metersphere_service._load_execution(execution["execution_id"]),
            "result": observed["operation_result"],
        }, ensure_ascii=False)
        for secret in (
            baseline["access_key"],
            baseline["secret_key"],
            updated["access_key"],
            updated["secret_key"],
        ):
            self.assertNotIn(secret, serialized)

    def test_source_legacy_run_serializes_connection_save(self):
        baseline = self._token_connection()
        metersphere_service.save_metersphere_config(baseline)
        execution, _binding = self._start_source_execution()
        updated = {
            **baseline,
            "base_url": "http://rotated-metersphere.example.test",
            "token": "connection-token-b",
        }

        def legacy_run(method, path, payload=None, timeout=30):
            return {"ok": True, "data": {"id": "legacy-run-locked"}}

        observed = self._run_source_phase_during_config_save(
            lambda: metersphere_service.create_metersphere_run(
                self.plan["plan_id"],
            ),
            legacy_run,
            updated,
        )

        self.assertTrue(observed["entered"])
        self.assertFalse(observed["saved_while_remote_blocked"])
        self.assertTrue(observed["save_finished"])
        self.assertEqual(observed["operation_errors"], [])
        self.assertEqual(observed["save_errors"], [])
        self.assertEqual(observed["operation_result"].get("run_id"), "legacy-run-locked")
        self.assertTrue(all(
            cfg.get("base_url") == baseline["base_url"]
            and cfg.get("token") == baseline["token"]
            and cfg.get("project_id") == execution["project_id"]
            and cfg.get("environment_id") == execution["environment_id"]
            for cfg in observed["captured_configs"]
        ))
        serialized = json.dumps({
            "execution": metersphere_service._load_execution(execution["execution_id"]),
            "result": observed["operation_result"],
        }, ensure_ascii=False)
        self.assertNotIn(baseline["token"], serialized)
        self.assertNotIn(updated["token"], serialized)

    def test_source_report_serializes_connection_save(self):
        baseline = self._token_connection()
        metersphere_service.save_metersphere_config(baseline)
        execution, _binding = self._start_source_execution()
        updated = {
            **baseline,
            "base_url": "http://rotated-metersphere.example.test",
            "token": "connection-token-b",
        }

        def legacy_report(method, path, payload=None, timeout=30):
            return {
                "ok": True,
                "data": {"results": [{"id": "case-1", "status": "passed"}]},
            }

        observed = self._run_source_phase_during_config_save(
            lambda: metersphere_service.pull_metersphere_report(
                execution["run_id"],
                execution_id=execution["execution_id"],
            ),
            legacy_report,
            updated,
        )

        self.assertTrue(observed["entered"])
        self.assertFalse(observed["saved_while_remote_blocked"])
        self.assertTrue(observed["save_finished"])
        self.assertEqual(observed["operation_errors"], [])
        self.assertEqual(observed["save_errors"], [])
        self.assertTrue(observed["operation_result"].get("ok"))
        self.assertTrue(all(
            cfg.get("base_url") == baseline["base_url"]
            and cfg.get("token") == baseline["token"]
            and cfg.get("project_id") == execution["project_id"]
            and cfg.get("environment_id") == execution["environment_id"]
            for cfg in observed["captured_configs"]
        ))

    def test_manual_report_pull_uses_execution_snapshot_after_global_selection_changes(self):
        captured = []

        def report_request(method, path, payload=None, timeout=30, *, config=None):
            captured.append((method, path, config))
            return {"ok": True, "data": {
                "results": [{"id": "case-1", "name": "用例", "status": "passed"}],
            }}

        metersphere_service.save_metersphere_config(self._token_connection(
            project_id="project-b",
            environment_id="env-b",
        ))
        execution, _binding = self._start_source_execution()
        metersphere_service._request_json = report_request

        result = metersphere_service.pull_metersphere_report(
            "run-source-a",
            execution_id=execution["execution_id"],
        )

        self.assertTrue(result["ok"])
        self.assertEqual(captured[0][2]["project_id"], "project-a")
        self.assertNotEqual(captured[0][2]["project_id"], "project-b")

    def test_source_report_fails_before_network_when_connection_changes(self):
        baseline = self._token_connection()
        metersphere_service.save_metersphere_config(baseline)
        execution, _binding = self._start_source_execution()
        calls = []

        def report_request(method, path, payload=None, timeout=30, *, config=None):
            calls.append((method, path, config))
            return {"ok": True, "data": {
                "results": [{"id": "case-1", "name": "用例", "status": "passed"}],
            }}

        metersphere_service._request_json = report_request
        changes = {
            "base_url": "http://other-metersphere.example.test",
            "token": "connection-token-b",
            "workspace_id": "org-b",
            "case_push_path": "/other/cases",
            "plan_run_path": "/other/runs",
            "run_status_path": "/other/runs/{run_id}",
            "report_path": "/other/reports/{run_id}",
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                metersphere_service.save_metersphere_config({**baseline, field: value})
                calls.clear()

                result = metersphere_service.pull_metersphere_report(
                    execution["run_id"],
                    execution_id=execution["execution_id"],
                )

                self.assertFalse(result["ok"])
                self.assertIn("连接配置已变更", result["error"])
                self.assertEqual(calls, [])

        metersphere_service.save_metersphere_config(baseline)
        calls.clear()
        result = metersphere_service.pull_metersphere_report(
            execution["run_id"],
            execution_id=execution["execution_id"],
        )
        self.assertTrue(result["ok"])
        self.assertEqual(calls[0][2]["project_id"], "project-a")
        self.assertEqual(calls[0][2]["environment_id"], "env-a")

    def test_source_poll_fails_before_network_when_connection_changes(self):
        baseline = self._token_connection()
        metersphere_service.save_metersphere_config(baseline)
        execution, _binding = self._start_source_execution()
        calls = []

        def status_request(method, path, payload=None, timeout=30, *, config=None):
            calls.append((method, path, config))
            return {"ok": True, "data": {"status": "RUNNING"}}

        metersphere_service._request_json = status_request
        changes = {
            "base_url": "http://other-metersphere.example.test",
            "token": "connection-token-b",
            "workspace_id": "org-b",
            "case_push_path": "/other/cases",
            "plan_run_path": "/other/runs",
            "run_status_path": "/other/runs/{run_id}",
            "report_path": "/other/reports/{run_id}",
        }
        for field, value in changes.items():
            with self.subTest(field=field):
                metersphere_service.save_metersphere_config({**baseline, field: value})
                calls.clear()

                refreshed = metersphere_service._refresh_running_execution(copy.deepcopy(execution))

                self.assertEqual(refreshed["status"], "failed")
                self.assertIn("连接配置已变更", refreshed["error"])
                self.assertEqual(calls, [])

        metersphere_service.save_metersphere_config(baseline)
        calls.clear()
        refreshed = metersphere_service._refresh_running_execution(copy.deepcopy(execution))
        self.assertEqual(refreshed["status"], "running")
        self.assertEqual(calls[0][2]["project_id"], "project-a")
        self.assertEqual(calls[0][2]["environment_id"], "env-a")

    def test_legacy_execution_id_cannot_bypass_source_run_ownership(self):
        metersphere_service.save_metersphere_config(self._token_connection())
        source_execution, _binding = self._start_source_execution(run_id="run-shared")
        metersphere_service._save_execution({
            "execution_id": "execution-legacy-collision",
            "source_id": "",
            "project_id": "project-global",
            "environment_id": "env-global",
            "run_id": source_execution["run_id"],
            "adapter": "legacy",
        })
        calls = []
        metersphere_service._request_json = lambda *args, **kwargs: calls.append((args, kwargs)) or {
            "ok": True,
            "data": {"results": []},
        }

        result = metersphere_service.pull_metersphere_report(
            source_execution["run_id"],
            execution_id="execution-legacy-collision",
        )

        self.assertFalse(result["ok"])
        self.assertIn("source", result["error"])
        self.assertEqual(calls, [])

    def test_pure_legacy_execution_id_keeps_global_report_compatibility(self):
        metersphere_service.save_metersphere_config(self._token_connection())
        metersphere_service._save_execution({
            "execution_id": "execution-legacy-only",
            "source_id": "",
            "run_id": "run-legacy-only",
            "adapter": "legacy",
        })
        calls = []

        def report_request(method, path, payload=None, timeout=30, *, config=None):
            calls.append((method, path, config))
            return {"ok": True, "data": {"results": []}}

        metersphere_service._request_json = report_request

        result = metersphere_service.pull_metersphere_report(
            "run-legacy-only",
            execution_id="execution-legacy-only",
        )

        self.assertTrue(result["ok"])
        self.assertEqual(len(calls), 1)

    def test_context_does_not_fake_all_businesses_when_exact_project_options_fail(self):
        original_request = self.remote.request

        def failed_project_options(method, path, payload=None, timeout=30, *, config=None):
            if path == "/project/list/options/org-a":
                return {"ok": False, "error": "project options unavailable"}
            return original_request(method, path, payload, timeout)

        metersphere_service._request_json = failed_project_options
        try:
            context = metersphere_service.metersphere_execution_context(force=True)
        finally:
            metersphere_service._request_json = self.remote.request

        self.assertEqual(context["businesses"], [])
        self.assertFalse(context["readiness"]["can_execute"])
        self.assertIn("MeterSphere 当前业务不可用", context["metadata"]["errors"])

    def test_v365_execution_refresh_uses_report_status_without_manual_status_path(self):
        push = metersphere_service.push_plan_to_metersphere(self.plan["plan_id"])
        run = metersphere_service.create_metersphere_run(self.plan["plan_id"])
        record = {
            "execution_id": "ms-execution-v365",
            "plan_id": self.plan["plan_id"],
            "status": "running",
            "adapter": "metersphere_v3.6.5",
            "run_id": run["run_id"],
            "remote_status": "running",
            "report_status": "waiting",
            "stats": {"total": 0, "passed": 0, "failed": 0},
            "phases": [
                {"id": "push_cases", "state": "succeeded"},
                {"id": "trigger_plan", "state": "succeeded"},
                {"id": "metersphere_run", "state": "running", "started_at": "2026-07-23 10:00:00"},
                {"id": "sync_report", "state": "waiting"},
            ],
            "events": [],
            "created_at": "2026-07-23 10:00:00",
            "started_at": "2026-07-23 10:00:00",
            "updated_at": "2026-07-23 10:00:00",
            "finished_at": "",
            "unchanged_polls": 0,
            "status_poll_failures": 0,
        }
        metersphere_service._save_execution(record)
        running = metersphere_service.get_metersphere_execution(record["execution_id"], refresh=True)
        self.remote.report.update({
            "execStatus": "COMPLETED",
            "status": "SUCCESS",
            "stepSuccessCount": 1,
            "stepPendingCount": 0,
            "children": [],
        })
        completed = metersphere_service.get_metersphere_execution(record["execution_id"], refresh=True)

        self.assertTrue(push["ok"])
        self.assertEqual(running["status"], "running")
        self.assertEqual(completed["status"], "succeeded")
        self.assertEqual(completed["remote_status"], "succeeded")
        self.assertEqual(completed["report_status"], "succeeded")
        self.assertTrue(completed["report_id"])
        self.assertFalse(any("/guessed/status" in path for _method, path, _payload in self.remote.calls))

    def test_failed_v365_run_still_syncs_failure_report(self):
        push = metersphere_service.push_plan_to_metersphere(self.plan["plan_id"])
        run = metersphere_service.create_metersphere_run(self.plan["plan_id"])
        record = {
            "execution_id": "ms-execution-v365-failed",
            "plan_id": self.plan["plan_id"],
            "status": "running",
            "adapter": "metersphere_v3.6.5",
            "run_id": run["run_id"],
            "remote_status": "running",
            "report_status": "waiting",
            "stats": {"total": 0, "passed": 0, "failed": 0},
            "phases": [
                {"id": "push_cases", "state": "succeeded"},
                {"id": "trigger_plan", "state": "succeeded"},
                {"id": "metersphere_run", "state": "running", "started_at": "2026-07-23 10:00:00"},
                {"id": "sync_report", "state": "waiting"},
            ],
            "events": [],
            "created_at": "2026-07-23 10:00:00",
            "started_at": "2026-07-23 10:00:00",
            "updated_at": "2026-07-23 10:00:00",
            "finished_at": "",
            "unchanged_polls": 0,
            "status_poll_failures": 0,
        }
        metersphere_service._save_execution(record)
        step_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "midscene:api-plan-pets:API-001"))
        self.remote.report.update({
            "execStatus": "COMPLETED",
            "status": "FAILED",
            "stepSuccessCount": 0,
            "stepErrorCount": 1,
            "stepPendingCount": 0,
            "children": [{
                "stepId": step_id,
                "name": "更新宠物成功",
                "status": "ERROR",
                "message": "HTTP 状态断言失败",
                "requestTime": 73,
            }],
        })

        failed = metersphere_service.get_metersphere_execution(
            record["execution_id"],
            refresh=True,
        )

        phases = {item["id"]: item["state"] for item in failed["phases"]}
        self.assertTrue(push["ok"])
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["remote_status"], "failed")
        self.assertEqual(failed["report_status"], "succeeded")
        self.assertTrue(failed["report_id"])
        self.assertEqual(phases["metersphere_run"], "failed")
        self.assertEqual(phases["sync_report"], "succeeded")
        self.assertIn("MeterSphere 执行失败", failed["error"])

    def test_stalled_provider_terminal_state_ends_execution_and_syncs_report(self):
        metersphere_service.push_plan_to_metersphere(self.plan["plan_id"])
        run = metersphere_service.create_metersphere_run(self.plan["plan_id"])
        record = {
            "execution_id": "ms-execution-v365-stalled",
            "plan_id": self.plan["plan_id"],
            "status": "running",
            "adapter": "metersphere_v3.6.5",
            "run_id": run["run_id"],
            "remote_status": "running",
            "report_status": "waiting",
            "stats": {"total": 0, "passed": 0, "failed": 0},
            "phases": [
                {"id": "push_cases", "state": "succeeded"},
                {"id": "trigger_plan", "state": "succeeded"},
                {"id": "metersphere_run", "state": "running", "started_at": "2026-07-23 10:00:00"},
                {"id": "sync_report", "state": "waiting"},
            ],
            "events": [],
            "created_at": "2026-07-23 10:00:00",
            "started_at": "2026-07-23 10:00:00",
            "updated_at": "2026-07-23 10:00:00",
            "finished_at": "",
            "unchanged_polls": 0,
            "status_poll_failures": 0,
        }
        metersphere_service._save_execution(record)
        step_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "midscene:api-plan-pets:API-001"))
        self.remote.report.update({
            "execStatus": "PENDING",
            "status": "",
            "startTime": 1,
            "endTime": None,
            "requestTotal": 0,
            "stepSuccessCount": 1,
            "stepErrorCount": 0,
            "stepPendingCount": 0,
            "children": [{
                "stepId": step_id,
                "stepType": "API_CASE",
                "name": "更新宠物成功",
                "status": "SUCCESS",
                "code": "200",
                "requestTime": 73,
            }],
        })

        failed = metersphere_service.get_metersphere_execution(
            record["execution_id"],
            refresh=True,
        )

        phases = {item["id"]: item["state"] for item in failed["phases"]}
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["remote_status"], "failed")
        self.assertEqual(failed["report_status"], "succeeded")
        self.assertEqual(phases["metersphere_run"], "failed")
        self.assertEqual(phases["sync_report"], "succeeded")
        self.assertIn("未回写终态", json.dumps(failed["events"], ensure_ascii=False))


if __name__ == "__main__":
    unittest.main(verbosity=2)
