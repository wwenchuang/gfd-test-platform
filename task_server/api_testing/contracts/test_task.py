"""Public immutable view of a lightweight API testing task."""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Optional


@dataclass(frozen=True)
class ApiTestTaskView:
    id: str
    project_id: str
    source_revision_id: str
    environment_revision_id: str
    name: str
    state: str
    selected_endpoint_ids: tuple
    latest_ai_job_id: Optional[str]
    latest_execution_id: Optional[str]
    summary: MappingProxyType
    created_at: object
    updated_at: object
