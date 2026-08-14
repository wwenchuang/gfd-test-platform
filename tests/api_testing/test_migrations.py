import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
from unittest.mock import patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.exc import StaleDataError

from task_server.api_testing.models import Base
from task_server.api_testing.models.case import ApiCase, ApiCaseVersion
from task_server.api_testing.models.environment import (
    ApiEnvironment,
    ApiEnvironmentRevision,
    ApiSecretValue,
)
from task_server.api_testing.models.project import ApiProject, ApiWorkspace
from task_server.api_testing.models.source import (
    ApiSource,
    ApiSourceEndpoint,
    ApiSourceRevision,
)


ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG = ROOT / "task_server" / "api_testing" / "migrations" / "alembic.ini"
TEST_SCHEMA_PREFIX = "test_api_testing_"
TEST_SCHEMA_PATTERN = re.compile(r"^test_api_testing_[0-9a-f]{32}$")
TRUE_VALUES = {"1", "true", "yes", "on"}

PHASE1_TABLES = {
    "api_projects",
    "api_project_members",
    "api_sources",
    "api_source_revisions",
    "api_source_endpoints",
    "api_source_schemas",
    "api_source_diffs",
    "api_environments",
    "api_environment_revisions",
    "api_environment_variables",
    "api_environment_services",
    "api_secret_values",
    "api_cases",
    "api_case_versions",
    "api_case_data_rows",
    "api_case_assertions",
    "api_case_extractions",
    "api_case_scripts",
    "api_baselines",
    "api_executions",
    "api_execution_cases",
    "api_execution_attempts",
    "api_execution_events",
    "api_execution_artifacts",
    "api_failure_analyses",
    "api_ai_jobs",
    "api_ai_job_batches",
}

COMPLETION_TABLES = {
    "api_provider_credentials",
    "api_test_tasks",
    "api_scheduled_jobs",
    "api_scheduled_job_targets",
    "api_scheduled_job_runs",
}

EXPECTED_INDEXES = {
    "api_project_members": {"ix_api_project_members_project_status"},
    "api_source_revisions": {"ix_api_source_revisions_source_number"},
    "api_source_endpoints": {"ix_api_source_endpoints_revision_method_path"},
    "api_cases": {"ix_api_cases_project_status"},
    "api_executions": {"ix_api_executions_project_state_created"},
    "api_execution_events": {"ix_api_execution_events_execution_sequence"},
}

JSONB_COLUMNS = {
    "api_source_revisions": {"normalized_document"},
    "api_source_endpoints": {"operation"},
    "api_source_schemas": {"schema"},
    "api_source_diffs": {"summary", "changes"},
    "api_case_versions": {"request_template", "validation_summary"},
    "api_case_assertions": {"definition"},
    "api_executions": {"request_snapshot", "summary"},
    "api_execution_attempts": {"request", "response", "assertion_results"},
    "api_execution_events": {"payload"},
    "api_failure_analyses": {"analysis"},
}


def _postgres_tests_required():
    return (
        os.getenv("API_TESTING_REQUIRE_POSTGRES_TESTS", "0").strip().lower()
        in TRUE_VALUES
    )


def _database_url():
    value = os.getenv("TEST_DATABASE_URL", "").strip()
    if not value:
        if _postgres_tests_required():
            pytest.fail(
                "TEST_DATABASE_URL is required when "
                "API_TESTING_REQUIRE_POSTGRES_TESTS=1"
            )
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL migration tests")
    if not value.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("TEST_DATABASE_URL must point to PostgreSQL")
    return value


def _schema_url(database_url, schema_name):
    parsed = make_url(database_url)
    query = dict(parsed.query)
    query["options"] = f"-csearch_path={schema_name}"
    return parsed.set(query=query).render_as_string(hide_password=False)


def _alembic_config(database_url):
    config = Config(str(ALEMBIC_CONFIG))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def _without_database_environment():
    environment = os.environ.copy()
    environment.pop("TEST_DATABASE_URL", None)
    environment.pop("API_TESTING_DATABASE_URL", None)
    return patch.dict(os.environ, environment, clear=True)


def _assert_current_test_schema(schema_url, expected_schema):
    if not TEST_SCHEMA_PATTERN.fullmatch(expected_schema):
        raise RuntimeError("refusing non-test PostgreSQL schema")
    with create_engine(schema_url).connect() as connection:
        current_schema = connection.execute(text("SELECT current_schema()")).scalar_one()
    if current_schema != expected_schema:
        raise RuntimeError(
            f"refusing schema cleanup: current schema is {current_schema!r}"
        )


def _create_test_schema(database_url, created_schemas):
    schema_name = f"{TEST_SCHEMA_PREFIX}{uuid.uuid4().hex}"
    quoted = create_engine(database_url).dialect.identifier_preparer.quote(schema_name)
    engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(text(f"CREATE SCHEMA {quoted}"))
    created_schemas.add(schema_name)
    schema_url = _schema_url(database_url, schema_name)
    _assert_current_test_schema(schema_url, schema_name)
    return schema_name, schema_url


def _drop_test_schema(database_url, schema_name, created_schemas):
    if not TEST_SCHEMA_PATTERN.fullmatch(schema_name):
        raise RuntimeError("refusing non-test PostgreSQL schema")
    if schema_name not in created_schemas:
        raise RuntimeError("refusing PostgreSQL schema not created by this test run")
    schema_url = _schema_url(database_url, schema_name)
    _assert_current_test_schema(schema_url, schema_name)
    quoted = create_engine(database_url).dialect.identifier_preparer.quote(schema_name)
    engine = create_engine(database_url, isolation_level="AUTOCOMMIT")
    with engine.connect() as connection:
        connection.execute(text(f"DROP SCHEMA {quoted} CASCADE"))
    created_schemas.remove(schema_name)


@pytest.fixture()
def isolated_schema():
    database_url = _database_url()
    created_schemas = set()
    schema_name, schema_url = _create_test_schema(database_url, created_schemas)
    try:
        yield database_url, schema_name, schema_url, created_schemas
    finally:
        if schema_name in created_schemas:
            _drop_test_schema(database_url, schema_name, created_schemas)


@pytest.fixture()
def migrated_database(isolated_schema):
    database_url, schema_name, schema_url, created_schemas = isolated_schema
    config = _alembic_config(schema_url)
    with _without_database_environment():
        command.upgrade(config, "head")
    _assert_current_test_schema(schema_url, schema_name)
    yield database_url, schema_name, schema_url, config, created_schemas


def _audit(actor="admin"):
    return {"owner_id": actor, "created_by": actor, "updated_by": actor}


def _seed_project(session, suffix=""):
    project = ApiProject(
        name=f"Task 2 Project {suffix}",
        slug=f"task-2-project-{suffix or uuid.uuid4().hex}",
        **_audit(),
    )
    session.add(project)
    session.flush()
    return project


def _seed_source(session, project, suffix):
    source = ApiSource(
        project_id=project.id,
        name=f"Source {suffix}",
        source_type="openapi",
        **_audit(),
    )
    session.add(source)
    session.flush()
    revision = ApiSourceRevision(
        source_id=source.id,
        revision_number=1,
        status="active",
        document_hash=(suffix * 64)[:64],
        normalized_document={"openapi": "3.0.1"},
        **_audit(),
    )
    session.add(revision)
    session.flush()
    return source, revision


def _seed_environment(session, project, suffix):
    environment = ApiEnvironment(
        project_id=project.id,
        name=f"Environment {suffix}",
        **_audit(),
    )
    session.add(environment)
    session.flush()
    revision = ApiEnvironmentRevision(
        environment_id=environment.id,
        revision_number=1,
        name=f"Environment {suffix}",
        **_audit(),
    )
    session.add(revision)
    session.flush()
    return environment, revision


def _seed_case(session, project, endpoint, suffix):
    case = ApiCase(
        project_id=project.id,
        endpoint_id=endpoint.id,
        name=f"Case {suffix}",
        origin="manual",
        **_audit(),
    )
    session.add(case)
    session.flush()
    version = ApiCaseVersion(
        case_id=case.id,
        endpoint_id=endpoint.id,
        version_number=1,
        purpose="parent ownership test",
        request_template={"method": "GET", "path": "/favorites"},
        **_audit(),
    )
    session.add(version)
    session.flush()
    return case, version


def test_postgres_required_mode_fails_instead_of_skipping(monkeypatch):
    monkeypatch.delenv("TEST_DATABASE_URL", raising=False)
    monkeypatch.setenv("API_TESTING_REQUIRE_POSTGRES_TESTS", "1")

    with pytest.raises(pytest.fail.Exception, match="TEST_DATABASE_URL is required"):
        _database_url()


def test_schema_cleanup_refuses_public_and_unowned_test_schema():
    fake_url = "not-used"
    with pytest.raises(RuntimeError, match="non-test"):
        _drop_test_schema(fake_url, "public", set())
    with pytest.raises(RuntimeError, match="not created"):
        _drop_test_schema(
            fake_url,
            f"{TEST_SCHEMA_PREFIX}{'a' * 32}",
            set(),
        )


def test_offline_upgrade_contains_complete_phase1_schema():
    environment = os.environ.copy()
    environment.pop("TEST_DATABASE_URL", None)
    environment["API_TESTING_DATABASE_URL"] = (
        "postgresql+psycopg://offline:offline@127.0.0.1/offline"
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            str(ALEMBIC_CONFIG),
            "upgrade",
            "head",
            "--sql",
        ],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    generated_sql = result.stdout.lower()
    for table_name in PHASE1_TABLES | COMPLETION_TABLES:
        assert f"create table {table_name}" in generated_sql


def test_mutable_heads_are_parent_aware_composite_foreign_keys():
    expected_targets = {
        ("api_sources", "active_revision_id"): {
            "api_source_revisions.source_id",
            "api_source_revisions.id",
        },
        ("api_environments", "active_revision_id"): {
            "api_environment_revisions.environment_id",
            "api_environment_revisions.id",
        },
        ("api_cases", "active_version_id"): {
            "api_case_versions.case_id",
            "api_case_versions.id",
        },
    }
    for (table_name, column_name), expected_targets_for_head in expected_targets.items():
        table = Base.metadata.tables[table_name]
        constraints = [
            constraint
            for constraint in table.foreign_key_constraints
            if column_name in constraint.column_keys
        ]
        assert len(constraints) == 1
        assert {
            element.target_fullname for element in constraints[0].elements
        } == expected_targets_for_head


def test_project_members_reserve_rbac_identity_without_permission_logic():
    table = Base.metadata.tables["api_project_members"]
    assert {
        "project_id",
        "member_identity",
        "identity_type",
        "role",
        "status",
    }.issubset(table.c.keys())
    assert {
        foreign_key.target_fullname
        for foreign_key in table.c.project_id.foreign_keys
    } == {"api_projects.id"}
    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if constraint.__class__.__name__ == "UniqueConstraint"
    }
    assert ("project_id", "identity_type", "member_identity") in unique_columns


def test_workspace_context_has_one_owner_row_and_constrained_references():
    table = ApiWorkspace.__table__
    assert {"owner_id", "project_id", "source_revision_id", "environment_revision_id"}.issubset(table.c.keys())
    assert any(set(constraint.columns.keys()) == {"owner_id"} for constraint in table.constraints if constraint.__class__.__name__ == "UniqueConstraint")
    assert {
        foreign_key.target_fullname
        for column in (table.c.project_id, table.c.source_revision_id, table.c.environment_revision_id)
        for foreign_key in column.foreign_keys
    } == {"api_projects.id", "api_source_revisions.id", "api_environment_revisions.id"}


def test_explicit_alembic_url_wins_over_environment_url(isolated_schema):
    database_url, explicit_schema, explicit_url, created_schemas = isolated_schema
    fallback_schema, fallback_url = _create_test_schema(database_url, created_schemas)
    config = _alembic_config(explicit_url)
    try:
        environment = os.environ.copy()
        environment.pop("TEST_DATABASE_URL", None)
        environment["API_TESTING_DATABASE_URL"] = fallback_url
        with patch.dict(os.environ, environment, clear=True):
            command.upgrade(config, "head")
        assert "api_projects" in inspect(create_engine(explicit_url)).get_table_names()
        assert "api_projects" not in inspect(create_engine(fallback_url)).get_table_names()
    finally:
        _drop_test_schema(database_url, fallback_schema, created_schemas)


def test_test_database_url_wins_when_alembic_url_is_empty(isolated_schema):
    database_url, test_schema, test_url, created_schemas = isolated_schema
    fallback_schema, fallback_url = _create_test_schema(database_url, created_schemas)
    config = Config(str(ALEMBIC_CONFIG))
    try:
        environment = os.environ.copy()
        environment["TEST_DATABASE_URL"] = test_url
        environment["API_TESTING_DATABASE_URL"] = fallback_url
        with patch.dict(os.environ, environment, clear=True):
            command.upgrade(config, "head")
        assert "api_projects" in inspect(create_engine(test_url)).get_table_names()
        assert "api_projects" not in inspect(create_engine(fallback_url)).get_table_names()
    finally:
        _drop_test_schema(database_url, fallback_schema, created_schemas)


def test_upgrade_creates_complete_phase1_schema(migrated_database):
    _, _, schema_url, _, _ = migrated_database
    inspector = inspect(create_engine(schema_url))
    assert (PHASE1_TABLES | COMPLETION_TABLES).issubset(
        set(inspector.get_table_names())
    )


def test_phase1_schema_uses_explicit_foreign_keys_indexes_and_jsonb(migrated_database):
    _, _, schema_url, _, _ = migrated_database
    inspector = inspect(create_engine(schema_url))
    for table_name in PHASE1_TABLES - {"api_projects"}:
        assert inspector.get_foreign_keys(table_name), f"{table_name} has no explicit foreign key"
    for table_name, expected_names in EXPECTED_INDEXES.items():
        actual_names = {item["name"] for item in inspector.get_indexes(table_name)}
        assert expected_names.issubset(actual_names)
    for table_name, expected_columns in JSONB_COLUMNS.items():
        actual = {
            item["name"]: str(item["type"]).upper()
            for item in inspector.get_columns(table_name)
        }
        for column_name in expected_columns:
            assert actual[column_name] == "JSONB"


def test_all_primary_tables_have_uuid_owner_and_utc_audit_columns(migrated_database):
    _, _, schema_url, _, _ = migrated_database
    inspector = inspect(create_engine(schema_url))
    for table_name in PHASE1_TABLES:
        columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        assert {"id", "owner_id", "created_at", "updated_at"}.issubset(columns), table_name
        assert str(columns["id"]["type"]).upper().startswith("VARCHAR"), table_name
        assert columns["created_at"]["type"].timezone is True, table_name
        assert columns["updated_at"]["type"].timezone is True, table_name


def test_upgrade_is_idempotent(migrated_database):
    _, schema_name, schema_url, config, _ = migrated_database
    engine = create_engine(schema_url)
    _assert_current_test_schema(schema_url, schema_name)
    with engine.connect() as connection:
        before = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    tables_before = set(inspect(engine).get_table_names())
    with _without_database_environment():
        command.upgrade(config, "head")
    with engine.connect() as connection:
        after = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert after == before
    assert set(inspect(engine).get_table_names()) == tables_before


def test_downgrade_removes_only_the_owned_test_schema_tables(migrated_database):
    _, schema_name, schema_url, config, _ = migrated_database
    _assert_current_test_schema(schema_url, schema_name)
    with _without_database_environment():
        command.downgrade(config, "base")
    assert not {
        table for table in inspect(create_engine(schema_url)).get_table_names()
        if table.startswith("api_")
    }


def test_row_version_increments_and_rejects_stale_updates(migrated_database):
    _, _, schema_url, _, _ = migrated_database
    Session = sessionmaker(bind=create_engine(schema_url), expire_on_commit=False)
    with Session() as seed_session:
        project = _seed_project(seed_session, uuid.uuid4().hex)
        seed_session.commit()
        project_id = project.id
    first = Session()
    stale = Session()
    try:
        first_project = first.get(ApiProject, project_id)
        stale_project = stale.get(ApiProject, project_id)
        assert first_project.row_version == 1
        first_project.description = "first writer"
        first.commit()
        assert first_project.row_version == 2
        stale_project.description = "stale writer"
        with pytest.raises(StaleDataError):
            stale.commit()
        stale.rollback()
        with Session() as verify_session:
            persisted = verify_session.get(ApiProject, project_id)
            assert persisted.description == "first writer"
            assert persisted.row_version == 2
    finally:
        first.close()
        stale.close()


def test_active_heads_reject_cross_parent_revisions(migrated_database):
    _, _, schema_url, _, _ = migrated_database
    engine = create_engine(schema_url)
    Session = sessionmaker(bind=engine)
    with Session.begin() as session:
        project = _seed_project(session, uuid.uuid4().hex)
        source_a, source_revision_a = _seed_source(session, project, "a")
        source_b, source_revision_b = _seed_source(session, project, "b")
        environment_a, environment_revision_a = _seed_environment(session, project, "a")
        environment_b, environment_revision_b = _seed_environment(session, project, "b")
        endpoint = ApiSourceEndpoint(
            revision_id=source_revision_a.id,
            stable_key="a" * 64,
            method="GET",
            path="/favorites",
            normalized_path="/favorites",
            operation={"responses": {"200": {}}},
            **_audit(),
        )
        session.add(endpoint)
        session.flush()
        case_a, case_version_a = _seed_case(session, project, endpoint, "a")
        case_b, case_version_b = _seed_case(session, project, endpoint, "b")
        ids = {
            "source_a": source_a.id,
            "source_revision_b": source_revision_b.id,
            "environment_a": environment_a.id,
            "environment_revision_b": environment_revision_b.id,
            "case_a": case_a.id,
            "case_version_b": case_version_b.id,
        }
    invalid_updates = [
        update(ApiSource).where(ApiSource.id == ids["source_a"]).values(
            active_revision_id=ids["source_revision_b"]
        ),
        update(ApiEnvironment).where(ApiEnvironment.id == ids["environment_a"]).values(
            active_revision_id=ids["environment_revision_b"]
        ),
        update(ApiCase).where(ApiCase.id == ids["case_a"]).values(
            active_version_id=ids["case_version_b"]
        ),
    ]
    for statement in invalid_updates:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(statement)


def test_environment_variable_secret_boundary_and_environment_ownership(migrated_database):
    _, _, schema_url, _, _ = migrated_database
    engine = create_engine(schema_url)
    Session = sessionmaker(bind=engine)
    variable_table = Base.metadata.tables["api_environment_variables"]
    with Session.begin() as session:
        project = _seed_project(session, uuid.uuid4().hex)
        environment_a, revision_a = _seed_environment(session, project, "a")
        environment_b, revision_b = _seed_environment(session, project, "b")
        secret_a = ApiSecretValue(
            project_id=project.id,
            environment_id=environment_a.id,
            name="token-a",
            ciphertext="encrypted-a",
            fingerprint="a" * 12,
            **_audit(),
        )
        secret_b = ApiSecretValue(
            project_id=project.id,
            environment_id=environment_b.id,
            name="token-b",
            ciphertext="encrypted-b",
            fingerprint="b" * 12,
            **_audit(),
        )
        session.add_all([secret_a, secret_b])
        session.flush()
        context = {
            "revision_a": revision_a.id,
            "revision_b": revision_b.id,
            "environment_a": environment_a.id,
            "environment_b": environment_b.id,
            "secret_a": secret_a.id,
            "secret_b": secret_b.id,
        }

    def row(name, **overrides):
        payload = {
            "id": str(uuid.uuid4()),
            "revision_id": context["revision_a"],
            "environment_id": context["environment_a"],
            "secret_value_id": None,
            "name": name,
            "value": {"configured": True},
            "is_secret": False,
            "enabled": True,
            "scope": "environment",
            "row_version": 1,
            **_audit(),
        }
        payload.update(overrides)
        return payload

    with engine.begin() as connection:
        connection.execute(variable_table.insert(), row("public-valid"))
        connection.execute(
            variable_table.insert(),
            row(
                "secret-valid",
                value=None,
                is_secret=True,
                secret_value_id=context["secret_a"],
            ),
        )

    invalid_rows = [
        row(
            "secret-plaintext",
            is_secret=True,
            secret_value_id=context["secret_a"],
        ),
        row("secret-without-reference", value=None, is_secret=True),
        row(
            "public-with-secret-reference",
            secret_value_id=context["secret_a"],
        ),
        row("public-without-value", value=None),
        row(
            "secret-cross-environment",
            value=None,
            is_secret=True,
            secret_value_id=context["secret_b"],
        ),
        row(
            "revision-cross-environment",
            revision_id=context["revision_b"],
        ),
    ]
    for invalid_row in invalid_rows:
        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(variable_table.insert(), invalid_row)
