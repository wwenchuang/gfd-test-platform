import importlib.util
from pathlib import Path
from unittest.mock import Mock
import pytest
from task_server.services import mobile_tracking as track

spec = importlib.util.spec_from_file_location('ios_probe_mac_runner', Path('mac-midscene-runner.py'))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def blocked_ios(**updates):
    return dict(dict(device_id=track.IPHONE, platform='ios', status='online',
                     runner_id='mac', runner_online=True, usage_status='unknown',
                     preflight_status='blocked', preflight={'ready':False,'reason':'WDA 未连接'},
                     runner_capabilities={'tracking_ios_probe_devices':{track.IPHONE: {'ready':True,'reason':'可诊断'}}}), **updates)


def test_probe_readiness_is_separate_from_wda_and_requires_one_idle_runner(tmp_path, monkeypatch):
    monkeypatch.setattr(track, 'LEARNING_DIR',tmp_path)
    row = blocked_ios()
    online = {'__source__':{'available':True},track.IPHONE:{'sonic_status':'ONLINE'}}
    selected = lambda rows, directory=online: track.selectable_devices(rows,directory)[-1]
    assert not selected([row])['ready'] and selected([row])['probeReady']
    assert not selected([blocked_ios(usage_status='busy')])['probeReady']
    assert not selected([blocked_ios(runner_online=False)])['probeReady']
    assert not selected([row,row])['probeReady']
    assert not selected([row],{'__source__':{'available':True},track.IPHONE:{'sonic_status':'TESTING'}})['probeReady']


def test_claim_isolates_legacy_capture_and_probe_owner_and_result(tmp_path, monkeypatch):
    monkeypatch.setattr(track,'LEARNING_DIR',tmp_path)
    rid=track.start('tester','mac',device=track.IPHONE,probe=True)
    assert track.read('tester',rid)['kind']=='ios_proxy_probe'
    assert not track.selectable_devices([blocked_ios()])[-1]['probeReady']
    with pytest.raises(ValueError):track.read('other',rid)
    with pytest.raises(ValueError):track.start('operator','mac',['首页'],track.IPHONE)
    assert track.claim('mac',[track.IPHONE]) is None
    assert track.claim('mac',[],probe_devices=[track.HONOR]) is None
    assert track.claim('mac',[],probe_devices=[track.IPHONE])==rid
    track.finish(rid,'other',{'events':[{'raw':'secret'}]})
    assert track.read('tester',rid)['state']=='running'
    track.finish(rid,'mac',{'device':track.IPHONE,'events':[{'raw':'secret'}], 'probeResult':{'kind':'ios_proxy_probe','stage':'unavailable','raw':'secret','reason':'secret','sourceVerified':False,'connectCount':5}})
    result=track.read('tester',rid)
    assert 'secret' not in str(result) and 'events' not in result
    assert result['state']=='done' and result['probeResult']['kind']=='ios_proxy_probe'
    normal=track.start('tester','mac',['首页'],track.IPHONE)
    assert track.claim('mac',[],probe_devices=[track.IPHONE]) is None
    assert track.claim('mac',[track.IPHONE])==normal
    with pytest.raises(ValueError):track.start('tester','mac',device=track.HONOR,probe=True)


def test_runner_heartbeat_checks_tools_without_activating_apps(monkeypatch):
    monkeypatch.setenv('IOS_DEVICE_ID',track.IPHONE)
    monkeypatch.setattr(runner,'IOS_EXECUTION_ENABLED',True)
    monkeypatch.setattr(runner,'ios_probe_tools_available',lambda: True)
    row={'platform':'ios','device_id':track.IPHONE,'status':'online','ios_pairing_verified':True,
         'preflight':{'ready':False},'installed_apps':[{'installed':True,'source_package':'com.kfb.model','package':'com.kfb.3d'}]}
    assert runner.ios_probe_states([row])[track.IPHONE]['ready']
    assert not runner.ios_probe_states([dict(row,ios_pairing_verified=False)])[track.IPHONE]['ready']
    monkeypatch.setenv('IOS_DEVICE_ID','wrong')
    assert not runner.ios_probe_states([row])[track.IPHONE]['ready']


def test_user_route_creates_probe_only_on_registered_runner(tmp_path, monkeypatch):
    from task_server import router
    from task_server.services import ui_execution_contract
    monkeypatch.setattr(track,'LEARNING_DIR',tmp_path)
    monkeypatch.setattr(ui_execution_contract,'require_ui_actor',lambda **_: {'user_id':'tester'})
    monkeypatch.setattr(router,'all_online_devices',lambda:[blocked_ios()])
    monkeypatch.setattr(router,'sonic_list_device_statuses',lambda:{'__source__':{'available':True}})
    handler=Mock()
    handler._body.return_value={'probe':True,'device':track.IPHONE,'runner':'foreign'}
    router._tracking_user(handler,{})
    rid=handler._json.call_args.args[0]['id']
    assert track.assignment_for(rid,'mac')['kind']=='ios_proxy_probe'
    assert not track.assignment_for(rid,'foreign')
    handler._body.return_value={'id':rid}
    router._tracking_user(handler,{})
    assert handler._json.call_args.args[0]['state']=='queued'
    with track.db() as con:
        con.execute('UPDATE runs SET created=0 WHERE id=?',(rid,))
    assert '超时' in track.read('tester',rid)['note']
    assert track.claim('mac',[],probe_devices=[track.IPHONE]) is None
    assert track.start('tester','mac',device=track.IPHONE,probe=True)!=rid


def test_poll_dispatches_explicit_probe_while_capture_unavailable(monkeypatch):
    from mobile_tracking_ios_probe import REASONS
    import mobile_tracking_ios_probe as module
    monkeypatch.setattr(runner,'RUNNER_PLATFORM','ios')
    monkeypatch.setattr(runner,'_tracking_thread',None)
    monkeypatch.setattr(runner,'_tracking_next',0)
    monkeypatch.setattr(runner,'ios_tracking_states',lambda _: {track.IPHONE:{'ready':False}})
    monkeypatch.setattr(runner,'ios_probe_states',lambda _: {track.IPHONE:{'ready':True}})
    replies=[{},dict(id='a',device=track.IPHONE,kind='ios_proxy_probe')]
    calls=[]
    def exchange(method,endpoint,payload,timeout):
        calls.append(payload)
        if 'result' in payload:return {}
        assert payload['devices']==[] and payload['probeDevices']==[track.IPHONE]
        return replies.pop(0)
    monkeypatch.setattr(runner,'http_json',exchange)
    def probe(serial,bundle):
        assert serial==track.IPHONE and bundle=='com.kfb.3d'
        assert runner._IOS_JOB_ACTIVE.is_set()
        return {'kind':'ios_proxy_probe','reason':REASONS['no_event']}
    launch=Mock(side_effect=probe)
    monkeypatch.setattr(module,'probe',launch)
    runner.poll_tracking([])
    runner._tracking_thread.join(2)
    launch.assert_not_called()
    monkeypatch.setattr(runner,'_tracking_next',0)
    runner.poll_tracking([])
    runner._tracking_thread.join(2)
    launch.assert_called_once()
    assert not runner._IOS_JOB_ACTIVE.is_set()
    assert calls[-1]['result']['probeResult']['kind']=='ios_proxy_probe'


def test_saved_diagnosis_keeps_measured_zero_separate_from_unknown(tmp_path, monkeypatch):
    from mobile_tracking_ios_probe import REASONS
    monkeypatch.setattr(track,'LEARNING_DIR',tmp_path)
    for measured in (False,True):
        rid=track.start('tester','mac',device=track.IPHONE,probe=True)
        assert track.claim('mac',[],probe_devices=[track.IPHONE])==rid
        result=dict(kind='ios_proxy_probe',stage='complete' if measured else 'observe',
                    reason=REASONS['no_event'] if measured else REASONS['incomplete'],
                    sourceVerified=True, probeMatches=2,appRestored=True,observationComplete=measured,
                    windowSeconds=12,connectCount=0,postCount=0,receiptCount=0,acceptedCount=0,eventCount=0,
                    sourceIp='unpublished',events=[{'private':'unpublished'}])
        track.finish(rid,'mac',{'device':track.IPHONE,'probeResult':result})
        public=track.read('tester',rid)['probeResult']
        assert (public['acceptedCount']==0 if measured else public['acceptedCount'] is None)
        assert public['reason']==result['reason'] and 'unpublished' not in str(public)


def test_delayed_diagnostic_result_does_not_replace_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(track,'LEARNING_DIR',tmp_path)
    rid=track.start('tester','mac',device=track.IPHONE,probe=True)
    assert track.claim('mac',[],probe_devices=[track.IPHONE])==rid
    with track.db() as con:
        con.execute('UPDATE runs SET created=0 WHERE id=?',(rid,))
    expired=track.read('tester',rid)
    track.finish(rid,'mac',{'device':track.IPHONE,'probeResult':{}})
    assert track.read('tester',rid)==expired
