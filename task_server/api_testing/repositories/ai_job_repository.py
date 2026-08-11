"""Transaction-scoped persistence for AI-assisted API case jobs."""

import copy

from sqlalchemy import select

from ..models.case import ApiAiJob, ApiAiJobBatch, ApiCaseVersion
from ..models.environment import (
    ApiEnvironment,
    ApiEnvironmentRevision,
    ApiEnvironmentService,
    ApiEnvironmentVariable,
)
from ..models.source import ApiSource, ApiSourceEndpoint, ApiSourceRevision
from .source_repository import audit_fields


class AiJobRepository:
    def __init__(self, session):
        self.session = session

    def get_endpoints(self, endpoint_ids):
        identifiers = tuple(dict.fromkeys(endpoint_ids))
        if not identifiers:
            return {}
        return {
            item.id: item
            for item in self.session.scalars(
                select(ApiSourceEndpoint).where(ApiSourceEndpoint.id.in_(identifiers))
            )
        }

    def get_source_revision(self, revision_id):
        return self.session.get(ApiSourceRevision, revision_id)

    def get_source(self, source_id):
        return self.session.get(ApiSource, source_id)

    def get_environment_revision(self, revision_id):
        return self.session.get(ApiEnvironmentRevision, revision_id)

    def get_environment(self, environment_id):
        return self.session.get(ApiEnvironment, environment_id)

    def get_environment_variables(self, revision_id):
        return tuple(
            self.session.scalars(
                select(ApiEnvironmentVariable)
                .where(ApiEnvironmentVariable.revision_id == revision_id)
                .order_by(ApiEnvironmentVariable.name)
            )
        )

    def get_environment_services(self, revision_id):
        return tuple(
            self.session.scalars(
                select(ApiEnvironmentService)
                .where(ApiEnvironmentService.revision_id == revision_id)
                .order_by(ApiEnvironmentService.service_name)
            )
        )

    def create_job(
        self,
        *,
        project_id,
        environment_revision_id,
        endpoint_ids,
        requested_provider_id,
        requested_model,
        intent,
        actor_id,
    ):
        job = ApiAiJob(
            project_id=project_id,
            environment_revision_id=environment_revision_id,
            state="queued",
            endpoint_ids=list(endpoint_ids),
            requested_model=requested_model,
            actual_model="",
            summary={
                "requested_provider_id": requested_provider_id,
                "intent": intent,
                "generated_drafts": 0,
                "invalid_candidates": 0,
                "gateway_failures": 0,
            },
            **audit_fields(actor_id),
        )
        self.session.add(job)
        self.session.flush()
        return job

    def create_batch(
        self,
        *,
        job_id,
        sequence,
        endpoint_ids,
        requested_provider_id,
        requested_model,
        actor_id,
    ):
        batch = ApiAiJobBatch(
            job_id=job_id,
            sequence=sequence,
            state="queued",
            endpoint_ids=list(endpoint_ids),
            requested_model=requested_model,
            actual_model="",
            result={
                "requested_provider_id": requested_provider_id,
                "draft_version_ids": [],
                "validation_errors": [],
            },
            error={},
            **audit_fields(actor_id),
        )
        self.session.add(batch)
        self.session.flush()
        return batch

    def get_job(self, job_id):
        return self.session.get(ApiAiJob, job_id)

    def latest_incomplete_job(self, project_id):
        return self.session.scalar(
            select(ApiAiJob)
            .where(
                ApiAiJob.project_id == project_id,
                ApiAiJob.state.in_(("queued", "running")),
            )
            .order_by(ApiAiJob.created_at.desc(), ApiAiJob.id.desc())
            .limit(1)
        )

    def get_job_for_update(self, job_id):
        return self.session.scalar(
            select(ApiAiJob).where(ApiAiJob.id == job_id).with_for_update()
        )

    def get_batch_for_update(self, batch_id):
        return self.session.scalar(
            select(ApiAiJobBatch)
            .where(ApiAiJobBatch.id == batch_id)
            .with_for_update()
        )

    def list_batches(self, job_id):
        return tuple(
            self.session.scalars(
                select(ApiAiJobBatch)
                .where(ApiAiJobBatch.job_id == job_id)
                .order_by(ApiAiJobBatch.sequence)
            )
        )

    def get_case_versions(self, version_ids):
        identifiers = tuple(dict.fromkeys(version_ids))
        if not identifiers:
            return {}
        return {
            item.id: item
            for item in self.session.scalars(
                select(ApiCaseVersion).where(ApiCaseVersion.id.in_(identifiers))
            )
        }

    def update_batch(self, batch, *, state, actual_model="", result=None, error=None, actor_id):
        batch.state = state
        batch.actual_model = actual_model
        if result is not None:
            batch.result = copy.deepcopy(result)
        if error is not None:
            batch.error = copy.deepcopy(error)
        batch.updated_by = actor_id
        self.session.flush()

    def update_job(self, job, *, state, actual_model="", summary=None, actor_id):
        job.state = state
        job.actual_model = actual_model
        if summary is not None:
            job.summary = copy.deepcopy(summary)
        job.updated_by = actor_id
        self.session.flush()
