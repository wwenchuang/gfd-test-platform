import importlib.util
import json
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest

from task_server.services import ui_schedule_clock as clock

spec = importlib.util.spec_from_file_location('ui_daily_dispatch', Path(__file__).parents[1] / 'deploy/ui-schedules/dispatch.py')
dispatch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dispatch)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(clock, 'CONFIG_DIR', tmp_path)
    monkeypatch.setattr(clock, 'LEGACY_STATE', {p: tmp_path / p for p in ('ios', 'android')})
    monkeypatch.setattr(dispatch, 'latest_selection', lambda doc: doc)
    (tmp_path / 'claims').mkdir()
    (tmp_path / 'enabled').touch()
    for path in clock.LEGACY_STATE.values():
        path.mkdir()
    return tmp_path


def config(platform, time_string):
    return {'platform': platform, 'target': {'platform': platform, 'runner_id': 'runner', 'device_id': 'device'},
            'time': time_string, 'version': 'v1', 'effective_at': '2026-11-24T10:00:00+08:00',
            'cases': [{'case_id': 'case', 'revision': 'r1'}]}


def local(value):
    return datetime.fromisoformat(value).replace(tzinfo=clock.LOCAL)


def test_actual_worker_receives_saved_snapshot_once(env, monkeypatch):
    due = local('2026-11-25T12:55')
    monkeypatch.setattr(dispatch, 'datetime', type('Fixed', (datetime,), {'now': staticmethod(lambda tz: due)}))
    cfg = config('android', '12:55')
    (env / 'android.json').write_text(json.dumps(cfg))
    calls = []

    def execute(command, **kwargs):
        snapshot = json.loads(Path(kwargs['env']['MIDSCENE_UI_SCHEDULE_SNAPSHOT']).read_text())
        calls.append((command[-1], snapshot))
        return Mock(returncode=0)

    monkeypatch.setattr(dispatch.subprocess, 'run', execute)
    assert dispatch.run('android') == dispatch.run('android') == 0
    assert calls == [('--run', dict(cfg, scheduled_date='2026-11-25'))]
    assert clock.next_run_at('android', '14:00', due).startswith('2026-11-26T14:00')


def test_original_android_slot_and_same_day_legacy_run(env):
    cfg = config('android', '05:00')
    now = local('2026-11-25T05:02')
    assert dispatch.due_snapshot('android', cfg, now) == (None, None)
    (clock.LEGACY_STATE['android'] / '2026-11-25.json').write_text('{}')
    assert dispatch.due_snapshot('android', cfg, now-timedelta(minutes=1)) == (None, None)


def test_ios_catchup_and_no_retroactive_edit(env):
    cfg = config('ios', '22:00')
    now = local('2026-11-26T01:00')
    path, frozen = dispatch.due_snapshot('ios', cfg, now)
    assert frozen['scheduled_date'] == '2026-11-25'
    edited = dict(cfg, effective_at=now.isoformat(), time='23:00', version='v2')
    assert dispatch.due_snapshot('ios', edited, now) == (None, None)
    dispatch.write_atomic(path, frozen)
    assert dispatch.due_snapshot('ios', edited, now)[1] == frozen
    assert dispatch.due_snapshot('ios', edited, now+timedelta(hours=4)) == (None, None)


def test_manual_run_over_due_minute_preserves_daily_slot(env, monkeypatch):
    cfg = config('android', '05:00')
    (env / 'android.json').write_text(json.dumps(cfg))
    run_id = 'manual-' + 'a' * 32
    directory = clock.manual_dir('android')
    directory.mkdir(parents=True)
    (directory / (run_id + '.queued')).write_text(json.dumps(dict(cfg, manual_run_id=run_id)))
    tick = [local('2026-11-25T04:59')]
    monkeypatch.setattr(dispatch, 'datetime', type('Fixed', (datetime,), {'now': staticmethod(lambda tz: tick[0])}))
    snapshots = []

    class Manual:
        def __init__(self, command, env):
            snapshots.append(json.loads(Path(env['MIDSCENE_UI_SCHEDULE_SNAPSHOT']).read_text()))
        def wait(self, timeout):
            if tick[0].minute == 59:
                tick[0] = local('2026-11-25T05:00')
                raise dispatch.subprocess.TimeoutExpired('worker', timeout)
            tick[0] = local('2026-11-25T06:00')
            record = clock.LEGACY_STATE['android'] / (run_id + '.json')
            record.write_text(json.dumps({'final': True, 'manual_run_id': run_id}))
            return 0

    monkeypatch.setattr(dispatch.subprocess, 'Popen', Manual)
    assert dispatch.run('android') == 0
    assert snapshots[0]['started_at'] == '2026-11-25T04:59:00+08:00'
    assert (directory / (run_id + '.finished')).exists()
    daily_path, frozen = dispatch.due_snapshot('android', cfg, tick[0])
    assert daily_path.is_file() and 'manual_run_id' not in frozen
    assert frozen['scheduled_date'] == '2026-11-25'


def test_timed_run_precedes_already_queued_manual(env, monkeypatch):
    now = local('2026-11-25T05:00')
    monkeypatch.setattr(dispatch, 'datetime', type('Fixed', (datetime,), {'now': staticmethod(lambda tz: now)}))
    cfg = config('android', '05:00')
    (env / 'android.json').write_text(json.dumps(cfg))
    directory = clock.manual_dir('android')
    directory.mkdir(parents=True)
    queued = directory / ('manual-' + 'b' * 32 + '.queued')
    queued.write_text(json.dumps(dict(cfg, manual_run_id=queued.stem)))
    worker = Mock(return_value=Mock(returncode=0))
    monkeypatch.setattr(dispatch.subprocess, 'run', worker)
    assert dispatch.run('android') == 0
    snapshot = Path(worker.call_args.kwargs['env']['MIDSCENE_UI_SCHEDULE_SNAPSHOT'])
    assert snapshot == clock.claim_path('android', now.date())
    assert queued.exists() and snapshot.with_suffix('.done').exists()


def test_daily_freezes_latest_revision_only_once(env, monkeypatch):
    now = local('2026-11-27T05:00')
    cfg = config('android', '05:00')
    (env/'android.json').write_text(json.dumps(cfg))
    def resolve(snapshot):
        return dict(snapshot, cases=[dict(row,revision='published') for row in snapshot['cases']])
    latest = Mock(side_effect=resolve)
    monkeypatch.setattr(dispatch, 'latest_selection', latest)
    path, first = dispatch.claim_due('android', now)
    assert first['cases'][0]['revision'] == 'published'
    assert dispatch.claim_due('android', now) == (path, first)
    latest.assert_called_once()
