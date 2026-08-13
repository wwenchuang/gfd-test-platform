"""Idempotent API execution orchestration and truthful aggregation."""

import copy
from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Optional

from sqlalchemy.exc import IntegrityError

from ..events import EventStream
from ..executor import CaseExecutionResult, HttpExecutor, redact
from ..repositories.execution_repository import ExecutionRepository
from .case_service import CaseService
from .environment_service import EnvironmentService


MAX_FAILURE_ANALYSIS_EVIDENCE_BYTES = 128 * 1024


class ExecutionConflictError(ValueError):
    pass


class ExecutionNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class ExecutionView:
    id: str
    project_id: str
    task_id: Optional[str]
    task_name: Optional[str]
    state: str
    execution_type: str
    source_revision_id: str
    environment_revision_id: str
    environment_name: str
    case_statuses: tuple
    case_results: tuple
    summary: MappingProxyType
    notifications: MappingProxyType
    cancellation_requested: bool
    created_at: object
    started_at: object
    finished_at: object


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _optional_identifier_array(value, field):
    if value is None:
        return None
    if (
        not isinstance(value, list)
        or not value
        or len(value) > 500
        or not all(isinstance(item, str) and item for item in value)
        or len(set(value)) != len(value)
    ):
        raise ValueError(f"{field} must be a unique non-empty string array")
    return value


def _execution_notification_status(events):
    notifications = {}
    for event in events or ():
        event_type = getattr(event, "event_type", "") or getattr(event, "type", "")
        payload = copy.deepcopy(getattr(event, "payload", {}) or {})
        channel_type = str(payload.get("channel_type") or "")
        if channel_type != "feishu":
            continue
        if event_type == "notification_sent":
            notifications["feishu"] = {
                "sent": True,
                "failed": False,
                "message": str(payload.get("message") or "飞书通知已发"),
            }
        elif event_type == "notification_failed":
            notifications["feishu"] = {
                "sent": False,
                "failed": True,
                "message": str(payload.get("message") or "飞书通知发送失败"),
            }
    return MappingProxyType(notifications)


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
    if result["execution_type"] not in {"debug", "regression", "baseline_regression"}:
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
    def __init__(
        self,
        session_factory,
        *,
        executor=None,
        event_stream=None,
        failure_analyzer=None,
        failure_analysis_dispatcher=None,
    ):
        self.session_factory = session_factory
        self.event_stream = event_stream or EventStream(session_factory)
        self.executor = executor or HttpExecutor(
            CaseService(session_factory), EnvironmentService(session_factory)
        )
        self.failure_analyzer = failure_analyzer
        self.failure_analysis_dispatcher = failure_analysis_dispatcher

    def submit(self, request, actor_id, idempotency_key, *, task=None):
        parsed = _parse_request(request)
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise ValueError("idempotency key is invalid")
        if not isinstance(actor_id, str) or not actor_id:
            raise ValueError("actor id is required")
        task_snapshot = self._task_snapshot(task)
        fingerprint_payload = (
            parsed if task_snapshot is None else {**parsed, "_task": task_snapshot}
        )
        fingerprint = _fingerprint(fingerprint_payload)
        with self.session_factory.begin() as session:
            repository = ExecutionRepository(session)
            existing = repository.get_by_idempotency(parsed["project_id"], idempotency_key)
            if existing is not None:
                if existing.request_snapshot.get("fingerprint") != fingerprint:
                    raise ExecutionConflictError("idempotency key was used with a different payload")
                children = repository.get_execution_cases(existing.id)
                return self._repository_view(repository, existing, children)
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
            if task_snapshot is not None:
                snapshot["task"] = task_snapshot
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
                children = repository.get_execution_cases(existing.id)
                return self._repository_view(repository, existing, children)
            view = self._repository_view(repository, execution, children)
        self.event_stream.append(view.id, "execution_queued", {"case_count": len(view.case_statuses)})
        return view

    def submit_active_baselines(self, request, actor_id, idempotency_key, *, task=None):
        required_fields = {
            "project_id",
            "source_revision_id",
            "environment_revision_id",
        }
        allowed_fields = required_fields | {"endpoint_ids", "baseline_ids"}
        if not isinstance(request, dict) or not (
            required_fields <= set(request) <= allowed_fields
        ):
            raise ValueError("baseline regression request fields are invalid")
        if not all(
            isinstance(request.get(field), str) and request[field]
            for field in required_fields
        ):
            raise ValueError("baseline regression context is required")
        endpoint_ids = _optional_identifier_array(request.get("endpoint_ids"), "endpoint_ids")
        baseline_ids = _optional_identifier_array(request.get("baseline_ids"), "baseline_ids")
        with self.session_factory() as session:
            version_ids = ExecutionRepository(session).active_baseline_version_ids(
                request["project_id"],
                actor_id,
                endpoint_ids,
                baseline_ids,
            )
        if not version_ids:
            raise ExecutionConflictError(
                "no active baselines match the selected project or baseline selection"
            )
        return self.submit(
            {
                **{field: request[field] for field in required_fields},
                "case_version_ids": list(version_ids),
                "execution_type": "baseline_regression",
                "overrides": {},
            },
            actor_id,
            idempotency_key,
            task=task,
        )

    def get(self, execution_id):
        with self.session_factory() as session:
            repository = ExecutionRepository(session)
            execution = repository.get_execution(execution_id)
            if execution is None:
                raise ExecutionNotFoundError("API execution was not found")
            children = repository.get_execution_cases(execution.id)
            return self._repository_view(repository, execution, children)

    def list(self, project_id, actor_id, limit=50):
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("execution list limit must be between 1 and 100")
        with self.session_factory() as session:
            repository = ExecutionRepository(session)
            return tuple(
                self._repository_view(
                    repository,
                    execution,
                    repository.get_execution_cases(execution.id),
                )
                for execution in repository.list_executions(
                    project_id, actor_id, limit
                )
            )

    def cancel(self, execution_id, actor_id):
        with self.session_factory.begin() as session:
            repository = ExecutionRepository(session)
            execution = repository.request_cancellation(execution_id, actor_id)
            if execution is None:
                raise ExecutionNotFoundError("API execution was not found")
            children = repository.get_execution_cases(execution.id)
            view = self._repository_view(repository, execution, children)
        self.event_stream.signal_cancel(execution_id)
        self.event_stream.append(execution_id, "cancellation_requested", {})
        return view

    def archive(self, execution_id, actor_id):
        with self.session_factory.begin() as session:
            repository = ExecutionRepository(session)
            try:
                execution = repository.archive_execution(execution_id, actor_id)
            except ValueError as error:
                raise ExecutionConflictError(str(error))
            if execution is None:
                raise ExecutionNotFoundError("API execution was not found")
            children = repository.get_execution_cases(execution.id)
            return self._repository_view(repository, execution, children)

    def archive_many(self, execution_ids, actor_id):
        identifiers = tuple(dict.fromkeys(execution_ids))
        if not identifiers:
            raise ValueError("execution_ids must not be empty")
        if len(identifiers) > 200:
            raise ValueError("execution_ids cannot exceed 200")
        with self.session_factory.begin() as session:
            repository = ExecutionRepository(session)
            views = []
            try:
                for execution_id in identifiers:
                    execution = repository.archive_execution(execution_id, actor_id)
                    if execution is None:
                        raise ExecutionNotFoundError("API execution was not found")
                    views.append(
                        self._repository_view(
                            repository,
                            execution,
                            repository.get_execution_cases(execution.id),
                        )
                    )
            except ValueError as error:
                raise ExecutionConflictError(str(error))
            return tuple(views)

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
                attempt_id = self._persist_child_result(execution_id, child.id, result)
                self._dispatch_failure_analysis(
                    execution_id, child.id, attempt_id, result
                )
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
                return repository.create_attempt(current, result, "worker").id
        return None

    def _dispatch_failure_analysis(self, execution_id, child_id, attempt_id, result):
        if (
            self.failure_analysis_dispatcher is None
            or attempt_id is None
            or result.status not in {"FAILED", "BROKEN"}
        ):
            return
        evidence = self._bounded_failure_evidence(redact(result.to_dict()))
        try:
            self.failure_analysis_dispatcher(
                execution_id, child_id, attempt_id, evidence
            )
        except Exception as error:
            self._safe_event(
                execution_id,
                "failure_analysis_unavailable",
                {
                    "execution_case_id": child_id,
                    "error": type(error).__name__,
                },
            )

    def analyze_failure(self, execution_id, child_id, attempt_id, evidence):
        if self.failure_analyzer is None:
            raise RuntimeError("failure analyzer is not configured")
        try:
            payload = self.failure_analyzer.analyze(redact(copy.deepcopy(evidence)))
            with self.session_factory.begin() as session:
                repository = ExecutionRepository(session)
                child = next(
                    item for item in repository.get_execution_cases(execution_id)
                    if item.id == child_id
                )
                analysis = repository.create_failure_analysis(
                    child, attempt_id, payload, "worker"
                )
            self._safe_event(
                execution_id,
                "failure_analysis",
                {
                    "execution_case_id": child_id,
                    "analyzer": analysis.analyzer,
                    "model": analysis.model,
                },
            )
            return True
        except Exception as error:
            self._safe_event(
                execution_id,
                "failure_analysis_unavailable",
                {
                    "execution_case_id": child_id,
                    "error": type(error).__name__,
                },
            )
            return False

    @staticmethod
    def _bounded_failure_evidence(evidence):
        encoded = json.dumps(
            evidence, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        if len(encoded) <= MAX_FAILURE_ANALYSIS_EVIDENCE_BYTES:
            return evidence
        preview = encoded[: MAX_FAILURE_ANALYSIS_EVIDENCE_BYTES // 2].decode(
            "utf-8", errors="ignore"
        )
        return {
            "truncated": True,
            "original_bytes": len(encoded),
            "preview": preview,
        }

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
                or (
                    request["execution_type"] != "baseline_regression"
                    and endpoint.revision_id != source_revision.id
                )
            ):
                raise ValueError("case version does not match source revision and project")
        return {"versions": versions, "endpoints": endpoints}

    @classmethod
    def _repository_view(cls, repository, execution, children):
        return cls._view(
            execution,
            children,
            repository.display_metadata(execution, children),
        )

    @staticmethod
    def _task_snapshot(task):
        if task is None:
            return None
        if isinstance(task, dict):
            raw_id = task.get("id")
            raw_name = task.get("name")
        else:
            raw_id = getattr(task, "id", None)
            raw_name = getattr(task, "name", None)
        if not isinstance(raw_id, str) or not raw_id.strip():
            return None
        name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else "未命名任务"
        return {"id": raw_id.strip(), "name": name[:200]}

    @staticmethod
    def _view(execution, children, display_metadata=None):
        display = display_metadata or {}
        snapshot = getattr(execution, "request_snapshot", {}) or {}
        task = snapshot.get("task", {}) if isinstance(snapshot, dict) else {}
        if not isinstance(task, dict):
            task = {}
        versions = display.get("versions", {})
        cases = display.get("cases", {})
        endpoints = display.get("endpoints", {})
        failure_analyses = display.get("failure_analyses", {})
        events = display.get("events", ())
        return ExecutionView(
            id=execution.id,
            project_id=execution.project_id,
            task_id=task.get("id") if isinstance(task.get("id"), str) else None,
            task_name=task.get("name") if isinstance(task.get("name"), str) else None,
            state=execution.state,
            execution_type=execution.execution_type,
            source_revision_id=execution.source_revision_id,
            environment_revision_id=execution.environment_revision_id,
            environment_name=str(display.get("environment_name", "")),
            case_statuses=tuple(item.status for item in children),
            case_results=tuple(
                MappingProxyType({
                    "execution_case_id": item.id,
                    "case_version_id": item.case_version_id,
                    "endpoint_id": item.endpoint_id,
                    "case_name": (
                        cases.get(versions[item.case_version_id].case_id).name
                        if item.case_version_id in versions
                        and cases.get(versions[item.case_version_id].case_id)
                        else ""
                    ),
                    "endpoint_summary": endpoints[item.endpoint_id].summary
                    if item.endpoint_id in endpoints
                    else "",
                    "method": endpoints[item.endpoint_id].method
                    if item.endpoint_id in endpoints
                    else "",
                    "path": endpoints[item.endpoint_id].path
                    if item.endpoint_id in endpoints
                    else "",
                    "status": item.status,
                    "failure_category": item.failure_category,
                    "duration_ms": item.duration_ms,
                    "sanitized_result": copy.deepcopy(dict(item.sanitized_result)),
                    "failure_analysis": (
                        {
                            "category": failure_analyses[item.id].category,
                            "analyzer": failure_analyses[item.id].analyzer,
                            "model": failure_analyses[item.id].model,
                            "analysis": copy.deepcopy(dict(failure_analyses[item.id].analysis)),
                        }
                        if item.id in failure_analyses else None
                    ),
                })
                for item in children
            ),
            summary=MappingProxyType(copy.deepcopy(dict(execution.summary))),
            notifications=_execution_notification_status(events),
            cancellation_requested=execution.cancellation_requested_at is not None,
            created_at=execution.created_at,
            started_at=execution.started_at,
            finished_at=execution.finished_at,
        )
