"""Persist platform managed API baseline groups.

Revision ID: 0005
Revises: 0004
"""

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "api_baselines",
        sa.Column("group_name", sa.String(length=120), server_default="", nullable=False),
    )
    op.create_index(
        "ix_api_baselines_project_group",
        "api_baselines",
        ["project_id", "group_name"],
    )


def downgrade():
    op.drop_index("ix_api_baselines_project_group", table_name="api_baselines")
    op.drop_column("api_baselines", "group_name")
