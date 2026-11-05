import threading
import http.client

from task_server import app, auth
from task_server.services import week1_smoke
from test_identity import identity_db, create_member, activate, ALL_SCOPE
from test_week1_probe_http import request


def raw_request(server, path, token=None):
    connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=5)
    headers = {'Authorization': 'Bearer ' + token} if token else {}
    connection.request('GET', path, headers=headers)
    response = connection.getresponse()
    status, content_type, cache, payload = response.status, response.getheader('Content-Type'), response.getheader('Cache-Control'), response.read()
    connection.close()
    return status, content_type, cache, payload


def test_http_requires_scope_owner_and_fixed_body(identity_db, tmp_path, monkeypatch):
    _, store = identity_db
    monkeypatch.setenv('TASK_OPERATION_DB', str(tmp_path / 'operations.sqlite3'))
    store.create_role('admin', {'id':'smoke-admin','name':'冒烟管理员','permissions':['platform.configure']})
    create_member(store, username='observer')
    activate(store, 'observer')
    create_member(store, username='other', role_ids=['smoke-admin'], scope=ALL_SCOPE)
    activate(store, 'other')
    create_member(store, username='limited', role_ids=['smoke-admin'], scope={'ui_apps':['app.a']})
    activate(store, 'limited')
    admin = auth.create_session_token()
    observer = auth.create_session_token('observer')
    other = auth.create_session_token('other')
    limited = auth.create_session_token('limited')
    records = {}
    def run(owner, kind):
        result = {'id':'test-result', 'kind':kind, 'status':'blocked', 'checks':[]}
        records.setdefault(owner, []).append(result)
        return result
    monkeypatch.setattr(week1_smoke, 'run', run)
    monkeypatch.setattr(week1_smoke, 'list_results', lambda owner: records.get(owner, []))
    server = app.ThreadingHTTPServer(('127.0.0.1', 0), app.TaskHTTPHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        path = '/api/platform/week1-smoke'
        assert request(server, 'GET', path)[0] == 401
        assert request(server, 'GET', path, observer)[0] == 403
        assert request(server, 'GET', path, limited)[0] == 403
        assert request(server, 'POST', path, observer, {'kind':'api'})[0] == 403
        assert request(server, 'POST', path, admin, {'kind':'api','url':'http://evil'})[0] == 400
        assert request(server, 'POST', path, admin, {'kind':'api','extra':'x'*1100})[0] == 413
        assert request(server, 'POST', path, admin, {'kind':'environment'})[0] == 200
        assert len(request(server, 'GET', path, admin)[1]['results']) == 1
        assert request(server, 'GET', path, other)[1]['results'] == []
        artifact = path+'/artifact?id='+'a'*32+'&item=positive-screenshot'
        assert raw_request(server, artifact)[0] == 401
        assert raw_request(server, artifact, observer)[0] == 403
        assert raw_request(server, artifact, limited)[0] == 403
        admin_owner = next(iter(records))
        def get_artifact(owner, result_id, item):
            if owner != admin_owner or result_id != 'a'*32 or item != 'positive-screenshot':
                raise ValueError('not_found')
            return b'png', 'image/png'
        monkeypatch.setattr(week1_smoke, 'get_artifact', get_artifact, raising=False)
        status, mime, cache, payload = raw_request(server, artifact, admin)
        assert (status, mime, cache, payload) == (200, 'image/png', 'private, no-store', b'png')
        assert raw_request(server, artifact, other)[0] == 404
        assert raw_request(server, path+'/artifact?id=../secret&item=positive-screenshot', admin)[0] == 404
        monkeypatch.setattr(week1_smoke, 'run', lambda *args: (_ for _ in ()).throw(week1_smoke.SmokeBusy()))
        status, data = request(server, 'POST', path, admin, {'kind':'api'})
        assert status == 429 and data['code'] == 'smoke_busy'
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
