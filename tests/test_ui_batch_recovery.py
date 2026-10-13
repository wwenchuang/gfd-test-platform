"""Cancellation, interrupted leases and authenticated sequenced events."""
import pytest
from test_ui_batch_dispatch import dispatch_env, environment, create
from test_ui_execution_service import ACTORS
from task_server.services.operation_attribution import actor_context


def claimed():
    from task_server.services import ui_batch_dispatch as dispatch, job_service
    batch = create()
    dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    job = job_service.get_job(batch['case_attempts'][0]['dispatch_key'])
    payload = dispatch.claim_ui_job(job)
    return batch, job, payload


def event(payload, **changes):
    return {'runner_id':'mac', 'device_id':'iphone', 'ui_claim_token':payload['ui_claim_token'], **changes}


def test_cancel_retains_lease_until_authenticated_stop_and_late_progress_cannot_revive(dispatch_env):
    from task_server.services import ui_batch_dispatch as dispatch, job_service
    batch, job, payload = claimed()
    with actor_context(ACTORS['a']): dispatch.cancel_ui_batch(ACTORS['a'], batch['ui_batch_id'])
    b = create('b')
    dispatch.dispatch_ui_batch(b['ui_batch_id'])
    assert job_service.get_job(b['case_attempts'][0]['dispatch_key']) is None
    result = dispatch.record_ui_attempt_event(job['job_id'], 1, event(payload, type='progress'))
    assert result['ignored'] == 'terminal_job'
    dispatch.record_ui_attempt_event(job['job_id'], 2, event(payload, type='result', status='success', execution_stopped=True))
    saved = dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    assert saved['status'] == 'cancelled'
    dispatch.dispatch_ui_batch(b['ui_batch_id'])
    assert job_service.get_job(b['case_attempts'][0]['dispatch_key'])


def test_unclaimed_cancel_releases_and_cannot_create_or_claim_job(dispatch_env):
    from task_server.services import ui_batch_dispatch as dispatch, job_service
    batch = create()
    dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    job = job_service.get_job(batch['case_attempts'][0]['dispatch_key'])
    with actor_context(ACTORS['a']): dispatch.cancel_ui_batch(ACTORS['a'], batch['ui_batch_id'])
    assert dispatch.claim_ui_job(job) is None
    b = create('b')
    dispatch.dispatch_ui_batch(b['ui_batch_id'])
    assert job_service.get_job(b['case_attempts'][0]['dispatch_key'])


def test_bad_claim_token_or_target_cannot_change_state(dispatch_env):
    from task_server.services import ui_batch_dispatch as dispatch
    batch, job, payload = claimed()
    for bad in ({'ui_claim_token':'forged'}, {'runner_id':'other'}, {'device_id':'other'}):
        with pytest.raises(PermissionError):
            dispatch.record_ui_attempt_event(job['job_id'], 1, event(payload, type='result', status='success', execution_stopped=True, **bad))
    assert dispatch.dispatch_ui_batch(batch['ui_batch_id'])['status'] == 'running'


def test_sequence_dedup_and_no_green_from_log_or_percentage(dispatch_env):
    from task_server.services import ui_batch_dispatch as dispatch
    batch, job, payload = claimed()
    dispatch.record_ui_attempt_event(job['job_id'], 2, event(payload, type='progress', progress=99, stdout='all passed'))
    assert dispatch.record_ui_attempt_event(job['job_id'], 1, event(payload, type='progress'))['ignored'] == 'stale_event'
    saved = dispatch.dispatch_ui_batch(batch['ui_batch_id'])['case_attempts'][0]
    assert saved['step_progress_known'] is False
    assert all(s['status'] == 'pending' for s in saved['steps'])


def test_steps_fold_source_flows_and_terminal_does_not_fake_success(dispatch_env):
    from task_server.services import ui_batch_dispatch as dispatch
    batch, job, payload = claimed()
    snap = {'version':1, 'tasks':[{'status':'running','current_step':1,'total_steps':3,'steps':[{'label':'launch'},{'label':'tap'},{'label':'assert'}]}]}
    dispatch.record_ui_attempt_event(job['job_id'], 1, event(payload, type='progress', execution_progress=snap))
    saved = dispatch.dispatch_ui_batch(batch['ui_batch_id'])['case_attempts'][0]
    assert [s['status'] for s in saved['steps']] == ['passed','running','pending']
    assert len({s['step_id'] for s in saved['steps']}) == 3
    dispatch.record_ui_attempt_event(job['job_id'], 2, event(payload, type='result', status='success', execution_stopped=True))
    saved = dispatch.dispatch_ui_batch(batch['ui_batch_id'])['case_attempts'][0]
    assert saved['status'] == 'passed'
    assert saved['steps'][2]['status'] == 'pending'


def test_disconnect_restart_and_elapsed_time_never_release_claim(dispatch_env):
    from task_server.services import ui_batch_dispatch as dispatch, job_service
    from task_server.services.ui_execution_store import UiExecutionStore
    batch, job, payload = claimed()
    dispatch_env[0]['mac']['online'] = False
    dispatch.reconcile_ui_batches()
    saved = UiExecutionStore().get_batch(batch['ui_batch_id'])
    assert saved['case_attempts'][0]['status'] == 'interrupted'
    dispatch_env[0]['mac']['online'] = True
    b = create('b')
    dispatch.dispatch_ui_batch(b['ui_batch_id'])
    assert job_service.get_job(b['case_attempts'][0]['dispatch_key']) is None
    assert dispatch.claim_ui_job(job) is None


def test_rerun_copies_frozen_case_to_new_owner_batch_and_preserves_old_attempt(dispatch_env):
    from task_server.services import ui_batch_dispatch as dispatch
    batch, job, payload = claimed()
    dispatch.record_ui_attempt_event(job['job_id'], 1, event(payload, type='result', status='failed', execution_stopped=True))
    with actor_context(ACTORS['a']):
        rerun = dispatch.rerun_ui_batch(ACTORS['a'], batch['ui_batch_id'], 'failed', 'retry-1')
    assert rerun['ui_batch_id'] != batch['ui_batch_id']
    assert rerun['case_attempts'][0]['compiled_case'] == batch['case_attempts'][0]['compiled_case']
    assert dispatch.dispatch_ui_batch(batch['ui_batch_id'])['status'] == 'failed'


def test_http_result_authenticates_claim_and_preserves_cancel_state(dispatch_env, monkeypatch):
    from task_server import router
    from task_server.services import ui_batch_dispatch as dispatch
    batch, job, payload = claimed()
    monkeypatch.setattr(router, '_require_user_auth', lambda _: False)
    class Handler:
        def __init__(self, body): self.body = body
        def _body(self): return self.body
        def _json(self, payload, status=200): self.response = payload,status
    with actor_context(ACTORS['a']): dispatch.cancel_ui_batch(ACTORS['a'], batch['ui_batch_id'])
    body = event(payload, status='success', execution_stopped=True, ui_event_sequence=1)
    handler = Handler(body)
    router._handle_runner_job_result(handler, job['job_id'])
    assert handler.response[0]['ignored'] == 'terminal_job'
    assert dispatch.dispatch_ui_batch(batch['ui_batch_id'])['case_attempts'][0]['stop_confirmed'] == 1
    handler = Handler({**body,'ui_claim_token':'forged'})
    router._handle_runner_job_result(handler, job['job_id'])
    assert handler.response[1] == 403


def test_generic_timeout_cannot_recycle_claimed_ui_job(dispatch_env, monkeypatch):
    from task_server.services import job_service
    batch, job, payload = claimed()
    job_service.update_job(job['job_id'], {'status':'running','started_at':'2000-01-01 00:00:00'})
    job_service.recover_timed_out_jobs()
    assert job_service.get_job(job['job_id'])['status'] == 'running'


def test_terminal_result_is_immutable_but_report_ready_updates_evidence(dispatch_env):
    from task_server.services import ui_batch_dispatch as dispatch
    batch, job, payload = claimed()
    dispatch.record_ui_attempt_event(job['job_id'],1,event(payload,type='result',status='failed',execution_stopped=True,stderr='original'))
    dispatch.record_ui_attempt_event(job['job_id'],2,event(payload,type='result',status='success',execution_stopped=True))
    from task_server.services.ui_batch_report_service import save_ui_artifact
    artifact = save_ui_artifact(job['job_id'],event(payload),b'private report','html')
    dispatch.record_ui_attempt_event(job['job_id'],3,event(payload,type='report_ready',report_url='/report/final.html'))
    attempt = dispatch.dispatch_ui_batch(batch['ui_batch_id'])['case_attempts'][0]
    assert attempt['status'] == 'failed'
    assert attempt['result']['stderr'] == 'original'
    assert attempt['result']['report_url'] == artifact['url']


def test_ui_action_routes_enforce_owner_and_replay_idempotency(dispatch_env):
    import re
    from task_server import router, identity
    from task_server.access_control import MainAccess
    batch = create()
    path = '/api/ui-executions/' + batch['ui_batch_id']
    policy = MainAccess(identity.get_access_profile('a'),catalog=[])
    for suffix in ('cancel','rerun'):
        policy.check('POST',path+'/'+suffix,{}, {})
    policy.check('POST','/api/ui-executions/preflight',{}, {})
    class Handler:
        headers = {'Idempotency-Key':'rerun-1'}
        def _body(self): return {'scope':'all'}
        def _json(self, payload, status=200): self.response = payload,status
    handler = Handler()
    route = next(fn for regex,fn in router._POST_REGEX_ROUTES if regex.pattern == r'^/api/ui-executions/([^/]+)/(cancel|rerun)$')
    with actor_context(ACTORS['b']):
        route(handler,{},re.match(r'^/api/ui-executions/([^/]+)/(cancel|rerun)$',path+'/cancel'))
        assert handler.response[1] == 404
    with actor_context(ACTORS['a']):
        route(handler,{},re.match(r'^/api/ui-executions/([^/]+)/(cancel|rerun)$',path+'/cancel'))
        assert handler.response[0]['batch']['status'] == 'cancelled'
        route(handler,{},re.match(r'^/api/ui-executions/([^/]+)/(cancel|rerun)$',path+'/rerun'))
        first = handler.response[0]['batch']['ui_batch_id']
        route(handler,{},re.match(r'^/api/ui-executions/([^/]+)/(cancel|rerun)$',path+'/rerun'))
        assert handler.response[0]['batch']['ui_batch_id'] == first


def test_cancelled_attempt_retains_final_failure_evidence(dispatch_env):
    from task_server.services import ui_batch_dispatch as dispatch
    batch, job, payload = claimed()
    with actor_context(ACTORS['a']): dispatch.cancel_ui_batch(ACTORS['a'],batch['ui_batch_id'])
    dispatch.record_ui_attempt_event(job['job_id'],1,event(payload,type='result',status='cancelled',execution_stopped=True,stderr='Runner process stopped'))
    saved = dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    assert saved['case_attempts'][0]['result']['stderr'] == 'Runner process stopped'
    assert saved['counts'] == {'cancelled':1}
    assert saved['started_ns'] and saved['finished_ns']


def test_recovery_pages_rotate_past_permanently_blocked_batches(dispatch_env):
    from task_server.services.ui_execution_store import UiExecutionStore
    a,b = create(),create('b')
    store = UiExecutionStore()
    assert store.recoverable_batch_ids(1) == [a['ui_batch_id']]
    store.mark_reconciled(a['ui_batch_id'])
    assert store.recoverable_batch_ids(1) == [b['ui_batch_id']]


def test_claimed_cancel_is_not_finished_until_stop_acknowledged(dispatch_env):
    from task_server.services import ui_batch_dispatch as dispatch
    batch, job, payload = claimed()
    with actor_context(ACTORS['a']): pending = dispatch.cancel_ui_batch(ACTORS['a'],batch['ui_batch_id'])
    assert pending['status'] == 'cancelling'
    assert pending['finished_ns'] is None
    assert pending['execution_settled'] is False
    assert pending['case_attempts'][0]['stop_pending'] is True
    dispatch.record_ui_attempt_event(job['job_id'],1,event(payload,type='result',status='success',execution_stopped=True))
    settled = dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    assert settled['status'] == 'cancelled' and settled['execution_settled'] is True
    assert settled['case_attempts'][0]['stop_pending'] is False


def test_late_success_confirms_stop_without_turning_interrupted_into_pass(dispatch_env):
    from task_server.services import ui_batch_dispatch as dispatch
    batch, job, payload = claimed()
    dispatch_env[0]['mac']['online'] = False
    dispatch.reconcile_ui_batches()
    pending = dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    assert pending['status'] == 'interrupted' and not pending['execution_settled']
    dispatch.record_ui_attempt_event(job['job_id'],1,event(payload,type='result',status='success',execution_stopped=True))
    settled = dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    assert settled['status'] == 'interrupted' and settled['execution_settled']
    assert settled['finished_ns']


def test_restart_replays_job_terminal_write_after_sqlite_commit(dispatch_env, monkeypatch):
    from task_server.services import ui_batch_dispatch as dispatch, job_service, runner_service
    batch = create()
    dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    job = runner_service.assign_job('mac',['iphone'])
    payload = job['_ui_delivery']
    original = job_service.update_job
    monkeypatch.setattr(job_service,'update_job',lambda *a,**k:(_ for _ in ()).throw(RuntimeError('crash after SQLite commit')))
    with pytest.raises(RuntimeError):
        dispatch.record_ui_attempt_event(job['job_id'],1,event(payload,type='result',status='success',execution_stopped=True))
    assert job_service.get_job(job['job_id'])['status'] == 'running'
    monkeypatch.setattr(job_service,'update_job',original)
    dispatch.reconcile_ui_batches()
    assert job_service.get_job(job['job_id'])['status'] == 'success'
    b = create('b')
    dispatch.dispatch_ui_batch(b['ui_batch_id'])
    assert job_service.get_job(b['case_attempts'][0]['dispatch_key'])


def test_unconfirmed_result_has_no_attempt_finish_time(dispatch_env):
    from task_server.services import ui_batch_dispatch as dispatch
    batch, job, payload = claimed()
    dispatch.record_ui_attempt_event(job['job_id'],1,event(payload,type='result',status='failed',execution_stopped=False))
    result = dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    attempt = result['case_attempts'][0]
    assert attempt['status'] == 'interrupted' and attempt['stop_pending']
    assert attempt['finished_ns'] is None
    assert not attempt['execution_settled']
    dispatch.record_ui_attempt_event(job['job_id'],2,event(payload,type='result',status='failed',execution_stopped=True))
    assert dispatch.dispatch_ui_batch(batch['ui_batch_id'])['case_attempts'][0]['finished_ns']
