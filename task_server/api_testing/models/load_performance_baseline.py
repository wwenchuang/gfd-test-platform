"""Immutable manually adopted performance references; no scheduler ownership."""
from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, PrimaryRecord


class ApiLoadPerformanceBaseline(PrimaryRecord, Base):
    __tablename__ = 'api_load_performance_baselines'
    __table_args__ = (
        Index('ix_load_performance_baseline_scope', 'project_id', 'scenario_id', 'environment_id', 'created_at'),
        Index('uq_load_performance_baseline_active', 'project_id', 'scenario_id', 'environment_id', unique=True,
              postgresql_where=text("status = 'active'")),
    )
    project_id: Mapped[str] = mapped_column(ForeignKey('api_projects.id', ondelete='RESTRICT'), nullable=False)
    scenario_id: Mapped[str] = mapped_column(ForeignKey('api_load_scenarios.id', ondelete='RESTRICT'), nullable=False)
    environment_id: Mapped[str] = mapped_column(ForeignKey('api_environments.id', ondelete='RESTRICT'), nullable=False)
    environment_revision_id: Mapped[str] = mapped_column(ForeignKey('api_environment_revisions.id', ondelete='RESTRICT'), nullable=False)
    # The frozen evidence keeps source identity even if the execution is later removed.
    source_run_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    adoption_reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default='active')
    evidence_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    regression_policy: Mapped[dict] = mapped_column(JSONB, nullable=False)
    adoption_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
