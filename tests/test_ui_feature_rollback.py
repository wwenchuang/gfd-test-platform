"""Feature-off admission versus private evidence/recovery, using real HTTP and stores."""
import json
import time
import pytest
from test_identity import identity_db, create_member, activate
from test_operation_http import operation_server, request as http
from test_ui_execution_service import environment, ACTORS, APP, request
from test_ui_batch_dispatch import dispatch_env, create
from test_ui_batch_recovery import claimed, event
from test_ui_manual_stop_recovery import confirmation
from task_server.services.operation_attribution import actor_context


def test_feature_off_existing_report_http_and_cancel_keep_authorization(operation_server, dispatch_env, identity_db, monkeypatch):
    from task_server import identity, auth
    from task_server.services import ui_batch_dispatch as dispatch, job_service, ui_batch_report_service as reports
    from task_server.services.ui_execution_store import UiExecutionStore
    monkeypatch.setattr(reports, '_render_docx', lambda _: b'docx test stub')
    monkeypatch.setattr(reports, '_WORKER', None)
    monkeypatch.setattr(identity, 'get_access_profile', identity_db[1].get_access_profile)
    create_member(identity_db[1], scope={'ui_apps':[APP], 'api_projects':[], 'api_environments':[]})
    activate(identity_db[1])
    server, _ = operation_server
    admin, member = auth.create_session_token(), auth.create_session_token('member')
    status, raw = http(server, 'POST', '/api/ui-executions', admin, request(), {'Idempotency-Key':'ready'})
    assert status == 201, raw
    batch = json.loads(raw)['batch']; bid = batch['ui_batch_id']; path = '/api/ui-executions/'+bid
    dispatch.dispatch_ui_batch(bid)
    job = job_service.get_job(batch['case_attempts'][0]['dispatch_key']); payload = dispatch.claim_ui_job(job)
    evidence = event(payload, type='result', status='failed', execution_stopped=True)
    artifact = reports.save_ui_artifact(job['job_id'], evidence, b'private evidence', 'html')
    dispatch.record_ui_attempt_event(job['job_id'], 1, evidence)
    status, raw = http(server, 'POST', path+'/report', admin, {})
    assert status == 202, raw
    reports.tick_ui_report_worker(); reports._WORKER.join(3)
    status, raw = http(server, 'GET', path+'/report', admin)
    assert status == 200 and json.loads(raw)['report']['status'] == 'ready'
    revision = json.loads(raw)['report']['report_revision']
    status, raw = http(server, 'POST', '/api/ui-executions', admin, request(), {'Idempotency-Key':'queued'})
    assert status == 201, raw
    queued = json.loads(raw)['batch']['ui_batch_id']
    active_body=request(); active_body['targets']=[{'runner_id':'mac','device_id':'phone2','platform':'ios'}]
    status, raw=http(server,'POST','/api/ui-executions',admin,active_body,{'Idempotency-Key':'active'})
    assert status==201,raw
    active=json.loads(raw)['batch']; active_path='/api/ui-executions/'+active['ui_batch_id']
    dispatch.dispatch_ui_batch(active['ui_batch_id'])
    active_job=job_service.get_job(active['case_attempts'][0]['dispatch_key']); active_payload=dispatch.claim_ui_job(active_job)
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED', 'false')
    status, raw = http(server, 'GET', '/api/ui-executions/availability', admin)
    assert status == 200 and json.loads(raw)['evidence_available'] is True
    for url in ['/api/ui-executions', path, path+'/report?revision='+revision,
                path+'/report/download?format=html&revision='+revision, path+'/summary',
                path+'/artifacts/'+artifact['artifact_id'], path+'/deliveries?revision='+revision]:
        assert http(server, 'GET', url, admin)[0] == 200, url
        if url != '/api/ui-executions':
            assert http(server, 'GET', url, member)[0] == 404, url
            assert http(server, 'GET', url)[0] == 401, url
    assert http(server, 'POST', '/api/ui-executions/'+queued+'/cancel', admin, {})[0] == 200
    assert UiExecutionStore().get_batch(queued)['status'] == 'cancelled'
    assert http(server,'POST',active_path+'/cancel',member,{})[0]==404
    status,raw=http(server,'POST',active_path+'/cancel',admin,{})
    assert status==200 and json.loads(raw)['batch']['status']=='cancelling'
    # A cancelling attempt only allows manual recovery after its unchanged 120s ACK window.
    db=UiExecutionStore()._connect()
    try: db.execute('UPDATE case_attempts SET last_event_ns=? WHERE ui_batch_id=?',(time.time_ns()-121*10**9,active['ui_batch_id']))
    finally: db.close()
    saved=json.loads(http(server,'GET',active_path,admin)[1])['batch']
    assert http(server,'POST',active_path+'/confirm-stop',member,confirmation(saved))[0]==404
    status,raw=http(server,'POST',active_path+'/confirm-stop',admin,confirmation(saved))
    assert status==200 and json.loads(raw)['batch']['status']=='cancelled'
    with pytest.raises(PermissionError):
        dispatch.record_ui_attempt_event(active_job['job_id'],1,event(active_payload,type='result',status='success',execution_stopped=True))
    for url, body in [('/api/ui-executions',request()),('/api/ui-executions/preflight',request()),(path+'/rerun',{'scope':'all'})]:
        assert http(server, 'POST', url, admin, body, {'Idempotency-Key':'off'})[0] == 404
    # Current app scope still applies after rollback.
    original=identity.get_access_profile
    monkeypatch.setattr(identity,'get_access_profile',lambda name:{**original(name),'scope':{'ui_apps':[]}})
    assert http(server,'GET',path+'/report?revision='+revision,admin)[0] == 403


def test_feature_off_restart_drains_job_sync_reports_and_interrupts_without_dispatch(dispatch_env, monkeypatch):
    from task_server.services import ui_batch_dispatch as dispatch, job_service, ui_batch_report_service as reports
    from task_server.services.ui_execution_store import UiExecutionStore
    monkeypatch.setattr(reports, '_render_docx', lambda _: b'docx test stub')
    monkeypatch.setattr(reports, '_WORKER', None)
    batch, job, payload = claimed()
    queued = create('b')
    original = job_service.update_job
    monkeypatch.setattr(job_service, 'update_job', lambda *a,**k: (_ for _ in ()).throw(RuntimeError('after SQLite commit')))
    with pytest.raises(RuntimeError):
        dispatch.record_ui_attempt_event(job['job_id'], 1, event(payload,type='result',status='failed',execution_stopped=True))
    monkeypatch.setattr(job_service, 'update_job', original)
    # Another already-claimed phone must still detect loss after rollback.
    active = create('a','active',targets=[{'runner_id':'mac','device_id':'phone2','platform':'ios'}])
    dispatch.dispatch_ui_batch(active['ui_batch_id'])
    active_job=job_service.get_job(active['case_attempts'][0]['dispatch_key'])
    active_payload=dispatch.claim_ui_job(active_job)
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED','false')
    dispatch_env[0]['mac']['online']=False
    monkeypatch.setattr(job_service,'create_job',lambda *a,**k:pytest.fail('rollback must not dispatch'))
    monkeypatch.setattr(UiExecutionStore,'claim_attempt',lambda *a,**k:pytest.fail('rollback must not claim'))
    dispatch.reconcile_ui_batches()
    assert job_service.get_job(job['job_id'])['status'] == 'failed'
    saved=UiExecutionStore().get_batch(active['ui_batch_id'])
    assert saved['status']=='interrupted' and not saved['execution_settled']
    assert dispatch.claim_ui_job(active_job, prepared=active_payload) is None
    assert job_service.get_job(queued['case_attempts'][0]['dispatch_key']) is None
    assert UiExecutionStore().get_batch(queued['ui_batch_id'])['status']=='queued'
    with actor_context(ACTORS['a']):
        recovered=dispatch.confirm_ui_attempt_stopped(None,active['ui_batch_id'],confirmation(saved))
    assert recovered['status']=='interrupted' and recovered['execution_settled']
    with pytest.raises(PermissionError):
        dispatch.record_ui_attempt_event(active_job['job_id'],2,event(active_payload,type='result',status='success',execution_stopped=True))
    for _ in range(2):
        reports.tick_ui_report_worker(); reports._WORKER.join(3)
    assert UiExecutionStore().get_batch(batch['ui_batch_id'])['report_status']=='ready'
    assert UiExecutionStore().get_batch(active['ui_batch_id'])['report_status']=='ready'
    # Re-enable admission; the drained jobs remain terminal and aren't recreated.
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED','1');dispatch_env[0]['mac']['online']=True
    monkeypatch.setattr(job_service,'create_job',lambda *a,**k:pytest.fail('only inspect drained batches'))
    dispatch.dispatch_ui_batch(batch['ui_batch_id']);dispatch.dispatch_ui_batch(active['ui_batch_id'])


def test_feature_off_startup_uses_existing_store_only(environment, monkeypatch):
    from task_server.services import ui_batch_dispatch as dispatch, ui_batch_report_service as reports, ui_batch_notification_service as notify
    from task_server.services.ui_execution_store import default_db_path
    calls=[]
    class Worker:
        def __init__(self, target, **kwargs): self.target=target
        def is_alive(self): return False
        def start(self): calls.append('start')
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED','false')
    monkeypatch.setattr(dispatch,'_WORKER',None);monkeypatch.setattr(dispatch.threading,'Thread',Worker)
    dispatch.start_ui_batch_reconciler();dispatch.reconcile_ui_batches()
    assert calls==[] and not default_db_path().exists()
    from task_server.services.ui_execution_service import get_ui_batch, list_ui_batches
    with actor_context(ACTORS['a']):
        assert get_ui_batch(None,'uib_'+'a'*32) is None
        assert list_ui_batches(None)['items']==[]
    assert not default_db_path().exists()
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED','1');create()
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED','false')
    dispatch.start_ui_batch_reconciler();assert calls==['start']
    monkeypatch.setattr(dispatch,'reconcile_ui_batches',lambda:calls.append('drain'))
    monkeypatch.setattr(reports,'tick_ui_report_worker',lambda:calls.append('report'))
    monkeypatch.setattr(notify,'tick_batch_notification_worker',lambda:calls.append('notify'))
    class EndTick(Exception): pass
    monkeypatch.setattr(dispatch.time,'sleep',lambda _:(_ for _ in ()).throw(EndTick()))
    with pytest.raises(EndTick): dispatch._WORKER.target()
    assert calls==['start','drain','report']  # no implicit notification on restart


def test_feature_off_trusted_admission_and_dispatch_have_no_store_side_effects(environment, monkeypatch):
    from task_server.services import ui_execution_service as service, ui_batch_dispatch as dispatch
    from task_server.services.ui_execution_store import default_db_path
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED','false')
    with actor_context(ACTORS['a']):
        for operation in (lambda:service.create_ui_batch(None,request(),'off'),
                          lambda:service.preflight_ui_batch(None,request()),
                          lambda:dispatch.rerun_ui_batch(None,'uib_'+'a'*32,'all','off')):
            with pytest.raises(service.BatchValidationError,match='feature_disabled'):operation()
    assert dispatch.dispatch_ui_batch('uib_'+'a'*32) is None
    assert not default_db_path().exists()


def test_http_tester_cannot_request_notify_but_can_execute_without_it(operation_server, environment, identity_db, monkeypatch):
    from task_server import identity,auth
    monkeypatch.setattr(identity,'get_access_profile',identity_db[1].get_access_profile)
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED','1')
    create_member(identity_db[1],scope={'ui_apps':[APP],'api_projects':[],'api_environments':[]});activate(identity_db[1])
    server,_=operation_server;token=auth.create_session_token('member')
    body=request();body['notifications']['enabled']=True
    for path in ('/api/ui-executions','/api/ui-executions/preflight'):
        assert http(server,'POST',path,token,body,{'Idempotency-Key':'forbidden'})[0]==403
    for key,body in [('false',request()),('omitted',{k:v for k,v in request().items() if k!='notifications'})]:
        status,raw=http(server,'POST','/api/ui-executions',token,body,{'Idempotency-Key':key})
        assert status==201,raw
        assert json.loads(raw)['batch']['selection']['notifications']['enabled'] is False
