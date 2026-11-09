import json
import http.client
import subprocess
import threading

from task_server import app, auth
from task_server.services import week2_real
from test_identity import identity_db, create_member, activate, ALL_SCOPE
from test_week1_probe_http import request


def test_http_permission_fixed_input_and_private_history(identity_db, tmp_path, monkeypatch):
    _, store = identity_db
    monkeypatch.setenv('TASK_OPERATION_DB', str(tmp_path / 'operations.sqlite3'))
    store.create_role('admin', {'id': 'w2-viewer', 'name': '实战观察员', 'permissions': ['platform.configure', 'api.view']})
    store.create_role('admin', {'id': 'w2-no-api', 'name': '配置员', 'permissions': ['platform.configure']})
    store.create_role('admin', {'id': 'w2-no-config', 'name': '接口观察员', 'permissions': ['api.view']})
    for name, role, scope in [('alice','w2-viewer',ALL_SCOPE), ('bob','w2-viewer',ALL_SCOPE),
                              ('limited','w2-viewer',{'ui_apps':['app.a']}), ('noapi','w2-no-api',ALL_SCOPE),
                              ('noconfig','w2-no-config',ALL_SCOPE)]:
        create_member(store, username=name, role_ids=[role], scope=scope)
        activate(store, name)
    tokens = {name:auth.create_session_token(name) for name in ('alice','bob','limited','noapi','noconfig')}
    records = {}
    def run(owner, username, **kwargs):
        records.setdefault(owner, []).append({'id':'result','status':'blocked'})
        if kwargs: records[owner][-1]['selected'] = kwargs
        return records[owner][-1]
    monkeypatch.setattr(week2_real, 'run', run)
    monkeypatch.setattr(week2_real, 'list_results', lambda owner, **kwargs: records.get(owner, []))
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.TaskHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        path = '/api/platform/week2-real'
        assert request(server, 'GET', path)[0] == 401
        assert request(server, 'GET', path, tokens['limited'])[0] == 403
        assert request(server, 'GET', path, tokens['noapi'])[0] == 403
        assert request(server, 'GET', path, tokens['noconfig'])[0] == 403
        assert request(server, 'POST', path, tokens['noconfig'], {})[0] == 403
        assert request(server, 'POST', path, tokens['noapi'], {})[0] == 403
        assert request(server, 'POST', path, tokens['alice'], {'url':'http://evil'})[0] == 400
        assert request(server, 'POST', path, tokens['alice'], {'execution_id':'bad'})[0] == 400
        assert request(server, 'POST', path, tokens['alice'], {'case_id':'22222222-2222-4222-8222-222222222222'})[0] == 400
        assert request(server, 'POST', path, tokens['alice'], {'execution_id':'bad','case_id':'bad'})[0] == 400
        assert request(server, 'POST', path, tokens['alice'], {'x':'y'*1100})[0] == 413
        assert request(server, 'POST', path, tokens['alice'], {})[0] == 200
        assert len(request(server, 'GET', path, tokens['alice'])[1]['results']) == 1
        chosen = {'execution_id':'11111111-1111-4111-8111-111111111111','case_id':'22222222-2222-4222-8222-222222222222'}
        assert request(server, 'POST', path, tokens['alice'], chosen)[1]['result']['selected'] == chosen
        assert request(server, 'POST', path, tokens['alice'], {'execution_id':chosen['execution_id']})[1]['result']['selected'] == {'execution_id':chosen['execution_id'], 'case_id':None}
        assert request(server, 'GET', path, tokens['bob'])[1]['results'] == []
        monkeypatch.setattr(week2_real, 'run', lambda *_: (_ for _ in ()).throw(week2_real.PracticeBusy()))
        status, value = request(server, 'POST', path, tokens['alice'], {})
        assert status == 429 and value['code'] == 'practice_busy'
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_http_filters_previously_visible_results_after_scope_revoked(identity_db, tmp_path, monkeypatch):
    identity, store = identity_db
    monkeypatch.setenv('TASK_OPERATION_DB', str(tmp_path / 'operations.sqlite3'))
    store.create_role('admin', {'id': 'scope-reader', 'name': '授权观察员', 'permissions': ['platform.configure', 'api.view']})
    create_member(store, username='scoped', role_ids=['scope-reader'], scope=ALL_SCOPE)
    activate(store, 'scoped')
    owner = identity.get_access_profile('scoped')['user_id']
    week2_real._save(None, owner, {'id':'a'*32, 'status':'passed',
        'execution_id':week2_real.EXECUTION_ID, 'execution_case_id':week2_real.CASE_ID}, 1)
    denied = [False]
    def scope(username, execution_id, case_id):
        assert username == 'scoped'
        if denied[0]: raise PermissionError('scope revoked')
    monkeypatch.setattr(week2_real, '_scope_authorized', scope)
    monkeypatch.setattr(week2_real, '_invoke_authorization', lambda owner, username, sources, timeout, **kwargs:
                        week2_real._verify_sources(owner, username, sources))
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.TaskHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        token = auth.create_session_token('scoped')
        assert len(request(server, 'GET', '/api/platform/week2-real', token)[1]['results']) == 1
        denied[0] = True
        hidden = request(server, 'GET', '/api/platform/week2-real', token)[1]['results']
        assert len(hidden) == 1 and hidden[0]['status'] == 'blocked' and 'execution_id' not in hidden[0]
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_http_denied_or_unverified_analysis_never_returns_source_ids(identity_db, tmp_path, monkeypatch):
    _, store = identity_db
    monkeypatch.setenv('TASK_OPERATION_DB', str(tmp_path / 'operations.sqlite3'))
    store.create_role('admin', {'id': 'limited-api', 'name': 'limited', 'permissions': ['platform.configure', 'api.view']})
    create_member(store, username='reader', role_ids=['limited-api'],
                  scope={'ui_apps': '*', 'api_projects': ['other']})
    activate(store, 'reader')
    execution = '11111111-1111-4111-8111-111111111111'
    case = '22222222-2222-4222-8222-222222222222'
    mode = ['denied']
    def worker(owner, username, execution_id, case_id, **kwargs):
        if mode[0] == 'denied':
            return week2_real._derive(owner, username, execution_id, case_id)
        if mode[0] == 'timeout':
            raise subprocess.TimeoutExpired('worker', 0.01)
        return True, {'status': 'passed', 'execution_case_id': case_id,
                      'checks': [{'id': 'private'}], 'original_assertions': []}
    def authorize(owner, username, sources, timeout, **kwargs):
        if mode[0] == 'revoked': return [False] * len(sources)
        if mode[0] == 'auth_timeout': raise subprocess.TimeoutExpired('authorize', .01)
        return [False] * len(sources)
    monkeypatch.setattr(week2_real, '_invoke_worker', worker)
    monkeypatch.setattr(week2_real, '_fetch_authorized',
                        lambda *_: (_ for _ in ()).throw(PermissionError('project or environment denied')))
    monkeypatch.setattr(week2_real, '_invoke_authorization', authorize)
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.TaskHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        token = auth.create_session_token('reader')
        for mode[0] in ('denied', 'timeout', 'revoked', 'auth_timeout'):
            for body in ({}, {'execution_id': execution}, {'execution_id': execution, 'case_id': case}):
                status, payload = request(server, 'POST', '/api/platform/week2-real', token, body)
                assert status == 200 and payload['result']['status'] == 'blocked'
                encoded = json.dumps(payload)
                for forbidden in (week2_real.EXECUTION_ID, week2_real.CASE_ID, execution, case, 'private'):
                    assert forbidden not in encoded
        status, payload = request(server, 'GET', '/api/platform/week2-real', token)
        assert status == 200
        encoded = json.dumps(payload)
        for forbidden in (week2_real.EXECUTION_ID, week2_real.CASE_ID, execution, case, 'private'):
            assert forbidden not in encoded
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_http_authorized_source_still_returns_analysis(identity_db, tmp_path, monkeypatch):
    _, store = identity_db
    monkeypatch.setenv('TASK_OPERATION_DB', str(tmp_path / 'operations.sqlite3'))
    store.create_role('admin', {'id': 'reader-api', 'name': 'reader', 'permissions': ['platform.configure', 'api.view']})
    create_member(store, username='reader', role_ids=['reader-api'], scope=ALL_SCOPE)
    activate(store, 'reader')
    monkeypatch.setattr(week2_real, '_invoke_worker', lambda *args, **kwargs:
                        (True, {'status': 'passed', 'execution_case_id': args[3], 'checks': [{'id':'check'}]}))
    monkeypatch.setattr(week2_real, '_invoke_authorization', lambda owner, username, sources, timeout, **kwargs:
                        [True] * len(sources))
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.TaskHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        token = auth.create_session_token('reader')
        status, payload = request(server, 'POST', '/api/platform/week2-real', token, {})
        result = payload['result']
        assert status == 200 and result['status'] == 'passed'
        assert result['execution_id'] == week2_real.EXECUTION_ID
        assert result['execution_case_id'] == week2_real.CASE_ID
        assert result['checks'] == [{'id':'check'}]
        status, payload = request(server, 'GET', '/api/platform/week2-real', token)
        assert status == 200 and payload['results'][0] == result
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_http_actor_permission_revoked_after_source_authorization_hides_result(identity_db, tmp_path, monkeypatch):
    _, store = identity_db
    monkeypatch.setenv('TASK_OPERATION_DB', str(tmp_path / 'operations.sqlite3'))
    store.create_role('admin', {'id': 'reader-api', 'name': 'reader', 'permissions': ['platform.configure', 'api.view']})
    create_member(store, username='reader', role_ids=['reader-api'], scope=ALL_SCOPE)
    activate(store, 'reader')
    valid = [True]
    monkeypatch.setattr(week2_real, 'authorized_actor', lambda *_: valid[0])
    monkeypatch.setattr(week2_real, '_invoke_worker', lambda *args, **kwargs:
                        (True, {'status': 'passed', 'execution_case_id': args[3], 'checks': [{'id':'private'}]}))
    def verify(*args, **kwargs):
        valid[0] = False
        return [True]
    monkeypatch.setattr(week2_real, '_invoke_authorization', verify)
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.TaskHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        token = auth.create_session_token('reader')
        status, payload = request(server, 'POST', '/api/platform/week2-real', token, {})
        assert status == 200 and payload['result']['status'] == 'blocked'
        encoded = json.dumps(payload)
        assert week2_real.EXECUTION_ID not in encoded and week2_real.CASE_ID not in encoded
        assert 'private' not in encoded
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_http_project_scope_revoked_after_source_authorization_hides_result(identity_db, tmp_path, monkeypatch):
    _, store = identity_db
    monkeypatch.setenv('TASK_OPERATION_DB', str(tmp_path / 'operations.sqlite3'))
    store.create_role('admin', {'id': 'reader-api', 'name': 'reader', 'permissions': ['platform.configure', 'api.view']})
    create_member(store, username='reader', role_ids=['reader-api'], scope=ALL_SCOPE)
    activate(store, 'reader')
    monkeypatch.setattr(week2_real, '_invoke_worker', lambda *args, **kwargs:
                        (True, {'status': 'passed', 'execution_case_id': args[3], 'checks': [{'id':'private'}]}))
    def verify(*args, **kwargs):
        store.update_user('admin', 'reader', {'scope': {'ui_apps':'*', 'api_projects':['other']}})
        return [True]
    monkeypatch.setattr(week2_real, '_invoke_authorization', verify)
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.TaskHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        token = auth.create_session_token('reader')
        status, payload = request(server, 'POST', '/api/platform/week2-real', token, {})
        assert status == 200 and payload['result']['status'] == 'blocked'
        encoded = json.dumps(payload)
        assert week2_real.EXECUTION_ID not in encoded and week2_real.CASE_ID not in encoded
        assert 'private' not in encoded
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_http_history_scope_revoked_after_authorization_hides_existing_result(identity_db, tmp_path, monkeypatch):
    identity, store = identity_db
    monkeypatch.setenv('TASK_OPERATION_DB', str(tmp_path / 'operations.sqlite3'))
    store.create_role('admin', {'id': 'reader-api', 'name': 'reader', 'permissions': ['platform.configure', 'api.view']})
    create_member(store, username='reader', role_ids=['reader-api'], scope=ALL_SCOPE)
    activate(store, 'reader')
    owner = identity.get_access_profile('reader')['user_id']
    week2_real._save(None, owner, {'id':'a'*32, 'status':'passed',
        'execution_id':week2_real.EXECUTION_ID, 'execution_case_id':week2_real.CASE_ID,
        'checks':[{'id':'private'}]}, 1)
    def verify(owner, username, sources, timeout, **kwargs):
        store.update_user('admin', 'reader', {'scope': {'ui_apps':'*', 'api_projects':['other']}})
        return [True] * len(sources)
    monkeypatch.setattr(week2_real, '_invoke_authorization', verify)
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.TaskHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        token = auth.create_session_token('reader')
        status, payload = request(server, 'GET', '/api/platform/week2-real', token)
        assert status == 200 and payload['results'][0]['status'] == 'blocked'
        encoded = json.dumps(payload)
        assert week2_real.EXECUTION_ID not in encoded and week2_real.CASE_ID not in encoded
        assert 'private' not in encoded
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_http_evidence_get_and_post_are_never_cacheable(identity_db, tmp_path, monkeypatch):
    _, store = identity_db
    monkeypatch.setenv('TASK_OPERATION_DB', str(tmp_path / 'operations.sqlite3'))
    store.create_role('admin', {'id': 'reader-api', 'name': 'reader', 'permissions': ['platform.configure', 'api.view']})
    create_member(store, username='reader', role_ids=['reader-api'], scope=ALL_SCOPE)
    activate(store, 'reader')
    monkeypatch.setattr(week2_real, '_invoke_worker', lambda *args, **kwargs: (False, None))
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.TaskHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        token = auth.create_session_token('reader')
        for method, current_token in (('GET', token), ('POST', token), ('GET', 'invalid-token')):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
            connection.request(method, '/api/platform/week2-real', body='{}' if method == 'POST' else None,
                               headers={'Authorization':'Bearer '+current_token, 'Content-Type':'application/json'})
            response = connection.getresponse()
            assert response.status == (401 if current_token == 'invalid-token' else 200)
            assert response.getheader('Cache-Control') == 'private, no-store'
            response.read(); connection.close()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
