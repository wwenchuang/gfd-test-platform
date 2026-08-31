"""Transaction-scoped persistence for versioned API sources."""

import copy

from sqlalchemy import func, select
from sqlalchemy.orm import defer

from .. import access

from ..models.project import ApiProject
from ..models.source import (
    ApiSource,
    ApiSourceDiff,
    ApiSourceEndpoint,
    ApiSourceRevision,
    ApiSourceSchema,
)


def audit_fields(actor_id):
    return {
        "owner_id": actor_id,
        "created_by": actor_id,
        "updated_by": actor_id,
    }


class SourceRepository:
    def __init__(self, session):
        self.session = session

    def get_project(self, project_id):
        return self.session.get(ApiProject, project_id)

    def get_source(self, source_id):
        return self.session.get(ApiSource, source_id)

    def get_source_for_update(self, source_id):
        return self.session.scalar(
            select(ApiSource).where(ApiSource.id == source_id).with_for_update()
        )

    def find_source_by_name(self, project_id, name):
        return self.session.scalar(
            select(ApiSource).where(
                ApiSource.project_id == project_id,
                ApiSource.name == name,
            )
        )

    def create_source(self, project_id, name, source_type, actor_id):
        source = ApiSource(
            project_id=project_id,
            name=name,
            source_type=source_type,
            connection_config={},
            **access.inherited_audit(self.session, actor_id, ApiProject, project_id),
        )
        self.session.add(source)
        self.session.flush()
        return source

    def next_revision_number(self, source_id):
        current = self.session.scalar(
            select(func.max(ApiSourceRevision.revision_number)).where(
                ApiSourceRevision.source_id == source_id
            )
        )
        return int(current or 0) + 1

    def create_revision(
        self,
        source_id,
        revision_number,
        status,
        normalized,
        import_metadata,
        expires_at,
        actor_id,
    ):
        revision = ApiSourceRevision(
            source_id=source_id,
            revision_number=revision_number,
            status=status,
            document_hash=normalized.document_hash,
            normalized_document=dict(normalized.document),
            import_metadata=dict(import_metadata),
            expires_at=expires_at,
            **access.inherited_audit(self.session, actor_id, ApiSource, source_id),
        )
        self.session.add(revision)
        self.session.flush()
        return revision

    def add_endpoints(self, revision_id, endpoints, actor_id):
        records = []
        for endpoint in endpoints:
            record = ApiSourceEndpoint(
                revision_id=revision_id,
                stable_key=endpoint.stable_key,
                operation_id=endpoint.operation_id,
                method=endpoint.method,
                path=endpoint.path,
                normalized_path=endpoint.normalized_path,
                summary=endpoint.summary,
                tags=list(endpoint.tags),
                operation=dict(endpoint.operation),
                **access.inherited_audit(self.session, actor_id, ApiSourceRevision, revision_id),
            )
            self.session.add(record)
            records.append(record)
        self.session.flush()
        return records

    def add_schemas(self, revision_id, schemas, actor_id):
        for schema_key, schema in schemas.items():
            self.session.add(
                ApiSourceSchema(
                    revision_id=revision_id,
                    schema_key=schema_key,
                    schema=copy.deepcopy(schema),
                    **access.inherited_audit(self.session, actor_id, ApiSourceRevision, revision_id),
                )
            )
        self.session.flush()

    def create_diff(
        self,
        source_id,
        previous_revision_id,
        candidate_revision_id,
        summary,
        changes,
        expires_at,
        actor_id,
    ):
        diff = ApiSourceDiff(
            source_id=source_id,
            previous_revision_id=previous_revision_id,
            candidate_revision_id=candidate_revision_id,
            summary=summary,
            changes=changes,
            expires_at=expires_at,
            **access.inherited_audit(self.session, actor_id, ApiSource, source_id),
        )
        self.session.add(diff)
        self.session.flush()
        return diff

    def get_diff_for_update(self, diff_id):
        return self.session.scalar(
            select(ApiSourceDiff).where(ApiSourceDiff.id == diff_id).with_for_update()
        )

    def get_revision(self, revision_id):
        return self.session.get(ApiSourceRevision, revision_id)

    def get_revision_metadata(self, revision_id):
        return self.session.scalar(
            select(ApiSourceRevision)
            .options(
                defer(ApiSourceRevision.normalized_document, raiseload=True),
                defer(ApiSourceRevision.import_metadata, raiseload=True),
            )
            .where(ApiSourceRevision.id == revision_id)
        )

    def get_endpoints(self, revision_id):
        return tuple(
            self.session.scalars(
                select(ApiSourceEndpoint)
                .where(ApiSourceEndpoint.revision_id == revision_id)
                .order_by(ApiSourceEndpoint.normalized_path, ApiSourceEndpoint.method)
            )
        )

    def get_active_revision(self, project_id, source_id):
        return self.session.scalar(
            select(ApiSourceRevision)
            .join(
                ApiSource,
                (ApiSource.id == ApiSourceRevision.source_id)
                & (ApiSource.active_revision_id == ApiSourceRevision.id),
            )
            .where(
                ApiSource.project_id == project_id,
                ApiSource.id == source_id,
            )
        )
