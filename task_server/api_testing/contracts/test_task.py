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
    runnable_baseline_count: int
    runnable_endpoint_count: int
    latest_ai_job_id: Optional[str]
    latest_execution_id: Optional[str]
    latest_execution_state: Optional[str]
    latest_execution_summary: MappingProxyType
    latest_execution_at: Optional[object]
    summary: MappingProxyType
    created_at: object
    updated_at: object
