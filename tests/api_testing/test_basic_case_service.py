from types import SimpleNamespace
from uuid import uuid4

from task_server.api_testing import http
from task_server.api_testing.services.ai_service import AiCaseService
from task_server.api_testing.services.basic_case_service import BasicCaseService


def _environment_revision():
    return SimpleNamespace(default_headers={"Authorization": "Bearer {{ZXBToken}}"})


def _environment_revision_without_headers():
    return SimpleNamespace(default_headers={})


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


def test_basic_positive_payload_uses_documented_business_success_code():
    endpoint = SimpleNamespace(
        method="GET",
        path="/pmc/api/v1/iot/ota/upgrade-info",
        summary="固件更新接口",
        operation={
            "responses": {
                "200": {
                    "content": {
                        "application/json": {
                            "examples": {
                                "success": {
                                    "value": {
                                        "code": 200,
                                        "message": "返回成功",
                                        "data": {"needUpdate": True},
                                    }
                                }
                            },
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "code": {"type": "integer"},
                                    "message": {"type": "string"},
                                    "data": {"type": "object"},
                                },
                            },
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

    assert {"type": "json_path", "path": "$.code", "operator": "equals", "expected": 200, "timeout_ms": 0, "enabled": True} in payload["assertions"]
    assert {"type": "json_path", "path": "$.data", "operator": "exists", "timeout_ms": 0, "enabled": True} in payload["assertions"]


def test_basic_positive_payload_infers_platform_runtime_headers_from_environment_variables():
    endpoint = SimpleNamespace(
        method="POST",
        path="/print3d/api/v1/collection/add",
        summary="添加修改收藏",
        operation={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"example": {"modelSn": "m001"}},
                },
            },
            "responses": {"200": {"description": "OK"}},
        },
    )

    payload = BasicCaseService.build_case_payload(
        endpoint,
        _environment_revision_without_headers(),
        _variables("Biz", "ZXBToken"),
    )

    assert payload["request"]["headers"] == {
        "Biz": "{{Biz}}",
        "Authorization": "{{ZXBToken}}",
    }
    assert payload["request"]["body"] == {"modelSn": "m001"}


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


def test_basic_positive_payload_defaults_platform_code_assertion_for_response_envelope_schema():
    endpoint = SimpleNamespace(
        method="GET",
        path="/print3d/api/v1/devices/info",
        summary="查询设备详情",
        operation={
            "responses": {
                "200": {
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "properties": {
                                    "code": {"type": "integer"},
                                    "msg": {"type": "string"},
                                    "data": {"type": "object"},
                                },
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

    assert {"type": "json_path", "path": "$.code", "operator": "equals", "expected": 0, "timeout_ms": 0, "enabled": True} in payload["assertions"]
    assert {"type": "json_path", "path": "$.data", "operator": "exists", "timeout_ms": 0, "enabled": True} in payload["assertions"]


def test_basic_positive_payload_uses_platform_code_assertion_for_json_object_response_without_shape():
    endpoint = SimpleNamespace(
        method="POST",
        path="/print3d/api/v1/feedback/add",
        summary="提交反馈",
        operation={
            "responses": {
                "200": {
                    "content": {
                        "application/json": {
                            "schema": {"type": "object", "properties": {}},
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

    assert payload["assertions"] == [
        {"type": "status_code", "operator": "equals", "expected": 200, "timeout_ms": 0, "enabled": True},
        {"type": "json_path", "path": "$.code", "operator": "equals", "expected": 0, "timeout_ms": 0, "enabled": True},
    ]


def test_basic_positive_payload_does_not_add_platform_code_assertion_for_array_response():
    endpoint = SimpleNamespace(
        method="GET",
        path="/print3d/api/v1/model/scad2stlStream",
        summary="openScad转stl 流式",
        operation={
            "responses": {
                "200": {
                    "content": {
                        "application/json": {
                            "schema": {"type": "array", "items": {"type": "integer"}},
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

    assert payload["assertions"] == [
        {"type": "status_code", "operator": "equals", "expected": 200, "timeout_ms": 0, "enabled": True},
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


def test_basic_positive_payload_allows_credential_words_inside_api_paths():
    endpoint = SimpleNamespace(
        method="GET",
        path="/pmc/api/v1/iot/qidiAuth/firmware-release-history",
        summary="获取设备密钥",
        operation={"responses": {"200": {"description": "OK"}}},
    )

    payload = BasicCaseService.build_case_payload(
        endpoint,
        _environment_revision(),
        _variables("ZXBToken"),
    )

    AiCaseService._assert_no_literal_secrets(payload)
    assert payload["request"]["path"] == endpoint.path


def test_basic_positive_payload_completes_example_with_required_schema_fields():
    endpoint = SimpleNamespace(
        method="POST",
        path="/print3d/api/v1/knobSetting/knobPrint",
        summary="旋钮打印",
        operation={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "example": {"deviceId": "48CA43BD05C6", "position": "K3"},
                        "schema": {
                            "type": "object",
                            "required": ["deviceSn", "settings"],
                            "properties": {
                                "deviceSn": {"type": "string"},
                                "settings": {
                                    "type": "array",
                                    "minItems": 1,
                                    "items": {
                                        "type": "object",
                                        "required": ["position"],
                                        "properties": {"position": {"type": "string"}},
                                    },
                                },
                                "position": {"type": "string"},
                            },
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
        _variables("ZXBToken"),
    )

    assert payload["request"]["body"] == {
        "deviceId": "48CA43BD05C6",
        "position": "K3",
        "deviceSn": "sample",
        "settings": [{"position": "sample"}],
    }


def test_basic_positive_payload_replaces_schema_incompatible_body_example():
    endpoint = SimpleNamespace(
        method="POST",
        path="/manage/v1/filamentTemperature/edit",
        summary="编辑",
        operation={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "example": "",
                        "schema": {
                            "type": "object",
                            "required": ["id", "name"],
                            "properties": {
                                "id": {"type": "integer"},
                                "name": {"type": "string"},
                            },
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
        _variables("ZXBToken"),
    )

    assert payload["request"]["body"] == {"id": 1, "name": "sample"}


def test_basic_positive_payload_repairs_malformed_placeholder_like_strings():
    endpoint = SimpleNamespace(
        method="POST",
        path="/pmc/api/v1/open/flashReport",
        summary="闪铸上报",
        operation={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "example": {"body": "{\"value\":\"{{not a variable}\"}"},
                        "schema": {
                            "type": "object",
                            "required": ["body"],
                            "properties": {"body": {"type": "string"}},
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
        _variables("ZXBToken"),
    )

    assert payload["request"]["body"] == {"body": "sample"}


def test_basic_positive_payload_conforms_null_schema_values():
    endpoint = SimpleNamespace(
        method="POST",
        path="/print3d/api/v1/printJob/print",
        summary="确认打印接口",
        operation={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "example": {"printParam": {"ms": [{"slot": 1, "srgb": "#fff"}]}},
                        "schema": {
                            "type": "object",
                            "required": ["printParam"],
                            "properties": {
                                "printParam": {
                                    "type": "object",
                                    "required": ["ms"],
                                    "properties": {
                                        "ms": {
                                            "type": "array",
                                            "items": {
                                                "type": "object",
                                                "properties": {
                                                    "slot": {"type": "null"},
                                                    "srgb": {"type": "null"},
                                                },
                                            },
                                        }
                                    },
                                }
                            },
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
        _variables("ZXBToken"),
    )

    assert payload["request"]["body"] == {"printParam": {"ms": [{"slot": None, "srgb": None}]}}


def test_basic_positive_payload_fills_required_field_without_property_schema():
    endpoint = SimpleNamespace(
        method="POST",
        path="/print3d/api/v1/modelDownloads/add",
        summary="添加下载任务",
        operation={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["01JRWE1WMKPQWXEPG7TEGH94V1"],
                            "properties": {},
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
        _variables("ZXBToken"),
    )

    assert payload["request"]["body"] == {"01JRWE1WMKPQWXEPG7TEGH94V1": "sample"}


def test_basic_positive_payload_conforms_parameter_example_to_schema():
    endpoint = SimpleNamespace(
        method="GET",
        path="/print3d/manage/v1/guidance/page",
        summary="新手必学详情",
        operation={
            "parameters": [
                {
                    "name": "categoryId",
                    "in": "query",
                    "required": True,
                    "example": "all",
                    "schema": {"type": "integer"},
                }
            ],
            "responses": {"200": {"description": "OK"}},
        },
    )

    payload = BasicCaseService.build_case_payload(
        endpoint,
        _environment_revision(),
        _variables("ZXBToken"),
    )

    assert payload["request"]["query"] == {"categoryId": 1}


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


def test_http_route_scopes_basic_positive_preview_without_persisting(monkeypatch):
    endpoint_id = str(uuid4())
    environment_revision_id = str(uuid4())
    task_id = str(uuid4())
    preview = {
        "id": f"basic-positive-{endpoint_id}",
        "endpoint_id": endpoint_id,
        "origin": "imported",
        "case": {"name": "基础正向预览"},
    }
    calls = []

    class FakeService:
        def __init__(self, factory):
            calls.append(("service", factory))

        def preview(self, endpoint_ids, environment_revision_id_arg, actor_id):
            calls.append(("preview", endpoint_ids, environment_revision_id_arg, actor_id))
            return [preview]

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
        ("cases", "basic-positive", "preview"),
        {"endpoint_ids": [endpoint_id], "environment_revision_id": environment_revision_id, "task_id": task_id},
        "owner-a",
        SimpleNamespace(),
    )

    assert result == {"case_previews": [preview]}
    assert calls == [
        ("scope-endpoint", "factory", endpoint_id, "owner-a"),
        ("scope-environment", "factory", environment_revision_id, "owner-a"),
        ("scope-task", "factory", task_id, "owner-a"),
        ("service", "factory"),
        ("preview", [endpoint_id], environment_revision_id, "owner-a"),
    ]
