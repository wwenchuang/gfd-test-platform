"""Start owner-scoped investigations for recent failed automatic regressions."""
import logging
import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select

from ..models.execution import ApiExecution, ApiExecutionCase
from ..models.scheduled_job import ApiScheduledJobRun
from ...services import failure_analysis_tasks as investigations
from ...services.operation_attribution import actor_context, actor_from_trusted_username

logger = logging.getLogger(__name__)


def scan(factory, job_ids):
    if not job_ids:
        return
    failed_case = select(ApiExecutionCase.id).where(
        ApiExecutionCase.execution_id == ApiExecution.id,
        ApiExecutionCase.status.in_(('FAILED', 'BROKEN')),
    ).exists()
    query = (select(ApiExecution.id, ApiExecution.created_by, failed_case)
             .join(ApiScheduledJobRun, ApiScheduledJobRun.execution_id == ApiExecution.id)
             .where(ApiScheduledJobRun.job_id.in_(job_ids), ApiScheduledJobRun.trigger_type == 'schedule',
                    ApiExecution.state == 'DONE',
                    or_(ApiExecution.finished_at >= datetime.now(timezone.utc) - timedelta(days=1),
                        ApiExecution.id == os.getenv("MIDSCENE_PATROL_REPLAY_EXECUTION", "")))
             .order_by(ApiExecution.finished_at.desc()).limit(100))
    with factory() as db:
        runs = db.execute(query).all()
    for run_id, username, has_failures in runs:
        actor = actor_from_trusted_username(username)
        if actor['kind'] != 'user':
            continue
        try:
            with actor_context(actor):
                task = investigations.latest(actor, 'api', run_id)
                needs_repair = (task and task.get('status') == 'limited' and not task.get('evidence')
                                and 'secret_context_incomplete' in (task.get('result') or {}).get('missing_evidence', []))
                if has_failures and (not task or needs_repair):
                    prefix = 'midscene:patrol-safe-stats:' if needs_repair else 'midscene:regression-patrol:'
                    request_id = uuid.uuid5(uuid.NAMESPACE_URL, prefix + run_id)
                    task = investigations.start(actor, dict(source='api', run_id=run_id, request_id=str(request_id)))
                if not task or task['status'] not in ('queued', 'running'):
                    from .patrol_summary import publish
                    publish(factory, run_id, username, job_ids, task)
        except (investigations.Busy, PermissionError, LookupError):
            continue
