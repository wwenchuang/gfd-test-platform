"""Private immutable reports, built exclusively from one frozen execution batch.

All artifact bytes live beside execution state in its private SQLite database.
Nothing in this module writes the public REPORT_DIR or follows Runner URLs.
"""
import hashlib
import hmac
import json
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

from .ui_execution_store import UiExecutionStore, _json
from .ui_report_presentation import build_presentation, render_markdown
import yaml

MIMES = {'md': 'text/markdown; charset=utf-8', 'html': 'text/html; charset=utf-8',
         'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
         'png': 'image/png', 'jpg': 'image/jpeg'}
_BUILD_LOCK = threading.Lock()
_WORKER = None
STATUS_LABELS = {"passed":"通过", "success":"通过", "failed":"失败", "cancelled":"已取消",
                 "interrupted":"执行中断", "blocked":"被阻断", "queued":"排队中",
                 "pending":"未执行", "running":"执行中", "cancelling":"取消中", "skipped":"已跳过"}
_SCHEMA_LOCK = threading.Lock()


def _connect():
    db = UiExecutionStore()._connect()
    with _SCHEMA_LOCK:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS ui_report_snapshots (
            ui_batch_id TEXT NOT NULL REFERENCES batches(ui_batch_id), report_revision TEXT NOT NULL,
            snapshot_json TEXT NOT NULL, created_ns INTEGER NOT NULL, status TEXT NOT NULL,
            error TEXT NOT NULL DEFAULT '', markdown BLOB, html BLOB, docx BLOB,
            PRIMARY KEY(ui_batch_id, report_revision));
        CREATE TABLE IF NOT EXISTS ui_report_heads (
            ui_batch_id TEXT PRIMARY KEY REFERENCES batches(ui_batch_id), report_revision TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS ui_report_artifacts (
            ui_batch_id TEXT NOT NULL REFERENCES batches(ui_batch_id), artifact_id TEXT NOT NULL,
            kind TEXT NOT NULL, content BLOB NOT NULL, PRIMARY KEY(ui_batch_id,artifact_id));
        CREATE TABLE IF NOT EXISTS ui_report_evidence (
            ui_batch_id TEXT NOT NULL, attempt_id TEXT NOT NULL, artifact_id TEXT NOT NULL,
            created_ns INTEGER NOT NULL, PRIMARY KEY(attempt_id,artifact_id));
        CREATE INDEX IF NOT EXISTS ui_evidence_batch ON ui_report_evidence(ui_batch_id,created_ns);
        CREATE INDEX IF NOT EXISTS ui_reports_pending ON batches(created_ns)
            WHERE execution_settled=1 AND report_status='pending' AND report_requested_ns IS NOT NULL;
        CREATE TABLE IF NOT EXISTS ui_report_chunks (
            job_id TEXT NOT NULL, upload_id TEXT NOT NULL, part INTEGER NOT NULL,
            total INTEGER NOT NULL, content BLOB NOT NULL, created_ns INTEGER NOT NULL,
            PRIMARY KEY(job_id,upload_id,part));
        CREATE INDEX IF NOT EXISTS ui_report_chunks_expiry ON ui_report_chunks(created_ns);
        ''')
    return db


def _batch(actor, batch_id):
    from .ui_execution_service import get_ui_batch
    batch = get_ui_batch(actor,batch_id)
    if not batch:
        raise FileNotFoundError('batch_not_found')
    return batch


def _validate_ui_upload_in_db(db, job_id, credentials):
    row = db.execute('SELECT * FROM case_attempts WHERE job_id=?',(job_id,)).fetchone()
    if (not row or not row['claim_token'] or
            not hmac.compare_digest(row['claim_token'],str(credentials.get('ui_claim_token') or '')) or
            credentials.get('runner_id') != row['runner_id'] or credentials.get('device_id') != row['device_id']):
        raise PermissionError('claim_mismatch')
    return dict(row)


def validate_ui_upload(job_id, credentials):
    """The machine token alone cannot assign evidence to a user's batch."""
    db = _connect()
    try:
        return _validate_ui_upload_in_db(db,job_id,credentials)
    finally:
        db.close()


def _artifact_metadata(batch_id, artifact_id, kind, attempt_id=None):
    return {'artifact_id':artifact_id,'kind':kind,'attempt_id':attempt_id,
            'url':f'/api/ui-executions/{batch_id}/artifacts/{artifact_id}'}


def save_ui_artifact(job_id, credentials, content, kind):
    from task_server.config import MAX_UPLOAD_BODY_SIZE
    row = validate_ui_upload(job_id,credentials)
    if kind not in {'html','png','jpg'} or not content or len(content) > MAX_UPLOAD_BODY_SIZE:
        raise ValueError('artifact_invalid')
    # The media type is part of identity; identical bytes cannot change MIME later.
    artifact_id = hashlib.sha256(kind.encode()+b'\0'+content).hexdigest()
    db = _connect()
    try:
        db.execute('BEGIN IMMEDIATE')
        row = _validate_ui_upload_in_db(db,job_id,credentials)
        db.execute('INSERT OR IGNORE INTO ui_report_artifacts VALUES(?,?,?,?)',
                   (row['ui_batch_id'],artifact_id,kind,content))
        inserted = db.execute('INSERT OR IGNORE INTO ui_report_evidence VALUES(?,?,?,?)',
                             (row['ui_batch_id'],row['attempt_id'],artifact_id,time.time_ns())).rowcount
        if kind == 'html':
            db.execute('DELETE FROM ui_report_chunks WHERE job_id=?',(job_id,))
        if inserted:
            db.execute("UPDATE batches SET report_status='pending' WHERE ui_batch_id=? AND report_requested_ns IS NOT NULL",(row['ui_batch_id'],))
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise
    finally:
        db.close()
    return _artifact_metadata(row['ui_batch_id'],artifact_id,kind,row['attempt_id'])


def read_ui_artifact(actor,batch_id,artifact_id):
    _batch(actor,batch_id)
    db = _connect()
    try:
        row = db.execute('SELECT kind,content FROM ui_report_artifacts WHERE ui_batch_id=? AND artifact_id=?',
                         (batch_id,artifact_id)).fetchone()
        if not row:
            raise FileNotFoundError('artifact_not_found')
        content = bytes(row['content'])
        if row['kind'] == 'html':
            from .ui_report_html import inert_evidence_html
            content = inert_evidence_html(content)
        label = '原始报告静态内容' if row['kind'] == 'html' else '执行截图'
        return content,MIMES[row['kind']],f"{label}-{artifact_id[:12]}.{row['kind']}"
    finally:
        db.close()


def read_ui_replay(actor, batch_id, artifact_id):
    _batch(actor, batch_id)
    db = _connect()
    try:
        row = db.execute('SELECT kind,content FROM ui_report_artifacts WHERE ui_batch_id=? AND artifact_id=?',
                         (batch_id, artifact_id)).fetchone()
        if not row or row['kind'] != 'html':
            raise FileNotFoundError('artifact_not_found')
        from .ui_report_replay import build_replay_document
        return {'document': build_replay_document(bytes(row['content']))}
    finally:
        db.close()


def _evidence(db,batch_id):
    return [_artifact_metadata(batch_id,r['artifact_id'],r['kind'],r['attempt_id']) for r in db.execute(
        '''SELECT e.artifact_id,e.attempt_id,a.kind FROM ui_report_evidence e JOIN ui_report_artifacts a
        ON a.ui_batch_id=e.ui_batch_id AND a.artifact_id=e.artifact_id WHERE e.ui_batch_id=?
        ORDER BY e.attempt_id,e.artifact_id''',(batch_id,))]


def get_ui_batch_evidence(actor,batch_id):
    """Read the private manifest without rendering a report or reading its blobs."""
    _batch(actor,batch_id)
    db = _connect()
    try:
        return {'ui_batch_id':batch_id,'artifacts':_evidence(db,batch_id)}
    finally:
        db.close()


def _expectations(source):
    # Read only assertions from the frozen source; never manufacture expected outcomes.
    data = yaml.safe_load(source) or {}
    result = []
    for task in data.get('tasks') or []:
        for step in task.get('flow') or []:
            if isinstance(step, dict) and isinstance(step.get('aiAssert'), str):
                value = step['aiAssert'].strip()
                if value and value not in result:
                    result.append(value)
    return result


def _snapshot(batch,db):
    snapshots = {s['snapshot_id']:s for s in batch['snapshots']}
    attempts = []
    for attempt in batch['case_attempts']:
        source = snapshots[attempt['snapshot_id']]
        attempts.append({**{k:attempt[k] for k in ('attempt_id','case_id','case_revision','attempt_no','job_id',
            'platform','runner_id','device_id','status','started_ns','finished_ns','block_code','block_message',
            'execution_progress','steps','result','manual_stop_recovery') if k in attempt},
            'source_hash':source['source_hash'],'metadata':source['metadata'],
            'device_snapshot':attempt['target_resolution'].get('device_snapshot',{}),
            'expectations':_expectations(source['source_yaml'])})
    counts = dict(Counter(a['status'] for a in attempts))
    app_name = next((s['metadata'].get('app_name') for s in batch['snapshots'] if s['metadata'].get('app_name')),batch['app_id'])
    snapshot = {**{k:batch[k] for k in ('ui_batch_id','owner_user_id','owner_display_name','app_id','suite_id',
                'suite_revision','created_ns','started_ns','finished_ns','status')},
            'app_name':app_name,'selection':batch['selection'],'attempts':attempts,'counts':counts,
            'artifacts':_evidence(db,batch['ui_batch_id']),
            'cumulative_bugs':None,'cumulative_bugs_note':'历史累计缺陷未纳入本批结论。'}
    snapshot['presentation'] = build_presentation(snapshot)
    snapshot['summary'] = snapshot['presentation']['summary']
    return snapshot


def _date(value):
    return time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(value/10**9)) if value else '未执行'


def _filename(snapshot):
    title = (snapshot.get('presentation') or build_presentation(snapshot))['title']
    parts = [title, snapshot['owner_display_name'], _date(snapshot['created_ns']).replace(':','')]
    return '_'.join(re.sub(r'[\\/:*?"<>|\x00-\x1f]','_',str(p))[:80] for p in parts)


def _markdown(snapshot):
    return render_markdown(snapshot.get('presentation') or build_presentation(snapshot))


def _render_docx(markdown):
    # Process timeout also terminates a wedged parser; no accumulating stuck threads.
    return subprocess.run([sys.executable,'-c',
        'import sys; from task_server.services.report_word_service import render_report_docx; '
        'sys.stdout.buffer.write(render_report_docx(sys.stdin.buffer.read().decode("utf-8"), release=True))'],
        input=markdown.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=True,
        cwd=str(Path(__file__).resolve().parents[2])).stdout


def _report_delivery_state(db,batch,revision):
    """Ready revisions need discovery once; previously queued/sent digests keep their state."""
    policy=(batch.get('report_request') or {}).get('notifications') or {}
    if not policy.get('enabled'): return 'not_requested'
    row=db.execute('SELECT status FROM delivery_outbox WHERE ui_batch_id=? AND report_revision=? AND channel_id=?',
                   (batch['ui_batch_id'],revision,policy.get('channel_id','unconfigured'))).fetchone()
    return ('queued' if row[0]=='pending' else row[0]) if row else 'pending'


def _build(batch_id):
    db = _connect()
    try:
        # Freeze rows and manifest under one short write lock, never across rendering.
        db.execute('BEGIN IMMEDIATE')
        batch = UiExecutionStore().get_batch(batch_id)
        if not batch or not batch['execution_settled'] or batch['stop_pending']:
            raise ValueError('execution_not_settled')
        if batch['report_requested_ns'] is None:
            raise ValueError('report_not_requested')
        snapshot = _snapshot(batch,db)
        revision = hashlib.sha256(_json(snapshot).encode()).hexdigest()
        existing = db.execute('SELECT status FROM ui_report_snapshots WHERE ui_batch_id=? AND report_revision=?',(batch_id,revision)).fetchone()
        if existing and existing['status'] == 'ready':
            db.execute('INSERT OR REPLACE INTO ui_report_heads VALUES(?,?)',(batch_id,revision))
            db.execute("UPDATE batches SET report_status='ready',delivery_status=? WHERE ui_batch_id=?",(_report_delivery_state(db,batch,revision),batch_id))
            db.execute('COMMIT')
            return _report(db,batch_id,revision)
        db.execute("INSERT OR IGNORE INTO ui_report_snapshots(ui_batch_id,report_revision,snapshot_json,created_ns,status) VALUES(?,?,?,?, 'pending')",
                   (batch_id,revision,_json(snapshot),time.time_ns()))
        db.execute('INSERT OR REPLACE INTO ui_report_heads VALUES(?,?)',(batch_id,revision))
        db.execute('COMMIT')
        try:
            from .test_report_service import _markdown_to_html
            markdown = _markdown(snapshot)
            docx = _render_docx(markdown)
            from .ui_report_html import protect_export_html
            html = protect_export_html(_markdown_to_html(markdown,_filename(snapshot)))
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE ui_report_snapshots SET status='ready',error='',markdown=?,html=?,docx=? WHERE ui_batch_id=? AND report_revision=?",
                       (markdown.encode(),html.encode(),docx,batch_id,revision))
            # Evidence may have arrived while rendering. Leave pending for next version.
            current = _snapshot(UiExecutionStore().get_batch(batch_id),db)
            state = 'ready' if hashlib.sha256(_json(current).encode()).hexdigest() == revision else 'pending'
            db.execute('UPDATE batches SET report_status=?,delivery_status=? WHERE ui_batch_id=?',(state,_report_delivery_state(db,batch,revision),batch_id))
            db.execute('COMMIT')
        except Exception:
            if db.in_transaction: db.execute('ROLLBACK')
            db.execute("UPDATE ui_report_snapshots SET status='failed',error='report_render_failed' WHERE ui_batch_id=? AND report_revision=?",(batch_id,revision))
            db.execute("UPDATE batches SET report_status='failed' WHERE ui_batch_id=?",(batch_id,))
        return _report(db,batch_id,revision)
    finally:
        db.close()


def build_ui_batch_report(actor,batch_id):
    # Synchronous callers are explicit requests too; the automatic worker calls
    # _build directly and can never grant itself report-generation intent.
    request_ui_batch_report(actor,batch_id)
    with _BUILD_LOCK:
        return _build(batch_id)


def _report(db,batch_id,revision=None):
    explicit_revision = revision is not None
    if revision is None:
        head = db.execute('SELECT report_revision FROM ui_report_heads WHERE ui_batch_id=?',(batch_id,)).fetchone()
        revision = head[0] if head else ''
    row = db.execute('SELECT report_revision,snapshot_json,status,error FROM ui_report_snapshots WHERE ui_batch_id=? AND report_revision=?',
                     (batch_id,revision)).fetchone()
    if not row:
        if explicit_revision: raise FileNotFoundError('report_not_found')
        return {'status':'not_requested','ui_batch_id':batch_id,'report_revision':None,'snapshot':None}
    return {'ui_batch_id':batch_id,'report_revision':row['report_revision'],'status':row['status'],
            'error':row['error'],'snapshot':json.loads(row['snapshot_json'])}


def get_ui_batch_report(actor,batch_id,revision=None):
    batch = _batch(actor,batch_id)
    db = _connect()
    try:
        result = _report(db,batch_id,revision)
        if not revision: result['status'] = batch['report_status']
        return result
    finally:
        db.close()


def request_ui_batch_report(actor,batch_id,request=None):
    from .ui_execution_contract import require_ui_actor
    from .operation_attribution import current_actor
    from .business_line_service import configured_test_application
    from .feishu_service import task_app_feishu_channel
    batch = _batch(actor,batch_id)
    request = {} if request is None else request
    if not isinstance(request,dict) or set(request)-{'notifications'}:
        raise ValueError('report_request_invalid')
    notifications = request.get('notifications',{'enabled':False})
    if (not isinstance(notifications,dict) or set(notifications)!={'enabled'}
            or type(notifications.get('enabled')) is not bool):
        raise ValueError('report_request_invalid')
    if notifications['enabled']:
        require_ui_actor(actor,permission='platform.notify',app_id=batch['app_id'])
    if not batch['execution_settled'] or batch['stop_pending']:
        raise ValueError('execution_not_settled')
    channel = task_app_feishu_channel(configured_test_application(batch['app_id']) or {})
    trusted = current_actor()
    now = time.time_ns()
    intent = {'requested_ns':now,'actor':{k:trusted[k] for k in ('user_id','username','display_name')},
              'notifications':{**notifications,**{k:channel[k] for k in ('channel_id','target_label','source')}}}
    db = _connect()
    try:
        db.execute('BEGIN IMMEDIATE')
        current = UiExecutionStore().get_batch(batch_id)
        if not current['execution_settled'] or current['stop_pending']:
            raise ValueError('execution_not_settled')
        if current['report_requested_ns'] is None:
            # Preserve frozen execution selection, owner and measured versions.
            # A readable legacy report is reused, never rewritten by migration.
            db.execute("""UPDATE batches SET report_requested_ns=?,report_request_json=?,
                report_status=CASE WHEN report_status='ready' THEN 'ready' ELSE 'pending' END,
                delivery_status=? WHERE ui_batch_id=?""",
                (now,_json(intent),'pending' if notifications['enabled'] else 'not_requested',batch_id))
        elif current['report_status'] == 'failed':
            db.execute("UPDATE batches SET report_status='pending' WHERE ui_batch_id=?",(batch_id,))
        db.execute('COMMIT')
    finally:
        if db.in_transaction: db.execute('ROLLBACK')
        db.close()
    return get_ui_batch_report(actor,batch_id)


def download_ui_batch_report(actor,batch_id,format,revision=None):
    if format not in {'html','md','docx'}: raise ValueError('format_invalid')
    report = get_ui_batch_report(actor,batch_id,revision)
    if report['status'] != 'ready': raise ValueError('report_not_ready')
    db = _connect()
    try:
        column = 'markdown' if format == 'md' else format
        row = db.execute(f'SELECT {column} FROM ui_report_snapshots WHERE ui_batch_id=? AND report_revision=?',
                         (batch_id,report['report_revision'])).fetchone()
        return bytes(row[0]),MIMES[format],_filename(report['snapshot'])+'.'+format
    finally:
        db.close()


def get_ui_batch_summary(actor,batch_id):
    report = get_ui_batch_report(actor,batch_id)
    return {'status':report['status'],'report_revision':report['report_revision'],
            'summary':report['snapshot']['summary'] if report['status'] == 'ready' else ''}


def cleanup_ui_report_chunks(limit=16):
    """At most 16 expired 1-MiB pieces per tick; live uploads keep their rows."""
    db = _connect()
    try:
        db.execute('DELETE FROM ui_report_chunks WHERE rowid IN (SELECT rowid FROM ui_report_chunks WHERE created_ns<? ORDER BY created_ns LIMIT ?)',
                   (time.time_ns()-24*3600*10**9,max(1,min(int(limit),16))))
    finally:
        db.close()


def tick_ui_report_worker():
    """One bounded build off the execution worker; pending index avoids full scans."""
    global _WORKER
    if _WORKER and _WORKER.is_alive(): return
    cleanup_ui_report_chunks()
    db = _connect()
    try:
        row = db.execute("SELECT ui_batch_id FROM batches WHERE execution_settled=1 AND report_status='pending' AND report_requested_ns IS NOT NULL ORDER BY created_ns LIMIT 1").fetchone()
    finally:
        db.close()
    if row:
        def run():
            with _BUILD_LOCK:
                try: _build(row[0])
                except Exception:
                    import logging
                    logging.getLogger(__name__).exception('Private UI report build failed')
                    db = _connect()
                    try:
                        db.execute("UPDATE batches SET report_status='failed' WHERE ui_batch_id=?",(row[0],))
                    finally:
                        db.close()
        _WORKER = threading.Thread(target=run,name='ui-batch-report',daemon=True)
        _WORKER.start()
