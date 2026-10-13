"""Direct screenshot requests obey the current actor and immutable capture scope."""
import base64
import io
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


def test_direct_snapshot_allows_current_scoped_app_and_denies_other_app(env):
    profile, get, _ = env
    allowed=get()
    assert allowed.status==200 and allowed.wfile.getvalue()==PNG
    assert get('com.secret').status==403
    assert get(None).status==403
    profile['scope']['ui_apps']=[]
    assert get(APP).status==403


def test_direct_snapshot_denies_missing_permission_and_unknown_legacy_capture(env):
    profile, get, _ = env
    profile['permissions']=[]
    assert get(APP).status==403
    profile['permissions']=['ui.view']
    rows=runner_service.load_runners()
    rows['r1']['devices'][0].pop('snapshot_app_package',None)
    runner_service.save_runners(rows)
    assert get(APP).status==403
    profile['scope']['ui_apps']='*'
    assert get(None).status==200


def test_snapshot_fd_survives_replacement_after_headers_begin(env, monkeypatch):
    _, get, tmp = env
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
