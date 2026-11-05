import json
import time
import subprocess
from subprocess import CompletedProcess
from unittest.mock import patch

import pytest

from task_server.services import week1_smoke as smoke


def proc(mode, code=None, expected=None, actual=None):
    if expected is None:
        expected = 4009 if mode == 'positive' else 999
    if actual is None:
        actual = 4009
    if code is None:
        code = 0 if mode == 'positive' else 1
    data = {'scenario': 'anonymous', 'http_status': 200, 'expected_code': expected,
            'actual_code': actual, 'data_is_list': False, 'passed': code == 0}
    return CompletedProcess([], code, json.dumps(data), '')


def test_success_history_and_owner_isolation(tmp_path):
    db = tmp_path / 'results.db'
    with patch.object(smoke, '_run_process', side_effect=lambda kind, mode, out: proc(mode)):
        record = smoke.run('owner-a', 'api', db)
    assert record['status'] == 'passed'
    assert [c['exit_code'] for c in record['checks']] == [0, 1]
    assert all(c['status'] == 'passed' for c in record['checks'])
    assert smoke.list_results('owner-a', db)[0]['id'] == record['id']
    assert smoke.list_results('owner-b', db) == []
    assert record['artifacts'] == 'unavailable'


def test_negative_process_error_or_unproven_failure_is_not_a_pass(tmp_path):
    with patch.object(smoke, '_run_process', side_effect=[proc('positive'), proc('negative', 2)]):
        result = smoke.run('user', 'api', tmp_path / 'one.db')
    assert result['status'] == 'blocked' and result['checks'][1]['status'] == 'blocked'
    assert result['checks'][1]['exit_code'] == 2
    with patch.object(smoke, '_run_process', side_effect=[proc('positive'), proc('negative', -9)]):
        killed = smoke.run('user', 'api', tmp_path / 'killed.db')
    assert killed['status'] == 'blocked' and killed['checks'][1]['exit_code'] == -9
    with patch.object(smoke, '_run_process', side_effect=[proc('positive'), proc('negative', 1, 999, 999)]):
        result = smoke.run('user', 'api', tmp_path / 'two.db')
    assert result['status'] != 'passed'


def test_process_exception_is_persisted_without_leaking(tmp_path):
    db = tmp_path / 'three.db'
    with patch.object(smoke, '_run_process', side_effect=RuntimeError('secret-token /private/path')):
        result = smoke.run('user', 'ui', db)
    assert result['status'] == 'blocked'
    assert 'secret-token' not in json.dumps(smoke.list_results('user', db))
    assert '/private/path' not in json.dumps(result)
    assert all(check['exit_code'] is None for check in result['checks'])


def test_input_and_global_concurrency_rejected(tmp_path):
    db = tmp_path / 'four.db'
    for owner, kind in [('', 'api'), ('user', 'other')]:
        with pytest.raises(ValueError):
            smoke.run(owner, kind, db)
    token = smoke._claim(db)
    try:
        with pytest.raises(smoke.SmokeBusy):
            smoke.run('other-user', 'api', db)
    finally:
        smoke._release(db, token)


def test_ui_pass_requires_two_real_output_assertions_and_artifacts(tmp_path):
    db = tmp_path / 'five.db'
    def execute(kind, mode, out):
        expected = '功夫豆测试平台' if mode == 'positive' else '故意设置不存在的标题'
        actual = '功夫豆测试平台'
        code = 0 if mode == 'positive' else 1
        actual_mode = 'positive' if mode == 'positive' else 'intentional-failure'
        folder = out / actual_mode
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'screenshot.png').write_bytes(b'png')
        (folder / 'trace.zip').write_bytes(b'zip')
        return CompletedProcess([], code, json.dumps({'mode': actual_mode, 'expected': expected, 'actual': actual, 'passed': code == 0}), '')
    with patch.object(smoke, '_run_process', side_effect=execute):
        result = smoke.run('user', 'ui', db)
    assert result['status'] == 'passed'
    assert result['artifacts'] == 'saved'
    assert str(tmp_path) not in json.dumps(result)


def test_environment_individual_results_are_persisted(tmp_path):
    db = tmp_path / 'environment.db'
    checks = [{'name': '千问端点', 'expected': '传输可达', 'actual': 'HTTP 404（不代表鉴权成功）', 'exit_code': 0, 'status': 'passed'},
              {'name': 'Python/pip', 'expected': '同一环境', 'actual': '未安装 pip', 'exit_code': 2, 'status': 'blocked'}]
    with patch.object(smoke, '_environment_checks', return_value=checks):
        result = smoke.run('owner', 'environment', db)
    assert result['status'] == 'blocked'
    assert result['checks'] == checks
    assert smoke.list_results('owner', db)[0]['kind'] == 'environment'


def test_ui_missing_artifacts_never_reports_complete(tmp_path):
    def execute(kind, mode, out):
        expected = '功夫豆测试平台' if mode == 'positive' else '故意设置不存在的标题'
        code = 0 if mode == 'positive' else 1
        actual_mode = 'positive' if mode == 'positive' else 'intentional-failure'
        return CompletedProcess([], code, json.dumps({'mode': actual_mode, 'expected': expected,
            'actual': '功夫豆测试平台', 'passed': code == 0}), '')
    with patch.object(smoke, '_run_process', side_effect=execute):
        result = smoke.run('user', 'ui', tmp_path / 'missing.db')
    assert result['status'] == 'blocked' and result['artifacts'] == 'unavailable'


def test_abandoned_run_is_reported_as_interrupted(tmp_path):
    db = tmp_path / 'abandoned.db'
    record = {'id':'a', 'kind':'ui', 'status':'running', 'created_at':'earlier',
              'checks':[], 'artifacts':'unavailable', 'message':'running'}
    smoke._save(db, 'user', record, time.time() - smoke._LEASE_SECONDS - 1)
    assert smoke.list_results('user', db)[0]['status'] == 'blocked'
    assert '中断' in smoke.list_results('user', db)[0]['message']


def test_environment_checks_do_not_invent_process_exit_codes(monkeypatch):
    class Response:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self, size): return b'{"ok":true}'
    class Opener:
        def open(self, request, timeout): return Response()
    monkeypatch.setattr(smoke.urllib.request, 'build_opener', lambda *args: Opener())
    monkeypatch.setattr(smoke, '_configured_path', lambda *args, **kwargs: '/node')
    monkeypatch.setattr(smoke.shutil, 'which', lambda name, path: '/usr/bin/' + name)
    def run(args, **kwargs):
        if args[0].endswith('git'): text = 'git version 2.39.0\n'
        elif args[0].endswith('npm'): text = '10.8.2\n'
        else: text = 'v20.1.2\n'
        return CompletedProcess([], 0, text, '')
    monkeypatch.setattr(smoke.subprocess, 'run', run)
    checks = smoke._environment_checks()
    for check in checks:
        assert check['exit_code'] == (0 if check['name'] in ('Node.js', 'npm', 'Git') else None)


def test_artifacts_are_owner_scoped_enum_only_and_symlink_safe(tmp_path):
    db = tmp_path / 'results.db'
    rid = 'a' * 32
    record = {'id':rid, 'kind':'ui', 'status':'passed', 'checks':[],
              'created_at':'now', 'artifacts':'saved', 'message':''}
    smoke._save(db, 'alice', record, time.time())
    folder = tmp_path / 'week1-smoke-artifacts' / rid / 'positive'
    folder.mkdir(parents=True)
    (folder / 'screenshot.png').write_bytes(b'png')
    assert smoke.get_artifact('alice', rid, 'positive-screenshot', db) == (b'png', 'image/png')
    with pytest.raises(ValueError):
        smoke.get_artifact('bob', rid, 'positive-screenshot', db)
    for invalid_id, item in [(rid, '../screenshot.png'), ('../a', 'positive-screenshot'), (rid, 'other')]:
        with pytest.raises(ValueError):
            smoke.get_artifact('alice', invalid_id, item, db)
    (folder / 'screenshot.png').unlink()
    (folder / 'screenshot.png').symlink_to(tmp_path / 'secret')
    (tmp_path / 'secret').write_bytes(b'secret')
    with pytest.raises(ValueError):
        smoke.get_artifact('alice', rid, 'positive-screenshot', db)


def test_artifact_state_matches_download_limits_and_symlink_rules(tmp_path):
    out = tmp_path / ('b' * 32)
    for mode in ('positive', 'intentional-failure'):
        folder = out / mode
        folder.mkdir(parents=True)
        (folder / 'trace.zip').write_bytes(b'zip')
        (folder / 'screenshot.png').write_bytes(b'png')
    assert smoke._artifact_state(out) == 'saved'
    with (out / 'positive' / 'screenshot.png').open('wb') as large:
        large.truncate(8 * 1024 * 1024 + 1)
    assert smoke._artifact_state(out) == 'partial'
    (out / 'positive' / 'screenshot.png').unlink()
    (out / 'positive' / 'screenshot.png').symlink_to(out / 'intentional-failure' / 'screenshot.png')
    assert smoke._artifact_state(out) == 'partial'


def test_process_timeout_reaps_with_bounded_wait_even_when_group_is_gone(monkeypatch, tmp_path):
    class Pipe:
        closed = False
        def close(self): self.closed = True
    class Child:
        pid = 12345
        stdout = Pipe()
        stderr = Pipe()
        def __init__(self): self.calls = []
        def communicate(self, timeout):
            self.calls.append(timeout)
            raise subprocess.TimeoutExpired('node', timeout)
        def wait(self, timeout): self.calls.append(('wait', timeout)); return 0
    child = Child()
    monkeypatch.setattr(smoke.subprocess, 'Popen', lambda *args, **kwargs: child)
    monkeypatch.setattr(smoke.os, 'killpg', lambda *args: (_ for _ in ()).throw(ProcessLookupError()))
    with pytest.raises(TimeoutError):
        smoke._run_process('api', 'positive', tmp_path)
    assert child.calls == [45, 2, ('wait', 1)]
    assert child.stdout.closed and child.stderr.closed
