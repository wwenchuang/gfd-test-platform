"""Independent, opt-in scheduled performance executions."""
from typing import Optional
from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, PrimaryRecord

class ApiLoadSchedule(PrimaryRecord, Base):
    __tablename__ = 'api_load_schedules'
    project_id: Mapped[str] = mapped_column(ForeignKey('api_projects.id', ondelete='RESTRICT'))
    environment_revision_id: Mapped[str] = mapped_column(ForeignKey('api_environment_revisions.id', ondelete='RESTRICT'))
    name: Mapped[str] = mapped_column(String(160))
    source_run_id: Mapped[str] = mapped_column(String(36))
    snapshot: Mapped[dict] = mapped_column(JSONB)
    source_labels: Mapped[dict] = mapped_column(JSONB, default=dict)
    daily_time: Mapped[str] = mapped_column(String(5))
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    notification_enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default='false')
    next_run_at: Mapped[object] = mapped_column(DateTime(timezone=True), index=True)
    claimed_at: Mapped[Optional[object]] = mapped_column(DateTime(timezone=True))
    active_run_id: Mapped[Optional[str]] = mapped_column(String(36))
    last_run_id: Mapped[Optional[str]] = mapped_column(String(36))
    last_status: Mapped[str] = mapped_column(String(40), default='idle', server_default='idle')
    last_message: Mapped[str] = mapped_column(Text, default='', server_default='')

class ApiLoadScheduleOccurrence(PrimaryRecord, Base):
    __tablename__ = 'api_load_schedule_occurrences'
    schedule_id: Mapped[str] = mapped_column(ForeignKey('api_load_schedules.id', ondelete='CASCADE'), index=True)
    run_id: Mapped[str] = mapped_column(String(36), unique=True)
    notification_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    finalize_state: Mapped[str] = mapped_column(String(32), default='pending', server_default='pending')
    finalize_claimed_at: Mapped[Optional[object]] = mapped_column(DateTime(timezone=True))
    notification_status: Mapped[str] = mapped_column(String(32), default='pending')
