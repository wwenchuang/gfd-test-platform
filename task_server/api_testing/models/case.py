"""Versioned API case, baseline, and AI generation models."""

from typing import Optional

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, PrimaryRecord


class ApiCase(PrimaryRecord, Base):
    __tablename__ = "api_cases"
    __table_args__ = (Index("ix_api_cases_project_status", "project_id", "status"),)

    project_id: Mapped[str] = mapped_column(ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("api_source_endpoints.id", ondelete="RESTRICT"), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    active_version_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey(
            "api_case_versions.id",
            name="fk_api_cases_active_version_id_api_case_versions",
            use_alter=True,
            ondelete="SET NULL",
        ),
        nullable=True,
    )


class ApiCaseVersion(PrimaryRecord, Base):
    __tablename__ = "api_case_versions"
    __table_args__ = (
        UniqueConstraint("case_id", "version_number"),
        Index("ix_api_case_versions_case_number", "case_id", "version_number"),
    )

    case_id: Mapped[str] = mapped_column(ForeignKey("api_cases.id", ondelete="CASCADE"), nullable=False)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("api_source_endpoints.id", ondelete="RESTRICT"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), nullable=False, server_default="P1")
    request_template: Mapped[dict] = mapped_column(JSONB, nullable=False)
    validation_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    dependency_spec: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    processing_spec: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")


class ApiCaseDataRow(PrimaryRecord, Base):
    __tablename__ = "api_case_data_rows"
    __table_args__ = (UniqueConstraint("case_version_id", "name"),)

    case_version_id: Mapped[str] = mapped_column(ForeignKey("api_case_versions.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    values: Mapped[dict] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, server_default="true")
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")


class ApiCaseAssertion(PrimaryRecord, Base):
    __tablename__ = "api_case_assertions"
    __table_args__ = (UniqueConstraint("case_version_id", "sequence"),)

    case_version_id: Mapped[str] = mapped_column(ForeignKey("api_case_versions.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    assertion_type: Mapped[str] = mapped_column(String(64), nullable=False)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    enabled: Mapped[bool] = mapped_column(nullable=False, server_default="true")


class ApiCaseExtraction(PrimaryRecord, Base):
    __tablename__ = "api_case_extractions"
    __table_args__ = (UniqueConstraint("case_version_id", "target_name"),)

    case_version_id: Mapped[str] = mapped_column(ForeignKey("api_case_versions.id", ondelete="CASCADE"), nullable=False)
    target_name: Mapped[str] = mapped_column(String(200), nullable=False)
    extraction_type: Mapped[str] = mapped_column(String(64), nullable=False)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)


class ApiCaseScript(PrimaryRecord, Base):
    __tablename__ = "api_case_scripts"
    __table_args__ = (UniqueConstraint("case_version_id", "phase", "sequence"),)

    case_version_id: Mapped[str] = mapped_column(ForeignKey("api_case_versions.id", ondelete="CASCADE"), nullable=False)
    phase: Mapped[str] = mapped_column(String(32), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False, server_default="declarative")
    source: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    config: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")


class ApiBaseline(PrimaryRecord, Base):
    __tablename__ = "api_baselines"
    __table_args__ = (Index("ix_api_baselines_project_case", "project_id", "case_id"),)

    project_id: Mapped[str] = mapped_column(ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False)
    case_id: Mapped[str] = mapped_column(ForeignKey("api_cases.id", ondelete="CASCADE"), nullable=False)
    case_version_id: Mapped[str] = mapped_column(ForeignKey("api_case_versions.id", ondelete="RESTRICT"), nullable=False)
    environment_revision_id: Mapped[str] = mapped_column(ForeignKey("api_environment_revisions.id", ondelete="RESTRICT"), nullable=False)
    debug_execution_case_id: Mapped[str] = mapped_column(ForeignKey("api_execution_cases.id", ondelete="RESTRICT"), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")
    adoption_reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class ApiAiJob(PrimaryRecord, Base):
    __tablename__ = "api_ai_jobs"
    __table_args__ = (Index("ix_api_ai_jobs_project_state_created", "project_id", "state", "created_at"),)

    project_id: Mapped[str] = mapped_column(ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False)
    environment_revision_id: Mapped[str] = mapped_column(ForeignKey("api_environment_revisions.id", ondelete="RESTRICT"), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="queued")
    endpoint_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    requested_model: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    actual_model: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")


class ApiAiJobBatch(PrimaryRecord, Base):
    __tablename__ = "api_ai_job_batches"
    __table_args__ = (UniqueConstraint("job_id", "sequence"),)

    job_id: Mapped[str] = mapped_column(ForeignKey("api_ai_jobs.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="queued")
    endpoint_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    requested_model: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    actual_model: Mapped[str] = mapped_column(String(200), nullable=False, server_default="")
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    error: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
