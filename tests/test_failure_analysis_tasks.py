"""Durable analysis boundaries; isolated identities and no external execution."""
import importlib.util
import json
import uuid
import time
import pytest

from test_identity import identity_db, create_member, activate
from task_server.services.operation_attribution import actor_context, actor_from_trusted_username


def test_task_service_exists():
    assert importlib.util.find_spec('task_server.services.failure_analysis_tasks'), 'durable failure analysis tasks are missing'


@pytest.fixture
def setup(identity_db, monkeypatch):
    from task_server.services import failure_analysis_tasks as tasks
    _, store = identity_db
    for name in ('reader', 'other'):
        create_member(store, username=name)
        activate(store, name)
    allowed = {'run-one', 'run-two'}
    class Source:
        def __init__(self, actor, source, run_id):
            self.actor, self.source, self.run_id = actor, source, run_id
        def authorize(self):
            tasks._profile(self.actor)
            if self.run_id not in allowed:
                raise PermissionError('scope revoked')
    class Dispatcher:
        def __init__(self): self.ids = []
        def submit(self, key): self.ids.append(key); return True
    dispatcher = Dispatcher()
    monkeypatch.setattr(tasks, '_source', lambda actor, source, run_id: Source(actor, source, run_id))
    monkeypatch.setattr(tasks, '_dispatcher', dispatcher)
    actor = actor_from_trusted_username('reader')
    with actor_context(actor):
        yield tasks, actor, store, allowed, dispatcher


def start(tasks, actor, **changes):
    return tasks.start(actor, dict(source='ui', run_id='run-one', request_id=str(uuid.uuid4()), **changes))


def test_start_is_idempotent_and_active_task_is_reused(setup):
    tasks, actor, _, _, dispatcher = setup
    body = dict(source='ui', run_id='run-one', request_id=str(uuid.uuid4()))
    task = tasks.start(actor, body)
    assert task['status'] == 'queued'
    assert tasks.start(actor, body)['id'] == task['id']
    assert start(tasks, actor)['id'] == task['id']
    assert dispatcher.ids == [task['id']]
    assert tasks.latest(actor, 'ui', 'run-one')['id'] == task['id']
    with pytest.raises(ValueError):
        tasks.start(actor, {**body, 'run_id': 'run-two'})


def test_coalesced_request_stays_idempotent_after_completion(setup, monkeypatch):
    tasks, actor, _, _, dispatcher = setup
    task = start(tasks, actor)
    retry = dict(source='ui', run_id='run-one', request_id=str(uuid.uuid4()))
    assert tasks.start(actor, retry)['id'] == task['id']
    monkeypatch.setattr(tasks, '_investigate', lambda *args, **kwargs: {'status': 'completed'})
    tasks._run(task['id'], {})
    assert tasks.start(actor, retry)['id'] == task['id']
    assert len(dispatcher.ids) == 1


def test_owner_scope_and_revocation_are_rechecked(setup):
    tasks, actor, store, allowed, _ = setup
    task = start(tasks, actor)
    outsider = actor_from_trusted_username('other')
    with actor_context(outsider), pytest.raises(LookupError):
        tasks.read(outsider, task['id'])
    allowed.clear()
    with pytest.raises(PermissionError): tasks.read(actor, task['id'])
    allowed.add('run-one')
    store.update_user('admin', 'reader', {'status': 'disabled'})
    with pytest.raises(PermissionError): tasks.read(actor, task['id'])


def test_worker_preserves_evidence_and_records_actual_result(setup, monkeypatch):
    tasks, actor, _, _, _ = setup
    task = start(tasks, actor)
    partial = dict(trace=[{'tool': 'get_failed_steps', 'status': 'completed'}], evidence=[{'id': 'S1'}], rounds=1)
    def investigate(source, *, progress, cancelled):
        assert source.run_id == 'run-one'
        progress(partial)
        assert tasks.read(actor, task['id'])['evidence'] == [{'id': 'S1'}]
        return dict(partial, status='completed', result={'summary': '有证据的结论'}, usage={'total_tokens': 123})
    monkeypatch.setattr(tasks, '_investigate', investigate)
    tasks._run(task['id'], {})
    final = tasks.read(actor, task['id'])
    assert final['status'] == 'completed'
    assert final['usage']['total_tokens'] == 123
    assert final['result']['summary'] == '有证据的结论'
    assert 'actor' not in json.dumps(final)


def test_cancellation_keeps_evidence_and_late_result_cannot_overwrite(setup, monkeypatch):
    tasks, actor, _, _, _ = setup
    task = start(tasks, actor)
    def investigate(source, *, progress, cancelled):
        progress({'evidence': [{'id': 'S1'}], 'rounds': 1})
        tasks.cancel(actor, task['id'])
        assert cancelled()
        return {'status': 'completed', 'result': {'summary': 'late'}}
    monkeypatch.setattr(tasks, '_investigate', investigate)
    tasks._run(task['id'], {})
    final = tasks.read(actor, task['id'])
    assert final['status'] == 'cancelled' and final['evidence'] == [{'id': 'S1'}]
    assert not final.get('result')


def test_restart_marks_interrupted_without_losing_evidence(setup):
    tasks, actor, _, _, _ = setup
    task = start(tasks, actor)
    with tasks._db() as db:
        db.execute("UPDATE tasks SET status='running', boot='previous-boot', payload=? WHERE id=?", (json.dumps({'evidence': [{'id': 'S1'}]}), task['id']))
    final = tasks.read(actor, task['id'])
    assert final['status'] == 'interrupted'
    assert final['evidence'] == [{'id': 'S1'}]


def test_busy_queue_does_not_create_infinite_pending_task(setup, monkeypatch):
    tasks, actor, _, _, dispatcher = setup
    monkeypatch.setattr(dispatcher, 'submit', lambda key: False)
    task = start(tasks, actor)
    assert task['status'] == 'failed'
    assert task['error'] == '分析队列已满，请稍后重试。'


def test_actual_shared_dispatcher_reaches_terminal_state(setup, monkeypatch):
    from task_server.background_jobs import PersistedJobDispatcher
    tasks, actor, _, _, _ = setup
    dispatcher = PersistedJobDispatcher(1, 2, job_loader=tasks._load,
        runner_resolver=lambda kind: tasks._run, failure_recorder=tasks._finish_error)
    monkeypatch.setattr(tasks, '_dispatcher', dispatcher)
    monkeypatch.setattr(tasks, '_investigate', lambda source, **kwargs: {
        'status': 'completed', 'result': {'summary': '真实队列已执行'}, 'evidence': []})
    try:
        task = start(tasks, actor)
        for _ in range(80):
            task = tasks.read(actor, task['id'])
            if task['status'] not in tasks.ACTIVE:
                break
            time.sleep(.025)
        assert task['status'] == 'completed', task
        assert task['result']['summary'] == '真实队列已执行'
    finally:
        dispatcher.stop()
