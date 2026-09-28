import importlib.util
from pathlib import Path
from unittest import mock
import pytest

spec = importlib.util.spec_from_file_location('live_progress_runner', Path(__file__).parents[1] / 'windows-midscene-runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_progress_snapshot_is_bounded_and_whitelisted():
    from task_server.services.runner_progress import normalize_execution_progress
    raw = {'version': 1, 'secret': 'not for storage', 'tasks': [{'name': 'A', 'status': 'running', 'current_step': 0,
        'steps': [{'label': '等候', 'secret': 'no'}] * 999}] * 999}
    result = normalize_execution_progress(raw)
    assert result['truncated']
    assert len(result['tasks']) <= 100
    assert sum(len(t['steps']) for t in result['tasks']) <= 1000
    assert 'secret' not in str(result)
    assert result['tasks'][0]['current_step'] == 0


def test_invalid_progress_is_not_accepted():
    from task_server.services.runner_progress import normalize_execution_progress
    for raw in [None, [], {}, {'version': 9, 'tasks': []}]:
        assert normalize_execution_progress(raw) is None


def test_observer_frame_is_consumed_without_guessing_from_log_text():
    state = runner.RunnerStepProgress()
    assert not state.consume('aiTap: 我的 ✓')
    assert not state.consume('MIDSCENE_PLATFORM_PROGRESS {invalid')
    assert state.consume('MIDSCENE_PLATFORM_PROGRESS {"version":1,"tasks":[{"name":"A","status":"running","current_step":0,"steps":[{"label":"点击我的"}]}]}')
    assert state.snapshot['tasks'][0]['current_step'] == 0


def test_terminal_job_ignores_late_progress():
    from task_server import router
    job={'job_id':'j','status':'cancelled','progress':22,'current_task_index':0}
    handler=mock.Mock()
    handler._body.return_value={'progress':99,'current_task_index':3,'execution_progress':{'version':1,'tasks':[]}}
    with mock.patch.object(router,'_require_user_auth',return_value=False), mock.patch.object(router,'find_job',return_value=(job,[job])), mock.patch.object(router,'save_jobs') as save:
        router._handle_runner_job_progress(handler,'j')
    assert job['progress']==22
    assert job['current_task_index']==0
    save.assert_not_called()


def test_observer_preload_supports_chinese_and_spaces_in_workspace(tmp_path):
    import os
    import shutil
    import subprocess
    node = shutil.which('node')
    assert node, 'Node is required for the Runner preload path regression'
    directory = tmp_path / '测试任务 with spaces'
    directory.mkdir()
    with mock.patch.object(runner, 'midscene_env', return_value=dict(os.environ)):
        env = runner.progress_observer_env(directory, '')
    result = subprocess.run([node, '-e', 'console.log("original CLI still runs")'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'original CLI still runs' in result.stdout
