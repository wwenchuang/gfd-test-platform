"""Durable API execution and report evidence models."""

from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, PrimaryRecord


class ApiExecution(PrimaryRecord, Base):
    __tablename__ = "api_executions"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key"),
        Index("ix_api_executions_project_state_created", "project_id", "state", "created_at"),
    )

    project_id: Mapped[str] = mapped_column(ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False)
    source_revision_id: Mapped[str] = mapped_column(ForeignKey("api_source_revisions.id", ondelete="RESTRICT"), nullable=False)
    environment_revision_id: Mapped[str] = mapped_column(ForeignKey("api_environment_revisions.id", ondelete="RESTRICT"), nullable=False)
    execution_type: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="QUEUED")
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)
    requested_case_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    request_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    cancellation_requested_at: Mapped[Optional[object]] = mapped_column(DateTime(timezone=True), nullable=True)
    started_at: Mapped[Optional[object]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[object]] = mapped_column(DateTime(timezone=True), nullable=True)


class ApiExecutionCase(PrimaryRecord, Base):
    __tablename__ = "api_execution_cases"
    __table_args__ = (
        UniqueConstraint("execution_id", "ordinal"),
        Index(
            "ix_api_execution_cases_version_created",
            "case_version_id",
            "created_at",
        ),
    )

    execution_id: Mapped[str] = mapped_column(ForeignKey("api_executions.id", ondelete="CASCADE"), nullable=False)
    case_version_id: Mapped[str] = mapped_column(ForeignKey("api_case_versions.id", ondelete="RESTRICT"), nullable=False)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("api_source_endpoints.id", ondelete="RESTRICT"), nullable=False)
    environment_revision_id: Mapped[str] = mapped_column(ForeignKey("api_environment_revisions.id", ondelete="RESTRICT"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="QUEUED")
    failure_category: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    sanitized_result: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")


class ApiExecutionAttempt(PrimaryRecord, Base):
    __tablename__ = "api_execution_attempts"
    __table_args__ = (UniqueConstraint("execution_case_id", "attempt_number"),)

    execution_case_id: Mapped[str] = mapped_column(ForeignKey("api_execution_cases.id", ondelete="CASCADE"), nullable=False)
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    request: Mapped[dict] = mapped_column(JSONB, nullable=False)
    response: Mapped[dict] = mapped_column(JSONB, nullable=False)
    assertion_results: Mapped[list] = mapped_column(JSONB, nullable=False)
    timing: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    error_message: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class ApiExecutionEvent(PrimaryRecord, Base):
    __tablename__ = "api_execution_events"
    __table_args__ = (
        UniqueConstraint("execution_id", "sequence"),
        Index("ix_api_execution_events_execution_sequence", "execution_id", "sequence"),
    )

    execution_id: Mapped[str] = mapped_column(ForeignKey("api_executions.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)


class ApiExecutionArtifact(PrimaryRecord, Base):
    __tablename__ = "api_execution_artifacts"

    execution_id: Mapped[str] = mapped_column(ForeignKey("api_executions.id", ondelete="CASCADE"), nullable=False)
    execution_case_id: Mapped[Optional[str]] = mapped_column(ForeignKey("api_execution_cases.id", ondelete="CASCADE"), nullable=True)
    artifact_type: Mapped[str] = mapped_column(String(64), nullable=False)
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    metadata_json: Mapped[dict] = mapped_column("metadata", JSONB, nullable=False, server_default="{}")


class ApiFailureAnalysis(PrimaryRecord, Base):
    __tablename__ = "api_failure_analyses"
    __table_args__ = (Index("ix_api_failure_analyses_execution_case", "execution_case_id", "created_at"),)

    execution_case_id: Mapped[str] = mapped_column(ForeignKey("api_execution_cases.id", ondelete="CASCADE"), nullable=False)
    attempt_id: Mapped[Optional[str]] = mapped_column(ForeignKey("api_execution_attempts.id", ondelete="SET NULL"), nullable=True)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    analyzer: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    analysis: Mapped[dict] = mapped_column(JSONB, nullable=False)
