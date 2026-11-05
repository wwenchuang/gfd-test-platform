import http.client
import json
import threading

from task_server import app, auth
from task_server.services import week1_probe
from test_identity import identity_db, create_member, activate, ALL_SCOPE


def request(server, method, path, token=None, payload=None):
    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    body = None if payload is None else json.dumps(payload).encode()
    if body is not None: headers['Content-Type'] = 'application/json'
    conn.request(method, path, body, headers)
    response = conn.getresponse()
    status, data = response.status, response.read()
    conn.close()
    return status, json.loads(data)


def test_http_auth_owner_and_body_limit(identity_db, tmp_path, monkeypatch):
    _, store = identity_db
    monkeypatch.setenv('TASK_OPERATION_DB', str(tmp_path / 'operations.sqlite3'))
    store.create_role('admin', {'id':'probe-admin','name':'探针管理员','permissions':['platform.configure']})
    create_member(store, username='observer')
    activate(store, 'observer')
    create_member(store, username='other', role_ids=['probe-admin'], scope=ALL_SCOPE)
    activate(store, 'other')
    admin = auth.create_session_token()
    observer = auth.create_session_token('observer')
    other = auth.create_session_token('other')
    monkeypatch.setattr(week1_probe, '_request_model', lambda mode: {'choices':[{'message':{'content':'左红矩形右蓝圆'}}]})
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.TaskHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        path = '/api/platform/week1-probes'
        assert request(server, 'GET', path)[0] == 401
        assert request(server, 'POST', path, observer, {'mode':'vision'})[0] == 403
        assert request(server, 'GET', path, observer)[0] == 403
        assert request(server, 'POST', path, admin, {'mode':'vision', 'extra':'x'*1100})[0] == 413
        assert request(server, 'POST', path, admin, {'mode':'vision', 'url':'https://evil.test'})[0] == 400
        status, response = request(server, 'POST', path, admin, {'mode':'vision'})
        assert status == 200 and response['result']['manual_check'] == 'pending'
        record_id = response['result']['id']
        assert request(server, 'GET', path, other)[1]['results'] == []
        assert request(server, 'POST', path+'/confirm', other, {'id':record_id,'correct':True})[0] == 400
        status, result = request(server, 'POST', path+'/confirm', admin, {'id':record_id,'correct':True})
        assert status == 200 and result['result']['manual_check'] == 'passed'
        assert len(request(server, 'GET', path, admin)[1]['results']) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
