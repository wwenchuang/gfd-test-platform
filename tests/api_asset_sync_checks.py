#!/usr/bin/env python3
"""Focused contract checks for Apifox source synchronization and API revisions."""

from __future__ import annotations

import os
import json
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ApiSourceConfigTests(unittest.TestCase):
    def setUp(self):
        from task_server.services import api_source_service

        self.service = api_source_service
        self.old_dir = api_source_service.API_TESTING_DIR
        self.temp_dir = tempfile.mkdtemp(prefix="api_source_checks_")
        api_source_service.API_TESTING_DIR = self.temp_dir
        self.env_backup = {
            key: os.environ.get(key)
            for key in (
                "APIFOX_ACCESS_TOKEN",
                "APIFOX_PROJECT_ID",
                "APIFOX_SOURCE_NAME",
                "APIFOX_BASE_URL",
                "APIFOX_SYNC_ENABLED",
                "APIFOX_SYNC_INTERVAL_MINUTES",
            )
        }
        for key in self.env_backup:
            os.environ.pop(key, None)

    def tearDown(self):
        self.service.API_TESTING_DIR = self.old_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)
        for key, value in self.env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_source_token_is_write_only_and_empty_update_preserves_it(self):
        saved = self.service.save_api_source({
            "source_type": "apifox",
            "name": "3D API",
            "project_id": "project-123",
            "access_token": "secret-apifox-token",
            "sync_interval_minutes": 60,
        })

        self.assertTrue(saved["credential_configured"])
        self.assertNotIn("access_token", saved)
        self.assertNotIn("secret-apifox-token", str(saved))

        updated = self.service.save_api_source({
            "source_id": saved["source_id"],
            "name": "3D API renamed",
            "access_token": "",
        })
        raw = self.service.get_api_source(saved["source_id"], masked=False)

        self.assertEqual("3D API renamed", updated["name"])
        self.assertEqual("secret-apifox-token", raw["access_token"])
        self.assertTrue(updated["credential_configured"])

    def test_source_token_requires_explicit_clear(self):
        saved = self.service.save_api_source({
            "source_type": "apifox",
            "name": "Clearable",
            "project_id": "project-123",
            "access_token": "secret-apifox-token",
        })

        cleared = self.service.save_api_source({
            "source_id": saved["source_id"],
            "clear_credentials": True,
        })

        self.assertFalse(cleared["credential_configured"])
        self.assertEqual("", self.service.get_api_source(saved["source_id"], masked=False)["access_token"])

    def test_changing_source_origin_requires_resubmitting_the_write_only_token(self):
        saved = self.service.save_api_source({
            "source_type": "apifox",
            "name": "Origin bound",
            "project_id": "project-123",
            "access_token": "secret-apifox-token",
            "base_url": "https://api.apifox.com",
        })

        with self.assertRaisesRegex(ValueError, "重新提交访问令牌"):
            self.service.save_api_source({
                "source_id": saved["source_id"],
                "base_url": "https://example.invalid",
                "access_token": "",
            })

        updated = self.service.save_api_source({
            "source_id": saved["source_id"],
            "base_url": "https://example.invalid",
            "access_token": "replacement-token",
        })
        raw = self.service.get_api_source(saved["source_id"], masked=False)
        self.assertTrue(updated["credential_configured"])
        self.assertEqual("replacement-token", raw["access_token"])

    def test_source_type_and_interval_are_validated(self):
        low = self.service.save_api_source({
            "source_type": "apifox",
            "name": "Low interval",
            "project_id": "p-low",
            "sync_interval_minutes": 1,
        })
        high = self.service.save_api_source({
            "source_type": "openapi_upload",
            "name": "Upload fallback",
            "sync_interval_minutes": 5000,
        })

        self.assertEqual(15, low["sync_interval_minutes"])
        self.assertEqual(1440, high["sync_interval_minutes"])
        with self.assertRaisesRegex(ValueError, "source_type"):
            self.service.save_api_source({"source_type": "unknown", "name": "Bad"})

    def test_config_and_sync_state_updates_do_not_overwrite_each_other(self):
        saved = self.service.save_api_source({
            "source_type": "apifox",
            "name": "Original",
            "project_id": "project-123",
            "access_token": "secret-apifox-token",
        })
        original_write = self.service._write_source
        start = threading.Barrier(3)
        errors = []

        def slow_write(source):
            time.sleep(0.05)
            original_write(source)

        def save_config():
            try:
                start.wait()
                self.service.save_api_source({"source_id": saved["source_id"], "name": "Renamed"})
            except Exception as exc:
                errors.append(exc)

        def save_sync_state():
            try:
                start.wait()
                self.service.update_api_source_sync_state(
                    saved["source_id"], last_sync_status="failed", last_error="remote unavailable"
                )
            except Exception as exc:
                errors.append(exc)

        self.service._write_source = slow_write
        threads = [threading.Thread(target=save_config), threading.Thread(target=save_sync_state)]
        try:
            for thread in threads:
                thread.start()
            start.wait()
            for thread in threads:
                thread.join(timeout=2)
        finally:
            self.service._write_source = original_write

        raw = self.service.get_api_source(saved["source_id"], masked=False)
        self.assertEqual([], errors)
        self.assertEqual("Renamed", raw["name"])
        self.assertEqual("failed", raw["last_sync_status"])
        self.assertEqual("remote unavailable", raw["last_error"])


class _FakeHttpResponse:
    def __init__(self, payload, status=200, headers=None):
        self.payload = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.status = status
        self.headers = headers or {}

    def read(self, size=-1):
        return self.payload if size is None or size < 0 else self.payload[:size]

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class ApifoxAdapterTests(unittest.TestCase):
    def test_export_uses_official_read_only_contract(self):
        from task_server.services.apifox_service import ApifoxSourceAdapter

        calls = []

        def opener(request, timeout):
            calls.append((request, timeout))
            return _FakeHttpResponse(
                {"openapi": "3.0.1", "info": {"title": "3D"}, "paths": {"/items": {"get": {"responses": {"200": {"description": "ok"}}}}}},
                headers={"ETag": '"revision-1"', "Last-Modified": "Wed, 22 Jul 2026 08:00:00 GMT"},
            )

        result = ApifoxSourceAdapter(opener=opener).fetch_openapi({
            "base_url": "https://api.apifox.com",
            "project_id": "5904970",
            "branch_id": "12",
            "environment_id": "34, 56",
            "access_token": "secret-apifox-token",
        })

        self.assertEqual(1, len(calls))
        request, timeout = calls[0]
        self.assertEqual("POST", request.get_method())
        self.assertEqual("https://api.apifox.com/v1/projects/5904970/export-openapi?locale=zh-CN", request.full_url)
        self.assertEqual("Bearer secret-apifox-token", request.get_header("Authorization"))
        self.assertEqual("2024-03-28", request.get_header("X-apifox-api-version"))
        self.assertEqual("midscene-task-platform/api-sync", request.get_header("User-agent"))
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual({"type": "ALL"}, body["scope"])
        self.assertEqual("3.0", body["oasVersion"])
        self.assertEqual("JSON", body["exportFormat"])
        self.assertTrue(body["options"]["includeApifoxExtensionProperties"])
        self.assertEqual([34, 56], body["environmentIds"])
        self.assertEqual(12, body["branchId"])
        self.assertEqual(30, timeout)
        self.assertEqual('"revision-1"', result["etag"])
        self.assertEqual("3D", result["document"]["info"]["title"])
        self.assertRegex(result["document_hash"], r"^[0-9a-f]{64}$")

    def test_export_falls_back_to_current_cli_route_only_for_empty_official_response(self):
        from task_server.services.apifox_service import ApifoxSourceAdapter

        calls = []

        def opener(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                return _FakeHttpResponse(b"", status=201)
            return _FakeHttpResponse({
                "openapi": "3.0.1",
                "info": {"title": "3D"},
                "paths": {"/items": {"get": {"responses": {"200": {"description": "ok"}}}}},
            })

        result = ApifoxSourceAdapter(opener=opener).fetch_openapi({
            "base_url": "https://api.apifox.com",
            "project_id": "5904970",
            "branch_id": "12",
            "environment_id": "34",
            "access_token": "secret-apifox-token",
        })

        self.assertEqual(2, len(calls))
        fallback = calls[1]
        self.assertEqual("https://api.apifox.com/api/v1/projects/5904970/export-openapi", fallback.full_url)
        self.assertEqual("2026-05-28", fallback.get_header("X-apifox-api-version"))
        self.assertEqual("midscene-task-platform/api-sync", fallback.get_header("User-agent"))
        self.assertEqual("5904970", fallback.get_header("X-project-id"))
        self.assertEqual("12", fallback.get_header("X-branch-id"))
        fallback_body = json.loads(fallback.data.decode("utf-8"))
        self.assertEqual([34], fallback_body["environmentIds"])
        self.assertNotIn("branchId", fallback_body)
        self.assertNotIn("exportFormat", fallback_body)
        self.assertEqual("3D", result["document"]["info"]["title"])

    def test_export_rejects_invalid_or_oversized_documents_without_leaking_token(self):
        from task_server.services.apifox_service import ApifoxRequestError, ApifoxSourceAdapter

        source = {
            "base_url": "https://api.apifox.com",
            "project_id": "5904970",
            "access_token": "secret-apifox-token",
        }

        with self.assertRaisesRegex(ApifoxRequestError, "JSON"):
            ApifoxSourceAdapter(opener=lambda request, timeout: _FakeHttpResponse(b"not-json")).fetch_openapi(source)

        with self.assertRaisesRegex(ApifoxRequestError, "paths"):
            ApifoxSourceAdapter(opener=lambda request, timeout: _FakeHttpResponse({"openapi": "3.0.1", "paths": {}})).fetch_openapi(source)

        adapter = ApifoxSourceAdapter(
            opener=lambda request, timeout: _FakeHttpResponse(b"{" + (b"x" * 64) + b"}"),
            max_response_bytes=32,
        )
        with self.assertRaisesRegex(ApifoxRequestError, "过大") as caught:
            adapter.fetch_openapi(source)
        self.assertNotIn("secret-apifox-token", str(caught.exception))

    def test_export_requires_project_and_token(self):
        from task_server.services.apifox_service import ApifoxRequestError, ApifoxSourceAdapter

        adapter = ApifoxSourceAdapter(opener=lambda request, timeout: _FakeHttpResponse({}))
        with self.assertRaisesRegex(ApifoxRequestError, "project_id"):
            adapter.fetch_openapi({"access_token": "token"})
        with self.assertRaisesRegex(ApifoxRequestError, "令牌"):
            adapter.fetch_openapi({"project_id": "5904970"})


def _openapi_document(response_type="string", path="/items", operation_id="listItems", provider_id=""):
    operation = {
        "operationId": operation_id,
        "summary": "List items",
        "responses": {
            "200": {
                "description": "ok",
                "content": {
                    "application/json": {
                        "schema": {"type": response_type},
                    }
                },
            }
        },
    }
    if provider_id:
        operation["x-apifox-endpoint-id"] = provider_id
    return {
        "openapi": "3.0.1",
        "info": {"title": "3D", "version": "1.0"},
        "paths": {path: {"get": operation}},
    }


class ApiAssetRevisionTests(unittest.TestCase):
    def setUp(self):
        from task_server.services import api_asset_service

        self.service = api_asset_service
        self.old_dir = api_asset_service.API_TESTING_DIR
        self.temp_dir = tempfile.mkdtemp(prefix="api_revision_checks_")
        api_asset_service.API_TESTING_DIR = self.temp_dir

    def tearDown(self):
        self.service.API_TESTING_DIR = self.old_dir
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_revision_is_persisted_before_activation_and_no_change_reuses_it(self):
        staged = self.service.stage_api_revision(
            source_id="api_source_3d",
            source_name="3D",
            document=_openapi_document(),
            source_type="apifox",
            source_revision='"remote-1"',
        )

        self.assertEqual("staged", staged["status"])
        asset = self.service.get_api_asset(staged["asset_id"])
        self.assertEqual("", asset["active_revision_id"])
        self.assertEqual(staged["revision_id"], self.service.get_api_revision(staged["revision_id"])["revision_id"])

        activated = self.service.activate_api_revision(staged["asset_id"], staged["revision_id"])
        self.assertEqual(staged["revision_id"], activated["active_revision_id"])

        repeated = self.service.stage_api_revision(
            source_id="api_source_3d",
            source_name="3D",
            document=_openapi_document(),
            source_type="apifox",
            source_revision='"remote-1"',
        )
        self.assertEqual("no_change", repeated["status"])
        self.assertEqual(staged["revision_id"], repeated["revision_id"])
        self.assertEqual(1, len(self.service.list_api_revisions(staged["asset_id"])))

    def test_default_snapshot_view_never_exposes_an_unactivated_revision(self):
        active = self.service.stage_api_revision(
            "api_source_3d", "3D", _openapi_document(response_type="string"), source_type="apifox"
        )
        self.service.activate_api_revision(active["asset_id"], active["revision_id"])
        staged = self.service.stage_api_revision(
            "api_source_3d", "3D", _openapi_document(response_type="array"), source_type="apifox"
        )

        default_snapshot = self.service.get_api_snapshot()
        visible_ids = [item.get("revision_id") for item in self.service.list_api_snapshots(limit=20)]
        history_ids = [item.get("revision_id") for item in self.service.list_api_revisions(active["asset_id"])]

        self.assertEqual(active["revision_id"], default_snapshot["revision_id"])
        self.assertIn(active["revision_id"], visible_ids)
        self.assertNotIn(staged["revision_id"], visible_ids)
        self.assertIn(staged["revision_id"], history_ids)

    def test_endpoint_identity_stays_stable_across_schema_change(self):
        first = self.service.stage_api_revision(
            "api_source_3d", "3D", _openapi_document(response_type="string"), source_type="apifox"
        )
        self.service.activate_api_revision(first["asset_id"], first["revision_id"])
        second = self.service.stage_api_revision(
            "api_source_3d", "3D", _openapi_document(response_type="array"), source_type="apifox"
        )

        old_endpoint = first["revision"]["endpoints"][0]
        new_endpoint = second["revision"]["endpoints"][0]
        self.assertEqual("operation:listitems", old_endpoint["endpoint_key"])
        self.assertEqual(old_endpoint["endpoint_key"], new_endpoint["endpoint_key"])
        self.assertEqual(old_endpoint["endpoint_id"], new_endpoint["endpoint_id"])
        self.assertNotEqual(old_endpoint["schema_hash"], new_endpoint["schema_hash"])
        self.assertNotEqual(old_endpoint["endpoint_revision_id"], new_endpoint["endpoint_revision_id"])

    def test_provider_identity_survives_path_change_and_fallback_uses_route(self):
        provider_old = self.service.build_revision_endpoints(
            _openapi_document(path="/old", operation_id="", provider_id="endpoint-77"),
            source_type="apifox",
        )[0]
        provider_new = self.service.build_revision_endpoints(
            _openapi_document(path="/new", operation_id="", provider_id="endpoint-77"),
            source_type="apifox",
        )[0]
        fallback = self.service.build_revision_endpoints(
            _openapi_document(path="/items/{id}", operation_id=""),
            source_type="openapi_upload",
        )[0]

        self.assertEqual("apifox:endpoint-77", provider_old["endpoint_key"])
        self.assertEqual(provider_old["endpoint_key"], provider_new["endpoint_key"])
        self.assertEqual("route:GET /items/{id}", fallback["endpoint_key"])

    def test_real_apifox_run_link_supplies_stable_provider_identity(self):
        old_document = _openapi_document(path="/old", operation_id="")
        old_document["paths"]["/old"]["get"]["x-run-in-apifox"] = (
            "https://app.apifox.com/web/project/5904970/apis/api-278430172-run"
        )
        new_document = _openapi_document(path="/new", operation_id="")
        new_document["paths"]["/new"]["get"]["x-run-in-apifox"] = (
            "https://app.apifox.com/web/project/5904970/apis/api-278430172-run"
        )

        old_endpoint = self.service.build_revision_endpoints(old_document, source_type="apifox")[0]
        new_endpoint = self.service.build_revision_endpoints(new_document, source_type="apifox")[0]

        self.assertEqual("apifox:278430172", old_endpoint["endpoint_key"])
        self.assertEqual("278430172", old_endpoint["source_ref"])
        self.assertEqual(old_endpoint["endpoint_key"], new_endpoint["endpoint_key"])

    def test_apifox_folder_is_the_primary_business_module(self):
        document = _openapi_document(path="/print3d/api/v1/collection/page")
        operation = document["paths"]["/print3d/api/v1/collection/page"]["get"]
        operation["tags"] = ["generic-tag"]
        operation["x-apifox-folder"] = "家用业务/app接口/我的/我的收藏"

        endpoint = self.service.build_revision_endpoints(document, source_type="apifox")[0]

        self.assertEqual("家用业务/app接口/我的/我的收藏", endpoint["module"])

    def test_asset_route_selects_the_requested_source_and_revision(self):
        from task_server import router

        source_two = self.service.stage_api_revision(
            "api_source_two", "Source Two", _openapi_document(path="/two"), source_type="apifox"
        )
        self.service.activate_api_revision(source_two["asset_id"], source_two["revision_id"])
        source_one = self.service.stage_api_revision(
            "api_source_one", "Source One", _openapi_document(path="/one"), source_type="apifox"
        )
        self.service.activate_api_revision(source_one["asset_id"], source_one["revision_id"])

        by_source = _RouteHandler()
        router.GET_ROUTES["/api/api-testing/assets"](by_source, {"source_id": "api_source_two"})
        source_payload = by_source.responses[-1][1]

        self.assertEqual("api_source_two", source_payload["asset"]["source_id"])
        self.assertEqual(source_two["revision_id"], source_payload["snapshot"]["revision_id"])
        self.assertEqual("/two", source_payload["endpoints"][0]["path"])

        by_revision = _RouteHandler()
        router.GET_ROUTES["/api/api-testing/assets"](
            by_revision, {"snapshot_id": source_two["revision_id"]}
        )
        revision_payload = by_revision.responses[-1][1]

        self.assertEqual("api_source_two", revision_payload["asset"]["source_id"])
        self.assertEqual(source_two["revision_id"], revision_payload["snapshot"]["revision_id"])

    def test_legacy_uploaded_snapshot_remains_readable(self):
        legacy = self.service.import_openapi_document("Legacy", _openapi_document(), "legacy.json")

        loaded = self.service.get_api_snapshot(legacy["snapshot_id"])

        self.assertEqual(legacy["snapshot_id"], loaded["snapshot_id"])
        self.assertEqual(1, len(self.service.list_api_endpoints(legacy["snapshot_id"])))


class ApiSchemaDiffTests(unittest.TestCase):
    def test_diff_classifies_added_changed_removed_and_unchanged(self):
        from task_server.services.api_schema_diff_service import compare_api_revisions

        old_revision = {
            "revision_id": "rev-old",
            "endpoints": [
                {"endpoint_key": "route:GET /changed", "schema_hash": "old", "method": "GET", "path": "/changed", "parameters": [], "request_schema": {}, "response_schema": {"type": "string"}, "responses": [], "security": []},
                {"endpoint_key": "route:GET /removed", "schema_hash": "removed", "method": "GET", "path": "/removed"},
                {"endpoint_key": "route:GET /same", "schema_hash": "same", "method": "GET", "path": "/same"},
            ],
        }
        new_revision = {
            "revision_id": "rev-new",
            "endpoints": [
                {"endpoint_key": "route:GET /changed", "schema_hash": "new", "method": "GET", "path": "/changed", "parameters": [], "request_schema": {}, "response_schema": {"type": "array"}, "responses": [], "security": []},
                {"endpoint_key": "route:GET /added", "schema_hash": "added", "method": "GET", "path": "/added"},
                {"endpoint_key": "route:GET /same", "schema_hash": "same", "method": "GET", "path": "/same"},
            ],
        }

        diff = compare_api_revisions(old_revision, new_revision)

        self.assertEqual({"added": 1, "changed": 1, "removed": 1, "unchanged": 1}, diff["summary"])
        self.assertEqual(["route:GET /added"], [item["endpoint_key"] for item in diff["added"]])
        self.assertEqual(["route:GET /removed"], [item["endpoint_key"] for item in diff["removed"]])
        self.assertIn("response_schema", diff["changed"][0]["fields"])

    def test_plan_impact_returns_exact_plan_and_case_ids(self):
        from task_server.services.api_schema_diff_service import analyze_api_plan_impact

        diff = {
            "added": [{"endpoint_key": "route:GET /new"}],
            "changed": [{"endpoint_key": "route:GET /changed"}],
            "removed": [{"endpoint_key": "route:GET /removed"}],
            "unchanged": [],
        }
        plans = [
            {
                "plan_id": "plan-1",
                "endpoints": [{"endpoint_id": "legacy-changed", "endpoint_key": "route:GET /changed"}],
                "cases": [
                    {"case_id": "case-direct", "endpoint_key": "route:GET /removed"},
                    {"case_id": "case-alias", "endpoint_id": "legacy-changed"},
                ],
            },
            {
                "plan_id": "plan-unaffected",
                "cases": [{"case_id": "case-new", "endpoint_key": "route:GET /new"}],
            },
            {
                "plan_id": "plan-legacy",
                "cases": [{"case_id": "case-unknown", "endpoint_id": "unknown-legacy-id"}],
            },
        ]

        impact = analyze_api_plan_impact(diff, plans)

        self.assertEqual(["plan-1"], impact["affected_plan_ids"])
        self.assertEqual(["case-alias", "case-direct"], impact["affected_case_ids"])
        self.assertEqual(["plan-legacy"], impact["unresolved_legacy_plan_ids"])
        self.assertEqual(1, impact["affected_plans"])

    def test_metadata_only_change_is_not_misclassified_as_unchanged(self):
        from task_server.services.api_schema_diff_service import compare_api_revisions

        old_revision = {
            "revision_id": "rev-old",
            "endpoints": [{
                "endpoint_key": "apifox:77",
                "schema_hash": "same-schema",
                "method": "GET",
                "path": "/items",
                "name": "旧名称",
                "tags": ["旧分组"],
                "deprecated": False,
            }],
        }
        new_revision = {
            "revision_id": "rev-new",
            "endpoints": [{
                "endpoint_key": "apifox:77",
                "schema_hash": "same-schema",
                "method": "GET",
                "path": "/items",
                "name": "新名称",
                "tags": ["新分组"],
                "deprecated": True,
            }],
        }

        diff = compare_api_revisions(old_revision, new_revision)

        self.assertEqual({"added": 0, "changed": 1, "removed": 0, "unchanged": 0}, diff["summary"])
        self.assertEqual(["deprecated", "name", "tags"], sorted(diff["changed"][0]["fields"]))


class _SequenceApifoxAdapter:
    def __init__(self, values):
        self.values = list(values)

    def fetch_openapi(self, source, timeout=30):
        value = self.values.pop(0)
        if isinstance(value, Exception):
            raise value
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        import hashlib
        return {
            "document": value,
            "document_hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "etag": "",
            "last_modified": "",
            "source_revision": "",
            "fetched_at": "2026-07-22 10:00:00",
        }


class _BlockingApifoxAdapter:
    def __init__(self, document):
        self.document = document
        self.started = threading.Event()
        self.release = threading.Event()
        self.seen_source = {}

    def fetch_openapi(self, source, timeout=30):
        self.seen_source = dict(source)
        self.started.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test adapter was not released")
        raw = json.dumps(self.document, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        import hashlib
        return {
            "document": self.document,
            "document_hash": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
            "etag": "",
            "last_modified": "",
            "source_revision": "",
            "fetched_at": "2026-07-23 10:00:00",
        }


class ApiSyncServiceTests(unittest.TestCase):
    def setUp(self):
        from task_server.services import api_asset_service, api_source_service, api_sync_service, api_test_plan_service

        self.asset_service = api_asset_service
        self.source_service = api_source_service
        self.sync_service = api_sync_service
        self.plan_service = api_test_plan_service
        self.old_dirs = {
            "asset": api_asset_service.API_TESTING_DIR,
            "source": api_source_service.API_TESTING_DIR,
            "plan": api_test_plan_service.API_TESTING_DIR,
        }
        self.temp_dir = tempfile.mkdtemp(prefix="api_sync_checks_")
        api_asset_service.API_TESTING_DIR = self.temp_dir
        api_source_service.API_TESTING_DIR = self.temp_dir
        api_test_plan_service.API_TESTING_DIR = self.temp_dir
        self.source = api_source_service.save_api_source({
            "source_type": "apifox",
            "name": "3D",
            "project_id": "5904970",
            "access_token": "secret-apifox-token",
            "sync_enabled": True,
            "sync_interval_minutes": 60,
        })

    def tearDown(self):
        self.asset_service.API_TESTING_DIR = self.old_dirs["asset"]
        self.source_service.API_TESTING_DIR = self.old_dirs["source"]
        self.plan_service.API_TESTING_DIR = self.old_dirs["plan"]
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def _run(self, adapter):
        queued = self.sync_service.start_api_source_sync(
            self.source["source_id"], spawn=False
        )
        return self.sync_service.run_api_source_sync(queued["sync_id"], adapter=adapter)

    def test_first_no_change_and_failed_sync_keep_atomic_active_revision(self):
        initial_document = _openapi_document(response_type="string")
        removed_document = _openapi_document(path="/removed", operation_id="removedItem")
        initial_document["paths"]["/removed"] = removed_document["paths"]["/removed"]

        first = self._run(_SequenceApifoxAdapter([initial_document]))
        self.assertEqual("succeeded", first["status"])
        self.assertEqual(2, first["summary"]["added"])
        active_revision_id = first["revision_id"]

        no_change = self._run(_SequenceApifoxAdapter([initial_document]))
        self.assertEqual("no_change", no_change["status"])
        self.assertEqual(2, no_change["summary"]["unchanged"])
        self.assertEqual(active_revision_id, no_change["revision_id"])

        changed = self._run(_SequenceApifoxAdapter([_openapi_document(response_type="array")]))
        self.assertEqual("succeeded", changed["status"])
        self.assertEqual(1, changed["summary"]["changed"])
        self.assertEqual(1, changed["summary"]["removed"])
        self.assertEqual(active_revision_id, changed["previous_revision_id"])
        active_revision_id = changed["revision_id"]

        failed = self._run(_SequenceApifoxAdapter([ValueError("remote unavailable secret-apifox-token")]))
        self.assertEqual("failed", failed["status"])
        self.assertNotIn("secret-apifox-token", failed["error"])
        asset = self.asset_service.get_api_asset(first["asset_id"])
        self.assertEqual(active_revision_id, asset["active_revision_id"])

    def test_discovery_failure_before_activation_keeps_the_previous_active_revision(self):
        first = self._run(_SequenceApifoxAdapter([_openapi_document(response_type="string")]))
        active_revision_id = first["revision_id"]
        original_discovery_update = self.source_service.update_api_source_discovery_state

        def fail_discovery(*_args, **_kwargs):
            raise OSError("discovery storage unavailable")

        self.source_service.update_api_source_discovery_state = fail_discovery
        try:
            failed = self._run(_SequenceApifoxAdapter([_openapi_document(response_type="array")]))
        finally:
            self.source_service.update_api_source_discovery_state = original_discovery_update

        self.assertEqual("failed", failed["status"])
        asset = self.asset_service.get_api_asset(first["asset_id"])
        self.assertEqual(active_revision_id, asset["active_revision_id"])

    def test_success_sync_record_failure_keeps_the_previous_active_revision(self):
        first = self._run(_SequenceApifoxAdapter([_openapi_document(response_type="string")]))
        active_revision_id = first["revision_id"]
        original_update_sync = self.sync_service._update_sync

        def fail_success_sync(sync_id, **changes):
            if changes.get("status") == "succeeded":
                raise OSError("sync record storage unavailable")
            return original_update_sync(sync_id, **changes)

        self.sync_service._update_sync = fail_success_sync
        try:
            failed = self._run(_SequenceApifoxAdapter([_openapi_document(response_type="array")]))
        finally:
            self.sync_service._update_sync = original_update_sync

        self.assertEqual("failed", failed["status"])
        self.assertEqual("failed", self.sync_service.get_api_sync(failed["sync_id"])["status"])
        self.assertEqual(active_revision_id, self.asset_service.get_api_asset(first["asset_id"])["active_revision_id"])

    def test_source_success_state_failure_keeps_the_previous_active_revision(self):
        first = self._run(_SequenceApifoxAdapter([_openapi_document(response_type="string")]))
        active_revision_id = first["revision_id"]
        original_update_source = self.source_service.update_api_source_sync_state

        def fail_source_success(source_id, **changes):
            if changes.get("last_sync_status") == "succeeded":
                raise OSError("source state storage unavailable")
            return original_update_source(source_id, **changes)

        self.source_service.update_api_source_sync_state = fail_source_success
        try:
            failed = self._run(_SequenceApifoxAdapter([_openapi_document(response_type="array")]))
        finally:
            self.source_service.update_api_source_sync_state = original_update_source

        self.assertEqual("failed", failed["status"])
        self.assertEqual("failed", self.sync_service.get_api_sync(failed["sync_id"])["status"])
        self.assertEqual("failed", self.source_service.get_api_source(self.source["source_id"], masked=True)["last_sync_status"])
        self.assertEqual(active_revision_id, self.asset_service.get_api_asset(first["asset_id"])["active_revision_id"])

    def test_selected_scope_stages_only_the_selected_module(self):
        document = _openapi_document(path="/selected", operation_id="selected")
        document["paths"]["/selected"]["get"]["x-apifox-folder"] = "A/B"
        sibling = _openapi_document(path="/sibling", operation_id="sibling")
        sibling["paths"]["/sibling"]["get"]["x-apifox-folder"] = "A/BB"
        document["paths"].update(sibling["paths"])
        self.source = self.source_service.save_api_source({
            "source_id": self.source["source_id"],
            "sync_scope": {"mode": "selected", "module_paths": ["A/B"]},
        })

        synced = self._run(_SequenceApifoxAdapter([document]))

        self.assertEqual("succeeded", synced["status"])
        revision = self.asset_service.get_api_revision(synced["revision_id"])
        self.assertEqual(["/selected"], [endpoint["path"] for endpoint in revision["endpoints"]])
        self.assertEqual(2, synced["module_count"])
        self.assertEqual(1, synced["scoped_module_count"])
        self.assertEqual("selected", revision["sync_scope"]["mode"])

    def test_scope_fingerprint_change_stages_a_new_revision_for_the_same_document(self):
        document = _openapi_document(path="/selected", operation_id="selected")
        document["paths"]["/selected"]["get"]["x-apifox-folder"] = "A/B/C"
        first = self._run(_SequenceApifoxAdapter([document]))
        self.source = self.source_service.save_api_source({
            "source_id": self.source["source_id"],
            "sync_scope": {"mode": "selected", "module_paths": ["A/B"]},
        })

        scoped = self._run(_SequenceApifoxAdapter([document]))

        self.assertEqual("succeeded", scoped["status"])
        self.assertNotEqual(first["revision_id"], scoped["revision_id"])
        self.assertEqual(
            [
                (endpoint["endpoint_key"], endpoint["path"], endpoint["schema_hash"])
                for endpoint in self.asset_service.get_api_revision(first["revision_id"])["endpoints"]
            ],
            [
                (endpoint["endpoint_key"], endpoint["path"], endpoint["schema_hash"])
                for endpoint in self.asset_service.get_api_revision(scoped["revision_id"])["endpoints"]
            ],
        )

    def test_sync_configuration_drift_after_fetch_never_stages_or_activates_old_project_document(self):
        initial = self._run(_SequenceApifoxAdapter([_openapi_document(path="/active", operation_id="active")]))
        active_revision_id = initial["revision_id"]
        adapter = _BlockingApifoxAdapter(_openapi_document(path="/p1-only", operation_id="p1Only"))
        queued = self.sync_service.start_api_source_sync(self.source["source_id"], spawn=False)
        worker = threading.Thread(
            target=self.sync_service.run_api_source_sync,
            args=(queued["sync_id"], adapter),
        )
        worker.start()
        self.assertTrue(adapter.started.wait(timeout=2))

        changed = self.source_service.save_api_source({
            "source_id": self.source["source_id"],
            "project_id": "project-p2",
            "base_url": "https://p2.example.test",
            "branch_id": "branch-p2",
            "environment_id": "environment-p2",
            "access_token": "token-p2",
            "sync_scope": {"mode": "selected", "module_paths": ["P2/Module"]},
        })
        adapter.release.set()
        worker.join(timeout=5)

        result = self.sync_service.get_api_sync(queued["sync_id"])
        asset = self.asset_service.get_api_asset(initial["asset_id"])
        revisions = self.asset_service.list_api_revisions(initial["asset_id"])
        current = self.source_service.get_api_source(self.source["source_id"], masked=False)

        self.assertFalse(worker.is_alive())
        self.assertEqual("failed", result["status"])
        self.assertTrue(result["conflict"])
        self.assertEqual(active_revision_id, asset["active_revision_id"])
        self.assertEqual([active_revision_id], [item["revision_id"] for item in revisions])
        self.assertEqual("project-p2", current["project_id"])
        self.assertEqual("https://p2.example.test", current["base_url"])
        self.assertEqual("branch-p2", current["branch_id"])
        self.assertEqual("environment-p2", current["environment_id"])
        self.assertEqual("selected", current["sync_scope"]["mode"])
        self.assertNotEqual("failed", current["last_sync_status"])
        self.assertNotIn("secret-apifox-token", result["error"])
        self.assertNotIn("token-p2", result["error"])

    def test_token_rotation_during_sync_is_a_configuration_conflict_without_secret_leak(self):
        initial = self._run(_SequenceApifoxAdapter([_openapi_document(path="/active", operation_id="active")]))
        adapter = _BlockingApifoxAdapter(_openapi_document(path="/rotated", operation_id="rotated"))
        queued = self.sync_service.start_api_source_sync(self.source["source_id"], spawn=False)
        worker = threading.Thread(
            target=self.sync_service.run_api_source_sync,
            args=(queued["sync_id"], adapter),
        )
        worker.start()
        self.assertTrue(adapter.started.wait(timeout=2))

        self.source_service.save_api_source({
            "source_id": self.source["source_id"],
            "access_token": "rotated-token-only",
        })
        adapter.release.set()
        worker.join(timeout=5)

        result = self.sync_service.get_api_sync(queued["sync_id"])
        asset = self.asset_service.get_api_asset(initial["asset_id"])
        revisions = self.asset_service.list_api_revisions(initial["asset_id"])

        self.assertFalse(worker.is_alive())
        self.assertEqual("failed", result["status"])
        self.assertTrue(result["conflict"])
        self.assertEqual(initial["revision_id"], asset["active_revision_id"])
        self.assertEqual([initial["revision_id"]], [item["revision_id"] for item in revisions])
        self.assertNotIn("secret-apifox-token", result["error"])
        self.assertNotIn("rotated-token-only", result["error"])

    def test_duplicate_sync_reuses_current_sync_id(self):
        first = self.sync_service.start_api_source_sync(self.source["source_id"], spawn=False)
        second = self.sync_service.start_api_source_sync(self.source["source_id"], spawn=False)

        self.assertEqual(first["sync_id"], second["sync_id"])
        self.assertFalse(second["created"])
        self.assertTrue(second["conflict"])

    def test_restart_recovery_and_due_source_selection(self):
        queued = self.sync_service.start_api_source_sync(self.source["source_id"], spawn=False)
        recovered = self.sync_service.recover_stale_api_syncs()
        stored = self.sync_service.get_api_sync(queued["sync_id"])

        self.assertIn(queued["sync_id"], recovered)
        self.assertEqual("failed", stored["status"])
        self.assertNotIn(self.source["source_id"], self.sync_service.due_api_source_ids(now=time.time() + 60))
        self.assertIn(self.source["source_id"], self.sync_service.due_api_source_ids(now=time.time() + 7200))

    def test_guarded_thread_failure_redacts_credentials_and_updates_source_state(self):
        queued = self.sync_service.start_api_source_sync(self.source["source_id"], spawn=False)
        original = self.sync_service.run_api_source_sync

        def crash(sync_id, adapter=None):
            raise RuntimeError("thread failed with secret-apifox-token")

        self.sync_service.run_api_source_sync = crash
        try:
            self.sync_service._run_api_source_sync_guarded(queued["sync_id"])
        finally:
            self.sync_service.run_api_source_sync = original

        stored = self.sync_service.get_api_sync(queued["sync_id"])
        source = self.source_service.get_api_source(self.source["source_id"], masked=True)
        self.assertEqual("failed", stored["status"])
        self.assertNotIn("secret-apifox-token", stored["error"])
        self.assertEqual("failed", source["last_sync_status"])
        self.assertNotIn("secret-apifox-token", source["last_error"])


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


class ApiSourceRouteTests(unittest.TestCase):
    def test_source_and_sync_routes_are_registered_and_token_safe(self):
        from task_server import router
        from task_server.services import api_source_service

        old_dir = api_source_service.API_TESTING_DIR
        temp_dir = tempfile.mkdtemp(prefix="api_source_route_checks_")
        api_source_service.API_TESTING_DIR = temp_dir
        try:
            self.assertIn("/api/api-testing/sources", router.GET_ROUTES)
            self.assertIn("/api/api-testing/sources", router.POST_ROUTES)
            handler = _RouteHandler({
                "source_type": "apifox",
                "name": "3D",
                "project_id": "5904970",
                "access_token": "secret-apifox-token",
            })

            router.POST_ROUTES["/api/api-testing/sources"](handler, {})

            status, payload = handler.responses[-1]
            self.assertEqual(200, status)
            self.assertTrue(payload["source"]["credential_configured"])
            self.assertNotIn("secret-apifox-token", str(payload))
            patterns = [pattern.pattern for pattern, _fn in router._POST_REGEX_ROUTES]
            self.assertIn(r"^/api/api-testing/sources/([^/]+)/sync$", patterns)
            get_patterns = [pattern.pattern for pattern, _fn in router._GET_REGEX_ROUTES]
            self.assertIn(r"^/api/api-testing/syncs/([^/]+)$", get_patterns)
            self.assertIn(r"^/api/api-testing/assets/([^/]+)/revisions$", get_patterns)
            self.assertIn(r"^/api/api-testing/assets/([^/]+)/diff$", get_patterns)
            self.assertIn(r"^/api/api-testing/assets/([^/]+)/impact$", get_patterns)
        finally:
            api_source_service.API_TESTING_DIR = old_dir
            shutil.rmtree(temp_dir, ignore_errors=True)

    def test_source_routes_require_user_authentication(self):
        from task_server import router

        handler = _RouteHandler(authorized=False)
        router.GET_ROUTES["/api/api-testing/sources"](handler, {})

        self.assertEqual(401, handler.responses[-1][0])

    def test_sync_route_returns_not_found_for_a_missing_source(self):
        from task_server import router
        from task_server.services import api_source_service

        old_dir = api_source_service.API_TESTING_DIR
        temp_dir = tempfile.mkdtemp(prefix="api_source_missing_route_checks_")
        api_source_service.API_TESTING_DIR = temp_dir
        try:
            path = "/api/api-testing/sources/missing-source/sync"
            pattern, route = next(
                (pattern, route)
                for pattern, route in router._POST_REGEX_ROUTES
                if pattern.pattern == r"^/api/api-testing/sources/([^/]+)/sync$"
            )
            handler = _RouteHandler()
            route(handler, {}, pattern.match(path))

            self.assertEqual(404, handler.responses[-1][0])
        finally:
            api_source_service.API_TESTING_DIR = old_dir
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
