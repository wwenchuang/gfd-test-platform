"""Shutdown evidence and concurrent run/shard lock regression contracts."""
from datetime import datetime, timedelta, timezone
import threading

import pytest
from sqlalchemy import event, select

from task_server.api_testing.models.load_testing import ApiLoadRun, ApiLoadRunShard
from task_server.api_testing.services.load_metric_service import LoadMetricService
from task_server.api_testing.services.load_run_service import LoadRunService
from tests.api_testing.test_load_metric_service import _payload, _running
from tests.api_testing.test_load_testing_repository import load_factory, load_records, load_run_with_shard


def test_stopping_shard_can_flush_its_last_metric_bucket(load_factory, load_run_with_shard):
    _, run, shard = load_run_with_shard
    with load_factory.begin() as session:
        session.get(ApiLoadRun, run.id).state = 'stopping'
        session.get(ApiLoadRunShard, shard.id).state = 'stopping'
    result = LoadMetricService(load_factory, publisher=lambda *_args: None).ingest(shard.agent_id, shard.id, _payload())
    assert result['accepted'] == 1


def test_automatic_stop_remains_failed_when_another_node_goes_stale(load_factory, load_run_with_shard):
    _, run, shard = load_run_with_shard
    now = datetime.now(timezone.utc)
    with load_factory.begin() as session:
        saved = session.get(ApiLoadRun, run.id)
        saved.state = 'stopping'; saved.summary = {'automatic_stop': {'reason': 'node_failed_with_guard_enabled'}}
        saved_shard = session.get(ApiLoadRunShard, shard.id)
        saved_shard.state = 'stopping'; saved_shard.last_heartbeat_at = now - timedelta(seconds=180)
    assert run.id in LoadRunService(load_factory, preflight_service=None, now=lambda: now).recover_stale_runs()
    with load_factory() as session:
        assert session.get(ApiLoadRun, run.id).state == 'failed'


@pytest.mark.parametrize('operation', ['metrics', 'finish'])
def test_ingest_and_finish_do_not_hold_shard_while_waiting_for_run_lock(load_factory, load_run_with_shard, operation):
    _, run, shard = load_run_with_shard
    _running(load_factory, run, shard)
    requesting_run_lock = threading.Event()
    failures = []
    engine = load_factory.kw['bind']
    def observe(_conn, _cursor, statement, _parameters, _context, _many):
        if threading.current_thread().name == 'lock-order-worker' and 'api_load_runs' in statement and 'FOR UPDATE' in statement:
            requesting_run_lock.set()
    def work():
        try:
            if operation == 'metrics':
                LoadMetricService(load_factory, publisher=lambda *_args: None).ingest(shard.agent_id, shard.id, _payload())
            else:
                LoadRunService(load_factory, preflight_service=None).finish_shard(shard.agent_id, shard.id, 'finished')
        except Exception as error:
            failures.append(error)
    worker = threading.Thread(target=work, name='lock-order-worker', daemon=True)
    event.listen(engine, 'before_cursor_execute', observe)
    try:
        with load_factory.begin() as blocking:
            blocking.scalar(select(ApiLoadRun).where(ApiLoadRun.id == run.id).with_for_update())
            worker.start()
            assert requesting_run_lock.wait(5), 'worker never attempted run lock'
            # A concurrent run owner must still be able to lock its shards.
            blocking.scalar(select(ApiLoadRunShard).where(ApiLoadRunShard.id == shard.id).with_for_update(nowait=True))
    finally:
        worker.join(timeout=5)
        event.remove(engine, 'before_cursor_execute', observe)
    assert not worker.is_alive()
    assert failures == []
