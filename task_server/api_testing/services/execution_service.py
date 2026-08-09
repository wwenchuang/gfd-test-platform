"""Idempotent API execution orchestration and truthful aggregation."""

import copy
from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType

from sqlalchemy.exc import IntegrityError

from ..events import EventStream
from ..executor import CaseExecutionResult, HttpExecutor, redact
from ..repositories.execution_repository import ExecutionRepository
from .case_service import CaseService
from .environment_service import EnvironmentService


class ExecutionConflictError(ValueError):
    pass


class ExecutionNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class ExecutionView:
    id: str
    project_id: str
    state: str
    execution_type: str
    source_revision_id: str
    environment_revision_id: str
    case_statuses: tuple
    case_results: tuple
    summary: MappingProxyType
    cancellation_requested: bool
    created_at: object
    started_at: object
    finished_at: object


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _parse_request(value):
    if not isinstance(value, dict):
        raise ValueError("execution request must be an object")
    allowed = {
        "project_id",
        "source_revision_id",
        "environment_revision_id",
        "case_version_ids",
        "execution_type",
        "overrides",
    }
    if set(value) != allowed:
        raise ValueError("execution request fields are invalid")
    result = copy.deepcopy(value)
    for field in ("project_id", "source_revision_id", "environment_revision_id"):
        if not isinstance(result[field], str) or not result[field]:
            raise ValueError(f"{field} is required")
    if result["execution_type"] not in {"debug", "regression"}:
        raise ValueError("execution type is not supported")
    identifiers = result["case_version_ids"]
    if (
        not isinstance(identifiers, list)
        or not identifiers
        or len(identifiers) > 500
        or not all(isinstance(item, str) and item for item in identifiers)
        or len(set(identifiers)) != len(identifiers)
    ):
        raise ValueError("case_version_ids must be a unique non-empty string array")
    if not isinstance(result["overrides"], dict):
        raise ValueError("overrides must be an object")
    return result


class ExecutionService:
    def __init__(self, session_factory, *, executor=None, event_stream=None):
        self.session_factory = session_factory
        self.event_stream = event_stream or EventStream(session_factory)
        self.executor = executor or HttpExecutor(
            CaseService(session_factory), EnvironmentService(session_factory)
        )

    def submit(self, request, actor_id, idempotency_key):
        parsed = _parse_request(request)
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise ValueError("idempotency key is invalid")
        if not isinstance(actor_id, str) or not actor_id:
            raise ValueError("actor id is required")
        fingerprint = _fingerprint(parsed)
        with self.session_factory.begin() as session:
            repository = ExecutionRepository(session)
            existing = repository.get_by_idempotency(parsed["project_id"], idempotency_key)
            if existing is not None:
                if existing.request_snapshot.get("fingerprint") != fingerprint:
                    raise ExecutionConflictError("idempotency key was used with a different payload")
                children = repository.get_execution_cases(existing.id)
                return self._view(existing, children)
            context = self._validate_snapshot(repository, parsed)
            snapshot = {
                "fingerprint": fingerprint,
                "request": redact(parsed),
                "case_versions": [
                    {
                        "id": version.id,
                        "case_id": version.case_id,
                        "endpoint_id": version.endpoint_id,
                        "version": version.version_number,
                    }
                    for version in context["versions"]
                ],
                "source_revision_id": parsed["source_revision_id"],
                "environment_revision_id": parsed["environment_revision_id"],
            }
            try:
                with session.begin_nested():
                    execution = repository.create_execution(
                        parsed, snapshot, actor_id, idempotency_key
                    )
                    children = tuple(
                        repository.create_execution_case(
                            execution,
                            version,
                            context["endpoints"][version.endpoint_id],
                            ordinal,
                            actor_id,
                        )
                        for ordinal, version in enumerate(context["versions"])
                    )
            except IntegrityError:
                existing = repository.get_by_idempotency(
                    parsed["project_id"], idempotency_key
                )
                if existing is None:
                    raise ExecutionConflictError(
                        "idempotency key conflicted during submit"
                    )
                if existing.request_snapshot.get("fingerprint") != fingerprint:
                    raise ExecutionConflictError(
                        "idempotency key was used with a different payload"
                    )
                return self._view(
                    existing, repository.get_execution_cases(existing.id)
                )
            view = self._view(execution, children)
        self.event_stream.append(view.id, "execution_queued", {"case_count": len(view.case_statuses)})
        return view

    def get(self, execution_id):
        with self.session_factory() as session:
            repository = ExecutionRepository(session)
            execution = repository.get_execution(execution_id)
            if execution is None:
                raise ExecutionNotFoundError("API execution was not found")
            return self._view(execution, repository.get_execution_cases(execution.id))

    def cancel(self, execution_id, actor_id):
        with self.session_factory.begin() as session:
            repository = ExecutionRepository(session)
            execution = repository.request_cancellation(execution_id, actor_id)
            if execution is None:
                raise ExecutionNotFoundError("API execution was not found")
            children = repository.get_execution_cases(execution.id)
            view = self._view(execution, children)
        self.event_stream.signal_cancel(execution_id)
        self.event_stream.append(execution_id, "cancellation_requested", {})
        return view

    def run(self, execution_id):
        with self.session_factory.begin() as session:
            repository = ExecutionRepository(session)
            execution = repository.get_execution(execution_id)
            if execution is None:
                raise ExecutionNotFoundError("API execution was not found")
            if execution.cancellation_requested_at is not None:
                return False
            if not repository.compare_and_set_execution(execution_id, {"QUEUED"}, "RUNNING"):
                return False
            snapshot = copy.deepcopy(execution.request_snapshot)
            children = repository.get_execution_cases(execution_id)
        worker_error = None
        try:
            self.event_stream.append(execution_id, "execution_started", {})
            for child in children:
                if self._is_cancelled(execution_id):
                    break
                with self.session_factory.begin() as session:
                    repository = ExecutionRepository(session)
                    if not repository.compare_and_set_case(
                        child.id, {"QUEUED"}, "RUNNING"
                    ):
                        continue
                self.event_stream.append(
                    execution_id,
                    "case_started",
                    {
                        "execution_case_id": child.id,
                        "case_version_id": child.case_version_id,
                    },
                )
                try:
                    result = self._execute_child(execution_id, child, snapshot)
                except Exception as exc:
                    worker_error = exc
                    result = self._broken_result(exc)
                self._persist_child_result(execution_id, child.id, result)
                self.event_stream.append(
                    execution_id,
                    "case_finished",
                    {
                        "execution_case_id": child.id,
                        "status": result.status,
                        "failure_category": result.failure_category,
                    },
                )
                if worker_error is not None:
                    break
        except Exception as exc:
            worker_error = exc
        if worker_error is not None:
            self._converge_outstanding(execution_id, worker_error)
            self._safe_event(
                execution_id,
                "failure",
                {
                    "status": "BROKEN",
                    "failure_category": "worker",
                    "error_message": self._safe_worker_error(worker_error),
                },
            )
        state, summary = self._finalize(execution_id)
        self._safe_event(
            execution_id,
            "execution_finished",
            {"state": state, "summary": summary},
        )
        return True

    def _execute_child(self, execution_id, child, snapshot):
        def phase_callback(phase, payload):
            self.event_stream.append(
                execution_id,
                phase,
                {"execution_case_id": child.id, **copy.deepcopy(payload)},
            )

        return self.executor.execute_case(
            child.case_version_id,
            child.environment_revision_id,
            copy.deepcopy(snapshot["request"]["overrides"]),
            cancellation_check=lambda _phase: self._is_cancelled(execution_id),
            phase_callback=phase_callback,
        )

    def _persist_child_result(self, execution_id, child_id, result):
        with self.session_factory.begin() as session:
            repository = ExecutionRepository(session)
            current = next(
                item
                for item in repository.get_execution_cases(
                    execution_id, for_update=True
                )
                if item.id == child_id
            )
            if current.status == "RUNNING":
                repository.create_attempt(current, result, "worker")

    def _converge_outstanding(self, execution_id, error):
        result = self._broken_result(error)
        with self.session_factory.begin() as session:
            repository = ExecutionRepository(session)
            execution = repository.get_execution(execution_id, for_update=True)
            for child in repository.get_execution_cases(
                execution_id, for_update=True
            ):
                if child.status not in {"QUEUED", "RUNNING"}:
                    continue
                if execution.cancellation_requested_at is not None:
                    child.status = "CANCELLED"
                    child.failure_category = "cancelled"
                    child.updated_by = "worker"
                else:
                    repository.create_attempt(child, result, "worker")

    def _finalize(self, execution_id):
        with self.session_factory.begin() as session:
            repository = ExecutionRepository(session)
            execution = repository.get_execution(execution_id, for_update=True)
            for child in repository.get_execution_cases(
                execution_id, for_update=True
            ):
                if child.status in {"QUEUED", "RUNNING"}:
                    if execution.cancellation_requested_at is not None:
                        child.status = "CANCELLED"
                        child.failure_category = "cancelled"
                    else:
                        repository.create_attempt(
                            child,
                            self._broken_result(
                                RuntimeError("worker ended before case completion")
                            ),
                            "worker",
                        )
            finalized = repository.finalize_execution(execution_id, "worker")
            return finalized.state, copy.deepcopy(finalized.summary)

    def _safe_event(self, execution_id, event_type, payload):
        try:
            self.event_stream.append(execution_id, event_type, payload)
        except Exception:
            pass

    @staticmethod
    def _safe_worker_error(error):
        return f"worker execution failed ({type(error).__name__})"

    @classmethod
    def _broken_result(cls, error):
        return CaseExecutionResult(
            status="BROKEN",
            failure_category="worker",
            duration_ms=0,
            sanitized_request={},
            sanitized_response={},
            assertion_results=(),
            extracted_variables={},
            error_message=cls._safe_worker_error(error),
            trace=(
                {
                    "phase": "failure",
                    "status": "BROKEN",
                    "failure_category": "worker",
                },
            ),
        )

    def _is_cancelled(self, execution_id):
        with self.session_factory() as session:
            return ExecutionRepository(session).cancellation_requested(execution_id)

    @staticmethod
    def _validate_snapshot(repository, request):
        project = repository.get_project(request["project_id"])
        if project is None:
            raise ValueError("project was not found")
        source_revision = repository.get_source_revision(request["source_revision_id"])
        source = repository.get_source(source_revision.source_id) if source_revision else None
        if source is None or source.project_id != project.id:
            raise ValueError("source revision does not belong to project")
        environment_revision = repository.get_environment_revision(
            request["environment_revision_id"]
        )
        environment = (
            repository.get_environment(environment_revision.environment_id)
            if environment_revision
            else None
        )
        if environment is None or environment.project_id != project.id:
            raise ValueError("environment revision does not belong to project")
        versions_by_id = repository.get_case_versions(request["case_version_ids"])
        if len(versions_by_id) != len(request["case_version_ids"]):
            raise ValueError("case version was not found")
        versions = [versions_by_id[item] for item in request["case_version_ids"]]
        cases = repository.get_cases(item.case_id for item in versions)
        endpoints = repository.get_endpoints(item.endpoint_id for item in versions)
        for version in versions:
            case = cases.get(version.case_id)
            endpoint = endpoints.get(version.endpoint_id)
            if (
                case is None
                or case.project_id != project.id
                or endpoint is None
                or endpoint.revision_id != source_revision.id
            ):
                raise ValueError("case version does not match source revision and project")
        return {"versions": versions, "endpoints": endpoints}

    @staticmethod
    def _view(execution, children):
        return ExecutionView(
            id=execution.id,
            project_id=execution.project_id,
            state=execution.state,
            execution_type=execution.execution_type,
            source_revision_id=execution.source_revision_id,
            environment_revision_id=execution.environment_revision_id,
            case_statuses=tuple(item.status for item in children),
            case_results=tuple(
                MappingProxyType({
                    "execution_case_id": item.id,
                    "case_version_id": item.case_version_id,
                    "endpoint_id": item.endpoint_id,
                    "status": item.status,
                    "failure_category": item.failure_category,
                    "duration_ms": item.duration_ms,
                    "sanitized_result": copy.deepcopy(dict(item.sanitized_result)),
                })
                for item in children
            ),
            summary=MappingProxyType(copy.deepcopy(dict(execution.summary))),
            cancellation_requested=execution.cancellation_requested_at is not None,
            created_at=execution.created_at,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
        )
