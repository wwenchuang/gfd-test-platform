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
    for name, role, scope in [('alice','w2-viewer',ALL_SCOPE), ('bob','w2-viewer',ALL_SCOPE),
                              ('limited','w2-viewer',{'ui_apps':['app.a']}), ('noapi','w2-no-api',ALL_SCOPE)]:
        create_member(store, username=name, role_ids=[role], scope=scope)
        activate(store, name)
    tokens = {name:auth.create_session_token(name) for name in ('alice','bob','limited','noapi')}
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
