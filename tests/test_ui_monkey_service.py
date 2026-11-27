import json
import time

import pytest
from test_identity import activate, create_member, identity_db
from test_operation_http import operation_server, request as http
from test_ui_execution_store import prepared

from task_server import auth
from task_server.services import ui_monkey_analysis as analysis, ui_monkey_service as m
from task_server.services.operation_attribution import actor_context

APP = 'com.test.app'
TARGET = {'platform': 'android', 'runner_id': 'mac', 'device_id': 'phone'}
BODY = {'app_id': APP, 'target': TARGET, 'mode': 'browse', 'request_id': 'retry', 'event_count': 4, 'seed': 5}


@pytest.fixture
def isolated(tmp_path, monkeypatch, identity_db):
    from task_server.services import business_line_service
    _, store = identity_db
    for username, apps in [('member', [APP]), ('second', [APP]), ('other', ['app.other'])]:
        create_member(store, username=username, scope={'ui_apps': apps})
        activate(store, username)
    monkeypatch.setenv('TASK_UI_EXECUTION_DB', str(tmp_path/'private'/'ui.sqlite'))
    monkeypatch.setenv('UI_SIMPLE_FLOW_ENABLED', '1')
    monkeypatch.setattr(m.job_service, '_read_jobs_raw', lambda **kw: [])
    monkeypatch.setattr(m.job_service, 'load_ui_dispatch_jobs', lambda *a: ({}, {}))
    monkeypatch.setattr(business_line_service, 'configured_test_application', lambda app: {'enabled': True} if app == APP else None)
    runner = {'online': True, 'capabilities': {'ui_monkey_v1': True, 'ui_execution_contract': 'ui-v1',
        'job_execution': True, 'ui_platforms': ['android'], 'ui_actions': ['launch', 'aiTap', 'aiWaitFor', 'aiAssert']},
        'devices': [{**TARGET, 'status': 'online', 'preflight_status': 'ready',
                     'installed_apps': [{'package': APP, 'installed': True, 'version_code': 2}]}]}
    monkeypatch.setattr(m.runner_service, 'list_runners', lambda: {'mac': runner})
    monkeypatch.setattr(m, '_read_occupancy', lambda fresh: {'__source__': {'available': True}, 'phone': {'usage_status': 'idle'}})
    profile = store.get_access_profile('member')
    with actor_context({'kind': 'user', **{k: profile[k] for k in ('username', 'user_id', 'display_name')}}):
        yield runner


def test_default_mode_is_mixed_and_explicit_browse_is_preserved():
    request = {k: v for k, v in BODY.items() if k != 'mode'}
    assert m.normalize_request(request)['mode'] == 'mixed'
    assert m.normalize_request(BODY)['mode'] == 'browse'


def claim_run():
    run = m.create_run(BODY)
    job = m.claim('mac', {'phone'})
    assert job['run_id'] == run['run_id']
    return run['run_id'], {k: job[k] for k in ('runner_id', 'device_id', 'monkey_claim_token')}


def test_request_replay_and_claim_rechecks_target(isolated):
    run = m.create_run(BODY)
    assert m.create_run(BODY) == run
    with pytest.raises(m.MonkeyError, match='idempotency_conflict'):
        m.create_run(dict(BODY, seed=1))
    with pytest.raises(m.MonkeyError, match='device_busy'):
        m.create_run(dict(BODY, request_id='other'))
    assert m.claim('other', {'phone'}) is None
    assert m.claim('mac', {'other'}) is None
    isolated['devices'][0]['installed_apps'][0]['version_code'] = 3
    assert m.claim('mac', {'phone'}) is None
    assert m.get_run(run['run_id'])['status'] == 'inconclusive'
    isolated['capabilities']['ui_monkey_v1'] = False
    with pytest.raises(m.MonkeyError, match='runner_upgrade_required'):
        m.create_run(dict(BODY, request_id='other'))


def test_lost_claim_before_start_releases_only_unstarted_driver(isolated):
    run, credentials = claim_run()
    with m.UiExecutionStore()._transaction() as db:
        db.execute('UPDATE ui_monkey_runs SET updated_ns=?', (time.time_ns() - 123 * 10**9,))
    assert m.claim('mac', {'phone'}) is None
    assert not m.UiExecutionStore().device_reserved('phone')
    late = m.callback(run, 'progress', dict(credentials, executed=0))
    assert late['stop_requested'] and late['status'] == 'inconclusive'


def test_sqlite_device_lease_both_directions(isolated):
    store = m.UiExecutionStore()
    run = m.create_run(BODY)
    data = prepared()
    data['attempts'][0]['target'].update(TARGET)
    attempt = store.create_batch(data)['case_attempts'][0]['attempt_id']
    assert not store.reserve_attempt(attempt)
    assert store.device_reserved('phone')
    assert m.stop(run['run_id'])['status'] == 'stopped'
    assert store.reserve_attempt(attempt)
    with pytest.raises(m.MonkeyError, match='device_busy'):
        m.create_run(dict(BODY, request_id='another'))


def test_stop_needs_exit_proof_and_handles_lost_connection(isolated):
    run, credentials = claim_run()
    with pytest.raises(m.MonkeyError, match='claim_mismatch'):
        m.callback(run, 'progress', dict(credentials, device_id='other', executed=1))
    assert m.callback(run, 'progress', dict(credentials, executed=2, log='secret=test-value')) == {'status':'running','stop_requested':False}
    assert m.stop(run)['status'] == 'stopping'
    assert m.callback(run, 'progress', dict(credentials, executed=0))['stop_requested']
    with pytest.raises(m.MonkeyError, match='result_invalid'):
        m.callback(run, 'result', dict(credentials, executed=2, status='stopped'))
    with m.UiExecutionStore()._transaction() as db:
        db.execute('UPDATE ui_monkey_runs SET updated_ns=?', (time.time_ns()-123*10**9,))
    assert m.get_run(run)['connection_lost'] and m.UiExecutionStore().device_reserved('phone')
    stopped = dict(credentials, executed=2, status='stopped', process_stopped=True)
    m.callback(run, 'result', stopped)
    assert m.callback(run, 'result', stopped)['ignored'] == 'terminal_job'
    assert not m.UiExecutionStore().device_reserved('phone')
    assert 'test-value' not in m.get_run(run)['log']
    with pytest.raises(m.MonkeyError, match='crash_evidence_missing'):
        m.analyze(run)


def test_only_incident_projection_reaches_model_and_facts_are_server_owned(isolated, monkeypatch):
    run, credentials = claim_run()
    m.callback(run, 'result', dict(credentials, status='crashed', executed=1, process_stopped=True,
        log='#1 swipe', crash_log='kind=am_crash\npid=123\nexception_type=java.lang.IllegalStateException\n'
        'frame=com.test.Foo.start(Foo.java:4)\ncookie=private\nmessage=private\nframe=/Users/private/app'))
    calls = []
    def model(name, content, system_prompt):
        calls.append(json.loads(content))
        return {'facts':['untrusted'], 'hypotheses':['有待定位。'], 'next_steps':['核对首个代码帧。']}, {'model':'qwen-test','usage':{}}, None
    monkeypatch.setattr(analysis.qwen, '_call', model)
    result = m.analyze(run)['analysis']
    assert result['model'] == 'qwen-test' and 'untrusted' not in result['facts']
    assert calls[0]['diagnostics'] == ['kind=am_crash','pid=123','exception_type=java.lang.IllegalStateException','frame=com.test.Foo.start(Foo.java:4)']
    results = m.list_runs(APP)
    assert 'private' not in json.dumps(calls) and len(results) == 1
    assert results[0]['analysis'] == result == m.analyze(run)['analysis'] and len(calls) == 1


def test_real_routes_and_scope(operation_server, isolated, monkeypatch):
    from task_server import router
    monkeypatch.setattr(auth, 'TOKEN', 'runner-test-key')
    monkeypatch.setattr(router, 'assign_job', lambda *a: None)
    monkeypatch.setattr(router, 'recover_timed_out_jobs', lambda: None)
    server, _ = operation_server
    uri = '/api/ui-monkey/runs'
    member, second, other = (auth.create_session_token(x) for x in ('member','second','other'))
    def request(method, path, token=None, body=None, extra=None):
        status, raw = http(server, method, path, token, body, extra)
        return status, json.loads(raw)
    assert request('GET', uri+'?app_id='+APP, other)[0] == 403
    assert request('POST', uri, other, BODY)[0] == 403
    status, result = request('POST', uri, member, BODY)
    assert status == 200, result
    rid = result['run']['run_id']
    assert request('GET', uri+'/'+rid, second)[0] == 404
    assert request('POST', uri+'/'+rid+'/stop', second)[0] == 404
    assert request('GET', uri+'?app_id='+APP, second)[1]['runs'] == []
    assert request('GET', uri+'?app_id='+APP, member)[1]['runs'][0]['run_id'] == rid
    key = {'x-token':'runner-test-key'}
    status, delivery = request('GET','/api/runner/jobs/next?runner_id=mac&devices=phone',extra=key)
    assert status == 200 and delivery['job']['job_id'] == rid
    credentials = {k:delivery['job'][k] for k in ('runner_id','device_id','monkey_claim_token')}
    cb = '/api/runner/jobs/'+rid
    assert request('POST', cb+'/progress', member, dict(credentials,executed=1))[0] in (401,403)
    assert request('POST', cb+'/progress',body=dict(credentials,executed=1),extra=key)[0] == 200
    assert request('POST', uri+'/'+rid+'/stop',member)[1]['run']['status'] == 'stopping'
    assert request('POST', cb+'/progress',body=dict(credentials,executed=1),extra=key)[1]['stop_requested']
    assert request('POST', cb+'/result',body=dict(credentials,executed=1,status='stopped',process_stopped=True),extra=key)[0] == 200
    assert request('POST', uri+'/'+rid+'/analysis', member)[0] == 409
    result = request('GET', uri+'/'+rid, member)[1]['run']
    assert result['status'] == 'stopped' and credentials['monkey_claim_token'] not in json.dumps(result)


def test_duration_budget_is_independent_of_event_limit(isolated):
    body = dict(BODY, duration_seconds=60)
    with pytest.raises(m.MonkeyError):
        m.create_run(body)
    del body['event_count']
    for seconds in (0, 59, 86401, True):
        with pytest.raises(m.MonkeyError):
            m.create_run(dict(body, duration_seconds=seconds))
    run = m.create_run(body)
    job = m.claim('mac', {'phone'})
    assert job['event_count'] is None and job['duration_seconds'] == 60
    cred = {k: job[k] for k in ('runner_id','device_id','monkey_claim_token')}
    with pytest.raises(m.MonkeyError):
        m.callback(run['run_id'], 'progress', dict(cred, executed=1))
    m.callback(run['run_id'], 'progress', dict(cred, executed=10001, elapsed_seconds=58))
    with pytest.raises(m.MonkeyError):
        m.callback(run['run_id'], 'result', dict(cred, executed=10002, elapsed_seconds=59, status='passed', process_stopped=True))
    m.callback(run['run_id'], 'result', dict(cred, executed=10002, elapsed_seconds=60, status='passed', process_stopped=True))
    final = m.get_run(run['run_id'])
    assert final['executed'] == 10002 and final['elapsed_seconds'] == 60 and final['status'] == 'passed'


def test_history_model_receives_only_os_incident(monkeypatch):
    captured = []
    def model(name, content, system_prompt):
        captured.append(json.loads(content))
        return {'hypotheses':['尚待核对。'],'next_steps':['核对线程。']}, {'model': 'test'}, None
    monkeypatch.setattr(analysis.qwen, '_call', model)
    history = analysis.analyze_evidence({'platform':'ios', 'executed':None, 'crash_log':'kind=ios_crash\nsignal=SIGABRT'})
    assert captured == [{'platform':'ios','diagnostics':['kind=ios_crash','signal=SIGABRT']}]
    assert history['facts'] == captured[0]['diagnostics']


def test_daily_freezes_once_and_records_busy_or_offline(isolated, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from datetime import datetime
    from task_server.services import ui_monkey_schedule as s
    config = dict(BODY, daily_time='18:59', enabled=False)
    del config['request_id']
    disabled = s.save_schedule(config)
    assert disabled['next_run_at'] is None
    s.tick()
    assert m.list_runs(APP) == []
    daily = s.save_schedule(dict(config, enabled=True))
    assert daily['schedule_id'] == disabled['schedule_id']
    due = datetime.fromisoformat(daily['next_run_at'])
    assert due.strftime('%H:%M %z') == '18:59 +0800'
    s.tick(due.timestamp()-1)
    assert m.list_runs(APP) == []
    # Crash/restart after the due config was frozen but before creating a run.
    s._freeze_due(m.UiExecutionStore(), due.timestamp())
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(s.tick, [due.timestamp()]*4))
    runs = m.list_runs(APP)
    assert len(runs) == 1 and runs[0]['status'] == 'queued'
    saved = s.list_schedules(APP)[0]
    assert saved['last_run_id'] == runs[0]['run_id'] and saved['last_status'] == 'queued'
    m.stop(runs[0]['run_id'])
    isolated['online'] = False
    s.tick(due.timestamp() + 86400)
    assert '离线' in s.list_schedules(APP)[0]['last_message']
    isolated['online'] = True
    monkeypatch.setattr(m, '_read_occupancy', lambda fresh: {'__source__':{'available':True},'phone':{'usage_status':'busy'}})
    s.tick(due.timestamp() + 2*86400)
    saved = s.list_schedules(APP)[0]
    assert saved['last_status'] == 'inconclusive' and '空闲' in saved['last_message']
    s.save_schedule(config)
    s.tick(due.timestamp() + 3*86400)
    assert len(m.list_runs(APP)) == 3


def test_daily_http_permissions_and_disable(operation_server, isolated):
    server, _ = operation_server
    url = '/api/ui-monkey/schedules'
    member, second, other = (auth.create_session_token(u) for u in ('member','second','other'))
    body = dict(BODY, daily_time='08:45', enabled=True, duration_seconds=86400)
    del body['request_id'], body['event_count']
    assert http(server, 'POST', url, other, body)[0] == 403
    status, raw = http(server, 'POST', url, member, body)
    assert status == 200, raw
    scheme = json.loads(raw)['schedule']
    assert scheme['daily_time'] == '08:45' and scheme['duration_seconds'] == 86400
    assert http(server, 'POST', url, second, body)[0] == 409
    assert json.loads(http(server,'GET',url+'?app_id='+APP,second)[1])['schedules'] == []
    assert http(server, 'GET', url+'?app_id='+APP, other)[0] == 403
    isolated['online'] = False
    disabled = json.loads(http(server,'POST',url,member,dict(body,enabled=False))[1])['schedule']
    assert disabled['schedule_id'] == scheme['schedule_id'] and disabled['next_run_at'] is None
