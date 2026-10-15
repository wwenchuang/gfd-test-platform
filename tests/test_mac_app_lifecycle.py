import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest

spec = importlib.util.spec_from_file_location('lifecycle_runner', Path(__file__).parents[1] / 'mac-midscene-runner.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def test_android_reset_only_touches_target(monkeypatch):
    calls=[]
    monkeypatch.setattr(r,'run_cmd',lambda args,**kw: calls.append(args) or SimpleNamespace(returncode=0,stdout=''))
    monkeypatch.setattr(r.time,'sleep',lambda _:None)
    r.reset_foreground_app('adb','phone','com.kfb.model')
    assert calls == [['adb','-s','phone','shell','am','force-stop','com.kfb.model']]

@pytest.mark.parametrize('status',['passed','failed','cancelled'])
def test_cleanup_preserves_business_result_and_records_outcome(tmp_path,monkeypatch,status):
    calls=[]
    result={'status':status,'stdout':'business result'}
    actual=r.with_app_cleanup(lambda:result,lambda:calls.append('closed'),tmp_path/'teardown.json')
    assert actual is result and actual['status']==status
    assert actual['teardown']['ok'] is True and calls==['closed']
    assert (tmp_path/'teardown.json').exists()


def test_cleanup_on_exception_preserves_original_error(tmp_path):
    calls=[]
    def execute(): raise RuntimeError('original')
    with pytest.raises(RuntimeError,match='original'):
        r.with_app_cleanup(execute,lambda:calls.append('closed'),tmp_path/'teardown.json')
    assert calls==['closed']


def test_cleanup_failure_is_explicit_not_fake_success(tmp_path):
    def cleanup(): raise RuntimeError('unavailable')
    result=r.with_app_cleanup(lambda:{'status':'passed'},cleanup,tmp_path/'teardown.json')
    assert result['status']=='passed'
    assert result['teardown']['ok'] is False


def test_android_cleanup_refuses_unsettled_runner(monkeypatch):
    monkeypatch.setitem(r._UI_DELIVERIES,'job',{'stopped':False})
    monkeypatch.setattr(r,'resolve_adb_with_devices',lambda **kw: pytest.fail('must not touch phone'))
    with pytest.raises(RuntimeError,match='停止尚未确认'):
        r.close_android_test_app('job','phone','com.kfb.model')


def test_ios_closes_target_before_usb_forward_is_released(tmp_path,monkeypatch):
    from contextlib import contextmanager
    calls=[]
    @contextmanager
    def forward(device):
        calls.append('forward-open');yield 12345;calls.append('forward-close')
    monkeypatch.setattr(r,'RUNNER_PLATFORM','ios')
    monkeypatch.setattr(r,'IOS_EXECUTION_ENABLED',True)
    monkeypatch.setattr(r,'WORKSPACE',tmp_path)
    monkeypatch.setattr(r,'initialize_ui_delivery',lambda job:None)
    monkeypatch.setattr(r,'ios_execution_readiness',lambda *a,**kw:{'ready':True})
    monkeypatch.setattr(r,'ios_usb_forward',forward)
    monkeypatch.setattr(r,'ios_wda_request',lambda *a:{'ready':True})
    monkeypatch.setattr(r,'reset_ios_foreground_app',lambda port,bundle,**kw:calls.append(bundle))
    monkeypatch.setattr(r,'run_standard_job',lambda job:(r._APP_STOP_PROOF.update({'ios-cleanup':True}),calls.append('execute'),{'status':'passed'})[-1])
    result=r.run_job({'job_id':'ios-cleanup','target_runner_id':r.RUNNER_ID,'device_id':'phone',
                     'yaml_content':'android: {}\ntasks:\n- name: test\n  flow:\n  - launch: com.kfb.model\n'})
    assert result['status']=='passed' and result['teardown']['ok']
    assert calls==['forward-open','com.kfb.3d','execute','com.kfb.3d','forward-close']

@pytest.mark.parametrize('outcome',[False,'exception'])
def test_non_ui_stop_failure_never_allows_cleanup(monkeypatch,outcome):
    def stop(proc):
        if outcome=='exception':raise OSError('cannot stop')
        return outcome
    monkeypatch.setattr(r,'stop_midscene_process',stop)
    r.record_execution_stopped('legacy',object())
    monkeypatch.setattr(r,'resolve_adb_with_devices',lambda **kw:pytest.fail('must not clean'))
    with pytest.raises(RuntimeError,match='停止尚未确认'):
        r.close_android_test_app('legacy','phone','com.kfb.model')
