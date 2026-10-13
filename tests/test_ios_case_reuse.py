import copy
import importlib.util
from pathlib import Path

import pytest
import yaml


SPEC = importlib.util.spec_from_file_location("mac_runner", Path(__file__).resolve().parents[1] / "mac-midscene-runner.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

SOURCE = """android: {}
tasks:
- name: existing-case
  flow:
  - launch: com.kfb.model
  - aiWaitFor: 我的入口可见
    timeout: 12000
  - aiTap: 我的
  - aiTap: 打印记录
  - aiTap: 返回按钮
  - aiTap: 首页
"""


def convert(text=SOURCE):
    return runner.prepare_ios_yaml(text, source_app="com.kfb.model", bundle_id="com.kfb.3d", wda_port=18100)


def test_reuses_business_steps_without_modifying_source():
    original = yaml.safe_load(SOURCE)
    expected = copy.deepcopy(original["tasks"])
    expected[0]["flow"][0]["launch"] = "com.kfb.3d"
    result = yaml.safe_load(convert())
    assert result["tasks"] == expected
    assert result["ios"] == {"wdaHost": "127.0.0.1", "wdaPort": 18100}
    assert "android" not in result
    assert yaml.safe_load(SOURCE) == original


@pytest.mark.parametrize("step", ["adbShell: input keyevent 4", "runAdbShell: input keyevent 4", "aiTap:\n      x: 100\n      y: 200", "launch: com.other.app", "back: true"])
def test_platform_specific_or_unmapped_steps_fail_closed(step):
    with pytest.raises(ValueError):
        convert(SOURCE + "  - " + step + "\n")


def test_android_interface_options_are_not_silently_dropped():
    with pytest.raises(ValueError):
        convert(SOURCE.replace("android: {}", "android:\n  keyboardDismissStrategy: back"))


def test_rejects_missing_launch_mapping():
    with pytest.raises(ValueError):
        runner.prepare_ios_yaml(SOURCE, source_app="", bundle_id="", wda_port=18100)


def test_accepts_server_injected_android_device_id_only():
    assert "android" not in yaml.safe_load(convert(SOURCE.replace("android: {}", "android:\n  deviceId: original-phone")))


def test_native_ios_uses_local_wda_and_rejects_remote_target():
    with pytest.raises(ValueError):
        convert(SOURCE.replace("android: {}", "ios:\n  wdaHost: other-host"))


def test_job_must_target_exact_runner_and_device():
    assert runner.ios_job_matches({"target_runner_id": "mac-ios", "device_id": "phone"}, "mac-ios", "phone")
    for job in ({}, {"device_id": "phone"}, {"target_runner_id": "mac-ios", "device_id": "other"}):
        assert not runner.ios_job_matches(job, "mac-ios", "phone")


def test_ios_command_never_sends_android_device_override(monkeypatch):
    monkeypatch.setattr(runner, "RUNNER_PLATFORM", "ios")
    command = runner.execution_command("midscene", Path("test.yaml"), "iphone")
    assert "--android.deviceId" not in command
    assert "--android.deviceId" not in command


def test_android_command_unchanged(monkeypatch):
    monkeypatch.setattr(runner, "RUNNER_PLATFORM", "android")
    assert runner.execution_command("midscene", Path("test.yaml"), "android-phone") == ["midscene", "test.yaml", "--android.deviceId", "android-phone"]


def test_ios_runtime_has_no_android_serial(monkeypatch):
    monkeypatch.setenv("MIDSCENE_RUNNER_PLATFORM", "ios")
    monkeypatch.setenv("ANDROID_SERIAL", "wrong-phone")
    monkeypatch.setattr(runner, "task_runtime_env", lambda: {})
    monkeypatch.setattr(runner, "ensure_android_sdk_env", lambda _: pytest.fail("iOS invoked Android setup"))
    env = runner.midscene_env("iphone")
    assert "ANDROID_SERIAL" not in env


def test_dispatch_rejects_automatic_jobs_for_explicit_only_runner():
    from task_server.services.runner_service import runner_accepts_explicit_job
    info = {"capabilities": {"explicit_target_only": True}}
    assert not runner_accepts_explicit_job(info, {}, "mac-ios")
    assert not runner_accepts_explicit_job(info, {"device_id": "phone"}, "mac-ios")
    assert runner_accepts_explicit_job(info, {"target_runner_id": "mac-ios", "device_id": "phone"}, "mac-ios")
    assert runner_accepts_explicit_job({}, {}, "windows")


def test_real_server_dispatch_preserves_agent_options():
    from task_server.services.yaml_service import midscene_cli_dispatch_yaml_text
    dispatched = midscene_cli_dispatch_yaml_text(SOURCE, device_id="iphone")
    compiled = yaml.safe_load(convert(dispatched))
    assert compiled["agent"] == {"screenshotShrinkFactor": 2}
    assert compiled["tasks"][0]["flow"][0] == {"launch": "com.kfb.3d"}


def test_learning_app_reuses_verified_mapping():
    text = SOURCE.replace('com.kfb.model', 'com.xbxxhz.box')
    converted = runner.prepare_ios_yaml(text, source_app='com.xbxxhz.box', bundle_id='com.xbxxhz.box', wda_port=18100)
    assert yaml.safe_load(converted)['tasks'][0]['flow'][0] == {'launch':'com.xbxxhz.box'}


def test_ios_not_ready_does_not_execute(monkeypatch):
    monkeypatch.setattr(runner,'RUNNER_PLATFORM','ios')
    monkeypatch.setattr(runner,'IOS_EXECUTION_ENABLED',True)
    monkeypatch.setattr(runner,'ios_connected_device_ids',lambda:['phone'])
    monkeypatch.setattr(runner,'ios_execution_readiness',lambda device, **kw: {'ready':False,'reason':'WDA不可用'})
    monkeypatch.setattr(runner,'execute_midscene',lambda *a: pytest.fail('must not execute'))
    result=runner.run_job({'job_id':'j','target_runner_id':runner.RUNNER_ID,'device_id':'phone','yaml_content':SOURCE})
    assert result['status']=='failed'
    assert 'WDA' in result['stderr']


def test_ios_does_not_claim_unselected_job(monkeypatch):
    monkeypatch.setattr(runner,'RUNNER_PLATFORM','ios')
    monkeypatch.setattr(runner,'IOS_EXECUTION_ENABLED',True)
    result=runner.run_job({'job_id':'j','device_id':'phone','yaml_content':SOURCE})
    assert result['status']=='failed'
    assert '明确' in result['stderr']


def test_terminal_progress_cancels_ios_process_group(monkeypatch):
    from unittest.mock import Mock
    monkeypatch.setattr(runner,'RUNNER_PLATFORM','ios')
    monkeypatch.setattr(runner,'resolve_command',lambda *a:'midscene')
    monkeypatch.setattr(runner,'midscene_env',lambda *a:{})
    monkeypatch.setattr(runner,'post_job_progress',lambda *a:{'ignored':'terminal_job','job':{'status':'cancelled'}})
    monkeypatch.setattr(runner,'TIMEOUT_SECONDS',-1)
    proc=Mock(pid=123,returncode=None,stdout=None)
    proc.poll.return_value=None
    stopped=[]
    monkeypatch.setattr(runner.subprocess,'Popen',lambda *a,**kw:proc)
    monkeypatch.setattr(runner,'stop_midscene_process',lambda p:stopped.append(p),raising=False)
    result=runner.execute_midscene('j',Path('/tmp'),Path('a.yaml'),['case'],'phone')
    assert result['status']=='cancelled'
    assert stopped == [proc]


def test_dispatch_uses_only_ready_explicit_iphone(monkeypatch):
    from task_server.services import runner_service as service, sonic_service
    runner_info={'capabilities':{'ios_case_reuse':True,'explicit_target_only':True,'job_execution':True},'devices':[
        {'device_id':'ready','status':'online','platform':'ios','preflight_status':'ready'},
        {'device_id':'blocked','status':'online','platform':'ios','preflight_status':'blocked'}]}
    jobs=[{'job_id':'auto','status':'pending','device_strategy':'auto'},
          {'job_id':'blocked','status':'pending','device_id':'blocked','target_runner_id':'mac'},
          {'job_id':'chosen','status':'pending','device_id':'ready','target_runner_id':'mac'}]
    monkeypatch.setattr(service,'load_runners',lambda:{'mac':runner_info})
    monkeypatch.setattr(service,'save_runners',lambda _:None)
    monkeypatch.setattr(service,'_load_jobs',lambda:jobs)
    monkeypatch.setattr(service,'_save_jobs',lambda _:None)
    monkeypatch.setattr(sonic_service,'sonic_list_device_statuses',lambda:{'__source__':{'available':True},'ready':{'usage_status':'idle'},'blocked':{'usage_status':'idle'}})
    assert service.assign_job('mac')['job_id']=='chosen'
    assert jobs[0]['status']=='pending' and jobs[1]['status']=='pending'
