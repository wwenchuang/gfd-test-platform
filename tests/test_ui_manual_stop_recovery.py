"""Explicit owner-confirmed recovery never infers execution success."""
import sqlite3
import time
import pytest
from test_ui_batch_recovery import dispatch_env, environment, claimed, event
from test_ui_batch_dispatch import create
from test_ui_execution_service import ACTORS
from task_server.services.operation_attribution import actor_context
from task_server.services.ui_execution_store import UiExecutionStore, BatchConflict


def interrupted():
    batch, job, payload = claimed()
    UiExecutionStore().interrupt_attempt(batch['case_attempts'][0]['attempt_id'], 'execution_ack_lost')
    return UiExecutionStore().get_batch(batch['ui_batch_id']), job, payload


def confirmation(batch):
    a = batch['case_attempts'][0]
    return dict(attempt_id=a['attempt_id'], expected_status=a['status'], expected_event_sequence=a['event_sequence'],
                expected_recovery_version=a['manual_stop_recovery_version'], confirmed_stopped=True, reason='已在执行机核对进程结束')


def recover(batch, body=None, actor='a'):
    from task_server.services.ui_batch_dispatch import confirm_ui_attempt_stopped
    with actor_context(ACTORS[actor]):
        return confirm_ui_attempt_stopped(ACTORS[actor], batch['ui_batch_id'], body or confirmation(batch))


def test_recovery_durable_identity_revokes_claim_and_is_idempotent(dispatch_env):
    batch, job, payload = interrupted()
    old_artifact = __import__('task_server.services.ui_batch_report_service', fromlist=['save_ui_artifact']).save_ui_artifact(job['job_id'], event(payload), b'prior', 'html')
    saved = recover(batch)
    a = saved['case_attempts'][0]
    assert a['status'] == 'interrupted' and a['stop_confirmed'] and not a['stop_pending']
    assert saved['execution_settled'] and saved['report_status'] == 'not_requested'
    assert saved['report_requested_ns'] is None and saved['delivery_status'] == 'not_requested'
    assert a['manual_stop_recovery']['actor_user_id'] == 'u-a'
    assert a['manual_stop_recovery']['job_id'] == job['job_id']
    assert a['manual_stop_recovery']['device_id'] == 'iphone'
    assert recover(batch)['case_attempts'][0]['manual_stop_recovery'] == a['manual_stop_recovery']
    db = UiExecutionStore()._connect()
    assert db.execute('SELECT count(*) FROM ui_manual_stop_recoveries').fetchone()[0] == 1
    assert db.execute('SELECT count(*) FROM device_leases').fetchone()[0] == 0
    assert db.execute('SELECT content FROM ui_report_artifacts WHERE artifact_id=?', (old_artifact['artifact_id'],)).fetchone()[0] == b'prior'
    db.close()
    from task_server.services import ui_batch_dispatch as d, ui_batch_report_service as r
    for kind in ('progress', 'result', 'report_ready'):
        with pytest.raises(PermissionError): d.record_ui_attempt_event(job['job_id'], 2, event(payload, type=kind, status='success', execution_stopped=True))
    with pytest.raises(PermissionError): r.save_ui_artifact(job['job_id'],event(payload),b'late','html')
    from task_server.services.ui_batch_report_service import _connect, _snapshot
    db = _connect(); snapshot = _snapshot(saved, db); db.close()
    assert snapshot['status'] == 'interrupted'
    assert snapshot['attempts'][0]['manual_stop_recovery']['actor_user_id'] == 'u-a'
    new = create('a', 'new'); d.dispatch_ui_batch(new['ui_batch_id'])
    from task_server.services import job_service
    new_payload = d.claim_ui_job(job_service.get_job(new['case_attempts'][0]['dispatch_key']))
    assert new_payload['ui_claim_token'] != payload['ui_claim_token']
    recover(batch)
    db=UiExecutionStore()._connect()
    assert db.execute('SELECT attempt_id FROM device_leases').fetchone()[0] == new['case_attempts'][0]['attempt_id']
    db.close()


@pytest.mark.parametrize('body_change', [{'confirmed_stopped':False}, {'confirmed_stopped':1}, {'reason':''}, {'actor_user_id':'fake'}, {'device_id':'other'}])
def test_confirmation_is_explicit_and_browser_cannot_rewrite_identity(dispatch_env, body_change):
    batch, _, _ = interrupted()
    with pytest.raises(ValueError): recover(batch, {**confirmation(batch), **body_change})
    assert UiExecutionStore().get_batch(batch['ui_batch_id'])['stop_pending']


def test_owner_viewer_and_current_scope(dispatch_env, monkeypatch):
    batch, _, _ = interrupted()
    assert recover(batch, actor='b') is None
    from task_server import identity
    original = identity.get_access_profile
    monkeypatch.setattr(identity,'get_access_profile',lambda name:{**original(name),'permissions':['ui.view']})
    with pytest.raises(PermissionError): recover(batch)
    monkeypatch.setattr(identity,'get_access_profile',lambda name:{**original(name),'scope':{'ui_apps':[]}})
    for name in ('a','admin'):
        with pytest.raises(PermissionError): recover(batch,actor=name)
    monkeypatch.setattr(identity,'get_access_profile',original)
    assert recover(batch, actor='admin')['case_attempts'][0]['manual_stop_recovery']['actor_user_id'] == 'u-admin'


@pytest.mark.parametrize('status', ['running','queued','passed','failed','cancelled','cancelling'])
def test_active_unclaimed_terminal_and_fresh_cancelling_refused(dispatch_env, status):
    batch, _, _ = interrupted(); db = UiExecutionStore()._connect()
    db.execute('UPDATE case_attempts SET status=?', (status,)); db.close()
    batch = UiExecutionStore().get_batch(batch['ui_batch_id'])
    with pytest.raises(ValueError): recover(batch)


def test_stale_cancelling_uses_ack_threshold_and_finishes_cancelled(dispatch_env):
    batch, _, _ = interrupted(); db = UiExecutionStore()._connect()
    db.execute("UPDATE case_attempts SET status='cancelling',last_event_ns=?", (time.time_ns()-121*10**9,)); db.close()
    batch = UiExecutionStore().get_batch(batch['ui_batch_id'])
    assert batch['case_attempts'][0]['manual_stop_recovery_eligible']
    assert recover(batch)['status'] == 'cancelled'


def test_callback_and_state_race_conflict_does_not_release(dispatch_env):
    from task_server.services import ui_batch_dispatch as d
    batch, job, payload = interrupted()
    d.record_ui_attempt_event(job['job_id'], 1, event(payload,type='progress'))
    with pytest.raises(BatchConflict): recover(batch)
    fresh = UiExecutionStore().get_batch(batch['ui_batch_id'])
    UiExecutionStore().cancel_batch(batch['ui_batch_id'])
    with pytest.raises(BatchConflict): recover(fresh)
    assert UiExecutionStore().get_batch(batch['ui_batch_id'])['stop_pending']


@pytest.mark.parametrize('mutation', ['lease_other','lease_missing','unclaimed','released'])
def test_exact_claimed_lease_only(dispatch_env, mutation):
    batch, _, _ = interrupted(); db = UiExecutionStore()._connect()
    if mutation=='lease_other': db.execute("UPDATE device_leases SET runner_id='other'")
    if mutation=='lease_missing': db.execute('DELETE FROM device_leases')
    if mutation=='unclaimed': db.execute("UPDATE case_attempts SET claim_token=''")
    if mutation=='released': db.execute('UPDATE case_attempts SET stop_confirmed=1')
    db.close()
    with pytest.raises(BatchConflict): recover(batch)


def test_audit_insert_failure_rolls_back_release_and_credential(dispatch_env):
    batch, job, payload = interrupted(); db = UiExecutionStore()._connect()
    db.execute("CREATE TRIGGER reject_recovery BEFORE INSERT ON ui_manual_stop_recoveries BEGIN SELECT RAISE(ABORT,'audit failed'); END")
    db.close()
    with pytest.raises(sqlite3.IntegrityError): recover(batch)
    saved=UiExecutionStore().get_batch(batch['ui_batch_id'])
    assert saved['stop_pending'] and not saved['execution_settled']
    from task_server.services.ui_batch_report_service import validate_ui_upload
    assert validate_ui_upload(job['job_id'],event(payload))['claim_token'] == payload['ui_claim_token']

@pytest.mark.parametrize('mutation', ['claim','lease'])
def test_same_sequence_with_replaced_claim_or_lease_conflicts(dispatch_env, mutation):
    batch, _, _ = interrupted(); db=UiExecutionStore()._connect()
    if mutation=='claim': db.execute("UPDATE case_attempts SET claim_token='new claim'")
    else: db.execute('UPDATE device_leases SET acquired_ns=acquired_ns+1')
    db.close()
    with pytest.raises(BatchConflict): recover(batch)

@pytest.mark.parametrize('operation', ['artifact','chunk','finish'])
def test_upload_window_rechecks_revocation_before_persisting(dispatch_env, monkeypatch, operation):
    import base64
    from task_server.services import ui_batch_report_service as r, ui_batch_report_upload as u
    batch, job, payload=interrupted()
    credentials=event(payload)
    class Handler:
        def _json(self,data,status=200): self.response=data,status
    body={**credentials,'job_id':job['job_id'],'upload_id':'upload-1','total':1,'index':0,'contentBase64':base64.b64encode(b'late html').decode()}
    if operation=='finish': u.handle_chunk_upload(Handler(),body)
    module=r if operation=='artifact' else u
    original=module.validate_ui_upload
    def validate_then_revoke(*args):
        result=original(*args)
        recover(batch)
        return result
    monkeypatch.setattr(module,'validate_ui_upload',validate_then_revoke)
    if operation=='artifact':
        with pytest.raises(PermissionError): r.save_ui_artifact(job['job_id'],credentials,b'late html','html')
    else:
        h=Handler();u.handle_chunk_upload(h,body,finish=operation=='finish');assert h.response[1]==403
    db=r._connect()
    assert db.execute('SELECT count(*) FROM ui_report_artifacts').fetchone()[0]==0
    if operation=='chunk': assert db.execute('SELECT count(*) FROM ui_report_chunks').fetchone()[0]==0
    db.close()


def test_http_route_access_feature_flag_and_conflict(dispatch_env, monkeypatch):
    import re
    from task_server import router, identity
    from task_server.access_control import MainAccess
    batch, job, payload=interrupted()
    path='/api/ui-executions/'+batch['ui_batch_id']+'/confirm-stop'
    MainAccess(identity.get_access_profile('a'),catalog=[]).check('POST',path,{}, {})
    viewer={**identity.get_access_profile('a'),'permissions':['ui.view']}
    with pytest.raises(PermissionError): MainAccess(viewer,catalog=[]).check('POST',path,{}, {})
    class Handler:
        def _body(self): return confirmation(batch)
        def _json(self,data,status=200): self.response=data,status
    handler=Handler();match=re.match(r'^/api/ui-executions/([^/]+)/confirm-stop$',path)
    from task_server.services import ui_batch_dispatch as d
    d.record_ui_attempt_event(job['job_id'],1,event(payload,type='progress'))
    with actor_context(ACTORS['a']): router._post_ui_execution_confirm_stop(handler,{},match)
    assert handler.response[1]==409 and handler.response[0]['code']=='recovery_state_changed'
    with actor_context(ACTORS['b']): router._post_ui_execution_confirm_stop(handler,{},match)
    assert handler.response[1]==404
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED','0')
    with actor_context(ACTORS['a']): router._post_ui_execution_confirm_stop(handler,{},match)
    assert handler.response[1]==409 and handler.response[0]['code']=='recovery_state_changed'
    batch=UiExecutionStore().get_batch(batch['ui_batch_id'])
    with actor_context(ACTORS['a']): router._post_ui_execution_confirm_stop(handler,{},match)
    assert handler.response[1]==200 and handler.response[0]['batch']['status']=='interrupted'
    assert handler.response[0]['batch']['execution_settled'] is True


def test_schema3_upgrade_preserves_artifact_frozen_report_and_outbox(dispatch_env):
    from task_server.services import ui_batch_report_service as r
    batch,job,payload=interrupted()
    artifact=r.save_ui_artifact(job['job_id'],event(payload),b'prior html','html')
    db=r._connect()
    db.execute("INSERT INTO ui_report_snapshots(ui_batch_id,report_revision,snapshot_json,created_ns,status,markdown) VALUES(?,?,?,?,?,?)",(batch['ui_batch_id'],'old-revision','{}',1,'ready',b'frozen'))
    db.execute("INSERT INTO delivery_outbox(delivery_key,ui_batch_id,channel_id,report_revision,status,due_ns) VALUES(?,?,?,?,?,?)",('old-delivery',batch['ui_batch_id'],'old-channel','old-revision','sent',1))
    db.execute('DROP TABLE ui_manual_stop_recoveries')
    db.execute('DROP INDEX ui_reports_pending')
    db.execute('ALTER TABLE batches DROP COLUMN report_requested_ns')
    db.execute('ALTER TABLE batches DROP COLUMN report_request_json')
    db.execute('PRAGMA user_version=3');db.close()
    db=r._connect()
    assert db.execute('PRAGMA user_version').fetchone()[0]==5
    assert db.execute('SELECT markdown FROM ui_report_snapshots').fetchone()[0]==b'frozen'
    assert db.execute('SELECT content FROM ui_report_artifacts WHERE artifact_id=?',(artifact['artifact_id'],)).fetchone()[0]==b'prior html'
    assert db.execute('SELECT status FROM delivery_outbox').fetchone()[0]=='sent'
    db.close();recover(batch)
    db=r._connect()
    assert db.execute('SELECT markdown FROM ui_report_snapshots').fetchone()[0]==b'frozen'
    assert db.execute('SELECT status FROM delivery_outbox').fetchone()[0]=='sent';db.close()


def test_recovery_job_sync_is_pending_then_replayed_without_rerun(dispatch_env):
    from task_server.services import ui_batch_dispatch as d, job_service
    batch,job,_=interrupted()
    saved=recover(batch)
    assert saved['needs_job_sync'] and saved['case_attempts'][0]['job_sync_pending']
    d.reconcile_ui_batches()
    saved=UiExecutionStore().get_batch(batch['ui_batch_id'])
    assert not saved['needs_job_sync'] and job_service.get_job(job['job_id'])['status']=='failed'
    assert len(job_service.load_jobs(limit=None))==1
    assert saved['case_attempts'][0]['status']=='interrupted'


def test_chunk_finish_revoked_after_assembly_cannot_persist_artifact(dispatch_env, monkeypatch):
    import base64
    from task_server.services import ui_batch_report_upload as u, ui_batch_report_service as r
    batch,job,payload=interrupted()
    body={**event(payload),'job_id':job['job_id'],'upload_id':'assembled','total':1,'index':0,'contentBase64':base64.b64encode(b'assembled bytes').decode()}
    class Handler:
        def _json(self,data,status=200): self.response=data,status
    u.handle_chunk_upload(Handler(),body)
    original=u.save_ui_artifact
    def revoke_after_assembly(*args):
        recover(batch)
        return original(*args)
    monkeypatch.setattr(u,'save_ui_artifact',revoke_after_assembly)
    h=Handler();u.handle_chunk_upload(h,body,finish=True)
    assert h.response[1]==403
    db=r._connect();assert db.execute('SELECT count(*) FROM ui_report_artifacts').fetchone()[0]==0;db.close()


def test_failure_after_audit_insert_rolls_back_audit_and_release(dispatch_env):
    batch,job,payload=interrupted();db=UiExecutionStore()._connect()
    db.execute("CREATE TRIGGER reject_release BEFORE DELETE ON device_leases BEGIN SELECT RAISE(ABORT,'release failed'); END")
    db.close()
    with pytest.raises(sqlite3.IntegrityError): recover(batch)
    db=UiExecutionStore()._connect()
    assert db.execute('SELECT count(*) FROM ui_manual_stop_recoveries').fetchone()[0]==0
    assert db.execute('SELECT count(*) FROM device_leases').fetchone()[0]==1
    assert db.execute('SELECT claim_token,stop_confirmed,finished_ns FROM case_attempts').fetchone()[:] == (payload['ui_claim_token'],0,None)
    db.close()
