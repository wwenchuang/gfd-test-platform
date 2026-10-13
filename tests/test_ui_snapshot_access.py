"""Direct screenshot requests obey the current actor and immutable capture scope."""
import base64
import builtins
import io
import threading
from pathlib import Path

import pytest

from task_server.services import runner_service
from task_server.services.operation_attribution import actor_context


APP = 'com.fixture.app'
ACTOR = {'kind':'user','username':'reader','user_id':'u-reader','display_name':'读者'}
PNG = b'\x89PNG\r\n\x1a\nfirst-image'


class Handler:
    def __init__(self):
        self.status = None
        self.body = None
        self.headers = {}
        self.wfile = io.BytesIO()
    def _json(self, body, status=200):
        self.body, self.status = body, status
    def send_response(self, status):
        self.status = status
    def _cors(self):
        pass
    def send_header(self, name, value):
        self.headers[name] = value
    def end_headers(self):
        pass


@pytest.fixture
def env(tmp_path, monkeypatch):
    from task_server import identity, router
    from task_server.services import business_line_service
    profile = {'username':'reader','user_id':'u-reader','status':'active','permissions':['ui.view'],
               'scope':{'ui_apps':[APP]},'must_change_password':False}
    apps = [{ 'package':APP,'enabled':True,'platform_mappings':{'ios':'com.fixture.ios'} },
            { 'package':'com.secret','enabled':True,'platform_mappings':{'ios':'com.secret.ios'} }]
    monkeypatch.setattr(identity,'get_access_profile',lambda _:profile)
    monkeypatch.setattr(business_line_service,'configured_test_application',lambda package,**_:next((a for a in apps if a['package']==package),None))
    monkeypatch.setattr(router,'_require_user_auth',lambda _:False)
    monkeypatch.setattr(runner_service,'RUNNERS_FILE',str(tmp_path/'runners.json'))
    monkeypatch.setattr(runner_service,'DEVICE_SNAPSHOT_DIR',str(tmp_path/'shots'))
    monkeypatch.setattr(runner_service,'JOBS_FILE',str(tmp_path/'jobs.json'))
    runner_service.save_runners({'r1':{'runner_id':'r1','last_seen_ts':runner_service.time.time(),
        'devices':[{'device_id':'p1','platform':'ios','status':'online'}]}})
    request=runner_service.request_device_snapshots()['requests'][0]
    runner_service.save_device_snapshot('r1',{'device_id':'p1','request_id':request['request_id'],
        'foreground_package':'com.fixture.ios','content_base64':base64.b64encode(PNG).decode()})
    def get(app_id=APP):
        h=Handler()
        with actor_context(ACTOR):
            router._get_runner_device_snapshot(h,{'runner_id':'r1','device_id':'p1','app_id':app_id} if app_id is not None else {'runner_id':'r1','device_id':'p1'})
        return h
    return profile, get, tmp_path


def test_direct_snapshot_denies_restricted_scope_even_for_matching_capture(env):
    profile, get, _ = env
    assert get().status==403
    assert get('com.secret').status==403
    assert get(None).status==403
    profile['scope']['ui_apps']=[]
    assert get(APP).status==403


def test_direct_snapshot_requires_live_ui_view_and_all_app_scope(env):
    profile, get, _ = env
    profile['scope']['ui_apps']='*'
    assert get(APP).status==200 and get(None).wfile.getvalue()==PNG
    profile['permissions']=[]
    assert get(APP).status==403
    profile['permissions']=['ui.view']
    rows=runner_service.load_runners()
    rows['r1']['devices'][0].pop('snapshot_app_package',None)
    runner_service.save_runners(rows)
    assert get(APP).status==200
    assert get(None).status==200
    profile['scope']['ui_apps']=[APP]
    assert get(None).status==403


def test_snapshot_fd_survives_replacement_after_headers_begin(env, monkeypatch):
    profile, get, tmp = env
    profile['scope']['ui_apps']='*'
    # The response must use the opened descriptor, not a second path lookup.
    original=Handler.send_header
    def replace(self,name,value):
        if name=='Content-Length':
            rows=runner_service.load_runners()
            filename=rows['r1']['devices'][0]['snapshot_file']
            Path(tmp/'shots'/filename).unlink()
            (tmp/'shots'/filename).write_bytes(b'bad-replacement')
        original(self,name,value)
    monkeypatch.setattr(Handler,'send_header',replace)
    assert get().wfile.getvalue()==PNG


def test_snapshot_open_precedes_concurrent_pruning(env, monkeypatch):
    profile, get, _ = env
    profile['scope']['ui_apps']='*'
    pending=runner_service.request_device_snapshots()['requests'][0]
    started=threading.Event()
    completed=threading.Event()
    worker=[]
    def replace_capture():
        started.set()
        runner_service.save_device_snapshot('r1',{'device_id':'p1','request_id':pending['request_id'],
            'foreground_package':'com.other','content_base64':base64.b64encode(PNG+b'-new').decode()})
        completed.set()
    original=builtins.open
    def observed_open(path, *args, **kwargs):
        if str(path).endswith('.png') and threading.current_thread() is threading.main_thread():
            thread=threading.Thread(target=replace_capture)
            worker.append(thread)
            thread.start()
            assert started.wait(1)
            assert not completed.wait(0.05)
        return original(path,*args,**kwargs)
    monkeypatch.setattr(builtins,'open',observed_open)
    assert get().wfile.getvalue()==PNG
    worker[0].join(2)
    assert completed.is_set()
