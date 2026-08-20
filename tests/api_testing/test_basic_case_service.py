from types import SimpleNamespace
from uuid import uuid4

from task_server.api_testing import http
from task_server.api_testing.services.basic_case_service import BasicCaseService


def _environment_revision():
    return SimpleNamespace(default_headers={"Authorization": "Bearer {{ZXBToken}}"})


def _variables(*names):
    return tuple(SimpleNamespace(name=name, enabled=True) for name in names)


def test_basic_positive_payload_uses_environment_header_placeholders_and_success_code():
    endpoint = SimpleNamespace(
        method="GET",
        path="/print3d/api/v1/favorite/list",
        summary="查询我的收藏",
        operation={
            "path_parameters": [
                {"name": "Biz", "in": "header", "required": True, "schema": {"type": "string"}},
            ],
            "parameters": [
                {"name": "pageNum", "in": "query", "required": True, "schema": {"type": "integer", "minimum": 1}},
                {"name": "pageSize", "in": "query", "required": False, "schema": {"type": "integer", "default": 20}},
                {"name": "Authorization", "in": "header", "required": True, "schema": {"type": "string"}},
            ],
            "responses": {
                "200": {
                    "content": {
                        "application/json": {
                            "examples": {"success": {"value": {"code": 0, "data": []}}},
                        }
                    }
                }
            },
        },
    )

    payload = BasicCaseService.build_case_payload(
        endpoint,
        _environment_revision(),
        _variables("Biz", "ZXBToken"),
    )

    assert payload["name"] == "查询我的收藏 - 基础正向流程"
    assert payload["request"]["headers"] == {"Biz": "{{Biz}}"}
    assert payload["request"]["query"] == {"pageNum": 1, "pageSize": 20}
    assert payload["request"]["body"] is None
    assert {"type": "status_code", "operator": "equals", "expected": 200, "timeout_ms": 0, "enabled": True} in payload["assertions"]
    assert {"type": "json_path", "path": "$.code", "operator": "equals", "expected": 0, "timeout_ms": 0, "enabled": True} in payload["assertions"]
    assert "ZXBToken" not in repr(payload)


def test_basic_positive_payload_prefers_json_body_example_and_success_flag():
    endpoint = SimpleNamespace(
        method="POST",
        path="/print3d/api/v1/collection/add",
        summary="添加收藏",
        operation={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "example": {"targetId": "synthetic-model-001", "favoriteType": "MODEL"},
                    }
                },
            },
            "responses": {
                "201": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {"success": {"type": "boolean", "default": True}},
                            }
                        }
                    }
                }
            },
        },
    )

    payload = BasicCaseService.build_case_payload(
        endpoint,
        _environment_revision(),
        _variables("ZXBToken"),
    )

    assert payload["request"]["method"] == "POST"
    assert payload["request"]["body"] == {"targetId": "synthetic-model-001", "favoriteType": "MODEL"}
    assert payload["assertions"] == [
        {"type": "status_code", "operator": "equals", "expected": 201, "timeout_ms": 0, "enabled": True},
        {"type": "json_path", "path": "$.success", "operator": "equals", "expected": True, "timeout_ms": 0, "enabled": True},
    ]


def test_basic_positive_payload_replaces_sensitive_body_example_values():
    endpoint = SimpleNamespace(
        method="POST",
        path="/print3d/api/v1/device/token/refresh",
        summary="刷新设备 token",
        operation={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "example": {
                            "deviceSn": "demo-device",
                            "deviceToken": "literal-token-from-doc",
                            "metadata": {"Authorization": "Bearer literal-token-from-doc"},
                        },
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )

    payload = BasicCaseService.build_case_payload(
        endpoint,
        _environment_revision(),
        _variables("deviceToken", "Authorization", "ZXBToken"),
    )

    assert payload["request"]["body"] == {
        "deviceSn": "demo-device",
        "deviceToken": "{{deviceToken}}",
        "metadata": {"Authorization": "{{Authorization}}"},
    }
    assert "literal-token-from-doc" not in repr(payload)


def test_basic_positive_payload_builds_required_json_body_from_schema_and_env_token():
    endpoint = SimpleNamespace(
        method="POST",
        path="/print3d/api/v1/device/bind",
        summary="绑定设备",
        operation={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["deviceSn", "deviceToken"],
                            "properties": {
                                "deviceSn": {"type": "string"},
                                "deviceToken": {"type": "string"},
                                "source": {"type": "string", "enum": ["app"]},
                            },
                        }
                    }
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )

    payload = BasicCaseService.build_case_payload(
        endpoint,
        _environment_revision(),
        _variables("deviceToken", "ZXBToken"),
    )

    assert payload["request"]["body"] == {
        "deviceSn": "sample",
        "deviceToken": "{{deviceToken}}",
        "source": "app",
    }
    assert "device-token" not in repr(payload).lower()


def test_http_route_scopes_basic_positive_generation(monkeypatch):
    endpoint_id = str(uuid4())
    environment_revision_id = str(uuid4())
    task_id = str(uuid4())
    calls = []

    class FakeService:
        def __init__(self, factory):
            calls.append(("service", factory))

        def generate(self, endpoint_ids, environment_revision_id_arg, actor_id):
            calls.append(("generate", endpoint_ids, environment_revision_id_arg, actor_id))
            return [{"id": "version-1", "endpoint_id": endpoint_ids[0]}]

    monkeypatch.setattr(http, "_factory", lambda: "factory")
    monkeypatch.setattr(http, "BasicCaseService", FakeService, raising=False)
    monkeypatch.setattr(http, "_scope_endpoint", lambda factory, record_id, actor: calls.append(("scope-endpoint", factory, record_id, actor)))
    monkeypatch.setattr(http, "_scope_environment_revision", lambda factory, record_id, actor: calls.append(("scope-environment", factory, record_id, actor)))
    def scope_task(factory, record_id, actor):
        calls.append(("scope-task", factory, record_id, actor))
        return SimpleNamespace(
            selected_endpoint_ids=[endpoint_id],
            environment_revision_id=environment_revision_id,
        )

    monkeypatch.setattr(http, "_scope_task", scope_task)

    result = http._post(
        ("cases", "basic-positive"),
        {"endpoint_ids": [endpoint_id], "environment_revision_id": environment_revision_id, "task_id": task_id},
        "owner-a",
        SimpleNamespace(),
    )

    assert result == {"case_versions": [{"id": "version-1", "endpoint_id": endpoint_id}]}
    assert calls == [
        ("scope-endpoint", "factory", endpoint_id, "owner-a"),
        ("scope-environment", "factory", environment_revision_id, "owner-a"),
        ("scope-task", "factory", task_id, "owner-a"),
        ("service", "factory"),
        ("generate", [endpoint_id], environment_revision_id, "owner-a"),
    ]
