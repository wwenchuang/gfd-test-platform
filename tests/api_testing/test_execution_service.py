import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
import threading
import time

from alembic import command
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
import pytest
import redis

from task_server.api_testing.events import EventStream
from task_server.api_testing.models.case import ApiCase, ApiCaseVersion
from task_server.api_testing.models.environment import ApiEnvironment, ApiEnvironmentRevision
from task_server.api_testing.models.execution import (
    ApiExecution,
    ApiExecutionAttempt,
    ApiExecutionCase,
    ApiExecutionEvent,
)
from task_server.api_testing.models.project import ApiProject
from task_server.api_testing.services.case_service import CaseService
from task_server.api_testing.services.execution_service import (
    ExecutionConflictError,
    ExecutionService,
)
from task_server.api_testing.services.source_service import SourceService
from tests.api_testing.test_case_service import FAVORITES_OPENAPI, valid_list_case
from tests.api_testing.test_migrations import (
    _alembic_config,
    _assert_current_test_schema,
    _create_test_schema,
    _database_url,
    _drop_test_schema,
    _without_database_environment,
)


def _audit(actor="admin"):
    return {"owner_id": actor, "created_by": actor, "updated_by": actor}


@pytest.fixture(scope="module")
def execution_database():
    database_url = _database_url()
    created_schemas = set()
    schema_name, schema_url = _create_test_schema(database_url, created_schemas)
    with _without_database_environment():
        command.upgrade(_alembic_config(schema_url), "head")
    _assert_current_test_schema(schema_url, schema_name)
    try:
        yield schema_url
    finally:
        _drop_test_schema(database_url, schema_name, created_schemas)


@pytest.fixture(scope="module")
def session_factory(execution_database):
    engine = create_engine(execution_database, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture(scope="module")
def redis_client():
    client = redis.Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
    client.ping()
    client.flushdb()
    try:
        yield client
    finally:
        client.flushdb()


@pytest.fixture()
def execution_context(session_factory):
    suffix = os.urandom(6).hex()
    with session_factory.begin() as session:
        project = ApiProject(
            name=f"Task 6 Project {suffix}",
            slug=f"task-6-project-{suffix}",
            **_audit(),
        )
        session.add(project)
        session.flush()
    source_service = SourceService(session_factory)
    preview = source_service.preview_refresh(
        project.id, None, copy.deepcopy(FAVORITES_OPENAPI), "admin"
    )
    revision = source_service.activate_preview(preview.id, "admin")
    endpoint = next(item for item in revision.endpoints if item.operation_id == "favoriteList")
    with session_factory.begin() as session:
        environment = ApiEnvironment(
            project_id=project.id,
            source_id=revision.source_id,
            name=f"Task 6 Environment {suffix}",
            **_audit(),
        )
        session.add(environment)
        session.flush()
        environment_revision = ApiEnvironmentRevision(
            environment_id=environment.id,
            source_revision_id=revision.id,
            revision_number=1,
            name=environment.name,
            default_headers={},
            **_audit(),
        )
        session.add(environment_revision)
        session.flush()
        environment.active_revision_id = environment_revision.id
    case = CaseService(session_factory).create_draft(
        endpoint.id, valid_list_case(endpoint), "manual", "admin"
    )
    return {
        "project": project,
        "source_revision": revision,
        "environment_revision": environment_revision,
        "case": case,
        "endpoint": endpoint,
    }


def _request(context, **changes):
    value = {
        "project_id": context["project"].id,
        "source_revision_id": context["source_revision"].id,
        "environment_revision_id": context["environment_revision"].id,
        "case_version_ids": [context["case"].id],
        "execution_type": "debug",
        "overrides": {"Biz": "ZXB"},
    }
    value.update(changes)
    return value


class _FakeExecutor:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def execute_case(self, *_args, **_kwargs):
        self.calls += 1
        return self.results.pop(0)


class _ExplodingExecutor:
    def execute_case(self, *_args, **_kwargs):
        raise RuntimeError("worker exploded with token=plain-worker-secret")


class _PhaseExecutor:
    def __init__(self):
        self.barrier = threading.Barrier(2)

    def execute_case(
        self,
        case_version_id,
        _environment_revision_id,
        _overrides,
        *,
        cancellation_check=None,
        phase_callback=None,
    ):
        self.barrier.wait(timeout=2)
        phase_callback(
            "request", {"case_version_id": case_version_id, "token": "phase-secret"}
        )
        phase_callback(
            "response", {"case_version_id": case_version_id, "status_code": 200}
        )
        phase_callback(
            "assertion", {"case_version_id": case_version_id, "passed": True}
        )
        return _Result("PASSED")


class _Result:
    def __init__(self, status, category=""):
        self.status = status
        self.failure_category = category
        self.duration_ms = 5
        self.sanitized_request = {"url": "https://example.test"}
        self.sanitized_response = {"status_code": 200}
        self.assertion_results = ()
        self.error_message = ""
        self.extracted_variables = {}
        self.trace = ()

    def to_dict(self):
        return {
            "status": self.status,
            "failure_category": self.failure_category,
            "duration_ms": self.duration_ms,
        }


def test_submit_idempotency_snapshots_and_payload_conflict(
    session_factory, redis_client, execution_context
):
    service = ExecutionService(session_factory, event_stream=EventStream(session_factory, redis_client))
    request = _request(execution_context)
    first = service.submit(request, "admin", "same-key")
    second = service.submit(copy.deepcopy(request), "admin", "same-key")
    assert second.id == first.id
    assert first.case_statuses == ("QUEUED",)

    with pytest.raises(ExecutionConflictError, match="idempotency"):
        service.submit(
            _request(execution_context, overrides={"Biz": "OTHER"}),
            "admin",
            "same-key",
        )

    with session_factory() as session:
        record = session.get(ApiExecution, first.id)
        child = session.scalar(
            select(ApiExecutionCase).where(ApiExecutionCase.execution_id == first.id)
        )
        assert record.source_revision_id == execution_context["source_revision"].id
        assert record.environment_revision_id == execution_context["environment_revision"].id
        assert child.case_version_id == execution_context["case"].id
        assert child.endpoint_id == execution_context["endpoint"].id


def test_concurrent_submit_with_same_key_creates_one_execution(
    session_factory, redis_client, execution_context
):
    request = _request(execution_context)

    def submit(_index):
        return ExecutionService(
            session_factory,
            event_stream=EventStream(session_factory, redis_client),
        ).submit(copy.deepcopy(request), "admin", "concurrent-key")

    with ThreadPoolExecutor(max_workers=4) as pool:
        views = list(pool.map(submit, range(4)))
    assert len({item.id for item in views}) == 1
    with session_factory() as session:
        assert session.scalar(
            select(func.count(ApiExecution.id)).where(
                ApiExecution.project_id == execution_context["project"].id,
                ApiExecution.idempotency_key == "concurrent-key",
            )
        ) == 1


def test_duplicate_worker_is_compare_and_set_and_summary_keeps_child_truth(
    session_factory, redis_client, execution_context
):
    executor = _FakeExecutor(
        [_Result("PASSED"), _Result("FAILED", "product_assertion")]
    )
    service = ExecutionService(
        session_factory,
        executor=executor,
        event_stream=EventStream(session_factory, redis_client),
    )
    first_case = execution_context["case"]
    with session_factory.begin() as session:
        original = session.get(ApiCaseVersion, first_case.id)
        parent = session.get(ApiCase, original.case_id)
        second = ApiCaseVersion(
            case_id=parent.id,
            endpoint_id=original.endpoint_id,
            version_number=original.version_number + 1,
            status="draft",
            purpose=original.purpose,
            priority=original.priority,
            request_template=copy.deepcopy(original.request_template),
            dependency_spec={"dependencies": []},
            processing_spec={"pre": [], "post": []},
            **_audit(),
        )
        session.add(second)
        session.flush()
        second_id = second.id
    request = _request(
        execution_context,
        case_version_ids=[first_case.id, second_id],
    )
    execution = service.submit(request, "admin", "worker-key")

    assert service.run(execution.id) is True
    assert service.run(execution.id) is False
    view = service.get(execution.id)
    assert view.state == "DONE"
    assert view.summary == {
        "total": 2,
        "passed": 1,
        "failed": 1,
        "broken": 0,
        "cancelled": 0,
    }
    assert view.case_statuses == ("PASSED", "FAILED")
    assert view.case_results[0]["case_version_id"] == first_case.id
    assert view.case_results[0]["status"] == "PASSED"
    assert view.case_results[0]["sanitized_result"]["status"] == "PASSED"
    assert executor.calls == 2


def test_worker_exception_creates_broken_attempt_and_converges_all_children(
    session_factory, redis_client, execution_context
):
    first_case = execution_context["case"]
    with session_factory.begin() as session:
        original = session.get(ApiCaseVersion, first_case.id)
        parent = session.get(ApiCase, original.case_id)
        second = ApiCaseVersion(
            case_id=parent.id,
            endpoint_id=original.endpoint_id,
            version_number=original.version_number + 1,
            status="draft",
            purpose=original.purpose,
            priority=original.priority,
            request_template=copy.deepcopy(original.request_template),
            dependency_spec={"dependencies": []},
            processing_spec={"pre": [], "post": []},
            **_audit(),
        )
        session.add(second)
        session.flush()
        second_id = second.id
    service = ExecutionService(
        session_factory,
        executor=_ExplodingExecutor(),
        event_stream=EventStream(session_factory, redis_client),
    )
    execution = service.submit(
        _request(
            execution_context,
            case_version_ids=[first_case.id, second_id],
        ),
        "admin",
        "worker-exception",
    )
    assert service.run(execution.id) is True
    assert service.run(execution.id) is False
    view = service.get(execution.id)
    assert view.state == "DONE"
    assert view.case_statuses == ("BROKEN", "BROKEN")
    assert view.summary["broken"] == 2
    with session_factory() as session:
        attempts = tuple(
            session.scalars(
                select(ApiExecutionAttempt)
                .join(
                    ApiExecutionCase,
                    ApiExecutionCase.id == ApiExecutionAttempt.execution_case_id,
                )
                .where(ApiExecutionCase.execution_id == execution.id)
            )
        )
        assert len(attempts) == 2
        persisted = json.dumps(
            [
                {
                    "status": item.status,
                    "error": item.error_message,
                    "request": item.request,
                    "response": item.response,
                }
                for item in attempts
            ]
        )
        assert "plain-worker-secret" not in persisted
        failure_events = tuple(
            session.scalars(
                select(ApiExecutionEvent).where(
                    ApiExecutionEvent.execution_id == execution.id,
                    ApiExecutionEvent.event_type == "failure",
                )
            )
        )
        assert len(failure_events) == 1
        assert "plain-worker-secret" not in json.dumps(
            failure_events[0].payload
        )


def test_cancel_intent_is_persistent_and_prevents_request(
    session_factory, redis_client, execution_context
):
    executor = _FakeExecutor([_Result("PASSED")])
    service = ExecutionService(
        session_factory,
        executor=executor,
        event_stream=EventStream(session_factory, redis_client),
    )
    execution = service.submit(_request(execution_context), "admin", "cancel-key")
    cancelled = service.cancel(execution.id, "admin")
    assert cancelled.cancellation_requested is True
    assert service.run(execution.id) is False
    assert service.get(execution.id).state == "CANCELLED"
    assert executor.calls == 0


def test_event_resume_is_strict_and_redis_failure_falls_back_to_postgres(
    session_factory, redis_client, execution_context, monkeypatch
):
    service = ExecutionService(session_factory, event_stream=EventStream(session_factory, redis_client))
    execution = service.submit(_request(execution_context), "admin", "events-key")
    stream = service.event_stream
    first = stream.append(execution.id, "started", {"token": "do-not-persist"})
    second = stream.append(execution.id, "case_finished", {"status": "PASSED"})
    assert [item.sequence for item in stream.read(execution.id, first, 0)] == [second]
    assert stream.read(execution.id, second, 0) == ()

    monkeypatch.setattr(redis_client, "xread", lambda *_args, **_kwargs: (_ for _ in ()).throw(ConnectionError("down")))
    assert [item.sequence for item in stream.read(execution.id, first, 1)] == [second]


def test_postgres_fallback_blocks_until_deadline_or_new_event(
    session_factory, execution_context
):
    stream = EventStream(session_factory, None)
    service = ExecutionService(session_factory, event_stream=stream)
    execution = service.submit(
        _request(execution_context), "admin", "postgres-block"
    )
    latest = stream.read(execution.id, 0, 0)[-1].sequence
    started = time.monotonic()
    assert stream.read(execution.id, latest, 180) == ()
    assert time.monotonic() - started >= 0.16

    def append_later():
        time.sleep(0.07)
        stream.append(execution.id, "late", {"status": "ready"})

    thread = threading.Thread(target=append_later)
    thread.start()
    started = time.monotonic()
    events = stream.read(execution.id, latest, 500)
    thread.join(timeout=1)
    assert [item.type for item in events] == ["late"]
    assert time.monotonic() - started < 0.3


def test_phase_events_are_durable_sanitized_and_isolated_between_executions(
    session_factory, redis_client, execution_context
):
    executor = _PhaseExecutor()
    service = ExecutionService(
        session_factory,
        executor=executor,
        event_stream=EventStream(session_factory, redis_client),
    )
    first = service.submit(
        _request(execution_context), "admin", "phase-first"
    )
    second = service.submit(
        _request(execution_context), "admin", "phase-second"
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(service.run, [first.id, second.id])) == [True, True]
    for execution in (first, second):
        events = service.event_stream.read(execution.id, 0, 0)
        phase_events = [
            item for item in events if item.type in {"request", "response", "assertion"}
        ]
        assert [item.type for item in phase_events] == [
            "request",
            "response",
            "assertion",
        ]
        persisted = json.dumps([item.payload for item in events])
        assert "phase-secret" not in persisted
        assert len(
            {
                item.payload["execution_case_id"]
                for item in phase_events
            }
        ) == 1
    with session_factory() as session:
        assert session.scalar(
            select(func.count(ApiExecutionEvent.id)).where(
                ApiExecutionEvent.execution_id.in_([first.id, second.id]),
                ApiExecutionEvent.event_type == "request",
            )
        ) == 2


def test_redis_stream_is_bounded_and_expires(
    session_factory, redis_client, execution_context, monkeypatch
):
    monkeypatch.setattr(EventStream, "MAX_LENGTH", 5)
    service = ExecutionService(
        session_factory, event_stream=EventStream(session_factory, redis_client)
    )
    execution = service.submit(_request(execution_context), "admin", "bounded-events")
    for index in range(12):
        service.event_stream.append(execution.id, "progress", {"index": index})
    key = service.event_stream._key(execution.id)
    assert redis_client.xlen(key) <= 5
    assert 0 < redis_client.ttl(key) <= EventStream.TTL_SECONDS
    service.event_stream.append(
        execution.id, "large", {"body": "x" * (EventStream.MAX_PAYLOAD_BYTES * 2)}
    )
    large = service.event_stream.read(execution.id, 0, 0)[-1]
    assert large.payload["truncated"] is True
    assert len(json.dumps(large.payload).encode()) <= EventStream.MAX_PAYLOAD_BYTES


def test_submit_rejects_cross_project_or_revision_drift(
    session_factory, redis_client, execution_context
):
    service = ExecutionService(session_factory, event_stream=EventStream(session_factory, redis_client))
    with pytest.raises(ValueError, match="source revision"):
        service.submit(
            _request(execution_context, source_revision_id="00000000-0000-4000-8000-000000000000"),
            "admin",
            "bad-source",
        )
