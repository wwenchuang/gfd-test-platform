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
from ..models.execution import ApiExecution, ApiExecutionCase
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
        return tuple(
            self.session.execute(
                select(ApiCaseVersion, ApiCase)
                .join(ApiCase, ApiCase.id == ApiCaseVersion.case_id)
                .join(
                    ApiSourceEndpoint,
                    ApiSourceEndpoint.id == ApiCaseVersion.endpoint_id,
                )
                .join(
                    ApiSourceRevision,
                    ApiSourceRevision.id == ApiSourceEndpoint.revision_id,
                )
                .join(ApiSource, ApiSource.id == ApiSourceRevision.source_id)
                .join(ApiProject, ApiProject.id == ApiSource.project_id)
                .where(
                    ApiSourceRevision.id == revision_id,
                    ApiProject.owner_id == actor_id,
                    ApiCase.owner_id == actor_id,
                    ApiCase.project_id == ApiProject.id,
                    ApiCase.endpoint_id == ApiSourceEndpoint.id,
                    ApiCase.status != "archived",
                    ApiCaseVersion.endpoint_id == ApiCase.endpoint_id,
                    ApiCase.active_version_id == ApiCaseVersion.id,
                )
                .order_by(
                    ApiSourceEndpoint.normalized_path,
                    ApiSourceEndpoint.method,
                    ApiCase.name,
                    ApiCase.id,
                )
            )
        )

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

    def get_extractions(self, version_id):
        return tuple(
            self.session.scalars(
                select(ApiCaseExtraction)
                .where(ApiCaseExtraction.case_version_id == version_id)
                .order_by(ApiCaseExtraction.target_name)
            )
        )

    def get_execution_case(self, execution_case_id):
        return self.session.get(ApiExecutionCase, execution_case_id)

    def get_execution(self, execution_id):
        return self.session.get(ApiExecution, execution_id)

    def get_environment_revision(self, revision_id):
        return self.session.get(ApiEnvironmentRevision, revision_id)

    def get_environment(self, environment_id):
        return self.session.get(ApiEnvironment, environment_id)

    def list_active_baselines(self, project_id, actor_id):
        return tuple(
            self.session.execute(
                select(ApiBaseline, ApiCase, ApiCaseVersion, ApiSourceEndpoint)
                .join(ApiCase, ApiCase.id == ApiBaseline.case_id)
                .join(ApiCaseVersion, ApiCaseVersion.id == ApiBaseline.case_version_id)
                .join(ApiSourceEndpoint, ApiSourceEndpoint.id == ApiCaseVersion.endpoint_id)
                .where(
                    ApiBaseline.project_id == project_id,
                    ApiBaseline.status != "archived",
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
                )
            )
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
