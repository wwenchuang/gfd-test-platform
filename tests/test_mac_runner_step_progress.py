import importlib.util
import json
import sys
from pathlib import Path


spec = importlib.util.spec_from_file_location('mac_progress_runner', Path(__file__).parents[1] / 'mac-midscene-runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_failed_cli_keeps_final_step_state_and_does_not_log_protocol_frames(tmp_path, monkeypatch):
    snapshots = [
        {'version': 1, 'tasks': [{'name': 'navigation', 'status': status,
          'current_step': 1, 'total_steps': 3,
          'steps': [{'label': 'launch'}, {'label': 'assert'}, {'label': 'tap'}]}]}
        for status in ('running', 'failed')
    ]
    output = '\n'.join('MIDSCENE_PLATFORM_PROGRESS ' + json.dumps(s) for s in snapshots)
    command = [sys.executable, '-c', 'import sys; print(' + repr(output) + '); print("assertion failed"); sys.exit(1)']
    sent = []
    monkeypatch.setattr(runner, 'RUNNER_PLATFORM', 'ios')
    monkeypatch.setattr(runner, 'resolve_command', lambda *args: sys.executable)
    monkeypatch.setattr(runner, 'execution_command', lambda *args: command)
    monkeypatch.setattr(runner, 'post_job_progress', lambda job, body: sent.append(body))
    result = runner.execute_midscene('job', tmp_path, tmp_path / 'case.yaml', ['navigation'], 'iphone')
    assert result['status'] == 'failed'
    assert sent[-1]['execution_progress'] == snapshots[-1]
    assert 'MIDSCENE_PLATFORM_PROGRESS' not in result['stdout']
    assert 'assertion failed' in result['stdout']


def test_observer_is_scoped_to_cli_and_preserves_original_node_options(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'midscene_env', lambda _: {'NODE_OPTIONS': '--trace-warnings'})
    env = runner.progress_observer_env(tmp_path, 'iphone')
    assert env['NODE_OPTIONS'].startswith('--trace-warnings --require ')
    assert (tmp_path / 'runner-progress-observer.cjs').is_file()
