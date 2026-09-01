"""Versioned API case drafts, deterministic validation, and baseline adoption."""

import copy

from task_server.services.business_line_service import (
    business_line_id,
    business_line_name,
    configured_test_application,
    resolve_test_application,
)

from .. import access

from ..contracts.case import (
    AssertionView,
    BaselineCaseView,
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


class BaselineScopeRepairError(BaselineGateError):
    pass


ALLOWED_ORIGINS = frozenset({"manual", "ai", "imported"})
DEFAULT_BASELINE_GROUP = "未分组"


class CaseService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def create_draft(self, endpoint_id, payload, origin, actor_id):
        access.require_permission(actor_id, "api.edit")
        parsed = parse_case_payload(payload)
        if origin not in ALLOWED_ORIGINS:
            raise ValueError("case origin is not supported")
        with self.session_factory.begin() as session:
            repository = CaseRepository(session)
            endpoint = repository.get_endpoint(endpoint_id)
            if endpoint is None:
                raise EndpointNotFoundError("API source endpoint was not found")
            project_id = self._endpoint_project_id(repository, endpoint)
            access.require_resource(session, endpoint, actor_id, "api.edit")
            case = repository.create_case(
                project_id, endpoint.id, parsed["name"], origin, actor_id
            )
            version = self._persist_version(repository, case, parsed, 1, actor_id)
            case.active_version_id = version.id
            repository.flush()
            return self._version_view(repository, version, case)

    def create_version(self, case_id, payload, actor_id, endpoint_id=None):
        access.require_permission(actor_id, "api.edit")
        parsed = parse_case_payload(payload, allow_disabled_scope=True)
        with self.session_factory.begin() as session:
            repository = CaseRepository(session)
            case = repository.get_case_for_update(case_id)
            if case is None or not access.resource_allowed(session, case, actor_id) or case.status == "archived":
                raise CaseNotFoundError("API case was not found")
            if endpoint_id and endpoint_id != case.endpoint_id:
                self._adapt_case_endpoint(repository, case, endpoint_id)
            version_number = repository.next_version_number(case.id)
            previous_group_name = ""
            if case.active_version_id:
                previous = repository.get_version(case.active_version_id)
                if previous is not None:
                    previous_group_name = previous.group_name or ""
            version = self._persist_version(
                repository, case, parsed, version_number, actor_id, previous_group_name
            )
            case.name = parsed["name"]
            case.active_version_id = version.id
            case.updated_by = actor_id
            repository.flush()
            return self._version_view(repository, version, case)

    def archive_case(self, case_id, actor_id):
        access.require_permission(actor_id, "api.delete")
        with self.session_factory.begin() as session:
            repository = CaseRepository(session)
            case = repository.get_case_for_update(case_id)
            if case is None or not access.resource_allowed(session, case, actor_id):
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

    def update_case_version_group(self, version_id, group_name, actor_id):
        access.require_permission(actor_id, "api.edit")
        group_name = self._clean_group_name(group_name, "case group name is invalid")
        with self.session_factory.begin() as session:
            repository = CaseRepository(session)
            version = repository.get_version_for_update(version_id)
            if version is None or not access.resource_allowed(session, version, actor_id):
                raise CaseNotFoundError("API case version was not found")
            case = repository.get_case(version.case_id)
            if case is None or not access.resource_allowed(session, case, actor_id) or case.status == "archived":
                raise CaseNotFoundError("API case version was not found")
            version.group_name = group_name
            version.updated_by = actor_id
            repository.flush()
            return self._version_view(repository, version, case)

    def list_active_versions_for_source_revision(self, revision_id, actor_id):
        access.require_permission(actor_id, "api.view")
        with self.session_factory() as session:
            repository = CaseRepository(session)
            projected = repository.list_active_versions_for_source_revision(
                revision_id,
                actor_id,
            )
            version_ids = [version.id for version, _case, _endpoint, _state in projected]
            data_rows = repository.get_data_rows_for_versions(version_ids)
            assertions = repository.get_assertions_for_versions(version_ids)
            extractions = repository.get_extractions_for_versions(version_ids)
            lifecycle = repository.case_lifecycle(
                [case.id for _version, case, _endpoint, _state in projected],
                actor_id,
            )
            return tuple(
                self._version_view(
                    repository,
                    version,
                    case,
                    current_endpoint_id=current_endpoint.id,
                    source_state=source_state,
                    lifecycle=lifecycle.get(case.id, {}),
                    data_rows=data_rows.get(version.id, ()),
                    assertion_records=assertions.get(version.id, ()),
                    extraction_records=extractions.get(version.id, ()),
                )
                for version, case, current_endpoint, source_state in projected
            )

    def list_active_baselines(self, project_id, actor_id):
        access.require_permission(actor_id, "api.view")
        with self.session_factory() as session:
            repository = CaseRepository(session)
            return tuple(
                self._baseline_case_view(baseline, case, version, endpoint)
                for baseline, case, version, endpoint in repository.list_active_baselines(
                    project_id,
                    actor_id,
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
        access.require_permission(actor_id, "api.baseline")
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
            access.require_resource(session, case, actor_id, "api.baseline")
            access.require_resource(session, execution, actor_id, "api.baseline")
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
            repository.supersede_active_baselines(case.id, actor_id)
            baseline = repository.create_baseline(
                case.project_id,
                case.id,
                version.id,
                evidence.environment_revision_id,
                evidence.id,
                self._default_group_name(endpoint),
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

    def update_baseline_group(self, baseline_ids, group_name, actor_id):
        access.require_permission(actor_id, "api.baseline")
        group_name = self._clean_group_name(group_name, "baseline group name is invalid")
        if not baseline_ids:
            raise ValueError("baseline_ids is required")
        with self.session_factory.begin() as session:
            repository = CaseRepository(session)
            views = []
            for baseline_id in baseline_ids:
                baseline = repository.get_baseline_for_update(baseline_id)
                if (
                    baseline is None
                    or not access.resource_allowed(session, baseline, actor_id)
                    or baseline.status == "archived"
                ):
                    raise CaseNotFoundError("API baseline was not found")
                baseline.group_name = group_name
                baseline.updated_by = actor_id
                views.append(self._baseline_view(baseline))
            repository.flush()
            return tuple(views)

    def preview_baseline_scope_repair(
        self, baseline_ids, app_package, business, actor_id
    ):
        access.require_permission(actor_id, "api.baseline")
        target = self._baseline_scope_target(app_package, business)
        with self.session_factory() as session:
            return self._baseline_scope_repair_result(
                CaseRepository(session), baseline_ids, target, actor_id
            )

    def repair_baseline_scope(
        self, baseline_ids, app_package, business, actor_id
    ):
        access.require_permission(actor_id, "api.baseline")
        target = self._baseline_scope_target(app_package, business)
        with self.session_factory.begin() as session:
            repository = CaseRepository(session)
            result = self._baseline_scope_repair_result(
                repository, baseline_ids, target, actor_id, for_update=True
            )
            if result["conflicts"]:
                raise BaselineScopeRepairError(
                    "所选基线已有不同归属，请缩小范围后重新预览"
                )
            updated = 0
            for item in result["items"]:
                if item["status"] != "eligible":
                    continue
                baseline = repository.get_baseline_for_update(item["baseline_id"])
                version = repository.get_version_for_update(baseline.case_version_id)
                template = copy.deepcopy(dict(version.request_template or {}))
                template.update(
                    {
                        "app_package": target["app_package"],
                        "app_name": target["app_name"],
                        "business": target["business"],
                    }
                )
                version.request_template = template
                version.updated_by = actor_id
                baseline.updated_by = actor_id
                item["status"] = "updated"
                item["reason"] = "已补齐缺失的应用和业务归属"
                updated += 1
            repository.flush()
            result["updated"] = updated
            return result

    def archive_baseline(self, baseline_id, actor_id):
        access.require_permission(actor_id, "api.baseline")
        with self.session_factory.begin() as session:
            repository = CaseRepository(session)
            baseline = repository.get_baseline_for_update(baseline_id)
            if (
                baseline is None
                or not access.resource_allowed(session, baseline, actor_id)
                or baseline.status == "archived"
            ):
                raise CaseNotFoundError("API baseline was not found")
            baseline.status = "archived"
            baseline.updated_by = actor_id
            repository.flush()
            return self._baseline_view(baseline)

    @staticmethod
    def _endpoint_project_id(repository, endpoint):
        revision = repository.get_source_revision(endpoint.revision_id)
        source = repository.get_source(revision.source_id) if revision else None
        if source is None:
            raise EndpointNotFoundError("API endpoint source was not found")
        return source.project_id

    @staticmethod
    def _baseline_scope_target(app_package, business):
        package = str(app_package or "").strip()
        application = configured_test_application(package, include_disabled=False)
        if not application or not application.get("enabled"):
            raise BaselineScopeRepairError("目标应用未配置、已移除或已停用，请重新选择")
        try:
            business_id = business_line_id(
                business, app_package=package, require_active=True
            )
        except ValueError as exc:
            raise BaselineScopeRepairError(f"目标业务不可用：{exc}") from exc
        return {
            "app_package": package,
            "app_name": str(application["name"]),
            "business": business_id,
            "business_name": business_line_name(
                business_id, app_package=package
            ),
        }

    @staticmethod
    def _baseline_scope_repair_result(
        repository, baseline_ids, target, actor_id, *, for_update=False
    ):
        ids = list(dict.fromkeys(str(item or "").strip() for item in baseline_ids))
        if not ids or any(not item for item in ids):
            raise ValueError("baseline_ids is required")
        if len(ids) > 500:
            raise ValueError("baseline scope repair is limited to 500 items")
        items = []
        counts = {"eligible": 0, "unchanged": 0, "conflict": 0}
        for baseline_id in ids:
            baseline = (
                repository.get_baseline_for_update(baseline_id)
                if for_update
                else repository.get_baseline(baseline_id)
            )
            if (
                baseline is None
                or not access.resource_allowed(repository.session, baseline, actor_id)
                or baseline.status == "archived"
            ):
                raise CaseNotFoundError("API baseline was not found")
            version = (
                repository.get_version_for_update(baseline.case_version_id)
                if for_update
                else repository.get_version(baseline.case_version_id)
            )
            case = repository.get_case(baseline.case_id)
            if version is None or case is None:
                raise CaseNotFoundError("API baseline case version was not found")
            template = dict(version.request_template or {})
            before = {
                "app_package": str(template.get("app_package") or "").strip(),
                "app_name": str(template.get("app_name") or "").strip(),
                "business": str(template.get("business") or "").strip(),
            }
            conflicts = []
            if baseline.status != "active":
                conflicts.append("仅允许补齐当前有效基线")
            if before["app_package"] and before["app_package"] != target["app_package"]:
                conflicts.append("已有应用与目标应用不同")
            if before["app_name"] and before["app_name"] != target["app_name"]:
                conflicts.append("已有应用名称与平台配置不同")
            if before["business"]:
                current_business = business_line_id(
                    before["business"],
                    app_package=before["app_package"] or target["app_package"],
                )
                if current_business != target["business"]:
                    conflicts.append("已有业务与目标业务不同")
            exact = before == {
                "app_package": target["app_package"],
                "app_name": target["app_name"],
                "business": target["business"],
            }
            if conflicts:
                status = "conflict"
                reason = "；".join(conflicts)
            elif exact:
                status = "unchanged"
                reason = "应用和业务归属已经完整，无需修改"
            else:
                status = "eligible"
                reason = "只补齐空缺归属，请求、断言和调试证据保持不变"
            counts[status] += 1
            items.append(
                {
                    "baseline_id": baseline.id,
                    "case_version_id": version.id,
                    "case_name": str(template.get("name") or case.name),
                    "group_name": baseline.group_name or DEFAULT_BASELINE_GROUP,
                    "status": status,
                    "reason": reason,
                    "before": before,
                }
            )
        return {
            "total": len(items),
            "eligible": counts["eligible"],
            "unchanged": counts["unchanged"],
            "conflicts": counts["conflict"],
            "target": dict(target),
            "items": items,
        }

    @classmethod
    def _adapt_case_endpoint(cls, repository, case, endpoint_id):
        previous_endpoint = repository.get_endpoint(case.endpoint_id)
        current_endpoint = repository.get_endpoint(endpoint_id)
        if previous_endpoint is None or current_endpoint is None:
            raise EndpointNotFoundError("API source endpoint was not found")
        previous_revision = repository.get_source_revision(previous_endpoint.revision_id)
        current_revision = repository.get_source_revision(current_endpoint.revision_id)
        if (
            previous_revision is None
            or current_revision is None
            or previous_revision.source_id != current_revision.source_id
            or previous_endpoint.stable_key != current_endpoint.stable_key
            or cls._endpoint_project_id(repository, current_endpoint) != case.project_id
        ):
            raise EndpointNotFoundError(
                "API case can only adapt to the same logical endpoint in a newer source revision"
            )
        case.endpoint_id = current_endpoint.id

    @staticmethod
    def _persist_version(repository, case, payload, version_number, actor_id, group_name=""):
        version = repository.create_version(
            case, payload, version_number, actor_id, group_name
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
    def _version_view(
        repository,
        version,
        case,
        *,
        current_endpoint_id=None,
        source_state="current",
        lifecycle=None,
        data_rows=None,
        assertion_records=None,
        extraction_records=None,
    ):
        request_template = copy.deepcopy(dict(version.request_template))
        name = request_template.get("name", case.name)
        request = request_template.get("request", request_template)
        application = resolve_test_application(
            request_template.get("app_package"),
            request_template.get("app_name"),
            request_template.get("business"),
            include_disabled=True,
        )
        app_package = str(
            request_template.get("app_package") or application.get("package") or ""
        )
        app_name = str(
            request_template.get("app_name") or application.get("name") or ""
        )
        rows = tuple(
            DataRowView(item.name, item.values, item.enabled, item.sequence)
            for item in (
                repository.get_data_rows(version.id)
                if data_rows is None
                else data_rows
            )
        )
        assertions = []
        for item in (
            repository.get_assertions(version.id)
            if assertion_records is None
            else assertion_records
        ):
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
        for item in (
            repository.get_extractions(version.id)
            if extraction_records is None
            else extraction_records
        ):
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
            current_endpoint_id=current_endpoint_id or version.endpoint_id,
            source_state=source_state,
            name=name,
            status=version.status,
            origin=case.origin,
            version=version.version_number,
            purpose=version.purpose,
            priority=version.priority,
            app_package=app_package,
            app_name=app_name,
            business=str(request_template.get("business") or ""),
            group_name=version.group_name or "",
            request=request,
            data_rows=rows,
            assertions=tuple(assertions),
            extractions=tuple(extractions),
            dependencies=dependencies,
            processing=copy.deepcopy(dict(version.processing_spec)),
            validation_summary=copy.deepcopy(dict(version.validation_summary)),
            lifecycle=copy.deepcopy(dict(lifecycle or {})),
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
            group_name=baseline.group_name or DEFAULT_BASELINE_GROUP,
            status=baseline.status,
            adopted_by=baseline.created_by,
            adopted_at=baseline.created_at,
        )

    @staticmethod
    def _baseline_case_view(baseline, case, version, endpoint):
        request_template = dict(version.request_template or {})
        application = resolve_test_application(
            request_template.get("app_package"),
            request_template.get("app_name"),
            request_template.get("business"),
            include_disabled=True,
        )
        return BaselineCaseView(
            id=baseline.id,
            project_id=baseline.project_id,
            case_id=case.id,
            case_version_id=version.id,
            environment_revision_id=baseline.environment_revision_id,
            source_revision_id=endpoint.revision_id,
            endpoint_id=endpoint.id,
            status=baseline.status,
            case_name=str(request_template.get("name") or case.name),
            case_version=version.version_number,
            priority=version.priority,
            app_package=str(
                request_template.get("app_package") or application.get("package") or ""
            ),
            app_name=str(
                request_template.get("app_name") or application.get("name") or ""
            ),
            business=str(request_template.get("business") or ""),
            origin=case.origin,
            method=endpoint.method,
            path=endpoint.path,
            endpoint_summary=endpoint.summary,
            tags=tuple(endpoint.tags or ()),
            group_name=baseline.group_name or CaseService._default_group_name(endpoint),
            adoption_reason=baseline.adoption_reason,
            adopted_at=baseline.created_at,
        )

    @staticmethod
    def _default_group_name(endpoint):
        tags = tuple(endpoint.tags or ())
        return str(tags[0]).strip() if tags and str(tags[0]).strip() else DEFAULT_BASELINE_GROUP

    @staticmethod
    def _clean_group_name(value, message):
        if not isinstance(value, str):
            raise ValueError(message)
        group_name = value.strip()
        if not group_name or len(group_name) > 120:
            raise ValueError(message)
        return group_name
