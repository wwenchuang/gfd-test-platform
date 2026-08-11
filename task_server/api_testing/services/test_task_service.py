"""Persist and advance one lightweight API testing workflow task."""

import copy
from types import MappingProxyType

from ..contracts.test_task import ApiTestTaskView
from ..repositories.test_task_repository import TestTaskRepository


class TestTaskInputError(ValueError):
    pass


class TestTaskScopeError(ValueError):
    pass


class TestTaskNotFoundError(LookupError):
    pass


def _text(value, label, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise TestTaskInputError("%s is invalid" % label)
    return value.strip()


def _selection(value):
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 500
        or not all(isinstance(item, str) and item for item in value)
        or len(set(value)) != len(value)
    ):
        raise TestTaskInputError(
            "selected_endpoint_ids must be a unique non-empty string array"
        )
    return list(value)


class TestTaskService:
    def __init__(self, session_factory):
        self._session_factory = session_factory

    def save_context(self, owner_id, payload, actor_id):
        owner = _text(owner_id, "owner id", 128)
        actor = _text(actor_id, "actor id", 128)
        if owner != actor:
            raise TestTaskScopeError("task actor does not own this workflow")
        parsed = self._parse_context(payload)
        with self._session_factory.begin() as session:
            repository = TestTaskRepository(session)
            self._validate_context(repository, owner, parsed)
            task = repository.get_active(
                parsed["project_id"], owner, for_update=True
            )
            if task is None:
                task = repository.create(parsed, actor)
            else:
                for name in (
                    "source_revision_id",
                    "environment_revision_id",
                    "name",
                    "selected_endpoint_ids",
                ):
                    setattr(task, name, copy.deepcopy(parsed[name]))
                task.state = "draft"
                task.latest_ai_job_id = None
                task.latest_execution_id = None
                task.summary = {}
                task.updated_by = actor
                repository.flush()
            return self._view(repository, task)

    def update_context(self, task_id, owner_id, payload, actor_id):
        owner = _text(owner_id, "owner id", 128)
        actor = _text(actor_id, "actor id", 128)
        if owner != actor:
            raise TestTaskScopeError("task actor does not own this workflow")
        parsed = self._parse_context(payload)
        with self._session_factory.begin() as session:
            repository = TestTaskRepository(session)
            task = self._owned_task(repository, task_id, owner, for_update=True)
            if parsed["project_id"] != task.project_id:
                raise TestTaskScopeError("task project cannot be changed")
            self._validate_context(repository, owner, parsed)
            for name in (
                "source_revision_id",
                "environment_revision_id",
                "name",
                "selected_endpoint_ids",
            ):
                setattr(task, name, copy.deepcopy(parsed[name]))
            task.state = "draft"
            task.latest_ai_job_id = None
            task.latest_execution_id = None
            task.summary = {}
            task.updated_by = actor
            repository.flush()
            return self._view(repository, task)

    def get(self, task_id, owner_id):
        owner = _text(owner_id, "owner id", 128)
        with self._session_factory() as session:
            repository = TestTaskRepository(session)
            task = repository.get_task(task_id)
            if task is None or task.owner_id != owner:
                raise TestTaskNotFoundError("API testing task was not found")
            return self._view(repository, task)

    def get_active(self, project_id, owner_id):
        owner = _text(owner_id, "owner id", 128)
        with self._session_factory() as session:
            repository = TestTaskRepository(session)
            project = repository.get_project(project_id)
            if project is None or project.owner_id != owner:
                raise TestTaskNotFoundError("API testing project was not found")
            task = repository.get_active(project_id, owner)
            return self._view(repository, task) if task is not None else None

    def attach_ai_job(self, task_id, ai_job_id, actor_id):
        actor = _text(actor_id, "actor id", 128)
        with self._session_factory.begin() as session:
            repository = TestTaskRepository(session)
            task = self._owned_task(repository, task_id, actor, for_update=True)
            job = repository.get_ai_job(ai_job_id)
            if (
                job is None
                or job.owner_id != actor
                or job.project_id != task.project_id
                or job.environment_revision_id != task.environment_revision_id
                or not set(job.endpoint_ids).issubset(set(task.selected_endpoint_ids))
            ):
                raise TestTaskScopeError("AI job does not match this task")
            task.latest_ai_job_id = job.id
            task.state = "designing"
            task.updated_by = actor
            repository.flush()
            return self._view(repository, task)

    def attach_execution(self, task_id, execution_id, actor_id):
        actor = _text(actor_id, "actor id", 128)
        with self._session_factory.begin() as session:
            repository = TestTaskRepository(session)
            task = self._owned_task(repository, task_id, actor, for_update=True)
            execution = repository.get_execution(execution_id)
            if (
                execution is None
                or execution.owner_id != actor
                or execution.project_id != task.project_id
                or execution.source_revision_id != task.source_revision_id
                or execution.environment_revision_id != task.environment_revision_id
            ):
                raise TestTaskScopeError("execution does not match this task")
            snapshot_versions = execution.request_snapshot.get("case_versions", [])
            execution_endpoint_ids = {
                str(item.get("endpoint_id"))
                for item in snapshot_versions
                if isinstance(item, dict) and item.get("endpoint_id")
            }
            if execution_endpoint_ids and not execution_endpoint_ids.issubset(
                set(task.selected_endpoint_ids)
            ):
                raise TestTaskScopeError(
                    "execution contains endpoints outside this task"
                )
            task.latest_execution_id = execution.id
            task.state = (
                "debugging" if execution.execution_type == "debug" else "running"
            )
            task.updated_by = actor
            repository.flush()
            return self._view(repository, task)

    def refresh_terminal_summary(self, task_id, actor_id):
        actor = _text(actor_id, "actor id", 128)
        with self._session_factory.begin() as session:
            repository = TestTaskRepository(session)
            task = self._owned_task(repository, task_id, actor, for_update=True)
            execution = repository.get_execution(task.latest_execution_id)
            if execution is None:
                raise TestTaskScopeError("task execution was not found")
            self._apply_execution_terminal(task, execution, actor)
            repository.flush()
            return self._view(repository, task)

    def refresh_for_ai_job(self, ai_job_id):
        with self._session_factory.begin() as session:
            repository = TestTaskRepository(session)
            task = repository.get_by_ai_job(ai_job_id, for_update=True)
            if task is None:
                return None
            job = repository.get_ai_job(ai_job_id)
            if job is None:
                return None
            if job.state in {"completed", "partial"}:
                task.state = "ready"
            elif job.state in {"failed_validation", "failed_gateway"}:
                task.state = "failed"
            else:
                return self._view(repository, task)
            task.summary = {
                "ai_state": job.state,
                "ai": copy.deepcopy(dict(job.summary)),
            }
            task.updated_by = "worker"
            repository.flush()
            return self._view(repository, task)

    def refresh_for_execution(self, execution_id):
        with self._session_factory.begin() as session:
            repository = TestTaskRepository(session)
            task = repository.get_by_execution(execution_id, for_update=True)
            if task is None:
                return None
            execution = repository.get_execution(execution_id)
            if execution is None:
                return None
            self._apply_execution_terminal(task, execution, "worker")
            repository.flush()
            return self._view(repository, task)

    @staticmethod
    def _apply_execution_terminal(task, execution, actor_id):
        if execution.state not in {"DONE", "CANCELLED"}:
            return
        task.summary = copy.deepcopy(dict(execution.summary))
        if execution.execution_type == "debug":
            task.state = "ready"
        elif execution.state == "CANCELLED":
            task.state = "failed"
        elif int(task.summary.get("failed", 0)) or int(
            task.summary.get("broken", 0)
        ):
            task.state = "failed"
        else:
            task.state = "completed"
        task.updated_by = actor_id

    @staticmethod
    def _parse_context(payload):
        if not isinstance(payload, dict):
            raise TestTaskInputError("task payload must be an object")
        return {
            "project_id": _text(payload.get("project_id"), "project id", 36),
            "source_revision_id": _text(
                payload.get("source_revision_id"), "source revision id", 36
            ),
            "environment_revision_id": _text(
                payload.get("environment_revision_id"),
                "environment revision id",
                36,
            ),
            "name": _text(payload.get("name"), "task name"),
            "selected_endpoint_ids": _selection(
                payload.get("selected_endpoint_ids")
            ),
        }

    @staticmethod
    def _owned_task(repository, task_id, actor_id, *, for_update=False):
        task = repository.get_task(task_id, for_update=for_update)
        if task is None or task.owner_id != actor_id:
            raise TestTaskNotFoundError("API testing task was not found")
        return task

    @staticmethod
    def _validate_context(repository, owner, payload):
        project = repository.get_project(payload["project_id"])
        source_revision = repository.get_source_revision(
            payload["source_revision_id"]
        )
        source = (
            repository.get_source(source_revision.source_id)
            if source_revision is not None
            else None
        )
        environment_revision = repository.get_environment_revision(
            payload["environment_revision_id"]
        )
        environment = (
            repository.get_environment(environment_revision.environment_id)
            if environment_revision is not None
            else None
        )
        if (
            project is None
            or project.owner_id != owner
            or source is None
            or source.project_id != project.id
            or environment is None
            or environment.project_id != project.id
            or environment_revision.source_revision_id != source_revision.id
        ):
            raise TestTaskScopeError("task context is outside this project")
        endpoints = repository.get_endpoints(payload["selected_endpoint_ids"])
        if len(endpoints) != len(payload["selected_endpoint_ids"]) or any(
            item.revision_id != source_revision.id for item in endpoints.values()
        ):
            raise TestTaskScopeError(
                "selected endpoint does not belong to the source revision"
            )

    @staticmethod
    def _view(repository, task):
        return ApiTestTaskView(
            id=task.id,
            project_id=task.project_id,
            source_revision_id=task.source_revision_id,
            environment_revision_id=task.environment_revision_id,
            name=task.name,
            state=task.state,
            selected_endpoint_ids=tuple(task.selected_endpoint_ids),
            runnable_baseline_count=repository.runnable_baseline_count(task),
            latest_ai_job_id=task.latest_ai_job_id,
            latest_execution_id=task.latest_execution_id,
            summary=MappingProxyType(copy.deepcopy(dict(task.summary))),
            created_at=task.created_at,
            updated_at=task.updated_at,
        )
