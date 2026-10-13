"""Win32 calls are mocked; real guard tests run locally, not Windows QA."""
import ctypes
import importlib.util
import io
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest


@pytest.fixture
def windows():
    path = Path(__file__).parents[1] / 'windows-midscene-runner.py'
    spec = importlib.util.spec_from_file_location('win_boundary', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def kernel(active=(1, 0), fail=''):
    api = Mock()
    api.CreateJobObjectW.return_value = 123
    api.SetInformationJobObject.return_value = fail != 'set'
    api.AssignProcessToJobObject.return_value = fail != 'assign'
    api.TerminateJobObject.return_value = fail != 'terminate'
    api.CloseHandle.return_value = fail != 'close'
    values = iter(active)
    def query(handle, info_class, info, size, returned):
        info._obj.ActiveProcesses = next(values, 0)
        return fail != 'query'
    api.QueryInformationJobObject.side_effect = query
    if fail == 'create': api.CreateJobObjectW.return_value = 0
    return api


def test_win32_abi_and_handle_is_private(windows):
    api = kernel()
    boundary = windows.WindowsUiJob(api)
    assert ctypes.sizeof(windows.JOB_BASIC_ACCOUNTING) == 48
    assert windows.JOB_BASIC_ACCOUNTING.ActiveProcesses.offset == 40
    assert windows.JOB_BASIC_LIMIT.LimitFlags.offset == 16
    assert ctypes.sizeof(windows.JOB_EXTENDED_LIMIT) == (144 if ctypes.sizeof(ctypes.c_void_p) == 8 else 112)
    api.CreateJobObjectW.assert_called_once_with(None, None)
    args = api.SetInformationJobObject.call_args.args
    assert args[1] == 9
    assert args[2]._obj.BasicLimitInformation.LimitFlags == 0x2000
    assert api.CreateJobObjectW.restype == ctypes.c_void_p
    assert api.AssignProcessToJobObject.argtypes == [ctypes.c_void_p, ctypes.c_void_p]
    boundary.assign(Mock(_handle=456))
    api.AssignProcessToJobObject.assert_called_once_with(123, 456)
    boundary.close()
    boundary.close()
    api.CloseHandle.assert_called_once_with(123)


def test_exited_parent_still_terminates_and_queries_descendants(windows):
    api = kernel(active=(2, 1, 0))
    proc = Mock(returncode=0)
    proc.poll.return_value = 0
    boundary = windows.WindowsUiJob(api)
    assert boundary.stop(proc) is True
    api.TerminateJobObject.assert_called_once_with(123, 1)
    assert api.QueryInformationJobObject.call_count == 3
    api.CloseHandle.assert_called_once_with(123)
    assert boundary.stop(proc) is True


@pytest.mark.parametrize('failure', ['terminate', 'query', 'close'])
def test_cleanup_error_is_not_stop_proof(windows, failure):
    api = kernel(fail=failure)
    boundary = windows.WindowsUiJob(api)
    assert boundary.stop(Mock()) is False
    assert boundary.stop(Mock()) is False
    assert api.CloseHandle.called  # last-handle fallback is attempted, never proof


def test_live_job_after_bounded_termination_is_not_proof(windows):
    api = kernel(active=(1, 1))
    assert windows.WindowsUiJob(api).stop(Mock(), timeout=0) is False


@pytest.mark.parametrize('failure', ['create', 'set', 'assign', 'popen', 'gate'])
def test_boundary_failure_does_not_release_cli_gate(windows, monkeypatch, failure, tmp_path):
    api = kernel(fail=failure)
    original = windows.WindowsUiJob
    monkeypatch.setattr(windows, 'WindowsUiJob', lambda: original(api))
    proc = Mock(_handle=456, stdin=Mock())
    if failure == 'gate': proc.stdin.write.side_effect = OSError('gate closed')
    popen = Mock(return_value=proc)
    if failure == 'popen': popen.side_effect = OSError('start failed')
    monkeypatch.setattr(windows.subprocess, 'Popen', popen)
    with pytest.raises(OSError):
        windows.start_ui_process(['cli', 'yaml'], cwd=str(tmp_path), env={})
    if failure in {'create', 'set'}: popen.assert_not_called()
    else:
        assert popen.call_args.kwargs['close_fds'] is True
        assert popen.call_args.args[0][1:4] == ['-I','-S','-c']
    if failure == 'assign': proc.stdin.write.assert_not_called()
    if failure not in {'create', 'set', 'popen'}: proc.kill.assert_called_once()
    if failure != 'create': assert api.CloseHandle.called

def install_local_job_emulation(windows, monkeypatch, on_assign=None):
    """Exercise real guard/stop code using a private POSIX group as a fake Win32 job."""
    original_class, original_popen = windows.WindowsUiJob, subprocess.Popen
    owned = {}
    def factory():
        api = kernel()
        def assign(handle, process_handle):
            if on_assign: on_assign()
            owned[handle] = process_handle
            return 1
        def query(handle, info_class, info, size, returned):
            try: os.killpg(owned[handle], 0)
            except ProcessLookupError: count = 0
            except PermissionError: count = 1  # macOS may transiently expose an unreaped zombie group
            else: count = 1
            info._obj.ActiveProcesses = count
            return 1
        def terminate(handle, code):
            try: os.killpg(owned[handle], signal.SIGKILL)
            except ProcessLookupError: pass
            return 1
        api.AssignProcessToJobObject.side_effect = assign
        api.QueryInformationJobObject.side_effect = query
        api.TerminateJobObject.side_effect = terminate
        return original_class(api)
    def popen(*args, **kwargs):
        proc = original_popen(*args, start_new_session=True, **kwargs)
        proc._handle = proc.pid  # Test emulation only; production uses a retained Win32 handle.
        return proc
    monkeypatch.setattr(windows, 'WindowsUiJob', factory)
    monkeypatch.setattr(windows.subprocess, 'Popen', popen)


@pytest.mark.parametrize('parent_exits_first', [False, True])
@pytest.mark.skipif(os.name == 'nt', reason='Local POSIX Job emulation; real Windows QA is a separate acceptance')
def test_real_guard_tree_including_term_ignoring_descendant(windows, monkeypatch, tmp_path, parent_exits_first):
    ready = tmp_path/'child-ready'
    install_local_job_emulation(windows, monkeypatch, on_assign=lambda: assert_not_exists(ready))
    child = ('import os,pathlib,signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);'
             f'pathlib.Path({str(ready)!r}).write_text(str(os.getpid()));time.sleep(60)')
    parent = (f'import subprocess,sys,time,pathlib;subprocess.Popen([sys.executable,"-c",{child!r}]);'
              f'p=pathlib.Path({str(ready)!r});\nwhile not p.exists():time.sleep(.01)\n' +
              ('sys.exit(0)' if parent_exits_first else 'time.sleep(60)'))
    proc = windows.start_ui_process([sys.executable,'-c',parent], cwd=str(tmp_path), text=True,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic()+5
        while not ready.exists() and time.monotonic()<deadline: time.sleep(.01)
        assert ready.exists()
        if parent_exits_first: assert proc.wait(timeout=3) == 0
        os.kill(int(ready.read_text()), signal.SIGTERM)
        os.kill(int(ready.read_text()), 0)  # child ignored the graceful signal
        assert windows.stop_ui_process(proc) is True
        with pytest.raises(ProcessLookupError): os.killpg(proc.pid,0)
    finally:
        try: os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        proc.wait(timeout=3)


def assert_not_exists(path):
    assert not path.exists(), 'CLI started before Job Object assignment'


def test_real_guard_waits_for_gate_and_preserves_arguments_output_and_code(windows, tmp_path):
    marker = tmp_path/'started'
    command = [sys.executable, '-c',
               'import pathlib,sys;pathlib.Path(sys.argv[1]).write_text(sys.argv[2]);'
               'print("stdout",flush=True);print("stderr",file=sys.stderr,flush=True);sys.exit(7)',
               str(marker), '中文 spaces & % $ literal']
    proc = subprocess.Popen([sys.executable, '-c', windows.UI_PROCESS_GUARD, windows.json.dumps(command)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        time.sleep(.1)
        assert not marker.exists()
        output, _ = proc.communicate('start\n', timeout=5)
        assert proc.returncode == 7
        assert marker.read_text() == command[-1]
        assert 'stdout' in output and 'stderr' in output
    finally:
        if proc.poll() is None: proc.kill()
        proc.wait(timeout=3)


def test_real_guard_eof_never_starts_cli(windows, tmp_path):
    marker = tmp_path/'must-not-start'
    command = [sys.executable, '-c', f'open({str(marker)!r},"w").close()']
    proc = subprocess.Popen([sys.executable, '-c', windows.UI_PROCESS_GUARD, windows.json.dumps(command)],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    proc.communicate('', timeout=5)
    assert proc.returncode != 0
    assert not marker.exists()


def test_guard_preserves_full_windows_dword_return_code(windows, monkeypatch):
    class GuardExit(BaseException): pass
    api = Mock()
    def exit_process(code):
        assert code == 0xC0000005  # Access violation exit status does not fit signed C long.
        raise GuardExit()
    api.ExitProcess.side_effect = exit_process
    monkeypatch.setattr(ctypes, 'WinDLL', lambda *_a,**_kw:api, raising=False)
    monkeypatch.setattr(sys, 'platform', 'win32')
    monkeypatch.setattr(sys, 'stdin', io.StringIO('start\n'))
    monkeypatch.setattr(sys, 'argv', ['guard', '["cli"]'])
    monkeypatch.setattr(subprocess, 'Popen', Mock(return_value=Mock(wait=Mock(return_value=0xC0000005))))
    with pytest.raises(GuardExit): exec(windows.UI_PROCESS_GUARD, {})
    assert api.ExitProcess.argtypes == [ctypes.c_uint32]
    subprocess.Popen.assert_called_once_with(['cli'], stdin=subprocess.DEVNULL, close_fds=True)


def test_missing_owned_boundary_cannot_prove_stop(windows):
    proc = Mock(returncode=0)
    proc._ui_job = None
    proc.poll.return_value = 0
    assert windows.stop_ui_process(proc) is False


@pytest.mark.parametrize('mode', ['success', 'timeout', 'timeout_zero', 'cancel', 'cancel_cleanup_failure', 'cleanup_failure', 'cleanup_exception', 'start_failure'])
def test_execute_callback_preserves_proof_and_real_return_code(windows, monkeypatch, tmp_path, mode):
    job = {'job_id':'uib_test_1_1','ui_batch_id':'uib_test','ui_claim_token':'secret','ui_attempt_events':1,
           'runner_id':windows.RUNNER_ID,'target_runner_id':windows.RUNNER_ID,'device_id':'selected'}
    windows.initialize_ui_delivery(job)
    frame = 'MIDSCENE_PLATFORM_PROGRESS {"version":1,"tasks":[{"name":"t","status":"passed","steps":[]}]}\n'
    proc = Mock(returncode=0, stdout=io.StringIO('original output\n' + frame))
    proc.poll.return_value = None if mode in {'timeout', 'timeout_zero', 'cancel', 'cancel_cleanup_failure'} else 0
    def stop():
        if mode == 'cleanup_exception': raise OSError('cleanup denied')
        proc.returncode = 0 if mode in {'success','cleanup_failure','timeout_zero'} else 9
        proc.poll.return_value = proc.returncode
        return mode not in {'cleanup_failure','cancel_cleanup_failure'}
    proc._ui_job.stop.side_effect = lambda _: stop()
    start = Mock(return_value=proc)
    if mode == 'start_failure': start.side_effect = OSError('assignment failed')
    monkeypatch.setattr(windows, 'start_ui_process', start)
    monkeypatch.setattr(windows, 'resolve_command', lambda *_:'cli')
    monkeypatch.setattr(windows, 'progress_observer_env', lambda *_:{})
    calls = []
    def progress(*_):
        calls.append(1)
        return {'ignored':'terminal_job'} if mode in {'cancel','cancel_cleanup_failure'} and len(calls) > 1 else {'ok':True}
    monkeypatch.setattr(windows, 'post_job_progress', progress)
    monkeypatch.setattr(windows, 'TIMEOUT_SECONDS', -1 if mode in {'timeout','timeout_zero'} else 300)
    result = windows.execute_midscene(job['job_id'],tmp_path,tmp_path/'t.yaml',['t'],'selected')
    sent = []
    monkeypatch.setattr(windows, 'http_json_retry', lambda method,url,body,**kw:sent.append(body.copy()) or {'ok':True})
    windows.post_job_result(job['job_id'], result)
    assert sent[0]['execution_stopped'] is (mode not in {'cleanup_failure','cleanup_exception','cancel_cleanup_failure','start_failure'})
    assert result['status'] == {'success':'passed','cancel':'cancelled','cancel_cleanup_failure':'cancelled'}.get(mode,'failed')
    if mode == 'cancel_cleanup_failure': assert '已停止本机执行进程' not in result['stderr']
    if mode in {'success','cleanup_failure','cleanup_exception','timeout_zero'}: assert result['returncode'] == 0
    if mode != 'start_failure': assert 'original output' in result['stdout']
    assert frame not in result['stdout']
