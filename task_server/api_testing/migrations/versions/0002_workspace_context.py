"""Persist owner-scoped API testing workspace context.

Revision ID: 0002
Revises: 0001
"""

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "api_workspaces",
        sa.Column("project_id", sa.String(length=36), nullable=True),
        sa.Column("source_revision_id", sa.String(length=36), nullable=True),
        sa.Column("environment_revision_id", sa.String(length=36), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("owner_id", sa.String(length=128), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["api_projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["source_revision_id"], ["api_source_revisions.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["environment_revision_id"], ["api_environment_revisions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id"),
    )
    op.create_index("ix_api_workspaces_owner_id", "api_workspaces", ["owner_id"])


def downgrade():
    op.drop_index("ix_api_workspaces_owner_id", table_name="api_workspaces")
    op.drop_table("api_workspaces")
