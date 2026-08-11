"""Durable workflow pointer for one API testing task."""

from typing import Optional

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, PrimaryRecord


class ApiTestTask(PrimaryRecord, Base):
    __tablename__ = "api_test_tasks"
    __table_args__ = (
        Index(
            "ix_api_test_tasks_owner_project_state_updated",
            "owner_id",
            "project_id",
            "state",
            "updated_at",
        ),
    )

    project_id: Mapped[str] = mapped_column(
        ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False
    )
    source_revision_id: Mapped[str] = mapped_column(
        ForeignKey("api_source_revisions.id", ondelete="RESTRICT"), nullable=False
    )
    environment_revision_id: Mapped[str] = mapped_column(
        ForeignKey("api_environment_revisions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="draft"
    )
    selected_endpoint_ids: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    latest_ai_job_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("api_ai_jobs.id", ondelete="SET NULL"), nullable=True
    )
    latest_execution_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey("api_executions.id", ondelete="SET NULL"), nullable=True
    )
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
