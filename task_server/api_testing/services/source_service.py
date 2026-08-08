"""Manual source preview, deterministic diff, and atomic activation."""

from datetime import datetime, timedelta, timezone

from ..adapters.openapi import normalize_openapi_document
from ..contracts.source import (
    SourceChange,
    SourceEndpointView,
    SourceRefreshPreview,
    SourceRevisionView,
)
from ..repositories.source_repository import SourceRepository


class SourceNotFoundError(LookupError):
    pass


class SourcePreviewNotFoundError(LookupError):
    pass


class SourcePreviewExpiredError(RuntimeError):
    pass


class StaleSourcePreviewError(RuntimeError):
    pass


class SourcePreviewStateError(RuntimeError):
    pass


def _utc_now():
    return datetime.now(timezone.utc)


def _changed_fields(previous, candidate):
    previous_operation = previous.operation
    candidate_operation = candidate.operation
    keys = set(previous_operation) | set(candidate_operation)
    changed = [key for key in keys if previous_operation.get(key) != candidate_operation.get(key)]
    for attribute in ("operation_id", "method", "path", "summary", "tags"):
        if getattr(previous, attribute) != getattr(candidate, attribute):
            changed.append(attribute)
    return tuple(sorted(set(changed)))


def _change_payload(change):
    return {
        "change_type": change.change_type,
        "stable_key": change.stable_key,
        "operation_id": change.operation_id,
        "method": change.method,
        "path": change.path,
        "changed_fields": list(change.changed_fields),
    }


def _change_from_payload(payload):
    return SourceChange(
        change_type=payload["change_type"],
        stable_key=payload["stable_key"],
        operation_id=payload.get("operation_id", ""),
        method=payload["method"],
        path=payload["path"],
        changed_fields=tuple(payload.get("changed_fields") or ()),
    )


def _compute_diff(previous_endpoints, candidate_endpoints):
    previous_by_key = {item.stable_key: item for item in previous_endpoints}
    candidate_by_key = {item.stable_key: item for item in candidate_endpoints}
    changes = []
    for key in sorted(candidate_by_key.keys() - previous_by_key.keys()):
        endpoint = candidate_by_key[key]
        changes.append(
            SourceChange("added", key, endpoint.operation_id, endpoint.method, endpoint.path)
        )
    for key in sorted(previous_by_key.keys() & candidate_by_key.keys()):
        previous = previous_by_key[key]
        candidate = candidate_by_key[key]
        fields = _changed_fields(previous, candidate)
        if fields:
            changes.append(
                SourceChange(
                    "changed",
                    key,
                    candidate.operation_id,
                    candidate.method,
                    candidate.path,
                    fields,
                )
            )
    for key in sorted(previous_by_key.keys() - candidate_by_key.keys()):
        endpoint = previous_by_key[key]
        changes.append(
            SourceChange("removed", key, endpoint.operation_id, endpoint.method, endpoint.path)
        )
    return tuple(changes)


class SourceService:
    def __init__(self, session_factory, preview_ttl=timedelta(hours=24)):
        self._session_factory = session_factory
        self._preview_ttl = preview_ttl

    def preview_refresh(self, project_id, source_id, document, actor_id):
        # Validate before opening a write transaction. Stable keys are rebound after
        # the source identity is known.
        prevalidated = normalize_openapi_document(document, "validation")
        now = _utc_now()
        expires_at = now + self._preview_ttl
        with self._session_factory.begin() as session:
            repository = SourceRepository(session)
            project = repository.get_project(project_id)
            if project is None:
                raise SourceNotFoundError("API testing project was not found")
            source_name = str(prevalidated.document["info"]["title"]).strip()
            if source_id:
                source = repository.get_source_for_update(source_id)
                if source is None or source.project_id != project_id:
                    raise SourceNotFoundError("API source was not found in this project")
            else:
                source = repository.find_source_by_name(project_id, source_name)
                if source is None:
                    source = repository.create_source(
                        project_id, source_name, "openapi", actor_id
                    )
                else:
                    source = repository.get_source_for_update(source.id)

            normalized = normalize_openapi_document(document, source.id)
            active_revision = (
                repository.get_revision(source.active_revision_id)
                if source.active_revision_id
                else None
            )
            previous_endpoints = (
                repository.get_endpoints(active_revision.id) if active_revision else ()
            )
            revision_number = repository.next_revision_number(source.id)
            candidate = repository.create_revision(
                source.id,
                revision_number,
                "candidate",
                normalized,
                {
                    "source_type": "openapi",
                    "openapi_version": normalized.document["openapi"],
                    "title": source_name,
                    "endpoint_count": len(normalized.endpoints),
                },
                expires_at,
                actor_id,
            )
            candidate_endpoints = repository.add_endpoints(
                candidate.id, normalized.endpoints, actor_id
            )
            repository.add_schemas(candidate.id, normalized.schemas, actor_id)
            changes = _compute_diff(previous_endpoints, candidate_endpoints)
            summary = {
                "added": sum(item.change_type == "added" for item in changes),
                "changed": sum(item.change_type == "changed" for item in changes),
                "removed": sum(item.change_type == "removed" for item in changes),
                "document_hash": normalized.document_hash,
            }
            diff = repository.create_diff(
                source.id,
                active_revision.id if active_revision else None,
                candidate.id,
                summary,
                [_change_payload(item) for item in changes],
                expires_at,
                actor_id,
            )
            return SourceRefreshPreview(
                id=diff.id,
                project_id=project_id,
                source_id=source.id,
                previous_revision_id=diff.previous_revision_id,
                candidate_revision_id=candidate.id,
                document_hash=normalized.document_hash,
                added_count=summary["added"],
                changed_count=summary["changed"],
                removed_count=summary["removed"],
                changes=changes,
                expires_at=expires_at,
            )

    def activate_preview(self, preview_id, actor_id):
        now = _utc_now()
        with self._session_factory.begin() as session:
            repository = SourceRepository(session)
            diff = repository.get_diff_for_update(preview_id)
            if diff is None:
                raise SourcePreviewNotFoundError("Source refresh preview was not found")
            if diff.status != "preview":
                raise SourcePreviewStateError("Source refresh preview is not pending")
            if diff.expires_at is None or diff.expires_at <= now:
                raise SourcePreviewExpiredError("Source refresh preview has expired")
            source = repository.get_source_for_update(diff.source_id)
            if source is None:
                raise SourceNotFoundError("API source was not found")
            if source.active_revision_id != diff.previous_revision_id:
                raise StaleSourcePreviewError(
                    "Source active revision changed after this preview was created"
                )
            candidate = repository.get_revision(diff.candidate_revision_id)
            if candidate is None or candidate.source_id != source.id or candidate.status != "candidate":
                raise SourcePreviewStateError("Source candidate revision is not activatable")

            if source.active_revision_id:
                previous = repository.get_revision(source.active_revision_id)
                previous.status = "superseded"
                previous.superseded_at = now
                previous.updated_by = actor_id
            candidate.status = "active"
            candidate.activated_at = now
            candidate.expires_at = None
            candidate.updated_by = actor_id
            source.active_revision_id = candidate.id
            source.updated_by = actor_id
            diff.status = "activated"
            diff.updated_by = actor_id
            session.flush()
            return self._revision_view(repository, candidate, source.project_id)

    def get_revision(self, revision_id):
        with self._session_factory() as session:
            repository = SourceRepository(session)
            revision = repository.get_revision(revision_id)
            if revision is None:
                raise SourceNotFoundError("API source revision was not found")
            source = repository.get_source(revision.source_id)
            return self._revision_view(repository, revision, source.project_id)

    def get_active_revision(self, project_id):
        with self._session_factory() as session:
            repository = SourceRepository(session)
            revision = repository.get_active_revision_for_project(project_id)
            if revision is None:
                return None
            source = repository.get_source(revision.source_id)
            return self._revision_view(repository, revision, source.project_id)

    @staticmethod
    def _revision_view(repository, revision, project_id):
        endpoints = tuple(
            SourceEndpointView(
                id=item.id,
                stable_key=item.stable_key,
                operation_id=item.operation_id,
                method=item.method,
                path=item.path,
                normalized_path=item.normalized_path,
                summary=item.summary,
                tags=tuple(item.tags),
                operation=item.operation,
            )
            for item in repository.get_endpoints(revision.id)
        )
        return SourceRevisionView(
            id=revision.id,
            project_id=project_id,
            source_id=revision.source_id,
            revision_number=revision.revision_number,
            status=revision.status,
            document_hash=revision.document_hash,
            normalized_document=revision.normalized_document,
            import_metadata=revision.import_metadata,
            activated_at=revision.activated_at,
            superseded_at=revision.superseded_at,
            endpoints=endpoints,
        )
