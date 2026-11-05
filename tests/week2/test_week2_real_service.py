import json
import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from task_server.services import week2_real as service


@pytest.fixture(autouse=True)
def authorized(monkeypatch):
    monkeypatch.setattr(service, 'authorized_actor', lambda *_: True)
    monkeypatch.setattr(service, '_scope_authorized', lambda *_: None)


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
    with patch.object(service, '_fetch_authorized', side_effect=lambda *_: time.sleep(.1)), patch.object(service, '_TIMEOUT_SECONDS', .01):
        result = service.run('alice-id', 'alice', db)
        assert result['status'] == 'blocked'
    assert len(service.list_results('alice-id', db)) == 2
    for worker in service._WORKERS:
        worker.join(timeout=2)


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


def test_late_worker_cannot_accumulate_or_remove_replacement_lease(tmp_path, monkeypatch):
    db = tmp_path / 'timeout.sqlite'
    release = threading.Event()
    def slow(*_):
        release.wait(timeout=2)
        return None
    with patch.object(service, '_fetch_authorized', slow), patch.object(service, '_TIMEOUT_SECONDS', .01):
        result = service.run('alice-id', 'alice', db)
        assert result['status'] == 'blocked'
        # Simulate expiration while the old thread remains stuck.
        with service._db(db) as conn:
            conn.execute('UPDATE lease SET until_time=0 WHERE id=1')
        with pytest.raises(service.PracticeBusy):
            service.run('alice-id', 'alice', db)
        replacement = service._claim(db)
        release.set()
        for worker in service._WORKERS: worker.join(timeout=2)
        with pytest.raises(service.PracticeBusy): service._claim(db)
        service._release(db, replacement)
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
