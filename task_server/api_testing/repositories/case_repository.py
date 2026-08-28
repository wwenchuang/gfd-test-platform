"""Transaction-scoped persistence for API case versions and baselines."""

import copy

from sqlalchemy import func, select

from ..models.case import (
    ApiBaseline,
    ApiCase,
    ApiCaseAssertion,
    ApiCaseDataRow,
    ApiCaseExtraction,
    ApiCaseScript,
    ApiCaseVersion,
)
from ..models.environment import ApiEnvironment, ApiEnvironmentRevision
from ..models.execution import ApiExecution, ApiExecutionAttempt, ApiExecutionCase
from ..models.project import ApiProject
from ..models.source import ApiSource, ApiSourceEndpoint, ApiSourceRevision
from .source_repository import audit_fields


class CaseRepository:
    def __init__(self, session):
        self.session = session

    def get_endpoint(self, endpoint_id):
        return self.session.get(ApiSourceEndpoint, endpoint_id)

    def get_source_revision(self, revision_id):
        return self.session.get(ApiSourceRevision, revision_id)

    def get_source(self, source_id):
        return self.session.get(ApiSource, source_id)

    def get_case(self, case_id):
        return self.session.get(ApiCase, case_id)

    def get_case_for_update(self, case_id):
        return self.session.scalar(
            select(ApiCase).where(ApiCase.id == case_id).with_for_update()
        )

    def list_active_versions_for_source_revision(self, revision_id, actor_id):
        current_revision = self.get_source_revision(revision_id)
        if current_revision is None:
            return ()
        current_endpoints = {
            item.stable_key: item
            for item in self.session.scalars(
                select(ApiSourceEndpoint).where(
                    ApiSourceEndpoint.revision_id == revision_id
                )
            )
        }
        rows = self.session.execute(
            select(ApiCaseVersion, ApiCase, ApiSourceEndpoint, ApiSourceRevision)
            .join(ApiCase, ApiCase.id == ApiCaseVersion.case_id)
            .join(ApiSourceEndpoint, ApiSourceEndpoint.id == ApiCaseVersion.endpoint_id)
            .join(ApiSourceRevision, ApiSourceRevision.id == ApiSourceEndpoint.revision_id)
            .join(ApiSource, ApiSource.id == ApiSourceRevision.source_id)
            .join(ApiProject, ApiProject.id == ApiSource.project_id)
            .where(
                ApiSource.id == current_revision.source_id,
                ApiProject.owner_id == actor_id,
                ApiCase.owner_id == actor_id,
                ApiCase.project_id == ApiProject.id,
                ApiCase.endpoint_id == ApiSourceEndpoint.id,
                ApiCase.status != "archived",
                ApiCaseVersion.endpoint_id == ApiCase.endpoint_id,
                ApiCase.active_version_id == ApiCaseVersion.id,
            )
        )
        projected = []
        for version, case, historical_endpoint, historical_revision in rows:
            current_endpoint = current_endpoints.get(historical_endpoint.stable_key)
            if current_endpoint is None:
                continue
            projected.append(
                (
                    version,
                    case,
                    current_endpoint,
                    "current"
                    if historical_revision.id == revision_id
                    else "needs_adaptation",
                )
            )
        projected.sort(
            key=lambda item: (
                item[2].normalized_path,
                item[2].method,
                item[1].name,
                item[1].id,
            )
        )
        return tuple(projected)

    def case_lifecycle(self, case_ids, actor_id):
        identifiers = tuple(set(case_ids))
        lifecycle = {case_id: {} for case_id in identifiers}
        if not identifiers:
            return lifecycle
        baseline_rows = self.session.execute(
            select(ApiBaseline)
            .where(
                ApiBaseline.case_id.in_(identifiers),
                ApiBaseline.owner_id == actor_id,
                ApiBaseline.status == "active",
            )
            .distinct(ApiBaseline.case_id)
            .order_by(ApiBaseline.case_id, ApiBaseline.created_at.desc())
        ).scalars()
        for baseline in baseline_rows:
            lifecycle[baseline.case_id].update(
                {
                    "baseline_id": baseline.id,
                    "baseline_status": baseline.status,
                    "baseline_adopted_at": baseline.created_at,
                }
            )

        execution_rows = self.session.execute(
            select(ApiCaseVersion.case_id, ApiExecutionCase, ApiExecution)
            .join(ApiExecutionCase, ApiExecutionCase.case_version_id == ApiCaseVersion.id)
            .join(ApiExecution, ApiExecution.id == ApiExecutionCase.execution_id)
            .where(
                ApiCaseVersion.case_id.in_(identifiers),
                ApiExecution.owner_id == actor_id,
            )
            .distinct(ApiCaseVersion.case_id, ApiExecution.execution_type)
            .order_by(
                ApiCaseVersion.case_id,
                ApiExecution.execution_type,
                ApiExecutionCase.created_at.desc(),
            )
        )
        for case_id, execution_case, execution in execution_rows:
            if execution.execution_type == "debug":
                lifecycle[case_id].update(
                    {
                        "debug_status": execution_case.status,
                        "debug_execution_id": execution.id,
                        "debugged_at": execution_case.created_at,
                    }
                )
            else:
                current = lifecycle[case_id].get("regressed_at")
                if current is None or execution_case.created_at > current:
                    lifecycle[case_id].update(
                        {
                            "regression_status": execution_case.status,
                            "regression_execution_id": execution.id,
                            "regressed_at": execution_case.created_at,
                        }
                    )
        return lifecycle

    def create_case(self, project_id, endpoint_id, name, origin, actor_id):
        record = ApiCase(
            project_id=project_id,
            endpoint_id=endpoint_id,
            name=name,
            origin=origin,
            status="draft",
            **audit_fields(actor_id),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def next_version_number(self, case_id):
        current = self.session.scalar(
            select(func.max(ApiCaseVersion.version_number)).where(
                ApiCaseVersion.case_id == case_id
            )
        )
        return int(current or 0) + 1

    def create_version(self, case, payload, version_number, actor_id, group_name=""):
        request_template = {
            "name": payload["name"],
            "app_package": payload["app_package"],
            "app_name": payload["app_name"],
            "business": payload.get("business", ""),
            "request": copy.deepcopy(payload["request"]),
        }
        record = ApiCaseVersion(
            case_id=case.id,
            endpoint_id=case.endpoint_id,
            version_number=version_number,
            status="draft",
            purpose=payload["purpose"],
            priority=payload["priority"],
            group_name=group_name,
            request_template=request_template,
            validation_summary={},
            dependency_spec={"dependencies": copy.deepcopy(payload["dependencies"])},
            processing_spec=copy.deepcopy(payload["processing"]),
            **audit_fields(actor_id),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def add_data_rows(self, version_id, rows, actor_id):
        for sequence, row in enumerate(rows):
            self.session.add(
                ApiCaseDataRow(
                    case_version_id=version_id,
                    name=row["name"],
                    values=copy.deepcopy(row["values"]),
                    enabled=row["enabled"],
                    sequence=sequence,
                    **audit_fields(actor_id),
                )
            )

    def add_assertions(self, version_id, assertions, actor_id):
        for sequence, assertion in enumerate(assertions):
            definition = copy.deepcopy(assertion)
            assertion_type = definition.pop("type")
            enabled = definition.pop("enabled")
            self.session.add(
                ApiCaseAssertion(
                    case_version_id=version_id,
                    sequence=sequence,
                    assertion_type=assertion_type,
                    definition=definition,
                    enabled=enabled,
                    **audit_fields(actor_id),
                )
            )

    def add_extractions(self, version_id, extractions, actor_id):
        for extraction in extractions:
            definition = copy.deepcopy(extraction)
            target_name = definition.pop("target")
            extraction_type = definition.pop("type")
            self.session.add(
                ApiCaseExtraction(
                    case_version_id=version_id,
                    target_name=target_name,
                    extraction_type=extraction_type,
                    definition=definition,
                    **audit_fields(actor_id),
                )
            )

    def add_processing(self, version_id, processing, actor_id):
        for phase in ("pre", "post"):
            for sequence, action in enumerate(processing[phase]):
                self.session.add(
                    ApiCaseScript(
                        case_version_id=version_id,
                        phase=phase,
                        sequence=sequence,
                        language="declarative",
                        source="",
                        config=copy.deepcopy(action),
                        **audit_fields(actor_id),
                    )
                )

    def get_version(self, version_id):
        return self.session.get(ApiCaseVersion, version_id)

    def get_version_for_update(self, version_id):
        return self.session.scalar(
            select(ApiCaseVersion).where(ApiCaseVersion.id == version_id).with_for_update()
        )

    def get_versions(self, version_ids):
        identifiers = tuple(set(version_ids))
        if not identifiers:
            return {}
        return {
            item.id: item
            for item in self.session.scalars(
                select(ApiCaseVersion).where(ApiCaseVersion.id.in_(identifiers))
            )
        }

    def get_cases(self, case_ids):
        identifiers = tuple(set(case_ids))
        if not identifiers:
            return {}
        return {
            item.id: item
            for item in self.session.scalars(
                select(ApiCase).where(ApiCase.id.in_(identifiers))
            )
        }

    def get_data_rows(self, version_id):
        return tuple(
            self.session.scalars(
                select(ApiCaseDataRow)
                .where(ApiCaseDataRow.case_version_id == version_id)
                .order_by(ApiCaseDataRow.sequence)
            )
        )

    def get_assertions(self, version_id):
        return tuple(
            self.session.scalars(
                select(ApiCaseAssertion)
                .where(ApiCaseAssertion.case_version_id == version_id)
                .order_by(ApiCaseAssertion.sequence)
            )
        )

    def get_assertions_for_versions(self, version_ids):
        identifiers = tuple(dict.fromkeys(version_ids))
        if not identifiers:
            return {}
        output = {version_id: [] for version_id in identifiers}
        records = self.session.scalars(
            select(ApiCaseAssertion)
            .where(ApiCaseAssertion.case_version_id.in_(identifiers))
            .order_by(ApiCaseAssertion.case_version_id, ApiCaseAssertion.sequence)
        )
        for record in records:
            output[record.case_version_id].append(record)
        return {key: tuple(value) for key, value in output.items()}

    def get_extractions(self, version_id):
        return tuple(
            self.session.scalars(
                select(ApiCaseExtraction)
                .where(ApiCaseExtraction.case_version_id == version_id)
                .order_by(ApiCaseExtraction.target_name)
            )
        )

    def get_extractions_for_versions(self, version_ids):
        identifiers = tuple(dict.fromkeys(version_ids))
        if not identifiers:
            return {}
        output = {version_id: [] for version_id in identifiers}
        records = self.session.scalars(
            select(ApiCaseExtraction)
            .where(ApiCaseExtraction.case_version_id.in_(identifiers))
            .order_by(ApiCaseExtraction.case_version_id, ApiCaseExtraction.target_name)
        )
        for record in records:
            output[record.case_version_id].append(record)
        return {key: tuple(value) for key, value in output.items()}

    def get_execution_case(self, execution_case_id):
        return self.session.get(ApiExecutionCase, execution_case_id)

    def get_execution_cases(self, execution_case_ids):
        identifiers = tuple(dict.fromkeys(execution_case_ids))
        if not identifiers:
            return {}
        return {
            item.id: item
            for item in self.session.scalars(
                select(ApiExecutionCase).where(ApiExecutionCase.id.in_(identifiers))
            )
        }

    def latest_execution_attempts(self, execution_case_ids):
        identifiers = tuple(dict.fromkeys(execution_case_ids))
        if not identifiers:
            return {}
        ranked = (
            select(
                ApiExecutionAttempt.id.label("attempt_id"),
                ApiExecutionAttempt.execution_case_id.label("execution_case_id"),
                func.row_number()
                .over(
                    partition_by=ApiExecutionAttempt.execution_case_id,
                    order_by=ApiExecutionAttempt.attempt_number.desc(),
                )
                .label("row_number"),
            )
            .where(ApiExecutionAttempt.execution_case_id.in_(identifiers))
            .subquery()
        )
        records = self.session.scalars(
            select(ApiExecutionAttempt)
            .join(
                ranked,
                ApiExecutionAttempt.id == ranked.c.attempt_id,
            )
            .where(ranked.c.row_number == 1)
        )
        return {record.execution_case_id: record for record in records}

    def get_execution(self, execution_id):
        return self.session.get(ApiExecution, execution_id)

    def get_environment_revision(self, revision_id):
        return self.session.get(ApiEnvironmentRevision, revision_id)

    def get_environment(self, environment_id):
        return self.session.get(ApiEnvironment, environment_id)

    def list_active_baselines(
        self,
        project_id,
        actor_id,
        *,
        current_only=False,
        limit=None,
        offset=0,
    ):
        status_filter = (
            ApiBaseline.status == "active"
            if current_only
            else ApiBaseline.status != "archived"
        )
        statement = (
            select(ApiBaseline, ApiCase, ApiCaseVersion, ApiSourceEndpoint)
            .join(ApiCase, ApiCase.id == ApiBaseline.case_id)
            .join(ApiCaseVersion, ApiCaseVersion.id == ApiBaseline.case_version_id)
            .join(ApiSourceEndpoint, ApiSourceEndpoint.id == ApiCaseVersion.endpoint_id)
            .where(
                ApiBaseline.project_id == project_id,
                status_filter,
                ApiBaseline.owner_id == actor_id,
                ApiCase.project_id == project_id,
                ApiCase.owner_id == actor_id,
                ApiCase.status != "archived",
                ApiCaseVersion.endpoint_id == ApiSourceEndpoint.id,
            )
            .order_by(
                ApiBaseline.group_name,
                ApiSourceEndpoint.tags,
                ApiSourceEndpoint.normalized_path,
                ApiSourceEndpoint.method,
                ApiCase.name,
                ApiBaseline.created_at.desc(),
                ApiBaseline.id,
            )
        )
        if offset:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        return tuple(
            self.session.execute(statement)
        )

    def create_baseline(
        self,
        project_id,
        case_id,
        case_version_id,
        environment_revision_id,
        debug_execution_case_id,
        group_name,
        actor_id,
    ):
        record = ApiBaseline(
            project_id=project_id,
            case_id=case_id,
            case_version_id=case_version_id,
            environment_revision_id=environment_revision_id,
            debug_execution_case_id=debug_execution_case_id,
            group_name=group_name,
            status="active",
            adoption_reason="passing debug evidence",
            **audit_fields(actor_id),
        )
        self.session.add(record)
        self.session.flush()
        return record

    def get_baseline(self, baseline_id):
        return self.session.get(ApiBaseline, baseline_id)

    def get_baseline_for_update(self, baseline_id):
        return self.session.scalar(
            select(ApiBaseline).where(ApiBaseline.id == baseline_id).with_for_update()
        )

    def flush(self):
        self.session.flush()
