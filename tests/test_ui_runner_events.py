"""Standalone runners bind callbacks to a delivery and acknowledge stopping."""
import importlib.util
from pathlib import Path
import pytest


@pytest.fixture(params=['mac','windows'])
def runner(request):
    path = Path(__file__).parents[1] / (request.param + '-midscene-runner.py')
    spec = importlib.util.spec_from_file_location('ui_events_' + request.param, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def job(runner):
    return {'job_id':'uib_test_1_1','ui_batch_id':'uib_test','ui_claim_token':'secret','ui_attempt_events':1,
            'runner_id':runner.RUNNER_ID,'target_runner_id':runner.RUNNER_ID,'device_id':'selected'}


def test_callback_sequence_is_per_claim_and_retries_preserve_payload(runner, monkeypatch):
    calls = []
    monkeypatch.setattr(runner,'http_json_retry',lambda method,url,body,**kw:calls.append(body.copy()) or {'ok':True})
    runner.initialize_ui_delivery(job(runner))
    runner.post_job_progress('uib_test_1_1',{'message':'first'})
    runner.post_job_progress('uib_test_1_1',{'message':'second'})
    result = {'status':'failed'}
    runner.post_job_result('uib_test_1_1',result)
    runner.post_job_result('uib_test_1_1',result)
    assert [c['ui_event_sequence'] for c in calls] == [1,2,3,3]
    assert all(c['ui_claim_token'] == 'secret' and c['device_id'] == 'selected' for c in calls)
    assert calls[-1]['execution_stopped'] is True  # no process was started


def test_cancel_ack_prevents_starting_cli(runner, monkeypatch, tmp_path):
    runner.initialize_ui_delivery(job(runner))
    monkeypatch.setattr(runner,'post_job_progress',lambda *a:{'ignored':'terminal_job'})
    monkeypatch.setattr(runner,'resolve_command',lambda *a:'midscene')
    monkeypatch.setattr(runner.subprocess,'Popen',lambda *a,**kw:pytest.fail('CLI started after cancel'))
    result = runner.execute_midscene('uib_test_1_1',tmp_path,tmp_path/'test.yaml',['task'],'selected')
    assert result['status'] == 'cancelled'


def test_missing_or_wrong_target_is_rejected_before_device_access(runner):
    for patch in ({'device_id':''},{'target_runner_id':'other'},{'ui_claim_token':''}):
        with pytest.raises(ValueError):
            runner.initialize_ui_delivery({**job(runner),**patch})


def test_result_with_unconfirmed_process_stop_keeps_false(runner, monkeypatch):
    runner.initialize_ui_delivery(job(runner))
    runner._UI_DELIVERIES['uib_test_1_1']['stopped'] = False
    calls = []
    monkeypatch.setattr(runner,'http_json_retry',lambda method,url,body,**kw:calls.append(body.copy()) or {'ok':True})
    runner.post_job_result('uib_test_1_1',{'status':'failed'})
    assert calls[0]['execution_stopped'] is False


def test_report_ready_carries_claim_sequence(runner,monkeypatch):
    calls = []
    runner.initialize_ui_delivery(job(runner))
    monkeypatch.setattr(runner,'http_json_retry',lambda method,url,body,**kw:calls.append(body.copy()) or {'ok':True})
    runner.post_job_report_ready('uib_test_1_1','/report/test.html')
    assert calls[0]['ui_claim_token'] == 'secret'
    assert calls[0]['ui_event_sequence'] == 1


def test_running_cli_is_actually_stopped_after_cancel(runner,monkeypatch,tmp_path):
    import sys
    runner.initialize_ui_delivery(job(runner))
    script = tmp_path/'fake_cli.py'
    script.write_text('import time\nprint("ready",flush=True)\ntime.sleep(30)\n')
    calls, processes = [], []
    real_popen = runner.subprocess.Popen
    def popen(*args,**kwargs):
        process = real_popen(*args,**kwargs)
        processes.append(process)
        return process
    def progress(*args):
        calls.append(args)
        return {'ok':True} if len(calls) == 1 else {'ignored':'terminal_job'}
    monkeypatch.setattr(runner.subprocess,'Popen',popen)
    monkeypatch.setattr(runner,'resolve_command',lambda *a:sys.executable)
    monkeypatch.setattr(runner,'post_job_progress',progress)
    if hasattr(runner,'execution_command'):
        monkeypatch.setattr(runner,'execution_command',lambda *a:[sys.executable,str(script)])
    result = runner.execute_midscene('uib_test_1_1',tmp_path,script,['task'],'selected')
    assert result['status'] == 'cancelled'
    assert processes[0].poll() is not None
    assert runner._UI_DELIVERIES['uib_test_1_1']['stopped'] is True


def test_mac_ui_android_stop_uses_owned_process_group(monkeypatch):
    import signal
    from unittest.mock import Mock
    path = Path(__file__).parents[1] / 'mac-midscene-runner.py'
    spec = importlib.util.spec_from_file_location('mac_ui_group',path)
    mac = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mac)
    monkeypatch.setattr(mac,'RUNNER_PLATFORM','android')
    proc = Mock(pid=99999)
    proc.poll.return_value = None
    proc._ui_process_group = True
    sent = []
    def killpg(pid,sig):
        if sig == 0: raise ProcessLookupError
        sent.append((pid,sig))
    monkeypatch.setattr(mac.os,'killpg',killpg)
    assert mac.stop_midscene_process(proc) is True
    assert sent == [(99999,signal.SIGTERM)]


@pytest.mark.parametrize('parent_exits_first', [False, True])
def test_mac_stop_confirms_entire_group_including_term_ignoring_child(tmp_path,parent_exits_first):
    import os
    import signal
    import subprocess
    import sys
    import time
    path = Path(__file__).parents[1] / 'mac-midscene-runner.py'
    spec = importlib.util.spec_from_file_location('mac_group_stop_proof',path)
    mac = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mac)
    ready = tmp_path/'child-ready'
    child_code = ('import os,signal,time,pathlib; signal.signal(signal.SIGTERM,signal.SIG_IGN); '
                  f'pathlib.Path({str(ready)!r}).write_text(str(os.getpid())); time.sleep(60)')
    parent_code = (f'import subprocess,sys,time,pathlib; subprocess.Popen([sys.executable,"-c",{child_code!r}]); '
                   f'p=pathlib.Path({str(ready)!r});\nwhile not p.exists(): time.sleep(.01)\n'
                   + ('sys.exit(0)\n' if parent_exits_first else 'time.sleep(60)\n'))
    parent = subprocess.Popen([sys.executable,'-c',parent_code],start_new_session=True)
    parent._ui_process_group = True
    try:
        deadline = time.monotonic()+3
        while not ready.exists() and time.monotonic()<deadline: time.sleep(.01)
        assert ready.exists()
        if parent_exits_first: parent.wait(timeout=3)
        assert mac.stop_midscene_process(parent) is True
        with pytest.raises(ProcessLookupError): os.killpg(parent.pid,0)
    finally:
        try: os.killpg(parent.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        parent.wait(timeout=3)
