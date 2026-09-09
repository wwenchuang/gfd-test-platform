from datetime import datetime, timezone
from types import SimpleNamespace

from task_server.api_testing import scheduler


def test_recovery_scan_queues_only_stale_running_executions(monkeypatch):
    calls = []

    class FakeExecutionService:
        def __init__(self, factory):
            assert factory == "factory"

        def stale_running_execution_ids(self, stale_before):
            calls.append(("scan", stale_before.isoformat()))
            return ("execution-1", "execution-2")

    monkeypatch.setattr(scheduler, "ExecutionService", FakeExecutionService)
    now = datetime(2026, 9, 9, 15, 0, tzinfo=timezone.utc)

    recovered = scheduler._recover_interrupted_executions(
        "factory",
        SimpleNamespace(execution_stale_seconds=300),
        now=now,
        enqueue=lambda execution_id, cutoff: calls.append(
            ("enqueue", execution_id, cutoff)
        ),
    )

    cutoff = "2026-09-09T14:55:00+00:00"
    assert recovered == ("execution-1", "execution-2")
    assert calls == [
        ("scan", cutoff),
        ("enqueue", "execution-1", cutoff),
        ("enqueue", "execution-2", cutoff),
    ]
