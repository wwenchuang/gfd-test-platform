import os
from types import SimpleNamespace

from alembic import command
import pytest
from sqlalchemy.orm import sessionmaker

from task_server.api_testing.db import engine_for_url
from task_server.api_testing.models.case import ApiAiJob
from task_server.api_testing.models.environment import ApiEnvironment, ApiEnvironmentRevision
from task_server.api_testing.models.execution import ApiExecution
from task_server.api_testing.models.project import ApiProject
from task_server.api_testing.models.source import ApiSource, ApiSourceEndpoint, ApiSourceRevision
from task_server.api_testing.services.test_task_service import (
    TestTaskNotFoundError as TaskNotFoundError,
    TestTaskScopeError as TaskScopeError,
    TestTaskService as TaskService,
)
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
def task_factory():
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
def task_records(task_factory):
    suffix = os.urandom(5).hex()
    with task_factory.begin() as session:
        project = ApiProject(
            name="3D " + suffix,
            slug="task-project-" + suffix,
            **_audit(),
        )
        other_project = ApiProject(
            name="other " + suffix,
            slug="task-other-" + suffix,
            **_audit("owner-b"),
        )
        session.add_all((project, other_project))
        session.flush()
        source = ApiSource(
            project_id=project.id,
            name="Apifox 3D " + suffix,
            source_type="apifox",
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
        other_revision = ApiSourceRevision(
            source_id=source.id,
            revision_number=2,
            status="candidate",
            document_hash="b" * 64,
            normalized_document={"openapi": "3.0.3"},
            **_audit(),
        )
        session.add_all((revision, other_revision))
        session.flush()
        source.active_revision_id = revision.id
        endpoint = ApiSourceEndpoint(
            revision_id=revision.id,
            stable_key="c" * 64,
            operation_id="favorites",
            method="GET",
            path="/favorites",
            normalized_path="/favorites",
            operation={},
            **_audit(),
        )
        foreign_endpoint = ApiSourceEndpoint(
            revision_id=other_revision.id,
            stable_key="d" * 64,
            operation_id="foreign",
            method="GET",
            path="/foreign",
            normalized_path="/foreign",
            operation={},
            **_audit(),
        )
        environment = ApiEnvironment(
            project_id=project.id,
            source_id=source.id,
            name="生产环境",
            **_audit(),
        )
        session.add_all((endpoint, foreign_endpoint, environment))
        session.flush()
        environment_revision = ApiEnvironmentRevision(
            environment_id=environment.id,
            source_revision_id=revision.id,
            revision_number=1,
            name="生产环境",
            **_audit(),
        )
        runtime_environment_revision = ApiEnvironmentRevision(
            environment_id=environment.id,
            source_revision_id=revision.id,
            revision_number=2,
            name="生产环境 V2",
            **_audit(),
        )
        session.add_all((environment_revision, runtime_environment_revision))
        session.flush()
        environment.active_revision_id = environment_revision.id
        ai_job = ApiAiJob(
            project_id=project.id,
            environment_revision_id=environment_revision.id,
            state="completed",
            endpoint_ids=[endpoint.id],
            summary={"generated_drafts": 2},
            **_audit(),
        )
        execution = ApiExecution(
            project_id=project.id,
            source_revision_id=revision.id,
            environment_revision_id=environment_revision.id,
            execution_type="regression",
            state="DONE",
            idempotency_key="task-execution-" + suffix,
            requested_case_ids=[],
            request_snapshot={},
            summary={"total": 3, "passed": 3, "failed": 0, "broken": 0, "cancelled": 0},
            **_audit(),
        )
        runtime_ai_job = ApiAiJob(
            project_id=project.id,
            environment_revision_id=runtime_environment_revision.id,
            state="completed",
            endpoint_ids=[endpoint.id],
            summary={"generated_drafts": 1},
            **_audit(),
        )
        runtime_execution = ApiExecution(
            project_id=project.id,
            source_revision_id=revision.id,
            environment_revision_id=runtime_environment_revision.id,
            execution_type="regression",
            state="DONE",
            idempotency_key="task-runtime-execution-" + suffix,
            requested_case_ids=[],
            request_snapshot={},
            summary={"total": 1, "passed": 1, "failed": 0, "broken": 0, "cancelled": 0},
            **_audit(),
        )
        session.add_all((ai_job, execution, runtime_ai_job, runtime_execution))
        session.flush()
        return {
            "project": project,
            "other_project": other_project,
            "revision": revision,
            "endpoint": endpoint,
            "foreign_endpoint": foreign_endpoint,
            "environment_revision": environment_revision,
            "runtime_environment_revision": runtime_environment_revision,
            "ai_job": ai_job,
            "execution": execution,
            "runtime_ai_job": runtime_ai_job,
            "runtime_execution": runtime_execution,
        }


def _payload(records, endpoint_ids=None):
    return {
        "project_id": records["project"].id,
        "source_revision_id": records["revision"].id,
        "environment_revision_id": records["environment_revision"].id,
        "name": "我的收藏接口回归",
        "selected_endpoint_ids": endpoint_ids or [records["endpoint"].id],
    }


def test_task_view_exposes_covered_endpoints_and_actual_baseline_versions():
    class Repository:
        @staticmethod
        def runnable_baseline_counts(_task):
            return 1, 2

    task = SimpleNamespace(
        id="task-1",
        project_id="project-1",
        source_revision_id="source-1",
        environment_revision_id="environment-1",
        name="同一接口多版本基线",
        state="ready",
        selected_endpoint_ids=["endpoint-1"],
        latest_ai_job_id=None,
        latest_execution_id=None,
        summary={},
        created_at=None,
        updated_at=None,
    )

    view = TaskService._view(Repository(), task, latest_execution=None)

    assert view.runnable_endpoint_count == 1
    assert view.runnable_baseline_count == 2


def test_task_restores_selection_and_advances_through_ai_and_execution(
    task_factory, task_records
):
    service = TaskService(task_factory)
    task = service.save_context("owner-a", _payload(task_records), "owner-a")

    designing = service.attach_ai_job(task.id, task_records["ai_job"].id, "owner-a")
    running = service.attach_execution(
        task.id, task_records["execution"].id, "owner-a"
    )
    completed = service.refresh_terminal_summary(task.id, "owner-a")

    assert designing.state == "designing"
    assert running.state == "running"
    assert completed.state == "completed"
    assert completed.summary == {
        "total": 3,
        "passed": 3,
        "failed": 0,
        "broken": 0,
        "cancelled": 0,
    }
    assert completed.latest_ai_job_id == task_records["ai_job"].id
    assert completed.latest_execution_id == task_records["execution"].id
    assert service.get_active(task_records["project"].id, "owner-a") is None


def test_saving_again_updates_the_current_task_instead_of_creating_duplicates(
    task_factory, task_records
):
    service = TaskService(task_factory)
    first = service.save_context("owner-a", _payload(task_records), "owner-a")
    second = service.save_context(
        "owner-a",
        {**_payload(task_records), "name": "收藏回归（已调整）"},
        "owner-a",
    )

    assert second.id == first.id
    assert second.name == "收藏回归（已调整）"
    assert service.get_active(task_records["project"].id, "owner-a").id == first.id


def test_create_task_keeps_earlier_saved_tasks_visible(task_factory, task_records):
    service = TaskService(task_factory)
    first = service.create_context("owner-a", _payload(task_records), "owner-a")
    second = service.create_context(
        "owner-a",
        {**_payload(task_records), "name": "新增收藏冒烟"},
        "owner-a",
    )

    tasks = service.list(task_records["project"].id, "owner-a")

    assert second.id != first.id
    assert [item.id for item in tasks][:2] == [second.id, first.id]
    assert service.get_active(task_records["project"].id, "owner-a").id == second.id


def test_task_can_be_renamed_without_changing_scope(task_factory, task_records):
    service = TaskService(task_factory)
    task = service.create_context("owner-a", _payload(task_records), "owner-a")

    renamed = service.rename(task.id, "发版收藏回归", "owner-a")

    assert renamed.id == task.id
    assert renamed.name == "发版收藏回归"
    assert renamed.selected_endpoint_ids == task.selected_endpoint_ids


def test_task_can_be_deleted_from_saved_list(task_factory, task_records):
    service = TaskService(task_factory)
    first = service.create_context("owner-a", _payload(task_records), "owner-a")
    second = service.create_context(
        "owner-a",
        {**_payload(task_records), "name": "保留的任务"},
        "owner-a",
    )

    deleted = service.delete(first.id, "owner-a")
    tasks = service.list(task_records["project"].id, "owner-a")

    assert deleted.id == first.id
    assert [item.id for item in tasks] == [second.id]
    with pytest.raises(TaskNotFoundError):
        service.get(first.id, "owner-a")


def test_task_rejects_endpoint_from_another_source_revision(
    task_factory, task_records
):
    service = TaskService(task_factory)

    with pytest.raises(TaskScopeError):
        service.save_context(
            "owner-a",
            _payload(task_records, [task_records["foreign_endpoint"].id]),
            "owner-a",
        )


def test_task_is_hidden_from_another_owner(task_factory, task_records):
    service = TaskService(task_factory)
    task = service.save_context("owner-a", _payload(task_records), "owner-a")

    with pytest.raises(TaskNotFoundError):
        service.get(task.id, "owner-b")


def test_worker_refreshes_task_when_ai_job_finishes(task_factory, task_records):
    service = TaskService(task_factory)
    task = service.save_context("owner-a", _payload(task_records), "owner-a")
    service.attach_ai_job(task.id, task_records["ai_job"].id, "owner-a")

    refreshed = service.refresh_for_ai_job(task_records["ai_job"].id)

    assert refreshed.state == "ready"
    assert refreshed.latest_ai_job_id == task_records["ai_job"].id


def test_ai_summary_does_not_replace_latest_execution_evidence(task_factory, task_records):
    service = TaskService(task_factory)
    task = service.save_context("owner-a", _payload(task_records), "owner-a")
    service.attach_execution(task.id, task_records["execution"].id, "owner-a")
    service.refresh_for_execution(task_records["execution"].id)
    service.attach_ai_job(task.id, task_records["ai_job"].id, "owner-a")

    refreshed = service.refresh_for_ai_job(task_records["ai_job"].id)

    assert refreshed.summary["ai_state"] == "completed"
    assert refreshed.latest_execution_id == task_records["execution"].id
    assert refreshed.latest_execution_state == "DONE"
    assert refreshed.latest_execution_summary["total"] == 3
    assert refreshed.latest_execution_summary["passed"] == 3
    assert refreshed.latest_execution_at is not None


def test_worker_refreshes_task_when_execution_finishes(task_factory, task_records):
    service = TaskService(task_factory)
    task = service.save_context("owner-a", _payload(task_records), "owner-a")
    service.attach_execution(
        task.id, task_records["execution"].id, "owner-a"
    )

    refreshed = service.refresh_for_execution(task_records["execution"].id)

    assert refreshed.state == "completed"
    assert refreshed.summary["passed"] == 3


def test_task_allows_runtime_environment_for_ai_job(task_factory, task_records):
    service = TaskService(task_factory)
    task = service.save_context("owner-a", _payload(task_records), "owner-a")

    designing = service.attach_ai_job(
        task.id,
        task_records["runtime_ai_job"].id,
        "owner-a",
    )

    assert designing.state == "designing"
    assert designing.latest_ai_job_id == task_records["runtime_ai_job"].id
    assert designing.environment_revision_id == task_records["environment_revision"].id


def test_task_allows_runtime_environment_for_execution(task_factory, task_records):
    service = TaskService(task_factory)
    task = service.save_context("owner-a", _payload(task_records), "owner-a")

    running = service.attach_execution(
        task.id,
        task_records["runtime_execution"].id,
        "owner-a",
    )

    assert running.state == "running"
    assert running.latest_execution_id == task_records["runtime_execution"].id
    assert running.environment_revision_id == task_records["environment_revision"].id
