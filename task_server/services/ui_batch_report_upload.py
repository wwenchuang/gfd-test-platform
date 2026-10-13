"""Job-bound adapters for direct, chunked and inline Runner evidence."""
import base64
import os
import re
import tempfile
import time
import urllib.parse

from .ui_batch_report_service import _connect, save_ui_artifact, validate_ui_upload, _validate_ui_upload_in_db
from .ui_execution_store import UiExecutionStore


def _is_private(job_id, filename=''):
    # Old/new mixed runners must never publish a reserved UI batch filename.
    return bool(str(job_id).startswith('uib_') or 'uib_' in urllib.parse.unquote(str(filename)))


def handle_direct_upload(handler):
    job_id = handler.headers.get('x-ui-job-id','')
    if not _is_private(job_id,handler.headers.get('x-filename','')): return False
    credentials = {key:handler.headers.get(header,'') for key,header in (
        ('ui_claim_token','x-ui-claim-token'),('runner_id','x-ui-runner-id'),('device_id','x-ui-device-id'))}
    path = None
    try:
        validate_ui_upload(job_id,credentials)
        fd,path = tempfile.mkstemp(prefix='ui-evidence-',dir=UiExecutionStore().path.parent)
        os.close(fd)
        handler._stream_body_to_file(path)
        from task_server.config import MAX_UPLOAD_BODY_SIZE
        if os.path.getsize(path) > MAX_UPLOAD_BODY_SIZE: raise ValueError('artifact_too_large')
        with open(path,'rb') as stream: content = stream.read()
        artifact = save_ui_artifact(job_id,credentials,content,'html')
        handler._text(artifact['url'])
    except PermissionError:
        handler._json({'ok':False,'code':'claim_mismatch'},403)
    except (ValueError,OSError):
        handler._json({'ok':False,'code':'artifact_upload_failed'},400)
    finally:
        if path and os.path.exists(path): os.unlink(path)
    return True


def handle_chunk_upload(handler,body,finish=False):
    job_id = body.get('job_id','')
    if not _is_private(job_id,body.get('filename') or body.get('upload_id','')): return False
    db = None
    try:
        validate_ui_upload(job_id,body)
        upload_id,total = body.get('upload_id'),body.get('total')
        if (not isinstance(upload_id,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,240}',upload_id)
                or type(total) is not int or not 1 <= total <= 4096): raise ValueError('chunk_invalid')
        from task_server.config import MAX_UPLOAD_BODY_SIZE
        db = _connect()
        db.execute('BEGIN IMMEDIATE')
        _validate_ui_upload_in_db(db,job_id,body)
        existing = db.execute('SELECT total FROM ui_report_chunks WHERE job_id=? AND upload_id=? LIMIT 1',(job_id,upload_id)).fetchone()
        if existing and existing[0] != total: raise ValueError('chunk_conflict')
        if finish:
            parts = list(db.execute('SELECT part,content FROM ui_report_chunks WHERE job_id=? AND upload_id=? ORDER BY part',(job_id,upload_id)))
            if [p[0] for p in parts] != list(range(total)): raise ValueError('chunks_incomplete')
            content = b''.join(p[1] for p in parts)
            if len(content) > MAX_UPLOAD_BODY_SIZE: raise ValueError('artifact_too_large')
            # Release the write transaction before artifact's own transaction.
            db.execute('COMMIT')
            artifact = save_ui_artifact(job_id,body,content,'html')
            db.execute('DELETE FROM ui_report_chunks WHERE job_id=? AND upload_id=?',(job_id,upload_id))
            handler._json({'ok':True,'url':artifact['url']})
        else:
            index = body.get('index')
            if type(index) is not int or not 0 <= index < total: raise ValueError('chunk_invalid')
            if not existing and index != 0: raise ValueError('chunk_start_required')
            content = base64.b64decode(body.get('contentBase64',''),validate=True)
            if not content or len(content) > 1024 * 1024: raise ValueError('chunk_invalid')
            if index == 0:
                db.execute('DELETE FROM ui_report_chunks WHERE job_id=? AND upload_id<>?',(job_id,upload_id))
            size = db.execute('SELECT COALESCE(SUM(length(content)),0) FROM ui_report_chunks WHERE job_id=? AND upload_id=? AND part<>?',(job_id,upload_id,index)).fetchone()[0]
            if size+len(content) > MAX_UPLOAD_BODY_SIZE: raise ValueError('artifact_too_large')
            db.execute('INSERT OR REPLACE INTO ui_report_chunks VALUES(?,?,?,?,?,?)',(job_id,upload_id,index,total,content,time.time_ns()))
            db.execute('COMMIT')
            handler._json({'ok':True,'upload_id':upload_id,'index':index,'total':total})
    except PermissionError:
        handler._json({'ok':False,'code':'claim_mismatch'},403)
    except (ValueError,TypeError):
        handler._json({'ok':False,'code':'chunk_invalid'},400)
    finally:
        if db: db.close()  # Rollback any rejected transaction.
    return True


def private_callback_event(job_id,sequence,event):
    row = validate_ui_upload(job_id,event)
    result = dict(event)
    # Discard any caller-provided URL, even for a valid claim. Only local bytes
    # previously verified and persisted for this attempt can become a link.
    result.pop('reportUrl',None)
    result['report_url'] = ''
    if type(sequence) is not int or not 1 <= sequence < 2**63:
        raise ValueError('event_invalid')
    if event.get('type') not in {'progress','result','report_ready'}:
        raise ValueError('event_invalid')
    if event['type'] == 'result' and event.get('status') not in {'passed','success','failed','cancelled'}:
        raise ValueError('result_status_invalid')
    if sequence > row['event_sequence']:
        html = event.get('report_html')
        if html:
            if not isinstance(html,str): raise ValueError('artifact_invalid')
            save_ui_artifact(job_id,event,html.encode(),'html')
        shots = event.get('screenshots') or []
        if isinstance(shots,list):
            for shot in shots[:4]:
                if not isinstance(shot,dict): continue
                try: content = base64.b64decode(shot.get('contentBase64',''),validate=True)
                except (ValueError,TypeError): continue
                if len(content) > 2*1024*1024: continue
                kind = 'png' if content.startswith(b'\x89PNG\r\n\x1a\n') else 'jpg' if content.startswith(b'\xff\xd8\xff') else None
                if kind: save_ui_artifact(job_id,event,content,kind)
    db = _connect()
    try:
        artifact = db.execute('''SELECT e.artifact_id FROM ui_report_evidence e JOIN ui_report_artifacts a
            ON a.ui_batch_id=e.ui_batch_id AND a.artifact_id=e.artifact_id
            WHERE e.attempt_id=? AND a.kind='html' ORDER BY e.created_ns DESC LIMIT 1''',(row['attempt_id'],)).fetchone()
        if artifact: result['report_url'] = f"/api/ui-executions/{row['ui_batch_id']}/artifacts/{artifact[0]}"
    finally:
        db.close()
    return result
