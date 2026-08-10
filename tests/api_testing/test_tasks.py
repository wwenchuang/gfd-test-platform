from task_server.api_testing import tasks


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

    monkeypatch.setattr(tasks, "ExecutionService", FakeExecutionService)
    monkeypatch.setattr(tasks, "TestTaskService", FakeTaskService, raising=False)
    monkeypatch.setattr(tasks, "_session_factory", lambda: object())

    assert tasks.execute_api_testing.run("execution-1") is True
    assert calls == [("run", "execution-1"), ("refresh", "execution-1")]


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
