"""Private immutable report delivery queue; network work never owns a DB lock.

A lost response is not proof of rejection: unknown deliveries require a receipt
check before an explicitly authorized manual resend. No exactly-once promise.
"""
import hashlib
import json
import os
import re
import threading
import time
import urllib.error
from urllib.parse import urlencode, urlsplit
import uuid

from task_server import identity
from . import business_line_service, feishu_service
from .notification_presentation import ui_batch_report_card
from .ui_execution_store import UiExecutionStore
from .ui_execution_service import get_ui_batch
from .ui_execution_contract import require_ui_actor

_BACKOFF = (30, 120, 600)
_WORKER = None
_WORKER_LOCK = threading.Lock()
_MESSAGES = {
    'sent': '飞书已确认接收本次报告。', 'pending': '等待发送本次报告。',
    'retry_wait': '飞书未确认接收，正在等待有界重试。',
    'failed': '发送失败，请检查渠道配置和权限后补发。',
    'unknown': '送达状态未知，请先核对群内消息；补发可能产生重复消息。',
    'sending': '正在发送本次报告。',
}


def notifications_enabled():
    """Explicit notification flag; admission rollback pauses delivery by default."""
    configured = os.getenv('UI_BATCH_NOTIFICATIONS_ENABLED')
    if configured is None:
        from .ui_batch_dispatch import enabled
        return enabled()
    return configured.strip().lower() in {'1','true','yes'}


def _now():
    return time.time_ns()


def _public(row):
    keys = ('delivery_key','ui_batch_id','channel_id','report_revision','status','due_ns',
            'attempts','manual_retries','error_code')
    result = {key: row[key] for key in keys}
    result['message'] = _MESSAGES.get(row['status'], '通知状态待核对。')
    return result


def _batch_status(db,batch_id,revision,status):
    # A delayed send for an old revision cannot overwrite the current head's state.
    db.execute('UPDATE batches SET delivery_status=? WHERE ui_batch_id=? AND EXISTS '
               '(SELECT 1 FROM ui_report_heads WHERE ui_batch_id=? AND report_revision=?)',
               ('queued' if status=='pending' else status,batch_id,batch_id,revision))


def enqueue_batch_delivery(batch_id, report_revision, channel_id):
    from .ui_batch_report_service import _connect, _report
    if not isinstance(report_revision,str) or not re.fullmatch(r'[0-9a-f]{64}',report_revision):
        raise ValueError('report_revision_invalid')
    store = UiExecutionStore()
    batch = store.get_batch(batch_id)
    if not batch: raise FileNotFoundError('batch_not_found')
    policy = (batch.get('report_request') or {}).get('notifications') or {}
    if batch.get('report_requested_ns') is None: raise ValueError('notification_not_requested')
    if not policy.get('enabled'): raise ValueError('notification_not_requested')
    if channel_id != policy.get('channel_id', 'unconfigured'): raise ValueError('channel_changed')
    key = hashlib.sha256(json.dumps([batch_id,report_revision,channel_id],separators=(',',':')).encode()).hexdigest()
    db = _connect()
    try:
        report = _report(db,batch_id,report_revision)
        if report['status'] != 'ready': raise ValueError('report_not_ready')
        db.execute('BEGIN IMMEDIATE')
        db.execute("INSERT OR IGNORE INTO delivery_outbox(delivery_key,ui_batch_id,channel_id,report_revision,status,due_ns) VALUES(?,?,?,?, 'pending',?)",
                   (key,batch_id,channel_id,report_revision,_now()))
        item = db.execute('SELECT * FROM delivery_outbox WHERE delivery_key=?',(key,)).fetchone()
        _batch_status(db,batch_id,report_revision,item['status'])
        db.execute('COMMIT')
        return _public(item)
    finally:
        db.close()


def enqueue_ready_batch_deliveries(limit=20):
    from .ui_batch_report_service import _connect
    db = _connect()
    try:
        # Bound every query. Existing identity suppresses repeated terminal callbacks.
        rows = db.execute("""SELECT b.ui_batch_id,b.report_request_json,h.report_revision FROM batches b
            JOIN ui_report_heads h ON h.ui_batch_id=b.ui_batch_id
            JOIN ui_report_snapshots r ON r.ui_batch_id=h.ui_batch_id AND r.report_revision=h.report_revision
            WHERE b.execution_settled=1 AND b.report_status='ready' AND b.delivery_status='pending' AND r.status='ready'
              AND b.report_requested_ns IS NOT NULL AND json_extract(b.report_request_json,'$.notifications.enabled')=1
              AND NOT EXISTS(SELECT 1 FROM delivery_outbox d WHERE d.ui_batch_id=b.ui_batch_id
                AND d.report_revision=h.report_revision AND d.channel_id=coalesce(json_extract(b.report_request_json,'$.notifications.channel_id'),'unconfigured'))
            ORDER BY b.created_ns LIMIT ?""",(max(1,min(int(limit),50)),)).fetchall()
    finally: db.close()
    for row in rows:
        policy=json.loads(row['report_request_json']).get('notifications') or {}
        enqueue_batch_delivery(row['ui_batch_id'],row['report_revision'],policy.get('channel_id','unconfigured'))
    return len(rows)


def _claim_due(limit):
    db = UiExecutionStore()._connect()
    now = _now()
    try:
        db.execute('BEGIN IMMEDIATE')
        stale = db.execute("SELECT delivery_key,ui_batch_id,report_revision FROM delivery_outbox WHERE status='sending' AND claimed_ns<? LIMIT ?",
                           (now-60*10**9,max(1,min(int(limit),50)))).fetchall()
        for row in stale:
            db.execute("UPDATE delivery_outbox SET status='unknown',error_code='worker_interrupted',claim_token='' WHERE delivery_key=?",(row['delivery_key'],))
            _batch_status(db,row['ui_batch_id'],row['report_revision'],'unknown')
        rows=db.execute("SELECT * FROM delivery_outbox WHERE status IN ('pending','retry_wait') AND due_ns<=? ORDER BY due_ns,delivery_key LIMIT ?",
                        (now,max(1,min(int(limit),50)))).fetchall()
        result=[]
        for row in rows:
            token=uuid.uuid4().hex
            db.execute("UPDATE delivery_outbox SET status='sending',claim_token=?,claimed_ns=?,attempts=attempts+1,cycle_attempts=cycle_attempts+1 WHERE delivery_key=?",
                       (token,now,row['delivery_key']))
            _batch_status(db,row['ui_batch_id'],row['report_revision'],'sending')
            result.append(dict(db.execute('SELECT * FROM delivery_outbox WHERE delivery_key=?',(row['delivery_key'],)).fetchone()))
        db.execute('COMMIT')
        return result
    finally: db.close()


def _target(batch, channel_id):
    profile=identity.get_access_profile(batch['owner_username'])
    if (not profile or profile.get('user_id')!=batch['owner_user_id'] or profile.get('status')!='active'
            or profile.get('must_change_password') or 'ui.view' not in (profile.get('permissions') or [])
            or 'ui.execute' not in (profile.get('permissions') or [])
            or ((profile.get('scope') or {}).get('ui_apps')!='*' and batch['app_id'] not in ((profile.get('scope') or {}).get('ui_apps') or []))):
        raise ValueError('owner_access_revoked')
    # The executor remains frozen. A later report requester authorizes sending;
    # legacy queued deliveries retain their original owner's authorization.
    requester=(batch.get('report_request') or {}).get('actor') or {'username':batch['owner_username'],'user_id':batch['owner_user_id']}
    sender=identity.get_access_profile(requester['username'])
    if (not sender or sender.get('user_id')!=requester['user_id'] or sender.get('status')!='active'
            or sender.get('must_change_password') or 'platform.notify' not in (sender.get('permissions') or [])
            or 'ui.view' not in (sender.get('permissions') or [])
            or ((sender.get('scope') or {}).get('ui_apps')!='*' and batch['app_id'] not in ((sender.get('scope') or {}).get('ui_apps') or []))):
        raise ValueError('owner_access_revoked')
    app=business_line_service.configured_test_application(batch['app_id'])
    if not app or not app.get('enabled') or app.get('historical_only'): raise ValueError('app_unavailable')
    current,target=feishu_service.resolve_task_app_feishu_channel(app)
    if channel_id=='unconfigured': raise ValueError('channel_unconfigured')
    if not current['ready'] or current['channel_id']!=channel_id: raise ValueError('channel_changed')
    return target


def _report_url(batch_id, revision):
    base=(os.getenv('TASK_PUBLIC_BASE_URL') or os.getenv('MIDSCENE_PUBLIC_BASE_URL') or os.getenv('PUBLIC_BASE_URL') or '').strip().rstrip('/')
    try: parsed=urlsplit(base)
    except ValueError: raise ValueError('report_link_unconfigured') from None
    if parsed.scheme not in {'https','http'} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError('report_link_unconfigured')
    return base+'/task-manager.html?'+urlencode({'ui_batch':batch_id,'report_revision':revision})


def _send(item):
    from .ui_batch_report_service import _connect, _report
    batch=UiExecutionStore().get_batch(item['ui_batch_id'])
    try:
        target=_target(batch,item['channel_id'])
        link=_report_url(item['ui_batch_id'],item['report_revision'])
    except ValueError as error:
        code=str(error)
        allowed={'owner_access_revoked','app_unavailable','channel_unconfigured','channel_changed','report_link_unconfigured'}
        return 'failed',code if code in allowed else 'configuration_unavailable'
    db=_connect()
    try: report=_report(db,item['ui_batch_id'],item['report_revision'])
    finally: db.close()
    if report['status']!='ready': return 'failed','report_not_ready'
    payload=ui_batch_report_card(item['ui_batch_id'],item['report_revision'],report['snapshot'],link)
    try:
        response=feishu_service.send_feishu_card(payload,webhook=target)
        # Only an explicit documented zero result confirms receipt; malformed/empty responses are unknown.
        if isinstance(response,dict):
            code=response.get('code',response.get('StatusCode'))
            if type(code) is int and code==0: return 'sent',''
            if type(code) is int: return 'rejected','remote_rejected'
        return 'unknown','response_unconfirmed'
    except urllib.error.HTTPError as error:
        if error.code in {400,401,403,404,413,422,429}: return 'rejected','http_rejected'
        return 'unknown','http_unconfirmed'
    except Exception:
        # Never persist exception strings: they can contain robot URLs or response bodies.
        return 'unknown','transport_unconfirmed'


def _finish(item,status,code):
    due=_now()
    if status=='rejected':
        count=item['cycle_attempts']
        if count<=len(_BACKOFF): status='retry_wait';due+=_BACKOFF[count-1]*10**9
        else: status='failed'
    db=UiExecutionStore()._connect()
    try:
        db.execute('BEGIN IMMEDIATE')
        changed=db.execute("UPDATE delivery_outbox SET status=?,error_code=?,due_ns=?,finished_ns=?,claim_token='' WHERE delivery_key=? AND status='sending' AND claim_token=?",
                           (status,code,due,_now(),item['delivery_key'],item['claim_token'])).rowcount
        if changed: _batch_status(db,item['ui_batch_id'],item['report_revision'],status)
        db.execute('COMMIT')
    finally: db.close()


def process_due_batch_deliveries(limit=10):
    if not notifications_enabled():
        return 0
    count=0
    for _ in range(max(1,min(int(limit),50))):
        # Claim just before sending: later rows cannot expire while earlier network calls wait.
        if not notifications_enabled(): break
        items=_claim_due(1)
        if not items: break
        item=items[0]
        try: status,code=_send(item)
        except Exception: status,code='unknown','worker_interrupted'
        _finish(item,status,code)
        count+=1
    return count


def get_batch_deliveries(actor,batch_id,revision=None):
    batch=get_ui_batch(actor,batch_id)
    if not batch: raise FileNotFoundError('batch_not_found')
    if revision is not None and (not isinstance(revision,str) or not re.fullmatch(r'[0-9a-f]{64}',revision)):
        raise ValueError('report_revision_invalid')
    db=UiExecutionStore()._connect()
    try:
        rows=db.execute('SELECT * FROM delivery_outbox WHERE ui_batch_id=?'+(' AND report_revision=?' if revision else '')+' ORDER BY rowid DESC LIMIT 50',
                        (batch_id,revision) if revision else (batch_id,)).fetchall()
        policy=(batch.get('report_request') or {}).get('notifications') or (batch['selection'].get('notifications') if rows else None) or {'enabled':False}
        return {'items':[_public(row) for row in rows], 'policy':policy,
                'delivery_status':(rows[0]['status'] if rows else ('pending' if policy.get('enabled') else 'not_requested')) if revision else batch['delivery_status']}
    finally: db.close()


def retry_batch_delivery(actor,delivery_id,confirmed_not_received=False):
    if type(confirmed_not_received) is not bool: raise ValueError('receipt_check_invalid')
    db=UiExecutionStore()._connect()
    try: item=db.execute('SELECT * FROM delivery_outbox WHERE delivery_key=?',(delivery_id,)).fetchone()
    finally: db.close()
    if not item: raise FileNotFoundError('delivery_not_found')
    batch=get_ui_batch(actor,item['ui_batch_id'])
    if not batch: raise FileNotFoundError('batch_not_found')
    profile=require_ui_actor(actor,permission='platform.notify',app_id=batch['app_id'])
    _target(batch,item['channel_id'])
    db=UiExecutionStore()._connect()
    try:
        db.execute('BEGIN IMMEDIATE')
        item=db.execute('SELECT * FROM delivery_outbox WHERE delivery_key=?',(delivery_id,)).fetchone()
        if item['status'] not in {'failed','unknown'}: raise ValueError('delivery_not_retryable')
        if item['status']=='unknown' and not confirmed_not_received: raise ValueError('receipt_check_required')
        db.execute("UPDATE delivery_outbox SET status='pending',error_code='',due_ns=?,cycle_attempts=0,manual_retries=manual_retries+1,retry_actor_id=? WHERE delivery_key=?",
                   (_now(),profile['user_id'],delivery_id))
        _batch_status(db,batch['ui_batch_id'],item['report_revision'],'pending')
        result=_public(db.execute('SELECT * FROM delivery_outbox WHERE delivery_key=?',(delivery_id,)).fetchone())
        db.execute('COMMIT')
        return result
    finally:
        if db.in_transaction: db.execute('ROLLBACK')
        db.close()


def tick_batch_notification_worker():
    """Independent network thread, so notification timeouts cannot block device claims."""
    global _WORKER
    if not notifications_enabled(): return
    with _WORKER_LOCK:
        if _WORKER and _WORKER.is_alive(): return
        def run():
            try:
                enqueue_ready_batch_deliveries(20)
                process_due_batch_deliveries(5)
            except Exception:
                # Generic logging avoids leaking target URLs through unexpected diagnostics.
                import logging
                logging.getLogger(__name__).warning('UI batch delivery worker unavailable')
        _WORKER=threading.Thread(target=run,name='ui-batch-notifications',daemon=True)
        _WORKER.start()
