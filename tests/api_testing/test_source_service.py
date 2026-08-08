import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import textwrap

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

    assert stored == document


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
            import sys
            import time

            mode = sys.argv[1]
            marker = Path(sys.argv[2])
            marker.write_text(os.getcwd(), encoding="utf-8")
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
    adapter = ApifoxAdapter(
        command=[sys.executable, str(script), mode, str(marker)],
        timeout_seconds=17,
        **limits,
    )
    return adapter, marker


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
