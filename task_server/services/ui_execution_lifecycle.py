"""Short SQLite transitions for UI dispatch; no job/file/network work in transactions."""
from contextlib import contextmanager
from .ui_execution_messages import blocker_message
import hmac
import json
import secrets
import time

TERMINAL = {'passed', 'failed', 'cancelled'}


class UiExecutionLifecycle:
    @contextmanager
    def _transaction(self):
        db = self._connect()
        try:
            db.execute('BEGIN IMMEDIATE')
            yield db
            db.execute('COMMIT')
        except Exception:
            db.execute('ROLLBACK')
            raise
        finally:
            db.close()

    def recoverable_batch_ids(self, limit=50):
        db = self._connect()
        try:
            return [r[0] for r in db.execute("SELECT ui_batch_id FROM batches WHERE execution_settled=0 OR needs_job_sync=1 ORDER BY last_reconcile_ns,created_ns LIMIT ?", (limit,))]
        finally:
            db.close()

    def mark_reconciled(self, batch_id):
        with self._transaction() as db:
            db.execute('UPDATE batches SET last_reconcile_ns=? WHERE ui_batch_id=?', (time.time_ns(),batch_id))

    def mark_job_synced(self, attempt):
        with self._transaction() as db:
            db.execute('UPDATE case_attempts SET job_sync_pending=0 WHERE attempt_id=? AND event_sequence=? AND status=?',
                       (attempt['attempt_id'],attempt['event_sequence'],attempt['status']))
            self._rollup(db,attempt['ui_batch_id'])

    def attempt_for_job(self, job_id):
        db = self._connect()
        try:
            row = db.execute('SELECT * FROM case_attempts WHERE dispatch_key=?', (job_id,)).fetchone()
            return self._attempt(row) if row else None
        finally:
            db.close()

    @staticmethod
    def _rollup(db, batch_id):
        rows = list(db.execute('SELECT status,stop_confirmed,job_sync_pending FROM case_attempts WHERE ui_batch_id=?', (batch_id,)))
        statuses = [r[0] for r in rows]
        settled = all(r[0] in TERMINAL or (r[0] == 'interrupted' and r[1]) for r in rows)
        if not statuses:
            return
        if all(s in TERMINAL for s in statuses):
            status = 'cancelled' if 'cancelled' in statuses else 'failed' if 'failed' in statuses else 'passed'
        else:
            status = next(s for s in ('cancelling','interrupted','running','blocked','queued') if s in statuses)
        db.execute('UPDATE batches SET status=?,execution_settled=?,needs_job_sync=? WHERE ui_batch_id=?', (status,int(settled),int(any(r[2] for r in rows)),batch_id))

    def block_attempt(self, attempt_id, code, message=''):
        with self._transaction() as db:
            row = db.execute('SELECT * FROM case_attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            if row and row['status'] in ('queued','blocked'):
                db.execute("UPDATE case_attempts SET status='blocked',block_code=?,block_message=? WHERE attempt_id=?", (code, message or blocker_message(code), attempt_id))
                self._rollup(db, row['ui_batch_id'])

    def reserve_attempt(self, attempt_id):
        with self._transaction() as db:
            row = db.execute('SELECT * FROM case_attempts WHERE attempt_id=?', (attempt_id,)).fetchone()
            if not row or row['status'] not in ('queued','blocked'):
                return False
            lease = db.execute('SELECT * FROM device_leases WHERE platform=? AND device_id=?', (row['platform'],row['device_id'])).fetchone()
            if lease and lease['attempt_id'] != attempt_id:
                return False
            if not lease:
                db.execute('INSERT INTO device_leases VALUES(?,?,?,?,?,?,?)',
                           (row['platform'],row['runner_id'],row['device_id'],row['ui_batch_id'],attempt_id,'reserved',time.time_ns()))
            db.execute("UPDATE case_attempts SET status='queued',block_code='',block_message='' WHERE attempt_id=?", (attempt_id,))
            self._rollup(db, row['ui_batch_id'])
            return True

    def claim_attempt(self, job_id):
        with self._transaction() as db:
            row = db.execute('SELECT * FROM case_attempts WHERE job_id=?', (job_id,)).fetchone()
            if not row or row['status'] != 'queued' or row['claim_token']:
                return None
            lease = db.execute("SELECT * FROM device_leases WHERE attempt_id=? AND state='reserved'", (row['attempt_id'],)).fetchone()
            if not lease:
                return None
            token, now = secrets.token_urlsafe(32), time.time_ns()
            db.execute("UPDATE case_attempts SET status='running',job_sync_pending=1,claim_token=?,started_ns=?,last_event_ns=? WHERE attempt_id=?", (token,now,now,row['attempt_id']))
            db.execute("UPDATE device_leases SET state='running' WHERE attempt_id=?", (row['attempt_id'],))
            self._rollup(db, row['ui_batch_id'])
            return token

    def device_reserved(self, device_id, except_job_id=''):
        db = self._connect()
        try:
            return bool(db.execute('SELECT 1 FROM device_leases l JOIN case_attempts a USING(attempt_id) WHERE l.device_id=? AND a.dispatch_key<>?', (device_id,except_job_id)).fetchone())
        finally:
            db.close()

    def cancel_batch(self, batch_id):
        with self._transaction() as db:
            # A claimed lease survives cancellation until the executor confirms stop.
            db.execute("DELETE FROM device_leases WHERE ui_batch_id=? AND state='reserved'", (batch_id,))
            db.execute("""UPDATE case_attempts SET job_sync_pending=CASE WHEN job_id IS NOT NULL THEN 1 ELSE 0 END, status=CASE WHEN claim_token<>'' AND stop_confirmed=0 THEN 'cancelling' ELSE 'cancelled' END,
                       finished_ns=CASE WHEN claim_token<>'' AND stop_confirmed=0 THEN NULL ELSE ? END
                       WHERE ui_batch_id=? AND status NOT IN ('passed','failed','cancelled')""", (time.time_ns(),batch_id))
            self._rollup(db, batch_id)

    def interrupt_attempt(self, attempt_id, code):
        with self._transaction() as db:
            row = db.execute('SELECT ui_batch_id FROM case_attempts WHERE attempt_id=? AND status IN (?,?)', (attempt_id,'running','interrupted')).fetchone()
            if row:
                db.execute("UPDATE case_attempts SET status='interrupted',block_code=?,block_message=? WHERE attempt_id=?", (code,blocker_message(code),attempt_id))
                db.execute("UPDATE device_leases SET state='interrupted' WHERE attempt_id=?", (attempt_id,))
                self._rollup(db,row[0])

    def record_attempt_event(self, job_id, sequence, event):
        from .runner_progress import normalize_execution_progress
        if type(sequence) is not int or not 1 <= sequence < 2**63 or event.get('type') not in {'progress','result','report_ready'}:
            raise ValueError('event_invalid')
        with self._transaction() as db:
            row = db.execute('SELECT * FROM case_attempts WHERE job_id=?', (job_id,)).fetchone()
            if not row or not row['claim_token'] or not hmac.compare_digest(row['claim_token'], str(event.get('ui_claim_token') or '')) or event.get('runner_id') != row['runner_id'] or event.get('device_id') != row['device_id']:
                raise PermissionError('claim_mismatch')
            if sequence <= row['event_sequence']:
                return {'ok':True,'ignored':'terminal_job' if row['status'] in TERMINAL | {'interrupted','cancelling'} else 'stale_event'}
            now = time.time_ns()
            db.execute('UPDATE case_attempts SET event_sequence=?,last_event_ns=?,job_sync_pending=1 WHERE attempt_id=?', (sequence,now,row['attempt_id']))
            if event['type'] == 'report_ready':
                evidence = json.loads(row['result_json'] or '{}')
                evidence.update({k:str(event[k])[:4000] for k in ('report_url','local_report_path','report_upload_error') if k in event})
                db.execute('UPDATE case_attempts SET result_json=? WHERE attempt_id=?', (json.dumps(evidence,ensure_ascii=False),row['attempt_id']))
                self._rollup(db,row['ui_batch_id'])
                return {'ok':True,'status':row['status']}
            stopped = event.get('type') == 'result' and event.get('execution_stopped') is True
            if event.get('type') == 'result' and event.get('status') not in {'passed','success','failed','cancelled'}:
                raise ValueError('result_status_invalid')
            if stopped:
                db.execute('DELETE FROM device_leases WHERE attempt_id=?', (row['attempt_id'],))
                db.execute('UPDATE case_attempts SET stop_confirmed=1 WHERE attempt_id=?', (row['attempt_id'],))
            if event['type'] == 'result':
                evidence = json.loads(row['result_json'] or '{}')
                for key in ('stdout','stderr','report_url','local_report_path','report_missing_reason','report_upload_error'):
                    if key in event and key not in evidence:
                        evidence[key] = str(event[key])[:32000]
                db.execute('UPDATE case_attempts SET result_json=? WHERE attempt_id=?', (json.dumps(evidence,ensure_ascii=False),row['attempt_id']))
            if row['status'] in {'cancelling','interrupted'}:
                if stopped:
                    final_status = 'cancelled' if row['status'] == 'cancelling' else 'interrupted'
                    db.execute('UPDATE case_attempts SET status=?,finished_ns=? WHERE attempt_id=?', (final_status,now,row['attempt_id']))
                    self._rollup(db,row['ui_batch_id'])
                self._rollup(db,row['ui_batch_id'])
                return {'ok':True,'ignored':'terminal_job','status':'cancelled' if stopped and row['status'] == 'cancelling' else row['status']}
            if row['status'] in TERMINAL:
                self._rollup(db,row['ui_batch_id'])
                return {'ok':True,'ignored':'terminal_job','status':row['status']}
            snapshot = normalize_execution_progress(event.get('execution_progress'))
            if snapshot is not None:
                db.execute('UPDATE case_attempts SET execution_progress_json=? WHERE attempt_id=?', (json.dumps(snapshot,ensure_ascii=False),row['attempt_id']))
            if event['type'] == 'result':
                status = 'passed' if event['status'] in {'passed','success'} else event['status']
                if not stopped:
                    status = 'interrupted'
                db.execute('UPDATE case_attempts SET status=?,finished_ns=?,result_json=?,block_code=?,block_message=? WHERE attempt_id=?',
                           (status,now if stopped else None,json.dumps(evidence,ensure_ascii=False),'' if stopped else 'stop_unconfirmed','' if stopped else '执行停止尚未确认',row['attempt_id']))
            self._rollup(db,row['ui_batch_id'])
            return {'ok':True,'status':status if event['type'] == 'result' else row['status']}
