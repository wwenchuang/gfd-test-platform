"""Independent opt-in performance schedules."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='0013'
down_revision='0012'
branch_labels=None
depends_on=None

def audit():
    return [sa.Column('id',sa.String(36),primary_key=True),*[sa.Column(k,sa.String(128),nullable=False) for k in ('owner_id','created_by','updated_by')],*[sa.Column(k,sa.DateTime(timezone=True),nullable=False,server_default=sa.func.now()) for k in ('created_at','updated_at')],sa.Column('row_version',sa.Integer(),nullable=False,server_default='1')]

def upgrade():
    op.create_table('api_load_schedules',*audit(),sa.Column('project_id',sa.String(36),sa.ForeignKey('api_projects.id',ondelete='RESTRICT'),nullable=False),sa.Column('environment_revision_id',sa.String(36),sa.ForeignKey('api_environment_revisions.id',ondelete='RESTRICT'),nullable=False),sa.Column('name',sa.String(160),nullable=False),sa.Column('source_run_id',sa.String(36),nullable=False),sa.Column('snapshot',JSONB,nullable=False),sa.Column('source_labels',JSONB,nullable=False),sa.Column('daily_time',sa.String(5),nullable=False),sa.Column('archived',sa.Boolean(),nullable=False,server_default='false'),sa.Column('enabled',sa.Boolean(),nullable=False,server_default='false'),sa.Column('notification_enabled',sa.Boolean(),nullable=False,server_default='false'),sa.Column('next_run_at',sa.DateTime(timezone=True),nullable=False),sa.Column('claimed_at',sa.DateTime(timezone=True)),sa.Column('active_run_id',sa.String(36)),sa.Column('last_run_id',sa.String(36)),sa.Column('last_status',sa.String(40),nullable=False,server_default='idle'),sa.Column('last_message',sa.Text(),nullable=False,server_default=''))
    op.create_index('ix_api_load_schedules_next_run_at','api_load_schedules',['next_run_at'])
    op.create_index('ix_api_load_schedules_owner_id','api_load_schedules',['owner_id'])
    op.create_table('api_load_schedule_occurrences',*audit(),sa.Column('schedule_id',sa.String(36),sa.ForeignKey('api_load_schedules.id',ondelete='CASCADE'),nullable=False),sa.Column('run_id',sa.String(36),nullable=False,unique=True),sa.Column('notification_enabled',sa.Boolean(),nullable=False),sa.Column('finalize_state',sa.String(32),nullable=False,server_default='pending'),sa.Column('finalize_claimed_at',sa.DateTime(timezone=True)),sa.Column('notification_status',sa.String(32),nullable=False))
    op.create_index('ix_api_load_schedule_occurrences_schedule_id','api_load_schedule_occurrences',['schedule_id'])
    op.create_index('ix_api_load_schedule_occurrences_owner_id','api_load_schedule_occurrences',['owner_id'])

def downgrade():
    op.drop_table('api_load_schedule_occurrences')
    op.drop_table('api_load_schedules')
