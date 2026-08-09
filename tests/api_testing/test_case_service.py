import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
import pytest

from task_server.api_testing.models.case import (
    ApiBaseline,
    ApiCase,
    ApiCaseAssertion,
    ApiCaseDataRow,
    ApiCaseExtraction,
    ApiCaseScript,
    ApiCaseVersion,
)
from task_server.api_testing.models.environment import (
    ApiEnvironment,
    ApiEnvironmentRevision,
)
from task_server.api_testing.models.execution import ApiExecution, ApiExecutionCase
from task_server.api_testing.models.project import ApiProject
from task_server.api_testing.models.source import ApiSourceEndpoint, ApiSourceRevision
from tests.api_testing.test_migrations import (
    _alembic_config,
    _assert_current_test_schema,
    _create_test_schema,
    _database_url,
    _drop_test_schema,
    _without_database_environment,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "my_favorites_openapi.json"
FAVORITES_OPENAPI = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
SYNTHETIC_SECRET = "task5-synthetic-secret-never-persist-in-case-views"


def _audit(actor="admin"):
    return {"owner_id": actor, "created_by": actor, "updated_by": actor}


@pytest.fixture(scope="module")
def case_database():
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
def session_factory(case_database):
    engine = create_engine(case_database, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture()
def project_context(session_factory):
    from task_server.api_testing.services.source_service import SourceService

    suffix = os.urandom(6).hex()
    with session_factory.begin() as session:
        project = ApiProject(
            name=f"Task 5 Project {suffix}",
            slug=f"task-5-project-{suffix}",
            description="Synthetic case service tests",
            **_audit(),
        )
        session.add(project)
        session.flush()

    source_service = SourceService(session_factory)
    preview = source_service.preview_refresh(
        project.id, None, copy.deepcopy(FAVORITES_OPENAPI), "admin"
    )
    revision = source_service.activate_preview(preview.id, "admin")

    with session_factory.begin() as session:
        environment = ApiEnvironment(
            project_id=project.id,
            source_id=revision.source_id,
            name=f"Task 5 Environment {suffix}",
            **_audit(),
        )
        session.add(environment)
        session.flush()
        environment_revision = ApiEnvironmentRevision(
            environment_id=environment.id,
            source_revision_id=revision.id,
            revision_number=1,
            name=environment.name,
            default_headers={"Authorization": "Bearer {{ZXBToken}}"},
            **_audit(),
        )
        session.add(environment_revision)
        session.flush()
        environment.active_revision_id = environment_revision.id

    endpoint_by_operation = {item.operation_id: item for item in revision.endpoints}
    return {
        "project": project,
        "source_revision": revision,
        "endpoints": endpoint_by_operation,
        "environment": environment,
        "environment_revision": environment_revision,
    }


@pytest.fixture()
def case_service(session_factory):
    from task_server.api_testing.services.case_service import CaseService

    return CaseService(session_factory)


def valid_list_case(endpoint):
    return {
        "name": "查询我的收藏-成功响应",
        "purpose": "验证当前用户能够查询收藏列表",
        "priority": "P0",
        "request": {
            "method": "GET",
            "path": endpoint.path,
            "path_params": {},
            "query": {"pageNum": 1},
            "headers": {"Biz": "{{Biz}}"},
            "cookies": {},
            "body": None,
        },
        "data_rows": [
            {
                "name": "默认数据",
                "values": {"Biz": "ZXB"},
                "enabled": True,
            }
        ],
        "assertions": [
            {"type": "status_code", "operator": "equals", "expected": 200},
            {
                "type": "json_path",
                "path": "$.code",
                "operator": "equals",
                "expected": 0,
            },
        ],
        "extractions": [
            {"target": "favoriteId", "type": "json_path", "path": "$.data[0].id"}
        ],
        "dependencies": [],
        "processing": {
            "pre": [{"action": "set_variable", "name": "traceTag", "value": "task5"}],
            "post": [],
        },
    }


def valid_add_case(endpoint):
    payload = valid_list_case(endpoint)
    payload.update(
        {
            "name": "添加收藏-成功响应",
            "purpose": "验证当前用户能够添加收藏",
            "request": {
                "method": "POST",
                "path": endpoint.path,
                "path_params": {},
                "query": {},
                "headers": {},
                "cookies": {},
                "body": {
                    "targetId": "synthetic-model-001",
                    "favoriteType": "MODEL",
                },
            },
            "extractions": [],
        }
    )
    return payload


def test_draft_persists_structured_children_before_debug(
    case_service, project_context, session_factory
):
    endpoint = project_context["endpoints"]["favoriteList"]
    draft = case_service.create_draft(endpoint.id, valid_list_case(endpoint), "manual", "admin")

    assert draft.status == "draft"
    assert draft.version == 1
    assert draft.origin == "manual"
    assert draft.request["method"] == "GET"
    assert draft.data_rows[0].values["Biz"] == "ZXB"
    assert draft.assertions[1].path == "$.code"
    assert draft.extractions[0].target == "favoriteId"
    assert draft.dependencies == ()
    assert draft.processing["pre"][0]["action"] == "set_variable"
    assert SYNTHETIC_SECRET not in repr(draft)

    with session_factory() as session:
        case = session.get(ApiCase, draft.case_id)
        assert case.active_version_id == draft.id
        assert session.scalar(
            select(func.count(ApiCaseDataRow.id)).where(
                ApiCaseDataRow.case_version_id == draft.id
            )
        ) == 1
        assert session.scalar(
            select(func.count(ApiCaseAssertion.id)).where(
                ApiCaseAssertion.case_version_id == draft.id
            )
        ) == 2
        assert session.scalar(
            select(func.count(ApiCaseExtraction.id)).where(
                ApiCaseExtraction.case_version_id == draft.id
            )
        ) == 1
        assert session.scalar(
            select(func.count(ApiCaseScript.id)).where(
                ApiCaseScript.case_version_id == draft.id
            )
        ) == 1


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda payload: payload.update({"surprise": True}), "unknown field"),
        (lambda payload: payload.update({"priority": "urgent"}), "priority"),
        (lambda payload: payload.update({"name": "x" * 301}), "name"),
        (lambda payload: payload["request"].update({"query": []}), "query"),
        (lambda payload: payload["assertions"][0].update({"operator": "run_python"}), "operator"),
        (lambda payload: payload["data_rows"][0].update({"enabled": "yes"}), "enabled"),
        (lambda payload: payload["processing"]["pre"][0].update({"shell": "rm -rf /"}), "unknown field"),
        (lambda payload: payload["processing"]["pre"][0].pop("name"), "requires name"),
    ],
)
def test_draft_input_rejects_unknown_or_invalid_fields(
    case_service, project_context, mutation, message
):
    from task_server.api_testing.contracts.case import CasePayloadError

    endpoint = project_context["endpoints"]["favoriteList"]
    payload = valid_list_case(endpoint)
    mutation(payload)

    with pytest.raises(CasePayloadError, match=message):
        case_service.create_draft(endpoint.id, payload, "manual", "admin")


def test_edit_creates_immutable_version_and_preserves_children(
    case_service, project_context, session_factory
):
    endpoint = project_context["endpoints"]["favoriteList"]
    first = case_service.create_draft(endpoint.id, valid_list_case(endpoint), "ai", "admin")
    changed_payload = valid_list_case(endpoint)
    changed_payload["name"] = "查询收藏-边界分页"
    changed_payload["request"]["query"]["pageNum"] = 999
    changed_payload["assertions"] = changed_payload["assertions"][:1]

    second = case_service.create_version(first.case_id, changed_payload, "editor")

    assert second.version == 2
    assert second.request["query"]["pageNum"] == 999
    assert len(second.assertions) == 1
    reloaded_first = case_service.get_version(first.id)
    assert reloaded_first.name == "查询我的收藏-成功响应"
    assert reloaded_first.request["query"]["pageNum"] == 1
    assert len(reloaded_first.assertions) == 2

    with session_factory() as session:
        case = session.get(ApiCase, first.case_id)
        assert case.active_version_id == second.id


def test_failed_edit_transaction_does_not_advance_case_head(
    case_service, project_context, session_factory, monkeypatch
):
    endpoint = project_context["endpoints"]["favoriteList"]
    first = case_service.create_draft(endpoint.id, valid_list_case(endpoint), "manual", "admin")

    def fail_children(*_args, **_kwargs):
        raise RuntimeError("synthetic child persistence failure")

    monkeypatch.setattr(
        "task_server.api_testing.repositories.case_repository.CaseRepository.add_assertions",
        fail_children,
    )
    with pytest.raises(RuntimeError, match="child persistence failure"):
        case_service.create_version(first.case_id, valid_list_case(endpoint), "editor")

    with session_factory() as session:
        case = session.get(ApiCase, first.case_id)
        assert case.active_version_id == first.id
        assert session.scalar(
            select(func.count(ApiCaseVersion.id)).where(ApiCaseVersion.case_id == first.case_id)
        ) == 1


def test_concurrent_edits_allocate_unique_versions(case_service, project_context):
    endpoint = project_context["endpoints"]["favoriteList"]
    first = case_service.create_draft(endpoint.id, valid_list_case(endpoint), "manual", "admin")

    def create(index):
        payload = valid_list_case(endpoint)
        payload["name"] = f"并发版本 {index}"
        return case_service.create_version(first.case_id, payload, f"editor-{index}")

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(create, range(3)))

    assert sorted(item.version for item in results) == [2, 3, 4]
    assert case_service.get_case(first.case_id).active_version_id in {
        item.id for item in results
    }


def test_validate_case_accepts_environment_and_data_row_variables(
    case_service, project_context
):
    from task_server.api_testing.validation import validate_case

    endpoint_view = project_context["endpoints"]["favoriteList"]
    draft = case_service.create_draft(
        endpoint_view.id, valid_list_case(endpoint_view), "manual", "admin"
    )
    with case_service.session_factory() as session:
        endpoint = session.get(ApiSourceEndpoint, endpoint_view.id)
        result = validate_case(
            draft,
            endpoint,
            {
                "variables": {"ZXBToken": {"configured": True}},
                "services": {"default": "https://api.example.test/app"},
            },
        )

    assert result.valid is True
    assert result.errors == ()


def test_validate_case_reports_contract_and_safety_errors_without_network(
    case_service, project_context, monkeypatch
):
    from task_server.api_testing.validation import validate_case

    endpoint_view = project_context["endpoints"]["favoriteAdd"]
    payload = valid_add_case(endpoint_view)
    payload["request"]["method"] = "GET"
    payload["request"]["path"] = "https://attacker.example/collect"
    payload["request"]["body"] = {"targetId": 7}
    payload["request"]["headers"] = {"X-Missing": "{{missingValue}}"}
    payload["assertions"] = [
        {"type": "json_path", "path": "code", "operator": "equals", "expected": 0}
    ]
    payload["extractions"] = [
        {"target": "invalid target", "type": "json_path", "path": "code"}
    ]
    payload["dependencies"] = [
        {"case_version_id": "not-a-uuid", "required": True, "exports": ["missingValue"]}
    ]
    draft = case_service.create_draft(endpoint_view.id, payload, "manual", "admin")

    monkeypatch.setattr(
        "socket.create_connection",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network used")),
    )
    with case_service.session_factory() as session:
        endpoint = session.get(ApiSourceEndpoint, endpoint_view.id)
        result = validate_case(draft, endpoint, {"variables": {}, "services": {}})

    codes = {item.code for item in result.errors}
    assert {
        "method_mismatch",
        "path_mismatch",
        "unsafe_absolute_url",
        "body_required_property",
        "body_type_mismatch",
        "undefined_variable",
        "assertion_path_invalid",
        "extraction_target_invalid",
        "extraction_path_invalid",
        "dependency_invalid",
    }.issubset(codes)
    assert result.valid is False


def test_validate_case_distinguishes_optional_contract_warnings(
    case_service, project_context
):
    from task_server.api_testing.validation import validate_case

    endpoint_view = project_context["endpoints"]["favoriteList"]
    payload = valid_list_case(endpoint_view)
    payload["request"]["query"] = {}
    draft = case_service.create_draft(endpoint_view.id, payload, "manual", "admin")
    with case_service.session_factory() as session:
        endpoint = session.get(ApiSourceEndpoint, endpoint_view.id)
        result = validate_case(
            draft,
            endpoint,
            {"variables": {"Biz": "ZXB"}, "services": {}},
        )

    assert result.valid is True
    assert result.errors == ()
    assert any(item.code == "optional_parameter_omitted" for item in result.warnings)


def test_validate_case_rejects_malformed_placeholders_and_wrong_endpoint(
    case_service, project_context
):
    from task_server.api_testing.validation import validate_case

    list_endpoint = project_context["endpoints"]["favoriteList"]
    add_endpoint = project_context["endpoints"]["favoriteAdd"]
    payload = valid_list_case(list_endpoint)
    payload["request"]["headers"]["X-Broken"] = "{{not valid}}"
    draft = case_service.create_draft(list_endpoint.id, payload, "manual", "admin")
    with case_service.session_factory() as session:
        wrong_endpoint = session.get(ApiSourceEndpoint, add_endpoint.id)
        result = validate_case(
            draft,
            wrong_endpoint,
            {"variables": {"Biz": "ZXB"}, "services": {}},
        )

    codes = {item.code for item in result.errors}
    assert "endpoint_identity_mismatch" in codes
    assert "placeholder_invalid" in codes


def _create_execution_evidence(
    session_factory,
    context,
    case_version,
    *,
    status="PASSED",
    execution_type="debug",
    endpoint_id=None,
    environment_revision_id=None,
    project_id=None,
    source_revision_id=None,
):
    with session_factory.begin() as session:
        execution = ApiExecution(
            project_id=project_id or context["project"].id,
            source_revision_id=source_revision_id or context["source_revision"].id,
            environment_revision_id=(
                environment_revision_id or context["environment_revision"].id
            ),
            execution_type=execution_type,
            state=status,
            idempotency_key=f"task5-{os.urandom(8).hex()}",
            requested_case_ids=[case_version.case_id],
            request_snapshot={"sanitized": True},
            **_audit(),
        )
        session.add(execution)
        session.flush()
        evidence = ApiExecutionCase(
            execution_id=execution.id,
            case_version_id=case_version.id,
            endpoint_id=endpoint_id or case_version.endpoint_id,
            environment_revision_id=(
                environment_revision_id or context["environment_revision"].id
            ),
            ordinal=1,
            status=status,
            sanitized_result={"status": status, "token": "***"},
            **_audit(),
        )
        session.add(evidence)
        session.flush()
        return evidence.id


def test_baseline_requires_exact_passing_debug_evidence(
    case_service, project_context, session_factory
):
    from task_server.api_testing.services.case_service import BaselineGateError

    endpoint = project_context["endpoints"]["favoriteList"]
    draft = case_service.create_draft(endpoint.id, valid_list_case(endpoint), "manual", "admin")

    rejected_status_ids = [
        _create_execution_evidence(
            session_factory, project_context, draft, status=status
        )
        for status in ("FAILED", "BROKEN", "QUEUED")
    ]
    regression_id = _create_execution_evidence(
        session_factory, project_context, draft, execution_type="regression"
    )
    for evidence_id in (*rejected_status_ids, regression_id):
        with pytest.raises(BaselineGateError):
            case_service.adopt_baseline(draft.id, evidence_id, "admin")

    passed_id = _create_execution_evidence(session_factory, project_context, draft)
    baseline = case_service.adopt_baseline(draft.id, passed_id, "admin")
    assert baseline.status == "active"
    assert baseline.case_version_id == draft.id
    assert baseline.environment_revision_id == project_context["environment_revision"].id
    assert SYNTHETIC_SECRET not in repr(baseline)


def test_baseline_rejects_cross_version_endpoint_environment_and_project(
    case_service, project_context, session_factory
):
    from task_server.api_testing.services.case_service import BaselineGateError

    list_endpoint = project_context["endpoints"]["favoriteList"]
    add_endpoint = project_context["endpoints"]["favoriteAdd"]
    first = case_service.create_draft(
        list_endpoint.id, valid_list_case(list_endpoint), "manual", "admin"
    )
    second = case_service.create_version(first.case_id, valid_list_case(list_endpoint), "admin")

    with session_factory.begin() as session:
        other_environment = ApiEnvironment(
            project_id=project_context["project"].id,
            name=f"Other Environment {os.urandom(4).hex()}",
            **_audit(),
        )
        session.add(other_environment)
        session.flush()
        other_environment_revision = ApiEnvironmentRevision(
            environment_id=other_environment.id,
            revision_number=1,
            name=other_environment.name,
            default_headers={},
            **_audit(),
        )
        session.add(other_environment_revision)
        session.flush()
        other_environment.active_revision_id = other_environment_revision.id

    wrong_version = _create_execution_evidence(session_factory, project_context, first)
    wrong_endpoint = _create_execution_evidence(
        session_factory, project_context, second, endpoint_id=add_endpoint.id
    )
    wrong_environment = _create_execution_evidence(
        session_factory,
        project_context,
        second,
        environment_revision_id=other_environment_revision.id,
    )

    for evidence_id in (wrong_version, wrong_endpoint):
        with pytest.raises(BaselineGateError):
            case_service.adopt_baseline(second.id, evidence_id, "admin")

    accepted = case_service.adopt_baseline(second.id, wrong_environment, "admin")
    assert accepted.environment_revision_id == other_environment_revision.id

    with session_factory.begin() as session:
        other_project = ApiProject(
            name=f"Other Project {os.urandom(4).hex()}",
            slug=f"other-project-{os.urandom(6).hex()}",
            **_audit(),
        )
        session.add(other_project)
        session.flush()
    wrong_project = _create_execution_evidence(
        session_factory,
        project_context,
        second,
        project_id=other_project.id,
    )
    with pytest.raises(BaselineGateError):
        case_service.adopt_baseline(second.id, wrong_project, "admin")


def test_readoption_supersedes_only_same_case_and_environment(
    case_service, project_context, session_factory
):
    endpoint = project_context["endpoints"]["favoriteList"]
    first = case_service.create_draft(endpoint.id, valid_list_case(endpoint), "manual", "admin")
    first_evidence = _create_execution_evidence(session_factory, project_context, first)
    first_baseline = case_service.adopt_baseline(first.id, first_evidence, "admin")

    second = case_service.create_version(first.case_id, valid_list_case(endpoint), "admin")
    second_evidence = _create_execution_evidence(session_factory, project_context, second)
    second_baseline = case_service.adopt_baseline(second.id, second_evidence, "admin")

    assert case_service.get_baseline(first_baseline.id).status == "superseded"
    assert case_service.get_baseline(second_baseline.id).status == "active"
    with session_factory() as session:
        assert session.scalar(
            select(func.count(ApiBaseline.id)).where(ApiBaseline.case_id == first.case_id)
        ) == 2


def test_new_revision_of_same_environment_supersedes_old_baseline(
    case_service, project_context, session_factory
):
    endpoint = project_context["endpoints"]["favoriteList"]
    first = case_service.create_draft(endpoint.id, valid_list_case(endpoint), "manual", "admin")
    first_evidence = _create_execution_evidence(session_factory, project_context, first)
    first_baseline = case_service.adopt_baseline(first.id, first_evidence, "admin")

    with session_factory.begin() as session:
        new_environment_revision = ApiEnvironmentRevision(
            environment_id=project_context["environment"].id,
            source_revision_id=project_context["source_revision"].id,
            revision_number=2,
            name=project_context["environment"].name,
            default_headers={},
            **_audit(),
        )
        session.add(new_environment_revision)
        session.flush()
        environment = session.get(ApiEnvironment, project_context["environment"].id)
        environment.active_revision_id = new_environment_revision.id

    second = case_service.create_version(first.case_id, valid_list_case(endpoint), "admin")
    second_evidence = _create_execution_evidence(
        session_factory,
        project_context,
        second,
        environment_revision_id=new_environment_revision.id,
    )
    second_baseline = case_service.adopt_baseline(second.id, second_evidence, "admin")

    assert case_service.get_baseline(first_baseline.id).status == "superseded"
    assert case_service.get_baseline(second_baseline.id).status == "active"


def test_case_and_baseline_views_do_not_expose_execution_payloads(
    case_service, project_context, session_factory
):
    endpoint = project_context["endpoints"]["favoriteList"]
    payload = valid_list_case(endpoint)
    payload["request"]["headers"]["Authorization"] = "Bearer {{ZXBToken}}"
    draft = case_service.create_draft(endpoint.id, payload, "manual", "admin")
    evidence_id = _create_execution_evidence(session_factory, project_context, draft)
    baseline = case_service.adopt_baseline(draft.id, evidence_id, "admin")

    assert not hasattr(baseline, "sanitized_result")
    assert not hasattr(baseline, "request")
    assert "ZXBToken" in repr(draft)
    assert SYNTHETIC_SECRET not in repr(draft)
    assert SYNTHETIC_SECRET not in repr(baseline)
