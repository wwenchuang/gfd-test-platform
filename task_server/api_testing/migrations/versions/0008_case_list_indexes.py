"""Add indexes for API case list lifecycle lookups.

Revision ID: 0008
Revises: 0007
"""

from alembic import op


revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index(
        "ix_api_baselines_case_status_created",
        "api_baselines",
        ["case_id", "status", "created_at"],
    )
    op.create_index(
        "ix_api_execution_cases_version_created",
        "api_execution_cases",
        ["case_version_id", "created_at"],
    )


def downgrade():
    op.drop_index(
        "ix_api_execution_cases_version_created",
        table_name="api_execution_cases",
    )
    op.drop_index(
        "ix_api_baselines_case_status_created",
        table_name="api_baselines",
    )
