import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from task_server.services import week2_real as service

REAL_INVOKE_WORKER = service._invoke_worker
REAL_AUTHORIZED_ACTOR = service.authorized_actor


@pytest.fixture(autouse=True)
def authorized(monkeypatch):
    monkeypatch.setattr(service.identity, 'get_access_profile', lambda _: {'user_id': 'alice-id', 'scope': {}})
    monkeypatch.setattr(service, 'authorized_actor', lambda *_: True)
    monkeypatch.setattr(service, '_scope_authorized', lambda *_: None)
    monkeypatch.setattr(service, '_invoke_worker', lambda *args, **kwargs: service._derive(*args))
    monkeypatch.setattr(service, '_invoke_authorization', lambda owner, username, sources, timeout, **kwargs:
                        service._verify_sources(owner, username, sources))


@pytest.fixture
def payload():
    # Synthetic test data, never used as a live source.
    return {'execution': {'id': service.EXECUTION_ID, 'state': 'DONE',
                          'case_results': [{'execution_case_id': service.CASE_ID}]},
            'case_result': {'execution_case_id': service.CASE_ID, 'status': 'PASSED',
                            'sanitized_result': {'status': 'PASSED',
        'request': {'method': 'GET', 'url': 'https://print.wisebeginner3d.com/app/print3d/api/v1/feedback/category/first'},
        'response': {'status_code': 200, 'body': json.dumps({'code': 0, 'data': [
            {'sn': 'secret-example', 'name': 'secret-name', 'children': [], 'order_related': False}]})},
        'assertions': [{'type': 'status_code', 'passed': True},
                       {'type': 'json_path', 'passed': True}, {'type': 'schema', 'passed': True}]}}}


def test_saved_result_uses_only_allowlisted_summary_and_owner(tmp_path, payload):
    db = tmp_path / 'week2.sqlite'
    with patch.object(service, '_fetch_authorized', return_value=payload):
        result = service.run('alice-id', 'alice', db)
    assert result['status'] == 'passed' and result['total'] == 17
    assert result['source_verification'] == 'server_authorized_platform_read'
    assert result['business_requests'] == 0
    assert service.list_results('alice-id', db)[0] == result
    assert service.list_results('bob-id', db) == []
    assert 'secret-' not in json.dumps(result, ensure_ascii=False)


def test_custom_ids_are_uuid_normalized_and_forwarded_without_url(tmp_path, payload):
    execution = '11111111-1111-4111-8111-111111111111'
    case = '22222222-2222-4222-8222-222222222222'
    payload['execution']['id'] = execution
    payload['execution']['case_results'][0]['execution_case_id'] = case
    payload['case_result']['execution_case_id'] = case
    with patch.object(service, '_fetch_authorized', return_value=payload) as fetch:
        result = service.run('alice-id', 'alice', tmp_path / 'custom.sqlite', execution_id=execution, case_id=case)
    assert result['status'] == 'passed' and result['execution_id'] == execution
    fetch.assert_called_once_with('alice', execution, case)
    with pytest.raises(ValueError):
        service.run('alice-id', 'alice', tmp_path / 'invalid.sqlite', execution_id='../bad', case_id=case)


def test_other_service_same_path_is_blocked(tmp_path, payload):
    payload['case_result']['sanitized_result']['request']['url'] = 'https://example.invalid/app/print3d/api/v1/feedback/category/first'
    with patch.object(service, '_fetch_authorized', return_value=payload):
        result = service.run('alice-id', 'alice', tmp_path / 'other.sqlite')
    assert result['status'] == 'blocked'


def test_source_without_three_assertion_kinds_is_blocked(tmp_path, payload):
    payload['case_result']['sanitized_result']['assertions'].pop()
    with patch.object(service, '_fetch_authorized', return_value=payload):
        result = service.run('alice-id', 'alice', tmp_path / 'missing-assertions.sqlite')
    assert result['status'] == 'blocked'


def test_fewer_than_twelve_processing_checks_is_blocked(tmp_path, payload, monkeypatch):
    fixture_dir = tmp_path / 'code'
    fixture_dir.mkdir()
    (fixture_dir / 'evidence.py').write_bytes((service._FIXTURES / 'evidence.py').read_bytes())
    (fixture_dir / 'checks.py').write_text("def run_checks(_): return [{'id':'one','status':'passed'}]\n")
    monkeypatch.setattr(service, '_FIXTURES', fixture_dir)
    with patch.object(service, '_fetch_authorized', return_value=payload):
        result = service.run('alice-id', 'alice', tmp_path / 'few.sqlite')
    assert result['status'] == 'blocked' and result['total'] == 1


def test_errors_and_timeout_are_recorded_without_secrets(tmp_path):
    db = tmp_path / 'fail.sqlite'
    with patch.object(service, '_fetch_authorized', side_effect=RuntimeError('secret-token /path')):
        result = service.run('alice-id', 'alice', db)
    assert result['status'] == 'blocked' and 'secret-token' not in json.dumps(result)
    with patch.object(service, '_invoke_worker', side_effect=subprocess.TimeoutExpired('worker', .01)):
        result = service.run('alice-id', 'alice', db)
        assert result['status'] == 'blocked'
    assert len(service.list_results('alice-id', db)) == 2


def test_timed_out_isolated_worker_is_reaped_and_does_not_block_next(tmp_path):
    # Exercise an actual process, including operating-system termination and reap.
    marker = 'week2-process-secret'
    pid_file = tmp_path / 'pid'
    slow = [sys.executable, '-c',
            'import os,sys,time; open(sys.argv[1], "w").write(str(os.getpid())); time.sleep(60)', str(pid_file)]
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        service._run_isolated(slow, marker, .5)
    assert time.monotonic() - started < 5
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    fast = [sys.executable, '-c', 'import sys; print(sys.stdin.read())']
    assert service._run_isolated(fast, 'ready', 3).strip() == 'ready'


def test_real_worker_rejects_unknown_identity(tmp_path, monkeypatch):
    monkeypatch.setenv('TASK_AUTH_DB', str(tmp_path / 'identity.sqlite'))
    ok, data = REAL_INVOKE_WORKER('unknown-id', 'unknown-user', service.EXECUTION_ID, service.CASE_ID)
    assert not ok and data is None


def test_scope_revoked_after_worker_returns_hides_summary(tmp_path, payload):
    def perform_then_revoke(*args, **kwargs):
        result = service._derive(*args[:4])
        def deny(*_): raise PermissionError('scope revoked')
        service._scope_authorized = deny
        return result
    with patch.object(service, '_fetch_authorized', return_value=payload), \
         patch.object(service, '_scope_authorized', lambda *_: None), \
         patch.object(service, '_invoke_worker', perform_then_revoke):
        result = service.run('alice-id', 'alice', tmp_path / 'late-scope.sqlite')
    assert result['status'] == 'blocked'
    assert result['checks'] == [] and result['original_assertions'] == []


def test_final_authorization_timeout_is_fail_closed(tmp_path, payload):
    with patch.object(service, '_fetch_authorized', return_value=payload), \
         patch.object(service, '_invoke_authorization', side_effect=subprocess.TimeoutExpired('authorization', .01)):
        result = service.run('alice-id', 'alice', tmp_path / 'authorization-timeout.sqlite')
    assert result['status'] == 'blocked' and result['checks'] == []


def test_permission_revoked_after_worker_returns_hides_summary(tmp_path, payload):
    def revoke(*args, **kwargs):
        answer = service._derive(*args[:4])
        service.authorized_actor = lambda *_: False
        return answer
    with patch.object(service, '_fetch_authorized', return_value=payload), \
         patch.object(service, 'authorized_actor', lambda *_: True), \
         patch.object(service, '_invoke_worker', revoke):
        result = service.run('alice-id', 'alice', tmp_path / 'late-permission.sqlite')
    assert result['status'] == 'blocked' and result['checks'] == []


@pytest.mark.parametrize('revoked', ['platform.configure', 'ui_apps'])
def test_platform_permission_or_ui_scope_revoked_during_analysis_hides_result(tmp_path, payload, revoked):
    profile = {'user_id':'alice-id', 'status':'active', 'must_change_password':False,
               'is_superuser':False, 'permissions':['api.view','platform.configure'], 'scope':{'ui_apps':'*'}}
    def analyze_then_revoke(*args, **kwargs):
        result = service._derive(*args[:4])
        if revoked == 'ui_apps': profile['scope']['ui_apps'] = ['only-one-app']
        else: profile['permissions'].remove(revoked)
        return result
    with patch.object(service.identity, 'get_access_profile', lambda _: profile), \
         patch.object(service, 'authorized_actor', REAL_AUTHORIZED_ACTOR), \
         patch.object(service, '_fetch_authorized', return_value=payload), \
         patch.object(service, '_invoke_worker', analyze_then_revoke):
        result = service.run('alice-id', 'alice', tmp_path / ('revoke-' + revoked + '.sqlite'))
    assert result['status'] == 'blocked' and result['checks'] == []
    assert result['original_assertions'] == []


def test_final_authorization_keeps_lease_and_uses_remaining_time(tmp_path, payload):
    db = tmp_path / 'pending-authorization.sqlite'
    started, release = threading.Event(), threading.Event()
    seen = []
    def verify(owner, username, sources, timeout, **kwargs):
        seen.append(timeout)
        started.set()
        assert release.wait(timeout=3)
        return [True]
    with patch.object(service, '_fetch_authorized', return_value=payload), \
         patch.object(service, '_invoke_authorization', verify):
        worker = threading.Thread(target=service.run, args=('alice-id', 'alice', db))
        worker.start()
        try:
            assert started.wait(timeout=3)
            with pytest.raises(service.PracticeBusy): service._claim(db)
            assert 0 < seen[0] < service._TIMEOUT_SECONDS
        finally:
            release.set()
            worker.join(timeout=3)
    assert not worker.is_alive()


def test_history_authorization_timeout_hides_all_sources(tmp_path):
    db = tmp_path / 'history-timeout.sqlite'
    service._save(db, 'alice-id', {'id':'a'*32, 'status':'passed', 'created_at':'now',
        'execution_id':service.EXECUTION_ID, 'execution_case_id':service.CASE_ID,
        'checks':[{'secret':'should-not-leak'}]}, time.time())
    with patch.object(service, '_invoke_authorization', side_effect=subprocess.TimeoutExpired('authorization', .01)):
        result = service.list_results('alice-id', db, username='alice')
    assert result[0]['status'] == 'blocked' and 'checks' not in result[0] and 'execution_id' not in result[0]


def test_history_busy_hides_data_and_allows_retry_after_release(tmp_path, monkeypatch):
    db = tmp_path / 'history-busy.sqlite'
    service._save(db, 'alice-id', {'id':'a'*32, 'status':'passed', 'created_at':'now',
        'execution_id':service.EXECUTION_ID, 'execution_case_id':service.CASE_ID,
        'checks':[{'id':'one','status':'passed'}]}, time.time())
    monkeypatch.setattr(service, '_HISTORY_SLOTS', 1)
    with service._history_slot(db):
        result = service.list_results('alice-id', db, username='alice')
        assert result[0]['status'] == 'blocked' and '稍后' in result[0]['message']
        assert 'checks' not in result[0] and 'execution_id' not in result[0]
    assert service.list_results('alice-id', db, username='alice')[0]['status'] == 'passed'


def test_history_slot_survives_parent_close_while_child_holds_fd(tmp_path, monkeypatch):
    db = tmp_path / 'history-cross-process.sqlite'
    with service._db(db):
        pass
    monkeypatch.setattr(service, '_HISTORY_SLOTS', 1)
    child = None
    try:
        with service._history_slot(db) as slot:
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], pass_fds=(slot,))
        with pytest.raises(service.HistoryBusy):
            with service._history_slot(db): pass
    finally:
        if child is not None:
            child.terminate()
            child.wait(timeout=3)
    with service._history_slot(db):
        pass


def test_analysis_admission_stays_busy_after_parent_closes_inherited_lock(tmp_path):
    db = tmp_path / 'analysis-cross-process.sqlite'
    with service._db(db): pass
    child = None
    try:
        with service._analysis_slot(db) as slot:
            child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], pass_fds=(slot,))
        # No SQLite lease exists, just as after a lease expires; the live child still owns admission.
        with pytest.raises(service.PracticeBusy):
            service.run('alice-id', 'alice', db)
    finally:
        if child is not None:
            child.terminate()
            child.wait(timeout=3)
    with service._analysis_slot(db): pass


def test_unconfirmed_process_cleanup_keeps_lease(tmp_path):
    db = tmp_path / 'cleanup-unknown.sqlite'
    with patch.object(service, '_invoke_worker', side_effect=service.WorkerCleanupUncertain()):
        assert service.run('alice-id', 'alice', db)['status'] == 'blocked'
    with pytest.raises(service.PracticeBusy):
        service._claim(db)


def test_timeout_kills_descendant_holding_stdout_pipe(tmp_path):
    marker = tmp_path / 'descendant-survived'
    child = 'import sys,time; time.sleep(.8); open(sys.argv[1],"w").write("survived")'
    code = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",sys.argv[1],sys.argv[2]]); time.sleep(60)'
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        service._run_isolated([sys.executable, '-c', code, child, str(marker)], '', .3)
    assert time.monotonic() - started < 1.5
    time.sleep(.9)
    assert not marker.exists()


def test_busy_and_stale_results(tmp_path):
    db = tmp_path / 'busy.sqlite'
    token = service._claim(db)
    try:
        with pytest.raises(service.PracticeBusy): service.run('bob-id', 'bob', db)
    finally:
        service._release(db, token)
    result = {'id':'a'*32, 'status':'running'}
    service._save(db, 'alice-id', result, time.time() - service._LEASE_SECONDS - 1)
    assert service.list_results('alice-id', db)[0]['status'] == 'blocked'


def test_timeout_releases_lease_and_allows_next_analysis(tmp_path, payload):
    db = tmp_path / 'timeout.sqlite'
    with patch.object(service, '_invoke_worker', side_effect=subprocess.TimeoutExpired('worker', .01)):
        result = service.run('alice-id', 'alice', db)
        assert result['status'] == 'blocked'
    with patch.object(service, '_fetch_authorized', return_value=payload):
        assert service.run('alice-id', 'alice', db)['status'] == 'passed'
    assert service.list_results('alice-id', db)[-1]['status'] == 'blocked'


def test_failed_case_is_persisted_as_failed(tmp_path, payload):
    payload['case_result']['status'] = 'FAILED'
    payload['case_result']['sanitized_result']['status'] = 'FAILED'
    payload['case_result']['sanitized_result']['assertions'][0]['passed'] = False
    with patch.object(service, '_fetch_authorized', return_value=payload):
        result = service.run('alice-id', 'alice', tmp_path / 'bad.sqlite')
    assert result['status'] == 'failed'


def test_scope_revoked_prevents_result_and_hides_history(tmp_path, payload, monkeypatch):
    db = tmp_path / 'scope.sqlite'
    with patch.object(service, '_fetch_authorized', return_value=payload):
        assert service.run('alice-id', 'alice', db)['status'] == 'passed'
    def deny(*_):
        raise PermissionError('denied')
    monkeypatch.setattr(service, '_scope_authorized', deny)
    hidden = service.list_results('alice-id', db, username='alice')
    assert len(hidden) == 1 and hidden[0]['status'] == 'blocked'
    assert 'execution_id' not in hidden[0] and 'checks' not in hidden[0]
    with patch.object(service, '_fetch_authorized', return_value=payload):
        result = service.run('alice-id', 'alice', db)
    assert result['status'] == 'blocked' and result['checks'] == []


def test_running_and_blocked_entries_do_not_leak_after_scope_revoked(tmp_path, monkeypatch):
    db = tmp_path / 'revoked.sqlite'
    for index, status in enumerate(('running', 'blocked')):
        service._save(db, 'alice-id', {'id':str(index)*32, 'status':status,
            'execution_id':service.EXECUTION_ID, 'execution_case_id':service.CASE_ID,
            'created_at':'now'}, time.time() + index)
    def deny(*_): raise PermissionError('revoked')
    monkeypatch.setattr(service, '_scope_authorized', deny)
    history = service.list_results('alice-id', db, username='alice')
    assert len(history) == 2
    assert all('execution_id' not in item and item['status'] == 'blocked' for item in history)


def test_adapter_uses_same_actor_and_propagates_resource_denial(monkeypatch):
    from types import SimpleNamespace
    from task_server.api_testing import access, http
    from task_server.api_testing.config import ApiTestingSettings
    calls = []
    monkeypatch.setattr(ApiTestingSettings, 'from_env', classmethod(lambda cls: SimpleNamespace(enabled=True)))
    monkeypatch.setattr(access, 'authorize_http', lambda actor, method, path: calls.append(('permission', actor, path)))
    def get(path, query, actor, settings):
        calls.append(('read', actor, path))
        if len(path) == 4: raise PermissionError('scope denied')
        return {'execution': {}}
    monkeypatch.setattr(http, '_get', get)
    with pytest.raises(PermissionError):
        service._fetch_authorized('limited-actual-user', service.EXECUTION_ID, service.CASE_ID)
    assert len(calls) == 4
    assert all(call[1] == 'limited-actual-user' for call in calls)
    assert calls[-1][2] == ('executions', service.EXECUTION_ID, 'cases', service.CASE_ID)


def test_adapter_resolves_only_unambiguous_execution(monkeypatch):
    from types import SimpleNamespace
    from task_server.api_testing import access, http
    from task_server.api_testing.config import ApiTestingSettings
    monkeypatch.setattr(ApiTestingSettings, 'from_env', classmethod(lambda cls: SimpleNamespace(enabled=True)))
    monkeypatch.setattr(access, 'authorize_http', lambda *_: None)
    entries = [{'execution_case_id': service.CASE_ID}]
    def get(path, query, actor, settings):
        if len(path) == 2: return {'execution': {'id': service.EXECUTION_ID, 'case_results': entries}}
        return {'case_result': {'execution_case_id': path[-1]}}
    monkeypatch.setattr(http, '_get', get)
    assert service._fetch_authorized('alice', service.EXECUTION_ID, None)['case_result']['execution_case_id'] == service.CASE_ID
    entries.append({'execution_case_id': '22222222-2222-4222-8222-222222222222'})
    with pytest.raises(ValueError): service._fetch_authorized('alice', service.EXECUTION_ID, None)


def test_ambiguous_execution_gives_actionable_message(tmp_path):
    with patch.object(service, '_fetch_authorized', side_effect=service.AmbiguousCase()):
        result = service.run('alice-id', 'alice', tmp_path / 'ambiguous.sqlite', case_id=None)
    assert result['status'] == 'blocked' and '多条子用例' in result['message']
