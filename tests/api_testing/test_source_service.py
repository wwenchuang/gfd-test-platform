import copy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

from alembic import command
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
import pytest

from task_server.api_testing.models.project import ApiProject
from task_server.api_testing.models.source import (
    ApiSourceDiff,
    ApiSourceEndpoint,
    ApiSourceRevision,
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

    normalized = normalize_openapi_document(FAVORITES_OPENAPI, "source-1")

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
    assert normalized.document["vendor_extensions"]["x-apifox-project"] == "synthetic-project"
    assert list_endpoint.operation["vendor_extensions"]["x-apifox-folder"] == "favorites"
    assert "x-apifox-project" not in normalized.document
    saved_operation = normalized.document["paths"]["/print3d/api/v1/favorite/list"]["get"]
    assert "path_parameters" not in saved_operation
    assert "resolved_schemas" not in saved_operation


def test_openapi_31_is_supported_and_path_parameter_semantics_are_preserved():
    from task_server.api_testing.adapters.openapi import normalize_openapi_document

    document = copy.deepcopy(FAVORITES_OPENAPI)
    document["openapi"] = "3.1.0"
    document["jsonSchemaDialect"] = "https://json-schema.org/draft/2020-12/schema"
    operation = document["paths"].pop("/print3d/api/v1/favorite/list")
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
    assert source_service.get_active_revision(project.id) is None

    with session_factory() as session:
        candidate = session.get(ApiSourceRevision, preview.candidate_revision_id)
        assert candidate.status == "candidate"
        assert session.scalar(
            select(func.count()).select_from(ApiSourceEndpoint).where(
                ApiSourceEndpoint.revision_id == candidate.id
            )
        ) == 3


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
    assert source_service.get_active_revision(project.id).id == active.id


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
    assert all("resolved_schemas" in item.changed_fields for item in preview.changes)


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
    assert source_service.get_active_revision(project.id).id == second.id
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
    assert source_service.get_active_revision(project.id).id == activated.id
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
    assert source_service.get_active_revision(project.id).id == first.id


def test_apifox_adapter_uses_explicit_safe_subprocess_and_returns_metadata(
    monkeypatch,
):
    from task_server.api_testing.adapters.apifox import ApifoxAdapter, ApifoxConnection

    token = "synthetic-secret-token"
    monkeypatch.setenv("QWEN_API_KEY", "must-not-reach-apifox")
    monkeypatch.setenv("API_TESTING_DATABASE_URL", "must-not-reach-apifox")
    observed = {}

    def fake_runner(args, **kwargs):
        observed["args"] = args
        observed["kwargs"] = kwargs
        output_index = args.index("--output") + 1
        Path(args[output_index]).write_text(json.dumps(FAVORITES_OPENAPI), encoding="utf-8")
        metadata_index = args.index("--environment-output") + 1
        Path(args[metadata_index]).write_text(
            json.dumps({"name": "Synthetic Production", "variables": {"Biz": "ZXB"}}),
            encoding="utf-8",
        )
        return SimpleNamespace(returncode=0, stdout="exported", stderr="")

    adapter = ApifoxAdapter(command=["apifox", "export"], runner=fake_runner, timeout_seconds=17)
    result = adapter.fetch(
        token,
        ApifoxConnection(
            project_id="project-123",
            branch_id="branch-main",
            environment_id="environment-prod",
        ),
    )

    assert result.document["info"]["title"] == "Synthetic Favorites API"
    assert result.environment_metadata["name"] == "Synthetic Production"
    assert result.identifiers == {
        "project_id": "project-123",
        "branch_id": "branch-main",
        "environment_id": "environment-prod",
    }
    assert observed["args"][:2] == ["apifox", "export"]
    assert token not in " ".join(observed["args"])
    assert observed["kwargs"]["env"]["APIFOX_ACCESS_TOKEN"] == token
    assert "QWEN_API_KEY" not in observed["kwargs"]["env"]
    assert "API_TESTING_DATABASE_URL" not in observed["kwargs"]["env"]
    assert observed["kwargs"]["env"]["HOME"] == observed["kwargs"]["cwd"]
    assert observed["kwargs"]["shell"] is False
    assert observed["kwargs"]["timeout"] == 17
    assert observed["kwargs"]["check"] is True
    assert not Path(observed["kwargs"]["cwd"]).exists()


def test_apifox_adapter_redacts_token_from_failures():
    from task_server.api_testing.adapters.apifox import (
        ApifoxAdapter,
        ApifoxAdapterError,
        ApifoxConnection,
    )

    token = "synthetic-secret-token"

    def failing_runner(args, **kwargs):
        raise subprocess.CalledProcessError(
            1,
            args,
            output=f"stdout leaked {token}",
            stderr=f"stderr leaked {token}",
        )

    adapter = ApifoxAdapter(command=["apifox", "export"], runner=failing_runner)
    with pytest.raises(ApifoxAdapterError) as captured:
        adapter.fetch(token, ApifoxConnection(project_id="project-123"))

    assert token not in str(captured.value)
    assert "[REDACTED]" in str(captured.value)


def test_apifox_adapter_rejects_export_payload_containing_access_token():
    from task_server.api_testing.adapters.apifox import (
        ApifoxAdapter,
        ApifoxAdapterError,
        ApifoxConnection,
    )

    token = "synthetic-secret-token"

    def leaking_runner(args, **kwargs):
        output_index = args.index("--output") + 1
        leaked = copy.deepcopy(FAVORITES_OPENAPI)
        leaked["x-debug-access-token"] = token
        Path(args[output_index]).write_text(json.dumps(leaked), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="exported", stderr="")

    adapter = ApifoxAdapter(command=["apifox", "export"], runner=leaking_runner)
    with pytest.raises(ApifoxAdapterError) as captured:
        adapter.fetch(token, ApifoxConnection(project_id="project-123"))

    assert token not in str(captured.value)
    assert "access token" in str(captured.value)
