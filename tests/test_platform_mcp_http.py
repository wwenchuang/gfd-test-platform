"""Real personal sessions, HTTP dispatch and owner-scoped W05 evidence storage."""
import http.client
import json
import threading
import time
import uuid

from task_server import app, auth
from task_server.services import week5_web_service
from test_identity import activate, create_member, identity_db
from test_week5_web_service import PNG, WORKER, trace_bytes


def request(server, method, path, token=None, payload=None):
    conn = http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=8)
    try:
        headers = {'Authorization': 'Bearer ' + token} if token else {}
        if payload is not None:
            headers['Content-Type']='application/json'
        body = payload if isinstance(payload, bytes) else (json.dumps(payload).encode() if payload is not None else None)
        conn.request(method,path,body,headers)
        reply = conn.getresponse()
        data = reply.read()
        if reply.headers['Content-Type'].startswith('application/json'):
            data = json.loads(data)
        return reply.status, data, reply.headers
    finally:
        conn.close()


def test_real_sessions_dispatch_read_only_check_and_persist_evidence(identity_db, tmp_path, monkeypatch):
    _, store = identity_db
    monkeypatch.setenv('TASK_OPERATION_DB',str(tmp_path/'audit.sqlite3'))
    store.create_role('admin',{'id':'catalog-reader','name':'只读目录','permissions':['ui.view']})
    for user in ('reader','other'):
        create_member(store,username=user,role_ids=['catalog-reader'],scope={'ui_apps':['app.one']})
        activate(store,user)
    owner = auth.create_session_token('reader')
    outsider = auth.create_session_token('other')
    png = PNG
    trace = trace_bytes()
    def browser(*args,control=None):
        if control:
            control.worker_exit_code=0
            control.node_version='22.0.0'
        return dict(WORKER,total=5),png,trace
    monkeypatch.setattr(week5_web_service,'_execute',browser)
    server = app.ThreadingHTTPServer(('127.0.0.1',0),app.TaskHTTPHandler)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    base = '/api/platform/mcp'
    def call(token,name,args):
        return request(server,'POST',base+'/call',token,{'name':name,'arguments':args})
    try:
        assert call(None,'list_suites',{})[0]==401
        assert call(owner,'list_suites',{})[1]['result']['suites'][0]['suite_id']=='published-catalog'
        assert call(owner,'list_suites',{'extra':'x'*1100})[0]==413
        malformed = request(server,'POST',base+'/call',owner,b'{')
        assert malformed[0]==400 and malformed[1]['code']=='invalid_arguments'
        args={'request_id':str(uuid.uuid4()),'suite_id':'published-catalog','environment':'platform'}
        response = call(owner,'run_suite',args)
        assert response[0]==200 and response[2]['Cache-Control']=='private, no-store'
        rid = response[1]['result']['run_id']
        for _ in range(50):
            row = call(owner,'get_run',{'run_id':rid})[1]['result']
            if row['status'] not in ('queued','running'):
                break
            time.sleep(.05)
        assert row['status']=='passed' and row['total']==5
        assert call(owner,'run_suite',args)[1]['result']==row
        assert request(server,'GET',base+'/runs',owner)[1]['runs']==[row]
        import xml.etree.ElementTree as ET
        status, content, headers = request(server,'GET',row['junit_url'],owner)
        assert status == 200 and headers['Content-Type'].startswith('application/xml')
        assert headers['Cache-Control'] == 'private, no-store'
        suite = ET.fromstring(content)
        case = suite.find('testcase')
        assert suite.get('tests') == '1' and case.get('name') == row['cases'][0]['case_id']
        props = {p.get('name'):p.get('value') for p in case.findall('properties/property')}
        assert props['run_id'] == rid and props['attempt'] == '1'
        assert request(server,'GET',row['junit_url'],outsider)[0] == 404
        assert request(server,'GET',base+'/junit?run_id=nope',owner)[0] == 400
        assert request(server,'GET',base+'/runs',outsider)[1]['runs']==[]
        assert call(outsider,'get_run',{'run_id':rid})[0]==404
        for key,data in [('screenshot',png),('trace',trace)]:
            result=request(server,'GET',row['evidence'][key],owner)
            assert result[0]==200 and result[1]==data and result[2]['Cache-Control']=='private, no-store'
            assert request(server,'GET',row['evidence'][key],outsider)[0]==404
        store.update_user('admin','reader',{'scope':{'ui_apps':['app.two']}})
        assert call(owner,'get_run',{'run_id':rid})[0]==404
        assert request(server,'GET',row['evidence']['trace'],owner)[0]==404
        auth.logout(owner)
        assert request(server,'GET',base+'/runs',owner)[0]==401
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
