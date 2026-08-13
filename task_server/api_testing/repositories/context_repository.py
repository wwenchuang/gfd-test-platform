"""Read-only owner-scoped display metadata for the API workbench context."""

from sqlalchemy import func, or_, select

from ..models.environment import ApiEnvironment, ApiEnvironmentRevision
from ..models.project import ApiProject, ApiWorkspace
from ..models.source import ApiSource, ApiSourceEndpoint, ApiSourceRevision


class ContextRepository:
    def __init__(self, session):
        self.session = session

    def list_options(self, actor_id):
        workspace = self.session.scalar(
            select(ApiWorkspace).where(ApiWorkspace.owner_id == actor_id)
        )
        saved_source_revision_id = workspace.source_revision_id if workspace else None
        saved_environment_revision_id = (
            workspace.environment_revision_id if workspace else None
        )
        projects = self.session.execute(
            select(
                ApiProject.id,
                ApiProject.name,
                ApiProject.slug,
                ApiProject.description,
                ApiProject.status,
                ApiProject.created_at,
                ApiProject.updated_at,
            )
            .where(ApiProject.owner_id == actor_id, ApiProject.status == "active")
            .order_by(ApiProject.name, ApiProject.id)
        ).all()
        source_revisions = self.session.execute(
            select(
                ApiSourceRevision.id,
                ApiSource.id,
                ApiSource.project_id,
                ApiSource.name,
                ApiSource.source_type,
                ApiSource.status,
                ApiSourceRevision.revision_number,
                ApiSourceRevision.created_at,
                ApiSourceRevision.activated_at,
                func.count(ApiSourceEndpoint.id),
            )
            .join(
                ApiSource,
                ApiSource.id == ApiSourceRevision.source_id,
            )
            .join(ApiProject, ApiProject.id == ApiSource.project_id)
            .outerjoin(
                ApiSourceEndpoint,
                ApiSourceEndpoint.revision_id == ApiSourceRevision.id,
            )
            .where(
                ApiProject.owner_id == actor_id,
                ApiProject.status == "active",
                ApiSource.status == "active",
                or_(
                    ApiSource.active_revision_id == ApiSourceRevision.id,
                    ApiSourceRevision.id == saved_source_revision_id,
                ),
            )
            .group_by(
                ApiSourceRevision.id,
                ApiSource.id,
                ApiSource.project_id,
                ApiSource.name,
                ApiSource.source_type,
                ApiSource.status,
                ApiSourceRevision.revision_number,
                ApiSourceRevision.created_at,
                ApiSourceRevision.activated_at,
                ApiProject.name,
            )
            .order_by(
                ApiProject.name,
                ApiSource.name,
                ApiSourceRevision.revision_number,
                ApiSourceRevision.id,
            )
        ).all()
        environment_revisions = self.session.execute(
            select(
                ApiEnvironmentRevision.id,
                ApiEnvironment.id,
                ApiEnvironment.project_id,
                ApiEnvironmentRevision.name,
                ApiEnvironmentRevision.revision_number,
                ApiEnvironmentRevision.status,
                ApiEnvironmentRevision.created_at,
            )
            .join(
                ApiEnvironment,
                ApiEnvironment.id == ApiEnvironmentRevision.environment_id,
            )
            .join(ApiProject, ApiProject.id == ApiEnvironment.project_id)
            .where(
                ApiProject.owner_id == actor_id,
                ApiProject.status == "active",
                ApiEnvironment.status == "active",
                or_(
                    ApiEnvironment.active_revision_id
                    == ApiEnvironmentRevision.id,
                    ApiEnvironmentRevision.id == saved_environment_revision_id,
                ),
            )
            .order_by(
                ApiProject.name,
                ApiEnvironmentRevision.name,
                ApiEnvironmentRevision.revision_number,
                ApiEnvironmentRevision.id,
            )
        ).all()
        return {
            "projects": [
                {
                    "id": project_id,
                    "name": name,
                    "slug": slug,
                    "description": description,
                    "status": status,
                    "created_at": created_at,
                    "updated_at": updated_at,
                }
                for (
                    project_id,
                    name,
                    slug,
                    description,
                    status,
                    created_at,
                    updated_at,
                ) in projects
            ],
            "source_revisions": [
                {
                    "id": revision_id,
                    "source_id": source_id,
                    "project_id": project_id,
                    "name": name,
                    "source_type": source_type,
                    "source_status": source_status,
                    "revision_number": revision_number,
                    "created_at": created_at,
                    "activated_at": activated_at,
                    "endpoint_count": int(endpoint_count),
                }
                for (
                    revision_id,
                    source_id,
                    project_id,
                    name,
                    source_type,
                    source_status,
                    revision_number,
                    created_at,
                    activated_at,
                    endpoint_count,
                ) in source_revisions
            ],
            "environment_revisions": [
                {
                    "id": revision_id,
                    "environment_id": environment_id,
                    "project_id": project_id,
                    "name": name,
                    "revision": revision_number,
                    "status": status,
                    "created_at": created_at,
                }
                for (
                    revision_id,
                    environment_id,
                    project_id,
                    name,
                    revision_number,
                    status,
                    created_at,
                ) in environment_revisions
            ],
        }
