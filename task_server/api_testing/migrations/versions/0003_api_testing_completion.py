"""Persist provider credentials and lightweight API test tasks.

Revision ID: 0003
Revises: 0002
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _primary_columns():
    return (
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "row_version", sa.Integer(), server_default="1", nullable=False
        ),
    )


def upgrade():
    op.create_table(
        "api_provider_credentials",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("key_version", sa.Integer(), server_default="1", nullable=False),
        *_primary_columns(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "provider"),
    )
    op.create_index(
        "ix_api_provider_credentials_owner_id",
        "api_provider_credentials",
        ["owner_id"],
    )

    op.create_table(
        "api_test_tasks",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("source_revision_id", sa.String(length=36), nullable=False),
        sa.Column("environment_revision_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("state", sa.String(length=32), server_default="draft", nullable=False),
        sa.Column(
            "selected_endpoint_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("latest_ai_job_id", sa.String(length=36), nullable=True),
        sa.Column("latest_execution_id", sa.String(length=36), nullable=True),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        *_primary_columns(),
        sa.ForeignKeyConstraint(
            ["project_id"], ["api_projects.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["source_revision_id"],
            ["api_source_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["environment_revision_id"],
            ["api_environment_revisions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["latest_ai_job_id"], ["api_ai_jobs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["latest_execution_id"], ["api_executions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_api_test_tasks_owner_id", "api_test_tasks", ["owner_id"])
    op.create_index(
        "ix_api_test_tasks_owner_project_state_updated",
        "api_test_tasks",
        ["owner_id", "project_id", "state", "updated_at"],
    )


def downgrade():
    op.drop_index(
        "ix_api_test_tasks_owner_project_state_updated",
        table_name="api_test_tasks",
    )
    op.drop_index("ix_api_test_tasks_owner_id", table_name="api_test_tasks")
    op.drop_table("api_test_tasks")
    op.drop_index(
        "ix_api_provider_credentials_owner_id",
        table_name="api_provider_credentials",
    )
    op.drop_table("api_provider_credentials")
