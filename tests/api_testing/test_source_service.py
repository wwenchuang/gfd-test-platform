import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import sys
import textwrap
import time

from alembic import command
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
import pytest

from task_server.api_testing.models.project import ApiProject
from task_server.api_testing.models.source import (
    ApiSourceDiff,
    ApiSourceEndpoint,
    ApiSourceRevision,
    ApiSourceSchema,
)
from tests.api_testing.test_migrations import (
    _alembic_config,
    _assert_current_test_schema,
    _create_test_schema,
    _database_url,
    _drop_test_schema,
    _without_database_environment,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "my_favorites_openapi.json"
FAVORITES_OPENAPI = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _audit(actor="admin"):
    return {
        "owner_id": actor,
        "created_by": actor,
        "updated_by": actor,
    }


@pytest.fixture()
def source_database():
    database_url = _database_url()
    created_schemas = set()
    schema_name, schema_url = _create_test_schema(database_url, created_schemas)
    config = _alembic_config(schema_url)
    with _without_database_environment():
        command.upgrade(config, "head")
    _assert_current_test_schema(schema_url, schema_name)
    try:
        yield schema_url
    finally:
        _drop_test_schema(database_url, schema_name, created_schemas)


@pytest.fixture()
def session_factory(source_database):
    engine = create_engine(source_database, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture()
def project(session_factory):
    project = ApiProject(
        name="Task 3 Source Project",
        slug="task-3-source-project",
        description="Synthetic source import tests",
        **_audit(),
    )
    with session_factory.begin() as session:
        session.add(project)
        session.flush()
    return project


@pytest.fixture()
def source_service(session_factory):
    from task_server.api_testing.services.source_service import SourceService

    return SourceService(session_factory)


def _activate_fixture(source_service, project, document=FAVORITES_OPENAPI):
    preview = source_service.preview_refresh(project.id, None, document, "admin")
    return source_service.activate_preview(preview.id, "admin")


def _changed_document():
    document = copy.deepcopy(FAVORITES_OPENAPI)
    document["paths"]["/print3d/api/v1/favorite/add"]["post"]["requestBody"][
        "content"
    ]["application/json"]["schema"] = {
        "type": "object",
        "required": ["targetId", "favoriteType"],
        "properties": {
            "targetId": {"type": "string"},
            "favoriteType": {"type": "integer"},
        },
    }
    return document


def test_openapi_normalization_preserves_supported_contract_metadata():
    from task_server.api_testing.adapters.openapi import normalize_openapi_document

    document = copy.deepcopy(FAVORITES_OPENAPI)
    document["x-root-extension"] = {"enabled": True}
    operation = document["paths"]["/print3d/api/v1/favorite/list"]["get"]
    operation["x-operation-extension"] = "favorite-list"
    document["components"]["schemas"]["FavoriteListResponse"]["properties"][
        "x-business-field"
    ] = {"type": "string"}
    operation["responses"]["200"]["content"]["application/json"]["examples"][
        "success"
    ]["value"]["x-business-value"] = 7
    operation["responses"]["200"]["content"]["application/json"]["examples"][
        "success"
    ]["value"]["$ref"] = "business-value-not-a-json-reference"

    normalized = normalize_openapi_document(document, "source-1")

    assert len(normalized.endpoints) == 3
    assert set(normalized.schemas) == {
        "FavoriteMutationRequest",
        "MutationResponse",
        "FavoriteListResponse",
    }
    list_endpoint = next(item for item in normalized.endpoints if item.operation_id == "favoriteList")
    assert list_endpoint.method == "GET"
    assert list_endpoint.path == "/print3d/api/v1/favorite/list"
    assert list_endpoint.operation["parameters"][0]["name"] == "pageNum"
    assert list_endpoint.operation["path_parameters"][0]["name"] == "Biz"
    assert list_endpoint.operation["responses"]["200"]["content"]["application/json"][
        "examples"
    ]["success"]["value"]["code"] == 0
    assert list_endpoint.operation["security"] == [{"BearerAuth": []}]
    assert normalized.document["servers"][0]["url"] == "https://api.example.test/app"
    assert normalized.document["tags"][0]["name"] == "我的收藏"
    assert normalized.document["security"] == [{"BearerAuth": []}]
    assert normalized.document["x-apifox-project"] == "synthetic-project"
    assert normalized.document["x-root-extension"] == {"enabled": True}
    assert list_endpoint.operation["x-apifox-folder"] == "favorites"
    assert list_endpoint.operation["x-operation-extension"] == "favorite-list"
    assert normalized.document["components"]["schemas"]["FavoriteListResponse"][
        "properties"
    ]["x-business-field"] == {"type": "string"}
    assert normalized.document["paths"]["/print3d/api/v1/favorite/list"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["examples"]["success"]["value"][
        "x-business-value"
    ] == 7
    assert normalized.document["paths"]["/print3d/api/v1/favorite/list"]["get"][
        "responses"
    ]["200"]["content"]["application/json"]["examples"]["success"]["value"][
        "$ref"
    ] == "business-value-not-a-json-reference"
    saved_operation = normalized.document["paths"]["/print3d/api/v1/favorite/list"]["get"]
    assert "path_parameters" not in saved_operation
    assert "resolved_dependencies" not in saved_operation


def test_openapi_normalization_uses_apifox_folder_when_tags_are_missing():
    from task_server.api_testing.adapters.openapi import normalize_openapi_document

    document = copy.deepcopy(FAVORITES_OPENAPI)
    list_operation = document["paths"]["/print3d/api/v1/favorite/list"]["get"]
    list_operation.pop("tags")
    list_operation["x-apifox-folder"] = {
        "name": "我的收藏",
        "path": ["家用业务", "app接口", "我的收藏"],
    }

    normalized = normalize_openapi_document(document, "source-1")

    list_endpoint = next(item for item in normalized.endpoints if item.operation_id == "favoriteList")
    assert list_endpoint.tags == ("家用业务", "app接口", "我的收藏")


def test_openapi_normalization_merges_apifox_folder_with_existing_tags():
    from task_server.api_testing.adapters.openapi import normalize_openapi_document

    document = copy.deepcopy(FAVORITES_OPENAPI)
    list_operation = document["paths"]["/print3d/api/v1/favorite/list"]["get"]
    list_operation["tags"] = ["我的收藏"]
    list_operation["x-apifox-folder"] = {
        "name": "我的收藏",
        "path": ["家用业务", "app接口", "我的收藏"],
    }

    normalized = normalize_openapi_document(document, "source-1")

    list_endpoint = next(item for item in normalized.endpoints if item.operation_id == "favoriteList")
    assert list_endpoint.tags == ("家用业务", "app接口", "我的收藏")


def test_openapi_normalization_preserves_parameter_and_body_examples_for_drafts():
    from task_server.api_testing.adapters.openapi import normalize_openapi_document

    document = copy.deepcopy(FAVORITES_OPENAPI)
    document["paths"]["/print3d/api/v1/devices/workInfo"] = {
        "get": {
            "operationId": "deviceWorkInfo",
            "summary": "设备工作详情",
            "tags": ["设备"],
            "parameters": [
                {
                    "name": "deviceSn",
                    "in": "query",
                    "required": True,
                    "description": "设备序列号",
                    "schema": {"type": "string"},
                    "example": "1234567890123456789",
                },
                {
                    "name": "source",
                    "in": "query",
                    "required": True,
                    "description": "校准场景下 =calibration",
                    "schema": {"type": "string"},
                    "example": "calibration",
                },
                {
                    "name": "printSn",
                    "in": "query",
                    "required": False,
                    "schema": {"type": "string"},
                    "example": "1441818241848516608",
                },
            ],
            "responses": {"200": {"description": "ok"}},
        }
    }
    add_operation = document["paths"]["/print3d/api/v1/favorite/add"]["post"]
    add_operation["requestBody"]["content"]["application/json"]["example"] = {
        "modelSn": "m001"
    }

    normalized = normalize_openapi_document(document, "source-1")

    work_info = next(item for item in normalized.endpoints if item.operation_id == "deviceWorkInfo")
    assert [
        (item["name"], item["in"], item.get("required"), item.get("example"), item.get("description"))
        for item in work_info.operation["parameters"]
    ] == [
        ("deviceSn", "query", True, "1234567890123456789", "设备序列号"),
        ("source", "query", True, "calibration", "校准场景下 =calibration"),
        ("printSn", "query", False, "1441818241848516608", None),
    ]
    add_endpoint = next(item for item in normalized.endpoints if item.operation_id == "favoriteAdd")
    assert add_endpoint.operation["requestBody"]["content"]["application/json"]["example"] == {
        "modelSn": "m001"
    }


def _reference_chain_document():
    return {
        "openapi": "3.1.0",
        "info": {"title": "Reference Chain", "version": "1.0.0"},
        "paths": {
            "/favorites/{favoriteId}": {
                "$ref": "#/components/pathItems/Favorite~1Path~0Item"
            },
            "/favorites/{favoriteId}/copy": {
                "parameters": [{"$ref": "#/components/parameters/FavoriteId"}],
                "post": {
                    "operationId": "copyFavorite",
                    "requestBody": {"$ref": "#/components/requestBodies/FavoriteBody"},
                    "responses": {"200": {"$ref": "#/components/responses/FavoriteOk"}},
                },
            },
        },
        "components": {
            "pathItems": {
                "Favorite/Path~Item": {
                    "parameters": [{"$ref": "#/components/parameters/FavoriteId"}],
                    "get": {
                        "operationId": "getFavorite",
                        "responses": {
                            "200": {"$ref": "#/components/responses/FavoriteOk"}
                        },
                    },
                }
            },
            "parameters": {
                "FavoriteId": {
                    "name": "favoriteId",
                    "in": "path",
                    "required": True,
                    "schema": {"$ref": "#/components/schemas/Favorite~1Id"},
                }
            },
            "requestBodies": {
                "FavoriteBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Favorite"}
                        }
                    },
                }
            },
            "responses": {
                "FavoriteOk": {
                    "description": "ok",
                    "headers": {
                        "X-Trace": {"$ref": "#/components/headers/TraceHeader"}
                    },
                    "content": {
                        "application/json": {
                            "schema": {"$ref": "#/components/schemas/Favorite"}
                        }
                    },
                }
            },
            "headers": {
                "TraceHeader": {
                    "description": "trace",
                    "schema": {"type": "string"},
                }
            },
            "schemas": {
                "Favorite/Id": {"type": "string"},
                "Favorite": {
                    "allOf": [
                        {"$ref": "#/components/schemas/FavoriteBase"},
                        {"$ref": "#/components/schemas/FavoriteCycleA"},
                    ]
                },
                "FavoriteBase": {
                    "type": "object",
                    "properties": {"id": {"$ref": "#/components/schemas/Favorite~1Id"}},
                },
                "FavoriteCycleA": {"$ref": "#/components/schemas/FavoriteCycleB"},
                "FavoriteCycleB": {"$ref": "#/components/schemas/FavoriteCycleA"},
            },
        },
    }


def test_local_reference_closure_supports_path_items_chains_cycles_and_escaped_pointers():
    from task_server.api_testing.adapters.openapi import normalize_openapi_document

    normalized = normalize_openapi_document(_reference_chain_document(), "source-1")

    assert {item.operation_id for item in normalized.endpoints} == {
        "getFavorite",
        "copyFavorite",
    }
    endpoint = next(item for item in normalized.endpoints if item.operation_id == "getFavorite")
    dependencies = endpoint.operation["resolved_dependencies"]
    assert "#/components/pathItems/Favorite~1Path~0Item" in dependencies
    assert "#/components/parameters/FavoriteId" in dependencies
    assert "#/components/responses/FavoriteOk" in dependencies
    assert "#/components/headers/TraceHeader" in dependencies
    assert "#/components/schemas/Favorite~1Id" in dependencies
    assert "#/components/schemas/FavoriteCycleA" in dependencies
    assert "#/components/schemas/FavoriteCycleB" in dependencies
    assert normalized.document == _reference_chain_document()


def test_local_reference_supports_percent_encoded_uri_fragment_segments():
    from task_server.api_testing.adapters.openapi import normalize_openapi_document

    document = {
        "openapi": "3.0.1",
        "info": {"title": "Apifox encoded references", "version": "1.0.0"},
        "paths": {
            "/favorites": {
                "get": {
                    "operationId": "listFavorites",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Resp%3F"}
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {"schemas": {"Resp?": {"type": "object"}}},
    }

    normalized = normalize_openapi_document(document, "source-1")

    assert len(normalized.endpoints) == 1
    assert normalized.endpoints[0].operation["resolved_dependencies"] == {
        "#/components/schemas/Resp%3F": {"type": "object"}
    }


def test_unresolved_local_reference_is_rejected_but_external_reference_is_preserved():
    from task_server.api_testing.adapters.openapi import (
        OpenApiValidationError,
        normalize_openapi_document,
    )

    unresolved = _reference_chain_document()
    unresolved["components"]["responses"]["FavoriteOk"]["headers"]["X-Trace"][
        "$ref"
    ] = "#/components/headers/Missing"
    with pytest.raises(OpenApiValidationError, match="Unresolved local reference.*Missing"):
        normalize_openapi_document(unresolved, "source-1")

    external = _reference_chain_document()
    external["components"]["schemas"]["FavoriteBase"]["properties"]["remote"] = {
        "$ref": "https://schemas.example.test/common.json#/Remote"
    }
    normalized = normalize_openapi_document(external, "source-1")
    endpoint = next(item for item in normalized.endpoints if item.operation_id == "getFavorite")
    assert endpoint.operation["external_references"] == [
        "https://schemas.example.test/common.json#/Remote"
    ]
    assert normalized.document["components"]["schemas"]["FavoriteBase"]["properties"][
        "remote"
    ]["$ref"].startswith("https://")


def test_unresolved_local_reference_in_unused_component_is_rejected():
    from task_server.api_testing.adapters.openapi import (
        OpenApiValidationError,
        normalize_openapi_document,
    )

    document = copy.deepcopy(FAVORITES_OPENAPI)
    document["components"]["schemas"]["Unused"] = {
        "$ref": "#/components/schemas/Missing"
    }

    with pytest.raises(OpenApiValidationError, match="Unresolved local reference.*Missing"):
        normalize_openapi_document(document, "source-1")


def _named_schema_properties_document():
    document = copy.deepcopy(FAVORITES_OPENAPI)
    document["components"]["schemas"].update(
        {
            "NamedPropertiesEnvelope": {
                "type": "object",
                "properties": {
                    "value": {"$ref": "#/components/schemas/ValuePayload"},
                    "example": {"$ref": "#/components/schemas/ExamplePayload"},
                    "examples": {"$ref": "#/components/schemas/ExamplesPayload"},
                },
            },
            "ValuePayload": {"type": "string", "description": "value payload"},
            "ExamplePayload": {"type": "string", "description": "example payload"},
            "ExamplesPayload": {"type": "string", "description": "examples payload"},
        }
    )
    document["paths"]["/print3d/api/v1/favorite/list"]["get"]["responses"]["200"][
        "content"
    ]["application/json"]["schema"] = {
        "$ref": "#/components/schemas/NamedPropertiesEnvelope"
    }
    return document


def test_schema_properties_named_value_example_and_examples_resolve_dependencies():
    from task_server.api_testing.adapters.openapi import normalize_openapi_document

    normalized = normalize_openapi_document(_named_schema_properties_document(), "source-1")
    endpoint = next(item for item in normalized.endpoints if item.operation_id == "favoriteList")

    assert {
        "#/components/schemas/ValuePayload",
        "#/components/schemas/ExamplePayload",
        "#/components/schemas/ExamplesPayload",
    }.issubset(endpoint.operation["resolved_dependencies"])


@pytest.mark.parametrize("property_name", ["value", "example", "examples"])
def test_unresolved_reference_below_named_schema_property_is_rejected(property_name):
    from task_server.api_testing.adapters.openapi import (
        OpenApiValidationError,
        normalize_openapi_document,
    )

    document = _named_schema_properties_document()
    document["components"]["schemas"]["NamedPropertiesEnvelope"]["properties"][
        property_name
    ]["$ref"] = "#/components/schemas/Missing"

    with pytest.raises(OpenApiValidationError, match="Unresolved local reference.*Missing"):
        normalize_openapi_document(document, "source-1")


def test_named_schema_property_component_changes_are_included_in_diff(
    source_service, project
):
    document = _named_schema_properties_document()
    active = _activate_fixture(source_service, project, document)

    for schema_name in ("ValuePayload", "ExamplePayload", "ExamplesPayload"):
        changed = copy.deepcopy(document)
        changed["components"]["schemas"][schema_name]["description"] += " changed"
        preview = source_service.preview_refresh(
            project.id, active.source_id, changed, "admin"
        )
        assert preview.changed_count == 1, schema_name
        assert preview.changes[0].operation_id == "favoriteList"
        assert "resolved_dependencies" in preview.changes[0].changed_fields


def test_pure_cyclic_path_item_reference_is_rejected():
    from task_server.api_testing.adapters.openapi import (
        OpenApiValidationError,
        normalize_openapi_document,
    )

    document = {
        "openapi": "3.1.0",
        "info": {"title": "Cyclic Path Items", "version": "1"},
        "paths": {"/cycle": {"$ref": "#/components/pathItems/A"}},
        "components": {
            "pathItems": {
                "A": {"$ref": "#/components/pathItems/B"},
                "B": {"$ref": "#/components/pathItems/A"},
            }
        },
    }

    with pytest.raises(OpenApiValidationError, match="Cyclic local Path Item"):
        normalize_openapi_document(document, "source-1")


def test_cyclic_path_item_with_concrete_operation_uses_sibling_operation():
    from task_server.api_testing.adapters.openapi import normalize_openapi_document

    document = {
        "openapi": "3.1.0",
        "info": {"title": "Concrete Cyclic Path Item", "version": "1"},
        "paths": {"/cycle": {"$ref": "#/components/pathItems/A"}},
        "components": {
            "pathItems": {
                "A": {
                    "$ref": "#/components/pathItems/B",
                    "get": {
                        "operationId": "cycleWithConcreteSibling",
                        "responses": {"200": {"description": "ok"}},
                    },
                },
                "B": {"$ref": "#/components/pathItems/A"},
            }
        },
    }

    normalized = normalize_openapi_document(document, "source-1")

    assert [item.operation_id for item in normalized.endpoints] == [
        "cycleWithConcreteSibling"
    ]


def test_openapi_31_is_supported_and_path_parameter_semantics_are_preserved():
    from task_server.api_testing.adapters.openapi import normalize_openapi_document

    document = copy.deepcopy(FAVORITES_OPENAPI)
    document["openapi"] = "3.1.0"
    document["jsonSchemaDialect"] = "https://json-schema.org/draft/2020-12/schema"
    operation = document["paths"].pop("/print3d/api/v1/favorite/list")
    operation["parameters"] = [
        {
            "name": "favoriteId",
            "in": "path",
            "required": True,
            "schema": {"type": "string"},
        }
    ]
    document["paths"]["/print3d/api/v1/favorite/{favoriteId}"] = operation

    normalized = normalize_openapi_document(document, "source-1")
    endpoint = next(item for item in normalized.endpoints if item.operation_id == "favoriteList")

    assert endpoint.path == "/print3d/api/v1/favorite/{favoriteId}"
    assert endpoint.normalized_path == "/print3d/api/v1/favorite/{favoriteId}"
    assert normalized.document["jsonSchemaDialect"].endswith("2020-12/schema")


@pytest.mark.parametrize(
    "document, message",
    [
        ({"info": {}, "paths": {}}, "openapi"),
        ({"openapi": "3.0.3", "paths": {}}, "info"),
        ({"openapi": "3.0.3", "info": {"title": "x", "version": "1"}}, "paths"),
        ({"openapi": "2.0", "info": {"title": "x", "version": "1"}, "paths": {}}, "3.0 or 3.1"),
        ({"openapi": "3.1.not-semver", "info": {"title": "x", "version": "1"}, "paths": {}}, "3.0 or 3.1"),
        ({"openapi": "3.2.0", "info": {"title": "x", "version": "1"}, "paths": {}}, "3.0 or 3.1"),
        ({"openapi": "3.0.3", "info": {"title": "x", "version": "1"}, "paths": {"not/a/path": {"get": {}}}}, "start with"),
        ({"openapi": "3.0.3", "info": {"title": "x", "version": "1"}, "paths": {"/ok": {"fetch": {}}}}, "method"),
    ],
)
def test_openapi_validation_rejects_incomplete_or_invalid_documents(document, message):
    from task_server.api_testing.adapters.openapi import (
        OpenApiValidationError,
        normalize_openapi_document,
    )

    with pytest.raises(OpenApiValidationError, match=message):
        normalize_openapi_document(document, "source-1")


def test_openapi_validation_rejects_duplicate_normalized_method_path_identity():
    from task_server.api_testing.adapters.openapi import (
        OpenApiValidationError,
        normalize_openapi_document,
    )

    document = {
        "openapi": "3.0.3",
        "info": {"title": "duplicate", "version": "1"},
        "paths": {
            "/favorite": {"get": {"operationId": "first", "responses": {}}},
            " /favorite ": {"GET": {"operationId": "second", "responses": {}}},
        },
    }

    with pytest.raises(OpenApiValidationError, match="duplicate method/path"):
        normalize_openapi_document(document, "source-1")


@pytest.mark.parametrize(
    "parameters, message",
    [
        ([], "exactly match"),
        (
            [
                {
                    "name": "otherId",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "exactly match",
        ),
        (
            [
                {
                    "name": "favoriteId",
                    "in": "query",
                    "required": True,
                    "schema": {"type": "string"},
                }
            ],
            "exactly match",
        ),
        (
            [
                {
                    "name": "favoriteId",
                    "in": "path",
                    "required": False,
                    "schema": {"type": "string"},
                }
            ],
            "required true",
        ),
        (
            [
                {
                    "name": "favoriteId",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                },
                {
                    "name": "favoriteId",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                },
            ],
            "unique",
        ),
    ],
)
def test_openapi_validation_rejects_invalid_path_parameter_contract(parameters, message):
    from task_server.api_testing.adapters.openapi import (
        OpenApiValidationError,
        normalize_openapi_document,
    )

    document = {
        "openapi": "3.0.3",
        "info": {"title": "path parameters", "version": "1"},
        "paths": {
            "/favorites/{favoriteId}": {
                "get": {
                    "operationId": "favorite",
                    "parameters": parameters,
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }

    with pytest.raises(OpenApiValidationError, match=message):
        normalize_openapi_document(document, "source-1")


def test_component_parameter_reference_satisfies_path_parameter_contract():
    from task_server.api_testing.adapters.openapi import normalize_openapi_document

    document = {
        "openapi": "3.0.3",
        "info": {"title": "path parameter ref", "version": "1"},
        "paths": {
            "/favorites/{favoriteId}": {
                "parameters": [{"$ref": "#/components/parameters/FavoriteId"}],
                "get": {
                    "operationId": "favorite",
                    "responses": {"200": {"description": "ok"}},
                },
            }
        },
        "components": {
            "parameters": {
                "FavoriteId": {
                    "name": "favoriteId",
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                }
            }
        },
    }

    normalized = normalize_openapi_document(document, "source-1")

    assert normalized.endpoints[0].operation["path_parameters"][0]["name"] == "favoriteId"


def test_stable_endpoint_key_uses_unambiguous_encoding():
    from task_server.api_testing.adapters.openapi import stable_endpoint_key

    first = stable_endpoint_key("ab", "c", "get", "/favorite/{favoriteId}")
    second = stable_endpoint_key("a", "bc", "GET", "/favorite/{favoriteId}")
    same = stable_endpoint_key("ab", "c", "GET", "/favorite/{favoriteId}")

    assert len(first) == 64
    assert first != second
    assert first == same


def test_preview_does_not_replace_active_revision(source_service, project, session_factory):
    preview = source_service.preview_refresh(project.id, None, FAVORITES_OPENAPI, "admin")

    assert preview.added_count == 3
    assert preview.changed_count == 0
    assert preview.removed_count == 0
    assert preview.expires_at is not None
    assert source_service.get_active_revision(project.id, preview.source_id) is None

    with session_factory() as session:
        candidate = session.get(ApiSourceRevision, preview.candidate_revision_id)
        assert candidate.status == "candidate"
        assert session.scalar(
            select(func.count()).select_from(ApiSourceEndpoint).where(
                ApiSourceEndpoint.revision_id == candidate.id
            )
        ) == 3


def test_preview_normalizes_large_openapi_document_only_once(
    source_service, project, monkeypatch
):
    from task_server.api_testing.services import source_service as source_service_module

    original = source_service_module.normalize_openapi_document
    calls = []

    def observed(document, source_id):
        calls.append(source_id)
        return original(document, source_id)

    monkeypatch.setattr(
        source_service_module,
        "normalize_openapi_document",
        observed,
    )

    preview = source_service.preview_refresh(
        project.id,
        None,
        copy.deepcopy(FAVORITES_OPENAPI),
        "admin",
    )
    revision = source_service.get_revision(preview.candidate_revision_id)

    assert calls == ["validation"]
    assert all(endpoint.stable_key for endpoint in revision.endpoints)
    assert all(
        endpoint.stable_key
        != original(FAVORITES_OPENAPI, "validation").endpoints[index].stable_key
        for index, endpoint in enumerate(revision.endpoints)
    )


def test_canonical_extensions_and_business_keys_round_trip_through_postgresql(
    source_service, project
):
    document = copy.deepcopy(FAVORITES_OPENAPI)
    document["x-root-extension"] = {"source": "synthetic"}
    operation = document["paths"]["/print3d/api/v1/favorite/list"]["get"]
    operation["x-operation-extension"] = {"owner": "qa"}
    document["components"]["schemas"]["FavoriteListResponse"]["properties"][
        "x-business-field"
    ] = {"type": "string"}
    operation["responses"]["200"]["content"]["application/json"]["examples"][
        "success"
    ]["value"]["x-business-value"] = "kept"

    preview = source_service.preview_refresh(project.id, None, document, "admin")
    stored = source_service.get_revision(preview.candidate_revision_id).normalized_document
    revision = source_service.get_revision(preview.candidate_revision_id)
    endpoints = {item.operation_id: item for item in revision.endpoints}

    assert stored == document
    assert endpoints["favoriteAdd"].operation["requestBody"]["content"][
        "application/json"
    ]["example"] == {
        "targetId": "synthetic-model-001",
        "favoriteType": "MODEL",
    }
    assert endpoints["favoriteCancel"].operation["requestBody"]["content"][
        "application/json"
    ]["examples"]["model"]["value"] == {
        "targetId": "synthetic-model-001",
        "favoriteType": "MODEL",
    }


def test_repeated_document_after_activation_has_deterministic_no_change_diff(
    source_service, project
):
    active = _activate_fixture(source_service, project)

    preview = source_service.preview_refresh(
        project.id, active.source_id, copy.deepcopy(FAVORITES_OPENAPI), "admin"
    )

    assert preview.document_hash == active.document_hash
    assert preview.added_count == 0
    assert preview.changed_count == 0
    assert preview.removed_count == 0
    assert preview.changes == ()
    assert source_service.get_active_revision(project.id, active.source_id).id == active.id


def test_activation_response_does_not_duplicate_full_document_and_endpoint_catalog(
    source_service, project
):
    preview = source_service.preview_refresh(
        project.id, None, copy.deepcopy(FAVORITES_OPENAPI), "admin"
    )
    active = source_service.activate_preview(
        preview.id, "admin", include_content=False
    )
    fetched = source_service.get_revision(active.id)

    assert active.normalized_document == {}
    assert active.endpoints == ()
    assert fetched.normalized_document["paths"]
    assert fetched.endpoints


def test_diff_reports_added_changed_and_removed_endpoints(source_service, project):
    active = _activate_fixture(source_service, project)
    changed = _changed_document()
    changed["paths"].pop("/print3d/api/v1/favorite/cancel")
    changed["paths"]["/print3d/api/v1/favorite/status"] = {
        "get": {
            "operationId": "favoriteStatus",
            "summary": "查询收藏状态",
            "responses": {"200": {"description": "status"}},
        }
    }

    preview = source_service.preview_refresh(project.id, active.source_id, changed, "admin")

    assert preview.added_count == 1
    assert preview.changed_count == 1
    assert preview.removed_count == 1
    assert {item.change_type for item in preview.changes} == {"added", "changed", "removed"}
    changed_item = next(item for item in preview.changes if item.change_type == "changed")
    assert changed_item.operation_id == "favoriteAdd"
    assert "requestBody" in changed_item.changed_fields


def test_component_schema_change_marks_referencing_endpoints_changed(
    source_service, project
):
    active = _activate_fixture(source_service, project)
    changed = copy.deepcopy(FAVORITES_OPENAPI)
    changed["components"]["schemas"]["FavoriteMutationRequest"]["properties"][
        "favoriteType"
    ]["enum"] = ["MODEL", "DOCUMENT"]

    preview = source_service.preview_refresh(project.id, active.source_id, changed, "admin")

    assert preview.added_count == 0
    assert preview.changed_count == 2
    assert preview.removed_count == 0
    assert {item.operation_id for item in preview.changes} == {
        "favoriteAdd",
        "favoriteCancel",
    }
    assert all("resolved_dependencies" in item.changed_fields for item in preview.changes)


def test_component_chain_change_marks_every_referencing_endpoint_changed(
    source_service, project
):
    document = _reference_chain_document()
    active = _activate_fixture(source_service, project, document)
    changed = copy.deepcopy(document)
    changed["components"]["headers"]["TraceHeader"]["description"] = "new trace"

    preview = source_service.preview_refresh(project.id, active.source_id, changed, "admin")

    assert preview.added_count == 0
    assert preview.changed_count == 2
    assert preview.removed_count == 0
    assert {item.operation_id for item in preview.changes} == {
        "getFavorite",
        "copyFavorite",
    }
    assert all("resolved_dependencies" in item.changed_fields for item in preview.changes)


def test_openapi_31_boolean_schemas_persist_exactly_in_postgresql(
    source_service, project, session_factory
):
    document = {
        "openapi": "3.1.0",
        "info": {"title": "Boolean Schemas", "version": "1"},
        "paths": {
            "/boolean": {
                "get": {
                    "operationId": "booleanSchema",
                    "responses": {
                        "200": {
                            "description": "ok",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/Nested"}
                                }
                            },
                        }
                    },
                }
            }
        },
        "components": {
            "schemas": {
                "Anything": True,
                "Nothing": False,
                "Nested": {
                    "type": "object",
                    "properties": {"allowed": True, "denied": False},
                },
            }
        },
    }

    preview = source_service.preview_refresh(project.id, None, document, "admin")

    with session_factory() as session:
        schemas = {
            item.schema_key: item.schema
            for item in session.scalars(
                select(ApiSourceSchema).where(
                    ApiSourceSchema.revision_id == preview.candidate_revision_id
                )
            )
        }
    assert schemas == document["components"]["schemas"]


def test_openapi_30_rejects_top_level_boolean_schema():
    from task_server.api_testing.adapters.openapi import (
        OpenApiValidationError,
        normalize_openapi_document,
    )

    document = copy.deepcopy(FAVORITES_OPENAPI)
    document["components"]["schemas"]["InvalidBoolean"] = True

    with pytest.raises(OpenApiValidationError, match="OpenAPI 3.0.*boolean schema"):
        normalize_openapi_document(document, "source-1")


def test_activation_preserves_old_revision_and_detects_changed_schema(
    source_service, project, session_factory
):
    first = _activate_fixture(source_service, project)
    preview = source_service.preview_refresh(
        first.project_id, first.source_id, _changed_document(), "admin"
    )
    assert preview.changed_count == 1

    second = source_service.activate_preview(preview.id, "admin")

    assert second.id != first.id
    assert source_service.get_revision(first.id).status == "superseded"
    assert source_service.get_active_revision(project.id, first.source_id).id == second.id
    with session_factory() as session:
        old_count = session.scalar(
            select(func.count()).select_from(ApiSourceEndpoint).where(
                ApiSourceEndpoint.revision_id == first.id
            )
        )
        new_count = session.scalar(
            select(func.count()).select_from(ApiSourceEndpoint).where(
                ApiSourceEndpoint.revision_id == second.id
            )
        )
    assert old_count == 3
    assert new_count == 3


def test_stale_preview_cannot_overwrite_a_newer_activation(
    source_service, project, session_factory
):
    first = _activate_fixture(source_service, project)
    preview_one = source_service.preview_refresh(
        project.id, first.source_id, _changed_document(), "admin"
    )
    another_change = copy.deepcopy(FAVORITES_OPENAPI)
    another_change["paths"]["/print3d/api/v1/favorite/list"]["get"]["summary"] = (
        "查询我的收藏（新版）"
    )
    preview_two = source_service.preview_refresh(
        project.id, first.source_id, another_change, "admin"
    )

    activated = source_service.activate_preview(preview_one.id, "admin")

    from task_server.api_testing.services.source_service import StaleSourcePreviewError

    with pytest.raises(StaleSourcePreviewError, match="active revision changed"):
        source_service.activate_preview(preview_two.id, "admin")
    assert source_service.get_active_revision(project.id, first.source_id).id == activated.id
    with session_factory() as session:
        stale_diff = session.get(ApiSourceDiff, preview_two.id)
        stale_candidate = session.get(ApiSourceRevision, preview_two.candidate_revision_id)
        assert stale_diff.status == "preview"
        assert stale_candidate.status == "candidate"


def test_expired_preview_is_rejected_without_changing_active_revision(
    source_service, project, session_factory
):
    first = _activate_fixture(source_service, project)
    preview = source_service.preview_refresh(
        project.id, first.source_id, _changed_document(), "admin"
    )
    with session_factory.begin() as session:
        diff = session.get(ApiSourceDiff, preview.id)
        diff.expires_at = datetime.now(timezone.utc) - timedelta(hours=2)

    from task_server.api_testing.services.source_service import SourcePreviewExpiredError

    with pytest.raises(SourcePreviewExpiredError, match="expired"):
        source_service.activate_preview(preview.id, "admin")
    assert source_service.get_active_revision(project.id, first.source_id).id == first.id


def test_active_revision_read_is_scoped_to_selected_source(source_service, project):
    first = _activate_fixture(source_service, project)
    second_document = copy.deepcopy(FAVORITES_OPENAPI)
    second_document["info"]["title"] = "Synthetic Favorites Secondary API"
    second = _activate_fixture(source_service, project, second_document)

    assert source_service.get_active_revision(project.id, first.source_id).id == first.id
    assert source_service.get_active_revision(project.id, second.source_id).id == second.id
    assert source_service.get_active_revision(project.id, "missing-source") is None


def _write_apifox_test_cli(tmp_path):
    script = tmp_path / "synthetic_apifox_cli.py"
    script.write_text(
        textwrap.dedent(
            """
            import json
            import os
            from pathlib import Path
            import subprocess
            import sys
            import time

            mode = sys.argv[1]
            marker = Path(sys.argv[2])
            marker.write_text(os.getcwd(), encoding="utf-8")
            Path(str(marker) + ".pid").write_text(str(os.getpid()), encoding="utf-8")
            output = Path(sys.argv[sys.argv.index("--output") + 1])
            environment = Path(sys.argv[sys.argv.index("--environment-output") + 1])
            document = {
                "openapi": "3.0.3",
                "info": {"title": "Synthetic CLI API", "version": "1"},
                "paths": {},
            }
            metadata = {
                "name": "Synthetic Production",
                "variables": {"Biz": "ZXB"},
            }
            if mode == "stdout-oversize":
                os.write(1, b"x" * 4096)
                time.sleep(5)
            elif mode == "stderr-oversize":
                os.write(2, os.environ["APIFOX_ACCESS_TOKEN"].encode() + b"-" + b"x" * 4096)
                time.sleep(5)
            elif mode == "boundary-failure":
                os.write(2, ("界" * 19 + os.environ["APIFOX_ACCESS_TOKEN"] + "尾").encode())
                raise SystemExit(3)
            elif mode == "capture-boundary-overflow":
                os.write(2, ("界" * 19 + os.environ["APIFOX_ACCESS_TOKEN"] + "尾").encode())
                time.sleep(30)
            elif mode in {"timeout", "selector-failure"}:
                time.sleep(30)
            elif mode in {"grandchild-overflow", "parent-exits-grandchild"}:
                child = subprocess.Popen(
                    [
                        sys.executable,
                        "-c",
                        "import time; time.sleep(30)",
                        "midscene-apifox-grandchild",
                    ]
                )
                Path(str(marker) + ".grandchild.pid").write_text(
                    str(child.pid), encoding="utf-8"
                )
                if mode == "grandchild-overflow":
                    os.write(1, b"x" * 4096)
                    time.sleep(30)
                raise SystemExit(0)
            elif mode == "openapi-oversize":
                output.write_bytes(b"x" * 4096)
            elif mode == "environment-oversize":
                output.write_text(json.dumps(document), encoding="utf-8")
                environment.write_bytes(b"x" * 4096)
            elif mode == "failure":
                os.write(2, ("failed " + os.environ["APIFOX_ACCESS_TOKEN"]).encode())
                raise SystemExit(3)
            elif mode == "leak":
                document["x-debug-access-token"] = os.environ["APIFOX_ACCESS_TOKEN"]
                output.write_text(json.dumps(document), encoding="utf-8")
            else:
                output.write_text(json.dumps(document), encoding="utf-8")
                environment.write_text(json.dumps(metadata), encoding="utf-8")
            """
        ),
        encoding="utf-8",
    )
    return script


def _apifox_adapter_for_mode(tmp_path, mode, **limits):
    from task_server.api_testing.adapters.apifox import ApifoxAdapter

    marker = tmp_path / (mode + "-cwd.txt")
    script = _write_apifox_test_cli(tmp_path)
    timeout_seconds = limits.pop("timeout_seconds", 17)
    adapter = ApifoxAdapter(
        command=[sys.executable, str(script), mode, str(marker)],
        timeout_seconds=timeout_seconds,
        **limits,
    )
    return adapter, marker


def _read_process_pid(path, timeout=2):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if path.exists():
            return int(path.read_text(encoding="utf-8"))
        time.sleep(0.01)
    pytest.fail("synthetic process did not publish pid: %s" % path)


def _assert_process_absent(pid, timeout=3):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    pytest.fail("process or zombie still exists: %s" % pid)


def test_apifox_adapter_uses_explicit_safe_subprocess_and_returns_metadata(
    monkeypatch, tmp_path
):
    from task_server.api_testing.adapters.apifox import ApifoxConnection

    token = "synthetic-secret-token"
    monkeypatch.setenv("QWEN_API_KEY", "must-not-reach-apifox")
    monkeypatch.setenv("API_TESTING_DATABASE_URL", "must-not-reach-apifox")
    adapter, marker = _apifox_adapter_for_mode(tmp_path, "success")
    result = adapter.fetch(
        token,
        ApifoxConnection(
            project_id="project-123",
            branch_id="branch-main",
            environment_id="environment-prod",
        ),
    )

    assert result.document["info"]["title"] == "Synthetic CLI API"
    assert result.environment_metadata["name"] == "Synthetic Production"
    assert result.identifiers == {
        "project_id": "project-123",
        "branch_id": "branch-main",
        "environment_id": "environment-prod",
    }
    temporary_directory = Path(marker.read_text(encoding="utf-8"))
    assert not temporary_directory.exists()


def test_apifox_adapter_redacts_token_from_failures(tmp_path):
    from task_server.api_testing.adapters.apifox import (
        ApifoxAdapter,
        ApifoxAdapterError,
        ApifoxConnection,
    )

    token = "synthetic-secret-token"

    adapter, marker = _apifox_adapter_for_mode(tmp_path, "failure")
    with pytest.raises(ApifoxAdapterError) as captured:
        adapter.fetch(token, ApifoxConnection(project_id="project-123"))

    assert token not in str(captured.value)
    assert "[REDACTED]" in str(captured.value)
    assert not Path(marker.read_text(encoding="utf-8")).exists()


def test_apifox_adapter_rejects_export_payload_containing_access_token(tmp_path):
    from task_server.api_testing.adapters.apifox import (
        ApifoxAdapter,
        ApifoxAdapterError,
        ApifoxConnection,
    )

    token = "synthetic-secret-token"

    adapter, marker = _apifox_adapter_for_mode(tmp_path, "leak")
    with pytest.raises(ApifoxAdapterError) as captured:
        adapter.fetch(token, ApifoxConnection(project_id="project-123"))

    assert token not in str(captured.value)
    assert "access token" in str(captured.value)
    assert not Path(marker.read_text(encoding="utf-8")).exists()


@pytest.mark.parametrize(
    "mode, limit_name, message",
    [
        ("stdout-oversize", "max_stdout_bytes", "stdout exceeded"),
        ("stderr-oversize", "max_stderr_bytes", "stderr exceeded"),
        ("openapi-oversize", "max_openapi_bytes", "OpenAPI export exceeded"),
        ("environment-oversize", "max_environment_bytes", "environment export exceeded"),
    ],
)
def test_apifox_adapter_rejects_oversized_outputs_and_cleans_up(
    tmp_path, mode, limit_name, message
):
    from task_server.api_testing.adapters.apifox import (
        ApifoxAdapterError,
        ApifoxConnection,
    )

    token = "synthetic-secret-token"
    adapter, marker = _apifox_adapter_for_mode(
        tmp_path,
        mode,
        **{
            "max_stdout_bytes": 128,
            "max_stderr_bytes": 128,
            "max_openapi_bytes": 128,
            "max_environment_bytes": 128,
            limit_name: 64,
        },
    )

    with pytest.raises(ApifoxAdapterError, match=message) as captured:
        adapter.fetch(token, ApifoxConnection(project_id="project-123"))

    assert token not in str(captured.value)
    assert not Path(marker.read_text(encoding="utf-8")).exists()


@pytest.mark.parametrize(
    "mode, stderr_limit",
    [
        ("boundary-failure", 4096),
        ("capture-boundary-overflow", 64),
    ],
)
def test_apifox_diagnostics_do_not_leak_token_prefix_across_byte_boundaries(
    tmp_path, mode, stderr_limit
):
    from task_server.api_testing.adapters.apifox import (
        ApifoxAdapterError,
        ApifoxConnection,
    )

    token = "synthetic-boundary-token-value"
    adapter, marker = _apifox_adapter_for_mode(
        tmp_path,
        mode,
        max_stderr_bytes=stderr_limit,
        diagnostic_bytes=64,
    )

    with pytest.raises(ApifoxAdapterError) as captured:
        adapter.fetch(token, ApifoxConnection(project_id="project-123"))

    message = str(captured.value)
    assert token not in message
    assert token[:8] not in message
    assert "synthe" not in message
    assert not Path(marker.read_text(encoding="utf-8")).exists()


def test_apifox_rejects_non_posix_before_spawning(monkeypatch, tmp_path):
    from task_server.api_testing.adapters import apifox

    spawned = []

    def forbidden_spawn(*args, **kwargs):
        spawned.append((args, kwargs))
        raise AssertionError("process must not be spawned")

    monkeypatch.setattr(apifox, "_is_posix_server", lambda: False, raising=False)
    monkeypatch.setattr(apifox.subprocess, "Popen", forbidden_spawn)
    adapter, _ = _apifox_adapter_for_mode(tmp_path, "success")

    with pytest.raises(apifox.ApifoxAdapterError, match="POSIX"):
        adapter.fetch(
            "synthetic-secret-token",
            apifox.ApifoxConnection(project_id="project-123"),
        )

    assert spawned == []


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
def test_apifox_overflow_terminates_grandchild_process_group(tmp_path):
    from task_server.api_testing.adapters.apifox import (
        ApifoxAdapterError,
        ApifoxConnection,
    )

    adapter, marker = _apifox_adapter_for_mode(
        tmp_path, "grandchild-overflow", max_stdout_bytes=64
    )

    with pytest.raises(ApifoxAdapterError, match="stdout exceeded"):
        adapter.fetch(
            "synthetic-secret-token", ApifoxConnection(project_id="project-123")
        )

    parent_pid = _read_process_pid(Path(str(marker) + ".pid"))
    grandchild_pid = _read_process_pid(Path(str(marker) + ".grandchild.pid"))
    _assert_process_absent(parent_pid)
    _assert_process_absent(grandchild_pid)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
def test_apifox_parent_exit_with_descendant_pipe_times_out_and_kills_group(tmp_path):
    from task_server.api_testing.adapters.apifox import (
        ApifoxAdapterError,
        ApifoxConnection,
    )

    adapter, marker = _apifox_adapter_for_mode(
        tmp_path, "parent-exits-grandchild", timeout_seconds=1
    )

    with pytest.raises(ApifoxAdapterError, match="timed out"):
        adapter.fetch(
            "synthetic-secret-token", ApifoxConnection(project_id="project-123")
        )

    parent_pid = _read_process_pid(Path(str(marker) + ".pid"))
    grandchild_pid = _read_process_pid(Path(str(marker) + ".grandchild.pid"))
    _assert_process_absent(parent_pid)
    _assert_process_absent(grandchild_pid)


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
@pytest.mark.parametrize("failure_stage", ["create", "register"])
def test_apifox_selector_setup_failure_reaps_process(
    monkeypatch, tmp_path, failure_stage
):
    from task_server.api_testing.adapters import apifox

    adapter, marker = _apifox_adapter_for_mode(tmp_path, "selector-failure")
    pid_path = Path(str(marker) + ".pid")
    real_selector = apifox.selectors.DefaultSelector

    if failure_stage == "create":
        def failing_selector_factory():
            _read_process_pid(pid_path)
            raise PermissionError("synthetic selector creation failure")

        monkeypatch.setattr(apifox.selectors, "DefaultSelector", failing_selector_factory)
    else:
        class FailingRegisterSelector:
            def __init__(self):
                self._delegate = real_selector()

            def register(self, *args, **kwargs):
                _read_process_pid(pid_path)
                raise PermissionError("synthetic selector registration failure")

            def close(self):
                self._delegate.close()

        monkeypatch.setattr(
            apifox.selectors, "DefaultSelector", FailingRegisterSelector
        )

    with pytest.raises(apifox.ApifoxAdapterError, match="could not run"):
        adapter.fetch(
            "synthetic-secret-token",
            apifox.ApifoxConnection(project_id="project-123"),
        )

    _assert_process_absent(_read_process_pid(pid_path))


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_apifox_selector_loop_exception_reaps_process(
    monkeypatch, tmp_path, error_type
):
    from task_server.api_testing.adapters import apifox

    adapter, marker = _apifox_adapter_for_mode(tmp_path, "selector-failure")
    pid_path = Path(str(marker) + ".pid")
    real_selector = apifox.selectors.DefaultSelector

    class FailingSelectSelector:
        def __init__(self):
            self._delegate = real_selector()

        def register(self, *args, **kwargs):
            return self._delegate.register(*args, **kwargs)

        def get_map(self):
            return self._delegate.get_map()

        def select(self, *args, **kwargs):
            _read_process_pid(pid_path)
            raise error_type("synthetic selector loop failure")

        def close(self):
            self._delegate.close()

    monkeypatch.setattr(
        apifox.selectors, "DefaultSelector", FailingSelectSelector
    )

    with pytest.raises(error_type, match="selector loop failure"):
        adapter.fetch(
            "synthetic-secret-token",
            apifox.ApifoxConnection(project_id="project-123"),
        )

    _assert_process_absent(_read_process_pid(pid_path))


@pytest.mark.skipif(os.name != "posix", reason="POSIX process-group contract")
def test_apifox_timeout_reaps_direct_process(tmp_path):
    from task_server.api_testing.adapters.apifox import (
        ApifoxAdapterError,
        ApifoxConnection,
    )

    adapter, marker = _apifox_adapter_for_mode(
        tmp_path, "timeout", timeout_seconds=1
    )

    with pytest.raises(ApifoxAdapterError, match="timed out"):
        adapter.fetch(
            "synthetic-secret-token", ApifoxConnection(project_id="project-123")
        )

    _assert_process_absent(_read_process_pid(Path(str(marker) + ".pid")))
