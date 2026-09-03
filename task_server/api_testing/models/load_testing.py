"""Durable records for distributed API performance testing."""

from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, PrimaryRecord


class ApiLoadAgent(PrimaryRecord, Base):
    __tablename__ = "api_load_agents"
    __table_args__ = (
        Index("ix_api_load_agents_status_tier", "status", "scheduling_tier"),
        UniqueConstraint("name"),
    )

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="offline")
    scheduling_tier: Mapped[str] = mapped_column(String(32), nullable=False, server_default="normal")
    node_group: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    labels: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    agent_version: Mapped[str] = mapped_column(String(80), nullable=False, server_default="")
    k6_version: Mapped[str] = mapped_column(String(80), nullable=False, server_default="")
    credential_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hard_limits: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    soft_limits: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    current_usage: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    capabilities: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    health: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    egress_ip: Mapped[str] = mapped_column(String(64), nullable=False, server_default="")
    last_heartbeat_at: Mapped[Optional[object]] = mapped_column(DateTime(timezone=True), nullable=True)
    offline_reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class ApiLoadAgentEnrollment(PrimaryRecord, Base):
    __tablename__ = "api_load_agent_enrollments"
    __table_args__ = (Index("ix_api_load_agent_enrollments_expiry", "expires_at", "used_at"),)

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    expires_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[Optional[object]] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[Optional[object]] = mapped_column(DateTime(timezone=True), nullable=True)
    preset: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")


class ApiLoadScenario(PrimaryRecord, Base):
    __tablename__ = "api_load_scenarios"
    __table_args__ = (Index("ix_api_load_scenarios_project_status", "project_id", "status"),)

    project_id: Mapped[str] = mapped_column(ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    scenario_type: Mapped[str] = mapped_column(String(32), nullable=False)
    active_version_id: Mapped[Optional[str]] = mapped_column(
        ForeignKey(
            "api_load_scenario_versions.id",
            name="fk_load_scenario_active_version",
            ondelete="SET NULL",
            use_alter=True,
        ),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")


class ApiLoadScenarioVersion(PrimaryRecord, Base):
    __tablename__ = "api_load_scenario_versions"
    __table_args__ = (
        UniqueConstraint("scenario_id", "version_number"),
        Index("ix_api_load_scenario_versions_scenario_number", "scenario_id", "version_number"),
    )

    scenario_id: Mapped[str] = mapped_column(ForeignKey("api_load_scenarios.id", ondelete="CASCADE"), nullable=False)
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    definition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    validation_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    preflight_summary: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    compiler_version: Mapped[str] = mapped_column(String(80), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ApiLoadDataset(PrimaryRecord, Base):
    __tablename__ = "api_load_datasets"
    __table_args__ = (Index("ix_api_load_datasets_project_status", "project_id", "status"),)

    project_id: Mapped[str] = mapped_column(ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    field_schema: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    storage_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    sensitivity: Mapped[str] = mapped_column(String(32), nullable=False, server_default="normal")
    usage_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="active")


class ApiLoadRun(PrimaryRecord, Base):
    __tablename__ = "api_load_runs"
    __table_args__ = (
        Index("ix_api_load_runs_project_state_created", "project_id", "state", "created_at"),
    )

    project_id: Mapped[str] = mapped_column(ForeignKey("api_projects.id", ondelete="CASCADE"), nullable=False)
    scenario_version_id: Mapped[str] = mapped_column(ForeignKey("api_load_scenario_versions.id", ondelete="RESTRICT"), nullable=False)
    environment_revision_id: Mapped[str] = mapped_column(ForeignKey("api_environment_revisions.id", ondelete="RESTRICT"), nullable=False)
    load_model: Mapped[str] = mapped_column(String(48), nullable=False)
    queue_priority: Mapped[str] = mapped_column(String(16), nullable=False, server_default="normal")
    configuration: Mapped[dict] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="draft")
    verdict: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")
    stop_reason: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    started_at: Mapped[Optional[object]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[object]] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    ai_analysis_state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="pending")


class ApiLoadRunShard(PrimaryRecord, Base):
    __tablename__ = "api_load_run_shards"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence"),
        Index("ix_api_load_run_shards_run_state", "run_id", "state"),
    )

    run_id: Mapped[str] = mapped_column(ForeignKey("api_load_runs.id", ondelete="CASCADE"), nullable=False)
    agent_id: Mapped[Optional[str]] = mapped_column(ForeignKey("api_load_agents.id", ondelete="SET NULL"), nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    global_sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    allocation: Mapped[dict] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="assigned")
    last_heartbeat_at: Mapped[Optional[object]] = mapped_column(DateTime(timezone=True), nullable=True)
    process_info: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    summary: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    error: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")


class ApiLoadMetricBucket(PrimaryRecord, Base):
    __tablename__ = "api_load_metric_buckets"
    __table_args__ = (
        UniqueConstraint("run_id", "shard_id", "scenario_step_id", "bucket_started_at"),
        Index("ix_api_load_metric_buckets_run_time", "run_id", "bucket_started_at"),
    )

    run_id: Mapped[str] = mapped_column(ForeignKey("api_load_runs.id", ondelete="CASCADE"), nullable=False)
    shard_id: Mapped[str] = mapped_column(ForeignKey("api_load_run_shards.id", ondelete="CASCADE"), nullable=False)
    scenario_step_id: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    bucket_started_at: Mapped[object] = mapped_column(DateTime(timezone=True), nullable=False)
    bucket_seconds: Mapped[int] = mapped_column(Integer, nullable=False, server_default="5")
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")


class ApiLoadSample(PrimaryRecord, Base):
    __tablename__ = "api_load_samples"
    __table_args__ = (
        Index("ix_api_load_samples_run_kind", "run_id", "scenario_step_id", "kind"),
    )

    run_id: Mapped[str] = mapped_column(ForeignKey("api_load_runs.id", ondelete="CASCADE"), nullable=False)
    shard_id: Mapped[str] = mapped_column(ForeignKey("api_load_run_shards.id", ondelete="CASCADE"), nullable=False)
    scenario_step_id: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    kind: Mapped[str] = mapped_column(String(48), nullable=False)
    elapsed_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    business_code: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")


class ApiLoadEvent(PrimaryRecord, Base):
    __tablename__ = "api_load_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence"),
        Index("ix_api_load_events_run_sequence", "run_id", "sequence"),
    )

    run_id: Mapped[str] = mapped_column(ForeignKey("api_load_runs.id", ondelete="CASCADE"), nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")


class ApiLoadAiAnalysis(PrimaryRecord, Base):
    __tablename__ = "api_load_ai_analyses"
    __table_args__ = (Index("ix_api_load_ai_analyses_run_created", "run_id", "created_at"),)

    run_id: Mapped[str] = mapped_column(ForeignKey("api_load_runs.id", ondelete="CASCADE"), nullable=False)
    model: Mapped[str] = mapped_column(String(120), nullable=False, server_default="")
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="queued")
    result: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    error: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
