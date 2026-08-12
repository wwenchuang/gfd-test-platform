"""Transaction-scoped durable API execution state."""

import copy
from datetime import datetime, timezone

from sqlalchemy import func, select, update

from ..models.case import ApiBaseline, ApiCase, ApiCaseVersion
from ..models.environment import ApiEnvironment, ApiEnvironmentRevision
from ..models.execution import (
    ApiExecution,
    ApiExecutionAttempt,
    ApiExecutionCase,
    ApiExecutionEvent,
    ApiFailureAnalysis,
)
from ..models.project import ApiProject
from ..models.source import ApiSource, ApiSourceEndpoint, ApiSourceRevision
from .source_repository import audit_fields


def utc_now():
    return datetime.now(timezone.utc)


class ExecutionRepository:
    def __init__(self, session):
        self.session = session

    def get_project(self, record_id):
        return self.session.get(ApiProject, record_id)

    def get_source_revision(self, record_id):
        return self.session.get(ApiSourceRevision, record_id)

    def get_source(self, record_id):
        return self.session.get(ApiSource, record_id)

    def get_environment_revision(self, record_id):
        return self.session.get(ApiEnvironmentRevision, record_id)

    def get_environment(self, record_id):
        return self.session.get(ApiEnvironment, record_id)

    def get_case_versions(self, record_ids):
        identifiers = tuple(dict.fromkeys(record_ids))
        if not identifiers:
            return {}
        return {
            item.id: item
            for item in self.session.scalars(
                select(ApiCaseVersion).where(ApiCaseVersion.id.in_(identifiers))
            )
        }

    def get_cases(self, record_ids):
        identifiers = tuple(dict.fromkeys(record_ids))
        if not identifiers:
            return {}
        return {
            item.id: item
            for item in self.session.scalars(
                select(ApiCase).where(ApiCase.id.in_(identifiers))
            )
        }

    def get_endpoints(self, record_ids):
        identifiers = tuple(dict.fromkeys(record_ids))
        if not identifiers:
            return {}
        return {
            item.id: item
            for item in self.session.scalars(
                select(ApiSourceEndpoint).where(ApiSourceEndpoint.id.in_(identifiers))
            )
        }

    def active_baseline_version_ids(
        self,
        project_id,
        source_revision_id,
        environment_revision_id,
        owner_id,
        endpoint_ids=None,
        baseline_ids=None,
    ):
        statement = (
            select(ApiBaseline.case_version_id)
            .join(
                ApiCaseVersion,
                ApiCaseVersion.id == ApiBaseline.case_version_id,
            )
            .join(
                ApiSourceEndpoint,
                ApiSourceEndpoint.id == ApiCaseVersion.endpoint_id,
            )
            .where(
                ApiBaseline.project_id == project_id,
                ApiBaseline.environment_revision_id == environment_revision_id,
                ApiBaseline.owner_id == owner_id,
                ApiBaseline.status == "active",
                ApiSourceEndpoint.revision_id == source_revision_id,
            )
            .order_by(ApiBaseline.created_at, ApiBaseline.id)
        )
        identifiers = tuple(dict.fromkeys(endpoint_ids or ()))
        if identifiers:
            statement = statement.where(ApiSourceEndpoint.id.in_(identifiers))
        baseline_identifiers = tuple(dict.fromkeys(baseline_ids or ()))
        if baseline_identifiers:
            statement = statement.where(ApiBaseline.id.in_(baseline_identifiers))
        return tuple(self.session.scalars(statement))

    def get_by_idempotency(self, project_id, key):
        return self.session.scalar(
            select(ApiExecution).where(
                ApiExecution.project_id == project_id,
                ApiExecution.idempotency_key == key,
            )
        )

    def create_execution(self, request, snapshot, actor_id, idempotency_key):
        record = ApiExecution(
            project_id=request["project_id"],
            source_revision_id=request["source_revision_id"],
            environment_revision_id=request["environment_revision_id"],
            execution_type=request["execution_type"],
            state="QUEUED",
            idempotency_key=idempotency_key,
            requested_case_ids=copy.deepcopy(request["case_version_ids"]),
            request_snapshot=copy.deepcopy(snapshot),
            summary={
                "total": len(request["case_version_ids"]),
                "passed": 0,
                "failed": 0,
                "broken": 0,
                "cancelled": 0,
            },
            **audit_fields(actor_id),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def create_execution_case(self, execution, version, endpoint, ordinal, actor_id):
        record = ApiExecutionCase(
            execution_id=execution.id,
            case_version_id=version.id,
            endpoint_id=endpoint.id,
            environment_revision_id=execution.environment_revision_id,
            ordinal=ordinal,
            status="QUEUED",
            sanitized_result={},
            **audit_fields(actor_id),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def get_execution(self, execution_id, *, for_update=False):
        query = select(ApiExecution).where(ApiExecution.id == execution_id)
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def list_executions(self, project_id, owner_id, limit=50):
        return tuple(
            self.session.scalars(
                select(ApiExecution)
                .where(
                    ApiExecution.project_id == project_id,
                    ApiExecution.owner_id == owner_id,
                )
                .order_by(ApiExecution.created_at.desc(), ApiExecution.id.desc())
                .limit(limit)
            )
        )

    def display_metadata(self, execution, children):
        versions = self.get_case_versions(item.case_version_id for item in children)
        cases = self.get_cases(item.case_id for item in versions.values())
        endpoints = self.get_endpoints(item.endpoint_id for item in children)
        environment = self.get_environment_revision(execution.environment_revision_id)
        analyses = self.latest_failure_analyses(item.id for item in children)
        return {
            "environment_name": environment.name if environment is not None else "",
            "cases": cases,
            "versions": versions,
            "endpoints": endpoints,
            "failure_analyses": analyses,
        }

    def latest_failure_analyses(self, execution_case_ids):
        identifiers = tuple(dict.fromkeys(execution_case_ids))
        if not identifiers:
            return {}
        records = self.session.scalars(
            select(ApiFailureAnalysis)
            .where(ApiFailureAnalysis.execution_case_id.in_(identifiers))
            .order_by(ApiFailureAnalysis.created_at.desc(), ApiFailureAnalysis.id.desc())
        )
        output = {}
        for record in records:
            output.setdefault(record.execution_case_id, record)
        return output

    def get_execution_cases(self, execution_id, *, for_update=False):
        query = (
            select(ApiExecutionCase)
            .where(ApiExecutionCase.execution_id == execution_id)
            .order_by(ApiExecutionCase.ordinal)
        )
        if for_update:
            query = query.with_for_update()
        return tuple(self.session.scalars(query))

    def compare_and_set_execution(self, execution_id, expected, target):
        values = {"state": target, "row_version": ApiExecution.row_version + 1}
        now = utc_now()
        if target == "RUNNING":
            values["started_at"] = now
        if target in {"DONE", "CANCELLED"}:
            values["finished_at"] = now
        result = self.session.execute(
            update(ApiExecution)
            .where(ApiExecution.id == execution_id, ApiExecution.state.in_(tuple(expected)))
            .values(**values)
        )
        return result.rowcount == 1

    def compare_and_set_case(self, execution_case_id, expected, target):
        result = self.session.execute(
            update(ApiExecutionCase)
            .where(
                ApiExecutionCase.id == execution_case_id,
                ApiExecutionCase.status.in_(tuple(expected)),
            )
            .values(status=target, row_version=ApiExecutionCase.row_version + 1)
        )
        return result.rowcount == 1

    def request_cancellation(self, execution_id, actor_id):
        execution = self.get_execution(execution_id, for_update=True)
        if execution is None:
            return None
        if execution.cancellation_requested_at is None:
            execution.cancellation_requested_at = utc_now()
            execution.updated_by = actor_id
        if execution.state == "QUEUED":
            execution.state = "CANCELLED"
            execution.finished_at = utc_now()
            for child in self.get_execution_cases(execution.id, for_update=True):
                if child.status == "QUEUED":
                    child.status = "CANCELLED"
                    child.failure_category = "cancelled"
            return self.finalize_execution(execution.id, actor_id)
        self.session.flush()
        return execution

    def cancellation_requested(self, execution_id):
        value = self.session.scalar(
            select(ApiExecution.cancellation_requested_at).where(
                ApiExecution.id == execution_id
            )
        )
        return value is not None

    def create_attempt(self, execution_case, result, actor_id):
        attempt_number = int(
            self.session.scalar(
                select(func.max(ApiExecutionAttempt.attempt_number)).where(
                    ApiExecutionAttempt.execution_case_id == execution_case.id
                )
            )
            or 0
        ) + 1
        record = ApiExecutionAttempt(
            execution_case_id=execution_case.id,
            attempt_number=attempt_number,
            status=result.status,
            request=copy.deepcopy(result.sanitized_request),
            response=copy.deepcopy(result.sanitized_response),
            assertion_results=[
                copy.deepcopy(item.__dict__) if hasattr(item, "__dict__") else copy.deepcopy(item)
                for item in result.assertion_results
            ],
            timing={"duration_ms": result.duration_ms},
            error_message=result.error_message,
            **audit_fields(actor_id),
        )
        self.session.add(record)
        execution_case.status = result.status
        execution_case.failure_category = result.failure_category
        execution_case.duration_ms = result.duration_ms
        execution_case.sanitized_result = copy.deepcopy(result.to_dict())
        execution_case.updated_by = actor_id
        self.session.flush()
        return record

    def create_failure_analysis(self, execution_case, attempt_id, payload, actor_id):
        record = ApiFailureAnalysis(
            execution_case_id=execution_case.id,
            attempt_id=attempt_id,
            category=execution_case.failure_category or "unknown",
            analyzer=payload["analyzer"],
            model=payload["model"],
            analysis=copy.deepcopy(payload["analysis"]),
            **audit_fields(actor_id),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def finalize_execution(self, execution_id, actor_id):
        execution = self.get_execution(execution_id, for_update=True)
        children = self.get_execution_cases(execution_id, for_update=True)
        counts = {"passed": 0, "failed": 0, "broken": 0, "cancelled": 0}
        for child in children:
            key = child.status.lower()
            if key in counts:
                counts[key] += 1
        execution.summary = {"total": len(children), **counts}
        execution.state = (
            "CANCELLED" if execution.cancellation_requested_at is not None else "DONE"
        )
        execution.finished_at = utc_now()
        execution.updated_by = actor_id
        self.session.flush()
        return execution

    def append_event(self, execution_id, event_type, payload, actor_id="system"):
        execution = self.get_execution(execution_id, for_update=True)
        if execution is None:
            raise LookupError("API execution was not found")
        sequence = int(
            self.session.scalar(
                select(func.max(ApiExecutionEvent.sequence)).where(
                    ApiExecutionEvent.execution_id == execution_id
                )
            )
            or 0
        ) + 1
        record = ApiExecutionEvent(
            execution_id=execution_id,
            sequence=sequence,
            event_type=event_type,
            payload=copy.deepcopy(payload),
            **audit_fields(actor_id),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def read_events(self, execution_id, after_id):
        return tuple(
            self.session.scalars(
                select(ApiExecutionEvent)
                .where(
                    ApiExecutionEvent.execution_id == execution_id,
                    ApiExecutionEvent.sequence > after_id,
                )
                .order_by(ApiExecutionEvent.sequence)
            )
        )
