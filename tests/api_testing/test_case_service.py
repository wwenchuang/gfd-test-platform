import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker
import pytest

from task_server.services import business_line_service

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
from task_server.api_testing.models.execution import (
    ApiExecution,
    ApiExecutionAttempt,
    ApiExecutionCase,
)
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
        "app_package": "com.kfb.model",
        "app_name": "智小白3D",
        "business": "home",
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
    payload = valid_list_case(endpoint)
    payload["business"] = "shared"
    draft = case_service.create_draft(endpoint.id, payload, "manual", "admin")

    assert draft.status == "draft"
    assert draft.version == 1
    assert draft.origin == "manual"
    assert draft.business == "shared"
    assert draft.request["method"] == "GET"
    assert draft.data_rows[0].values["Biz"] == "ZXB"
    assert draft.assertions[1].path == "$.code"
    assert draft.extractions[0].target == "favoriteId"
    assert draft.dependencies == ()
    assert draft.processing["pre"][0]["action"] == "set_variable"
    assert SYNTHETIC_SECRET not in repr(draft)

    with session_factory() as session:
        case = session.get(ApiCase, draft.case_id)
        version = session.get(ApiCaseVersion, draft.id)
        assert case.active_version_id == draft.id
        assert version.request_template["business"] == "shared"
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


def test_draft_persists_application_identity_and_legacy_versions_stay_readable(
    case_service, project_context, session_factory
):
    endpoint = project_context["endpoints"]["favoriteList"]
    payload = valid_list_case(endpoint)
    payload.update({
        "app_package": "com.kfb.model",
        "app_name": "客户端伪造名称",
        "business": "home",
    })

    draft = case_service.create_draft(endpoint.id, payload, "manual", "admin")
    next_payload = valid_list_case(endpoint)
    next_payload.update({
        "name": "查询我的收藏-第二版",
        "app_package": "com.kfb.model",
        "app_name": "另一个客户端名称",
        "business": "shared",
    })
    second = case_service.create_version(draft.case_id, next_payload, "editor")

    assert draft.app_package == "com.kfb.model"
    assert draft.app_name == "智小白3D"
    assert second.version == 2
    assert second.app_package == "com.kfb.model"
    assert second.app_name == "智小白3D"
    assert second.business == "shared"
    reloaded_first = case_service.get_version(draft.id)
    reloaded_second = case_service.get_version(second.id)
    assert (reloaded_first.app_package, reloaded_first.app_name, reloaded_first.business) == (
        "com.kfb.model", "智小白3D", "home",
    )
    assert (reloaded_second.app_package, reloaded_second.app_name, reloaded_second.business) == (
        "com.kfb.model", "智小白3D", "shared",
    )
    with session_factory() as session:
        version = session.get(ApiCaseVersion, draft.id)
        next_version = session.get(ApiCaseVersion, second.id)
        assert version.request_template["app_package"] == "com.kfb.model"
        assert version.request_template["app_name"] == "智小白3D"
        assert next_version.request_template["app_package"] == "com.kfb.model"
        assert next_version.request_template["app_name"] == "智小白3D"
        assert next_version.request_template["business"] == "shared"
        version.request_template = {
            key: value for key, value in version.request_template.items()
            if key not in {"app_package", "app_name"}
        }
        session.commit()

    legacy = case_service.get_version(draft.id)
    assert legacy.app_package == ""
    assert legacy.app_name == ""


def test_disabled_application_case_can_be_edited_but_keeps_historical_scope(
    case_service, project_context, tmp_path, monkeypatch
):
    endpoint = project_context["endpoints"]["favoriteList"]
    draft = case_service.create_draft(endpoint.id, valid_list_case(endpoint), "manual", "admin")
    path = tmp_path / "task-apps.json"
    path.write_text(json.dumps({"apps": [{
        "package": "com.kfb.model",
        "name": "智小白3D",
        "enabled": False,
        "business_lines": [{"id": "home", "name": "家用", "enabled": False}],
    }]}), encoding="utf-8")
    monkeypatch.setattr(business_line_service, "TASK_APPS_FILE", str(path))
    edited_payload = valid_list_case(endpoint)
    edited_payload["name"] = "查询我的收藏-历史说明修订"

    edited = case_service.create_version(draft.case_id, edited_payload, "admin")

    assert edited.version == 2
    assert edited.app_name == "智小白3D"
    assert edited.business == "home"


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


@pytest.mark.parametrize(
    "assertion, message",
    [
        (
            {"type": "status_code", "operator": "matches", "expected": "["},
            "not supported",
        ),
        (
            {"type": "status_code", "operator": "equals", "expected": 99},
            "status code",
        ),
        (
            {"type": "status_code", "operator": "in", "expected": [200, 700]},
            "status code",
        ),
        (
            {"type": "json_path", "path": "$.code", "operator": "matches", "expected": "["},
            "regular expression",
        ),
        (
            {"type": "json_path", "path": "$.data", "operator": "contains", "expected": {"x": 1}},
            "scalar",
        ),
        (
            {"type": "json_path", "path": "$.count", "operator": "greater_than", "expected": "1"},
            "number",
        ),
        (
            {"type": "header", "name": "X-Trace", "operator": "in", "expected": "abc"},
            "array",
        ),
        (
            {"type": "response_time", "operator": "less_than", "expected": -1},
            "non-negative",
        ),
        (
            {"type": "json_path", "path": "$.data", "operator": "exists", "expected": True},
            "must not define expected",
        ),
        (
            {"type": "schema", "operator": "equals", "expected": "not-a-schema"},
            "schema object",
        ),
        (
            {"type": "schema", "operator": "equals", "expected": {"type": "object"}},
            "must constrain response fields",
        ),
    ],
)
def test_assertion_matrix_rejects_unsupported_operator_operands(
    case_service, project_context, assertion, message
):
    from task_server.api_testing.contracts.case import CasePayloadError

    endpoint = project_context["endpoints"]["favoriteList"]
    payload = valid_list_case(endpoint)
    payload["assertions"] = [assertion]

    with pytest.raises(CasePayloadError, match=message):
        case_service.create_draft(endpoint.id, payload, "manual", "admin")


def test_phase1_rejects_nonempty_dependency_condition_before_persistence(
    case_service, project_context, session_factory
):
    from task_server.api_testing.contracts.case import CasePayloadError

    endpoint = project_context["endpoints"]["favoriteList"]
    payload = valid_list_case(endpoint)
    payload["dependencies"] = [
        {
            "case_version_id": "11111111-1111-4111-8111-111111111111",
            "required": True,
            "exports": [],
            "condition": "__import__('os').system('echo bypass')",
        }
    ]

    with session_factory() as session:
        count_before = session.scalar(select(func.count(ApiCase.id)))

    with pytest.raises(CasePayloadError, match="condition"):
        case_service.create_draft(endpoint.id, payload, "manual", "admin")

    with session_factory() as session:
        assert session.scalar(select(func.count(ApiCase.id))) == count_before


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


def test_each_enabled_data_row_must_resolve_request_variables(
    case_service, project_context
):
    from task_server.api_testing.validation import validate_case

    endpoint_view = project_context["endpoints"]["favoriteList"]
    payload = valid_list_case(endpoint_view)
    payload["request"]["headers"]["X-Row"] = "{{rowOnly}}"
    payload["data_rows"] = [
        {"name": "完整数据", "values": {"Biz": "ZXB", "rowOnly": "ok"}, "enabled": True},
        {"name": "缺失数据", "values": {"Biz": "ZXB"}, "enabled": True},
        {"name": "已禁用", "values": {"Biz": "ZXB"}, "enabled": False},
    ]
    draft = case_service.create_draft(endpoint_view.id, payload, "manual", "admin")
    with case_service.session_factory() as session:
        endpoint = session.get(ApiSourceEndpoint, endpoint_view.id)
        result = validate_case(draft, endpoint, {"variables": {}, "services": {}})

    missing = [item for item in result.errors if item.code == "undefined_variable"]
    assert len(missing) == 1
    assert "缺失数据" in missing[0].field
    assert "rowOnly" in missing[0].message


def test_template_variables_are_validated_when_no_data_rows(
    case_service, project_context
):
    from task_server.api_testing.validation import validate_case

    endpoint_view = project_context["endpoints"]["favoriteList"]
    payload = valid_list_case(endpoint_view)
    payload["data_rows"] = []
    payload["request"]["headers"]["X-Missing"] = "{{templateOnly}}"
    draft = case_service.create_draft(endpoint_view.id, payload, "manual", "admin")
    with case_service.session_factory() as session:
        endpoint = session.get(ApiSourceEndpoint, endpoint_view.id)
        result = validate_case(draft, endpoint, {"variables": {"Biz": "ZXB"}})

    assert any(
        item.code == "undefined_variable" and "templateOnly" in item.message
        for item in result.errors
    )


def test_dependencies_use_repository_identity_and_real_extraction_targets(
    case_service, project_context, session_factory
):
    endpoint = project_context["endpoints"]["favoriteList"]
    producer_payload = valid_list_case(endpoint)
    producer_payload["name"] = "收藏前置数据"
    producer_payload["extractions"] = [
        {"target": "realExport", "type": "json_path", "path": "$.data[0].id"}
    ]
    producer = case_service.create_draft(endpoint.id, producer_payload, "manual", "admin")

    consumer_payload = valid_list_case(endpoint)
    consumer_payload["request"]["headers"]["X-Dependency"] = "{{realExport}}"
    consumer_payload["dependencies"] = [
        {"case_version_id": producer.id, "required": True, "exports": ["realExport"]}
    ]
    consumer = case_service.create_draft(endpoint.id, consumer_payload, "manual", "admin")
    valid_result = case_service.validate_case(consumer.id, {"variables": {"Biz": "ZXB"}})
    assert valid_result.valid is True

    random_payload = valid_list_case(endpoint)
    random_payload["request"]["headers"]["X-Ghost"] = "{{ghostExport}}"
    random_payload["dependencies"] = [
        {
            "case_version_id": "22222222-2222-4222-8222-222222222222",
            "required": True,
            "exports": ["ghostExport"],
        }
    ]
    random_case = case_service.create_draft(endpoint.id, random_payload, "manual", "admin")
    random_result = case_service.validate_case(random_case.id, {"variables": {"Biz": "ZXB"}})
    assert {item.code for item in random_result.errors} >= {
        "dependency_not_found",
        "undefined_variable",
    }

    fake_export_payload = valid_list_case(endpoint)
    fake_export_payload["request"]["headers"]["X-Fake"] = "{{fakeExport}}"
    fake_export_payload["dependencies"] = [
        {"case_version_id": producer.id, "required": True, "exports": ["fakeExport"]}
    ]
    fake_case = case_service.create_draft(endpoint.id, fake_export_payload, "manual", "admin")
    fake_result = case_service.validate_case(fake_case.id, {"variables": {"Biz": "ZXB"}})
    assert {item.code for item in fake_result.errors} >= {
        "dependency_export_invalid",
        "undefined_variable",
    }

    with session_factory.begin() as session:
        other_project = ApiProject(
            name=f"Cross Project {os.urandom(4).hex()}",
            slug=f"cross-project-{os.urandom(6).hex()}",
            **_audit(),
        )
        session.add(other_project)
        session.flush()
        cross_case = ApiCase(
            project_id=other_project.id,
            endpoint_id=endpoint.id,
            name="跨项目依赖",
            status="draft",
            origin="manual",
            **_audit(),
        )
        session.add(cross_case)
        session.flush()
        cross_version = ApiCaseVersion(
            case_id=cross_case.id,
            endpoint_id=endpoint.id,
            version_number=1,
            status="draft",
            purpose="synthetic cross-project dependency",
            priority="P1",
            request_template={"name": "跨项目依赖", "request": valid_list_case(endpoint)["request"]},
            validation_summary={},
            dependency_spec={"dependencies": []},
            processing_spec={"pre": [], "post": []},
            **_audit(),
        )
        session.add(cross_version)
        session.flush()
        session.add(
            ApiCaseExtraction(
                case_version_id=cross_version.id,
                target_name="crossExport",
                extraction_type="json_path",
                definition={"path": "$.id", "required": True},
                **_audit(),
            )
        )
        cross_case.active_version_id = cross_version.id

    cross_payload = valid_list_case(endpoint)
    cross_payload["request"]["headers"]["X-Cross"] = "{{crossExport}}"
    cross_payload["dependencies"] = [
        {"case_version_id": cross_version.id, "required": True, "exports": ["crossExport"]}
    ]
    cross_consumer = case_service.create_draft(endpoint.id, cross_payload, "manual", "admin")
    cross_result = case_service.validate_case(cross_consumer.id, {"variables": {"Biz": "ZXB"}})
    assert {item.code for item in cross_result.errors} >= {
        "dependency_project_mismatch",
        "undefined_variable",
    }


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


def test_validate_case_accepts_required_headers_managed_by_environment(
    case_service, project_context
):
    from task_server.api_testing.validation import validate_case

    endpoint_view = project_context["endpoints"]["favoriteList"]
    payload = valid_list_case(endpoint_view)
    payload["request"]["headers"] = {}
    payload["data_rows"] = []
    draft = case_service.create_draft(
        endpoint_view.id, payload, "manual", "admin"
    )
    with case_service.session_factory() as session:
        endpoint = session.get(ApiSourceEndpoint, endpoint_view.id)
        result = validate_case(
            draft,
            endpoint,
            {
                "variables": {},
                "headers": {"Biz": {"configured": True}},
                "services": {},
            },
        )

    assert result.valid is True
    assert not any(item.code == "required_parameter_missing" for item in result.errors)


def test_validate_case_resolves_referenced_request_body_contract(
    case_service, project_context
):
    from task_server.api_testing.validation import validate_case

    endpoint_view = project_context["endpoints"]["favoriteAdd"]
    payload = valid_add_case(endpoint_view)
    payload["request"]["body"] = {}
    draft = case_service.create_draft(
        endpoint_view.id, payload, "manual", "admin"
    )
    with case_service.session_factory() as session:
        endpoint = session.get(ApiSourceEndpoint, endpoint_view.id)
        operation = copy.deepcopy(endpoint.operation)
        original_body = operation["requestBody"]
        operation["requestBody"] = {"$ref": "#/components/requestBodies/AddFavorite"}
        operation.setdefault("resolved_dependencies", {})[
            "#/components/requestBodies/AddFavorite"
        ] = original_body
        endpoint.operation = operation
        session.flush()
        result = validate_case(draft, endpoint, {"variables": {}, "services": {}})

    assert any(item.code == "body_required_property" for item in result.errors)


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
    parent_state="DONE",
    response=None,
):
    with session_factory.begin() as session:
        execution = ApiExecution(
            project_id=project_id or context["project"].id,
            source_revision_id=source_revision_id or context["source_revision"].id,
            environment_revision_id=(
                environment_revision_id or context["environment_revision"].id
            ),
            execution_type=execution_type,
            state=parent_state,
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
        if response is not None:
            session.add(
                ApiExecutionAttempt(
                    execution_case_id=evidence.id,
                    attempt_number=1,
                    status=status,
                    request={"headers": {"Authorization": "***"}},
                    response=copy.deepcopy(response),
                    assertion_results=[],
                    **_audit(),
                )
            )
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


def test_baseline_assertion_audit_batches_stored_assertions_and_debug_evidence(
    case_service, project_context, session_factory
):
    from task_server.api_testing.services.baseline_assertion_audit_service import (
        BaselineAssertionAuditService,
    )

    endpoint = project_context["endpoints"]["favoriteList"]
    payload = valid_list_case(endpoint)
    payload["assertions"] = [
        {"type": "status_code", "operator": "equals", "expected": 200}
    ]
    draft = case_service.create_draft(endpoint.id, payload, "manual", "admin")
    evidence_id = _create_execution_evidence(
        session_factory,
        project_context,
        draft,
        response={
            "status_code": 200,
            "body": json.dumps({"code": 0, "data": []}),
            "headers": {"Authorization": SYNTHETIC_SECRET},
        },
    )
    baseline = case_service.adopt_baseline(draft.id, evidence_id, "admin")

    result = BaselineAssertionAuditService(session_factory).list(
        project_context["project"].id,
        "admin",
    )

    item = next(value for value in result["items"] if value["baseline_id"] == baseline.id)
    assert item["status"] == "upgrade_available"
    assert item["business_path"] == "$.code"
    assert item["business_value"] == 0
    assert item["execution"]["level"] == "direct"
    assert result["summary"]["upgrade_available"] >= 1
    assert SYNTHETIC_SECRET not in repr(result)


def test_baseline_assertion_audit_reads_large_projects_in_bounded_batches(
    case_service, project_context, session_factory, monkeypatch
):
    from task_server.api_testing.repositories.case_repository import CaseRepository
    from task_server.api_testing.services.baseline_assertion_audit_service import (
        BaselineAssertionAuditService,
    )

    endpoint = project_context["endpoints"]["favoriteList"]
    baseline_ids = []
    for index in range(5):
        payload = valid_list_case(endpoint)
        payload["name"] = f"分批审计用例-{index}"
        payload["assertions"] = [
            {"type": "status_code", "operator": "equals", "expected": 200}
        ]
        draft = case_service.create_draft(endpoint.id, payload, "manual", "admin")
        evidence_id = _create_execution_evidence(
            session_factory,
            project_context,
            draft,
            response={
                "status_code": 200,
                "body": json.dumps({"code": 0, "data": []}),
                "headers": {},
            },
        )
        baseline_ids.append(case_service.adopt_baseline(draft.id, evidence_id, "admin").id)

    calls = []
    original = CaseRepository.list_active_baselines

    def observed(self, project_id, actor_id, **options):
        calls.append((options.get("limit"), options.get("offset")))
        return original(self, project_id, actor_id, **options)

    monkeypatch.setattr(CaseRepository, "list_active_baselines", observed)
    monkeypatch.setattr(BaselineAssertionAuditService, "BATCH_SIZE", 2, raising=False)

    result = BaselineAssertionAuditService(session_factory).list(
        project_context["project"].id,
        "admin",
    )

    assert set(baseline_ids).issubset({item["baseline_id"] for item in result["items"]})
    assert calls == [(2, 0), (2, 2), (2, 4), (2, 5)]


def test_latest_execution_attempt_query_only_loads_latest_attempt(
    case_service, project_context, session_factory
):
    from task_server.api_testing.repositories.case_repository import CaseRepository

    endpoint = project_context["endpoints"]["favoriteList"]
    draft = case_service.create_draft(
        endpoint.id,
        valid_list_case(endpoint),
        "manual",
        "admin",
    )
    evidence_id = _create_execution_evidence(
        session_factory,
        project_context,
        draft,
        response={
            "status_code": 200,
            "body": json.dumps({"code": 0, "attempt": 1}),
            "headers": {},
        },
    )
    with session_factory.begin() as session:
        for attempt_number in (2, 3):
            session.add(
                ApiExecutionAttempt(
                    execution_case_id=evidence_id,
                    attempt_number=attempt_number,
                    status="PASSED",
                    request={"attempt": attempt_number},
                    response={
                        "status_code": 200,
                        "body": json.dumps({"code": 0, "attempt": attempt_number}),
                        "headers": {},
                    },
                    assertion_results=[],
                    **_audit(),
                )
            )

    loaded_attempts = []

    def record_load(target, _context):
        loaded_attempts.append(target.attempt_number)

    event.listen(ApiExecutionAttempt, "load", record_load)
    try:
        with session_factory() as session:
            latest = CaseRepository(session).latest_execution_attempts([evidence_id])
    finally:
        event.remove(ApiExecutionAttempt, "load", record_load)

    assert latest[evidence_id].attempt_number == 3
    assert loaded_attempts == [3]


def test_baseline_audit_does_not_parse_an_oversized_response_body(monkeypatch):
    from task_server.api_testing.services import baseline_assertion_audit_service as audit

    monkeypatch.setattr(audit, "MAX_AUDIT_BODY_PARSE_CHARS", 32)
    body = '{"code":0,"payload":"' + ("x" * 500) + '"}'
    original_loads = audit.json.loads

    def guarded_loads(value, *args, **kwargs):
        assert len(value) <= 32
        return original_loads(value, *args, **kwargs)

    monkeypatch.setattr(audit.json, "loads", guarded_loads)

    result = audit.analyze_baseline_assertions(
        [{"type": "status_code", "operator": "equals", "expected": 200}],
        {"status_code": 200, "body": body},
        {"baseline_policy": "direct"},
        {},
    )

    assert result["status"] == "upgrade_available"
    assert result["business_path"] == "$.code"
    assert result["business_value"] == 0


def test_baseline_audit_does_not_treat_nested_code_as_top_level_evidence(monkeypatch):
    from task_server.api_testing.services import baseline_assertion_audit_service as audit

    monkeypatch.setattr(audit, "MAX_AUDIT_BODY_PARSE_CHARS", 64)
    body = '{"data":{"code":0},"payload":"' + ("x" * 500) + '"}'

    result = audit.analyze_baseline_assertions(
        [{"type": "status_code", "operator": "equals", "expected": 200}],
        {"status_code": 200, "body": body},
        {"baseline_policy": "direct"},
        {},
    )

    assert result["status"] == "domain_assertion_required"
    assert result["business_path"] == ""


def test_baseline_assertion_audit_creates_a_review_draft_without_replacing_baseline(
    case_service, project_context, session_factory
):
    from task_server.api_testing.services.baseline_assertion_audit_service import (
        BaselineAssertionAuditService,
        BaselineAssertionUpgradeError,
    )

    endpoint = project_context["endpoints"]["favoriteList"]
    dependency_endpoint = project_context["endpoints"]["favoriteAdd"]
    dependency = case_service.create_draft(
        dependency_endpoint.id,
        valid_add_case(dependency_endpoint),
        "manual",
        "admin",
    )
    payload = valid_list_case(endpoint)
    payload["dependencies"] = [{
        "case_version_id": dependency.id,
        "required": True,
        "exports": ["favoriteId"],
    }]
    payload["assertions"] = [
        {"type": "status_code", "operator": "equals", "expected": 200}
    ]
    draft = case_service.create_draft(endpoint.id, payload, "manual", "admin")
    evidence_id = _create_execution_evidence(
        session_factory,
        project_context,
        draft,
        response={
            "status_code": 200,
            "body": json.dumps({"code": 0, "data": []}),
            "headers": {},
        },
    )
    baseline = case_service.adopt_baseline(draft.id, evidence_id, "admin")

    result = BaselineAssertionAuditService(session_factory).create_upgrade_draft(
        baseline.id,
        "admin",
    )

    upgraded = result["case_version"]
    assert upgraded.version == 2
    assert upgraded.case_id == draft.case_id
    assert [(item.type, item.path, item.expected) for item in upgraded.assertions] == [
        ("status_code", None, 200),
        ("json_path", "$.code", 0),
    ]
    assert tuple(upgraded.dependencies) == ({
        "case_version_id": dependency.id,
        "required": True,
        "exports": ["favoriteId"],
    },)
    assert result["source_baseline_id"] == baseline.id
    assert result["source_case_version_id"] == draft.id
    assert result["suggestion_count"] == 1

    active_baselines = case_service.list_active_baselines(
        project_context["project"].id,
        "admin",
    )
    unchanged = next(item for item in active_baselines if item.id == baseline.id)
    assert unchanged.case_version_id == draft.id
    assert unchanged.status == "active"

    audit = BaselineAssertionAuditService(session_factory).list(
        project_context["project"].id,
        "admin",
    )
    audited = next(item for item in audit["items"] if item["baseline_id"] == baseline.id)
    assert audited["upgrade_draft_case_version_id"] == upgraded.id

    with pytest.raises(BaselineAssertionUpgradeError, match="已有较新的用例版本"):
        BaselineAssertionAuditService(session_factory).create_upgrade_draft(
            baseline.id,
            "admin",
        )


def test_baseline_assertion_audit_never_turns_business_failure_into_success_draft(
    case_service, project_context, session_factory
):
    from task_server.api_testing.services.baseline_assertion_audit_service import (
        BaselineAssertionAuditService,
        BaselineAssertionUpgradeError,
    )

    endpoint = project_context["endpoints"]["favoriteList"]
    payload = valid_list_case(endpoint)
    payload["assertions"] = [
        {"type": "status_code", "operator": "equals", "expected": 200}
    ]
    draft = case_service.create_draft(endpoint.id, payload, "manual", "admin")
    evidence_id = _create_execution_evidence(
        session_factory,
        project_context,
        draft,
        response={
            "status_code": 200,
            "body": json.dumps({"code": 1001, "message": "设备不存在"}),
            "headers": {},
        },
    )
    baseline = case_service.adopt_baseline(draft.id, evidence_id, "admin")

    with pytest.raises(BaselineAssertionUpgradeError, match="不能生成待复核版本"):
        BaselineAssertionAuditService(session_factory).create_upgrade_draft(
            baseline.id,
            "admin",
        )


def test_active_case_list_exposes_debug_and_baseline_lifecycle(
    case_service, project_context, session_factory
):
    endpoint = project_context["endpoints"]["favoriteList"]
    draft = case_service.create_draft(
        endpoint.id,
        valid_list_case(endpoint),
        "manual",
        "admin",
    )
    evidence_id = _create_execution_evidence(
        session_factory,
        project_context,
        draft,
    )
    baseline = case_service.adopt_baseline(draft.id, evidence_id, "admin")

    listed = case_service.list_active_versions_for_source_revision(
        project_context["source_revision"].id,
        "admin",
    )

    current = next(item for item in listed if item.case_id == draft.case_id)
    assert current.lifecycle["debug_status"] == "PASSED"
    assert current.lifecycle["baseline_status"] == "active"
    assert current.lifecycle["baseline_id"] == baseline.id


@pytest.mark.parametrize(
    "parent_state",
    ["QUEUED", "RUNNING", "FAILED", "BROKEN", "CANCELLED", "PASSED"],
)
def test_baseline_rejects_passed_child_under_non_done_parent(
    case_service, project_context, session_factory, parent_state
):
    from task_server.api_testing.services.case_service import BaselineGateError

    endpoint = project_context["endpoints"]["favoriteList"]
    draft = case_service.create_draft(endpoint.id, valid_list_case(endpoint), "manual", "admin")
    evidence_id = _create_execution_evidence(
        session_factory,
        project_context,
        draft,
        status="PASSED",
        parent_state=parent_state,
    )

    with pytest.raises(BaselineGateError, match="successful terminal"):
        case_service.adopt_baseline(draft.id, evidence_id, "admin")


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


def test_readoption_keeps_multiple_active_baseline_versions_until_manual_archive(
    case_service, project_context, session_factory
):
    endpoint = project_context["endpoints"]["favoriteList"]
    first = case_service.create_draft(endpoint.id, valid_list_case(endpoint), "manual", "admin")
    first_evidence = _create_execution_evidence(session_factory, project_context, first)
    first_baseline = case_service.adopt_baseline(first.id, first_evidence, "admin")

    second = case_service.create_version(first.case_id, valid_list_case(endpoint), "admin")
    second_evidence = _create_execution_evidence(session_factory, project_context, second)
    second_baseline = case_service.adopt_baseline(second.id, second_evidence, "admin")

    assert case_service.get_baseline(first_baseline.id).status == "active"
    assert case_service.get_baseline(second_baseline.id).status == "active"
    active = case_service.list_active_baselines(project_context["project"].id, "admin")
    assert {item.id for item in active} >= {first_baseline.id, second_baseline.id}

    archived = case_service.archive_baseline(first_baseline.id, "admin")
    active_after_archive = case_service.list_active_baselines(
        project_context["project"].id,
        "admin",
    )

    assert archived.status == "archived"
    assert first_baseline.id not in {item.id for item in active_after_archive}
    assert second_baseline.id in {item.id for item in active_after_archive}
    with session_factory() as session:
        assert session.scalar(
            select(func.count(ApiBaseline.id)).where(ApiBaseline.case_id == first.case_id)
        ) == 2


def test_new_environment_revision_keeps_old_baseline_visible_until_manual_archive(
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

    assert case_service.get_baseline(first_baseline.id).status == "active"
    assert case_service.get_baseline(second_baseline.id).status == "active"
    active = case_service.list_active_baselines(project_context["project"].id, "admin")
    active_ids = {item.id for item in active}
    assert {first_baseline.id, second_baseline.id} <= active_ids
    assert case_service.get_baseline(first_baseline.id).environment_revision_id != (
        case_service.get_baseline(second_baseline.id).environment_revision_id
    )


def test_historical_superseded_baseline_remains_manageable_until_archived(
    case_service, project_context, session_factory
):
    endpoint = project_context["endpoints"]["favoriteList"]
    draft = case_service.create_draft(endpoint.id, valid_list_case(endpoint), "manual", "admin")
    evidence_id = _create_execution_evidence(session_factory, project_context, draft)
    baseline = case_service.adopt_baseline(draft.id, evidence_id, "admin")

    with session_factory.begin() as session:
        historical = session.get(ApiBaseline, baseline.id)
        historical.status = "superseded"

    visible = case_service.list_active_baselines(project_context["project"].id, "admin")

    assert [item.id for item in visible] == [baseline.id]
    assert visible[0].status == "superseded"

    updated = case_service.update_baseline_group([baseline.id], "历史稳定集", "admin")
    assert updated[0].group_name == "历史稳定集"

    archived = case_service.archive_baseline(baseline.id, "admin")
    active_after_archive = case_service.list_active_baselines(
        project_context["project"].id,
        "admin",
    )

    assert archived.status == "archived"
    assert baseline.id not in {item.id for item in active_after_archive}


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


def test_project_baselines_remain_visible_across_source_and_environment_revisions(
    case_service, project_context, session_factory
):
    endpoint = project_context["endpoints"]["favoriteList"]
    draft = case_service.create_draft(endpoint.id, valid_list_case(endpoint), "manual", "admin")
    evidence_id = _create_execution_evidence(session_factory, project_context, draft)
    baseline = case_service.adopt_baseline(draft.id, evidence_id, "admin")

    from task_server.api_testing.services.source_service import SourceService

    source_service = SourceService(session_factory)
    preview = source_service.preview_refresh(
        project_context["project"].id,
        project_context["source_revision"].source_id,
        copy.deepcopy(FAVORITES_OPENAPI),
        "admin",
    )
    new_revision = source_service.activate_preview(preview.id, "admin")
    with session_factory.begin() as session:
        new_environment_revision = ApiEnvironmentRevision(
            environment_id=project_context["environment"].id,
            source_revision_id=new_revision.id,
            revision_number=2,
            name=project_context["environment"].name,
            default_headers={"Authorization": "Bearer {{ZXBToken}}"},
            **_audit(),
        )
        session.add(new_environment_revision)
        session.flush()
        environment = session.get(ApiEnvironment, project_context["environment"].id)
        environment.active_revision_id = new_environment_revision.id

    baselines = case_service.list_active_baselines(
        project_context["project"].id,
        "admin",
    )

    assert [item.id for item in baselines] == [baseline.id]
    assert baselines[0].source_revision_id == project_context["source_revision"].id
    assert baselines[0].environment_revision_id == project_context["environment_revision"].id
    assert baselines[0].case_version_id == draft.id


def test_archived_case_is_removed_from_active_case_list(
    case_service, project_context, session_factory
):
    endpoint = project_context["endpoints"]["favoriteList"]
    draft = case_service.create_draft(endpoint.id, valid_list_case(endpoint), "ai", "admin")

    archived = case_service.archive_case(draft.case_id, "admin")
    active = case_service.list_active_versions_for_source_revision(
        project_context["source_revision"].id, "admin"
    )

    assert archived.status == "archived"
    assert all(item.case_id != draft.case_id for item in active)
    with session_factory() as session:
        case = session.get(ApiCase, draft.case_id)
        assert case.status == "archived"
        assert case.active_version_id is None


def test_case_follows_stable_endpoint_and_adapts_lazily_to_new_source_revision(
    case_service, project_context, session_factory
):
    from task_server.api_testing.services.source_service import SourceService

    old_endpoint = project_context["endpoints"]["favoriteList"]
    first = case_service.create_draft(
        old_endpoint.id,
        valid_list_case(old_endpoint),
        "manual",
        "admin",
    )
    preview = SourceService(session_factory).preview_refresh(
        project_context["project"].id,
        project_context["source_revision"].source_id,
        copy.deepcopy(FAVORITES_OPENAPI),
        "admin",
    )
    new_revision = SourceService(session_factory).activate_preview(preview.id, "admin")
    current_endpoint = next(
        item for item in new_revision.endpoints if item.operation_id == "favoriteList"
    )

    projected = case_service.list_active_versions_for_source_revision(
        new_revision.id,
        "admin",
    )

    inherited = next(item for item in projected if item.case_id == first.case_id)
    assert inherited.id == first.id
    assert inherited.endpoint_id == old_endpoint.id
    assert inherited.current_endpoint_id == current_endpoint.id
    assert inherited.source_state == "needs_adaptation"

    adapted = case_service.create_version(
        first.case_id,
        valid_list_case(current_endpoint),
        "admin",
        endpoint_id=current_endpoint.id,
    )

    assert adapted.endpoint_id == current_endpoint.id
    assert adapted.current_endpoint_id == current_endpoint.id
    assert adapted.source_state == "current"
    with session_factory() as session:
        assert session.get(ApiCaseVersion, first.id).endpoint_id == old_endpoint.id
        assert session.get(ApiCaseVersion, adapted.id).endpoint_id == current_endpoint.id
        case = session.get(ApiCase, first.case_id)
        assert case.endpoint_id == current_endpoint.id
        assert case.active_version_id == adapted.id


def test_case_version_group_can_be_updated_independently(
    case_service, project_context, session_factory
):
    endpoint = project_context["endpoints"]["favoriteList"]
    draft = case_service.create_draft(endpoint.id, valid_list_case(endpoint), "ai", "admin")

    updated = case_service.update_case_version_group(draft.id, "发版回归", "admin")
    active = case_service.list_active_versions_for_source_revision(
        project_context["source_revision"].id, "admin"
    )

    assert updated.id == draft.id
    assert updated.group_name == "发版回归"
    next_version = case_service.create_version(draft.case_id, valid_list_case(endpoint), "admin")
    assert next_version.group_name == "发版回归"
    assert [item.group_name for item in active if item.id == draft.id] == ["发版回归"]
    with session_factory() as session:
        assert session.get(ApiCaseVersion, draft.id).group_name == "发版回归"
        assert session.get(ApiCaseVersion, next_version.id).group_name == "发版回归"
