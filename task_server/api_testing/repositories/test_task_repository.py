"""Transaction-scoped persistence for lightweight API testing tasks."""

import copy

from sqlalchemy import select

from ..models.case import ApiAiJob
from ..models.environment import ApiEnvironment, ApiEnvironmentRevision
from ..models.execution import ApiExecution
from ..models.project import ApiProject
from ..models.source import ApiSource, ApiSourceEndpoint, ApiSourceRevision
from ..models.test_task import ApiTestTask
from .source_repository import audit_fields


TERMINAL_TASK_STATES = ("completed", "cancelled")


class TestTaskRepository:
    def __init__(self, session):
        self.session = session

    def get_project(self, record_id):
        return self.session.get(ApiProject, record_id)

    def get_source_revision(self, record_id):
        return self.session.get(ApiSourceRevision, record_id)

    def get_source(self, record_id):
        return self.session.get(ApiSource, record_id)

    def get_environment_revision(self, record_id):
        return self.session.get(ApiEnvironmentRevision, record_id)

    def get_environment(self, record_id):
        return self.session.get(ApiEnvironment, record_id)

    def get_endpoints(self, record_ids):
        identifiers = tuple(dict.fromkeys(record_ids))
        if not identifiers:
            return {}
        return {
            item.id: item
            for item in self.session.scalars(
                select(ApiSourceEndpoint).where(ApiSourceEndpoint.id.in_(identifiers))
            )
        }

    def get_ai_job(self, record_id):
        return self.session.get(ApiAiJob, record_id)

    def get_execution(self, record_id):
        return self.session.get(ApiExecution, record_id)

    def get_task(self, record_id, *, for_update=False):
        query = select(ApiTestTask).where(ApiTestTask.id == record_id)
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def get_by_ai_job(self, job_id, *, for_update=False):
        query = select(ApiTestTask).where(ApiTestTask.latest_ai_job_id == job_id)
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def get_by_execution(self, execution_id, *, for_update=False):
        query = select(ApiTestTask).where(
            ApiTestTask.latest_execution_id == execution_id
        )
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def get_active(self, project_id, owner_id, *, for_update=False):
        query = (
            select(ApiTestTask)
            .where(
                ApiTestTask.project_id == project_id,
                ApiTestTask.owner_id == owner_id,
                ApiTestTask.state.notin_(TERMINAL_TASK_STATES),
            )
            .order_by(ApiTestTask.updated_at.desc(), ApiTestTask.id.desc())
            .limit(1)
        )
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def create(self, payload, actor_id):
        record = ApiTestTask(
            project_id=payload["project_id"],
            source_revision_id=payload["source_revision_id"],
            environment_revision_id=payload["environment_revision_id"],
            name=payload["name"],
            state="draft",
            selected_endpoint_ids=copy.deepcopy(payload["selected_endpoint_ids"]),
            summary={},
            **audit_fields(actor_id),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def flush(self):
        self.session.flush()
