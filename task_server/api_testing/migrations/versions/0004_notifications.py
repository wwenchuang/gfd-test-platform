"""Persist API testing notification channel settings.

Revision ID: 0004
Revises: 0003
"""

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
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
        sa.Column("row_version", sa.Integer(), server_default="1", nullable=False),
    )


def upgrade():
    op.create_table(
        "api_notification_channels",
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("channel_type", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("ciphertext", sa.Text(), server_default="", nullable=False),
        sa.Column("fingerprint", sa.String(length=64), server_default="", nullable=False),
        sa.Column("key_version", sa.Integer(), server_default="1", nullable=False),
        *_primary_columns(),
        sa.ForeignKeyConstraint(["project_id"], ["api_projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id", "project_id", "channel_type"),
    )
    op.create_index(
        "ix_api_notification_channels_owner_id",
        "api_notification_channels",
        ["owner_id"],
    )
    op.create_index(
        "ix_api_notification_channels_project_id",
        "api_notification_channels",
        ["project_id"],
    )


def downgrade():
    op.drop_index(
        "ix_api_notification_channels_project_id",
        table_name="api_notification_channels",
    )
    op.drop_index(
        "ix_api_notification_channels_owner_id",
        table_name="api_notification_channels",
    )
    op.drop_table("api_notification_channels")
