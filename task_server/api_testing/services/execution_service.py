"""Idempotent API execution orchestration and truthful aggregation."""

import copy
from dataclasses import dataclass
import hashlib
import json
from types import MappingProxyType
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy import select
from ..models.case import ApiBaseline

from .. import access

from ..events import EventStream
from ..executor import CaseExecutionResult, HttpExecutor, redact
from ..repositories.execution_repository import ExecutionRepository
from ...services.notification_presentation import canonical_test_scope_summary
from ...services.business_line_service import resolve_test_application
from .case_service import CaseService
from .environment_service import EnvironmentService
from .test_scope_service import InactiveTestScopeError, ensure_active_case_version_scopes


MAX_FAILURE_ANALYSIS_EVIDENCE_BYTES = 128 * 1024
MAX_FAILURE_ANALYSIS_DISPATCH_CASES = 50
MAX_EXECUTION_CASES = 500


class ExecutionConflictError(ValueError):
    pass


class BaselineRequiredError(ExecutionConflictError):
    pass


class OneTimeBaselineConflictError(ExecutionConflictError):
    pass


class ExecutionScopeConflictError(ExecutionConflictError):
    pass


class ExecutionNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class ExecutionView:
    id: str
    project_id: str
    task_id: Optional[str]
    task_name: Optional[str]
    task_type: Optional[str]
    execution_source: str
    state: str
    execution_type: str
    source_revision_id: str
    environment_revision_id: str
    environment_name: str
    application_name: str
    business_name: str
    case_statuses: tuple
    case_results: tuple
    summary: MappingProxyType
    notifications: MappingProxyType
    cancellation_requested: bool
    created_at: object
    started_at: object
    finished_at: object


@dataclass(frozen=True)
class DependencyOutcome:
    status: str
    extracted_variables: dict


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
    if result["execution_type"] not in {"debug", "regression", "baseline_regression", "scheduled"}:
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
            access.require_resource(session, repository.get_project(parsed["project_id"]), actor_id, "api.execute")
            access.require_execution_environment(session, parsed["environment_revision_id"], actor_id, parsed["project_id"])
            existing = repository.get_by_idempotency(parsed["project_id"], idempotency_key)
            if existing is not None:
                access.require_resource(session, existing, actor_id, "api.execute")
                if access.get_access_profile(actor_id) is not None and existing.created_by != actor_id:
                    raise ExecutionConflictError("idempotency key belongs to another actor")
                if existing.request_snapshot.get("fingerprint") != fingerprint:
                    raise ExecutionConflictError("idempotency key was used with a different payload")
                children = repository.get_execution_cases(existing.id)
                return self._repository_view(repository, existing, children)
            context = self._validate_snapshot(repository, parsed)
            try:
                ensure_active_case_version_scopes(context["versions"])
            except InactiveTestScopeError as exc:
                raise ExecutionScopeConflictError(str(exc)) from exc
            snapshot = {
                "fingerprint": fingerprint,
                "request": redact(parsed),
                "case_versions": [
                    self._case_version_snapshot(
                        version,
                        requested_version_ids=context["requested_version_ids"],
                    )
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
                        parsed,
                        snapshot,
                        actor_id,
                        idempotency_key,
                        expanded_case_count=len(context["versions"]),
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
                access.require_resource(session, existing, actor_id, "api.execute")
                if access.get_access_profile(actor_id) is not None and existing.created_by != actor_id:
                    raise ExecutionConflictError("idempotency key belongs to another actor")
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
        access.require_permission(actor_id, "api.execute")
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
            if baseline_ids:
                allowed = set(session.scalars(select(ApiBaseline.id).where(
                    ApiBaseline.id.in_(baseline_ids), ApiBaseline.project_id == request["project_id"],
                    ApiBaseline.status == "active", access.resource_predicate(actor_id, ApiBaseline),
                )))
                if allowed != set(baseline_ids):
                    raise ExecutionNotFoundError("API baseline was not found")
            baseline_selection = ExecutionRepository(session).active_baseline_selection(
                request["project_id"],
                actor_id,
                endpoint_ids,
                baseline_ids,
            )
        if baseline_ids and baseline_selection.excluded_one_time_count:
            raise OneTimeBaselineConflictError(
                "所选基线包含一次性用例。一次性用例仅供人工调试，不会进入批量或定时回归；请取消选择后重试"
            )
        version_ids = baseline_selection.version_ids
        if not version_ids:
            raise BaselineRequiredError(
                "当前项目或所选范围没有可执行的活动基线；一次性用例仅供人工调试，请先采纳常规用例基线"
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

    def get(self, execution_id, *, include_evidence=True):
        with self.session_factory() as session:
            repository = ExecutionRepository(session)
            execution = repository.get_execution(execution_id)
            if execution is None:
                raise ExecutionNotFoundError("API execution was not found")
            children = repository.get_execution_cases(
                execution.id,
                include_evidence=include_evidence,
            )
            return self._repository_view(
                repository,
                execution,
                children,
                include_evidence=include_evidence,
            )

    def get_case_result(self, execution_id, execution_case_id):
        with self.session_factory() as session:
            repository = ExecutionRepository(session)
            execution = repository.get_execution(execution_id)
            if execution is None:
                raise ExecutionNotFoundError("API execution was not found")
            child = repository.get_execution_case(execution_id, execution_case_id)
            if child is None:
                raise ExecutionNotFoundError("API execution case was not found")
            view = self._repository_view(repository, execution, (child,))
            return view.case_results[0]

    def list(self, project_id, actor_id, limit=50):
        access.require_permission(actor_id, "api.view")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise ValueError("execution list limit must be between 1 and 100")
        with self.session_factory() as session:
            repository = ExecutionRepository(session)
            executions = repository.list_executions(project_id, actor_id, limit)
            children = repository.get_execution_cases_for_executions(
                (execution.id for execution in executions),
                include_evidence=False,
            )
            children_by_execution = {execution.id: [] for execution in executions}
            for child in children:
                children_by_execution.setdefault(child.execution_id, []).append(child)
            metadata = repository.display_metadata_for_execution_list(
                executions,
                children,
            )
            return tuple(
                self._view(
                    execution,
                    tuple(children_by_execution.get(execution.id, ())),
                    metadata.get(execution.id, {}),
                    include_evidence=False,
                    include_failure_analysis=False,
                )
                for execution in executions
            )

    def cancel(self, execution_id, actor_id):
        access.require_permission(actor_id, "api.execute")
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
        access.require_permission(actor_id, "api.delete")
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
        access.require_permission(actor_id, "api.delete")
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
                            repository.get_execution_cases(
                                execution.id,
                                include_evidence=False,
                            ),
                            include_evidence=False,
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
            try:
                access.require_resource(session, execution, execution.created_by, "api.execute")
                access.require_execution_environment(session, execution.environment_revision_id, execution.created_by, execution.project_id)
            except access.AccessDeniedError:
                repository.request_cancellation(execution.id, execution.created_by, authorize=False)
                return False
            if execution.cancellation_requested_at is not None:
                return False
            if not repository.compare_and_set_execution(execution_id, {"QUEUED"}, "RUNNING"):
                return False
            snapshot = copy.deepcopy(execution.request_snapshot)
            children = repository.get_execution_cases(
                execution_id,
                include_evidence=False,
            )
        worker_error = None
        outcomes = {}
        version_snapshots = {
            item.get("id"): item
            for item in snapshot.get("case_versions", ())
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        }
        required_exports = self._required_dependency_exports(
            version_snapshots.values()
        )
        try:
            self.event_stream.append(execution_id, "execution_started", {})
            allow_failure_analysis = self._should_dispatch_failure_analysis(children)
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
                    child_snapshot = version_snapshots.get(child.case_version_id, {})
                    dependency_overrides, blocked_reason = self._dependency_overrides(
                        child_snapshot,
                        outcomes,
                    )
                    result = (
                        self._skipped_result(blocked_reason)
                        if blocked_reason
                        else self._execute_child(
                            execution_id,
                            child,
                            snapshot["request"]["overrides"],
                            dependency_overrides,
                        )
                    )
                except Exception as exc:
                    worker_error = exc
                    result = self._broken_result(exc)
                outcomes[child.case_version_id] = self._dependency_outcome(
                    result,
                    required_exports.get(child.case_version_id, ()),
                )
                attempt_id = self._persist_child_result(execution_id, child.id, result)
                self._dispatch_failure_analysis(
                    execution_id,
                    child.id,
                    attempt_id,
                    result,
                    allow=allow_failure_analysis,
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

    def _execute_child(
        self,
        execution_id,
        child,
        overrides,
        dependency_overrides,
    ):
        def phase_callback(phase, payload):
            self.event_stream.append(
                execution_id,
                phase,
                {"execution_case_id": child.id, **copy.deepcopy(payload)},
            )

        keyword_arguments = {
            "cancellation_check": lambda _phase: self._is_cancelled(execution_id),
            "phase_callback": phase_callback,
        }
        if dependency_overrides:
            keyword_arguments["dependency_overrides"] = copy.deepcopy(
                dependency_overrides
            )
        return self.executor.execute_case(
            child.case_version_id,
            child.environment_revision_id,
            copy.deepcopy(overrides),
            **keyword_arguments,
        )

    def _persist_child_result(self, execution_id, child_id, result):
        with self.session_factory.begin() as session:
            repository = ExecutionRepository(session)
            current = repository.get_execution_case(
                execution_id,
                child_id,
                for_update=True,
            )
            if current is not None and current.status == "RUNNING":
                return repository.create_attempt(current, result, "worker").id
        return None

    @staticmethod
    def _should_dispatch_failure_analysis(children):
        return len(children or ()) <= MAX_FAILURE_ANALYSIS_DISPATCH_CASES

    def _dispatch_failure_analysis(
        self, execution_id, child_id, attempt_id, result, *, allow=True
    ):
        if (
            not allow
            or
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
                child = repository.get_execution_case(
                    execution_id,
                    child_id,
                )
                if child is None:
                    raise ExecutionNotFoundError("API execution case was not found")
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
            for child in repository.get_outstanding_execution_cases(
                execution_id,
                for_update=True,
            ):
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
            for child in repository.get_outstanding_execution_cases(
                execution_id,
                for_update=True,
            ):
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

    @staticmethod
    def _skipped_result(reason):
        message = str(reason or "required dependency was not satisfied")
        return CaseExecutionResult(
            status="SKIPPED",
            failure_category="dependency",
            duration_ms=0,
            sanitized_request={},
            sanitized_response={},
            assertion_results=(),
            extracted_variables={},
            error_message=message,
            trace=(
                {
                    "phase": "skip",
                    "status": "SKIPPED",
                    "failure_category": "dependency",
                    "skip_reason": message,
                },
            ),
        )

    @classmethod
    def _dependency_overrides(cls, version_snapshot, outcomes):
        overrides = {}
        dependencies = version_snapshot.get("dependencies", ())
        if not isinstance(dependencies, (list, tuple)):
            dependencies = ()
        for dependency in dependencies:
            if not isinstance(dependency, dict):
                continue
            dependency_id = dependency.get("case_version_id")
            if not isinstance(dependency_id, str) or not dependency_id:
                continue
            required = dependency.get("required", True) is not False
            result = outcomes.get(dependency_id)
            if result is None or result.status != "PASSED":
                if required:
                    status = result.status if result is not None else "MISSING"
                    return (
                        overrides,
                        f"required dependency {dependency_id} finished with {status}",
                    )
                continue
            exported = result.extracted_variables or {}
            export_names = dependency.get("exports", ())
            if not isinstance(export_names, (list, tuple)):
                export_names = ()
            missing = []
            for name in export_names:
                if not isinstance(name, str) or not name:
                    continue
                if name not in exported:
                    missing.append(name)
                    continue
                overrides[name] = copy.deepcopy(exported[name])
            if required and missing:
                return (
                    overrides,
                    "required dependency "
                    f"{dependency_id} did not export {', '.join(missing)}",
                )
        return overrides, ""

    @staticmethod
    def _required_dependency_exports(version_snapshots):
        output = {}
        for snapshot in version_snapshots:
            if not isinstance(snapshot, dict):
                continue
            dependencies = snapshot.get("dependencies", ())
            if not isinstance(dependencies, (list, tuple)):
                continue
            for dependency in dependencies:
                if not isinstance(dependency, dict):
                    continue
                dependency_id = dependency.get("case_version_id")
                if not isinstance(dependency_id, str) or not dependency_id:
                    continue
                names = output.setdefault(dependency_id, set())
                for name in dependency.get("exports", ()):
                    if isinstance(name, str) and name:
                        names.add(name)
        return output

    @staticmethod
    def _dependency_outcome(result, required_exports):
        extracted = result.extracted_variables or {}
        return DependencyOutcome(
            status=result.status,
            extracted_variables={
                name: copy.deepcopy(extracted[name])
                for name in required_exports
                if name in extracted
            },
        )

    def _is_cancelled(self, execution_id):
        with self.session_factory() as session:
            return ExecutionRepository(session).cancellation_requested(execution_id)

    @classmethod
    def _validate_snapshot(cls, repository, request):
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
        versions = cls._expand_dependency_versions(
            repository,
            request["case_version_ids"],
        )
        requested_version_ids = frozenset(request["case_version_ids"])
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
                    not (
                        request["execution_type"] == "baseline_regression"
                        and version.id in requested_version_ids
                    )
                    and endpoint.revision_id != source_revision.id
                )
            ):
                raise ValueError("case version does not match source revision and project")
        return {
            "versions": versions,
            "endpoints": endpoints,
            "requested_version_ids": requested_version_ids,
        }

    @classmethod
    def _expand_dependency_versions(cls, repository, requested_ids):
        cache = repository.get_case_versions(requested_ids)
        if len(cache) != len(requested_ids):
            raise ValueError("case version was not found")
        ordered = []
        visited = set()
        visiting = []

        def load(version_id):
            if version_id not in cache:
                cache.update(repository.get_case_versions([version_id]))
            version = cache.get(version_id)
            if version is None:
                raise ValueError(f"dependency case version was not found: {version_id}")
            return version

        def visit(version_id):
            if version_id in visited:
                return
            if version_id in visiting:
                cycle = visiting[visiting.index(version_id):] + [version_id]
                raise ValueError(
                    "cyclic case dependency: " + " -> ".join(cycle)
                )
            visiting.append(version_id)
            version = load(version_id)
            for dependency in cls._case_dependencies(version):
                visit(dependency["case_version_id"])
            visiting.pop()
            visited.add(version_id)
            ordered.append(version)
            if len(ordered) > MAX_EXECUTION_CASES:
                raise ValueError(
                    f"expanded case dependencies cannot exceed {MAX_EXECUTION_CASES}"
                )

        for requested_id in requested_ids:
            visit(requested_id)
        return ordered

    @staticmethod
    def _case_dependencies(version):
        spec = getattr(version, "dependency_spec", {}) or {}
        dependencies = spec.get("dependencies", ()) if isinstance(spec, dict) else ()
        if not isinstance(dependencies, (list, tuple)):
            raise ValueError("case dependencies must be an array")
        output = []
        for dependency in dependencies:
            if not isinstance(dependency, dict):
                raise ValueError("case dependency must be an object")
            identifier = dependency.get("case_version_id")
            if not isinstance(identifier, str) or not identifier:
                raise ValueError("case dependency version is required")
            exports = dependency.get("exports", ())
            if not isinstance(exports, (list, tuple)):
                raise ValueError("case dependency exports must be an array")
            output.append(
                {
                    "case_version_id": identifier,
                    "required": dependency.get("required", True) is not False,
                    "exports": [
                        name for name in exports if isinstance(name, str) and name
                    ],
                }
            )
        return output

    @classmethod
    def _case_version_snapshot(cls, version, requested_version_ids):
        request_template = getattr(version, "request_template", {}) or {}
        application = request_template if isinstance(request_template, dict) else {}
        configured_application = resolve_test_application(
            application.get("app_package"),
            application.get("app_name"),
            application.get("business"),
            include_disabled=True,
        )
        return {
            "id": version.id,
            "case_id": version.case_id,
            "endpoint_id": version.endpoint_id,
            "version": version.version_number,
            "role": "requested" if version.id in requested_version_ids else "dependency",
            "app_package": str(
                application.get("app_package")
                or configured_application.get("package")
                or ""
            ).strip(),
            "app_name": str(
                application.get("app_name")
                or configured_application.get("name")
                or ""
            ).strip(),
            "business": str(application.get("business") or "").strip(),
            "dependencies": cls._case_dependencies(version),
        }

    @classmethod
    def _repository_view(
        cls,
        repository,
        execution,
        children,
        *,
        include_evidence=True,
        include_failure_analysis=True,
    ):
        return cls._view(
            execution,
            children,
            repository.display_metadata(
                execution,
                children,
                include_details=include_failure_analysis,
            ),
            include_evidence=include_evidence,
            include_failure_analysis=include_failure_analysis,
        )

    @staticmethod
    def _task_snapshot(task):
        if task is None:
            return None
        task_type = None
        source = None
        if isinstance(task, dict):
            raw_id = task.get("id")
            raw_name = task.get("name")
            task_type = task.get("type")
            source = task.get("source")
            notify_feishu = task.get("notify_feishu")
        else:
            raw_id = getattr(task, "id", None)
            raw_name = getattr(task, "name", None)
            task_type = getattr(task, "type", None)
            source = getattr(task, "source", None)
            notify_feishu = getattr(task, "notify_feishu", None)
        if not isinstance(raw_id, str) or not raw_id.strip():
            return None
        name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else "未命名任务"
        snapshot = {"id": raw_id.strip(), "name": name[:200]}
        if isinstance(task_type, str) and task_type.strip():
            snapshot["type"] = task_type.strip()[:32]
        if isinstance(source, str) and source.strip():
            snapshot["source"] = source.strip()[:32]
        if isinstance(notify_feishu, bool):
            snapshot["notify_feishu"] = notify_feishu
        return snapshot

    @staticmethod
    def _view(
        execution,
        children,
        display_metadata=None,
        *,
        include_evidence=True,
        include_failure_analysis=True,
    ):
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
        snapshot_versions = snapshot.get("case_versions", ()) if isinstance(snapshot, dict) else ()
        application_name, business_name = canonical_test_scope_summary(
            snapshot_versions,
            fallback_package="",
        )
        execution_roles = {
            item.get("id"): item.get("role")
            for item in snapshot_versions
            if isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and item.get("role") in {"requested", "dependency"}
        }
        return ExecutionView(
            id=execution.id,
            project_id=execution.project_id,
            task_id=task.get("id") if isinstance(task.get("id"), str) else None,
            task_name=task.get("name") if isinstance(task.get("name"), str) else None,
            task_type=task.get("type") if isinstance(task.get("type"), str) else None,
            execution_source=(
                task.get("source")
                if isinstance(task.get("source"), str)
                else {
                    "debug": "online_debug",
                    "baseline_regression": "baseline_regression",
                    "scheduled": "scheduled_job",
                }.get(execution.execution_type, "manual_task")
            ),
            state=execution.state,
            execution_type=execution.execution_type,
            source_revision_id=execution.source_revision_id,
            environment_revision_id=execution.environment_revision_id,
            environment_name=str(display.get("environment_name", "")),
            application_name=application_name,
            business_name=business_name,
            case_statuses=tuple(item.status for item in children),
            case_results=tuple(
                MappingProxyType({
                    "execution_case_id": item.id,
                    "case_version_id": item.case_version_id,
                    "execution_role": execution_roles.get(
                        item.case_version_id,
                        "requested",
                    ),
                    "endpoint_id": item.endpoint_id,
                    "endpoint_stable_key": endpoints[item.endpoint_id].stable_key
                    if item.endpoint_id in endpoints
                    else "",
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
                    "sanitized_result": (
                        copy.deepcopy(dict(item.sanitized_result))
                        if include_evidence
                        else {}
                    ),
                    "evidence_loaded": include_evidence,
                    "failure_analysis": (
                        {
                            "category": failure_analyses[item.id].category,
                            "analyzer": failure_analyses[item.id].analyzer,
                            "model": failure_analyses[item.id].model,
                            "analysis": copy.deepcopy(dict(failure_analyses[item.id].analysis)),
                        }
                        if include_failure_analysis and item.id in failure_analyses else None
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
