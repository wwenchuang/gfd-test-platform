"""Dispatch creator lookup must stay bounded as unrelated audit history grows."""
from contextlib import contextmanager

import pytest

from task_server.services.operation_attribution import OperationStore


@pytest.mark.parametrize("job_id,expected", [("target", "creator"), ("absent", "")])
def test_creator_lookup_does_not_scan_unrelated_success_events(tmp_path, monkeypatch, job_id, expected):
    store = OperationStore(tmp_path / "operations.sqlite3")
    with store._connect() as db:
        db.executemany(
            "INSERT INTO events(event_id,timestamp,actor_kind,actor_id,action,resource_type,resource_id,result,data) "
            "VALUES (?,0,'user','creator','case.save','case',?,'success','{}')",
            ((str(i), str(i)) for i in range(20000)),
        )
        db.execute(
            "INSERT INTO events(event_id,timestamp,actor_kind,actor_id,action,resource_type,resource_id,result,data) "
            "VALUES ('created',0,'user','creator','job.created','job','target','success','{}')"
        )
    original = store._connect
    instructions = [0]

    @contextmanager
    def measured_connection():
        with original() as db:
            def tick():
                instructions[0] += 100
                return 0
            db.set_progress_handler(tick, 100)
            yield db

    monkeypatch.setattr(store, "_connect", measured_connection)
    assert store.job_creation_actor_id(job_id) == expected
    assert instructions[0] < 1000, "creator lookup scanned unrelated audit history"
