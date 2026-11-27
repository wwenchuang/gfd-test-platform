"""File requirement acceptance failures from bound Runner evidence into Feishu Project."""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from . import feishu_project_client as project
from .operation_attribution import actor_context, actor_from_trusted_username
from .requirement_intake import requirement_for_batch
from .ui_execution_contract import require_ui_actor
from .ui_execution_service import get_ui_batch
from .ui_execution_store import UiExecutionStore


_WORKER = None
_CONFIG = 'MIDSCENE_FEISHU_DEFECT_CONFIG'


def _connect():
    db = UiExecutionStore()._connect()
    db.executescript('''CREATE TABLE IF NOT EXISTS ui_defect_receipts (
        tracking_key TEXT PRIMARY KEY, title TEXT NOT NULL, description TEXT NOT NULL,
        status TEXT NOT NULL, issue_id INTEGER, detail TEXT NOT NULL DEFAULT '');
        CREATE TABLE IF NOT EXISTS ui_defect_reviews (
        batch_id TEXT PRIMARY KEY, finished INTEGER NOT NULL, retry_after INTEGER NOT NULL, result TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS intake_finished_batches ON batches(created_ns)
        WHERE execution_settled=1 AND idempotency_key GLOB 'requirement-*';''')
    return db


def _config(app):
    path = os.getenv(_CONFIG)
    if not path:
        return None
    try:
        with Path(path).open('rb') as stream:
            blob = stream.read(1024 * 1024 + 1)
        if len(blob) > 1024 * 1024:
            raise ValueError()
        target = json.loads(blob)['apps'].get(app)
        if target is None:
            return None
        if not all(isinstance(target.get(k),str) and re.fullmatch(r'[A-Za-z0-9_-]{1,128}',target[k])
                   for k in ('project_key','work_item_type_key','evidence_field')):
            raise ValueError()
        if 'template_id' in target and (type(target['template_id']) is not int or target['template_id'] < 1):
            raise ValueError()
        fields = target.get('field_value_pairs', [])
        if not isinstance(fields, list) or len(fields) > 50:
            raise ValueError()
        field_keys = {target['evidence_field']}
        for field in fields:
            if (not isinstance(field,dict) or set(field) != {'field_key','field_value'}
                    or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',field['field_key']) or field['field_key'] in field_keys):
                raise ValueError()
            field_keys.add(field['field_key'])
        if 'developer_assignment' in target:
            routing = target['developer_assignment']
            if (not isinstance(routing,dict) or set(routing) != {'requirement_type','developer_field','assignee_field'}
                    or any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',v) for v in routing.values())
                    or routing['assignee_field'] in field_keys):
                raise ValueError()
        rules = target['rules']
        if not isinstance(rules,list) or not rules:
            raise ValueError()
        for rule in rules:
            if not re.fullmatch(r'[0-9a-f]{64}',rule['case_revision']) or not re.fullmatch(r'step_[0-9a-f]{24}',rule['step_id']):
                raise ValueError()
            for field in ('case_id','diagnostic','title','expected'):
                value = rule[field]
                if not isinstance(value,str) or not value.strip() or len(value)>500 or len(value.splitlines())!=1:
                    raise ValueError()
        return target
    except (KeyError,AttributeError,TypeError,ValueError,OSError,RecursionError):
        raise ValueError('feishu_defect_config_invalid') from None


def _latest_attempts(batch):
    latest = {}
    for attempt in batch['case_attempts']:
        key = (attempt['case_id'],attempt['platform'],attempt['device_id'])
        if key not in latest or attempt['attempt_no'] > latest[key]['attempt_no']:
            latest[key] = attempt
    return [a for a in latest.values() if a['status']=='failed']


def _rule(target, attempt):
    if not (attempt.get('stop_confirmed') and attempt.get('job_id') and attempt.get('step_progress_known')):
        return None
    failed = [s['step_id'] for s in attempt['steps'] if s['status']=='failed']
    # Only compare full diagnostic lines against the operator's reviewed signature.
    # Raw stdout, prompts and the remainder of stderr never leave the workbench.
    from .agent_service import _agent_text_has_concrete_environment_evidence
    logs = attempt.get('result') or {}
    stderr = logs.get('stderr') or ''
    if _agent_text_has_concrete_environment_evidence(stderr + '\n' + (logs.get('stdout') or '')):
        return None
    lines = set(stderr.splitlines())
    matches = [rule for rule in target['rules'] if rule['case_id']==attempt['case_id'] and
               rule['case_revision'] == attempt['case_revision'] and failed == [rule['step_id']] and rule['diagnostic'] in lines]
    return matches[0] if len(matches)==1 else None


def _assigned_developer(target, requirement, headers):
    routing = target['developer_assignment']
    project_key = target['project_key']
    item = requirement['requirement_id']
    if requirement['source'] != 'feishu-project:' + project_key or not re.fullmatch(r'[1-9][0-9]{0,18}',item):
        return None
    kind = routing['requirement_type']
    reply = project.project_post(f'/open_api/{project_key}/work_item/{kind}/query', {
        'work_item_ids':[int(item)], 'fields':[routing['developer_field']]}, headers=headers)
    rows = reply.get('data')
    if not isinstance(rows,list) or len(rows)!=1 or not isinstance(rows[0],dict):
        return None
    row = rows[0]
    if (row.get('project_key'),row.get('work_item_type_key'),str(row.get('id'))) != (project_key,kind,item):
        return None
    from .feishu_requirement_sync import _value
    fields = row.get('fields')
    value = _value(fields,routing['developer_field']) if isinstance(fields,list) else None
    if isinstance(value,list) and len(value)==1:
        value = value[0]
    if not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',value):
        return None
    return {'field_key':routing['assignee_field'],'field_value':value}


def _deliver(db, target, key, title, description, requirement):
    db.execute('INSERT OR IGNORE INTO ui_defect_receipts(tracking_key,title,description,status) VALUES(?,?,?,?)',
               (key,title,description,'draft'))
    def receipt():
        return dict(db.execute('SELECT tracking_key,status,issue_id,detail FROM ui_defect_receipts WHERE tracking_key=?', (key,)).fetchone())
    prior = receipt()
    if prior['status']=='submitted':
        return prior
    def update(status, detail, issue_id=None):
        db.execute("UPDATE ui_defect_receipts SET status=?,detail=?,issue_id=? WHERE tracking_key=? AND status!='submitted'",
                   (status,detail,issue_id,key))
        return receipt()
    missing = project.missing_credentials()
    if missing:
        if prior['status']=='receipt_unknown':
            return prior
        db.execute("UPDATE ui_defect_receipts SET status='configuration_required',detail=? WHERE tracking_key=? AND status NOT IN ('submitted','receipt_unknown')",
                   (','.join(missing),key))
        return receipt()
    try:
        headers = project.project_headers()
    except project.ProjectUnavailable as exc:
        return {**receipt(), 'detail':str(exc)}
    assigned = []
    if prior['status'] != 'receipt_unknown' and 'developer_assignment' in target:
        try:
            owner_field = _assigned_developer(target,requirement,headers)
        except (project.ProjectRejected, project.ProjectUnavailable):
            owner_field = None
        if owner_field is None:
            return {**receipt(), 'status':'owner_unresolved', 'detail':'requirement_developer_unavailable'}
        assigned.append(owner_field)
    claimed = db.execute("UPDATE ui_defect_receipts SET status='receipt_unknown',detail='create_started' WHERE tracking_key=? AND status IN ('draft','configuration_required','rejected')",(key,)).rowcount
    prefix = '/open_api/'+target['project_key']+'/work_item/'
    try:
        if claimed:
            stored = db.execute('SELECT title,description FROM ui_defect_receipts WHERE tracking_key=?',(key,)).fetchone()
            fields = [{'field_key':target['evidence_field'],'field_value':stored['description']}]
            body = {'name':stored['title'], 'work_item_type_key':target['work_item_type_key'],
                    'required_mode':1, 'field_value_pairs':fields + assigned + target.get('field_value_pairs', [])}
            if 'template_id' in target:
                body['template_id'] = target['template_id']
            item_id = project.project_post(prefix+'create', body, headers=headers).get('data')
        elif receipt()['status']=='receipt_unknown':
            # A previous process may have crashed after committing remotely. Query only.
            stored_title = db.execute('SELECT title FROM ui_defect_receipts WHERE tracking_key=?',(key,)).fetchone()[0]
            reply = project.project_post(prefix+'filter', {'work_item_name':stored_title, 'work_item_type_keys':[target['work_item_type_key']],
                                         'page_num':1,'page_size':2}, headers=headers)
            rows = reply.get('data')
            item_id = None
            if isinstance(rows,list) and len(rows)==1 and isinstance(rows[0],dict) and rows[0].get('name')==stored_title and rows[0].get('work_item_type_key')==target['work_item_type_key']:
                item_id = rows[0].get('id')
        else:
            return receipt()
        if type(item_id) is int and 0 < item_id < 2**63:
            return update('submitted','',item_id)
        return receipt()
    except project.ProjectRejected as exc:
        return update('rejected',str(exc)) if claimed else receipt()
    except project.ProjectUnavailable:
        return receipt()


def _investigate(actor, batch_id):
    from . import failure_analysis_tasks as investigations
    try:
        task = investigations.latest(actor, 'ui', batch_id)
        if not task:
            request = str(uuid.uuid5(uuid.NAMESPACE_URL, 'requirement-defect-review:' + batch_id))
            task = investigations.start(actor, {'source':'ui', 'run_id':batch_id, 'request_id':request})
        return {key: task[key] for key in ('id', 'status')}
    except investigations.Busy:
        return {'status':'busy'}


def process_ui_batch(actor, batch_id):
    batch = get_ui_batch(actor,batch_id)
    if not batch or batch['owner_user_id']!=actor.get('user_id'):
        raise PermissionError('requirement_batch_owner_required')
    requirement = requirement_for_batch(batch)
    if not requirement or not batch['execution_settled']:
        return {'status':'ineligible','attempts':[]}
    attempts = _latest_attempts(batch)
    if not attempts:
        return {'status':'no_failed_attempts','attempts':[]}
    investigation = _investigate(actor, batch_id)
    target = _config(batch['app_id'])
    if not target or ((batch.get('selection') or {}).get('expected_build') and not target.get('developer_assignment')):
        return {'status':'configuration_required','detail':_CONFIG,'attempts':[], 'investigation':investigation}
    require_ui_actor(actor,permission='platform.notify',app_id=batch['app_id'])
    results = []
    with closing(_connect()) as db:
        for attempt in attempts:
            rule = _rule(target,attempt)
            if not rule:
                results.append({'attempt_id':attempt['attempt_id'],'status':'needs_evidence'})
                continue
            fingerprint = [batch['app_id'],requirement['source'],requirement['requirement_id'],requirement['revision'],
                           target['project_key'],target['work_item_type_key'],attempt['platform'],attempt['case_id'],
                           attempt['case_revision'],rule['step_id'],rule['diagnostic']]
            key = hashlib.sha256(json.dumps(fingerprint,ensure_ascii=False).encode()).hexdigest()
            title = f"{rule['title'][:100]} [QA-{key[:20]}]"
            description = '\n'.join((f"需求 {requirement['source']} / {requirement['requirement_id']} / {requirement['revision']}",
                f"平台 {attempt['platform']}，应用 {batch['app_id']}",f"用例 {attempt['case_id']}，版本 {attempt['case_revision']}",
                f"预期 {rule['expected']}",f"实际 {rule['diagnostic']}",f"失败步骤 {rule['step_id']}",
                f"运行 {batch_id}，尝试 {attempt['attempt_id']}，Runner 任务 {attempt['job_id']}",
                f"工作台 /task-manager.html?ui_batch={batch_id}"))
            build = (batch.get('selection') or {}).get('expected_build')
            if build:
                description += f"\nQA 构建 {build['build_id']}，{build['package']} {build['version_name']} ({build['version_code']})"
            results.append({'attempt_id':attempt['attempt_id'], **_deliver(db,target,key,title,description,requirement)})
    states = {r['status'] for r in results}
    return {'status':next(iter(states)) if len(states)==1 else 'mixed','attempts':results, 'investigation':investigation}


def batch_review(batch):
    """Expose only the status of an intake batch already authorized by its caller."""
    with closing(_connect()) as db:
        row = db.execute('SELECT result FROM ui_defect_reviews WHERE batch_id=?',(batch['ui_batch_id'],)).fetchone()
        return json.loads(row[0]) if row else None


def _next_batch(db):
    return db.execute('''SELECT b.ui_batch_id, b.owner_username, b.owner_user_id,
        (SELECT MAX(a.finished_ns) FROM case_attempts a WHERE a.ui_batch_id=b.ui_batch_id) AS last_finish
        FROM batches b LEFT JOIN ui_defect_reviews r ON r.batch_id=b.ui_batch_id
        WHERE b.execution_settled=1 AND b.idempotency_key GLOB 'requirement-*'
        AND (r.batch_id IS NULL OR r.finished!=last_finish OR r.retry_after<=?)
        ORDER BY COALESCE(r.retry_after,0),b.created_ns LIMIT 1''',(time.time_ns(),)).fetchone()


def _scan_one():
    with closing(_connect()) as db:
        row = _next_batch(db)
        if not row:
            return
        try:
            actor = actor_from_trusted_username(row['owner_username'])
            if actor.get('kind')!='user' or actor.get('user_id')!=row['owner_user_id']:
                raise PermissionError()
            with actor_context(actor):
                review = process_ui_batch(actor,row['ui_batch_id'])
        except PermissionError:
            review = {'status':'permission_required','attempts':[]}
        except ValueError:
            review = {'status':'configuration_required','detail':'feishu_defect_config_invalid','attempts':[]}
        states = {review['status'], *(a['status'] for a in review['attempts'])}
        analyzing = (review.get('investigation') or {}).get('status') in ('queued','running','busy')
        final = states <= {'submitted','no_failed_attempts','mixed'} and not analyzing
        # Recheck after restored credentials, corrected evidence rules, or a lost receipt.
        due = 2**63-1 if final else time.time_ns()+(60 if analyzing else 600)*10**9
        db.execute('INSERT OR REPLACE INTO ui_defect_reviews VALUES(?,?,?,?)',
                   (row['ui_batch_id'],row['last_finish'] or 0,due,json.dumps(review,ensure_ascii=False)))


def tick_defect_worker():
    global _WORKER
    if _WORKER and _WORKER.is_alive():
        return
    def run():
        try:
            _scan_one()
        except Exception:
            import logging
            logging.getLogger(__name__).error('Feishu Project defect scan unavailable')
    _WORKER = threading.Thread(target=run,name='feishu-project-defects',daemon=True)
    _WORKER.start()
