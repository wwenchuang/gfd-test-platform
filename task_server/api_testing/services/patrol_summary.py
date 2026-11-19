"""Publish a single comparison notice into the existing authorized execution report."""
from collections import defaultdict
from sqlalchemy import select
from .. import access
from ..models.execution import ApiExecution, ApiExecutionCase, ApiExecutionEvent
from ..models.scheduled_job import ApiScheduledJobRun
from ..repositories.execution_repository import ExecutionRepository

FAILED = {'FAILED', 'BROKEN'}


def compare(current, previous):
    groups = {key: [] for key in ('新增', '重复', '恢复', '待核对')}
    before = defaultdict(list)
    now = defaultdict(list)
    for row in previous:
        before[row.case_version_id].append(row)
    for row in current:
        now[row.case_version_id].append(row)
    for version in sorted(now.keys() | before.keys()):
        a, b = now[version], before[version]
        if not any(r.status in FAILED for r in a + b):
            continue
        category = '待核对'
        if len(a) == len(b) == 1:
            if a[0].status in FAILED and b[0].status in FAILED:
                category = '重复'
            elif a[0].status in FAILED and b[0].status == 'PASSED':
                category = '新增'
            elif a[0].status == 'PASSED' and b[0].status in FAILED:
                category = '恢复'
        groups[category].append({'本次用例': [r.id for r in a], '上次用例': [r.id for r in b]})
    return groups


def publish(factory, run_id, username, job_ids, analysis):
    with factory.begin() as db:
        repo = ExecutionRepository(db)
        run = repo.get_execution(run_id, for_update=True)
        if not run or not access.resource_allowed(db, run, username):
            return
        notice = db.scalar(select(ApiExecutionEvent).where(ApiExecutionEvent.execution_id == run_id,
                ApiExecutionEvent.event_type == 'agent_patrol_notice').limit(1))
        if notice and (not analysis or notice.payload.get('调查编号') == analysis.get('id')):
            if not (run.summary or {}).get('agent_patrol'):
                run.summary = {**(run.summary or {}), 'agent_patrol': notice.payload}
            return
        job = db.scalar(select(ApiScheduledJobRun.job_id).where(ApiScheduledJobRun.execution_id == run_id,
            ApiScheduledJobRun.trigger_type == 'schedule', ApiScheduledJobRun.job_id.in_(job_ids)))
        if not job:
            return
        prior = db.scalar(select(ApiExecution.id).join(ApiScheduledJobRun,
            ApiScheduledJobRun.execution_id == ApiExecution.id).where(ApiScheduledJobRun.job_id == job,
            ApiScheduledJobRun.trigger_type == 'schedule', ApiExecution.state == 'DONE',
            ApiExecution.project_id == run.project_id, ApiExecution.environment_revision_id == run.environment_revision_id,
            ApiExecution.finished_at < run.started_at, access.resource_predicate(username, ApiExecution))
            .order_by(ApiExecution.finished_at.desc(), ApiExecution.id).limit(1))
        def cases(rid):
            return db.execute(select(ApiExecutionCase.id, ApiExecutionCase.case_version_id,
                ApiExecutionCase.status).where(ApiExecutionCase.execution_id == rid,
                access.resource_predicate(username, ApiExecutionCase))).all()
        groups = compare(cases(run_id), cases(prior) if prior else [])
        message = '回归巡检 · ' + '，'.join(f'{k} {len(v)}' for k,v in groups.items())
        payload = dict(message=message, 对比明细=groups,
            上次执行=prior, 本次执行=run_id, 调查状态=analysis['status'] if analysis else '本次无失败',
            调查编号=analysis.get('id') if analysis else None)
        run.summary = {**(run.summary or {}), 'agent_patrol': payload}
        if notice:
            notice.payload = payload
        else:
            repo.append_event(run_id, 'agent_patrol_notice', payload, username)
