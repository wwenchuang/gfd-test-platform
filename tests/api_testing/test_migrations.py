import os
from pathlib import Path
import subprocess
import sys

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from task_server.api_testing.models import Base


ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG = ROOT / "task_server" / "api_testing" / "migrations" / "alembic.ini"

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


def _database_url():
    value = os.getenv("TEST_DATABASE_URL", "").strip()
    if not value:
        pytest.skip("TEST_DATABASE_URL is required for PostgreSQL migration tests")
    if not value.startswith(("postgresql://", "postgresql+psycopg://")):
        pytest.fail("TEST_DATABASE_URL must point to PostgreSQL")
    return value


def _alembic_config(database_url):
    config = Config(str(ALEMBIC_CONFIG))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


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
    for table_name in PHASE1_TABLES:
        assert f"create table {table_name}" in generated_sql


def test_mutable_heads_are_protected_by_explicit_foreign_keys():
    expected_targets = {
        ("api_sources", "active_revision_id"): "api_source_revisions.id",
        ("api_environments", "active_revision_id"): "api_environment_revisions.id",
        ("api_cases", "active_version_id"): "api_case_versions.id",
    }

    for (table_name, column_name), expected_target in expected_targets.items():
        foreign_keys = Base.metadata.tables[table_name].c[column_name].foreign_keys
        assert {foreign_key.target_fullname for foreign_key in foreign_keys} == {
            expected_target
        }


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


@pytest.fixture()
def migrated_database():
    database_url = _database_url()
    config = _alembic_config(database_url)
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    try:
        yield database_url, config
    finally:
        command.downgrade(config, "base")


def test_upgrade_creates_complete_phase1_schema(migrated_database):
    database_url, _ = migrated_database
    inspector = inspect(create_engine(database_url))

    assert PHASE1_TABLES.issubset(set(inspector.get_table_names()))


def test_phase1_schema_uses_explicit_foreign_keys_indexes_and_jsonb(migrated_database):
    database_url, _ = migrated_database
    inspector = inspect(create_engine(database_url))

    for table_name in PHASE1_TABLES - {"api_projects"}:
        assert inspector.get_foreign_keys(table_name), f"{table_name} has no explicit foreign key"

    for table_name, expected_names in EXPECTED_INDEXES.items():
        actual_names = {item["name"] for item in inspector.get_indexes(table_name)}
        assert expected_names.issubset(actual_names)

    for table_name, expected_columns in JSONB_COLUMNS.items():
        actual = {item["name"]: str(item["type"]).upper() for item in inspector.get_columns(table_name)}
        for column_name in expected_columns:
            assert actual[column_name] == "JSONB"


def test_all_primary_tables_have_uuid_owner_and_utc_audit_columns(migrated_database):
    database_url, _ = migrated_database
    inspector = inspect(create_engine(database_url))

    for table_name in PHASE1_TABLES:
        columns = {column["name"]: column for column in inspector.get_columns(table_name)}
        assert {"id", "owner_id", "created_at", "updated_at"}.issubset(columns), table_name
        assert str(columns["id"]["type"]).upper().startswith("VARCHAR"), table_name
        assert columns["created_at"]["type"].timezone is True, table_name
        assert columns["updated_at"]["type"].timezone is True, table_name


def test_upgrade_is_idempotent(migrated_database):
    database_url, config = migrated_database
    engine = create_engine(database_url)
    with engine.connect() as connection:
        before = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    tables_before = set(inspect(engine).get_table_names())

    command.upgrade(config, "head")

    with engine.connect() as connection:
        after = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert after == before
    assert set(inspect(engine).get_table_names()) == tables_before
