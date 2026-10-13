"""UI dispatch reads only occupancy and selected IDs, with coherent storage generations."""
import json
import os
from unittest.mock import patch

import pytest

from test_ui_batch_dispatch import dispatch_env, create
from test_ui_execution_service import environment, ACTORS
from task_server import storage
from task_server.services import job_service, runner_service, ui_batch_dispatch as dispatch
from task_server.services.operation_attribution import actor_context
from task_server.services.ui_execution_store import UiExecutionStore


def history(count=2000):
    return [{'job_id': f'old-{i}', 'status': 'success', 'created_at': '2026-10-13 00:00:00',
             'device_id': 'iphone'} for i in range(count)]


def view(keys=()):
    # This view is only authoritative while the ordinary Job lock is held.
    with job_service.JOB_LOCK:
        return job_service.load_ui_dispatch_jobs(keys)


@pytest.fixture
def jobs_path(tmp_path, monkeypatch):
    path = tmp_path / 'jobs.json'
    monkeypatch.setattr(job_service, 'JOBS_FILE', str(path))
    monkeypatch.setattr(runner_service, 'JOBS_FILE', str(path))
    storage.invalidate_json_cache()
    monkeypatch.setattr(job_service, '_UI_DISPATCH_INDEX', None)
    return path


def test_fifty_queued_batches_do_not_normalize_or_sort_completed_history(dispatch_env):
    job_service.save_jobs(history() + [{'job_id': 'legacy', 'status': 'running', 'device_id': 'iphone'}])
    batches = [create(key=f'queued-{i}') for i in range(50)]
    with patch.object(job_service, 'normalize_job_record', wraps=job_service.normalize_job_record) as normalize, \
         patch.object(job_service, '_parse_time', wraps=job_service._parse_time) as parse, \
         patch.object(storage, 'read_json_file', wraps=storage.read_json_file) as read:
        for batch in batches:
            result = dispatch.dispatch_ui_batch(batch['ui_batch_id'])
            assert result['case_attempts'][0]['block_code'] == 'device_busy'
        assert parse.call_count == 0, 'dispatch sorted unrelated completed history'
        assert normalize.call_count == 0, 'dispatch normalized unrelated completed history'
        assert read.call_count == 1, 'unchanged history was parsed again for each batch'


def test_exact_ids_and_running_aliases_have_bounded_normalization(jobs_path):
    rows = history() + [None, 'dirty', {'jobId': 'legacy', 'state': 'started', 'deviceId': ' iphone '},
                       {'job_id': 'selected', 'status': 'queued', 'events': [{'value': 1}]}]
    job_service.save_jobs(rows)
    with patch.object(job_service, 'normalize_job_record', wraps=job_service.normalize_job_record) as normalize, \
         patch.object(job_service, '_parse_time', wraps=job_service._parse_time) as parse, \
         patch.object(job_service, 'normalize_job_status', wraps=job_service.normalize_job_status) as status:
        for _ in range(50):
            selected, running = view(['selected', 'missing'])
            assert set(selected) == {'selected'}
            assert selected['selected']['status'] == 'pending'
            assert running == {'iphone': {'legacy'}}
        assert normalize.call_count == 50
        assert parse.call_count == 0
        # One generation scan plus one selected record per query, never 50 history scans.
        assert status.call_count == 2002 + 50


@pytest.mark.parametrize('writer', ['job', 'runner'])
def test_both_existing_writers_refresh_status_and_exact_keys(jobs_path, writer):
    job_service.save_jobs([{'job_id': 'one', 'status': 'running', 'device_id': 'iphone'}])
    assert view(['one'])[1] == {'iphone': {'one'}}
    write = job_service.save_jobs if writer == 'job' else runner_service._save_jobs
    with job_service.JOB_LOCK:
        write([{'job_id': 'one', 'status': 'success', 'device_id': 'iphone'},
               {'job_id': 'two', 'status': 'running', 'device_id': 'phone2'}])
    selected, running = view(['one', 'two'])
    assert selected['one']['status'] == 'success'
    assert running == {'phone2': {'two'}}


def test_view_cannot_mutate_cache_records_or_occupancy(jobs_path):
    job_service.save_jobs([{'job_id': 'one', 'status': 'running', 'device_id': 'iphone',
                           'events': [{'value': 1}], 'failure_review': {'reason': ['original']}}])
    selected, running = view(['one'])
    selected['one']['events'][0]['value'] = 99
    selected['one']['failure_review']['reason'].append('changed')
    running['iphone'].clear()
    later, active = view(['one'])
    assert later['one']['events'] == [{'value': 1}]
    assert later['one']['failure_review'] == {'reason': ['original']}
    assert active == {'iphone': {'one'}}
    assert job_service.get_job('one')['events'] == [{'value': 1}]


def test_path_switch_deletion_recreation_and_restart_empty_data(jobs_path, tmp_path, monkeypatch):
    job_service.save_jobs([{'job_id': 'one', 'status': 'running', 'device_id': 'iphone'}])
    assert view(['one'])[0]
    other = tmp_path / 'other.json'
    other.write_text('[]')
    monkeypatch.setattr(job_service, 'JOBS_FILE', str(other))
    assert view(['one']) == ({}, {})
    monkeypatch.setattr(job_service, 'JOBS_FILE', str(jobs_path))
    assert view(['one'])[0]
    jobs_path.unlink()
    assert view(['one']) == ({}, {})
    jobs_path.write_text(json.dumps([{'job_id': 'two', 'status': 'running', 'device_id': 'phone2'}]))
    assert view(['one', 'two'])[1] == {'phone2': {'two'}}
    storage.invalidate_json_cache()
    monkeypatch.setattr(job_service, '_UI_DISPATCH_INDEX', None)
    assert view(['two'])[0]['two']['status'] == 'running'
    job_service.save_jobs([])
    assert view(['two']) == ({}, {})


def test_same_signature_external_edit_refreshes_at_existing_ttl(jobs_path, monkeypatch):
    # Same byte length/inode/mtime: preserve the existing 3-second external-edit TTL.
    jobs_path.write_text('[{"job_id":"one","status":"running","device_id":"iphone"}]')
    initial = jobs_path.stat()
    with patch.object(storage.time, 'time', return_value=100):
        assert view()[1] == {'iphone': {'one'}}
    jobs_path.write_text('[{"job_id":"two","status":"running","device_id":"iphone"}]')
    os.utime(jobs_path, ns=(initial.st_atime_ns, initial.st_mtime_ns))
    with patch.object(storage.time, 'time', return_value=104):
        assert view()[1] == {'iphone': {'two'}}


def test_atomic_external_write_during_cache_read_cannot_bless_old_source(jobs_path, monkeypatch):
    job_service.save_jobs([{'job_id': 'one', 'status': 'success'}])
    view()
    original = job_service.read_json_cached
    changed = [False]
    def replace_after_read(*args, **kwargs):
        result = original(*args, **kwargs)
        if not changed[0]:
            changed[0] = True
            replacement = jobs_path.with_suffix('.new')
            replacement.write_text('[{"job_id":"two","status":"running","device_id":"iphone"}]')
            replacement.replace(jobs_path)
        return result
    monkeypatch.setattr(job_service, 'read_json_cached', replace_after_read)
    assert view(['one', 'two'])[1] == {'iphone': {'two'}}
    assert set(view(['one', 'two'])[0]) == {'two'}


def test_repeated_storage_changes_fail_closed_and_next_dispatch_can_retry(dispatch_env, monkeypatch):
    batch = create()
    original = job_service.read_json_cached
    def replace_each_read(*args, **kwargs):
        result = original(*args, **kwargs)
        runner_service._save_jobs(history(2))
        return result
    with monkeypatch.context() as temporary:
        temporary.setattr(job_service, 'read_json_cached', replace_each_read)
        with pytest.raises(RuntimeError, match='jobs_storage_changed'):
            dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    assert not UiExecutionStore().device_reserved('iphone')
    assert UiExecutionStore().get_batch(batch['ui_batch_id'])['status'] == 'queued'
    result = dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    assert result['case_attempts'][0]['job_id']


def test_status_write_during_target_check_is_seen_before_reservation(dispatch_env, monkeypatch):
    batch = create()
    job_service.save_jobs([{'job_id': 'legacy', 'status': 'pending', 'device_id': 'iphone'}])
    original = dispatch._target_check
    def claim_before_reservation(*args, **kwargs):
        code = original(*args, **kwargs)
        job_service.update_job('legacy', {'status': 'running'})
        return code
    monkeypatch.setattr(dispatch, '_target_check', claim_before_reservation)
    result = dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    assert result['case_attempts'][0]['block_code'] == 'device_busy'
    assert not UiExecutionStore().device_reserved('iphone')


def test_reservation_blocks_legacy_claim_in_reserve_create_window(dispatch_env, monkeypatch):
    legacy = job_service.create_job({'job_id': 'legacy', 'target_runner_id': 'mac', 'device_id': 'iphone'})
    batch = create()
    original = job_service.create_job
    def try_legacy_claim_before_create(payload):
        # Run the ordinary claim core while dispatch has released JOB_LOCK.
        assert UiExecutionStore().device_reserved('iphone')
        assert runner_service._assign_job('mac', ['iphone'], {}, UiExecutionStore(), dispatch_env[1]) is None
        return original(payload)
    monkeypatch.setattr(job_service, 'create_job', try_legacy_claim_before_create)
    result = dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    assert result['case_attempts'][0]['job_id']
    assert job_service.get_job(legacy['job_id'])['status'] == 'pending'


def test_job_lock_covers_refreshed_view_and_authoritative_reservation(dispatch_env, monkeypatch):
    batch = create()
    read, reserve = job_service.load_ui_dispatch_jobs, UiExecutionStore.reserve_attempt
    checked = []
    def checked_read(keys):
        assert job_service.JOB_LOCK.locked()
        assert dispatch.DISPATCH_LOCK._is_owned()
        checked.append('read')
        return read(keys)
    def checked_reserve(self, attempt_id):
        assert job_service.JOB_LOCK.locked()
        assert dispatch.DISPATCH_LOCK._is_owned()
        checked.append('reserve')
        return reserve(self, attempt_id)
    monkeypatch.setattr(job_service, 'load_ui_dispatch_jobs', checked_read)
    monkeypatch.setattr(UiExecutionStore, 'reserve_attempt', checked_reserve)
    assert dispatch.dispatch_ui_batch(batch['ui_batch_id'])['case_attempts'][0]['job_id']
    assert checked == ['read', 'reserve']


def test_duplicate_creation_after_lookup_relinks_exact_job(dispatch_env, monkeypatch):
    batch = create()
    original = job_service.create_job
    def competing_create(payload):
        original(payload)
        raise ValueError('job_id already exists')
    monkeypatch.setattr(job_service, 'create_job', competing_create)
    monkeypatch.setattr(job_service, 'get_job', lambda *_: pytest.fail('duplicate recovery scanned raw history'))
    result = dispatch.dispatch_ui_batch(batch['ui_batch_id'])
    assert result['case_attempts'][0]['job_id'] == batch['case_attempts'][0]['dispatch_key']
    assert len(job_service.load_jobs(limit=None)) == 1


def test_between_batches_terminal_result_refreshes_running_phone_view(dispatch_env):
    first, second = create(), create('b')
    dispatch.dispatch_ui_batch(first['ui_batch_id'])
    claimed = runner_service.assign_job('mac', ['iphone'])
    blocked = dispatch.dispatch_ui_batch(second['ui_batch_id'])
    assert blocked['case_attempts'][0]['block_code'] == 'device_busy'
    dispatch.record_ui_attempt_event(claimed['job_id'], 1, {'type': 'result', 'status': 'passed',
        'runner_id': 'mac', 'device_id': 'iphone', 'ui_claim_token': claimed['_ui_delivery']['ui_claim_token'],
        'execution_stopped': True})
    ready = dispatch.dispatch_ui_batch(second['ui_batch_id'])
    assert ready['case_attempts'][0]['job_id']


def test_cancel_late_result_does_not_release_new_reserved_lease(dispatch_env):
    first, second = create(), create('b')
    dispatch.dispatch_ui_batch(first['ui_batch_id'])
    claimed = runner_service.assign_job('mac', ['iphone'])
    with actor_context(ACTORS['a']):
        dispatch.cancel_ui_batch(ACTORS['a'], first['ui_batch_id'])
    event = {'type': 'result', 'status': 'passed', 'runner_id': 'mac', 'device_id': 'iphone',
             'ui_claim_token': claimed['_ui_delivery']['ui_claim_token'], 'execution_stopped': True}
    dispatch.record_ui_attempt_event(claimed['job_id'], 1, event)
    assert dispatch.dispatch_ui_batch(second['ui_batch_id'])['case_attempts'][0]['job_id']
    dispatch.record_ui_attempt_event(claimed['job_id'], 2, event)
    assert UiExecutionStore().device_reserved('iphone')
    assert job_service.get_job(claimed['job_id'])['status'] == 'cancelled'
