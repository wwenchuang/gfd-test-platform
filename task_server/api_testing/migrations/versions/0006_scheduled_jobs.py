"""Persist API scheduled regression jobs.

Revision ID: 0006
Revises: 0005
"""

from alembic import op
import sqlalchemy as sa


revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "api_scheduled_jobs",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("source_revision_id", sa.String(length=36), nullable=True),
        sa.Column("environment_revision_id", sa.String(length=36), nullable=True),
        sa.Column("environment_id", sa.String(length=36), nullable=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("schedule_type", sa.String(length=32), nullable=False),
        sa.Column("cron_expression", sa.String(length=120), server_default="", nullable=False),
        sa.Column("environment_strategy", sa.String(length=32), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("notify_feishu", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("retry_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), server_default="1800", nullable=False),
        sa.Column("summary", sa.Text(), server_default="", nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["environment_id"], ["api_environments.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["environment_revision_id"], ["api_environment_revisions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["api_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_revision_id"], ["api_source_revisions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_scheduled_jobs_owner_id", "api_scheduled_jobs", ["owner_id"])
    op.create_index("ix_api_scheduled_jobs_project_enabled", "api_scheduled_jobs", ["project_id", "enabled"])
    op.create_table(
        "api_scheduled_job_targets",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=200), nullable=False),
        sa.Column("group_name", sa.String(length=120), server_default="", nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["api_scheduled_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "sequence"),
    )
    op.create_index("ix_api_scheduled_job_targets_owner_id", "api_scheduled_job_targets", ["owner_id"])
    op.create_index("ix_api_scheduled_job_targets_job", "api_scheduled_job_targets", ["job_id"])
    op.create_table(
        "api_scheduled_job_runs",
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("execution_id", sa.String(length=36), nullable=True),
        sa.Column("trigger_type", sa.String(length=32), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="queued", nullable=False),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["execution_id"], ["api_executions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_id"], ["api_scheduled_jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_scheduled_job_runs_owner_id", "api_scheduled_job_runs", ["owner_id"])
    op.create_index("ix_api_scheduled_job_runs_job_created", "api_scheduled_job_runs", ["job_id", "created_at"])


def downgrade():
    op.drop_index("ix_api_scheduled_job_runs_job_created", table_name="api_scheduled_job_runs")
    op.drop_index("ix_api_scheduled_job_runs_owner_id", table_name="api_scheduled_job_runs")
    op.drop_table("api_scheduled_job_runs")
    op.drop_index("ix_api_scheduled_job_targets_job", table_name="api_scheduled_job_targets")
    op.drop_index("ix_api_scheduled_job_targets_owner_id", table_name="api_scheduled_job_targets")
    op.drop_table("api_scheduled_job_targets")
    op.drop_index("ix_api_scheduled_jobs_project_enabled", table_name="api_scheduled_jobs")
    op.drop_index("ix_api_scheduled_jobs_owner_id", table_name="api_scheduled_jobs")
    op.drop_table("api_scheduled_jobs")
