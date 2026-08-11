import os

from alembic import command
import pytest
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from task_server.api_testing.db import engine_for_url
from task_server.api_testing.models.project import ApiProject, ApiWorkspace
from task_server.api_testing.models.source import ApiSource, ApiSourceDiff, ApiSourceRevision
from task_server.api_testing.services.apifox_service import ApifoxService
from task_server.api_testing.services.environment_service import EnvironmentService
from task_server.api_testing.services.source_service import SourceService
from tests.api_testing.test_migrations import (
    _alembic_config,
    _create_test_schema,
    _database_url,
    _drop_test_schema,
    _without_database_environment,
)


def _audit(actor="admin"):
    return {"owner_id": actor, "created_by": actor, "updated_by": actor}


@pytest.fixture(scope="module")
def activation_factory():
    database_url = _database_url()
    created_schemas = set()
    schema_name, schema_url = _create_test_schema(database_url, created_schemas)
    with _without_database_environment():
        command.upgrade(_alembic_config(schema_url), "head")
    engine = engine_for_url(schema_url)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()
        _drop_test_schema(database_url, schema_name, created_schemas)


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    monkeypatch.setenv(
        "API_TESTING_SECRET_KEY",
        "apifox-activation-test-key-10ef0061d00d47e492bc5f00c0f59be3",
    )


def _preview(activation_factory, suffix):
    with activation_factory.begin() as session:
        project = ApiProject(
            name="Apifox activation " + suffix,
            slug="apifox-activation-" + suffix,
            **_audit(),
        )
        session.add(project)
        session.flush()
        project_id = project.id
    document = {
        "openapi": "3.0.3",
        "info": {"title": "3D " + suffix, "version": "1.0.0"},
        "paths": {
            "/favorites": {
                "get": {
                    "summary": "查询我的收藏",
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }
    preview = SourceService(activation_factory).preview_refresh(
        project_id, None, document, "admin"
    )
    candidate = {
        "project_id": project_id,
        "source_id": preview.source_id,
        "source_revision_id": preview.candidate_revision_id,
        "name": "生产环境（新）-腾讯云",
        "description": "从 Apifox 手动读取",
        "services": [
            {
                "name": "default",
                "module": "default",
                "base_url": "https://print.example.test/app",
                "metadata": {"provider": "apifox"},
            }
        ],
        "variables": {"Biz": "ZXB"},
        "secret_placeholders": ["ZXBToken"],
        "default_headers": {"Authorization": "Bearer {{ZXBToken}}"},
        "provider": {"type": "apifox", "environment_id": "33831678"},
    }
    with activation_factory.begin() as session:
        diff = session.get(ApiSourceDiff, preview.id)
        diff.summary = {**diff.summary, "environment_candidate": candidate}
    return project_id, preview


def test_apifox_activation_saves_source_environment_and_workspace_together(
    activation_factory
):
    project_id, preview = _preview(activation_factory, os.urandom(4).hex())
    service = ApifoxService(
        None,
        None,
        None,
        SourceService(activation_factory),
        session_factory=activation_factory,
        environment_service=EnvironmentService(activation_factory),
    )

    result = service.activate_preview("admin", preview.id, "admin")

    assert result.source_revision.id == preview.candidate_revision_id
    assert result.environment.source_revision_id == preview.candidate_revision_id
    assert result.environment.name == "生产环境（新）-腾讯云"
    assert result.workspace == {
        "project_id": project_id,
        "source_revision_id": preview.candidate_revision_id,
        "environment_revision_id": result.environment.revision_id,
    }
    assert result.secret_placeholders == ("ZXBToken",)
    with activation_factory() as session:
        workspace = session.scalar(
            select(ApiWorkspace).where(ApiWorkspace.owner_id == "admin")
        )
        assert workspace.source_revision_id == preview.candidate_revision_id
        assert workspace.environment_revision_id == result.environment.revision_id


def test_apifox_activation_rolls_back_source_when_environment_persistence_fails(
    activation_factory
):
    _, preview = _preview(activation_factory, os.urandom(4).hex())

    class FailingEnvironmentService:
        def upsert_from_source_in_session(self, *_args, **_kwargs):
            raise RuntimeError("synthetic environment failure")

    service = ApifoxService(
        None,
        None,
        None,
        SourceService(activation_factory),
        session_factory=activation_factory,
        environment_service=FailingEnvironmentService(),
    )

    with pytest.raises(RuntimeError, match="environment failure"):
        service.activate_preview("admin", preview.id, "admin")

    with activation_factory() as session:
        source = session.get(ApiSource, preview.source_id)
        candidate = session.get(ApiSourceRevision, preview.candidate_revision_id)
        diff = session.get(ApiSourceDiff, preview.id)
        assert source.active_revision_id is None
        assert candidate.status == "candidate"
        assert diff.status == "preview"
