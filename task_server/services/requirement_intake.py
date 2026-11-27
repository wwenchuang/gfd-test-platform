"""Receive versioned business requirements and submit mapped workbench smoke batches."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import time

from . import requirement_design, ui_execution_service as execution
from .operation_attribution import current_actor
from .ui_execution_contract import AccessDenied, require_ui_actor
from .ui_execution_store import UiExecutionStore, default_db_path

IDS = ('source', 'requirement_id', 'revision', 'app_id')
IDENTIFIER = re.compile(r'[a-zA-Z0-9_.:-]{1,128}')


class Conflict(ValueError):
    pass


class MappingUnavailable(ValueError):
    pass


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('需求、来源、版本和事件编号仅支持字母、数字及 _.:-')
    return value


def _event(body):
    if not isinstance(body, dict) or set(body) != {*IDS, 'event_id', 'title', 'acceptance_criteria'}:
        raise ValueError('请提供来源、事件编号、应用、需求编号、版本、标题及验收条件')
    event = {k: _identifier(body[k]) for k in (*IDS, 'event_id')}
    clauses = body['acceptance_criteria']
    if not isinstance(clauses, list) or not 1 <= len(clauses) <= 20:
        raise ValueError('请提供 1 至 20 条验收条件')
    event['acceptance_criteria'] = [requirement_design._text(clause, 900) for clause in clauses]
    event['title'] = requirement_design._text(body['title'], 160)
    return event


def _json(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(',', ':'))


@contextmanager
def _db():
    with requirement_design._db() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS requirement_intakes (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, material TEXT NOT NULL, origin TEXT NOT NULL,
                app_id TEXT NOT NULL, created REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS requirement_owner_time ON requirement_intakes(owner,created DESC);
            CREATE INDEX IF NOT EXISTS requirement_source_time ON requirement_intakes(origin,app_id,created DESC);
            CREATE TABLE IF NOT EXISTS requirement_events (
                owner TEXT, source TEXT, event_id TEXT, intake_id TEXT NOT NULL,
                PRIMARY KEY(owner,source,event_id));
            CREATE INDEX IF NOT EXISTS requirement_event_intake ON requirement_events(intake_id);
            CREATE TABLE IF NOT EXISTS requirement_build_context (
                owner TEXT, app_id TEXT, execution_revision TEXT, context TEXT NOT NULL,
                PRIMARY KEY(owner,app_id,execution_revision));
            CREATE TABLE IF NOT EXISTS requirement_stage_progress (
                owner TEXT, source TEXT, app_id TEXT, requirement_id TEXT, stage TEXT, title TEXT,
                revision TEXT, status TEXT, design_source TEXT, changed REAL,
                PRIMARY KEY(owner,source,app_id,requirement_id,stage));
            CREATE TABLE IF NOT EXISTS requirement_handoffs (
                intake_id TEXT, platform TEXT, request TEXT NOT NULL, batch_id TEXT UNIQUE, blocker TEXT,
                PRIMARY KEY(intake_id,platform));
        """)
        yield db


def _binding(material, scenario, *, release_plan=False, coverage=None):
    path = os.getenv('MIDSCENE_REQUIREMENT_BINDINGS')
    if not path:
        return []
    try:
        with Path(path).open('rb') as stream:
            raw = stream.read(1024 * 1024 + 1)
        if len(raw) > 1024 * 1024:
            raise ValueError()
        mappings = json.loads(raw)
        if not isinstance(mappings, list):
            raise ValueError()
        matched = [row for row in mappings if all(row.get(k) == material[k] for k in IDS)]
        if not matched and release_plan and coverage:
            matched = [row for row in mappings if isinstance(row,dict) and row.get('coverage')==coverage
                       and row.get('source')==material['source'] and row.get('app_id')==material['app_id']
                       and 'revision' not in row and 'requirement_id' not in row]
        if not matched and scenario:
            matched = [row for row in mappings if row.get('scenario') == scenario and
                       row.get('source') == material['source'] and row.get('app_id') == material['app_id'] and
                       'requirement_id' not in row and 'revision' not in row]
        if not matched:
            return []
        if len(matched) != 1:
            raise ValueError()
        runs = matched[0]['runs']
        if not isinstance(runs, list) or not 1 <= len(runs) <= 2:
            raise ValueError()
        requests = {}
        for run in runs:
            keys = {'cases','target','execution_policy'}
            if not isinstance(run,dict) or set(run) not in (keys,keys|{'remaining_cases'} if release_plan else keys) or run['execution_policy'] != 'reviewed_non_printing':
                raise ValueError()
            request = dict(app_id=material['app_id'], cases=run['cases'], targets=[run['target']],
                           notifications={'enabled':False}, release_report={'enabled':True})
            normalized = execution._normalize_request(request)
            platform = normalized['targets'][0]['platform']
            if platform in requests or len(normalized['cases']) > 3:
                raise ValueError()
            if release_plan:
                remaining = run.get('remaining_cases',[])
                if not isinstance(remaining,list):
                    raise ValueError()
                if remaining:
                    full = {**request,'cases':run['cases']+remaining}
                    execution._normalize_request(full)  # validates distinct cases and published revision format
                requests[platform] = (request,remaining)
            else:
                requests[platform] = request
        return requests.items()
    except (ValueError, KeyError, OSError, TypeError, AttributeError, RecursionError) as exc:
        raise MappingUnavailable('需求对应的冒烟映射配置不可用') from exc


def _record(owner, event, origin):
    material = {k:v for k,v in event.items() if k != 'event_id'}
    rid = hashlib.sha256(_json([owner, *[material[k] for k in IDS]]).encode()).hexdigest()
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute('SELECT material FROM requirement_intakes WHERE id=?', (rid,)).fetchone()
        delivery = db.execute('SELECT intake_id FROM requirement_events WHERE owner=? AND source=? AND event_id=?',
                              (owner, event['source'], event['event_id'])).fetchone()
        if (prior and prior['material'] != _json(material)) or (delivery and delivery['intake_id'] != rid):
            raise Conflict('该事件或需求版本已登记不同内容，请检查事件编号与需求版本')
        db.execute('INSERT OR IGNORE INTO requirement_intakes VALUES (?,?,?,?,?,?)',
                   (rid, owner, _json(material), origin, material['app_id'], time.time()))
        if origin == 'integration' and prior:
            db.execute("UPDATE requirement_intakes SET origin='integration' WHERE id=? AND origin!='integration'", (rid,))
        db.execute('INSERT OR IGNORE INTO requirement_events VALUES (?,?,?,?)',
                   (owner, event['source'], event['event_id'], rid))
    return rid


def _runs(rid, event, scenario, build_plan=None):
    with _db() as db:
        db.execute('BEGIN IMMEDIATE')
        rows = db.execute('SELECT * FROM requirement_handoffs WHERE intake_id=? ORDER BY platform', (rid,)).fetchall()
        if not rows:
            db.executemany('INSERT INTO requirement_handoffs (intake_id,platform,request) VALUES (?,?,?)',
                           [(rid, plat, _json(req)) for plat, req in (_binding(event, scenario) if build_plan is None else build_plan)])
            rows = db.execute('SELECT * FROM requirement_handoffs WHERE intake_id=? ORDER BY platform', (rid,)).fetchall()
        return rows


def _submit_run(actor, rid, row):
    if row['batch_id']:
        return
    request = json.loads(row['request'])
    platform = row['platform']
    key = 'requirement-' + rid + '-' + platform
    prior = (default_db_path().exists() and UiExecutionStore().find_idempotent(actor['user_id'], key))
    batch_id = blocker = None
    try:
        check = None if prior else execution.preflight_ui_batch(actor, request)
        if check is not None and not check['ready']:
            blocker = check['blockers'][0]['code']
        else:
            # The existing service compares the frozen request hash even during crash recovery.
            batch = execution.create_ui_batch(actor, request, key)
            batch_id = batch['ui_batch_id']
    except execution.BatchValidationError as exc:
        blocker = exc.code
    with _db() as db:
        db.execute('UPDATE requirement_handoffs SET batch_id=?,blocker=? WHERE intake_id=? AND platform=? AND batch_id IS NULL',
                   (batch_id, blocker, rid, platform))


def submit(body, *, origin='workbench', scenario=None, build_plan=None):
    event = _event(body)
    if origin == 'workbench' and event['source'].lower().startswith('feishu-project:'):
        raise ValueError('飞书项目来源由同步任务登记，请为手工录入选择工作台来源')
    actor = current_actor()
    for permission in ('ui.view', 'ui.execute'):
        require_ui_actor(actor, permission=permission, app_id=event['app_id'])
    rid = _record(actor['user_id'], event, origin)
    for row in _runs(rid, event, scenario, build_plan):
        _submit_run(actor, rid, row)
    return read(event['source'], event['event_id'])


def read(source, event_id):
    actor = current_actor()
    require_ui_actor(actor, permission='ui.view')
    with _db() as db:
        row = db.execute('''SELECT i.* FROM requirement_intakes i JOIN requirement_events e ON e.intake_id=i.id
                            WHERE e.owner=? AND e.source=? AND e.event_id=?''',
                         (actor['user_id'], _identifier(source), _identifier(event_id))).fetchone()
        if not row:
            return None
        return _receipt(db, row, actor, event_id, private=True)


def record_stage(event, stage, result):
    actor = current_actor()
    require_ui_actor(actor,permission='ui.view',app_id=event['app_id'])
    with _db() as db:
        if stage.startswith('qa_build_smoke.'):
            db.execute("""DELETE FROM requirement_stage_progress WHERE owner=? AND source=? AND app_id=?
                AND requirement_id=? AND stage='qa_build_smoke'""",
                (actor['user_id'],event['source'],event['app_id'],event['requirement_id']))
        db.execute("""INSERT INTO requirement_stage_progress VALUES (?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(owner,source,app_id,requirement_id,stage) DO UPDATE SET
            title=excluded.title, revision=excluded.revision,status=excluded.status,
            design_source=excluded.design_source,changed=excluded.changed WHERE
            title!=excluded.title OR revision!=excluded.revision OR status!=excluded.status
            OR design_source!=excluded.design_source""",
            (actor['user_id'],event['source'],event['app_id'],event['requirement_id'],stage,
             event['title'],event['revision'],result['status'],result.get('source_id',''),time.time()))


def recent(limit=10):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValueError('每次可查看 1 至 50 条需求')
    actor = current_actor()
    profile = require_ui_actor(actor, permission='ui.view')
    apps = (profile.get('scope') or {}).get('ui_apps') or []
    params = [actor['user_id']]
    condition = "(i.owner=? OR i.origin='integration')"
    if apps != '*':
        if not apps:
            return []
        condition += ' AND i.app_id IN (' + ','.join('?' for _ in apps) + ')'
        params.extend(apps)
    with _db() as db:
        rows = db.execute('''SELECT i.*, (SELECT e.event_id FROM requirement_events e
                             WHERE e.intake_id=i.id ORDER BY e.rowid LIMIT 1) AS event_id
                             FROM requirement_intakes i WHERE ''' + condition +
                          ' ORDER BY i.created DESC LIMIT ?', (*params, limit)).fetchall()
        receipts = [_receipt(db, row, actor, row['event_id'], private=bool(
                row['owner'] == actor['user_id'] or profile.get('is_superuser'))) for row in rows]
        where = '' if apps=='*' else 'WHERE app_id IN ('+','.join('?' for _ in apps)+')'
        stages = db.execute('SELECT * FROM requirement_stage_progress '+where+' ORDER BY changed DESC LIMIT ?',
                            (*(apps if apps!='*' else []),limit)).fetchall()
        for stage in stages:
            # Execution receipts already link to the actual batch when submission succeeds.
            if stage['stage'].startswith('qa_build_smoke') and stage['status'] in ('submitted','blocked','partial'):
                continue
            item={key:stage[key] for key in ('source','app_id','requirement_id','stage','title','revision','status')}
            if stage['stage']=='development_design' and stage['status']=='design_completed':
                from .feishu_development_design import last_design
                row=last_design(db,stage['owner'],stage['design_source'])
                if row and row['result']:
                    result=json.loads(row['result'])
                    item['design_cases']=[{k:c[k] for k in ('title','precondition','steps','expected','point_ids','plan') if k in c}
                                          for c in result.get('cases',[])]
            if stage['owner']==actor['user_id']:
                item['design_source']=stage['design_source']
            receipts.append({**item,'origin':'integration','created_at':stage['changed'],'batches':[],'blockers':[]})
        return sorted(receipts,key=lambda item:item['created_at'],reverse=True)[:limit]


def _receipt(db, row, actor, event_id, *, private):
    event = json.loads(row['material'])
    require_ui_actor(actor, permission='ui.view', app_id=event['app_id'])
    runs = db.execute('SELECT * FROM requirement_handoffs WHERE intake_id=? ORDER BY platform', (row['id'],)).fetchall()
    batches, blockers = [], []
    for run in runs:
        if run['batch_id'] and not private:
            batches.append({'platform':run['platform']})
            continue
        if run['batch_id']:
            batch = execution.get_ui_batch(actor, run['batch_id'])
            if batch:
                from .feishu_project_defects import batch_review
                batches.append({'platform':run['platform'], 'ui_batch_id':run['batch_id'], 'status':batch['status'],
                                'defect_review':batch_review(batch) if batch['execution_settled'] else None})
                continue
        blockers.append({'platform':run['platform'], **({'code':run['blocker'] or 'handoff_pending'} if private else {})})
    if not runs:
        status = 'awaiting_mapping'
    elif not blockers:
        status = 'submitted'
    else:
        status = 'partial' if batches else 'blocked'
    saved=db.execute('SELECT context FROM requirement_build_context WHERE owner=? AND app_id=? AND execution_revision=?',
                     (row['owner'],row['app_id'],event['revision'])).fetchone()
    if saved:
        event['build_context']=json.loads(saved[0])
    return {**event, 'origin':row['origin'], 'created_at':row['created'], 'event_id':event_id, 'status':status, 'batches':batches, 'blockers':blockers}


def design_source(source, event_id, app_id):
    """Save the accessible intake's original clauses in the designer's private source library."""
    actor = current_actor()
    require_ui_actor(actor, permission='ui.view', app_id=_identifier(app_id))
    owner = actor['user_id']
    if not requirement_design.authorized(owner, actor['username']):
        raise AccessDenied('design_forbidden')
    with _db() as db:
        rows = db.execute("""SELECT DISTINCT i.material FROM requirement_events e JOIN requirement_intakes i
                          ON i.id=e.intake_id WHERE e.source=? AND e.event_id=? AND i.app_id=?
                          AND (i.owner=? OR i.origin='integration') LIMIT 2""",
                          (_identifier(source), _identifier(event_id), app_id, owner)).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise Conflict('需求事件对应多个版本，请核对来源')
    material = json.loads(rows[0]['material'])
    clauses = material['acceptance_criteria']
    meta = dict(source_type='excerpt', title=material['title'], app=app_id, version=material['revision'],
                prd_file_sha256=hashlib.sha256('\n'.join(clauses).encode()).hexdigest(),
                clauses=[{'id':'P' + str(i), 'text':clause} for i, clause in enumerate(clauses, 1)],
                prototypes=[], requirement_ref={k: material[k] for k in IDS})
    return requirement_design._store(owner, meta, {})


def requirement_for_batch(batch):
    """Return only the persisted requirement whose handoff recorded this batch."""
    if not str(batch.get('idempotency_key') or '').startswith('requirement-'):
        return None
    with _db() as db:
        row = db.execute('''SELECT i.material,i.origin FROM requirement_handoffs h
                            JOIN requirement_intakes i ON h.intake_id=i.id WHERE h.batch_id=? AND i.owner=?''',
                         (batch.get('ui_batch_id'), batch.get('owner_user_id'))).fetchone()
        return {**json.loads(row['material']), 'origin':row['origin']} if row else None
