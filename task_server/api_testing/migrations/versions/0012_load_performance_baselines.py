"""Add immutable manual performance references and a unique active scope."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='0012'
down_revision='0011'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('api_load_performance_baselines',
        sa.Column('id',sa.String(36),primary_key=True),
        *[sa.Column(name,sa.String(128),nullable=False) for name in ('owner_id','created_by','updated_by')],
        *[sa.Column(name,sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()) for name in ('created_at','updated_at')],
        sa.Column('row_version',sa.Integer(),nullable=False,server_default='1'),
        sa.Column('project_id',sa.String(36),sa.ForeignKey('api_projects.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('scenario_id',sa.String(36),sa.ForeignKey('api_load_scenarios.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('environment_id',sa.String(36),sa.ForeignKey('api_environments.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('environment_revision_id',sa.String(36),sa.ForeignKey('api_environment_revisions.id',ondelete='RESTRICT'),nullable=False),
        sa.Column('source_run_id',sa.String(36),nullable=False),
        sa.Column('name',sa.String(160),nullable=False),sa.Column('adoption_reason',sa.Text(),nullable=False),
        sa.Column('status',sa.String(32),nullable=False,server_default='active'),
        sa.Column('evidence_snapshot',JSONB(),nullable=False),sa.Column('evidence_hash',sa.String(64),nullable=False),
        sa.Column('regression_policy',JSONB(),nullable=False),sa.Column('adoption_key',sa.String(64),nullable=False),
        sa.UniqueConstraint('adoption_key'))
    op.create_index('ix_api_load_performance_baselines_owner_id','api_load_performance_baselines',['owner_id'])
    op.create_index('ix_load_performance_baseline_scope','api_load_performance_baselines',['project_id','scenario_id','environment_id','created_at'])
    op.create_index('uq_load_performance_baseline_active','api_load_performance_baselines',['project_id','scenario_id','environment_id'],unique=True,postgresql_where=sa.text("status = 'active'"))


def downgrade():
    # Destructive: archive baseline snapshots externally before an intentional downgrade.
    op.drop_table('api_load_performance_baselines')
