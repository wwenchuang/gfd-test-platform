"""Add environment monitoring catalog and immutable configuration revisions."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None


def audit():
    return [sa.Column('id', sa.String(36), primary_key=True),
            *[sa.Column(n, sa.String(128), nullable=False) for n in ('owner_id', 'created_by', 'updated_by')],
            *[sa.Column(n, sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()) for n in ('created_at', 'updated_at')],
            sa.Column('row_version', sa.Integer(), nullable=False, server_default='1')]


def upgrade():
    op.create_table('api_load_monitoring_services', *audit(),
        sa.Column('environment_id', sa.String(36), sa.ForeignKey('api_environments.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('project_id', sa.String(36), sa.ForeignKey('api_projects.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('status', sa.String(32), nullable=False, server_default='active'),
        sa.Column('active_revision_id', sa.String(36)),
        sa.Column('last_check', JSONB(), nullable=False, server_default='{}'))
    op.create_table('api_load_monitoring_revisions', *audit(),
        sa.Column('service_id', sa.String(36), sa.ForeignKey('api_load_monitoring_services.id', ondelete='RESTRICT'), nullable=False),
        sa.Column('definition', JSONB(), nullable=False),
        sa.Column('authorized_host', sa.String(253), nullable=False),
        sa.Column('authorized_by', sa.String(128), nullable=False),
        sa.Column('secret_value_id', sa.String(36), sa.ForeignKey('api_secret_values.id', ondelete='RESTRICT')),
        sa.UniqueConstraint('service_id', 'id'))
    for table, columns in [('api_load_monitoring_services', ['owner_id', 'environment_id']), ('api_load_monitoring_revisions', ['owner_id', 'service_id'])]:
        for column in columns:
            op.create_index('ix_' + table + '_' + column, table, [column])


def downgrade():
    op.drop_table('api_load_monitoring_revisions')
    op.drop_table('api_load_monitoring_services')
