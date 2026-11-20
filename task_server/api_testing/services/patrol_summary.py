"""Publish a single comparison notice into the existing authorized execution report."""
from collections import defaultdict
from sqlalchemy import select
from .. import access
from ..models.execution import ApiExecution, ApiExecutionCase, ApiExecutionEvent
from ..models.scheduled_job import ApiScheduledJobRun
from ..repositories.execution_repository import ExecutionRepository
from .notification_service import NotificationDeliveryError, NotificationNotConfiguredError, NotificationService

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


def _grounded_text(analysis, field):
    evidence_ids = {item['id'] for item in analysis.get('evidence', []) if item.get('id')}
    for item in (analysis.get('result') or {}).get(field, []):
        refs = item.get('evidence_ids') or []
        if refs and all(ref in evidence_ids for ref in refs):
            text = ' '.join(str(item.get('text') or '').split())
            if text:
                return text if len(text) <= 180 else text[:179] + '…'
    return ''


def _summary(db, run, job, username, analysis):
    prior = db.scalar(select(ApiExecution.id).join(ApiScheduledJobRun,
        ApiScheduledJobRun.execution_id == ApiExecution.id).where(ApiScheduledJobRun.job_id == job,
        ApiScheduledJobRun.trigger_type == 'schedule', ApiExecution.state == 'DONE',
        ApiExecution.project_id == run.project_id, ApiExecution.environment_revision_id == run.environment_revision_id,
        ApiExecution.finished_at < (run.started_at or run.created_at), access.resource_predicate(username, ApiExecution))
        .order_by(ApiExecution.finished_at.desc(), ApiExecution.id).limit(1))

    def cases(rid):
        return db.execute(select(ApiExecutionCase.id, ApiExecutionCase.case_version_id,
            ApiExecutionCase.status).where(ApiExecutionCase.execution_id == rid,
            access.resource_predicate(username, ApiExecutionCase))).all()

    current = cases(run.id)
    groups = compare(current, cases(prior) if prior else [])
    failed = sum(row.status in FAILED for row in current)
    conclusion = _grounded_text(analysis or {}, 'facts')
    next_step = _grounded_text(analysis or {}, 'recommendations')
    if failed:
        conclusion = conclusion or f'本次有 {failed} 条用例失败或异常，原因待核实。'
        next_step = next_step or '打开报告中的失败用例，核对失败断言和同次请求回执。'
    else:
        conclusion = '本次没有失败或异常用例。'
        next_step = '打开报告核对恢复用例及其通过记录。' if groups['恢复'] else '在报告中核对执行范围和各用例终态。'
    return dict(message='回归巡检 · ' + '，'.join(f'{k} {len(v)}' for k, v in groups.items()),
                对比明细=groups, 上次执行=prior, 本次执行=run.id, 结论=conclusion, 下一步=next_step,
                调查状态=analysis['status'] if analysis else '本次无失败', 调查编号=analysis.get('id') if analysis else None)


def publish(factory, run_id, username, job_ids, analysis):
    if analysis and analysis.get('status') in {'queued', 'running'}:
        return
    with factory.begin() as db:
        repo = ExecutionRepository(db)
        run = repo.get_execution(run_id, for_update=True)
        if not run or run.state != 'DONE' or run.created_by != username or not access.resource_allowed(db, run, username):
            return
        job = db.scalar(select(ApiScheduledJobRun.job_id).where(ApiScheduledJobRun.execution_id == run_id,
            ApiScheduledJobRun.trigger_type == 'schedule', ApiScheduledJobRun.job_id.in_(job_ids)))
        if not job:
            return
        def event(kind):
            return db.scalar(select(ApiExecutionEvent).where(ApiExecutionEvent.execution_id == run_id,
                             ApiExecutionEvent.event_type == kind).limit(1))
        notice = event('agent_patrol_notice')
        if (not notice or not notice.payload.get('结论')
                or (analysis and notice.payload.get('调查编号') != analysis.get('id'))):
            payload = _summary(db, run, job, username, analysis)
            if notice:
                notice.payload = payload
            else:
                notice = repo.append_event(run_id, 'agent_patrol_notice', payload, username)
        if (run.summary or {}).get('agent_patrol') != notice.payload:
            run.summary = {**(run.summary or {}), 'agent_patrol': notice.payload}
        task = (run.request_snapshot or {}).get('task') or {}
        if (task.get('notify_feishu_after_patrol') is not True or task.get('notify_feishu') is not False
                or task.get('id') != job or task.get('trigger') != 'schedule'):
            return
        delivery = event('agent_patrol_delivery')
        if delivery:
            if run.summary.get('agent_patrol_notification') != delivery.payload:
                run.summary = {**run.summary, 'agent_patrol_notification': delivery.payload}
            return
        # Commit the claim before POST. If the process dies the bot may have received it.
        pending = {'status': 'unknown', 'message': '已领取发送，尚未取得飞书回执', 'channel_type': 'feishu'}
        delivery = repo.append_event(run_id, 'agent_patrol_delivery', pending, username)
        delivery_id = delivery.id
        run.summary = {**run.summary, 'agent_patrol_notification': pending}

    try:
        NotificationService(factory).send_execution_report(run_id, username, patrol=True)
        status, message = 'sent', '飞书已确认接收巡检报告'
    except NotificationDeliveryError as error:
        status, message = error.status, str(error)
    except NotificationNotConfiguredError:
        status, message = 'failed', '项目飞书通知未启用、未配置或执行已不可访问'
    except PermissionError:
        status, message = 'failed', '执行发起者当前缺少报告或飞书通知权限'
    except Exception:
        status, message = 'unknown', '未能确认巡检通知送达'
    with factory.begin() as db:
        run = ExecutionRepository(db).get_execution(run_id, for_update=True)
        delivery = db.get(ApiExecutionEvent, delivery_id)
        if run and delivery:
            outcome = {'status': status, 'message': message, 'channel_type': 'feishu'}
            delivery.payload = outcome
            run.summary = {**(run.summary or {}), 'agent_patrol_notification': outcome}
