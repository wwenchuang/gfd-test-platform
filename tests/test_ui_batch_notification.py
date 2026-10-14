"""Immutable UI report delivery: no credentials or external transport in tests."""
import concurrent.futures
import json
import time
import urllib.error
import pytest
from test_ui_batch_dispatch import dispatch_env
from test_ui_execution_service import environment, ACTORS, APP, request
from task_server.services.operation_attribution import actor_context


@pytest.fixture
def notify_env(dispatch_env, monkeypatch):
    from task_server.services import business_line_service, ui_batch_report_service as reports
    from task_server import identity
    original=identity.get_access_profile
    monkeypatch.setattr(identity,'get_access_profile',lambda name:{**original(name),'permissions':['ui.view','ui.execute','platform.notify']})
    app = {'package': APP, 'enabled': True, 'platform_mappings': {'android':APP,'ios':'com.kfb.3d'},
           'feishu_webhook':'https://example.invalid/bot/test-only'}
    monkeypatch.setattr(business_line_service,'configured_test_application',lambda _:app)
    monkeypatch.setattr(reports,'_render_docx',lambda _:b'docx test stub')
    monkeypatch.setenv('MIDSCENE_PUBLIC_BASE_URL','https://platform.example.invalid')
    monkeypatch.delenv('TASK_PUBLIC_BASE_URL',raising=False)
    return app


def ready(enabled=True, key='notify'):
    from task_server.services import ui_execution_service as service, ui_batch_dispatch as dispatch, job_service, ui_batch_report_service as reports
    body=request();body['notifications']['enabled']=enabled
    body['release_report']={'enabled':True}
    with actor_context(ACTORS['a']): batch=service.create_ui_batch(None,body,key)
    dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    job=job_service.get_job(batch['case_attempts'][0]['dispatch_key']);event=dispatch.claim_ui_job(job)
    event={**event,'runner_id':'mac','device_id':'iphone','status':'passed','execution_stopped':True,'type':'result'}
    dispatch.record_ui_attempt_event(job['job_id'],1,event)
    with actor_context(ACTORS['a']):
        reports.request_ui_batch_report(None,batch['ui_batch_id'],{'notifications':{'enabled':enabled}})
        report=reports.build_ui_batch_report(None,batch['ui_batch_id'])
        batch=service.get_ui_batch(None,batch['ui_batch_id'])
    return batch,report,job,event


def enqueue(batch,report):
    from task_server.services.ui_batch_notification_service import enqueue_batch_delivery
    return enqueue_batch_delivery(batch['ui_batch_id'],report['report_revision'],batch['selection']['notifications']['channel_id'])


def test_configured_destination_is_frozen_without_secret_and_idempotent(notify_env):
    from task_server.services.ui_execution_service import create_ui_batch
    batch,report,_,_=ready()
    policy=batch['selection']['notifications']
    assert policy['enabled'] and policy['channel_id'].startswith('feishu:')
    assert 'https:' not in json.dumps(policy)
    notify_env['feishu_webhook']='https://example.invalid/changed'
    body=request();body['notifications']['enabled']=True
    body['release_report']={'enabled':True}
    with actor_context(ACTORS['a']): again=create_ui_batch(None,body,'notify')
    assert again['selection']==batch['selection']
    assert enqueue(batch,report)['delivery_key']==enqueue(batch,report)['delivery_key']


def test_send_uses_pinned_revision_and_never_network_under_write_lock(notify_env,monkeypatch):
    from task_server.services import ui_batch_notification_service as notify, feishu_service, ui_batch_report_service as reports
    from task_server.services.ui_execution_store import UiExecutionStore
    batch,first,job,event=ready();delivery=enqueue(batch,first)
    reports.save_ui_artifact(job['job_id'],event,b'late','html')
    with actor_context(ACTORS['a']): second=reports.build_ui_batch_report(None,batch['ui_batch_id'])
    assert second['report_revision']!=first['report_revision']
    calls=[]
    def send(payload,webhook=None):
        db=UiExecutionStore()._connect()
        try: db.execute('BEGIN IMMEDIATE');db.execute('ROLLBACK')
        finally: db.close()
        calls.append(payload);return {'code':0}
    monkeypatch.setattr(feishu_service,'send_feishu_card',send)
    assert notify.process_due_batch_deliveries(1)==1
    assert notify.process_due_batch_deliveries(1)==0
    assert first['report_revision'] in json.dumps(calls[0])
    assert second['report_revision'] not in json.dumps(calls[0])
    assert 'UI 测试批次' in json.dumps(calls[0],ensure_ascii=False)
    with actor_context(ACTORS['a']): assert notify.get_batch_deliveries(None,batch['ui_batch_id'])['items'][0]['status']=='sent'


@pytest.mark.parametrize('change,code',[('missing','channel_unconfigured'),('invalid','channel_unconfigured'),('changed','channel_changed'),('disabled','app_unavailable')])
def test_missing_invalid_changed_disabled_channel_is_not_redirected(notify_env,monkeypatch,change,code):
    from task_server.services import ui_batch_notification_service as notify, feishu_service
    if change=='missing': notify_env['feishu_webhook']=''
    if change=='invalid': notify_env['feishu_webhook']='invalid'
    monkeypatch.delenv('FEISHU_WEBHOOK_DEFAULT',raising=False)
    monkeypatch.delenv('FEISHU_WEBHOOK_COM_KFB_MODEL',raising=False)
    batch,report,_,_=ready();enqueue(batch,report)
    if change=='changed': notify_env['feishu_webhook']='https://example.invalid/new'
    if change=='disabled': notify_env['enabled']=False
    monkeypatch.setattr(feishu_service,'send_feishu_card',lambda *a,**k:pytest.fail('must not send'))
    notify.process_due_batch_deliveries(1)
    with actor_context(ACTORS['a']):
        item=notify.get_batch_deliveries(None,batch['ui_batch_id'])['items'][0]
        assert item['status']=='failed' and item['error_code']==code
        assert 'https:' not in json.dumps(item)


def test_confirmed_rejections_use_bounded_backoff_then_fail(notify_env,monkeypatch):
    from task_server.services import ui_batch_notification_service as notify, feishu_service
    batch,report,_,_=ready();enqueue(batch,report)
    clock=[time.time_ns()];monkeypatch.setattr(notify,'_now',lambda:clock[0])
    monkeypatch.setattr(feishu_service,'send_feishu_card',lambda *a,**k: {'code':19021,'msg':'sensitive https://bad'})
    for expected,delay in [('retry_wait',30),('retry_wait',120),('retry_wait',600),('failed',0)]:
        assert notify.process_due_batch_deliveries(1)==1
        with actor_context(ACTORS['a']): item=notify.get_batch_deliveries(None,batch['ui_batch_id'])['items'][0]
        assert item['status']==expected and item['error_code']=='remote_rejected'
        if delay:
            assert item['due_ns']==clock[0]+delay*10**9
            assert notify.process_due_batch_deliveries(1)==0
            clock[0]=item['due_ns']
    assert item['attempts']==4 and 'sensitive' not in json.dumps(item)


@pytest.mark.parametrize('error,status',[(TimeoutError('lost after remote success https://secret'),'unknown'),(urllib.error.HTTPError('https://secret',403,'forbidden',{},None),'retry_wait')],ids=['ambiguous-timeout','http-403'])
def test_timeout_unknown_and_http_rejection_are_distinct(notify_env,monkeypatch,error,status):
    from task_server.services import ui_batch_notification_service as notify, feishu_service
    batch,report,_,_=ready();enqueue(batch,report)
    def send(*a,**k): raise error
    monkeypatch.setattr(feishu_service,'send_feishu_card',send)
    notify.process_due_batch_deliveries(1)
    with actor_context(ACTORS['a']): item=notify.get_batch_deliveries(None,batch['ui_batch_id'])['items'][0]
    assert item['status']==status and 'https:' not in json.dumps(item)
    if status=='unknown':
        assert notify.process_due_batch_deliveries(1)==0
        assert '核对' in item['message']


def test_concurrent_terminal_and_sonic_callbacks_cannot_send_twice(notify_env,monkeypatch):
    from task_server.services import ui_batch_notification_service as notify, feishu_service, sonic_service
    batch,report,job,_=ready();calls=[]
    monkeypatch.setattr(feishu_service,'send_feishu_card',lambda *a,**k:calls.append(a) or {'code':0})
    monkeypatch.setattr(sonic_service,'load_sonic_suite_results',lambda:pytest.fail('batch must not enter legacy suite'))
    with concurrent.futures.ThreadPoolExecutor(6) as pool:
        list(pool.map(lambda _:enqueue(batch,report),range(12)))
        list(pool.map(lambda _:notify.process_due_batch_deliveries(1),range(6)))
        assert list(pool.map(lambda _:sonic_service.register_sonic_suite_result({**job,'source':'sonic','status':'passed'}),range(4)))==['']*4
    assert len(calls)==1


def test_retry_permission_scope_and_unknown_receipt_check(notify_env,monkeypatch):
    from task_server import identity
    from task_server.services import ui_batch_notification_service as notify, feishu_service
    batch,report,_,_=ready();delivery=enqueue(batch,report)
    monkeypatch.setattr(feishu_service,'send_feishu_card',lambda *a,**k:(_ for _ in ()).throw(TimeoutError()))
    notify.process_due_batch_deliveries(1)
    original=identity.get_access_profile
    monkeypatch.setattr(identity,'get_access_profile',lambda name:{**original(name),'permissions':['ui.view','ui.execute']})
    with actor_context(ACTORS['a']):
        with pytest.raises(PermissionError):notify.retry_batch_delivery(None,delivery['delivery_key'])
    monkeypatch.setattr(identity,'get_access_profile',lambda name:{**original(name),'permissions':['ui.view','ui.execute','platform.notify']})
    with actor_context(ACTORS['b']):
        with pytest.raises(FileNotFoundError):notify.retry_batch_delivery(None,delivery['delivery_key'])
    with actor_context(ACTORS['a']):
        with pytest.raises(ValueError,match='receipt_check_required'):notify.retry_batch_delivery(None,delivery['delivery_key'])
        retried=notify.retry_batch_delivery(None,delivery['delivery_key'],confirmed_not_received=True)
        assert retried['status']=='pending' and retried['manual_retries']==1 and retried['attempts']==1
    monkeypatch.setattr(feishu_service,'send_feishu_card',lambda *a,**k:{'code':0});notify.process_due_batch_deliveries(1)
    with actor_context(ACTORS['a']):
        with pytest.raises(ValueError,match='delivery_not_retryable'):notify.retry_batch_delivery(None,delivery['delivery_key'])
    monkeypatch.setattr(identity,'get_access_profile',lambda name:{**original(name),'permissions':['ui.view','platform.notify'],'scope':{'ui_apps':[]}})
    with actor_context(ACTORS['admin']):
        with pytest.raises(PermissionError):notify.get_batch_deliveries(None,batch['ui_batch_id'])


def test_auto_delivery_requires_current_owner_access(notify_env,monkeypatch):
    from task_server import identity
    from task_server.services import ui_batch_notification_service as notify, feishu_service
    batch,report,_,_=ready();enqueue(batch,report)
    original=identity.get_access_profile
    monkeypatch.setattr(identity,'get_access_profile',lambda name:{**original(name),'scope':{'ui_apps':[]}})
    monkeypatch.setattr(feishu_service,'send_feishu_card',lambda *a,**k:pytest.fail('revoked'))
    notify.process_due_batch_deliveries(1)
    db=notify.UiExecutionStore()._connect()
    try: assert db.execute('SELECT error_code FROM delivery_outbox').fetchone()[0]=='owner_access_revoked'
    finally: db.close()


def test_expired_inflight_is_unknown_never_resent(notify_env,monkeypatch):
    from task_server.services import ui_batch_notification_service as notify, feishu_service
    batch,report,_,_=ready();enqueue(batch,report)
    item=notify._claim_due(1)[0]
    monkeypatch.setattr(notify,'_now',lambda:item['claimed_ns']+61*10**9)
    monkeypatch.setattr(feishu_service,'send_feishu_card',lambda *a,**k:pytest.fail('unknown receipt'))
    assert notify.process_due_batch_deliveries(1)==0
    with actor_context(ACTORS['a']): assert notify.get_batch_deliveries(None,batch['ui_batch_id'])['items'][0]['status']=='unknown'


def test_opt_out_never_enqueues_and_worker_does_not_block_dispatch(notify_env,monkeypatch):
    import threading
    from task_server.services import ui_batch_notification_service as notify, feishu_service
    batch,report,_,_=ready(False)
    assert notify.enqueue_ready_batch_deliveries(10)==0
    with pytest.raises(ValueError,match='notification_not_requested'):enqueue(batch,report)
    ready(True, key='notify-enabled')
    entered,release=threading.Event(),threading.Event()
    def send(*a,**k):entered.set();assert release.wait(3);return {'code':0}
    monkeypatch.setattr(feishu_service,'send_feishu_card',send);monkeypatch.setattr(notify,'_WORKER',None)
    try:
        notify.tick_batch_notification_worker();assert entered.wait(2)
        worker=notify._WORKER;notify.tick_batch_notification_worker();assert notify._WORKER is worker
    finally:release.set();notify._WORKER.join(3)


def test_http_5xx_does_not_prove_remote_message_was_rejected(notify_env,monkeypatch):
    from task_server.services import ui_batch_notification_service as notify, feishu_service
    batch,report,_,_=ready();enqueue(batch,report)
    def send(*a,**k):raise urllib.error.HTTPError('https://secret',503,'proxy lost response',{},None)
    monkeypatch.setattr(feishu_service,'send_feishu_card',send)
    notify.process_due_batch_deliveries(1)
    with actor_context(ACTORS['a']):assert notify.get_batch_deliveries(None,batch['ui_batch_id'])['items'][0]['status']=='unknown'


def test_old_delivery_completion_cannot_overwrite_current_revision_status(notify_env,monkeypatch):
    from task_server.services import ui_batch_notification_service as notify, ui_batch_report_service as reports
    batch,first,job,event=ready();enqueue(batch,first);old=notify._claim_due(1)[0]
    reports.save_ui_artifact(job['job_id'],event,b'late','html')
    with actor_context(ACTORS['a']):second=reports.build_ui_batch_report(None,batch['ui_batch_id'])
    enqueue(batch,second);notify._finish(old,'sent','')
    assert notify.UiExecutionStore().get_batch(batch['ui_batch_id'])['delivery_status']=='queued'


def test_global_access_policy_allows_scoped_batch_notify_without_shared_config_access(notify_env):
    from task_server.access_control import MainAccess, AccessDenied
    route='/api/ui-executions/uib_'+'a'*32+'/deliveries/'+'b'*64+'/retry'
    profile={'permissions':['ui.view','platform.notify'],'scope':{'ui_apps':[APP]},'is_superuser':False}
    policy=MainAccess(profile,catalog=[])
    assert policy.permission_for('POST',route,{}, {})=='platform.notify'
    policy.check('POST',route)
    with pytest.raises(AccessDenied):policy.check('POST','/api/feishu-drafts/submit')
    policy=MainAccess({**profile,'permissions':['ui.view']},catalog=[])
    with pytest.raises(AccessDenied):policy.check('POST',route)


def test_delivery_http_retry_matches_batch_and_rejects_forged_payload(notify_env,monkeypatch):
    import re
    from task_server import router, identity
    from task_server.services import ui_batch_notification_service as notify
    from test_ui_batch_report_access import Handler
    batch,report,_,_=ready();delivery=enqueue(batch,report)
    item=notify._claim_due(1)[0];notify._finish(item,'failed','remote_rejected')
    original=identity.get_access_profile
    monkeypatch.setattr(identity,'get_access_profile',lambda name:{**original(name),'permissions':['ui.view','ui.execute','platform.notify']})
    url='/api/ui-executions/'+batch['ui_batch_id']+'/deliveries/'+delivery['delivery_key']+'/retry'
    match=re.fullmatch(r'^/api/ui-executions/([^/]+)/deliveries/([0-9a-f]{64})/retry$',url)
    with actor_context(ACTORS['a']):
        h=Handler({'confirmed_not_received':False,'webhook':'https://evil'})
        router._retry_ui_batch_delivery(h,{},match);assert h.response[1]==409
        h=Handler({'confirmed_not_received':False});router._retry_ui_batch_delivery(h,{},match);assert h.response[1]==202
    with actor_context(ACTORS['b']):
        h=Handler({'confirmed_not_received':False});router._retry_ui_batch_delivery(h,{},match);assert h.response[1]==404


def test_public_report_link_must_be_configured_and_never_contains_auth(notify_env,monkeypatch):
    from task_server.services import ui_batch_notification_service as notify, feishu_service
    batch,report,_,_=ready();enqueue(batch,report)
    monkeypatch.delenv('MIDSCENE_PUBLIC_BASE_URL',raising=False);monkeypatch.delenv('PUBLIC_BASE_URL',raising=False)
    monkeypatch.setattr(feishu_service,'send_feishu_card',lambda *a,**k:pytest.fail('no usable report link'))
    notify.process_due_batch_deliveries(1)
    with actor_context(ACTORS['a']):item=notify.get_batch_deliveries(None,batch['ui_batch_id'])['items'][0]
    assert item['status']=='failed' and item['error_code']=='report_link_unconfigured'
    for invalid in ['https://user:secret@example.invalid','https://example.invalid?token=secret','https://example.invalid#token=secret']:
        monkeypatch.setenv('MIDSCENE_PUBLIC_BASE_URL',invalid)
        with pytest.raises(ValueError,match='report_link_unconfigured'):notify._report_url(batch['ui_batch_id'],report['report_revision'])


def test_schema_v2_upgrade_preserves_snapshots_leases_and_delivery_rows(notify_env):
    from task_server.services.ui_execution_store import UiExecutionStore
    batch,report,_,_=ready();delivery=enqueue(batch,report)
    db=UiExecutionStore()._connect()
    try:
        # Reproduce the previous v2 outbox table while retaining the existing batch tables.
        db.execute('DROP TABLE delivery_outbox')
        db.execute('CREATE TABLE delivery_outbox(delivery_key TEXT PRIMARY KEY,ui_batch_id TEXT NOT NULL REFERENCES batches(ui_batch_id),channel_id TEXT NOT NULL,report_revision TEXT NOT NULL,status TEXT NOT NULL,due_ns INTEGER NOT NULL,attempts INTEGER NOT NULL DEFAULT 0)')
        db.execute('INSERT INTO delivery_outbox VALUES(?,?,?,?,?,?,?)',(delivery['delivery_key'],batch['ui_batch_id'],delivery['channel_id'],report['report_revision'],'pending',time.time_ns(),0))
        db.execute('DROP INDEX ui_reports_pending')
        db.execute('ALTER TABLE batches DROP COLUMN report_requested_ns')
        db.execute('ALTER TABLE batches DROP COLUMN report_request_json')
        db.execute('PRAGMA user_version=2')
    finally:db.close()
    before=UiExecutionStore().get_batch(batch['ui_batch_id'])
    assert before['snapshots']==batch['snapshots'] and before['selection']==batch['selection']
    db=UiExecutionStore()._connect()
    try:
        assert db.execute('PRAGMA user_version').fetchone()[0]==5
        assert db.execute('SELECT count(*) FROM delivery_outbox').fetchone()[0]==1
        assert db.execute('SELECT manual_retries FROM delivery_outbox').fetchone()[0]==0
    finally:db.close()


def test_delivery_status_can_be_filtered_to_exact_report_revision(notify_env):
    from task_server.services import ui_batch_notification_service as notify, ui_batch_report_service as reports
    batch,first,job,event=ready();enqueue(batch,first)
    reports.save_ui_artifact(job['job_id'],event,b'late','html')
    with actor_context(ACTORS['a']):second=reports.build_ui_batch_report(None,batch['ui_batch_id'])
    enqueue(batch,second)
    with actor_context(ACTORS['a']):
        result=notify.get_batch_deliveries(None,batch['ui_batch_id'],first['report_revision'])
        assert [item['report_revision'] for item in result['items']]==[first['report_revision']]
        with pytest.raises(ValueError):notify.get_batch_deliveries(None,batch['ui_batch_id'],'latest')


def test_v2_inflight_migration_does_not_retry_unknown_outcome(notify_env):
    from task_server.services.ui_execution_store import UiExecutionStore
    batch,report,_,_=ready()
    db=UiExecutionStore()._connect()
    try:
        db.execute('DROP TABLE delivery_outbox')
        db.execute('CREATE TABLE delivery_outbox(delivery_key TEXT PRIMARY KEY,ui_batch_id TEXT NOT NULL REFERENCES batches(ui_batch_id),channel_id TEXT NOT NULL,report_revision TEXT NOT NULL,status TEXT NOT NULL,due_ns INTEGER NOT NULL,attempts INTEGER NOT NULL DEFAULT 0)')
        db.execute('INSERT INTO delivery_outbox VALUES(?,?,?,?,?,?,?)',('v2',batch['ui_batch_id'],batch['selection']['notifications']['channel_id'],report['report_revision'],'sending',time.time_ns(),1))
        db.execute('DROP INDEX ui_reports_pending')
        db.execute('ALTER TABLE batches DROP COLUMN report_requested_ns')
        db.execute('ALTER TABLE batches DROP COLUMN report_request_json')
        db.execute('PRAGMA user_version=2')
    finally:db.close()
    db=UiExecutionStore()._connect()
    try:assert db.execute('SELECT status,error_code FROM delivery_outbox').fetchone()[:]==('unknown','worker_interrupted')
    finally:db.close()


def test_enqueue_requires_an_explicit_immutable_revision(notify_env):
    from task_server.services import ui_batch_notification_service as notify
    batch,report,_,_=ready()
    with pytest.raises(ValueError,match='report_revision_invalid'):
        notify.enqueue_batch_delivery(batch['ui_batch_id'],None,batch['selection']['notifications']['channel_id'])


def test_new_report_revision_marks_pending_but_existing_digest_reuses_delivery_state(notify_env):
    from task_server.services import ui_batch_notification_service as notify, ui_batch_report_service as reports
    batch,first,job,event=ready();enqueue(batch,first);notify._finish(notify._claim_due(1)[0],'sent','')
    reports.save_ui_artifact(job['job_id'],event,b'late','html')
    with actor_context(ACTORS['a']):
        second=reports.build_ui_batch_report(None,batch['ui_batch_id'])
        assert second['report_revision']!=first['report_revision']
        assert notify.UiExecutionStore().get_batch(batch['ui_batch_id'])['delivery_status']=='pending'
        enqueue(batch,second);notify._finish(notify._claim_due(1)[0],'sent','')
        assert reports.build_ui_batch_report(None,batch['ui_batch_id'])['report_revision']==second['report_revision']
        assert notify.UiExecutionStore().get_batch(batch['ui_batch_id'])['delivery_status']=='sent'


def test_normal_enqueue_tick_uses_pending_index_not_all_notified_history(notify_env,monkeypatch):
    import hashlib
    from task_server.services import ui_batch_notification_service as notify, ui_batch_report_service as reports
    batch,report,_,_=ready();enqueue(batch,report);notify._finish(notify._claim_due(1)[0],'sent','')
    db=reports._connect()
    try:
        batch_row=dict(db.execute('SELECT * FROM batches WHERE ui_batch_id=?',(batch['ui_batch_id'],)).fetchone())
        report_row=dict(db.execute('SELECT * FROM ui_report_snapshots WHERE ui_batch_id=?',(batch['ui_batch_id'],)).fetchone())
        db.execute('BEGIN IMMEDIATE')
        for index in range(2000):
            identifier='uib_'+f'{index:032x}'
            row={**batch_row,'ui_batch_id':identifier,'idempotency_key':f'history-{index}'}
            columns=','.join(row);marks=','.join('?' for _ in row)
            db.execute(f'INSERT INTO batches({columns}) VALUES({marks})',tuple(row.values()))
            row={**report_row,'ui_batch_id':identifier};columns=','.join(row);marks=','.join('?' for _ in row)
            db.execute(f'INSERT INTO ui_report_snapshots({columns}) VALUES({marks})',tuple(row.values()))
            db.execute('INSERT INTO ui_report_heads VALUES(?,?)',(identifier,report['report_revision']))
            db.execute("INSERT INTO delivery_outbox(delivery_key,ui_batch_id,channel_id,report_revision,status,due_ns) VALUES(?,?,?,?, 'sent',0)",
                       (hashlib.sha256(identifier.encode()).hexdigest(),identifier,batch['selection']['notifications']['channel_id'],report['report_revision']))
        db.execute('COMMIT')
    finally:db.close()
    original=reports._connect;instructions=[0];queries=[]
    def connect():
        db=original()
        def progress():instructions[0]+=100;return 0
        db.set_progress_handler(progress,100);db.set_trace_callback(queries.append)
        return db
    monkeypatch.setattr(reports,'_connect',connect)
    assert notify.enqueue_ready_batch_deliveries(20)==0
    assert instructions[0]<3000
    query=next(q for q in queries if 'SELECT b.ui_batch_id' in q)
    db=original()
    try:plan=' '.join(row[3] for row in db.execute('EXPLAIN QUERY PLAN '+query))
    finally:db.close()
    assert 'batches_notify_ready' in plan


def test_notify_grant_revoked_after_queue_fails_closed_and_rerun_opts_out(notify_env, monkeypatch):
    from task_server import identity
    from task_server.services import ui_batch_notification_service as notify, ui_batch_dispatch as dispatch, feishu_service
    from task_server.services.ui_execution_store import UiExecutionStore
    batch, report, _, _=ready(); enqueue(batch,report)
    original=identity.get_access_profile
    monkeypatch.setattr(identity,'get_access_profile',lambda name:{**original(name),'permissions':['ui.view','ui.execute']})
    monkeypatch.setattr(feishu_service,'send_feishu_card',lambda *a,**k:pytest.fail('grant revoked before send'))
    assert notify.process_due_batch_deliveries(1)==1
    with actor_context(ACTORS['a']):
        item=notify.get_batch_deliveries(None,batch['ui_batch_id'])['items'][0]
        assert item['status']=='failed' and item['error_code']=='owner_access_revoked'
        rerun=dispatch.rerun_ui_batch(None,batch['ui_batch_id'],'all','revoked-rerun')
        repeat=dispatch.rerun_ui_batch(None,batch['ui_batch_id'],'all','revoked-rerun')
    assert rerun['selection']['notifications']['enabled'] is False
    assert rerun['delivery_status']=='not_requested' and repeat['ui_batch_id']==rerun['ui_batch_id']
    assert UiExecutionStore().get_batch(batch['ui_batch_id'])['selection']['notifications']['enabled'] is True
    with actor_context(ACTORS['a']):
        from task_server.services.ui_batch_report_service import get_ui_batch_report
        assert get_ui_batch_report(None,batch['ui_batch_id'],report['report_revision'])['report_revision']==report['report_revision']


def test_feature_off_notification_worker_is_paused_unless_explicitly_enabled(notify_env, monkeypatch):
    from task_server.services import ui_batch_notification_service as notify, feishu_service
    batch,report,_,_=ready();enqueue(batch,report);calls=[]
    monkeypatch.setattr(feishu_service,'send_feishu_card',lambda *a,**k:calls.append(a) or {'code':0})
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED','false');monkeypatch.setattr(notify,'_WORKER',None)
    notify.tick_batch_notification_worker()
    assert notify._WORKER is None and notify.process_due_batch_deliveries(1)==0 and calls==[]
    with actor_context(ACTORS['a']): assert notify.get_batch_deliveries(None,batch['ui_batch_id'])['items'][0]['status']=='pending'
    monkeypatch.setenv('UI_BATCH_NOTIFICATIONS_ENABLED','true')
    assert notify.process_due_batch_deliveries(1)==1 and len(calls)==1
