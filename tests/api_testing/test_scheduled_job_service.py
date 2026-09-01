import os
import json
from datetime import datetime

from alembic import command
import pytest
from sqlalchemy.orm import sessionmaker

from task_server.api_testing.db import engine_for_url
from task_server.api_testing.models.case import ApiBaseline, ApiCase, ApiCaseVersion
from task_server.api_testing.models.environment import ApiEnvironment, ApiEnvironmentRevision
from task_server.api_testing.models.execution import ApiExecution, ApiExecutionCase
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
                "app_package": "com.kfb.model",
                "app_name": "智小白3D",
                "business": "home",
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
        execution = ApiExecution(
            project_id=project.id,
            source_revision_id=revision.id,
            environment_revision_id=environment_revision.id,
            execution_type="debug",
            state="DONE",
            idempotency_key="scheduled-baseline-" + suffix,
            requested_case_ids=[case.id],
            request_snapshot={},
            **_audit(),
        )
        session.add(execution)
        session.flush()
        execution_case = ApiExecutionCase(
            execution_id=execution.id,
            case_version_id=version.id,
            endpoint_id=endpoint.id,
            environment_revision_id=environment_revision.id,
            ordinal=1,
            status="PASSED",
            sanitized_result={},
            **_audit(),
        )
        session.add(execution_case)
        session.flush()
        baseline = ApiBaseline(
            project_id=project.id,
            case_id=case.id,
            case_version_id=version.id,
            environment_revision_id=environment_revision.id,
            debug_execution_case_id=execution_case.id,
            group_name="核心回归",
            status="active",
            adoption_reason="定时任务测试基线",
            **_audit(),
        )
        session.add(baseline)
        session.flush()
        return {
            "project": project,
            "source_revision": revision,
            "environment": environment,
            "environment_revision": environment_revision,
            "case_version": version,
            "baseline": baseline,
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
    assert listed.allow_one_time_baselines is False


def test_scheduled_baseline_job_can_explicitly_include_one_time_baselines(
    scheduled_factory, scheduled_records
):
    from task_server.api_testing.services.scheduled_job_service import ScheduledJobService

    with scheduled_factory.begin() as session:
        version = session.get(ApiCaseVersion, scheduled_records["case_version"].id)
        version.request_template = {
            **dict(version.request_template or {}),
            "name": "收藏初始化 - 一次性人工验证",
        }

    service = ScheduledJobService(scheduled_factory, enqueue=lambda execution_id: None)
    job = service.create(
        {
            "project_id": scheduled_records["project"].id,
            "name": "每日一次性基线回归",
            "schedule_type": "daily",
            "cron_expression": "0 8 * * *",
            "environment_strategy": "fixed_revision",
            "environment_revision_id": scheduled_records["environment_revision"].id,
            "target_type": "baselines",
            "target_ids": [scheduled_records["baseline"].id],
            "enabled": True,
            "notify_feishu": False,
            "allow_one_time_baselines": True,
            "retry_count": 0,
            "timeout_seconds": 900,
        },
        "owner-a",
    )

    execution = service.run_once(
        job.id,
        "owner-a",
        idempotency_key="scheduled-one-time-" + job.id,
    )

    assert job.allow_one_time_baselines is True
    assert execution.execution_type == "baseline_regression"
    assert execution.case_results[0]["case_version_id"] == scheduled_records["case_version"].id


def test_new_scheduled_target_rejects_a_disabled_application(
    scheduled_factory, scheduled_records, tmp_path, monkeypatch
):
    from task_server.api_testing.services.scheduled_job_service import (
        ScheduledJobInputError,
        ScheduledJobService,
    )
    from task_server.services import business_line_service

    path = tmp_path / "task-apps.json"
    path.write_text(json.dumps({"apps": [{
        "package": "com.kfb.model",
        "name": "智小白3D",
        "enabled": False,
        "business_lines": [{"id": "home", "name": "家用", "enabled": True}],
    }]}), encoding="utf-8")
    monkeypatch.setattr(business_line_service, "TASK_APPS_FILE", str(path))

    with pytest.raises(ScheduledJobInputError, match="应用.*已停用"):
        ScheduledJobService(scheduled_factory).create(
            {
                "project_id": scheduled_records["project"].id,
                "name": "停用应用回归",
                "schedule_type": "daily",
                "cron_expression": "0 2 * * *",
                "environment_strategy": "fixed_revision",
                "environment_revision_id": scheduled_records["environment_revision"].id,
                "target_type": "cases",
                "target_ids": [scheduled_records["case_version"].id],
                "enabled": True,
                "notify_feishu": False,
                "retry_count": 0,
                "timeout_seconds": 900,
            },
            "owner-a",
        )


def test_existing_disabled_target_remains_editable_but_cannot_run(
    scheduled_factory, scheduled_records, tmp_path, monkeypatch
):
    from task_server.api_testing.services.scheduled_job_service import (
        ScheduledJobInputError,
        ScheduledJobService,
    )
    from task_server.services import business_line_service

    service = ScheduledJobService(scheduled_factory)
    payload = {
        "project_id": scheduled_records["project"].id,
        "name": "历史回归",
        "schedule_type": "daily",
        "cron_expression": "0 2 * * *",
        "environment_strategy": "fixed_revision",
        "environment_revision_id": scheduled_records["environment_revision"].id,
        "target_type": "cases",
        "target_ids": [scheduled_records["case_version"].id],
        "enabled": True,
        "notify_feishu": False,
        "retry_count": 0,
        "timeout_seconds": 900,
    }
    job = service.create(payload, "owner-a")

    path = tmp_path / "task-apps.json"
    path.write_text(json.dumps({"apps": [{
        "package": "com.kfb.model",
        "name": "智小白3D",
        "enabled": False,
        "business_lines": [{"id": "home", "name": "家用", "enabled": True}],
    }]}), encoding="utf-8")
    monkeypatch.setattr(business_line_service, "TASK_APPS_FILE", str(path))

    updated = service.update(job.id, {**payload, "name": "历史回归（改周期）"}, "owner-a")
    assert updated.name == "历史回归（改周期）"
    with pytest.raises(ScheduledJobInputError, match="应用.*已停用"):
        service.run_once(job.id, "owner-a", idempotency_key="disabled-" + job.id)


@pytest.mark.parametrize(
    ("target_type", "target_ids"),
    (("baselines", "baseline"), ("baseline_group", "核心回归")),
)
def test_new_scheduled_target_rejects_superseded_baselines(
    scheduled_factory, scheduled_records, target_type, target_ids
):
    from task_server.api_testing.services.scheduled_job_service import (
        ScheduledJobInputError,
        ScheduledJobService,
    )

    with scheduled_factory.begin() as session:
        session.get(ApiBaseline, scheduled_records["baseline"].id).status = "superseded"

    selected_ids = [
        scheduled_records["baseline"].id if target_ids == "baseline" else target_ids
    ]
    with pytest.raises(ScheduledJobInputError, match="没有当前有效基线|当前有效基线不存在"):
        ScheduledJobService(scheduled_factory).create(
            {
                "project_id": scheduled_records["project"].id,
                "name": "历史基线不应进入新计划",
                "schedule_type": "daily",
                "cron_expression": "0 2 * * *",
                "environment_strategy": "fixed_revision",
                "environment_revision_id": scheduled_records["environment_revision"].id,
                "target_type": target_type,
                "target_ids": selected_ids,
                "enabled": True,
                "notify_feishu": False,
                "retry_count": 0,
                "timeout_seconds": 900,
            },
            "owner-a",
        )


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
