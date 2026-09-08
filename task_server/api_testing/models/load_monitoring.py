"""Environment monitoring catalog and immutable, secret-free revisions."""
from typing import Optional
from sqlalchemy import ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, PrimaryRecord


class ApiLoadMonitoringService(PrimaryRecord, Base):
    __tablename__ = 'api_load_monitoring_services'
    environment_id: Mapped[str] = mapped_column(ForeignKey('api_environments.id', ondelete='RESTRICT'), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey('api_projects.id', ondelete='RESTRICT'), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default='active')
    active_revision_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    last_check: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default='{}')


class ApiLoadMonitoringRevision(PrimaryRecord, Base):
    __tablename__ = 'api_load_monitoring_revisions'
    __table_args__ = (UniqueConstraint('service_id', 'id'),)
    service_id: Mapped[str] = mapped_column(ForeignKey('api_load_monitoring_services.id', ondelete='RESTRICT'), nullable=False, index=True)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    authorized_host: Mapped[str] = mapped_column(String(253), nullable=False)
    authorized_by: Mapped[str] = mapped_column(String(128), nullable=False)
    secret_value_id: Mapped[Optional[str]] = mapped_column(ForeignKey('api_secret_values.id', ondelete='RESTRICT'), nullable=True)
