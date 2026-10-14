"""Ordinary execution keeps evidence; release reports require an explicit request."""
import json
import pytest

from test_ui_batch_report_service import completed
from test_ui_batch_dispatch import dispatch_env, create
from test_ui_execution_service import environment, ACTORS
from task_server.services.operation_attribution import actor_context
from test_ui_batch_notification import notify_env


def test_release_checkbox_freezes_intent_and_only_renders_after_settled(dispatch_env, monkeypatch):
    from test_ui_execution_service import request
    from task_server.services import ui_execution_service as service, ui_batch_dispatch as dispatch, ui_batch_report_service as reports, job_service
    body = {**request(), 'release_report':{'enabled':True}}
    with actor_context(ACTORS['a']):
        batch = service.create_ui_batch(None,body,'release')
        again = service.create_ui_batch(None,body,'release')
    assert batch == again and batch['report_status'] == 'pending'
    assert batch['report_request']['actor']['user_id'] == ACTORS['a']['user_id']
    assert batch['report_requested_ns'] == batch['report_request']['requested_ns']
    assert batch['report_request']['notifications']['enabled'] is False
    calls=[]
    monkeypatch.setattr(reports,'_render_docx',lambda content:(calls.append(content) or b'bounded renderer test'))
    monkeypatch.setattr(reports,'_WORKER',None)
    reports.tick_ui_report_worker()
    assert calls == [] and reports._WORKER is None
    dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    job=job_service.get_job(batch['case_attempts'][0]['dispatch_key'])
    event={**dispatch.claim_ui_job(job),'runner_id':'mac','device_id':'iphone','status':'failed','execution_stopped':True,'type':'result'}
    dispatch.record_ui_attempt_event(job['job_id'],1,event)
    reports.tick_ui_report_worker()
    reports._WORKER.join(3)
    assert len(calls) == 1
    with actor_context(ACTORS['a']):
        result=reports.get_ui_batch_report(None,batch['ui_batch_id'])
        assert result['status']=='ready' and result['snapshot']['counts']=={'failed':1}


@pytest.mark.parametrize('release', [True, None, {'enabled':1}, {'enabled':True,'actor':'forged'}])
def test_release_checkbox_only_accepts_explicit_boolean_shape(dispatch_env, release):
    from test_ui_execution_service import request
    from task_server.services.ui_execution_service import create_ui_batch, BatchValidationError
    with actor_context(ACTORS['a']):
        with pytest.raises(BatchValidationError,match='release_report_invalid'):
            create_ui_batch(None,{**request(),'release_report':release},'invalid')


def test_notify_cannot_request_an_ordinary_execution_report(notify_env):
    from test_ui_execution_service import request
    from task_server.services.ui_execution_service import create_ui_batch, BatchValidationError
    body=request();body['notifications']['enabled']=True
    with actor_context(ACTORS['a']):
        with pytest.raises(BatchValidationError,match='notifications_require_release_report'):
            create_ui_batch(None,body,'ordinary-notify')


def test_release_choice_changes_idempotency_intent_but_default_matches_false(dispatch_env):
    from test_ui_execution_service import request
    from task_server.services.ui_execution_service import create_ui_batch
    from task_server.services.ui_execution_store import BatchConflict
    with actor_context(ACTORS['a']):
        ordinary=create_ui_batch(None,request(),'same-click')
        again=create_ui_batch(None,{**request(),'release_report':{'enabled':False}},'same-click')
        assert again==ordinary
        with pytest.raises(BatchConflict,match='idempotency_conflict'):
            create_ui_batch(None,{**request(),'release_report':{'enabled':True}},'same-click')


def test_release_preflight_has_no_report_intent_or_render_side_effect(dispatch_env, monkeypatch):
    from test_ui_execution_service import request
    from task_server.services import ui_execution_service as service, ui_batch_report_service as reports
    monkeypatch.setattr(reports,'_render_docx',lambda _:pytest.fail('preflight rendered a release report'))
    with actor_context(ACTORS['a']):
        response=service.preflight_ui_batch(None,{**request(),'release_report':{'enabled':True}})
        assert response['ready'] is True
        assert service.list_ui_batches(None)['items']==[]


def test_ordinary_completion_never_starts_report_worker(dispatch_env, monkeypatch):
    from task_server.services import ui_batch_report_service as reports
    from task_server.services.ui_execution_store import UiExecutionStore
    batch, _, _ = completed()
    current = UiExecutionStore().get_batch(batch['ui_batch_id'])
    assert current['report_status'] == 'not_requested'
    assert current['report_requested_ns'] is None and current['report_request'] == {}
    assert current['delivery_status'] == 'not_requested'
    monkeypatch.setattr(reports, '_WORKER', None)
    monkeypatch.setattr(reports, '_render_docx', lambda _: pytest.fail('ordinary execution rendered a release report'))
    reports.tick_ui_report_worker()
    assert reports._WORKER is None
    with actor_context(ACTORS['a']):
        report = reports.get_ui_batch_report(None, batch['ui_batch_id'])
    assert report['status'] == 'not_requested' and report['snapshot'] is None
    with pytest.raises(ValueError, match='report_not_requested'):
        reports._build(batch['ui_batch_id'])


def test_ordinary_artifacts_callbacks_and_append_keep_request_disabled(dispatch_env):
    from task_server.services import ui_batch_report_service as reports, ui_batch_dispatch as dispatch
    from task_server.services.ui_execution_store import UiExecutionStore
    batch, job, event = completed()
    artifact = reports.save_ui_artifact(job['job_id'], event, b'image bytes', 'jpg')
    dispatch.record_ui_attempt_event(job['job_id'], 2, {**event, 'type':'report_ready', 'report_upload_error':'late upload'})
    dispatch.record_ui_attempt_event(job['job_id'], 3, {**event, 'stderr':'late diagnostic'})
    store = UiExecutionStore()
    current = store.get_batch(batch['ui_batch_id'])
    assert current['report_status'] == 'not_requested' and current['case_attempts'][0]['result']['stderr'] == 'late diagnostic'
    with actor_context(ACTORS['a']):
        evidence = reports.get_ui_batch_evidence(None, batch['ui_batch_id'])
        assert evidence['artifacts'] == [artifact]
        assert reports.read_ui_artifact(None, batch['ui_batch_id'], artifact['artifact_id'])[0] == b'image bytes'
    attempt = current['case_attempts'][0]
    store.append_case_attempt(batch['ui_batch_id'], attempt['case_id'], attempt['case_revision'],
        {k:attempt[k] for k in ('platform','runner_id','device_id')}, attempt['compiled_case'])
    assert store.get_batch(batch['ui_batch_id'])['report_status'] == 'not_requested'


def test_explicit_request_is_frozen_and_repeated_ready_request_does_not_queue(dispatch_env, monkeypatch):
    from task_server.services import ui_batch_report_service as reports, job_service
    from task_server.services.ui_execution_store import UiExecutionStore
    batch, _, _ = completed(status='failed')
    before = UiExecutionStore().get_batch(batch['ui_batch_id'])
    jobs = job_service.load_jobs(limit=None)
    monkeypatch.setattr(reports, '_render_docx', lambda _: b'bounded renderer test')
    with actor_context(ACTORS['admin']):
        assert reports.request_ui_batch_report(None, batch['ui_batch_id'], {})['status'] == 'pending'
        first = UiExecutionStore().get_batch(batch['ui_batch_id'])
        assert first['report_request']['actor']['user_id'] == ACTORS['admin']['user_id']
        assert first['report_requested_ns'] == first['report_request']['requested_ns']
        assert first['report_request']['notifications']['enabled'] is False
        built = reports.build_ui_batch_report(None, batch['ui_batch_id'])
        again = reports.request_ui_batch_report(None, batch['ui_batch_id'], {})
    after = UiExecutionStore().get_batch(batch['ui_batch_id'])
    assert again['status'] == 'ready' and again['report_revision'] == built['report_revision']
    assert after['report_request'] == first['report_request']
    for field in ('owner_user_id','owner_display_name','selection','snapshots','case_attempts'):
        assert after[field] == before[field]
    assert built['snapshot']['presentation']['conclusion_status'] == 'failed'
    assert jobs == job_service.load_jobs(limit=None)


def test_explicit_request_requires_settled_and_notify_permission(dispatch_env):
    from task_server.services import ui_batch_report_service as reports
    batch = create()
    with actor_context(ACTORS['a']):
        with pytest.raises(ValueError, match='execution_not_settled'):
            reports.request_ui_batch_report(None, batch['ui_batch_id'], {})
    batch, _, _ = completed(key='finished')
    with actor_context(ACTORS['a']):
        with pytest.raises(PermissionError):
            reports.request_ui_batch_report(None, batch['ui_batch_id'], {'notifications':{'enabled':True}})
        assert reports.get_ui_batch_report(None, batch['ui_batch_id'])['status'] == 'not_requested'
        assert reports.request_ui_batch_report(None, batch['ui_batch_id'], {})['status'] == 'pending'


def test_rerun_does_not_inherit_formal_report_request(dispatch_env, monkeypatch):
    from task_server.services import ui_batch_report_service as reports, ui_batch_dispatch as dispatch
    batch, _, _ = completed(status='failed')
    monkeypatch.setattr(reports, '_render_docx', lambda _: b'bounded renderer test')
    with actor_context(ACTORS['a']):
        reports.request_ui_batch_report(None, batch['ui_batch_id'], {})
        reports.build_ui_batch_report(None, batch['ui_batch_id'])
        rerun = dispatch.rerun_ui_batch(None, batch['ui_batch_id'], 'failed', 'again')
    assert rerun['report_status'] == 'not_requested'
    assert rerun['report_requested_ns'] is None and rerun['report_request'] == {}
    assert rerun['delivery_status'] == 'not_requested'


def test_v4_pending_is_not_auto_requested_and_saved_report_remains_readable(dispatch_env, monkeypatch):
    from task_server.services import ui_batch_report_service as reports
    from task_server.services.ui_execution_store import UiExecutionStore
    old, _, _ = completed()
    pending, _, _ = completed(key='old-pending')
    monkeypatch.setattr(reports, '_render_docx', lambda _: b'bounded renderer test')
    with actor_context(ACTORS['a']):
        reports.request_ui_batch_report(None, old['ui_batch_id'], {})
        saved = reports.build_ui_batch_report(None, old['ui_batch_id'])
    db = reports._connect()
    try:
        db.execute("DROP INDEX IF EXISTS ui_reports_pending")
        db.execute("ALTER TABLE batches DROP COLUMN report_requested_ns")
        db.execute("ALTER TABLE batches DROP COLUMN report_request_json")
        db.execute("UPDATE batches SET report_status='pending',delivery_status='pending' WHERE ui_batch_id=?", (pending['ui_batch_id'],))
        db.execute('PRAGMA user_version=4')
    finally:
        db.close()
    migrated = UiExecutionStore().get_batch(pending['ui_batch_id'])
    assert migrated['report_status'] == 'not_requested' and migrated['delivery_status'] == 'not_requested'
    assert migrated['report_requested_ns'] is None
    with actor_context(ACTORS['a']):
        historical = reports.get_ui_batch_report(None, old['ui_batch_id'], saved['report_revision'])
        assert historical == saved
        assert reports.download_ui_batch_report(None, old['ui_batch_id'], 'docx', saved['report_revision'])[0] == b'bounded renderer test'
    assert UiExecutionStore().get_batch(old['ui_batch_id'])['report_requested_ns'] is None
    monkeypatch.setattr(reports, '_WORKER', None)
    reports.tick_ui_report_worker()
    assert reports._WORKER is None


def test_notify_intent_is_frozen_at_manual_request_and_repeated_click_never_resends(notify_env):
    from task_server.services import ui_batch_report_service as reports, ui_batch_notification_service as notify
    from task_server.services.ui_execution_store import UiExecutionStore
    batch, _, _ = completed()
    assert batch['selection']['notifications']['enabled'] is False
    notify_env['feishu_webhook'] = 'https://example.invalid/changed-before-request'
    with actor_context(ACTORS['a']):
        assert notify.get_batch_deliveries(None,batch['ui_batch_id'])['policy']['enabled'] is False
        reports.request_ui_batch_report(None,batch['ui_batch_id'],{'notifications':{'enabled':True}})
        saved = UiExecutionStore().get_batch(batch['ui_batch_id'])
        policy = saved['report_request']['notifications']
        assert policy['enabled'] is True and policy['channel_id'] != batch['selection']['notifications']['channel_id']
        assert 'https:' not in json.dumps(policy)
        report = reports.build_ui_batch_report(None,batch['ui_batch_id'])
        assert notify.enqueue_ready_batch_deliveries() == 1
        item = notify._claim_due(1)[0]
        notify._finish(item,'sent','')
        reports.request_ui_batch_report(None,batch['ui_batch_id'],{'notifications':{'enabled':False}})
        assert UiExecutionStore().get_batch(batch['ui_batch_id'])['report_request'] == saved['report_request']
        assert notify.enqueue_ready_batch_deliveries() == 0
        deliveries = notify.get_batch_deliveries(None,batch['ui_batch_id'])
        assert len(deliveries['items']) == 1 and deliveries['items'][0]['status'] == 'sent'
        assert deliveries['items'][0]['report_revision'] == report['report_revision']


def legacy_v4_request(enabled):
    """Reproduce the exact pre-purpose request format and schema, not a new intent."""
    from test_ui_execution_service import request
    from task_server.services import ui_execution_service as service
    from task_server.services.ui_execution_store import UiExecutionStore
    body=request();body['notifications']['enabled']=enabled
    legacy_normalized={**body,'suite_id':None,'suite_revision':None}
    with actor_context(ACTORS['a']):
        batch=service.create_ui_batch(None,request(),'legacy-lost-response')
    selection=batch['selection'];selection.pop('release_report');selection['notifications']['enabled']=enabled
    db=UiExecutionStore()._connect()
    try:
        db.execute('UPDATE batches SET request_hash=?,selection_json=?,report_status=\'pending\',delivery_status=? WHERE ui_batch_id=?',
                   (service._request_hash(legacy_normalized),json.dumps(selection),'pending' if enabled else 'not_requested',batch['ui_batch_id']))
        db.execute('DROP INDEX ui_reports_pending')
        db.execute('ALTER TABLE batches DROP COLUMN report_requested_ns')
        db.execute('ALTER TABLE batches DROP COLUMN report_request_json')
        db.execute('PRAGMA user_version=4')
    finally:db.close()
    return body,UiExecutionStore().get_batch(batch['ui_batch_id'])


@pytest.mark.parametrize('enabled',[False,True])
def test_legacy_unknown_submission_reuses_exact_existing_batch_without_report_consent(dispatch_env, enabled):
    from task_server.services import ui_execution_service as service
    from task_server.services.ui_execution_store import UiExecutionStore
    body,before=legacy_v4_request(enabled)
    with actor_context(ACTORS['a']):
        again=service.create_ui_batch(None,body,'legacy-lost-response')
    assert again==before
    assert again['report_requested_ns'] is None and again['report_status']=='not_requested'
    assert again['delivery_status']=='not_requested'
    assert len(UiExecutionStore().list_batches('u-a')['items'])==1


@pytest.mark.parametrize('enabled',[False,True])
@pytest.mark.parametrize('change',['case','device','explicit_release'])
def test_legacy_existing_key_never_accepts_changed_execution_or_release_purpose(dispatch_env, enabled, change):
    from task_server.services import ui_execution_service as service
    from task_server.services.ui_execution_store import UiExecutionStore
    body,before=legacy_v4_request(enabled)
    if change=='case': body['cases']=[{**body['cases'][0],'revision':'f'*64}]
    elif change=='device': body['targets']=[{**body['targets'][0],'device_id':'other-phone'}]
    else: body['release_report']={'enabled':True}
    with actor_context(ACTORS['a']):
        with pytest.raises(ValueError):service.create_ui_batch(None,body,'legacy-lost-response')
    assert UiExecutionStore().get_batch(before['ui_batch_id'])==before


@pytest.mark.parametrize('revoked',['ui.view','ui.execute','app'])
def test_legacy_idempotent_receipt_requires_current_execution_and_read_scope(dispatch_env, monkeypatch, revoked):
    from task_server import identity
    from task_server.services import ui_execution_service as service
    body,before=legacy_v4_request(False)
    original=identity.get_access_profile
    def profile(name):
        value=original(name)
        return {**value,'scope':{'ui_apps':[]}} if revoked=='app' else {**value,'permissions':[p for p in value['permissions'] if p!=revoked]}
    monkeypatch.setattr(identity,'get_access_profile',profile)
    with actor_context(ACTORS['a']):
        with pytest.raises(PermissionError):service.create_ui_batch(None,body,'legacy-lost-response')
