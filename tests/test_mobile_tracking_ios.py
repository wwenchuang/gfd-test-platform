import importlib.util
from contextlib import contextmanager
from pathlib import Path

import pytest

import mobile_tracking_ios as ios

spec = importlib.util.spec_from_file_location('tracking_runner',Path('mac-midscene-runner.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

UDID='00008110-000150CC21C2801E'
BUNDLE='com.kfb.3d'
STATUS={'ready':True,'ios':{'ip':'192.168.4.76'}}


def test_first_capture_can_start_before_a_receipt_exists(tmp_path,monkeypatch):
    cli=tmp_path/'proxyman';cli.touch();monkeypatch.setattr(ios,'PROXY_CLI',cli)
    def empty(folder,target,ip,since,until):
        assert ip=='192.168.4.76' and until-since==120
        return (1,[])
    assert ios.readiness(UDID,lambda *_:STATUS,empty)=={'ready':True,'stage':'first_capture','reason':'可开始，尚待核对本机首笔回执'}
    def inaccessible(*_): raise OSError('proxy unavailable')
    assert not ios.readiness(UDID,lambda *_:STATUS,inaccessible)['ready']
    assert ios.readiness(UDID,lambda *_:{'ready':False})['stage']=='wda'
    assert ios.readiness(UDID,lambda *_:{'ready':True,'ios':{'ip':'127.0.0.1'}})['stage']=='wda'
    assert ios.readiness(UDID,lambda *_:STATUS,lambda *_:(1,[{'httpStatus':200,'businessCode':0}]))['ready']


def test_runner_preview_and_unselected_never_claim(monkeypatch):
    row={'device_id':UDID,'platform':'ios','status':'online','preflight':{'ready':True},
         'installed_apps':[{'source_package':'com.kfb.model','installed':True}]}
    monkeypatch.setattr(runner,'IOS_EXECUTION_ENABLED',False)
    assert not runner.ios_tracking_states([row])[UDID]['ready']
    monkeypatch.setattr(runner,'IOS_EXECUTION_ENABLED',True);monkeypatch.setenv('IOS_DEVICE_ID','other')
    assert not runner.ios_tracking_states([row])[UDID]['ready']
    monkeypatch.setenv('IOS_DEVICE_ID',UDID)
    monkeypatch.setattr(ios,'readiness',lambda *_:{'ready':True,'reason':'ok'})
    assert runner.ios_tracking_states([row])[UDID]['ready']
    runner._IOS_JOB_ACTIVE.set()
    try: assert runner.ios_tracking_states([row])[UDID]['stage']=='busy'
    finally: runner._IOS_JOB_ACTIVE.clear()
    assert not runner.ios_tracking_states([dict(row,preflight={'ready':False,'reason':'锁屏'})])[UDID]['ready']
    assert runner.ios_tracking_states([])=={}


@pytest.mark.parametrize('mode',['match','foreign','ambiguous','ip_change'])
def test_run_keeps_exact_phone_app_element_and_window(mode,monkeypatch):
    clicks=[];closed=[];times=iter([100,110]);ips=iter(['192.168.4.76','192.168.4.77' if mode=='ip_change' else '192.168.4.76'])
    monkeypatch.setattr(ios.time,'time',lambda:next(times))
    monkeypatch.setattr(ios.time,'sleep',lambda _:None)
    @contextmanager
    def forward(device):
        assert device==UDID
        yield 45555
    def call(port,method,path,payload):
        assert port==45555
        if path=='/session' and method=='POST':
            assert payload['capabilities']['alwaysMatch']['bundleId']==BUNDLE
            return {'sessionId':'s'}
        if path=='/status':return {'value':{'ready':True,'ios':{'ip':next(ips)}}}
        if path=='/wda/activeAppInfo':return {'value':{'bundleId':BUNDLE if mode!='foreign' else 'app.other'}}
        if path.endswith('/elements'):
            assert '"热门"' in payload['value']
            return {'value':[{ios.ELEMENT_ID:'e'}]*(2 if mode=='ambiguous' else 1)}
        if path.endswith('/click'):clicks.append(path);return {'value':None}
        if method=='DELETE':closed.append(path);return {'value':None}
        raise AssertionError((method,path))
    def collect(folder,target,ip,since,until):
        assert (ip,since,until)==('192.168.4.76',100,110)
        return 2,[{'event':'AEC','time':101,'sent':102,'businessCode':0,'httpStatus':200},
                  {'event':'AEC','time':99,'sent':91}]
    r=ios.capture(UDID,BUNDLE,['热门'],lambda:None,forward,call,collect)
    assert closed==['/session/s']
    assert len(clicks)==int(mode in ('match','ip_change'))
    assert len(r['events'])==int(mode=='match')
    if mode=='match':assert r['events'][0]['actionIndex']==0


def test_unsupported_action_does_not_contact_device():
    with pytest.raises(ValueError,match='允许范围'):ios.capture(UDID,BUNDLE,['立即打印'],None,None)
