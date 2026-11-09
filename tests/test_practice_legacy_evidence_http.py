import json
import http.client
import threading
from contextlib import contextmanager

import pytest

from task_server import app, auth
from task_server.services import week2_real
from test_identity import ALL_SCOPE, activate, create_member, identity_db
from test_week1_probe_http import request

E1 = '11111111-1111-4111-8111-111111111111'
E2 = '22222222-2222-4222-8222-222222222222'


def test_http_returns_only_currently_authorized_records(identity_db, monkeypatch):
    from task_server.services import practice_legacy_evidence as service
    _, store = identity_db
    store.create_role('admin', {'id':'reader', 'name':'Reader', 'permissions':['platform.configure','api.view']})
    store.create_role('admin', {'id':'config-only', 'name':'Config', 'permissions':['platform.configure']})
    store.create_role('admin', {'id':'api-only', 'name':'API', 'permissions':['api.view']})
    for user, role in [('reader','reader'),('noapi','config-only'),('noconfig','api-only')]:
        create_member(store, username=user, role_ids=[role], scope=ALL_SCOPE)
        activate(store, user)
    monkeypatch.setattr(service, '_RECORDS', (
        {'key':'baseline', 'execution_id':E1, 'summary':'私有测试记录一'},
        {'key':'failure', 'execution_id':E2, 'summary':'私有测试记录二'},))
    @contextmanager
    def slot(_): yield 1
    monkeypatch.setattr(week2_real, '_history_slot', slot)
    allow = {E1}
    monkeypatch.setattr(service, '_invoke_authorization',
                        lambda owner, username, ids, *_args, **_kwargs: [i['execution_id'] in allow for i in ids])
    server = app.ThreadingHTTPServer(('127.0.0.1',0), app.TaskHTTPHandler)
    thread = threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try:
        path = '/api/platform/practice-legacy-evidence'
        assert request(server,'GET',path)[0] == 401
        assert request(server,'GET',path,auth.create_session_token('noapi'))[0] == 403
        assert request(server,'GET',path,auth.create_session_token('noconfig'))[0] == 403
        token = auth.create_session_token('reader')
        status, payload = request(server,'GET',path,token)
        assert status == 200 and len(payload['records']) == 1
        assert payload['records'][0]['execution_id'] == E1
        assert E2 not in json.dumps(payload) and '私有测试记录二' not in json.dumps(payload,ensure_ascii=False)
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
        conn.request('GET',path,headers={'Authorization':'Bearer '+token})
        response=conn.getresponse();assert response.getheader('Cache-Control')=='private, no-store'
        response.read();conn.close()
        allow.clear()
        status, payload = request(server,'GET',path,token)
        assert status == 200 and payload['records'] == []
        assert E1 not in json.dumps(payload) and E2 not in json.dumps(payload)
        monkeypatch.setattr(service, '_invoke_authorization', lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError()))
        assert request(server,'GET',path,token)[1]['records'] == []
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)


def test_profile_mutated_during_verification_hides_every_record(monkeypatch):
    from task_server.services import practice_legacy_evidence as service
    profile={'user_id':'owner','scope':{'api_projects':['one']}}
    monkeypatch.setattr(service.identity,'get_access_profile',lambda _:profile)
    monkeypatch.setattr(week2_real,'authorized_actor',lambda *_:True)
    @contextmanager
    def slot(_): yield 1
    monkeypatch.setattr(week2_real,'_history_slot',slot)
    def changed(*_args,**_kwargs):
        profile['scope']['api_projects'].clear()
        return [True] * len(service._RECORDS)
    monkeypatch.setattr(service,'_invoke_authorization',changed)
    assert service.get_records('owner','reader') == []
