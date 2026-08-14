import os

from alembic import command
import pytest
from sqlalchemy.orm import sessionmaker

from task_server.api_testing.db import engine_for_url
from task_server.api_testing.models.case import ApiCase, ApiCaseVersion
from task_server.api_testing.models.environment import ApiEnvironment, ApiEnvironmentRevision
from task_server.api_testing.models.project import ApiProject
from task_server.api_testing.models.source import ApiSource, ApiSourceEndpoint, ApiSourceRevision
from tests.api_testing.test_migrations import (
    _alembic_config,
    _create_test_schema,
    _database_url,
    _drop_test_schema,
    _without_database_environment,
)


def _audit(actor="owner-a"):
    return {"owner_id": actor, "created_by": actor, "updated_by": actor}


@pytest.fixture(scope="module")
def scheduled_factory():
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


@pytest.fixture()
def scheduled_records(scheduled_factory):
    suffix = os.urandom(5).hex()
    with scheduled_factory.begin() as session:
        project = ApiProject(
            name="scheduled " + suffix,
            slug="scheduled-" + suffix,
            **_audit(),
        )
        session.add(project)
        session.flush()
        source = ApiSource(
            project_id=project.id,
            name="OpenAPI " + suffix,
            source_type="openapi",
            **_audit(),
        )
        session.add(source)
        session.flush()
        revision = ApiSourceRevision(
            source_id=source.id,
            revision_number=1,
            status="active",
            document_hash="a" * 64,
            normalized_document={"openapi": "3.0.3"},
            **_audit(),
        )
        session.add(revision)
        session.flush()
        source.active_revision_id = revision.id
        endpoint = ApiSourceEndpoint(
            revision_id=revision.id,
            stable_key="b" * 64,
            operation_id="favorite-list",
            method="GET",
            path="/favorites",
            normalized_path="/favorites",
            summary="查询收藏",
            operation={},
            **_audit(),
        )
        environment = ApiEnvironment(
            project_id=project.id,
            source_id=source.id,
            name="生产环境",
            **_audit(),
        )
        session.add_all((endpoint, environment))
        session.flush()
        environment_revision = ApiEnvironmentRevision(
            environment_id=environment.id,
            source_revision_id=revision.id,
            revision_number=1,
            name="生产环境",
            **_audit(),
        )
        session.add(environment_revision)
        session.flush()
        environment.active_revision_id = environment_revision.id
        case = ApiCase(
            project_id=project.id,
            endpoint_id=endpoint.id,
            name="收藏正常查询",
            origin="manual",
            active_version_id=None,
            **_audit(),
        )
        session.add(case)
        session.flush()
        version = ApiCaseVersion(
            case_id=case.id,
            endpoint_id=endpoint.id,
            version_number=1,
            status="draft",
            purpose="定时任务手动执行",
            request_template={
                "name": "收藏正常查询",
                "request": {
                    "method": "GET",
                    "path": "/favorites",
                    "path_params": {},
                    "query": {},
                    "headers": {},
                    "cookies": {},
                    "body": None,
                },
            },
            **_audit(),
        )
        session.add(version)
        session.flush()
        case.active_version_id = version.id
        return {
            "project": project,
            "source_revision": revision,
            "environment": environment,
            "environment_revision": environment_revision,
            "case_version": version,
        }


def test_scheduled_job_can_be_created_and_manually_run(scheduled_factory, scheduled_records):
    from task_server.api_testing.services.scheduled_job_service import ScheduledJobService

    service = ScheduledJobService(scheduled_factory, enqueue=lambda execution_id: None)
    job = service.create(
        {
            "project_id": scheduled_records["project"].id,
            "name": "每日发版回归",
            "schedule_type": "daily",
            "cron_expression": "",
            "environment_strategy": "fixed_revision",
            "environment_revision_id": scheduled_records["environment_revision"].id,
            "target_type": "cases",
            "target_ids": [scheduled_records["case_version"].id],
            "enabled": True,
            "notify_feishu": True,
            "retry_count": 1,
            "timeout_seconds": 900,
        },
        "owner-a",
    )

    execution = service.run_once(job.id, "owner-a", idempotency_key="scheduled-" + job.id)

    assert job.name == "每日发版回归"
    assert job.target_type == "cases"
    assert job.target_ids == (scheduled_records["case_version"].id,)
    assert execution.execution_type == "scheduled"
    assert execution.execution_source == "scheduled_job"
    assert execution.task_name == "每日发版回归"
