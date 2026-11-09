import http.client
import json
import subprocess
import threading

import pytest

from task_server import app, auth
from task_server.services import week2_real
from test_identity import ALL_SCOPE, activate, create_member, identity_db
from test_week1_probe_http import request


EXECUTION = '11111111-1111-4111-8111-111111111111'
CASE = '22222222-2222-4222-8222-222222222222'


@pytest.fixture(autouse=True)
def inline_authorization_worker(monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def slot(_):
        yield 1
    monkeypatch.setattr(week2_real, '_history_slot', slot)
    monkeypatch.setattr(week2_real, '_invoke_authorization',
                        lambda owner, username, sources, *_args, **_kwargs: week2_real._verify_sources(owner, username, sources))


def report():
    return {'scope': 'snapshot_hits_only', 'membership_revalidated': False,
            'history_source': 'same_schedule_and_exact_version', 'count': 1,
            'items': [{'cleanup_structure': {'steps': [{'index': 0, 'enabled': True, 'extraction_count': 1}]},
                       'latest_scheduled_history': {'execution_id': EXECUTION, 'execution_case_id': CASE,
                           'created_at': '2026-11-09T00:00:00+00:00', 'case_status': 'PASSED',
                           'cleanup_trace': [{'index': 0, 'attempts': [{'status': 'PASSED', 'output_state': 'nonempty_recorded'}]}]}}]}


def test_http_evidence_requires_identity_permissions_and_live_scope(identity_db, tmp_path, monkeypatch):
    _, store = identity_db
    store.create_role('admin', {'id':'reader', 'name':'Reader', 'permissions':['platform.configure','api.view']})
    store.create_role('admin', {'id':'config-only', 'name':'Config', 'permissions':['platform.configure']})
    store.create_role('admin', {'id':'api-only', 'name':'API', 'permissions':['api.view']})
    for username, role in [('reader','reader'), ('noapi','config-only'), ('noconfig','api-only')]:
        create_member(store, username=username, role_ids=[role], scope=ALL_SCOPE)
        activate(store, username)
    source = tmp_path / 'diagnostic.json'
    source.write_text(json.dumps(report()), encoding='utf-8'); source.chmod(0o600)
    monkeypatch.setenv('TASK_PRACTICE_CLEANUP_EVIDENCE', str(source))
    allow = {'reader': True}
    def scope(username, execution, case):
        assert (execution, case) == (EXECUTION, CASE)
        if not allow.get(username): raise PermissionError('scope denied')
    monkeypatch.setattr(week2_real, '_scope_authorized', scope)
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.TaskHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    path = '/api/platform/practice-cleanup-evidence'
    try:
        assert request(server, 'GET', path)[0] == 401
        assert request(server, 'GET', path, auth.create_session_token('noapi'))[0] == 403
        assert request(server, 'GET', path, auth.create_session_token('noconfig'))[0] == 403
        token = auth.create_session_token('reader')
        status, result = request(server, 'GET', path, token)
        assert status == 200 and result['evidence']['execution_id'] == EXECUTION
        assert '1 项' in result['evidence']['summary'] and '非空' in result['evidence']['summary']
        assert all(word in result['evidence']['summary'] for word in ('归属','物理清理','异常恢复','候选已上线'))
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
        conn.request('GET', path, headers={'Authorization':'Bearer '+token})
        response = conn.getresponse()
        assert response.getheader('Cache-Control') == 'private, no-store'
        response.read(); conn.close()
        allow['reader'] = False
        status, result = request(server, 'GET', path, token)
        assert status == 200 and result['evidence'] is None and EXECUTION not in json.dumps(result)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_missing_unsafe_or_unverified_evidence_is_never_returned(tmp_path, monkeypatch):
    from task_server.services import practice_cleanup_evidence as service
    monkeypatch.setattr(week2_real, 'authorized_actor', lambda owner, username: True)
    monkeypatch.setattr(service.identity, 'get_access_profile', lambda username: {'user_id':'owner'})
    calls = []
    monkeypatch.setattr(week2_real, '_scope_authorized', lambda username, execution, case: calls.append((username, execution, case)))
    monkeypatch.delenv('TASK_PRACTICE_CLEANUP_EVIDENCE', raising=False)
    assert service.get_evidence('owner', 'reader') is None
    source = tmp_path / 'audit.json'
    monkeypatch.setenv('TASK_PRACTICE_CLEANUP_EVIDENCE', str(source))
    assert service.get_evidence('owner', 'reader') is None
    source.write_text(json.dumps(report()), encoding='utf-8'); source.chmod(0o644)
    assert service.get_evidence('owner', 'reader') is None
    source.chmod(0o600)
    assert service.get_evidence('owner', 'reader')['execution_id'] == EXECUTION
    assert calls == [('reader', EXECUTION, CASE)]
    broken = report(); broken['items'][0]['latest_scheduled_history']['case_status'] = 'FAILED'
    source.write_text(json.dumps(broken), encoding='utf-8')
    assert service.get_evidence('owner', 'reader') is None
    broken = report(); broken['items'][0]['cleanup_structure']['steps'][0]['extraction_count'] = 0
    source.write_text(json.dumps(broken), encoding='utf-8')
    assert service.get_evidence('owner', 'reader') is None
    broken = report(); broken['items'][0]['latest_scheduled_history']['cleanup_trace'][0]['attempts'][0]['output_state'] = 'unknown'
    source.write_text(json.dumps(broken), encoding='utf-8')
    assert service.get_evidence('owner', 'reader') is None


def test_scope_changes_mid_request_fail_closed(tmp_path, monkeypatch):
    from task_server.services import practice_cleanup_evidence as service
    profile = {'user_id':'owner', 'scope':{'api_projects':['one'], 'api_environments':['one']}}
    monkeypatch.setattr(service.identity, 'get_access_profile', lambda username: profile)
    monkeypatch.setattr(week2_real, 'authorized_actor', lambda *_: True)
    monkeypatch.setattr(week2_real, '_scope_authorized', lambda *_: profile['scope']['api_projects'].clear())
    source = tmp_path / 'diagnostic.json'
    source.write_text(json.dumps(report())); source.chmod(0o600)
    monkeypatch.setenv('TASK_PRACTICE_CLEANUP_EVIDENCE', str(source))
    assert service.get_evidence('owner', 'reader') is None


@pytest.mark.parametrize('reason', ['busy', 'timeout'])
def test_authorization_worker_busy_or_timeout_hides_evidence(tmp_path, monkeypatch, reason):
    from contextlib import contextmanager
    from task_server.services import practice_cleanup_evidence as service
    monkeypatch.setattr(service.identity, 'get_access_profile', lambda username: {'user_id':'owner'})
    monkeypatch.setattr(week2_real, 'authorized_actor', lambda *_: True)
    monkeypatch.setattr(week2_real, '_scope_authorized', lambda *_: None)
    @contextmanager
    def slot(_):
        if reason == 'busy': raise week2_real.HistoryBusy()
        yield 1
    monkeypatch.setattr(week2_real, '_history_slot', slot)
    monkeypatch.setattr(week2_real, '_invoke_authorization', lambda *_args, **_kwargs: (_ for _ in ()).throw(subprocess.TimeoutExpired('scope', 15)))
    source = tmp_path / 'diagnostic.json'
    source.write_text(json.dumps(report())); source.chmod(0o600)
    monkeypatch.setenv('TASK_PRACTICE_CLEANUP_EVIDENCE', str(source))
    assert service.get_evidence('owner', 'reader') is None
