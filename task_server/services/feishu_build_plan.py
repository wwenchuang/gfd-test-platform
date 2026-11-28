"""Plan a reviewed QA smoke batch from the build receipt bound to a transferred story."""
import hashlib
import json

from . import feishu_development_design as development
from . import requirement_scripts, requirement_design, requirement_intake as intake


BUILD_FIELDS = {'project_key','requirement_id','revision','source_id','platform','environment',
                'build_id','package','version_name','version_code'}


def _check_build(cfg,value):
    if not isinstance(value,dict) or set(value)!=BUILD_FIELDS:
        raise ValueError('invalid_qa_build')
    for v in value.values():
        intake._identifier(v)
    if (value['environment']!='qa' or value['package']!=cfg['qa_packages'].get(value['platform'])
            or value['project_key']!=cfg['project_key']):
        raise ValueError('build_scope_mismatch')


def _inbox(db):
    db.execute("""CREATE TABLE IF NOT EXISTS feishu_qa_builds (
        owner TEXT, app_id TEXT, project TEXT, item TEXT, revision TEXT, platform TEXT,
        build_id TEXT, material TEXT NOT NULL,
        PRIMARY KEY(owner,app_id,project,item,revision,platform,build_id))""")


def record_build(cfg,value):
    # Packaging build numbers are monotonic per platform. Use them to resolve out-of-order webhooks.
    if not isinstance(value,dict):
        raise ValueError('invalid_qa_build')
    sequence=value.get('build_sequence')
    if type(sequence) is not int or not 0 < sequence < 2**63:
        raise ValueError('invalid_build_sequence')
    value={k:v for k,v in value.items() if k!='build_sequence'}
    _check_build(cfg,value)
    scope=(cfg['user_id'],cfg['app_id'],value['project_key'],value['requirement_id'],
           value['revision'],value['platform'],value['build_id'])
    material=intake._json({**value,'build_sequence':sequence})
    with requirement_design._db() as db:
        db.execute('BEGIN IMMEDIATE')
        _inbox(db)
        conflict=db.execute("""SELECT 1 FROM feishu_qa_builds WHERE owner=? AND app_id=? AND project=?
            AND item=? AND revision=? AND platform=? AND json_extract(material,'$.build_sequence')=? AND build_id!=?""",
            (*scope[:-1],sequence,scope[-1])).fetchone()
        if conflict:
            raise ValueError('build_receipt_conflict')
        db.execute('INSERT OR IGNORE INTO feishu_qa_builds VALUES (?,?,?,?,?,?,?,?)',(*scope,material))
        prior=db.execute("""SELECT material FROM feishu_qa_builds WHERE owner=? AND app_id=? AND
            project=? AND item=? AND revision=? AND platform=? AND build_id=?""",scope).fetchone()
        if prior['material']!=material:
            raise ValueError('build_receipt_conflict')


def build_context(cfg,item):
    if not isinstance(item,str) or not item.isascii() or not item.isdigit() or len(item)>20:
        raise ValueError('invalid_requirement_id')
    with intake._db() as db:
        row=db.execute("""SELECT revision,design_source,status FROM requirement_stage_progress WHERE
            owner=? AND source=? AND app_id=? AND requirement_id=? AND stage='development_design'""",
            (cfg['user_id'],'feishu-project:'+cfg['project_key'],cfg['app_id'],item)).fetchone()
        if not row or row['status']!='design_completed':
            raise ValueError('design_not_ready')
    return dict(project_key=cfg['project_key'],requirement_id=item,revision=row['revision'],source_id=row['design_source'])


def current_builds(cfg,event):
    with requirement_design._db() as db:
        _inbox(db)
        rows=db.execute("""SELECT material FROM feishu_qa_builds WHERE owner=? AND app_id=? AND
            project=? AND item=? AND revision=? ORDER BY json_extract(material,'$.build_sequence') DESC""",
            (cfg['user_id'],cfg['app_id'],cfg['project_key'],event['requirement_id'],event['revision']))
        found={}
        for row in rows:
            data=json.loads(row['material'])
            data.pop('build_sequence',None)
            found.setdefault(data['platform'],data)
    return list(found.values())


def _frozen_plan(cfg,event,sid,platform,revision,design,build,*,use_bindings=True):
    scope=(cfg['user_id'],cfg['app_id'],revision)
    with requirement_design._db() as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("""CREATE TABLE IF NOT EXISTS feishu_qa_plans (
            owner TEXT, app TEXT, revision TEXT, plan TEXT NOT NULL, PRIMARY KEY(owner,app,revision))""")
        row=db.execute('SELECT plan FROM feishu_qa_plans WHERE owner=? AND app=? AND revision=?',scope).fetchone()
        if row:
            return json.loads(row['plan'])
        _,source=requirement_design._source(db,cfg['user_id'],sid)
        coverage={'title':source['title'], 'clauses':[c['text'] for c in source['clauses']],
                  'frames':[{'file_key':r['file_key'],'node_id':r['node_id'],'sha256':p['sha256']}
                            for r,p in zip(source.get('figma_refs',[]),source['prototypes'])]}
        mapped=dict(intake._binding({**event,'revision':sid},None,release_plan=True,coverage=coverage)) if use_bindings else {}
        if platform in mapped:
            smoke,remaining=mapped[platform]
            plan={'smoke':smoke,'remaining':remaining}
        else:
            plan = requirement_scripts.build_plan(cfg, design, sid, platform, revision)
            if 'status' in plan:
                return plan
            plan['build'] = build
        db.execute('INSERT INTO feishu_qa_plans VALUES (?,?,?,?)',(*scope,intake._json(plan)))
        return plan


def _smoke_gate(batch,request):
    if not batch or not batch.get('execution_settled'):
        return 'smoke_running'
    if batch['status'] not in ('passed','failed'):
        return 'smoke_blocked'
    # Only original attempts on the frozen device and published revisions count.
    attempts=[a for a in batch['case_attempts'] if a['attempt_no']==1]
    expected={(c['case_id'],c['revision']) for c in request['cases']}
    if (len(attempts)!=len(expected) or {(a['case_id'],a['case_revision']) for a in attempts}!=expected
            or any(a['status'] not in ('passed','failed') for a in attempts)):
        return 'smoke_blocked'
    return 'continue' if 2*sum(a['status']=='passed' for a in attempts)>=len(expected) else 'smoke_blocked'


def submit_build(cfg, event, link, value, *, current_source=None):
    if not value:
        return {'status':'awaiting_qa_build'}
    if isinstance(value,str) and len(value) < 4096:
        try:
            value = json.loads(value)
        except ValueError:
            raise ValueError('invalid_qa_build') from None
    _check_build(cfg,value)
    platform = value['platform']
    if (value['revision'] != event['revision']
            or value['requirement_id'] != event['requirement_id']):
        raise ValueError('build_scope_mismatch')
    sid = current_source or development.store_source(cfg,event,link)
    if value['source_id'] != sid:
        return {'status':'design_changed_since_build'}
    with requirement_design._db() as db:
        design = development.last_design(db,cfg['user_id'],sid)
    if not design or design['status'] != 'completed' or not json.loads(design['result']).get('cases'):
        return {'status':'awaiting_design'}
    return submit_design(cfg,event,sid,platform,value,json.loads(design['result']),origin='integration')


def submit_design(cfg,event,sid,platform,value,design,*,origin):
    # Pin reviewed cases to this exact design, never to a broad scenario fallback.
    revision = hashlib.sha256(intake._json(value).encode()).hexdigest()
    build = {k:value[k] for k in ('package','version_name','version_code','environment','build_id')}
    plan = _frozen_plan(cfg,event,sid,platform,revision,design,build,use_bindings=origin=='integration')
    if 'status' in plan:
        return {**plan,'source_id':sid}
    request = {**plan['smoke'], 'expected_build':build}
    def submit_phase(phase,cases):
        run_revision = hashlib.sha256(f'{revision}/{phase}'.encode()).hexdigest()
        context=dict(requirement_revision=event['revision'],source_id=sid,phase=phase,platform=platform,**build)
        if plan.get('assets'):
            context.update(script_origin='requirement',generated_count=len(plan['assets']),deferred=plan['deferred'])
        with intake._db() as db:
            db.execute('INSERT OR IGNORE INTO requirement_build_context VALUES (?,?,?,?)',
                       (cfg['user_id'],cfg['app_id'],run_revision,intake._json(context)))
        material = {**event,'event_id':event['requirement_id']+'.'+run_revision,'revision':run_revision}
        token = requirement_scripts.active_assets.set(plan.get('assets'))
        try:
            return intake.submit(material,origin=origin,build_plan=[(platform,{**request,'cases':cases})])
        finally:
            requirement_scripts.active_assets.reset(token)
    smoke = submit_phase('smoke',request['cases'])
    if not plan['remaining'] or smoke['status']!='submitted':
        return smoke
    batch = intake.execution.get_ui_batch(intake.current_actor(),smoke['batches'][0]['ui_batch_id'])
    gate = _smoke_gate(batch,request)
    if gate!='continue':
        return {**smoke,'status':gate}
    return submit_phase('remaining',plan['remaining'])
