"""Exercise the schedule workers with dated snapshots, without submitting device jobs."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / 'deploy/3d-daily/run.py'
IOS = ROOT / 'deploy/3d-ios-nightly/nightly.py'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def daily():
    return load('daily_test', ANDROID)


def test_selected_stages_preserve_print_and_admission(daily):
    rows = json.loads(daily.MANIFEST.read_text())
    chosen = [r for r in rows if r['filename'] in (*daily.MODEL_FUNCTIONS, daily.DRAGON)]
    groups = daily.plan(chosen, selected=True)
    assert [(name, len(group)) for name, group in groups] == [('smoke', 3), ('previews', 4), ('dragon', 1)]
    assert {r['case_id'] for _, group in groups for r in group} == {r['case_id'] for r in chosen}
    dragon = groups[-1][1]
    assert daily.plan(dragon, selected=True) == [('dragon', dragon)]


@pytest.mark.parametrize('platform', ['android', 'ios'])
@pytest.mark.parametrize('manual', [False, True])
def test_worker_dispatches_dated_snapshot_and_pinned_selection_once(daily, tmp_path, monkeypatch, platform, manual):
    from task_server.services import ui_execution_contract, ui_execution_service, ui_execution_store

    from task_server.services import ui_schedule_service
    monkeypatch.setattr(ui_schedule_service, 'schedule_device_busy', lambda phone: False)
    worker = daily if platform == 'android' else load('nightly_test', IOS)
    common = daily if platform == 'android' else worker.daily
    chosen = {'case_id': 'a', 'task_name': '收藏', 'filename': 'favorite.yaml',
              'module': '3D打印基线', 'revision': 'b' * 64}
    rows = [chosen, dict(chosen, case_id='b', task_name='详情', filename='detail.yaml')]
    if platform == 'ios':
        rows.append(dict(chosen, case_id='c', task_name='关节龙打印', filename='关节龙打印.yaml', deferred='暂缓'))
    target = {'platform': platform, 'runner_id': 'my-runner', 'device_id': 'my-phone'}
    snapshot = tmp_path / 'snapshot.json'
    manual_id = 'manual-' + 'a' * 32
    extra = dict(manual_run_id=manual_id, requested_by='tester', started_at='2026-11-25T23:58:21+08:00') if manual else {}
    snapshot.write_text(json.dumps(dict(platform=platform, target=target, version='selected-version', **extra,
                                        scheduled_date='2026-11-25', time='23:58', cases=rows)))
    monkeypatch.setenv('MIDSCENE_UI_SCHEDULE_SNAPSHOT', str(snapshot))
    monkeypatch.setattr(worker, 'STATE', tmp_path)
    clock = daily.datetime.fromisoformat('2026-11-26T00:01:00+08:00')
    monkeypatch.setattr(common, 'now', lambda: clock)
    monkeypatch.setattr(common, 'trusted_actor', lambda: {'kind': 'user', 'user_id': 'owner'})
    monkeypatch.setattr(common, 'daily_sender', lambda: lambda card: {'code': 0})
    monkeypatch.setattr(common, 'validate_published', lambda rows: None)
    services = ui_execution_service if platform == 'android' else worker
    auth = ui_execution_contract if platform == 'android' else worker
    monkeypatch.setattr(auth, 'require_ui_actor', lambda **kw: None)
    monkeypatch.setattr(ui_execution_service, '_require_ui_admission', lambda: None)
    monkeypatch.setattr(ui_execution_store.UiExecutionStore, '__init__', lambda self: None)
    monkeypatch.setattr(ui_execution_store.UiExecutionStore, 'find_idempotent', lambda *_: None)
    monkeypatch.setattr(common, 'phone_target', lambda expected: target if expected == target else None)
    submissions = []

    def create(actor, request, key):
        assert (manual_id if manual else '2026-11-25') in key
        submissions.append(request)
        return {'ui_batch_id': 'batch'}
    monkeypatch.setattr(services, 'create_ui_batch', create)
    monkeypatch.setattr(services, 'preflight_ui_batch', lambda *args: {'ready': True})
    monkeypatch.setattr(common, 'watch', lambda *args, **kwargs: [
        dict(case_id=r['case_id'], case_revision=r['revision'], status='passed', attempt_no=1)
        for r in submissions[-1]['cases']])
    monkeypatch.setattr(sys, 'argv', ['worker.py', '--run' if platform == 'android' else 'run'])
    worker.main()
    worker.main()
    assert len(submissions) == 1
    assert submissions[0]['targets'] == [target]
    assert submissions[0]['cases'] == [{'case_id': r['case_id'], 'revision': r['revision']} for r in rows[:2]]
    record = json.loads((tmp_path / ((manual_id if manual else '2026-11-25') + '.json')).read_text())
    assert record['final'] and record['schedule_version'] == 'selected-version'
    assert record['target'] == target
    assert record['operate_at'] == extra.get('started_at', '2026-11-25T23:58:00+08:00')
    if manual:
        assert not (tmp_path / '2026-11-25.json').exists()
    assert not (tmp_path / '2026-11-26.json').exists()


def test_exact_android_target_uses_selected_device_occupancy(daily, tmp_path, monkeypatch):
    from task_server.services import job_service, sonic_service, ui_execution_catalog
    monkeypatch.setenv('TASK_UI_EXECUTION_DB', str(tmp_path / 'ui.sqlite'))
    chosen = {'platform': 'android', 'runner_id': 'r2', 'device_id': 'another-phone'}
    rows = [dict(target=chosen, connection_status='ready')]
    monkeypatch.setattr(ui_execution_catalog, 'get_ui_devices', lambda *_: {'devices': rows})
    monkeypatch.setattr(sonic_service, 'sonic_list_device_statuses', lambda **_: {
        '__source__': {'available': True}, chosen['device_id']: {'usage_status': 'idle'}})
    jobs = []
    monkeypatch.setattr(job_service, '_read_jobs_raw', lambda **_: jobs)
    assert daily.phone_target(chosen) == chosen
    assert daily.phone_target(dict(chosen, runner_id='another-runner')) is None
    jobs.append(dict(deviceId='another-phone', state='running'))
    assert daily.phone_target(chosen) is None


def test_snapshot_resume_reuses_only_matching_batch(daily):
    from task_server.services import ui_execution_service
    target = {'platform': 'android', 'runner_id': 'r1', 'device_id': 'my-device'}
    row = {'case_id': 'one', 'sha256': 'a' * 64}
    request = daily.batch_request(target, [row])
    prior = {'ui_batch_id': 'prior', 'request_hash': ui_execution_service._request_hash(
        ui_execution_service._normalize_request(request))}
    record = {'target': target, 'schedule_digest': 'immutable'}
    assert daily.resume_batch(prior, record, [row]) == 'prior'
    for changed in [daily.batch_request(dict(target, runner_id='r2'), [row]),
                    daily.batch_request(target, [dict(row, sha256='b' * 64)])]:
        wrong_hash = ui_execution_service._request_hash(ui_execution_service._normalize_request(changed))
        with pytest.raises(ValueError, match='scheduled_batch_conflict'):
            daily.resume_batch(dict(prior, request_hash=wrong_hash), record, [row])
