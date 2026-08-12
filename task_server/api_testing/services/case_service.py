"""Versioned API case drafts, deterministic validation, and baseline adoption."""

import copy

from ..contracts.case import (
    AssertionView,
    BaselineView,
    CaseVersionView,
    CaseView,
    DataRowView,
    ExtractionView,
    parse_case_payload,
)
from ..repositories.case_repository import CaseRepository
from ..validation import validate_case


class EndpointNotFoundError(LookupError):
    pass


class CaseNotFoundError(LookupError):
    pass


class BaselineGateError(ValueError):
    pass


ALLOWED_ORIGINS = frozenset({"manual", "ai", "imported"})


class CaseService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def create_draft(self, endpoint_id, payload, origin, actor_id):
        parsed = parse_case_payload(payload)
        if origin not in ALLOWED_ORIGINS:
            raise ValueError("case origin is not supported")
        with self.session_factory.begin() as session:
            repository = CaseRepository(session)
            endpoint = repository.get_endpoint(endpoint_id)
            if endpoint is None:
                raise EndpointNotFoundError("API source endpoint was not found")
            project_id = self._endpoint_project_id(repository, endpoint)
            case = repository.create_case(
                project_id, endpoint.id, parsed["name"], origin, actor_id
            )
            version = self._persist_version(repository, case, parsed, 1, actor_id)
            case.active_version_id = version.id
            repository.flush()
            return self._version_view(repository, version, case)

    def create_version(self, case_id, payload, actor_id):
        parsed = parse_case_payload(payload)
        with self.session_factory.begin() as session:
            repository = CaseRepository(session)
            case = repository.get_case_for_update(case_id)
            if case is None or case.status == "archived":
                raise CaseNotFoundError("API case was not found")
            version_number = repository.next_version_number(case.id)
            version = self._persist_version(
                repository, case, parsed, version_number, actor_id
            )
            case.name = parsed["name"]
            case.active_version_id = version.id
            case.updated_by = actor_id
            repository.flush()
            return self._version_view(repository, version, case)

    def archive_case(self, case_id, actor_id):
        with self.session_factory.begin() as session:
            repository = CaseRepository(session)
            case = repository.get_case_for_update(case_id)
            if case is None or case.owner_id != actor_id:
                raise CaseNotFoundError("API case was not found")
            case.status = "archived"
            case.active_version_id = None
            case.updated_by = actor_id
            repository.flush()
            return self._case_view(case)

    def get_case(self, case_id):
        with self.session_factory() as session:
            repository = CaseRepository(session)
            case = repository.get_case(case_id)
            if case is None:
                raise CaseNotFoundError("API case was not found")
            return self._case_view(case)

    def get_version(self, version_id):
        with self.session_factory() as session:
            repository = CaseRepository(session)
            version = repository.get_version(version_id)
            if version is None:
                raise CaseNotFoundError("API case version was not found")
            case = repository.get_case(version.case_id)
            return self._version_view(repository, version, case)

    def list_active_versions_for_source_revision(self, revision_id, actor_id):
        with self.session_factory() as session:
            repository = CaseRepository(session)
            return tuple(
                self._version_view(repository, version, case)
                for version, case in repository.list_active_versions_for_source_revision(
                    revision_id, actor_id
                )
            )

    def validate_case(self, case_version_id, environment_metadata):
        with self.session_factory() as session:
            repository = CaseRepository(session)
            version = repository.get_version(case_version_id)
            if version is None:
                raise CaseNotFoundError("API case version was not found")
            case = repository.get_case(version.case_id)
            endpoint = repository.get_endpoint(version.endpoint_id)
            if case is None or endpoint is None:
                raise CaseNotFoundError("API case validation context was not found")
            view = self._version_view(repository, version, case)
            dependency_ids = [
                item.get("case_version_id")
                for item in view.dependencies
                if isinstance(item.get("case_version_id"), str)
            ]
            versions = repository.get_versions(dependency_ids)
            cases = repository.get_cases(
                [item.case_id for item in versions.values()]
            )
            dependency_metadata = {}
            for dependency_id in dependency_ids:
                dependency_version = versions.get(dependency_id)
                if dependency_version is None:
                    dependency_metadata[dependency_id] = {"status": "missing"}
                    continue
                dependency_case = cases.get(dependency_version.case_id)
                if dependency_case is None:
                    dependency_metadata[dependency_id] = {"status": "missing"}
                    continue
                dependency_metadata[dependency_id] = {
                    "status": (
                        "trusted"
                        if dependency_case.project_id == case.project_id
                        else "project_mismatch"
                    ),
                    "project_id": dependency_case.project_id,
                    "exports": tuple(
                        item.target_name
                        for item in repository.get_extractions(dependency_version.id)
                    ),
                }
            return validate_case(
                view,
                endpoint,
                environment_metadata,
                dependency_metadata=dependency_metadata,
            )

    def adopt_baseline(self, case_version_id, debug_execution_case_id, actor_id):
        with self.session_factory.begin() as session:
            repository = CaseRepository(session)
            version = repository.get_version(case_version_id)
            evidence = repository.get_execution_case(debug_execution_case_id)
            if version is None or evidence is None:
                raise BaselineGateError("baseline requires existing passing debug evidence")
            case = repository.get_case_for_update(version.case_id)
            execution = repository.get_execution(evidence.execution_id)
            endpoint = repository.get_endpoint(version.endpoint_id)
            environment_revision = repository.get_environment_revision(
                evidence.environment_revision_id
            )
            environment = (
                repository.get_environment(environment_revision.environment_id)
                if environment_revision
                else None
            )
            if not all((case, execution, endpoint, environment_revision, environment)):
                raise BaselineGateError("baseline requires complete passing debug evidence")
            if execution.state != "DONE":
                raise BaselineGateError(
                    "baseline requires a successful terminal debug execution"
                )
            endpoint_project_id = self._endpoint_project_id(repository, endpoint)
            valid = (
                evidence.status == "PASSED"
                and execution.execution_type == "debug"
                and evidence.case_version_id == version.id
                and evidence.endpoint_id == version.endpoint_id
                and evidence.environment_revision_id == execution.environment_revision_id
                and execution.project_id == case.project_id
                and endpoint_project_id == case.project_id
                and environment.project_id == case.project_id
                and execution.source_revision_id == endpoint.revision_id
            )
            if not valid:
                raise BaselineGateError(
                    "baseline requires passing debug evidence for the same project, endpoint, case version, and environment revision"
                )
            for previous in repository.active_baselines_for_update(
                case.id, environment.id
            ):
                previous.status = "superseded"
                previous.updated_by = actor_id
            baseline = repository.create_baseline(
                case.project_id,
                case.id,
                version.id,
                evidence.environment_revision_id,
                evidence.id,
                actor_id,
            )
            repository.flush()
            return self._baseline_view(baseline)

    def get_baseline(self, baseline_id):
        with self.session_factory() as session:
            baseline = CaseRepository(session).get_baseline(baseline_id)
            if baseline is None:
                raise CaseNotFoundError("API baseline was not found")
            return self._baseline_view(baseline)

    @staticmethod
    def _endpoint_project_id(repository, endpoint):
        revision = repository.get_source_revision(endpoint.revision_id)
        source = repository.get_source(revision.source_id) if revision else None
        if source is None:
            raise EndpointNotFoundError("API endpoint source was not found")
        return source.project_id

    @staticmethod
    def _persist_version(repository, case, payload, version_number, actor_id):
        version = repository.create_version(
            case, payload, version_number, actor_id
        )
        repository.add_data_rows(version.id, payload["data_rows"], actor_id)
        repository.add_assertions(version.id, payload["assertions"], actor_id)
        repository.add_extractions(version.id, payload["extractions"], actor_id)
        repository.add_processing(version.id, payload["processing"], actor_id)
        repository.flush()
        return version

    @staticmethod
    def _case_view(case):
        return CaseView(
            id=case.id,
            project_id=case.project_id,
            endpoint_id=case.endpoint_id,
            name=case.name,
            status=case.status,
            origin=case.origin,
            active_version_id=case.active_version_id,
            created_at=case.created_at,
            updated_at=case.updated_at,
        )

    @staticmethod
    def _version_view(repository, version, case):
        request_template = copy.deepcopy(dict(version.request_template))
        name = request_template.get("name", case.name)
        request = request_template.get("request", request_template)
        rows = tuple(
            DataRowView(item.name, item.values, item.enabled, item.sequence)
            for item in repository.get_data_rows(version.id)
        )
        assertions = []
        for item in repository.get_assertions(version.id):
            definition = dict(item.definition)
            assertions.append(
                AssertionView(
                    type=item.assertion_type,
                    operator=definition.get("operator", ""),
                    expected=copy.deepcopy(definition.get("expected")),
                    path=definition.get("path"),
                    name=definition.get("name"),
                    timeout_ms=int(definition.get("timeout_ms", 0)),
                    enabled=item.enabled,
                    sequence=item.sequence,
                )
            )
        extractions = []
        for item in repository.get_extractions(version.id):
            definition = dict(item.definition)
            extractions.append(
                ExtractionView(
                    target=item.target_name,
                    type=item.extraction_type,
                    path=definition.get("path"),
                    name=definition.get("name"),
                    required=bool(definition.get("required", True)),
                    default=copy.deepcopy(definition.get("default")),
                )
            )
        dependencies = tuple(
            copy.deepcopy(version.dependency_spec.get("dependencies", []))
        )
        return CaseVersionView(
            id=version.id,
            case_id=case.id,
            project_id=case.project_id,
            endpoint_id=version.endpoint_id,
            name=name,
            status=version.status,
            origin=case.origin,
            version=version.version_number,
            purpose=version.purpose,
            priority=version.priority,
            request=request,
            data_rows=rows,
            assertions=tuple(assertions),
            extractions=tuple(extractions),
            dependencies=dependencies,
            processing=copy.deepcopy(dict(version.processing_spec)),
            validation_summary=copy.deepcopy(dict(version.validation_summary)),
            created_at=version.created_at,
            updated_at=version.updated_at,
        )

    @staticmethod
    def _baseline_view(baseline):
        return BaselineView(
            id=baseline.id,
            project_id=baseline.project_id,
            case_id=baseline.case_id,
            case_version_id=baseline.case_version_id,
            environment_revision_id=baseline.environment_revision_id,
            debug_execution_case_id=baseline.debug_execution_case_id,
            status=baseline.status,
            adopted_by=baseline.created_by,
            adopted_at=baseline.created_at,
        )
