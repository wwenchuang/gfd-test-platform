"""Allow an explicit per-job opt-in for one-time baselines.

Revision ID: 0009
Revises: 0008
"""

import sqlalchemy as sa
from alembic import op


revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "api_scheduled_jobs",
        sa.Column(
            "allow_one_time_baselines",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade():
    op.drop_column("api_scheduled_jobs", "allow_one_time_baselines")
