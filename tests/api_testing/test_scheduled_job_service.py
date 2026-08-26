import os
from datetime import datetime

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

    listed = service.list(scheduled_records["project"].id, "owner-a")[0]
    assert listed.effective_cron_expression == "0 2 * * *"
    assert listed.scheduler_timezone
    assert listed.scheduler_utc_offset[0] in {"+", "-"}
    assert len(listed.scheduler_utc_offset) == 6
    assert listed.next_run_at is not None
    assert listed.latest_run_at is not None
    assert listed.latest_run_trigger == "manual"
    assert listed.latest_execution_state == execution.state
    assert listed.latest_execution_summary == execution.summary


def test_next_cron_match_uses_the_next_minute_and_supports_weekdays():
    from task_server.api_testing.services.scheduled_job_service import _next_cron_match

    assert _next_cron_match(
        "0 10 * * *",
        datetime(2026, 8, 17, 9, 59, 12),
    ) == datetime(2026, 8, 17, 10, 0)
    assert _next_cron_match(
        "0 9 * * 1-5",
        datetime(2026, 8, 21, 9, 0),
    ) == datetime(2026, 8, 24, 9, 0)


def test_scheduled_job_can_be_updated_and_deleted(scheduled_factory, scheduled_records):
    from task_server.api_testing.services.scheduled_job_service import ScheduledJobService

    service = ScheduledJobService(scheduled_factory)
    job = service.create(
        {
            "project_id": scheduled_records["project"].id,
            "name": "每日发版回归",
            "schedule_type": "daily",
            "cron_expression": "0 2 * * *",
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

    updated = service.update(
        job.id,
        {
            "project_id": scheduled_records["project"].id,
            "name": "每周发版回归",
            "schedule_type": "weekly",
            "cron_expression": "0 9 * * 1",
            "environment_strategy": "fixed_revision",
            "environment_revision_id": scheduled_records["environment_revision"].id,
            "target_type": "cases",
            "target_ids": [scheduled_records["case_version"].id],
            "enabled": False,
            "notify_feishu": False,
            "retry_count": 2,
            "timeout_seconds": 1200,
        },
        "owner-a",
    )

    assert updated.id == job.id
    assert updated.name == "每周发版回归"
    assert updated.schedule_type == "weekly"
    assert updated.cron_expression == "0 9 * * 1"
    assert updated.enabled is False
    assert updated.notify_feishu is False
    assert updated.retry_count == 2
    assert updated.timeout_seconds == 1200

    deleted = service.delete(job.id, "owner-a")

    assert deleted.id == job.id
    assert service.list(scheduled_records["project"].id, "owner-a") == ()


def test_due_scheduled_job_dispatches_once_per_matching_minute(scheduled_factory, scheduled_records):
    from task_server.api_testing.services.scheduled_job_service import ScheduledJobService

    enqueued = []
    service = ScheduledJobService(scheduled_factory, enqueue=enqueued.append)
    job = service.create(
        {
            "project_id": scheduled_records["project"].id,
            "name": "凌晨回归",
            "schedule_type": "cron",
            "cron_expression": "17 3 * * *",
            "environment_strategy": "fixed_revision",
            "environment_revision_id": scheduled_records["environment_revision"].id,
            "target_type": "cases",
            "target_ids": [scheduled_records["case_version"].id],
            "enabled": True,
            "notify_feishu": True,
            "retry_count": 0,
            "timeout_seconds": 900,
        },
        "owner-a",
    )

    first = service.dispatch_due(now=datetime(2026, 8, 17, 3, 17, 8))
    second = service.dispatch_due(now=datetime(2026, 8, 17, 3, 17, 45))
    missed = service.dispatch_due(now=datetime(2026, 8, 17, 3, 18, 0))

    assert len(first) == 1
    assert second == ()
    assert missed == ()
    assert enqueued == [first[0].id]
    assert first[0].task_name == job.name
