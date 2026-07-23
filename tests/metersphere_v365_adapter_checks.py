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
from task_server.services import api_report_service, api_test_plan_service


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
            "auth_ref": "environment_default",
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
        self.old_request = metersphere_service._request_json
        self.old_get_plan = api_test_plan_service.get_api_test_plan
        self.old_list_plans = api_test_plan_service.list_api_test_plans
        metersphere_service.API_TESTING_DIR = self.temp_dir
        api_report_service.API_TESTING_DIR = self.temp_dir
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
        metersphere_service._request_json = self.old_request
        api_test_plan_service.get_api_test_plan = self.old_get_plan
        api_test_plan_service.list_api_test_plans = self.old_list_plans
        shutil.rmtree(self.temp_dir, ignore_errors=True)

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
