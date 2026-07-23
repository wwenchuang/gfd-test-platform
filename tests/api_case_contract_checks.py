#!/usr/bin/env python3
"""Focused checks for the executable API case contract."""

from __future__ import annotations

import copy
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

from task_server.services import api_case_contract_service


def _endpoint(**overrides):
    endpoint = {
        "endpoint_id": "api_pet_get",
        "endpoint_key": "apifox:pet-get",
        "asset_revision_id": "api_revision_1",
        "method": "POST",
        "path": "/pets/{petId}",
        "module": "pets",
        "name": "更新宠物",
        "parameters": [
            {
                "name": "petId",
                "in": "path",
                "required": True,
                "schema": {"type": "string", "example": "pet-001"},
            },
            {
                "name": "notify",
                "in": "query",
                "required": True,
                "schema": {"type": "boolean", "default": True},
            },
        ],
        "request_body_required": True,
        "request_schema": {
            "type": "object",
            "required": ["name", "kind"],
            "properties": {
                "name": {"type": "string", "example": "豆豆"},
                "kind": {"type": "string", "enum": ["cat", "dog"]},
            },
        },
        "responses": [
            {
                "status": "200",
                "description": "updated",
                "schema": {
                    "type": "object",
                    "required": ["id"],
                    "properties": {"id": {"type": "string"}},
                },
            },
            {"status": "400", "description": "invalid request", "schema": {}},
            {"status": "401", "description": "unauthorized", "schema": {}},
        ],
        "response_schema": {
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "string"}},
        },
        "required_fields": ["petId", "notify", "name", "kind"],
        "security": [{"bearerAuth": []}],
    }
    endpoint.update(overrides)
    return endpoint


class ApiCaseContractChecks(unittest.TestCase):
    def test_sensitive_openapi_header_example_never_enters_case(self):
        endpoint = _endpoint(parameters=[
            {
                "name": "Authorization",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "example": "Bearer leaked"},
            },
            {
                "name": "X-API-Key",
                "in": "header",
                "required": True,
                "schema": {"type": "string", "default": "api-key-leaked"},
            },
        ])

        contract = api_case_contract_service.build_api_case_contract(endpoint, "positive")

        serialized = json.dumps(contract, ensure_ascii=False)
        self.assertNotIn("leaked", serialized)
        self.assertNotIn("Authorization", contract["request"]["headers"])
        self.assertNotIn("X-API-Key", contract["request"]["headers"])

    def test_nested_sensitive_schema_values_never_enter_contract(self):
        endpoint = _endpoint(
            parameters=[
                {"name": "accessToken", "in": "query", "required": True,
                 "schema": {"type": "string", "example": "query-token-secret"}},
                {"name": "credential", "in": "path", "required": True,
                 "schema": {"type": "string", "default": "path-secret"}},
            ],
            request_schema={
                "type": "object",
                "example": {
                    "profile": {
                        "apiKey": "root-nested-api-key",
                        "password": "root-nested-password",
                        "displayName": "保留公开值",
                    },
                },
                "required": ["profile"],
                "properties": {
                    "profile": {
                        "type": "object",
                        "required": ["apiKey", "password"],
                        "properties": {
                            "apiKey": {"type": "string", "example": "nested-api-key"},
                            "password": {"type": "string", "default": "nested-password"},
                            "displayName": {"type": "string"},
                        },
                    },
                },
            },
            required_fields=["accessToken", "credential", "profile"],
        )

        contract = api_case_contract_service.build_api_case_contract(endpoint, "positive")

        serialized = json.dumps(contract, ensure_ascii=False)
        for secret in (
            "query-token-secret", "path-secret", "nested-api-key", "nested-password",
            "root-nested-api-key", "root-nested-password",
        ):
            self.assertNotIn(secret, serialized)
        self.assertEqual(contract["request"]["body"]["profile"]["displayName"], "保留公开值")
        self.assertEqual(contract["readiness"]["state"], "needs_review")
        self.assertIn("request.query.accessToken", contract["readiness"]["missing"])
        self.assertIn("request.path_params.credential", contract["readiness"]["missing"])
        self.assertIn("request.body.profile.apiKey", contract["readiness"]["missing"])


    def test_positive_contract_uses_only_explicit_openapi_values(self):
        case = api_case_contract_service.build_api_case_contract(
            _endpoint(),
            "positive",
        )

        self.assertEqual(case["contract_version"], "api_case_contract/v1")
        self.assertEqual(case["request"]["method"], "POST")
        self.assertEqual(case["request"]["path"], "/pets/{petId}")
        self.assertEqual(case["request"]["path_params"], {"petId": "pet-001"})
        self.assertEqual(case["request"]["query"], {"notify": True})
        self.assertEqual(case["request"]["body"], {"name": "豆豆", "kind": "cat"})
        self.assertEqual(case["request"]["auth_ref"], "environment_default")
        self.assertEqual(case["readiness"]["state"], "executable")
        self.assertEqual(case["readiness"]["missing"], [])
        self.assertIn(
            {"type": "status", "operator": "in", "expected": [200]},
            case["assertions"],
        )
        self.assertIn(
            {"type": "schema", "schema_ref": "response:2xx"},
            case["assertions"],
        )

    def test_missing_required_values_are_review_items_not_placeholders(self):
        endpoint = _endpoint(
            parameters=[{
                "name": "petId",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }],
            request_schema={
                "type": "object",
                "required": ["ownerId"],
                "properties": {"ownerId": {"type": "string"}},
            },
            required_fields=["petId", "ownerId"],
        )

        case = api_case_contract_service.build_api_case_contract(endpoint, "positive")

        self.assertEqual(case["readiness"]["state"], "needs_review")
        self.assertEqual(
            case["readiness"]["missing"],
            ["request.body.ownerId", "request.path_params.petId"],
        )
        self.assertNotIn("petId", case["request"]["path_params"])
        self.assertNotIn("ownerId", case["request"]["body"])
        self.assertNotIn("placeholder", str(case).lower())

    def test_negative_case_intentionally_omits_only_the_target_field(self):
        case = api_case_contract_service.build_api_case_contract(
            _endpoint(),
            "negative",
            omitted_field="body.kind",
        )

        self.assertEqual(case["request"]["body"], {"name": "豆豆"})
        self.assertEqual(case["readiness"]["state"], "executable")
        self.assertEqual(case["negative_target"], "body.kind")
        self.assertIn(
            {"type": "status", "operator": "in", "expected": [400]},
            case["assertions"],
        )

    def test_negative_case_requires_a_real_required_target(self):
        missing_target = api_case_contract_service.build_api_case_contract(
            _endpoint(),
            "negative",
        )
        invented_target = api_case_contract_service.build_api_case_contract(
            _endpoint(),
            "negative",
            omitted_field="body.notDeclared",
        )

        self.assertEqual(missing_target["readiness"]["state"], "needs_review")
        self.assertIn("negative_target", missing_target["readiness"]["missing"])
        self.assertEqual(invented_target["readiness"]["state"], "needs_review")
        self.assertIn("negative_target", invented_target["readiness"]["missing"])

    def test_auth_case_drops_auth_binding_and_uses_documented_auth_statuses(self):
        case = api_case_contract_service.build_api_case_contract(_endpoint(), "auth")

        self.assertEqual(case["request"]["auth_ref"], "")
        self.assertEqual(case["readiness"]["state"], "executable")
        self.assertIn(
            {"type": "status", "operator": "in", "expected": [401]},
            case["assertions"],
        )

    def test_optional_openapi_security_alternative_does_not_require_auth(self):
        endpoint = _endpoint(security=[{}, {"bearerAuth": []}])

        case = api_case_contract_service.build_api_case_contract(endpoint, "positive")

        self.assertEqual(case["request"]["auth_ref"], "")
        self.assertFalse(api_case_contract_service.endpoint_requires_auth(endpoint))

    def test_auth_case_requires_an_openapi_security_requirement(self):
        endpoint = _endpoint(security=[])

        case = api_case_contract_service.build_api_case_contract(endpoint, "auth")

        self.assertEqual(case["readiness"]["state"], "needs_review")
        self.assertIn("endpoint.security", case["readiness"]["missing"])

    def test_ai_proposal_cannot_override_route_or_invent_required_data(self):
        endpoint = _endpoint(
            parameters=[{
                "name": "petId",
                "in": "path",
                "required": True,
                "schema": {"type": "string"},
            }],
            request_schema={
                "type": "object",
                "required": ["ownerId"],
                "properties": {"ownerId": {"type": "string"}},
            },
            required_fields=["petId", "ownerId"],
        )
        proposed = {
            "request": {
                "method": "DELETE",
                "path": "/admin/all",
                "path_params": {"petId": "invented-id"},
                "body": {"ownerId": "invented-owner"},
            },
            "readiness": {"state": "executable", "missing": []},
            "assertions": [{"type": "status", "operator": "in", "expected": [204]}],
        }

        case = api_case_contract_service.normalize_api_case_contract(
            proposed,
            endpoint,
        )

        self.assertEqual(case["request"]["method"], "POST")
        self.assertEqual(case["request"]["path"], "/pets/{petId}")
        self.assertEqual(case["request"]["path_params"], {})
        self.assertEqual(case["request"]["body"], {})
        self.assertEqual(case["readiness"]["state"], "needs_review")
        self.assertEqual(
            case["readiness"]["missing"],
            ["request.body.ownerId", "request.path_params.petId"],
        )
        self.assertNotIn("204", str(case["assertions"]))

    def test_unsupported_required_parameter_location_is_explicitly_blocked(self):
        endpoint = _endpoint(parameters=[{
            "name": "session",
            "in": "cookie",
            "required": True,
            "schema": {"type": "string", "example": "session-example"},
        }])

        case = api_case_contract_service.build_api_case_contract(endpoint, "positive")

        self.assertEqual(case["readiness"]["state"], "needs_review")
        self.assertIn("request.parameters.cookie.session", case["readiness"]["missing"])
        self.assertIn(
            "unsupported_parameter_location:cookie:session",
            case["readiness"]["issues"],
        )

    def test_unknown_dependencies_block_execution(self):
        proposed = {
            "dependencies": [{"case_id": "API-UNKNOWN", "required": True}],
        }

        case = api_case_contract_service.normalize_api_case_contract(
            proposed,
            _endpoint(),
            known_case_ids={"API-KNOWN"},
        )

        self.assertEqual(case["readiness"]["state"], "needs_review")
        self.assertIn("dependencies.API-UNKNOWN", case["readiness"]["missing"])

    def test_optional_request_body_without_root_example_does_not_block(self):
        endpoint = _endpoint(
            request_body_required=False,
            request_schema={
                "type": "object",
                "required": ["ownerId"],
                "properties": {"ownerId": {"type": "string"}},
            },
            required_fields=["petId"],
        )

        case = api_case_contract_service.build_api_case_contract(endpoint, "positive")

        self.assertEqual(case["request"]["body"], {})
        self.assertNotIn("request.body.ownerId", case["readiness"]["missing"])
        self.assertEqual(case["readiness"]["state"], "executable")

    def test_chain_case_uses_documented_success_contract(self):
        proposed = {
            "type": "chain",
            "dependencies": [{"case_id": "API-SETUP", "required": True}],
        }

        case = api_case_contract_service.normalize_api_case_contract(
            proposed,
            _endpoint(),
            known_case_ids={"API-SETUP"},
        )

        self.assertEqual(case["readiness"]["state"], "executable")
        self.assertIn(
            {"type": "status", "operator": "in", "expected": [200]},
            case["assertions"],
        )
        self.assertIn(
            {"type": "schema", "schema_ref": "response:2xx"},
            case["assertions"],
        )

    def test_readiness_summary_is_deterministic(self):
        executable = api_case_contract_service.build_api_case_contract(_endpoint(), "positive")
        review = copy.deepcopy(executable)
        review["readiness"] = {
            "state": "needs_review",
            "missing": ["request.body.ownerId"],
            "issues": [],
        }

        summary = api_case_contract_service.summarize_api_case_readiness([executable, review])

        self.assertEqual(summary["case_count"], 2)
        self.assertEqual(summary["executable_case_count"], 1)
        self.assertEqual(summary["needs_review_case_count"], 1)
        self.assertTrue(summary["can_confirm"])
        self.assertEqual(summary["missing"], ["request.body.ownerId"])


def _openapi_document(response_type="string", include_health=False):
    paths = {
        "/pets/{petId}": {
            "post": {
                "x-apifox-id": "pet-get",
                "tags": ["pets"],
                "summary": "更新宠物",
                "parameters": [{
                    "name": "petId",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string", "example": "pet-001"},
                }],
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["name"],
                                "properties": {
                                    "name": {"type": "string", "example": "豆豆"},
                                },
                            }
                        }
                    },
                },
                "security": [{"bearerAuth": []}],
                "responses": {
                    "200": {
                        "description": "updated",
                        "content": {
                            "application/json": {
                                "schema": {
                                    "type": "object",
                                    "properties": {"id": {"type": response_type}},
                                }
                            }
                        },
                    },
                    "400": {"description": "invalid"},
                    "401": {"description": "unauthorized"},
                },
            }
        }
    }
    if include_health:
        paths["/health"] = {
            "get": {
                "x-apifox-id": "health",
                "summary": "健康检查",
                "responses": {"200": {"description": "ok"}},
            }
        }
    return {
        "openapi": "3.0.0",
        "info": {"title": "宠物接口", "version": "1.0"},
        "paths": paths,
    }


class ApiPlanContractChecks(unittest.TestCase):
    def setUp(self):
        from task_server.services import api_asset_service, api_source_service, api_test_plan_service, api_workspace_service

        self.asset_service = api_asset_service
        self.source_service = api_source_service
        self.plan_service = api_test_plan_service
        self.workspace_service = api_workspace_service
        self.old_asset_dir = api_asset_service.API_TESTING_DIR
        self.old_source_dir = api_source_service.API_TESTING_DIR
        self.old_plan_dir = api_test_plan_service.API_TESTING_DIR
        self.old_workspace_dir = api_workspace_service.API_TESTING_DIR
        self.temp_dir = tempfile.mkdtemp(prefix="api_case_plan_checks_")
        api_asset_service.API_TESTING_DIR = self.temp_dir
        api_source_service.API_TESTING_DIR = self.temp_dir
        api_test_plan_service.API_TESTING_DIR = self.temp_dir
        api_workspace_service.API_TESTING_DIR = self.temp_dir
        api_source_service.save_api_source({
            "source_id": "source-pets",
            "name": "宠物接口",
            "project_id": "pets",
            "access_token": "test-token",
        })
        api_workspace_service.save_api_workspace_binding(
            "source-pets", "ms-pets", "env-pets",
        )
        api_workspace_service.save_api_auth_binding_metadata(
            "source-pets",
            auth_type="bearer",
            header_name="Authorization",
            auth_ref="api_auth_pets",
            variable_name="MTP_API_AUTH_PETS",
            environment_id="env-pets",
        )

    def tearDown(self):
        self.asset_service.API_TESTING_DIR = self.old_asset_dir
        self.source_service.API_TESTING_DIR = self.old_source_dir
        self.plan_service.API_TESTING_DIR = self.old_plan_dir
        self.workspace_service.API_TESTING_DIR = self.old_workspace_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _activate(self, document):
        staged = self.asset_service.stage_api_revision(
            "source-pets",
            "宠物接口",
            document,
            source_type="apifox",
        )
        self.asset_service.activate_api_revision(
            staged["asset_id"],
            staged["revision_id"],
        )
        return staged

    def test_local_plan_contains_contract_counts_and_can_be_confirmed(self):
        staged = self._activate(_openapi_document())

        plan = self.plan_service.generate_api_test_plan(
            staged["revision_id"],
            [],
            use_ai=False,
        )

        self.assertEqual(plan["contract_version"], "api_case_contract/v1")
        self.assertEqual(plan["execution_readiness"]["state"], "ready")
        self.assertEqual(plan["executable_case_count"], plan["case_count"])
        self.assertEqual(plan["needs_review_case_count"], 0)
        self.assertTrue(all(case.get("request") for case in plan["cases"]))
        self.assertTrue(all(isinstance(case.get("assertions"), list) for case in plan["cases"]))

        confirmed = self.plan_service.confirm_api_test_plan(plan["plan_id"])

        self.assertEqual(confirmed["status"], "confirmed")
        self.assertTrue(confirmed["execution_readiness"]["can_execute"])

    def test_secured_plan_binds_only_the_current_source_auth_reference(self):
        staged = self._activate(_openapi_document())

        plan = self.plan_service.generate_api_test_plan(staged["revision_id"], [], use_ai=False)
        positive = next(case for case in plan["cases"] if case["type"] == "positive")

        self.assertEqual(plan["source_id"], "source-pets")
        self.assertEqual(plan["auth_binding"]["auth_ref"], "api_auth_pets")
        self.assertEqual(positive["request"]["auth_ref"], "api_auth_pets")
        self.assertEqual(positive["readiness"]["state"], "executable")

    def test_secured_plan_without_auth_binding_needs_review(self):
        self.workspace_service.clear_api_auth_binding_metadata("source-pets")
        staged = self._activate(_openapi_document())

        plan = self.plan_service.generate_api_test_plan(staged["revision_id"], [], use_ai=False)
        positive = next(case for case in plan["cases"] if case["type"] == "positive")

        self.assertEqual(positive["request"]["auth_ref"], "")
        self.assertEqual(positive["readiness"]["state"], "needs_review")
        self.assertIn("auth_binding", positive["readiness"]["missing"])

    def test_plan_never_persists_or_returns_raw_endpoint_secrets(self):
        document = _openapi_document()
        operation = document["paths"]["/pets/{petId}"]["post"]
        operation["parameters"].append({
            "name": "X-API-Key",
            "in": "header",
            "required": True,
            "schema": {"type": "string", "example": "endpoint-header-secret"},
        })
        operation["requestBody"]["content"]["application/json"]["schema"]["properties"]["password"] = {
            "type": "string", "default": "endpoint-body-secret",
        }
        staged = self._activate(document)

        plan = self.plan_service.generate_api_test_plan(staged["revision_id"], [], use_ai=False)
        stored = Path(self.plan_service._plan_path(plan["plan_id"])).read_text(encoding="utf-8")
        index = Path(self.plan_service._index_path()).read_text(encoding="utf-8")
        returned = self.plan_service.get_api_test_plan(plan["plan_id"])

        from task_server import router

        class Handler:
            def __init__(self):
                self.payload = {}

            def _authorized(self):
                return True

            def _json(self, payload, _status=200):
                self.payload = payload

        handler = Handler()
        router._get_api_testing_plan_detail(
            handler,
            {},
            re.match(r"^/api/api-testing/plans/([^/]+)$", f"/api/api-testing/plans/{plan['plan_id']}"),
        )

        for value in ("endpoint-header-secret", "endpoint-body-secret"):
            self.assertNotIn(value, json.dumps(plan, ensure_ascii=False))
            self.assertNotIn(value, stored)
            self.assertNotIn(value, index)
            self.assertNotIn(value, json.dumps(returned, ensure_ascii=False))
            self.assertNotIn(value, json.dumps(handler.payload, ensure_ascii=False))

    def test_confirmed_plan_stops_when_current_auth_binding_is_cleared(self):
        staged = self._activate(_openapi_document())
        plan = self.plan_service.generate_api_test_plan(staged["revision_id"], [], use_ai=False)
        self.plan_service.confirm_api_test_plan(plan["plan_id"])
        self.workspace_service.clear_api_auth_binding_metadata("source-pets")

        evaluated = self.plan_service.get_api_test_plan(plan["plan_id"])

        self.assertFalse(evaluated["execution_readiness"]["can_execute"])
        self.assertEqual(evaluated["execution_readiness"]["state"], "blocked")
        with self.assertRaisesRegex(ValueError, "认证绑定"):
            self.plan_service.confirm_api_test_plan(plan["plan_id"])

    def test_confirmed_plan_stops_when_auth_binding_is_rebound(self):
        staged = self._activate(_openapi_document())
        plan = self.plan_service.generate_api_test_plan(staged["revision_id"], [], use_ai=False)
        self.plan_service.confirm_api_test_plan(plan["plan_id"])
        self.workspace_service.save_api_auth_binding_metadata(
            "source-pets",
            auth_type="api_key",
            header_name="X-API-Key",
            auth_ref="api_auth_rebound",
            variable_name="MTP_API_AUTH_REBOUND",
            environment_id="env-pets",
        )

        evaluated = self.plan_service.get_api_test_plan(plan["plan_id"])

        self.assertIn("auth_binding_drift", evaluated["binding_drift"])
        self.assertFalse(evaluated["execution_readiness"]["can_execute"])

    def test_changed_selected_endpoint_makes_plan_stale_and_unconfirmable(self):
        first = self._activate(_openapi_document(response_type="string"))
        plan = self.plan_service.generate_api_test_plan(first["revision_id"], [], use_ai=False)
        self.plan_service.confirm_api_test_plan(plan["plan_id"])

        self._activate(_openapi_document(response_type="integer"))
        evaluated = self.plan_service.get_api_test_plan(plan["plan_id"])

        self.assertEqual(evaluated["revision_state"]["state"], "stale")
        self.assertTrue(evaluated["revision_state"]["affected_case_ids"])
        self.assertFalse(evaluated["execution_readiness"]["can_execute"])
        with self.assertRaisesRegex(ValueError, "已过期"):
            self.plan_service.confirm_api_test_plan(plan["plan_id"])

    def test_add_only_revision_keeps_selected_cases_fresh(self):
        first = self._activate(_openapi_document())
        endpoint_id = first["revision"]["endpoints"][0]["endpoint_id"]
        plan = self.plan_service.generate_api_test_plan(
            first["revision_id"],
            [endpoint_id],
            use_ai=False,
        )

        self._activate(_openapi_document(include_health=True))
        evaluated = self.plan_service.get_api_test_plan(plan["plan_id"])

        self.assertEqual(evaluated["revision_state"]["state"], "fresh")
        self.assertTrue(evaluated["execution_readiness"]["can_confirm"])

    def test_legacy_prose_cases_are_readable_but_not_executable(self):
        legacy = {
            "plan_id": "legacy-plan",
            "status": "draft",
            "cases": [{
                "case_id": "LEGACY-1",
                "steps": ["发送请求"],
                "assertions": ["返回成功"],
            }],
        }

        evaluated = self.plan_service.evaluate_api_plan(legacy)

        self.assertEqual(evaluated["execution_readiness"]["state"], "blocked")
        self.assertEqual(evaluated["executable_case_count"], 0)
        self.assertEqual(evaluated["needs_review_case_count"], 1)
        self.assertEqual(evaluated["contract_version"], "legacy")
        self.assertEqual(evaluated["cases"][0]["steps"], ["发送请求"])

    def test_ai_cases_are_revalidated_and_duplicate_ids_are_deduplicated(self):
        staged = self._activate(_openapi_document())
        endpoint_id = staged["revision"]["endpoints"][0]["endpoint_id"]
        old_run_ai_skill = self.plan_service.run_ai_skill

        def fake_run_ai_skill(skill_name, payload, **kwargs):
            kwargs["runtime_trace"].update({
                "providerId": "qwen_plus",
                "model": "qwen3.8-plus",
                "fallbackUsed": False,
                "source": "ai_gateway",
            })
            case = {
                "case_id": "API-AI-DUP",
                "endpoint_id": endpoint_id,
                "name": "AI 更新宠物",
                "type": "positive",
                "priority": "P0",
                "steps": ["准备请求", "发送请求"],
                "request": {"method": "DELETE", "path": "/unsafe"},
                "assertions": [{"type": "status", "operator": "in", "expected": [204]}],
                "readiness": {"state": "executable", "missing": []},
            }
            return {"cases": [case, copy.deepcopy(case)], "review": {}}

        self.plan_service.run_ai_skill = fake_run_ai_skill
        try:
            plan = self.plan_service.generate_api_test_plan(
                staged["revision_id"],
                [],
                use_ai=True,
            )
        finally:
            self.plan_service.run_ai_skill = old_run_ai_skill

        self.assertEqual(plan["source"], "ai")
        self.assertEqual(len({case["case_id"] for case in plan["cases"]}), 2)
        self.assertTrue(all(case["request"]["method"] == "POST" for case in plan["cases"]))
        self.assertTrue(all(case["request"]["path"] == "/pets/{petId}" for case in plan["cases"]))
        trace = plan["ai"]["decision_trace"]
        self.assertEqual(trace["skill"], "api_test_designer")
        self.assertEqual(trace["action"], "generate_case")
        self.assertEqual(trace["provider_id"], "qwen_plus")
        self.assertEqual(trace["model"], "qwen3.8-plus")
        self.assertTrue(trace["input_hash"])
        self.assertNotIn("payload", trace)

    def test_ai_plan_keeps_a_deterministic_positive_seed_per_endpoint(self):
        staged = self._activate(_openapi_document())
        endpoint_id = staged["revision"]["endpoints"][0]["endpoint_id"]
        old_run_ai_skill = self.plan_service.run_ai_skill

        def fake_run_ai_skill(skill_name, payload, **kwargs):
            return {"cases": [{
                "case_id": "API-AI-AUTH",
                "endpoint_id": endpoint_id,
                "name": "AI 未授权校验",
                "type": "auth",
                "priority": "P1",
                "request": {},
                "assertions": [],
                "variables": [],
                "dependencies": [],
                "readiness": {"state": "executable", "missing": [], "issues": []},
            }], "review": {}}

        self.plan_service.run_ai_skill = fake_run_ai_skill
        try:
            plan = self.plan_service.generate_api_test_plan(
                staged["revision_id"],
                [],
                use_ai=True,
            )
        finally:
            self.plan_service.run_ai_skill = old_run_ai_skill

        self.assertTrue(any(case.get("type") == "auth" for case in plan["cases"]))
        self.assertTrue(any(case.get("type") == "positive" for case in plan["cases"]))
        self.assertEqual(
            {case.get("endpoint_id") for case in plan["cases"] if case.get("type") == "positive"},
            {endpoint_id},
        )


def _meter_case(case_id, state="executable"):
    missing = [] if state == "executable" else ["request.body.ownerId"]
    return {
        "contract_version": "api_case_contract/v1",
        "case_id": case_id,
        "name": case_id,
        "request": {
            "method": "GET",
            "path": "/pets",
            "path_params": {},
            "query": {},
            "headers": {},
            "body": {},
            "auth_ref": "",
        },
        "assertions": [{"type": "status", "operator": "in", "expected": [200]}],
        "variables": [],
        "dependencies": [],
        "readiness": {"state": state, "missing": missing, "issues": []},
    }


class MeterSphereContractBoundaryChecks(unittest.TestCase):
    def setUp(self):
        from task_server.services import api_test_plan_service, metersphere_service

        self.plan_service = api_test_plan_service
        self.metersphere_service = metersphere_service
        self.old_get_plan = api_test_plan_service.get_api_test_plan

    def tearDown(self):
        self.plan_service.get_api_test_plan = self.old_get_plan

    def test_stale_and_zero_executable_plans_cannot_push_or_run(self):
        stale_plan = {
            "plan_id": "api-plan-stale",
            "name": "过期计划",
            "status": "confirmed",
            "case_count": 1,
            "cases": [_meter_case("API-1")],
            "revision_state": {"state": "stale"},
            "execution_readiness": {
                "state": "stale",
                "executable_case_count": 1,
                "can_execute": False,
            },
        }
        self.plan_service.get_api_test_plan = lambda plan_id: copy.deepcopy(stale_plan)

        with self.assertRaisesRegex(self.metersphere_service.MeterSphereExecutionValidationError, "过期"):
            self.metersphere_service._execution_plan("api-plan-stale")
        push = self.metersphere_service.push_plan_to_metersphere("api-plan-stale")
        run = self.metersphere_service.create_metersphere_run("api-plan-stale")
        self.assertFalse(push["ok"])
        self.assertFalse(run["ok"])
        self.assertIn("过期", push["error"])
        self.assertIn("过期", run["error"])

        blocked_plan = copy.deepcopy(stale_plan)
        blocked_plan["plan_id"] = "api-plan-blocked"
        blocked_plan["revision_state"] = {"state": "fresh"}
        blocked_plan["execution_readiness"] = {
            "state": "blocked",
            "executable_case_count": 0,
            "can_execute": False,
        }
        blocked_plan["cases"] = [_meter_case("API-REVIEW", "needs_review")]
        self.plan_service.get_api_test_plan = lambda plan_id: copy.deepcopy(blocked_plan)
        with self.assertRaisesRegex(self.metersphere_service.MeterSphereExecutionValidationError, "可执行用例"):
            self.metersphere_service._execution_plan("api-plan-blocked")

    def test_execution_plan_rejects_current_auth_binding_drift(self):
        drifted_plan = {
            "plan_id": "api-plan-auth-drift",
            "status": "confirmed",
            "cases": [_meter_case("API-1")],
            "revision_state": {"state": "fresh"},
            "binding_drift": ["auth_binding_drift"],
            "execution_readiness": {
                "state": "blocked",
                "executable_case_count": 0,
                "can_execute": False,
            },
        }
        self.plan_service.get_api_test_plan = lambda _plan_id: copy.deepcopy(drifted_plan)

        with self.assertRaisesRegex(self.metersphere_service.MeterSphereExecutionValidationError, "认证绑定"):
            self.metersphere_service._execution_plan("api-plan-auth-drift")

    def test_meter_payload_contains_only_executable_cases_with_audit_counts(self):
        plan = {
            "plan_id": "api-plan-mixed",
            "name": "混合计划",
            "status": "confirmed",
            "case_count": 2,
            "cases": [
                _meter_case("API-READY"),
                _meter_case("API-REVIEW", "needs_review"),
            ],
            "revision_state": {"state": "fresh"},
            "execution_readiness": {
                "state": "partial",
                "executable_case_count": 1,
                "needs_review_case_count": 1,
                "can_execute": True,
            },
        }

        payload = self.metersphere_service._meter_payload_for_plan(plan)

        self.assertEqual(payload["contractVersion"], "api_case_contract/v1")
        self.assertEqual(payload["totalCaseCount"], 2)
        self.assertEqual(payload["executableCaseCount"], 1)
        self.assertEqual(payload["excludedCaseCount"], 1)
        self.assertEqual([case["case_id"] for case in payload["cases"]], ["API-READY"])

    def test_non_executable_and_cyclic_dependencies_block_dependent_cases(self):
        review_case = _meter_case("API-SETUP", "needs_review")
        dependent_case = _meter_case("API-DEPENDENT")
        dependent_case["dependencies"] = [{"case_id": "API-SETUP", "required": True}]
        evaluated = self.plan_service.evaluate_api_plan({
            "plan_id": "api-plan-dependency",
            "status": "draft",
            "cases": [review_case, dependent_case],
        })
        dependent = next(case for case in evaluated["cases"] if case["case_id"] == "API-DEPENDENT")
        self.assertEqual(dependent["readiness"]["state"], "needs_review")
        self.assertIn(
            "dependencies.API-SETUP.not_executable",
            dependent["readiness"]["missing"],
        )

        first = _meter_case("API-CYCLE-A")
        second = _meter_case("API-CYCLE-B")
        first["dependencies"] = [{"case_id": "API-CYCLE-B", "required": True}]
        second["dependencies"] = [{"case_id": "API-CYCLE-A", "required": True}]
        cyclic = self.plan_service.evaluate_api_plan({
            "plan_id": "api-plan-cycle",
            "status": "draft",
            "cases": [first, second],
        })
        self.assertTrue(all(
            "dependencies.cycle" in case["readiness"]["missing"]
            for case in cyclic["cases"]
        ))

    def test_executable_cases_are_topologically_ordered(self):
        setup = _meter_case("API-SETUP")
        dependent = _meter_case("API-DEPENDENT")
        dependent["dependencies"] = [{"case_id": "API-SETUP", "required": True}]
        plan = {
            "plan_id": "api-plan-order",
            "status": "confirmed",
            "cases": [dependent, setup],
        }

        ordered = self.plan_service.executable_api_cases(plan)

        self.assertEqual(
            [case["case_id"] for case in ordered],
            ["API-SETUP", "API-DEPENDENT"],
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
