"""Project-scoped scheduled API regression jobs."""

from typing import Optional

from sqlalchemy import Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, PrimaryRecord


class ApiScheduledJob(PrimaryRecord, Base):
    __tablename__ = "api_scheduled_jobs"
    __table_args__ = (
        Index("ix_api_scheduled_jobs_project_enabled", "project_id", "enabled"),
    )

    project_id: Mapped[str] = mapped_column(ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False)
    source_revision_id: Mapped[Optional[str]] = mapped_column(ForeignKey("api_source_revisions.id", ondelete="SET NULL"), nullable=True)
    environment_revision_id: Mapped[Optional[str]] = mapped_column(ForeignKey("api_environment_revisions.id", ondelete="SET NULL"), nullable=True)
    environment_id: Mapped[Optional[str]] = mapped_column(ForeignKey("api_environments.id", ondelete="SET NULL"), nullable=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    schedule_type: Mapped[str] = mapped_column(String(32), nullable=False)
    cron_expression: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    environment_strategy: Mapped[str] = mapped_column(String(32), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    notify_feishu: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1800")
    summary: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class ApiScheduledJobTarget(PrimaryRecord, Base):
    __tablename__ = "api_scheduled_job_targets"
    __table_args__ = (
        UniqueConstraint("job_id", "sequence"),
        Index("ix_api_scheduled_job_targets_job", "job_id"),
    )

    job_id: Mapped[str] = mapped_column(ForeignKey("api_scheduled_jobs.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    target_type: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(200), nullable=False)
    group_name: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")


class ApiScheduledJobRun(PrimaryRecord, Base):
    __tablename__ = "api_scheduled_job_runs"
    __table_args__ = (
        Index("ix_api_scheduled_job_runs_job_created", "job_id", "created_at"),
    )

    job_id: Mapped[str] = mapped_column(ForeignKey("api_scheduled_jobs.id", ondelete="CASCADE"), nullable=False)
    execution_id: Mapped[Optional[str]] = mapped_column(ForeignKey("api_executions.id", ondelete="SET NULL"), nullable=True)
    trigger_type: Mapped[str] = mapped_column(String(32), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="queued")
