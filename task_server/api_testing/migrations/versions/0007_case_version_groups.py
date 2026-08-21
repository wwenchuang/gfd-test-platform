"""Persist user-managed API case groups.

Revision ID: 0007
Revises: 0006
"""

from alembic import op
import sqlalchemy as sa


revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "api_case_versions",
        sa.Column("group_name", sa.String(length=120), server_default="", nullable=False),
    )


def downgrade():
    op.drop_column("api_case_versions", "group_name")
