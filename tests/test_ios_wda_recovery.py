import importlib.util
from pathlib import Path
from unittest.mock import Mock
import pytest

spec = importlib.util.spec_from_file_location('wda_recovery_runner', Path('mac-midscene-runner.py'))
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
BAD = {'ready': False, 'reason': 'WDA连接中断', 'code': 'wda_unreachable'}
GOOD = {'ready': True, 'reason': '', 'code': 'ready'}

@pytest.fixture
def supervisor(monkeypatch, tmp_path):
    monkeypatch.setattr(r, 'IOS_WDA_AUTORECOVER', True)
    monkeypatch.setattr(r, 'IOS_WDA_DEVICE_IDS', {'phone'})
    monkeypatch.setattr(r, 'WORKSPACE', tmp_path)
    monkeypatch.setattr(r, 'ios_connected_device_ids', lambda: ['phone'])
    monkeypatch.setattr(r, 'ios_wda_launch_blocker', lambda d: '')
    monkeypatch.setattr(r, 'ios_wda_other_process', lambda d: False)
    r._IOS_JOB_ACTIVE.clear()
    manager = r.IOSWDARecovery()
    manager.launch = Mock(return_value=None)
    manager.schedule = lambda device, now: manager.launch(device, now)
    return manager

def test_no_launch_for_healthy_unconfigured_or_runner_fault(supervisor):
    for at in (0, 30, 60):
        assert supervisor.tick('phone', GOOD, at)['ready']
        supervisor.tick('other', BAD, at)
        supervisor.tick('phone', {**BAD, 'code':'midscene_missing'}, at)
    supervisor.launch.assert_not_called()

def test_two_independent_failure_samples_and_retry_throttle(supervisor):
    supervisor.tick('phone', BAD, 0)
    supervisor.tick('phone', BAD, 3)
    supervisor.launch.assert_not_called()
    result = supervisor.tick('phone', BAD, 30)
    supervisor.launch.assert_called_once_with('phone', 30)
    assert not result['ready']
    supervisor.tick('phone', BAD, 33)
    assert supervisor.launch.call_count == 1

def test_busy_job_never_starts_or_stops_wda(supervisor):
    r._IOS_JOB_ACTIVE.set()
    for at in (0, 30, 60): supervisor.tick('phone', BAD, at)
    supervisor.launch.assert_not_called()

def test_started_process_is_allowed_initialization_time(supervisor):
    state=supervisor.state('phone')
    state.update(process=Mock(poll=lambda: None), started=0, attempts=1)
    result=supervisor.tick('phone', BAD, 60)
    assert '正在恢复' in result['reason']
    supervisor.launch.assert_not_called()

def test_start_timeout_reaps_only_owned_process_and_backs_off(supervisor):
    state=supervisor.state('phone')
    state.update(process=Mock(poll=lambda: None), started=0, attempts=1)
    supervisor.stop=Mock()
    result=supervisor.tick('phone', BAD, 91)
    supervisor.stop.assert_called_once_with(state)
    assert '重试' in result['reason']
    assert state['next_retry'] > 91
    supervisor.launch.assert_not_called()

def test_repeated_failure_has_cooldown_and_stable_success_resets(supervisor):
    state=supervisor.state('phone');state['attempts']=3
    supervisor.failed(state, 100, '启动失败')
    assert state['next_retry'] == 400
    assert '冷却' in state['reason']
    supervisor.tick('phone',GOOD,110)
    assert state['attempts']==3
    supervisor.tick('phone',GOOD,171)
    assert state['attempts']==0

def test_disconnect_does_not_touch_foreign_process_and_resets_samples(supervisor):
    state=supervisor.state('phone');state['failures']=2
    supervisor.stop=Mock()
    supervisor.prune([])
    supervisor.stop.assert_not_called()
    assert 'phone' not in supervisor.states

@pytest.mark.parametrize('blocker',['手机已锁定，请解锁并保持亮屏','无法确认手机解锁状态'])
def test_launch_blocker_never_spawns(supervisor, monkeypatch, blocker):
    monkeypatch.setattr(r,'ios_wda_launch_blocker',lambda d:blocker)
    popen=Mock();monkeypatch.setattr(r.subprocess,'Popen',popen)
    r.IOSWDARecovery.launch(supervisor,'phone',0)
    popen.assert_not_called()
    assert blocker in supervisor.state('phone')['reason']

def test_existing_foreign_wda_never_duplicates(supervisor, monkeypatch):
    monkeypatch.setattr(r,'ios_wda_other_process',lambda d:True)
    popen=Mock();monkeypatch.setattr(r.subprocess,'Popen',popen)
    r.IOSWDARecovery.launch(supervisor,'phone',0)
    popen.assert_not_called()
    assert '现有' in supervisor.state('phone')['reason']

def test_file_lock_prevents_two_supervisors(supervisor, monkeypatch):
    import fcntl
    directory=r.WORKSPACE/'wda-recovery';directory.mkdir()
    lock=(directory/'phone.lock').open('w');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    popen=Mock();monkeypatch.setattr(r.subprocess,'Popen',popen)
    try: r.IOSWDARecovery.launch(supervisor,'phone',0)
    finally: lock.close()
    popen.assert_not_called()

def test_start_uses_exact_device_without_build_or_resigning(supervisor,monkeypatch,tmp_path):
    project=tmp_path/'WDA.xcodeproj';project.mkdir()
    monkeypatch.setattr(r,'IOS_WDA_PROJECT',str(project))
    proc=Mock(pid=123,poll=lambda:None)
    popen=Mock(return_value=proc);monkeypatch.setattr(r.subprocess,'Popen',popen)
    r.IOSWDARecovery.launch(supervisor,'phone',0)
    args=popen.call_args.args[0]
    assert 'id=phone' in args and 'test-without-building' in args
    assert '-allowProvisioningUpdates' not in args
    assert popen.call_args.kwargs['start_new_session'] is True
    supervisor.state('phone')['log'].close();supervisor.state('phone')['lock'].close()

def test_launch_rechecks_busy_after_precheck(supervisor, monkeypatch):
    def blocker(d): r._IOS_JOB_ACTIVE.set();return ''
    monkeypatch.setattr(r,'ios_wda_launch_blocker',blocker)
    popen=Mock();monkeypatch.setattr(r.subprocess,'Popen',popen)
    r.IOSWDARecovery.launch(supervisor,'phone',0)
    popen.assert_not_called()

def test_dead_owned_process_overrides_stale_ready_cache(supervisor):
    state=supervisor.state('phone');state.update(process=Mock(poll=lambda:65),attempts=1,was_ready=True)
    supervisor.stop=Mock()
    result=supervisor.tick('phone',GOOD,60)
    assert not result['ready']
    supervisor.stop.assert_called_once()


def test_schedule_is_background_and_does_not_wait_for_precheck(supervisor):
    import threading
    started=threading.Event();release=threading.Event()
    def launch(*args): started.set();release.wait(2)
    supervisor.launch=launch
    try:
        r.IOSWDARecovery.schedule(supervisor,'phone',0)
        assert started.wait(1)
        assert supervisor.state('phone')['launching']
    finally: release.set()


def test_external_process_match_is_exact_device(monkeypatch):
    monkeypatch.setattr(r.subprocess,'check_output',lambda *a,**k: '/Xcode/xcodebuild /Xcode/xcodebuild -scheme WebDriverAgentRunner -destination id=phone2 test\n/bin/zsh rg xcodebuild -scheme WebDriverAgentRunner -destination id=phone test')
    assert not r.ios_wda_other_process('phone')
    assert r.ios_wda_other_process('phone2')

@pytest.mark.parametrize('args', [
    '-scheme WebDriverAgentRunner -destination platform=iOS,id=phone test',
    '-xctestrun /tmp/OtherName.xctestrun -destination id=phone test-without-building',
    '-scheme OtherUITests -destination id=phone,platform=iOS test',
])
def test_other_xcode_device_sessions_are_not_stolen(monkeypatch,args):
    monkeypatch.setattr(r.subprocess,'check_output',lambda *a,**k: '/Xcode/xcodebuild /Xcode/xcodebuild '+args)
    assert r.ios_wda_other_process('phone')
    assert not r.ios_wda_other_process('phone2')

def test_brief_success_then_hang_still_counts_towards_cooldown(supervisor):
    state=supervisor.state('phone')
    state.update(process=Mock(poll=lambda:None),attempts=3,was_ready=True)
    supervisor.stop=Mock()
    supervisor.tick('phone',BAD,0)
    supervisor.tick('phone',BAD,30)
    supervisor.launch.assert_not_called()
    assert state['next_retry'] == 330
    assert '冷却' in state['reason']

def test_shutdown_blocks_late_worker_and_releases_owned_resources(supervisor,monkeypatch):
    state=supervisor.state('phone');state['process']=Mock(poll=lambda:None)
    supervisor.stop=Mock()
    supervisor.shutdown()
    supervisor.stop.assert_called_once_with(state)
    r.IOSWDARecovery.schedule(supervisor,'phone',0)
    supervisor.launch.assert_not_called()
    popen=Mock();monkeypatch.setattr(r.subprocess,'Popen',popen)
    r.IOSWDARecovery.launch(supervisor,'phone',0)
    popen.assert_not_called()

def test_stop_reaps_owned_group_even_if_leader_has_exited(supervisor,monkeypatch):
    state=supervisor.state('phone')
    process=Mock(pid=123,poll=lambda:65);state.update(process=process,log=Mock(),lock=Mock())
    def signal_group(pid,sig):
        if sig==0: raise ProcessLookupError
    kill=Mock(side_effect=signal_group);monkeypatch.setattr(r.os,'killpg',kill)
    supervisor.stop(state)
    kill.assert_any_call(123,r.signal.SIGTERM)
    assert state['process'] is None and state['lock'] is None and state['log'] is None

def test_main_cleans_up_on_interrupt(monkeypatch):
    monkeypatch.setattr(r,'RUNNER_PLATFORM','ios')
    monkeypatch.setattr(r,'_run_main',Mock(side_effect=KeyboardInterrupt))
    manager=Mock();monkeypatch.setattr(r,'_IOS_WDA_RECOVERY',manager)
    r.main()
    manager.shutdown.assert_called_once()

def test_stop_kills_remaining_group_after_leader_exits(supervisor,monkeypatch):
    state=supervisor.state('phone');state['process']=Mock(pid=123,poll=lambda:65)
    clock=iter([0,1,3]);monkeypatch.setattr(r.time,'monotonic',lambda:next(clock))
    monkeypatch.setattr(r.time,'sleep',lambda s:None)
    kill=Mock();monkeypatch.setattr(r.os,'killpg',kill)
    supervisor.stop(state)
    kill.assert_any_call(123,r.signal.SIGKILL)

def test_mac_ps_uses_full_executable_args(monkeypatch):
    check=Mock(return_value='64430 /Applications/Xcode.app/Contents/Developer/usr/bin/xcodebuild -destination id=phone test')
    monkeypatch.setattr(r.subprocess,'check_output',check)
    assert r.ios_wda_other_process('phone')
    assert 'pid=,args=' in check.call_args.args[0]
