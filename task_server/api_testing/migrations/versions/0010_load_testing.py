"""Add the distributed API performance-testing domain.

Revision ID: 0010
Revises: 0009
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def _audit_columns():
    return (
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def _owner_index(table_name):
    op.create_index(f"ix_{table_name}_owner_id", table_name, ["owner_id"])


def upgrade():
    op.create_table(
        "api_load_agents",
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="offline", nullable=False),
        sa.Column("scheduling_tier", sa.String(length=32), server_default="normal", nullable=False),
        sa.Column("node_group", sa.String(length=120), server_default="", nullable=False),
        sa.Column("labels", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("agent_version", sa.String(length=80), server_default="", nullable=False),
        sa.Column("k6_version", sa.String(length=80), server_default="", nullable=False),
        sa.Column("credential_hash", sa.String(length=64), nullable=False),
        sa.Column("hard_limits", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("soft_limits", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("current_usage", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("health", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("egress_ip", sa.String(length=64), server_default="", nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("offline_reason", sa.Text(), server_default="", nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint("name"),
    )
    _owner_index("api_load_agents")
    op.create_index("ix_api_load_agents_status_tier", "api_load_agents", ["status", "scheduling_tier"])

    op.create_table(
        "api_load_agent_enrollments",
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("preset", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        *_audit_columns(),
        sa.UniqueConstraint("token_hash"),
    )
    _owner_index("api_load_agent_enrollments")
    op.create_index(
        "ix_api_load_agent_enrollments_expiry",
        "api_load_agent_enrollments",
        ["expires_at", "used_at"],
    )

    op.create_table(
        "api_load_scenarios",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("scenario_type", sa.String(length=32), nullable=False),
        sa.Column("active_version_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["api_projects.id"], ondelete="CASCADE"),
    )
    _owner_index("api_load_scenarios")
    op.create_index(
        "ix_api_load_scenarios_project_status",
        "api_load_scenarios",
        ["project_id", "status"],
    )

    op.create_table(
        "api_load_scenario_versions",
        sa.Column("scenario_id", sa.String(length=36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_snapshot", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("validation_summary", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("preflight_summary", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("compiler_version", sa.String(length=80), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["scenario_id"], ["api_load_scenarios.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("scenario_id", "version_number"),
    )
    _owner_index("api_load_scenario_versions")
    op.create_index(
        "ix_api_load_scenario_versions_scenario_number",
        "api_load_scenario_versions",
        ["scenario_id", "version_number"],
    )
    op.create_foreign_key(
        "fk_load_scenario_active_version",
        "api_load_scenarios",
        "api_load_scenario_versions",
        ["active_version_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.create_table(
        "api_load_datasets",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("field_schema", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("row_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("storage_ref", sa.String(length=500), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("sensitivity", sa.String(length=32), server_default="normal", nullable=False),
        sa.Column("usage_mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["api_projects.id"], ondelete="CASCADE"),
    )
    _owner_index("api_load_datasets")
    op.create_index(
        "ix_api_load_datasets_project_status",
        "api_load_datasets",
        ["project_id", "status"],
    )

    op.create_table(
        "api_load_runs",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("scenario_version_id", sa.String(length=36), nullable=False),
        sa.Column("environment_revision_id", sa.String(length=36), nullable=False),
        sa.Column("load_model", sa.String(length=48), nullable=False),
        sa.Column("queue_priority", sa.String(length=16), server_default="normal", nullable=False),
        sa.Column("configuration", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column("verdict", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("stop_reason", sa.Text(), server_default="", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("ai_analysis_state", sa.String(length=32), server_default="pending", nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["api_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["scenario_version_id"], ["api_load_scenario_versions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["environment_revision_id"], ["api_environment_revisions.id"], ondelete="RESTRICT"),
    )
    _owner_index("api_load_runs")
    op.create_index(
        "ix_api_load_runs_project_state_created",
        "api_load_runs",
        ["project_id", "state", "created_at"],
    )

    op.create_table(
        "api_load_run_shards",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("global_sequence", sa.Integer(), nullable=False),
        sa.Column("allocation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="assigned", nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("process_info", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["run_id"], ["api_load_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["agent_id"], ["api_load_agents.id"], ondelete="SET NULL"),
        sa.UniqueConstraint("run_id", "sequence"),
    )
    _owner_index("api_load_run_shards")
    op.create_index(
        "ix_api_load_run_shards_run_state",
        "api_load_run_shards",
        ["run_id", "state"],
    )

    op.create_table(
        "api_load_metric_buckets",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("shard_id", sa.String(length=36), nullable=False),
        sa.Column("scenario_step_id", sa.String(length=120), server_default="", nullable=False),
        sa.Column("bucket_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_seconds", sa.Integer(), server_default="5", nullable=False),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["run_id"], ["api_load_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shard_id"], ["api_load_run_shards.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "shard_id", "scenario_step_id", "bucket_started_at"),
    )
    _owner_index("api_load_metric_buckets")
    op.create_index(
        "ix_api_load_metric_buckets_run_time",
        "api_load_metric_buckets",
        ["run_id", "bucket_started_at"],
    )

    op.create_table(
        "api_load_samples",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("shard_id", sa.String(length=36), nullable=False),
        sa.Column("scenario_step_id", sa.String(length=120), server_default="", nullable=False),
        sa.Column("kind", sa.String(length=48), nullable=False),
        sa.Column("elapsed_ms", sa.Float(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=True),
        sa.Column("business_code", sa.String(length=120), server_default="", nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("occurrence_count", sa.Integer(), server_default="1", nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["run_id"], ["api_load_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shard_id"], ["api_load_run_shards.id"], ondelete="CASCADE"),
    )
    _owner_index("api_load_samples")
    op.create_index(
        "ix_api_load_samples_run_kind",
        "api_load_samples",
        ["run_id", "scenario_step_id", "kind"],
    )

    op.create_table(
        "api_load_events",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["run_id"], ["api_load_runs.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("run_id", "sequence"),
    )
    _owner_index("api_load_events")
    op.create_index(
        "ix_api_load_events_run_sequence",
        "api_load_events",
        ["run_id", "sequence"],
    )

    op.create_table(
        "api_load_ai_analyses",
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("model", sa.String(length=120), server_default="", nullable=False),
        sa.Column("prompt_version", sa.String(length=80), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("error", sa.Text(), server_default="", nullable=False),
        *_audit_columns(),
        sa.ForeignKeyConstraint(["run_id"], ["api_load_runs.id"], ondelete="CASCADE"),
    )
    _owner_index("api_load_ai_analyses")
    op.create_index(
        "ix_api_load_ai_analyses_run_created",
        "api_load_ai_analyses",
        ["run_id", "created_at"],
    )


def downgrade():
    op.drop_index("ix_api_load_ai_analyses_run_created", table_name="api_load_ai_analyses")
    op.drop_index("ix_api_load_ai_analyses_owner_id", table_name="api_load_ai_analyses")
    op.drop_table("api_load_ai_analyses")
    op.drop_index("ix_api_load_events_run_sequence", table_name="api_load_events")
    op.drop_index("ix_api_load_events_owner_id", table_name="api_load_events")
    op.drop_table("api_load_events")
    op.drop_index("ix_api_load_samples_run_kind", table_name="api_load_samples")
    op.drop_index("ix_api_load_samples_owner_id", table_name="api_load_samples")
    op.drop_table("api_load_samples")
    op.drop_index("ix_api_load_metric_buckets_run_time", table_name="api_load_metric_buckets")
    op.drop_index("ix_api_load_metric_buckets_owner_id", table_name="api_load_metric_buckets")
    op.drop_table("api_load_metric_buckets")
    op.drop_index("ix_api_load_run_shards_run_state", table_name="api_load_run_shards")
    op.drop_index("ix_api_load_run_shards_owner_id", table_name="api_load_run_shards")
    op.drop_table("api_load_run_shards")
    op.drop_index("ix_api_load_runs_project_state_created", table_name="api_load_runs")
    op.drop_index("ix_api_load_runs_owner_id", table_name="api_load_runs")
    op.drop_table("api_load_runs")
    op.drop_index("ix_api_load_datasets_project_status", table_name="api_load_datasets")
    op.drop_index("ix_api_load_datasets_owner_id", table_name="api_load_datasets")
    op.drop_table("api_load_datasets")
    op.drop_constraint(
        "fk_load_scenario_active_version",
        "api_load_scenarios",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_api_load_scenario_versions_scenario_number",
        table_name="api_load_scenario_versions",
    )
    op.drop_index("ix_api_load_scenario_versions_owner_id", table_name="api_load_scenario_versions")
    op.drop_table("api_load_scenario_versions")
    op.drop_index("ix_api_load_scenarios_project_status", table_name="api_load_scenarios")
    op.drop_index("ix_api_load_scenarios_owner_id", table_name="api_load_scenarios")
    op.drop_table("api_load_scenarios")
    op.drop_index("ix_api_load_agent_enrollments_expiry", table_name="api_load_agent_enrollments")
    op.drop_index("ix_api_load_agent_enrollments_owner_id", table_name="api_load_agent_enrollments")
    op.drop_table("api_load_agent_enrollments")
    op.drop_index("ix_api_load_agents_status_tier", table_name="api_load_agents")
    op.drop_index("ix_api_load_agents_owner_id", table_name="api_load_agents")
    op.drop_table("api_load_agents")
