"""Transaction-scoped durable API execution state."""

import copy
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import defer

from ..case_classification import is_one_time_case
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


@dataclass(frozen=True)
class ActiveBaselineSelection:
    version_ids: tuple
    excluded_one_time_count: int


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
                select(ApiSourceEndpoint)
                .options(defer(ApiSourceEndpoint.operation, raiseload=True))
                .where(ApiSourceEndpoint.id.in_(identifiers))
            )
        }

    def active_baseline_selection(
        self,
        project_id,
        owner_id,
        endpoint_ids=None,
        baseline_ids=None,
    ):
        statement = (
            select(
                ApiBaseline.case_version_id,
                func.coalesce(
                    ApiCaseVersion.request_template["name"].as_string(),
                    ApiCase.name,
                ),
                ApiBaseline.group_name,
                ApiSourceEndpoint.tags,
            )
            .join(
                ApiCaseVersion,
                ApiCaseVersion.id == ApiBaseline.case_version_id,
            )
            .join(
                ApiSourceEndpoint,
                ApiSourceEndpoint.id == ApiCaseVersion.endpoint_id,
            )
            .join(ApiCase, ApiCase.id == ApiBaseline.case_id)
            .where(
                ApiBaseline.project_id == project_id,
                ApiBaseline.owner_id == owner_id,
                ApiBaseline.status == "active",
            )
            .order_by(ApiBaseline.created_at, ApiBaseline.id)
        )
        identifiers = tuple(dict.fromkeys(endpoint_ids or ()))
        if identifiers:
            statement = statement.where(ApiSourceEndpoint.id.in_(identifiers))
        baseline_identifiers = tuple(dict.fromkeys(baseline_ids or ()))
        if baseline_identifiers:
            statement = statement.where(ApiBaseline.id.in_(baseline_identifiers))
        version_ids = []
        excluded_one_time_count = 0
        for version_id, case_name, group_name, tags in self.session.execute(statement):
            if is_one_time_case(case_name, group_name, tags):
                excluded_one_time_count += 1
                continue
            version_ids.append(version_id)
        return ActiveBaselineSelection(
            version_ids=tuple(version_ids),
            excluded_one_time_count=excluded_one_time_count,
        )

    def get_by_idempotency(self, project_id, key):
        return self.session.scalar(
            select(ApiExecution).where(
                ApiExecution.project_id == project_id,
                ApiExecution.idempotency_key == key,
            )
        )

    def create_execution(
        self,
        request,
        snapshot,
        actor_id,
        idempotency_key,
        *,
        expanded_case_count=None,
    ):
        case_count = (
            int(expanded_case_count)
            if expanded_case_count is not None
            else len(request["case_version_ids"])
        )
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
                "total": case_count,
                "passed": 0,
                "failed": 0,
                "broken": 0,
                "skipped": 0,
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
                    ApiExecution.state != "ARCHIVED",
                )
                .order_by(ApiExecution.created_at.desc(), ApiExecution.id.desc())
                .limit(limit)
            )
        )

    def archive_execution(self, execution_id, actor_id):
        execution = self.get_execution(execution_id, for_update=True)
        if execution is None:
            return None
        if execution.state in {"QUEUED", "RUNNING"}:
            raise ValueError("running executions cannot be archived")
        if execution.state != "ARCHIVED":
            summary = copy.deepcopy(execution.summary or {})
            summary.setdefault("_archived_from_state", execution.state)
            execution.summary = summary
            execution.state = "ARCHIVED"
            execution.updated_by = actor_id
            self.session.flush()
        return execution

    def archive_executions(self, execution_ids, actor_id):
        records = []
        for execution_id in tuple(dict.fromkeys(execution_ids)):
            records.append(self.archive_execution(execution_id, actor_id))
        return tuple(record for record in records if record is not None)

    def display_metadata(self, execution, children, *, include_details=True):
        versions = self.get_case_versions(item.case_version_id for item in children)
        cases = self.get_cases(item.case_id for item in versions.values())
        endpoints = self.get_endpoints(item.endpoint_id for item in children)
        project = self.get_project(execution.project_id)
        environment = self.get_environment_revision(execution.environment_revision_id)
        analyses = (
            self.latest_failure_analyses(item.id for item in children)
            if include_details
            else {}
        )
        return {
            "project_name": project.name if project is not None else "",
            "environment_name": environment.name if environment is not None else "",
            "cases": cases,
            "versions": versions,
            "endpoints": endpoints,
            "failure_analyses": analyses,
            "events": self.read_events(
                execution.id,
                0,
                event_types=("notification_sent", "notification_failed"),
            ),
        }

    def display_metadata_for_execution_list(self, executions, children):
        execution_rows = tuple(executions)
        execution_ids = tuple(item.id for item in execution_rows)
        versions = self.get_case_versions(item.case_version_id for item in children)
        cases = self.get_cases(item.case_id for item in versions.values())
        endpoints = self.get_endpoints(item.endpoint_id for item in children)
        environment_ids = tuple(
            dict.fromkeys(item.environment_revision_id for item in execution_rows)
        )
        environments = {
            item.id: item
            for item in self.session.scalars(
                select(ApiEnvironmentRevision).where(
                    ApiEnvironmentRevision.id.in_(environment_ids)
                )
            )
        } if environment_ids else {}
        events_by_execution = {execution_id: [] for execution_id in execution_ids}
        if execution_ids:
            events = self.session.scalars(
                select(ApiExecutionEvent)
                .where(
                    ApiExecutionEvent.execution_id.in_(execution_ids),
                    ApiExecutionEvent.event_type.in_(
                        ("notification_sent", "notification_failed")
                    ),
                )
                .order_by(ApiExecutionEvent.execution_id, ApiExecutionEvent.sequence)
            )
            for event in events:
                events_by_execution.setdefault(event.execution_id, []).append(event)
        return {
            execution.id: {
                "environment_name": (
                    environments[execution.environment_revision_id].name
                    if execution.environment_revision_id in environments
                    else ""
                ),
                "cases": cases,
                "versions": versions,
                "endpoints": endpoints,
                "failure_analyses": {},
                "events": tuple(events_by_execution.get(execution.id, ())),
            }
            for execution in execution_rows
        }

    def latest_failure_analyses(self, execution_case_ids):
        identifiers = tuple(dict.fromkeys(execution_case_ids))
        if not identifiers:
            return {}
        ranked = (
            select(
                ApiFailureAnalysis.id.label("analysis_id"),
                ApiFailureAnalysis.execution_case_id.label("execution_case_id"),
                func.row_number()
                .over(
                    partition_by=ApiFailureAnalysis.execution_case_id,
                    order_by=(
                        ApiFailureAnalysis.created_at.desc(),
                        ApiFailureAnalysis.id.desc(),
                    ),
                )
                .label("row_number"),
            )
            .where(ApiFailureAnalysis.execution_case_id.in_(identifiers))
            .subquery()
        )
        records = self.session.scalars(
            select(ApiFailureAnalysis)
            .join(ranked, ApiFailureAnalysis.id == ranked.c.analysis_id)
            .where(ranked.c.row_number == 1)
        )
        return {record.execution_case_id: record for record in records}

    def get_execution_cases(
        self,
        execution_id,
        *,
        for_update=False,
        include_evidence=True,
    ):
        query = (
            select(ApiExecutionCase)
            .where(ApiExecutionCase.execution_id == execution_id)
            .order_by(ApiExecutionCase.ordinal)
        )
        if not include_evidence:
            query = query.options(
                defer(ApiExecutionCase.sanitized_result, raiseload=True)
            )
        if for_update:
            query = query.with_for_update()
        return tuple(self.session.scalars(query))

    def get_execution_cases_for_executions(
        self,
        execution_ids,
        *,
        include_evidence=False,
    ):
        identifiers = tuple(dict.fromkeys(execution_ids))
        if not identifiers:
            return ()
        query = (
            select(ApiExecutionCase)
            .where(ApiExecutionCase.execution_id.in_(identifiers))
            .order_by(ApiExecutionCase.execution_id, ApiExecutionCase.ordinal)
        )
        if not include_evidence:
            query = query.options(
                defer(ApiExecutionCase.sanitized_result, raiseload=True)
            )
        return tuple(self.session.scalars(query))

    def get_execution_case(self, execution_id, execution_case_id, *, for_update=False):
        query = select(ApiExecutionCase).where(
            ApiExecutionCase.execution_id == execution_id,
            ApiExecutionCase.id == execution_case_id,
        )
        if for_update:
            query = query.with_for_update()
        return self.session.scalar(query)

    def get_outstanding_execution_cases(self, execution_id, *, for_update=False):
        query = (
            select(ApiExecutionCase)
            .where(
                ApiExecutionCase.execution_id == execution_id,
                ApiExecutionCase.status.in_(("QUEUED", "RUNNING")),
            )
            .options(defer(ApiExecutionCase.sanitized_result, raiseload=True))
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
        counts = {
            "passed": 0,
            "failed": 0,
            "broken": 0,
            "skipped": 0,
            "cancelled": 0,
        }
        total = 0
        for status, count in self.session.execute(
            select(ApiExecutionCase.status, func.count(ApiExecutionCase.id))
            .where(ApiExecutionCase.execution_id == execution_id)
            .group_by(ApiExecutionCase.status)
        ):
            count = int(count or 0)
            total += count
            key = str(status or "").lower()
            if key in counts:
                counts[key] += count
        execution.summary = {"total": total, **counts}
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

    def read_events(self, execution_id, after_id, *, event_types=None):
        query = select(ApiExecutionEvent).where(
            ApiExecutionEvent.execution_id == execution_id,
            ApiExecutionEvent.sequence > after_id,
        )
        event_type_values = tuple(dict.fromkeys(event_types or ()))
        if event_type_values:
            query = query.where(ApiExecutionEvent.event_type.in_(event_type_values))
        return tuple(self.session.scalars(query.order_by(ApiExecutionEvent.sequence)))
