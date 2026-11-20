"""Deterministic simulation of the production timer, with no device or DB calls."""
import importlib.util
import json
from pathlib import Path

import pytest


ENTRY = Path(__file__).resolve().parents[1] / 'deploy/3d-daily/run.py'


@pytest.fixture
def daily():
    assert ENTRY.is_file(), 'daily UI baseline scheduler is missing'
    spec = importlib.util.spec_from_file_location('daily_baselines', ENTRY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def cases(d):
    names = [d.DRAGON, *['case-%s.yaml' % i for i in range(12)], *reversed(d.SMOKE)]
    return [{'case_id': str(i), 'module': '3D打印基线', 'filename': name,
             'sha256': 'a' * 64} for i, name in enumerate(names)]


def test_plan_keeps_exact_cases_and_dragon_last(daily):
    rows = cases(daily)
    phases = daily.plan(rows)
    assert [name for name, _ in phases] == ['smoke', 'remaining', 'dragon']
    assert [len(group) for _, group in phases] == [3, 12, 1]
    assert [r['filename'] for r in phases[0][1]] == list(daily.SMOKE)
    assert phases[-1][1] == rows[:1]
    with pytest.raises(ValueError):
        daily.plan(rows[:15] + [rows[0]])


@pytest.mark.parametrize('flags,allowed', [
    (['passed', 'failed', 'passed'], True),
    (['passed', 'failed', 'failed'], False),
    (['passed', 'passed', 'interrupted'], False),
    (['passed', 'passed', 'cancelled'], False),
])
def test_smoke_decision_uses_exact_original_attempts(daily, flags, allowed):
    rows = daily.plan(cases(daily))[0][1]
    attempts = [{'case_id': row['case_id'], 'case_revision': row['sha256'], 'status': flag,
                 'attempt_no': 1} for row, flag in zip(rows, flags)]
    assert daily.stage_complete(rows, attempts, smoke=True) is allowed
    assert not daily.stage_complete(rows, attempts + [dict(attempts[0], attempt_no=2)], smoke=True)


@pytest.mark.parametrize('rows,idle', [
    ([{'machine':'9888E0094F2A', 'state':'StandingBy', 'age_s':1, 'pending_command':0}], True),
    ([{'machine':'9888E0094F2A', 'state':'Printing', 'age_s':1, 'pending_command':0}], False),
    ([{'machine':'9888E0094F2A', 'state':'StandingBy', 'age_s':61, 'pending_command':0}], False),
    ([{'machine':'9888E0094F2A', 'state':'StandingBy', 'age_s':1, 'pending_command':1}], False),
    ([{'machine':'another', 'state':'StandingBy', 'age_s':1, 'pending_command':0}], False),
    ([], False),
])
def test_only_fresh_production_status_admits_dragon(daily, rows, idle):
    trace = []
    def query(sql, env):
        trace.append((sql, env))
        return rows
    assert daily.printer_idle(query) is idle
    assert trace[0][1] == 'prod'
    assert 'START TRANSACTION READ ONLY' in trace[0][0]
    assert "'9888E0094F2A'" in trace[0][0]


def test_cancel_unclaimed_never_interrupts_running_or_other_batches(daily, tmp_path, monkeypatch):
    from task_server.services.ui_execution_store import UiExecutionStore
    from tests.test_ui_execution_store import prepared
    monkeypatch.setenv('TASK_UI_EXECUTION_DB', str(tmp_path / 'ui.sqlite'))
    store = UiExecutionStore()
    batch = store.create_batch(prepared())
    attempt = batch['case_attempts'][0]
    assert store.reserve_attempt(attempt['attempt_id'])
    store.link_job(attempt['dispatch_key'], attempt['dispatch_key'])
    assert store.claim_attempt(attempt['dispatch_key'])
    other = store.create_batch({**prepared(), 'idempotency_key':'other'})
    daily.discard_unclaimed(batch['ui_batch_id'])
    assert store.get_batch(batch['ui_batch_id'])['case_attempts'][0]['status'] == 'running'
    assert store.get_batch(other['ui_batch_id'])['case_attempts'][0]['status'] == 'queued'
    daily.discard_unclaimed(other['ui_batch_id'])
    assert store.get_batch(other['ui_batch_id'])['case_attempts'][0]['status'] == 'cancelled'


@pytest.mark.parametrize(('passed', 'idle', 'submissions', 'final'), [
    (1, True, [3], 'stopped_smoke'),
    (2, False, [3, 12], 'skipped_printer_busy_or_unknown'),
    (3, True, [3, 12, 1], 'passed'),
])
def test_daily_phases_submit_once(daily, monkeypatch, tmp_path, passed, idle, submissions, final):
    from task_server.services import ui_execution_service, ui_execution_store
    batches, submitted = {}, []
    groups = daily.plan(cases(daily))
    target = {'platform':'android', 'device_id':'ecbfd645', 'runner_id':'runner-1'}
    class FakeStore:
        def find_idempotent(self, owner_id, key):
            return batches.get(key)
    def create(actor, request, key):
        assert request['notifications'] == {'enabled': False}
        assert request['release_report'] == {'enabled': True}
        assert request['targets'] == [target]
        assert key not in batches
        batch = {'ui_batch_id':'batch-' + str(len(batches))}
        submitted.append(request['cases'])
        batches[key] = batch
        return batch
    def watch(batch_id, deadline, dragon=False):
        rows = submitted[int(batch_id.rsplit('-', 1)[-1])]
        return [{'case_id': r['case_id'], 'case_revision':r['revision'], 'attempt_no':1,
                 'status':'failed' if len(rows) == 3 and i >= passed else 'passed'} for i, r in enumerate(rows)]
    monkeypatch.setattr(daily, 'phone_target', lambda: target)
    monkeypatch.setattr(daily, 'watch', watch)
    monkeypatch.setattr(daily, 'printer_idle', lambda: idle)
    monkeypatch.setattr(daily, 'print_receipt', lambda *_: {'print_sn':'321654'})
    monkeypatch.setattr(ui_execution_store, 'UiExecutionStore', FakeStore)
    monkeypatch.setattr(ui_execution_service, 'create_ui_batch', create)
    monkeypatch.setattr(ui_execution_service, 'preflight_ui_batch', lambda *a: {'ready':True})
    record = {'date':'2026-11-21', 'owner_id':'user', 'batches':{}}
    for _ in range(2):
        result = daily.run_stages(record, tmp_path / 'day.json', groups, daily.now() + daily.timedelta(hours=1))
        assert result == final
    assert list(map(len, submitted)) == submissions


@pytest.mark.parametrize(('date', 'delay'), [('2026-11-20',1500), ('2026-11-21',0)])
def test_timer_records_before_weekday_delay_and_skips_reentry(daily, tmp_path, monkeypatch, date, delay):
    from task_server.services import ui_execution_contract, ui_execution_service
    monkeypatch.setattr(daily, 'STATE', tmp_path)
    manifest = tmp_path / 'baselines.json'
    manifest.write_text(json.dumps(cases(daily)))
    monkeypatch.setattr(daily, 'MANIFEST', manifest)
    monkeypatch.setattr(daily, 'trusted_actor', lambda: {'kind':'user','user_id':'u','username':'owner'})
    monkeypatch.setattr(ui_execution_contract, 'require_ui_actor', lambda **kw: None)
    monkeypatch.setattr(ui_execution_service, '_require_ui_admission', lambda: None)
    monkeypatch.setattr(daily, 'validate_published', lambda rows: None)
    clock = daily.datetime.fromisoformat(date + 'T08:30:00+08:00')
    monkeypatch.setattr(daily, 'now', lambda: clock)
    path = tmp_path / (date + '.json')
    slept, executed = [], []
    def pause(seconds):
        saved = json.loads(path.read_text())
        assert saved['status'] == 'scheduled' and saved['batches'] == {}
        slept.append(seconds)
    def stages(*args):
        executed.append(1)
        return 'skipped_phone_busy_or_unavailable_smoke'
    monkeypatch.setattr(daily.time, 'sleep', pause)
    monkeypatch.setattr(daily, 'run_stages', stages)
    monkeypatch.setattr('sys.argv', ['run.py','--run'])
    daily.main()
    daily.main()
    assert slept == ([delay] if delay else [])
    assert executed == [1]
    assert json.loads(path.read_text())['final']


@pytest.mark.parametrize(('sonic', 'jobs', 'expected'), [
    ('idle', [], True),
    ('busy', [], False),
    ('unknown', [], False),
    ('idle', [{'state':'dispatched','deviceId':'ecbfd645'}], False),
    ('idle', [{'state':'running','deviceId':'another'}], True),
])
def test_phone_checks_exact_runner_sonic_and_legacy_queue(daily, tmp_path, monkeypatch, sonic, jobs, expected):
    from task_server.services import job_service, sonic_service, ui_execution_catalog
    monkeypatch.setenv('TASK_UI_EXECUTION_DB', str(tmp_path / 'ui.sqlite'))
    target = {'platform': 'android', 'device_id':'ecbfd645','runner_id':'win'}
    devices = [{'target':target,'connection_status':'ready'}]
    monkeypatch.setattr(ui_execution_catalog, 'get_ui_devices', lambda *_: {'devices':devices})
    def occupancy(*, force):
        assert force
        return {'__source__':{'available':True}, 'ecbfd645':{'usage_status':sonic}}
    monkeypatch.setattr(sonic_service, 'sonic_list_device_statuses', occupancy)
    monkeypatch.setattr(job_service, '_read_jobs_raw', lambda **kw: jobs)
    assert daily.phone_target() == (target if expected else None)
    devices.append({'target':dict(target,runner_id='other'), 'connection_status':'ready'})
    assert daily.phone_target() is None


def test_dragon_receipt_requires_a_unique_new_device_task(daily):
    attempt = {'started_ns': 1795141800123000000, 'finished_ns': 1795142000777000000,
               'status':'passed','attempt_no':1}
    row = {'machine':'9888E0094F2A','print_sn':'321654','state':'printing'}
    sql = []
    def query(statement, env):
        sql.append((statement, env))
        return [row]
    assert daily.print_receipt(attempt, query) == row
    assert sql[0][1] == 'prod' and 'LIMIT 2' in sql[0][0]
    assert '1795141800' in sql[0][0] and '1795142000' in sql[0][0]
    assert daily.print_receipt(attempt, lambda *_: [row, row]) is None
    assert daily.print_receipt(attempt, lambda *_: [dict(row,machine='other')]) is None
    assert daily.print_receipt(dict(attempt, started_ns=None), query) is None
