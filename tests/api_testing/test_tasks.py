from types import SimpleNamespace
from unittest.mock import Mock

from task_server.api_testing import tasks


def test_worker_heartbeat_has_short_expiry(monkeypatch):
    fake_redis = Mock()
    monkeypatch.setattr(tasks, "_heartbeat_redis", lambda: fake_redis)
    monkeypatch.setattr(
        tasks,
        "settings",
        SimpleNamespace(
            worker_heartbeat_key="midscene:api-testing:worker-heartbeat",
            worker_heartbeat_ttl_seconds=45,
        ),
    )

    tasks.publish_worker_heartbeat(None)

    fake_redis.set.assert_called_once_with(
        "midscene:api-testing:worker-heartbeat",
        "1",
        ex=45,
    )


def test_worker_heartbeat_failure_does_not_crash_worker(monkeypatch, caplog):
    def fail_redis():
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr(tasks, "_heartbeat_redis", fail_redis)

    tasks.publish_worker_heartbeat(None)

    assert "Unable to publish API testing worker heartbeat" in caplog.text


def test_execution_worker_refreshes_linked_task_after_running(monkeypatch):
    calls = []

    class FakeExecutionService:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, execution_id):
            calls.append(("run", execution_id))
            return True

    class FakeTaskService:
        def __init__(self, factory):
            pass

        def refresh_for_execution(self, execution_id):
            calls.append(("refresh", execution_id))

    class FakeExecution:
        owner_id = "owner-a"
        execution_type = "debug"
        state = "DONE"

    class FakeRepository:
        def __init__(self, session):
            pass

        def get_execution(self, execution_id):
            calls.append(("load-execution", execution_id))
            return FakeExecution()

    class FakeFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(tasks, "ExecutionService", FakeExecutionService)
    monkeypatch.setattr(tasks, "TestTaskService", FakeTaskService, raising=False)
    monkeypatch.setattr(tasks, "ExecutionRepository", FakeRepository, raising=False)
    monkeypatch.setattr(tasks, "_session_factory", lambda: FakeFactory())

    assert tasks.execute_api_testing.run("execution-1") is True
    assert calls == [("run", "execution-1"), ("refresh", "execution-1"), ("load-execution", "execution-1")]


def test_execution_worker_sends_project_feishu_for_baseline_regression(monkeypatch):
    calls = []

    class FakeExecution:
        owner_id = "owner-a"
        execution_type = "baseline_regression"
        state = "DONE"

    class FakeRepository:
        def __init__(self, session):
            pass

        def get_execution(self, execution_id):
            calls.append(("load-execution", execution_id))
            return FakeExecution()

    class FakeExecutionService:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, execution_id):
            calls.append(("run", execution_id))
            return True

    class FakeTaskService:
        def __init__(self, factory):
            pass

        def refresh_for_execution(self, execution_id):
            calls.append(("refresh", execution_id))

    class FakeNotificationService:
        def __init__(self, factory):
            pass

        def send_execution_report(self, execution_id, actor_id):
            calls.append(("notify", execution_id, actor_id))
            return SimpleNamespace(channel_type="feishu", message="飞书通知已发")

    class FakeEventStream:
        def __init__(self, *args, **kwargs):
            pass

        def append(self, execution_id, event_type, payload):
            calls.append(("event", execution_id, event_type, payload["message"]))

    class FakeFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(tasks, "ExecutionService", FakeExecutionService)
    monkeypatch.setattr(tasks, "TestTaskService", FakeTaskService, raising=False)
    monkeypatch.setattr(tasks, "NotificationService", FakeNotificationService, raising=False)
    monkeypatch.setattr(tasks, "ExecutionRepository", FakeRepository, raising=False)
    monkeypatch.setattr(tasks, "EventStream", FakeEventStream)
    monkeypatch.setattr(tasks, "_session_factory", lambda: FakeFactory())

    assert tasks.execute_api_testing.run("execution-1") is True
    assert calls == [
        ("run", "execution-1"),
        ("refresh", "execution-1"),
        ("load-execution", "execution-1"),
        ("notify", "execution-1", "owner-a"),
        ("event", "execution-1", "notification_sent", "飞书通知已发"),
    ]


def test_execution_worker_sends_feishu_for_scheduled_job_when_enabled(monkeypatch):
    calls = []

    class FakeExecution:
        owner_id = "owner-a"
        execution_type = "scheduled"
        state = "DONE"
        request_snapshot = {
            "task": {
                "id": "job-1",
                "name": "每日回归",
                "type": "scheduled_job",
                "source": "scheduled_job",
                "notify_feishu": True,
            },
        }

    class FakeRepository:
        def __init__(self, session):
            pass

        def get_execution(self, execution_id):
            calls.append(("load-execution", execution_id))
            return FakeExecution()

    class FakeExecutionService:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, execution_id):
            calls.append(("run", execution_id))
            return True

    class FakeTaskService:
        def __init__(self, factory):
            pass

        def refresh_for_execution(self, execution_id):
            calls.append(("refresh", execution_id))

    class FakeNotificationService:
        def __init__(self, factory):
            pass

        def send_execution_report(self, execution_id, actor_id):
            calls.append(("notify", execution_id, actor_id))
            return SimpleNamespace(channel_type="feishu", message="飞书通知已发")

    class FakeEventStream:
        def __init__(self, *args, **kwargs):
            pass

        def append(self, execution_id, event_type, payload):
            calls.append(("event", execution_id, event_type, payload["message"]))

    class FakeFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(tasks, "ExecutionService", FakeExecutionService)
    monkeypatch.setattr(tasks, "TestTaskService", FakeTaskService, raising=False)
    monkeypatch.setattr(tasks, "NotificationService", FakeNotificationService, raising=False)
    monkeypatch.setattr(tasks, "ExecutionRepository", FakeRepository, raising=False)
    monkeypatch.setattr(tasks, "EventStream", FakeEventStream)
    monkeypatch.setattr(tasks, "_session_factory", lambda: FakeFactory())

    assert tasks.execute_api_testing.run("execution-1") is True
    assert calls == [
        ("run", "execution-1"),
        ("refresh", "execution-1"),
        ("load-execution", "execution-1"),
        ("notify", "execution-1", "owner-a"),
        ("event", "execution-1", "notification_sent", "飞书通知已发"),
    ]


def test_execution_worker_does_not_auto_notify_debug_runs(monkeypatch):
    calls = []

    class FakeExecution:
        owner_id = "owner-a"
        execution_type = "debug"
        state = "DONE"

    class FakeRepository:
        def __init__(self, session):
            pass

        def get_execution(self, execution_id):
            calls.append(("load-execution", execution_id))
            return FakeExecution()

    class FakeExecutionService:
        def __init__(self, *args, **kwargs):
            pass

        def run(self, execution_id):
            calls.append(("run", execution_id))
            return True

    class FakeTaskService:
        def __init__(self, factory):
            pass

        def refresh_for_execution(self, execution_id):
            calls.append(("refresh", execution_id))

    class FakeNotificationService:
        def __init__(self, factory):
            pass

        def send_execution_report(self, *_args):
            raise AssertionError("debug executions must not auto notify")

    class FakeFactory:
        def __call__(self):
            return self

        def __enter__(self):
            return object()

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(tasks, "ExecutionService", FakeExecutionService)
    monkeypatch.setattr(tasks, "TestTaskService", FakeTaskService, raising=False)
    monkeypatch.setattr(tasks, "NotificationService", FakeNotificationService, raising=False)
    monkeypatch.setattr(tasks, "ExecutionRepository", FakeRepository, raising=False)
    monkeypatch.setattr(tasks, "_session_factory", lambda: FakeFactory())

    assert tasks.execute_api_testing.run("execution-1") is True
    assert calls == [("run", "execution-1"), ("refresh", "execution-1"), ("load-execution", "execution-1")]


def test_ai_worker_refreshes_linked_task_after_generation(monkeypatch):
    calls = []

    class Job:
        state = "completed"

    class FakeAiService:
        def __init__(self, factory):
            pass

        def process(self, job_id):
            calls.append(("process", job_id))
            return Job()

    class FakeTaskService:
        def __init__(self, factory):
            pass

        def refresh_for_ai_job(self, job_id):
            calls.append(("refresh", job_id))

    monkeypatch.setattr(tasks, "AiCaseService", FakeAiService)
    monkeypatch.setattr(tasks, "TestTaskService", FakeTaskService, raising=False)
    monkeypatch.setattr(tasks, "_session_factory", lambda: object())

    assert tasks.generate_api_cases.run("job-1") == "completed"
    assert calls == [("process", "job-1"), ("refresh", "job-1")]
