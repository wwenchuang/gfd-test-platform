import copy
from concurrent.futures import ThreadPoolExecutor
import os

from alembic import command
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
import pytest

from task_server.api_testing.models.environment import (
    ApiEnvironment,
    ApiEnvironmentRevision,
    ApiEnvironmentService,
    ApiEnvironmentVariable,
    ApiSecretValue,
)
from task_server.api_testing.models.project import ApiProject
from task_server.api_testing.models.source import ApiSource, ApiSourceRevision
from tests.api_testing.test_migrations import (
    _alembic_config,
    _assert_current_test_schema,
    _create_test_schema,
    _database_url,
    _drop_test_schema,
    _without_database_environment,
)


BUSINESS_TOKEN = "phase1-secret-fixture-business-token"


def _audit(actor="admin"):
    return {"owner_id": actor, "created_by": actor, "updated_by": actor}


@pytest.fixture(scope="module")
def environment_database():
    database_url = _database_url()
    created_schemas = set()
    schema_name, schema_url = _create_test_schema(database_url, created_schemas)
    with _without_database_environment():
        command.upgrade(_alembic_config(schema_url), "head")
    _assert_current_test_schema(schema_url, schema_name)
    try:
        yield schema_url
    finally:
        _drop_test_schema(database_url, schema_name, created_schemas)


@pytest.fixture(scope="module")
def session_factory(environment_database):
    engine = create_engine(environment_database, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture(autouse=True)
def secret_key(monkeypatch):
    monkeypatch.setenv(
        "API_TESTING_SECRET_KEY",
        "task4-test-only-secret-key-7pM4xR9vL2cN6qW8",
    )


@pytest.fixture()
def source_context(session_factory):
    suffix = os.urandom(6).hex()
    with session_factory.begin() as session:
        project = ApiProject(
            name=f"Task 4 Project {suffix}",
            slug=f"task-4-project-{suffix}",
            **_audit(),
        )
        session.add(project)
        session.flush()
        source = ApiSource(
            project_id=project.id,
            name=f"Task 4 Source {suffix}",
            source_type="apifox",
            connection_config={},
            **_audit(),
        )
        session.add(source)
        session.flush()
        revision = ApiSourceRevision(
            source_id=source.id,
            revision_number=1,
            status="active",
            document_hash="a" * 64,
            normalized_document={"openapi": "3.0.3", "info": {"title": "3D"}},
            import_metadata={"environment": {"name": "source-owned"}},
            **_audit(),
        )
        session.add(revision)
        session.flush()
        source.active_revision_id = revision.id
    return project, source, revision


@pytest.fixture()
def production_environment(source_context):
    project, source, revision = source_context
    return {
        "project_id": project.id,
        "source_id": source.id,
        "source_revision_id": revision.id,
        "name": "生产环境",
        "description": "Apifox imported metadata",
        "services": [
            {
                "name": "default",
                "module": "默认模块",
                "base_url": "https://print.example.test/app",
                "metadata": {"apifox_service_id": "service-default"},
            },
            {
                "name": "share",
                "module": "分享",
                "base_url": "https://share.example.test/api",
                "metadata": {"apifox_service_id": "service-share"},
            },
        ],
        "variables": {
            "Biz": "ZXB",
            "tenant": "{{Biz}}-tenant",
            "settings": {"locale": "zh-CN", "enabled": True},
        },
        "default_headers": {
            "Authorization": "Bearer {{ZXBToken}}",
            "X-Biz": "{{Biz}}",
        },
    }


@pytest.fixture()
def environment_service(session_factory):
    from task_server.api_testing.services.environment_service import EnvironmentService

    return EnvironmentService(session_factory)


def _import_with_token(environment_service, production_environment):
    imported = environment_service.import_from_source(production_environment, "admin")
    return environment_service.create_revision(
        imported.id,
        {},
        {"ZXBToken": BUSINESS_TOKEN},
        "admin",
    )


def test_environment_assets_are_project_scoped_and_keep_revision_history(
    environment_service, production_environment
):
    production = environment_service.import_from_source(
        production_environment, "admin"
    )
    environment_service.create_revision(
        production.id,
        {"description": "platform maintained revision"},
        {"ZXBToken": BUSINESS_TOKEN},
        "admin",
    )
    development_payload = copy.deepcopy(production_environment)
    development_payload["name"] = "开发环境"
    development_payload["description"] = "manual development environment"
    development = environment_service.import_from_source(
        development_payload, "admin"
    )

    assets = environment_service.list_assets(
        production_environment["project_id"], "admin"
    )
    history = environment_service.list_revisions(production.id, "admin")

    assert [item.name for item in assets] == ["开发环境", "生产环境"]
    assert {item.id for item in assets} == {production.id, development.id}
    assert next(item for item in assets if item.id == production.id).revision == 2
    assert next(item for item in assets if item.id == production.id).service_count == 2
    assert [item.revision for item in history] == [2, 1]
    assert history[0].description == "platform maintained revision"


def test_environment_assets_can_be_archived_and_restored_without_deleting_history(
    environment_service, production_environment
):
    imported = environment_service.import_from_source(production_environment, "admin")

    archived = environment_service.archive(imported.id, "admin")

    assert archived.status == "archived"
    assert environment_service.list_assets(
        production_environment["project_id"], "admin"
    ) == ()
    assert [item.id for item in environment_service.list_assets(
        production_environment["project_id"], "admin", status="archived"
    )] == [imported.id]
    assert len(environment_service.list_revisions(imported.id, "admin")) == 1

    restored = environment_service.restore(imported.id, "admin")

    assert restored.status == "active"
    assert [item.id for item in environment_service.list_assets(
        production_environment["project_id"], "admin"
    )] == [imported.id]


def test_environment_asset_queries_reject_cross_owner_access(
    environment_service, production_environment
):
    imported = environment_service.import_from_source(production_environment, "admin")

    from task_server.api_testing.services.environment_service import (
        EnvironmentNotFoundError,
    )

    with pytest.raises(EnvironmentNotFoundError):
        environment_service.list_assets(
            production_environment["project_id"], "another-owner"
        )
    with pytest.raises(EnvironmentNotFoundError):
        environment_service.list_revisions(imported.id, "another-owner")


def test_imported_environment_is_editable_without_mutating_source(
    environment_service, production_environment, session_factory
):
    source_snapshot = copy.deepcopy(production_environment)
    imported = environment_service.import_from_source(production_environment, "admin")
    edited = environment_service.create_revision(
        imported.id,
        {"name": "生产环境（腾讯云）", "variables": {"Biz": "ZXB-EDITED"}},
        {"ZXBToken": BUSINESS_TOKEN},
        "admin",
    )

    assert imported.revision == 1
    assert edited.revision == 2
    assert edited.name == "生产环境（腾讯云）"
    assert edited.variables["Biz"] == "ZXB-EDITED"
    assert edited.variables["tenant"] == "{{Biz}}-tenant"
    assert edited.variables["ZXBToken"].configured is True
    assert edited.variables["ZXBToken"].fingerprint
    assert BUSINESS_TOKEN not in repr(edited)
    assert BUSINESS_TOKEN not in str(edited)
    assert production_environment == source_snapshot

    with session_factory() as session:
        source_revision = session.get(
            ApiSourceRevision, production_environment["source_revision_id"]
        )
        assert source_revision.import_metadata == {"environment": {"name": "source-owned"}}


def test_import_preserves_services_public_variables_and_headers(
    environment_service, production_environment
):
    imported = environment_service.import_from_source(production_environment, "admin")

    assert imported.project_id == production_environment["project_id"]
    assert imported.source_id == production_environment["source_id"]
    assert imported.source_revision_id == production_environment["source_revision_id"]
    assert imported.services["default"].base_url == "https://print.example.test/app"
    assert imported.services["share"].module_name == "分享"
    assert imported.services["share"].metadata["apifox_service_id"] == "service-share"
    assert imported.variables["settings"] == {"locale": "zh-CN", "enabled": True}
    assert imported.default_headers["X-Biz"] == "{{Biz}}"


@pytest.mark.parametrize(
    "base_url",
    [
        "ftp://print.example.test/app",
        "https://user:password@print.example.test/app",
        "https:///missing-host",
        "https://example.com:bad/path",
        "https://exa mple.com/path",
        "https://-invalid.example.test/path",
    ],
)
def test_service_urls_require_http_without_embedded_credentials(
    environment_service, production_environment, base_url
):
    invalid = copy.deepcopy(production_environment)
    invalid["services"][0]["base_url"] = base_url

    with pytest.raises(ValueError, match="service URL"):
        environment_service.import_from_source(invalid, "admin")


def test_import_preserves_declared_service_without_available_url(
    environment_service, production_environment
):
    imported_payload = copy.deepcopy(production_environment)
    imported_payload["services"].append(
        {
            "name": "model",
            "module": "模型服务",
            "base_url": None,
            "metadata": {"apifox_service_id": "service-model"},
        }
    )

    imported = environment_service.import_from_source(imported_payload, "admin")

    assert imported.services["default"].unresolved is False
    assert imported.services["model"].base_url is None
    assert imported.services["model"].unresolved is True

    configured = environment_service.create_revision(
        imported.id, {}, {"ZXBToken": BUSINESS_TOKEN}, "admin"
    )
    copied = environment_service.create_revision(
        configured.id, {"description": "keep unresolved service"}, {}, "admin"
    )
    assert copied.services["model"].unresolved is True

    runtime = environment_service.resolve_runtime(copied.revision_id, {})
    assert runtime.base_url_for("default") == "https://print.example.test/app"
    assert runtime.unresolved_services == ("model",)

    from task_server.api_testing.services.environment_service import (
        UnresolvedServiceError,
    )

    with pytest.raises(UnresolvedServiceError, match="model"):
        runtime.base_url_for("model")
    with pytest.raises(UnresolvedServiceError, match="model"):
        environment_service.resolve_runtime(copied.revision_id, {}, service_name="model")

    resolved = environment_service.create_revision(
        copied.id,
        {
            "services": [
                {
                    "name": "default",
                    "module": "默认模块",
                    "base_url": "https://print.example.test/app",
                },
                {
                    "name": "model",
                    "module": "模型服务",
                    "base_url": "https://model.example.test/api",
                },
            ]
        },
        {},
        "admin",
    )
    assert resolved.services["model"].unresolved is False
    assert environment_service.resolve_runtime(
        resolved.revision_id, {}, service_name="model"
    ).base_url_for("model") == "https://model.example.test/api"


def test_revision_copy_set_clear_and_old_secret_resolution(
    environment_service, production_environment
):
    configured = _import_with_token(environment_service, production_environment)
    changed = environment_service.create_revision(
        configured.id,
        {
            "description": "new description",
            "default_headers": {"X-Revision": "three"},
        },
        {},
        "editor",
    )
    cleared = environment_service.create_revision(
        configured.id,
        {},
        {"ZXBToken": None},
        "editor",
    )

    assert changed.revision == 3
    assert changed.services == configured.services
    assert changed.variables["Biz"] == "ZXB"
    assert changed.variables["ZXBToken"].fingerprint == configured.variables["ZXBToken"].fingerprint
    assert changed.default_headers["Authorization"] == "Bearer {{ZXBToken}}"
    assert changed.default_headers["X-Revision"] == "three"
    assert cleared.revision == 4
    assert "ZXBToken" not in cleared.variables

    old_runtime = environment_service.resolve_runtime(configured.revision_id, {})
    assert old_runtime.headers["Authorization"] == f"Bearer {BUSINESS_TOKEN}"
    assert old_runtime.secrets["ZXBToken"] == BUSINESS_TOKEN
    with pytest.raises(Exception, match="ZXBToken") as error:
        environment_service.resolve_runtime(cleared.revision_id, {})
    assert BUSINESS_TOKEN not in str(error.value)


def test_create_revision_rolls_back_everything_when_secret_encryption_fails(
    environment_service, production_environment, session_factory, monkeypatch
):
    imported = environment_service.import_from_source(production_environment, "admin")

    def fail_encryption(_value):
        raise RuntimeError("synthetic encryption failure")

    monkeypatch.setattr(
        "task_server.api_testing.services.environment_service.encrypt_secret",
        fail_encryption,
    )
    with pytest.raises(RuntimeError, match="encryption failure"):
        environment_service.create_revision(
            imported.id, {"name": "must rollback"}, {"ZXBToken": BUSINESS_TOKEN}, "admin"
        )

    current = environment_service.get_environment(imported.id)
    assert current.revision == 1
    assert current.name == "生产环境"
    with session_factory() as session:
        assert session.scalar(
            select(func.count(ApiEnvironmentRevision.id)).where(
                ApiEnvironmentRevision.environment_id == imported.id
            )
        ) == 1
        assert session.scalar(
            select(func.count(ApiSecretValue.id)).where(
                ApiSecretValue.environment_id == imported.id
            )
        ) == 0


def test_concurrent_revision_creation_serializes_revision_numbers(
    environment_service, production_environment
):
    imported = environment_service.import_from_source(production_environment, "admin")

    def update(label):
        return environment_service.create_revision(
            imported.id, {"description": label}, {}, label
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        revisions = list(executor.map(update, ("writer-a", "writer-b")))

    assert sorted(item.revision for item in revisions) == [2, 3]
    assert environment_service.get_environment(imported.id).revision == 3


def test_runtime_resolves_nested_placeholders_and_ephemeral_overrides(
    environment_service, production_environment
):
    configured = _import_with_token(environment_service, production_environment)
    changed = environment_service.create_revision(
        configured.id,
        {
            "variables": {
                "userId": "default-user",
                "resourcePath": "/users/{{userId}}",
                "objectValue": {"active": True, "count": 2},
            }
        },
        {},
        "admin",
    )

    runtime = environment_service.resolve_runtime(changed.revision_id, {"userId": "135"})

    assert runtime.base_urls["default"] == "https://print.example.test/app"
    assert runtime.headers["Authorization"] == f"Bearer {BUSINESS_TOKEN}"
    assert runtime.headers["X-Biz"] == "ZXB"
    assert runtime.public_variables["tenant"] == "ZXB-tenant"
    assert runtime.public_variables["resourcePath"] == "/users/135"
    assert runtime.public_variables["objectValue"] == {"active": True, "count": 2}
    assert runtime.render(
        {
            "{{userId}}": ["{{resourcePath}}", 17, True],
            "nested": {"token": "{{ZXBToken}}"},
        }
    ) == {
        "135": ["/users/135", 17, True],
        "nested": {"token": BUSINESS_TOKEN},
    }
    request = runtime.render_request(
        path="/favorites/{{userId}}",
        query={"biz": "{{Biz}}", "page": 1},
        headers={"X-User": "{{userId}}"},
        body={"owner": "{{userId}}", "enabled": True},
    )
    assert request.path == "/favorites/135"
    assert request.query == {"biz": "ZXB", "page": 1}
    assert request.headers == {"X-User": "135"}
    assert request.body == {"owner": "135", "enabled": True}
    assert BUSINESS_TOKEN not in repr(runtime)
    assert BUSINESS_TOKEN not in str(runtime)

    persisted = environment_service.resolve_runtime(changed.revision_id, {})
    assert persisted.public_variables["userId"] == "default-user"


def test_runtime_and_rendered_request_repr_never_include_sensitive_contents(
    environment_service, production_environment
):
    configured = _import_with_token(environment_service, production_environment)
    derived = environment_service.create_revision(
        configured.id,
        {
            "variables": {"derivedAuth": "Bearer {{ZXBToken}}"},
            "services": [
                {
                    "name": "default",
                    "module": "默认模块",
                    "base_url": "https://print.example.test/private/{{ZXBToken}}",
                }
            ],
            "default_headers": {"X-Derived": "{{derivedAuth}}"},
        },
        {},
        "admin",
    )

    runtime = environment_service.resolve_runtime(derived.revision_id, {})
    request = runtime.render_request(
        path="/private/{{ZXBToken}}",
        query={"token": "{{ZXBToken}}"},
        headers={"Authorization": "Bearer {{ZXBToken}}"},
        body={"auth": "{{derivedAuth}}"},
    )

    for rendered in (repr(runtime), str(runtime), repr(request), str(request)):
        assert BUSINESS_TOKEN not in rendered
        assert f"Bearer {BUSINESS_TOKEN}" not in rendered
        assert "/private/" not in rendered
        assert "Authorization" not in rendered
        assert "derivedAuth" not in rendered


def test_runtime_rejects_secret_overrides_and_strict_placeholder_failures(
    environment_service, production_environment
):
    configured = _import_with_token(environment_service, production_environment)

    with pytest.raises(Exception, match="secret.*ZXBToken") as secret_error:
        environment_service.resolve_runtime(
            configured.revision_id, {"ZXBToken": "must-not-win"}
        )
    assert "must-not-win" not in str(secret_error.value)

    runtime = environment_service.resolve_runtime(configured.revision_id, {})
    from task_server.api_testing.services.environment_service import (
        PlaceholderCycleError,
        PlaceholderDepthError,
        PlaceholderSyntaxError,
        UnresolvedVariableError,
    )

    with pytest.raises(UnresolvedVariableError, match="missingModelId"):
        runtime.render("{{missingModelId}}")
    with pytest.raises(PlaceholderSyntaxError):
        runtime.render("{{ missingModelId }}")
    with pytest.raises(PlaceholderSyntaxError):
        runtime.render("prefix {{missing")

    cycle_view = environment_service.create_revision(
        configured.id,
        {"variables": {"cycleA": "{{cycleB}}", "cycleB": "{{cycleA}}"}},
        {},
        "admin",
    )
    with pytest.raises(PlaceholderCycleError, match="cycleA.*cycleB"):
        environment_service.resolve_runtime(cycle_view.revision_id, {})

    depth_source = copy.deepcopy(production_environment)
    depth_source["name"] = "生产环境-depth"
    depth_environment = environment_service.import_from_source(depth_source, "admin")
    depth_variables = {f"depth{index}": f"{{{{depth{index + 1}}}}}" for index in range(11)}
    depth_variables["depth11"] = "done"
    depth_view = environment_service.create_revision(
        depth_environment.id, {"variables": depth_variables}, {}, "admin"
    )
    with pytest.raises(PlaceholderDepthError, match="maximum depth"):
        environment_service.resolve_runtime(depth_view.revision_id, {})


def test_views_never_expose_ciphertext_or_plaintext(
    environment_service, production_environment, session_factory
):
    configured = _import_with_token(environment_service, production_environment)
    fetched = environment_service.get_revision(configured.revision_id)

    with session_factory() as session:
        secret = session.scalar(
            select(ApiSecretValue).where(
                ApiSecretValue.environment_id == configured.id,
                ApiSecretValue.name == "ZXBToken",
            )
        )
        ciphertext = secret.ciphertext

    rendered = repr(fetched) + str(fetched) + repr(fetched.variables["ZXBToken"])
    assert BUSINESS_TOKEN not in rendered
    assert ciphertext not in rendered
    assert not hasattr(fetched.variables["ZXBToken"], "ciphertext")
    assert not hasattr(fetched.variables["ZXBToken"], "plaintext")


def test_database_rows_keep_public_and_secret_values_separate(
    environment_service, production_environment, session_factory
):
    configured = _import_with_token(environment_service, production_environment)

    with session_factory() as session:
        rows = list(
            session.scalars(
                select(ApiEnvironmentVariable).where(
                    ApiEnvironmentVariable.revision_id == configured.revision_id
                )
            )
        )
        services = list(
            session.scalars(
                select(ApiEnvironmentService).where(
                    ApiEnvironmentService.revision_id == configured.revision_id
                )
            )
        )

    public = next(row for row in rows if row.name == "Biz")
    secret = next(row for row in rows if row.name == "ZXBToken")
    assert public.value == "ZXB"
    assert public.secret_value_id is None
    assert public.is_secret is False
    assert secret.value is None
    assert secret.secret_value_id
    assert secret.is_secret is True
    assert {service.service_name for service in services} == {"default", "share"}
