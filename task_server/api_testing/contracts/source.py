"""Immutable public contracts for versioned API source imports."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Mapping, Optional, Tuple


@dataclass(frozen=True)
class NormalizedEndpoint:
    stable_key: str
    operation_id: str
    method: str
    path: str
    normalized_path: str
    summary: str
    tags: Tuple[str, ...]
    operation: Mapping[str, Any]


@dataclass(frozen=True)
class NormalizedSourceDocument:
    document: Mapping[str, Any]
    document_hash: str
    endpoints: Tuple[NormalizedEndpoint, ...]
    schemas: Mapping[str, Any]


@dataclass(frozen=True)
class SourceChange:
    change_type: str
    stable_key: str
    operation_id: str
    method: str
    path: str
    changed_fields: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceRefreshPreview:
    id: str
    project_id: str
    source_id: str
    previous_revision_id: Optional[str]
    candidate_revision_id: str
    document_hash: str
    added_count: int
    changed_count: int
    removed_count: int
    changes: Tuple[SourceChange, ...]
    expires_at: datetime


@dataclass(frozen=True)
class SourceEndpointView:
    id: str
    stable_key: str
    operation_id: str
    method: str
    path: str
    normalized_path: str
    summary: str
    tags: Tuple[str, ...]
    operation: Mapping[str, Any]


@dataclass(frozen=True)
class SourceRevisionView:
    id: str
    project_id: str
    source_id: str
    revision_number: int
    status: str
    document_hash: str
    normalized_document: Mapping[str, Any]
    import_metadata: Mapping[str, Any]
    activated_at: Optional[datetime]
    superseded_at: Optional[datetime]
    endpoints: Tuple[SourceEndpointView, ...]
