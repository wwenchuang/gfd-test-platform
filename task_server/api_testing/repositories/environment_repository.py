"""Transaction-scoped persistence for API environment revisions."""

import copy

from sqlalchemy import func, select

from ..models.environment import (
    ApiEnvironment,
    ApiEnvironmentRevision,
    ApiEnvironmentService,
    ApiEnvironmentVariable,
    ApiSecretValue,
)
from ..models.project import ApiProject
from ..models.source import ApiSource, ApiSourceRevision
from .source_repository import audit_fields


class EnvironmentRepository:
    def __init__(self, session):
        self.session = session

    def get_project(self, project_id):
        return self.session.get(ApiProject, project_id)

    def get_source(self, source_id):
        return self.session.get(ApiSource, source_id)

    def get_source_revision(self, revision_id):
        return self.session.get(ApiSourceRevision, revision_id)

    def get_environment(self, environment_id):
        return self.session.get(ApiEnvironment, environment_id)

    def get_environment_for_update(self, environment_id):
        return self.session.scalar(
            select(ApiEnvironment)
            .where(ApiEnvironment.id == environment_id)
            .with_for_update()
        )

    def list_environments(self, project_id, status="active"):
        query = select(ApiEnvironment).where(ApiEnvironment.project_id == project_id)
        if status != "all":
            query = query.where(ApiEnvironment.status == status)
        return tuple(
            self.session.scalars(query.order_by(ApiEnvironment.name, ApiEnvironment.id))
        )

    def find_environment_for_update(self, project_id, source_id, name):
        query = select(ApiEnvironment).where(
            ApiEnvironment.project_id == project_id,
            ApiEnvironment.name == name,
        )
        if source_id is None:
            query = query.where(ApiEnvironment.source_id.is_(None))
        else:
            query = query.where(ApiEnvironment.source_id == source_id)
        return self.session.scalar(query.with_for_update())

    def create_environment(self, project_id, source_id, name, actor_id):
        environment = ApiEnvironment(
            project_id=project_id,
            source_id=source_id,
            name=name,
            **audit_fields(actor_id),
        )
        self.session.add(environment)
        self.session.flush()
        return environment

    def get_revision(self, revision_id):
        return self.session.get(ApiEnvironmentRevision, revision_id)

    def list_revisions(self, environment_id):
        return tuple(
            self.session.scalars(
                select(ApiEnvironmentRevision)
                .where(ApiEnvironmentRevision.environment_id == environment_id)
                .order_by(
                    ApiEnvironmentRevision.revision_number.desc(),
                    ApiEnvironmentRevision.id.desc(),
                )
            )
        )

    def next_revision_number(self, environment_id):
        current = self.session.scalar(
            select(func.max(ApiEnvironmentRevision.revision_number)).where(
                ApiEnvironmentRevision.environment_id == environment_id
            )
        )
        return int(current or 0) + 1

    def create_revision(
        self,
        environment_id,
        source_revision_id,
        revision_number,
        name,
        description,
        default_headers,
        actor_id,
    ):
        revision = ApiEnvironmentRevision(
            environment_id=environment_id,
            source_revision_id=source_revision_id,
            revision_number=revision_number,
            name=name,
            description=description,
            default_headers=copy.deepcopy(dict(default_headers)),
            **audit_fields(actor_id),
        )
        self.session.add(revision)
        self.session.flush()
        return revision

    def add_service(
        self, revision_id, service_name, module_name, base_url, metadata, actor_id
    ):
        record = ApiEnvironmentService(
            revision_id=revision_id,
            service_name=service_name,
            module_name=module_name,
            base_url=base_url,
            metadata_json=copy.deepcopy(dict(metadata)),
            **audit_fields(actor_id),
        )
        self.session.add(record)
        return record

    def add_public_variable(self, revision_id, environment_id, name, value, actor_id):
        record = ApiEnvironmentVariable(
            revision_id=revision_id,
            environment_id=environment_id,
            name=name,
            value=copy.deepcopy(value),
            is_secret=False,
            **audit_fields(actor_id),
        )
        self.session.add(record)
        return record

    def create_secret(
        self, project_id, environment_id, name, ciphertext, fingerprint, actor_id
    ):
        record = ApiSecretValue(
            project_id=project_id,
            environment_id=environment_id,
            name=name,
            ciphertext=ciphertext,
            fingerprint=fingerprint,
            **audit_fields(actor_id),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def add_secret_variable(
        self, revision_id, environment_id, name, secret_value_id, actor_id
    ):
        record = ApiEnvironmentVariable(
            revision_id=revision_id,
            environment_id=environment_id,
            secret_value_id=secret_value_id,
            name=name,
            value=None,
            is_secret=True,
            **audit_fields(actor_id),
        )
        self.session.add(record)
        return record

    def get_services(self, revision_id):
        return tuple(
            self.session.scalars(
                select(ApiEnvironmentService)
                .where(ApiEnvironmentService.revision_id == revision_id)
                .order_by(ApiEnvironmentService.service_name)
            )
        )

    def get_variables(self, revision_id):
        return tuple(
            self.session.scalars(
                select(ApiEnvironmentVariable)
                .where(ApiEnvironmentVariable.revision_id == revision_id)
                .order_by(ApiEnvironmentVariable.name)
            )
        )

    def get_secrets(self, secret_ids):
        identifiers = tuple(secret_ids)
        if not identifiers:
            return {}
        return {
            item.id: item
            for item in self.session.scalars(
                select(ApiSecretValue).where(ApiSecretValue.id.in_(identifiers))
            )
        }

    def flush(self):
        self.session.flush()
