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
            WHERE execution_settled=1 AND report_status='pending';
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


def validate_ui_upload(job_id, credentials):
    """The machine token alone cannot assign evidence to a user's batch."""
    db = _connect()
    try:
        row = db.execute('SELECT * FROM case_attempts WHERE job_id=?',(job_id,)).fetchone()
        if (not row or not row['claim_token'] or
                not hmac.compare_digest(row['claim_token'],str(credentials.get('ui_claim_token') or '')) or
                credentials.get('runner_id') != row['runner_id'] or credentials.get('device_id') != row['device_id']):
            raise PermissionError('claim_mismatch')
        return dict(row)
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
        db.execute('INSERT OR IGNORE INTO ui_report_artifacts VALUES(?,?,?,?)',
                   (row['ui_batch_id'],artifact_id,kind,content))
        inserted = db.execute('INSERT OR IGNORE INTO ui_report_evidence VALUES(?,?,?,?)',
                             (row['ui_batch_id'],row['attempt_id'],artifact_id,time.time_ns())).rowcount
        if kind == 'html':
            db.execute('DELETE FROM ui_report_chunks WHERE job_id=?',(job_id,))
        if inserted:
            db.execute("UPDATE batches SET report_status='pending' WHERE ui_batch_id=?",(row['ui_batch_id'],))
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
        return content,MIMES[row['kind']],artifact_id+'.'+row['kind']
    finally:
        db.close()


def _evidence(db,batch_id):
    return [_artifact_metadata(batch_id,r['artifact_id'],r['kind'],r['attempt_id']) for r in db.execute(
        '''SELECT e.artifact_id,e.attempt_id,a.kind FROM ui_report_evidence e JOIN ui_report_artifacts a
        ON a.ui_batch_id=e.ui_batch_id AND a.artifact_id=e.artifact_id WHERE e.ui_batch_id=?
        ORDER BY e.attempt_id,e.artifact_id''',(batch_id,))]


def _snapshot(batch,db):
    snapshots = {s['snapshot_id']:s for s in batch['snapshots']}
    attempts = []
    for attempt in batch['case_attempts']:
        source = snapshots[attempt['snapshot_id']]
        attempts.append({**{k:attempt[k] for k in ('attempt_id','case_id','case_revision','attempt_no','job_id',
            'platform','runner_id','device_id','status','started_ns','finished_ns','block_code','block_message',
            'execution_progress','steps','result') if k in attempt},
            'source_hash':source['source_hash'],'metadata':source['metadata'],
            'device_snapshot':attempt['target_resolution'].get('device_snapshot',{})})
    counts = dict(Counter(a['status'] for a in attempts))
    app_name = next((s['metadata'].get('app_name') for s in batch['snapshots'] if s['metadata'].get('app_name')),batch['app_id'])
    summary = f"{app_name}：{batch['owner_display_name']} 本批 {len(attempts)} 次执行；" + '，'.join(f'{STATUS_LABELS.get(k,k)} {v}' for k,v in sorted(counts.items()))
    return {**{k:batch[k] for k in ('ui_batch_id','owner_user_id','owner_display_name','app_id','suite_id',
                'suite_revision','created_ns','started_ns','finished_ns','status')},
            'app_name':app_name,'selection':batch['selection'],'attempts':attempts,'counts':counts,
            'artifacts':_evidence(db,batch['ui_batch_id']),'summary':summary,
            'cumulative_bugs':None,'cumulative_bugs_note':'历史累计 Bug 未纳入本批结论；未查询历史最近结果。'}


def _text(value):
    # Prevent Markdown HTML/link/table/heading injection as well as embedded newlines.
    value = re.sub(r'[\x00-\x1f\x7f]', ' ',str(value if value is not None else '未记录'))
    return value.translate(str.maketrans({'<':'＜','>':'＞','|':'｜','[':'［',']':'］','(':'（',')':'）',
                                         '`':'｀','*':'＊','_':'＿','#':'＃','!':'！','\\':'＼'}))


def _date(value):
    return time.strftime('%Y-%m-%d %H:%M:%S',time.localtime(value/10**9)) if value else '未执行'


def _filename(snapshot):
    parts = [snapshot['app_name'],snapshot['suite_id'] or '所选用例',snapshot['owner_display_name'],
             _date(snapshot['created_ns'])[:10],snapshot['ui_batch_id'][4:12]]
    value = '_'.join(re.sub(r'[\\/:*?"<>|\x00-\x1f]','_',str(p)).replace('测试报告','')[:60] for p in parts)
    return value+'_测试报告'


def _markdown(snapshot):
    lines = [f"# {_text(snapshot['app_name']).replace('测试报告','')} 测试报告",'',_text(snapshot['summary']),
             f"执行者：{_text(snapshot['owner_display_name'])}；批次：{snapshot['ui_batch_id']}",
             f"开始：{_date(snapshot['started_ns'])}；结束：{_date(snapshot['finished_ns'])}",
             f"重跑来源：{_text(snapshot['selection'].get('rerun_of') or '首次执行')}",
             _text(snapshot['cumulative_bugs_note']),
             '', '## 当次执行明细', '| 用例 | 版本 | 设备 / 系统 | 应用版本 | 次数 | 结果 |', '| --- | --- | --- | --- | --- | --- |']
    for attempt in snapshot['attempts']:
        device = attempt['device_snapshot']
        lines.append('| '+' | '.join(_text(v) for v in (
            attempt['metadata'].get('task_name') or attempt['case_id'],attempt['case_revision'][:12],
            f"{attempt['device_id']} / {attempt['platform']} {device.get('os_version') or device.get('ios_version') or device.get('android_version') or '版本未记录'}",
            '; '.join(f"{app.get('version_name') or '未知'}（构建 {app.get('version_code') or '未知'}）" for app in device.get('app_versions') or []) or '未记录',
            attempt['attempt_no'],STATUS_LABELS.get(attempt['status'],attempt['status'])))+' |')
    for attempt in snapshot['attempts']:
        lines += ['',f"## {_text(attempt['metadata'].get('task_name') or attempt['case_id'])}",
                  f"用例 ID：{_text(attempt['case_id'])}；版本：{attempt['case_revision']}",
                  f"Job：{_text(attempt['job_id'])}；结果：{_text(STATUS_LABELS.get(attempt['status'],attempt['status']))}",
                  f"用例作者：{_text(attempt['metadata'].get('author') or attempt['metadata'].get('created_by') or '未记录')}",
                  f"阻断说明：{_text(attempt['block_message'] or '无')}"]
        lines += ['| 步骤 | 状态 |', '| --- | --- |']
        for step in attempt.get('steps') or []:
            lines.append('| '+_text(step.get('label'))+' | '+_text(STATUS_LABELS.get(step.get('status'),step.get('status')))+' |')
        if not attempt['started_ns']:
            lines.append('此项未开始执行；取消不代表已完成测试。')
        for key in ('stderr','report_missing_reason','report_upload_error'):
            if attempt['result'].get(key): lines.append(_text(attempt['result'][key]))
    lines += ['','## 证据',f"私有证据 {len(snapshot['artifacts'])} 份；请在登录后的本批报告页查看。"]
    return '\n'.join(lines)


def _render_docx(markdown):
    # Process timeout also terminates a wedged parser; no accumulating stuck threads.
    return subprocess.run([sys.executable,'-c',
        'import sys; from task_server.services.report_word_service import render_report_docx; '
        'sys.stdout.buffer.write(render_report_docx(sys.stdin.buffer.read().decode("utf-8")))'],
        input=markdown.encode(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=True,
        cwd=str(Path(__file__).resolve().parents[2])).stdout


def _build(batch_id):
    db = _connect()
    try:
        # Freeze rows and manifest under one short write lock, never across rendering.
        db.execute('BEGIN IMMEDIATE')
        batch = UiExecutionStore().get_batch(batch_id)
        if not batch or not batch['execution_settled'] or batch['stop_pending']:
            raise ValueError('execution_not_settled')
        snapshot = _snapshot(batch,db)
        revision = hashlib.sha256(_json(snapshot).encode()).hexdigest()
        existing = db.execute('SELECT status FROM ui_report_snapshots WHERE ui_batch_id=? AND report_revision=?',(batch_id,revision)).fetchone()
        if existing and existing['status'] == 'ready':
            db.execute('INSERT OR REPLACE INTO ui_report_heads VALUES(?,?)',(batch_id,revision))
            db.execute("UPDATE batches SET report_status='ready' WHERE ui_batch_id=?",(batch_id,))
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
            db.execute('UPDATE batches SET report_status=? WHERE ui_batch_id=?',(state,batch_id))
            db.execute('COMMIT')
        except Exception:
            if db.in_transaction: db.execute('ROLLBACK')
            db.execute("UPDATE ui_report_snapshots SET status='failed',error='report_render_failed' WHERE ui_batch_id=? AND report_revision=?",(batch_id,revision))
            db.execute("UPDATE batches SET report_status='failed' WHERE ui_batch_id=?",(batch_id,))
        return _report(db,batch_id,revision)
    finally:
        db.close()


def build_ui_batch_report(actor,batch_id):
    _batch(actor,batch_id)
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
        return {'status':'pending','ui_batch_id':batch_id,'report_revision':None,'snapshot':None}
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


def request_ui_batch_report(actor,batch_id):
    batch = _batch(actor,batch_id)
    if not batch['execution_settled'] or batch['stop_pending']:
        raise ValueError('execution_not_settled')
    db = _connect()
    try:
        db.execute("UPDATE batches SET report_status='pending' WHERE ui_batch_id=?",(batch_id,))
    finally:
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
        row = db.execute("SELECT ui_batch_id FROM batches WHERE execution_settled=1 AND report_status='pending' ORDER BY created_ns LIMIT 1").fetchone()
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
