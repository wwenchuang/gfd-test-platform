import copy
import json
import os
from pathlib import Path
import threading

from alembic import command
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
import pytest

from task_server.api_testing.models.case import ApiAiJob, ApiAiJobBatch, ApiCase
from task_server.api_testing.models.project import ApiProject
from task_server.api_testing.models.source import ApiSourceEndpoint
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
SYNTHETIC_SECRET = "task7-secret-must-never-reach-ai"
SYNTHETIC_PUBLIC_VALUE = "task7-public-value-must-never-reach-ai"
SYNTHETIC_JWT = (
    "eyJhbGciOiJIUzI1NiJ9."
    "eyJzdWIiOiJ0YXNrLTctc2VjcmV0LXJlZ3Jlc3Npb24ifQ."
    "c3ludGhldGljLXNpZ25hdHVyZS12YWx1ZQ"
)
SYNTHETIC_FERNET = "gAAAAABtask7SyntheticCiphertextValue0123456789abcdef"
SYNTHETIC_FINGERPRINT = "a7" * 32


def _audit(actor="admin"):
    return {"owner_id": actor, "created_by": actor, "updated_by": actor}


@pytest.fixture(scope="module")
def ai_database():
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
def session_factory(ai_database):
    engine = create_engine(ai_database, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture()
def ai_context(session_factory, monkeypatch):
    from task_server.api_testing.services.environment_service import EnvironmentService
    from task_server.api_testing.services.source_service import SourceService

    monkeypatch.setenv("API_TESTING_SECRET_KEY", "task7-test-encryption-key-with-32-bytes")
    suffix = os.urandom(6).hex()
    with session_factory.begin() as session:
        project = ApiProject(
            name=f"Task 7 Project {suffix}",
            slug=f"task-7-project-{suffix}",
            description="AI case generation tests",
            **_audit(),
        )
        session.add(project)
        session.flush()

    source_service = SourceService(session_factory)
    preview = source_service.preview_refresh(
        project.id, None, copy.deepcopy(FAVORITES_OPENAPI), "admin"
    )
    revision = source_service.activate_preview(preview.id, "admin")
    environment_service = EnvironmentService(session_factory)
    environment = environment_service.import_from_source(
        {
            "project_id": project.id,
            "source_id": revision.source_id,
            "source_revision_id": revision.id,
            "name": f"Task 7 Environment {suffix}",
            "services": {
                "default": {"base_url": "https://example.invalid/app"},
                "optional": {"base_url": None},
            },
            "variables": {"Biz": SYNTHETIC_PUBLIC_VALUE, "pageNum": 1},
            "default_headers": {"Authorization": "Bearer {{ZXBToken}}"},
        },
        "admin",
    )
    environment = environment_service.create_revision(
        environment.id,
        {},
        {"ZXBToken": SYNTHETIC_SECRET},
        "admin",
    )
    endpoints = {item.operation_id: item for item in revision.endpoints}
    return {
        "project": project,
        "source_revision": revision,
        "environment": environment,
        "endpoints": endpoints,
    }


class FakeGateway:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def chat(self, *, messages, provider_id, model, timeout_seconds):
        self.calls.append(
            {
                "messages": copy.deepcopy(messages),
                "provider_id": provider_id,
                "model": model,
                "timeout_seconds": timeout_seconds,
            }
        )
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return copy.deepcopy(response)


def _case_payload(endpoint, suffix="成功响应"):
    body = None
    if endpoint.method == "POST":
        body = {"targetId": "model-001", "favoriteType": "MODEL"}
    return {
        "name": f"{endpoint.summary}-{suffix}",
        "purpose": f"验证{endpoint.summary}接口契约",
        "priority": "P0",
        "request": {
            "method": endpoint.method,
            "path": endpoint.path,
            "service": "default",
            "path_params": {},
            "query": {"pageNum": 1} if endpoint.method == "GET" else {},
            "headers": {"Biz": "{{Biz}}"},
            "cookies": {},
            "body": body,
        },
        "data_rows": [],
        "assertions": [
            {
                "type": "status_code",
                "operator": "equals",
                "expected": 200,
                "enabled": True,
            }
        ],
        "extractions": [],
        "dependencies": [],
        "processing": {"pre": [], "post": []},
    }


def _gateway_response(candidates, *, provider="qwen_plus", model="qwen3.7-plus"):
    return {
        "success": True,
        "content": json.dumps({"candidates": candidates}, ensure_ascii=False),
        "providerId": provider,
        "model": model,
        "fallbackUsed": provider != "qwen_plus",
        "fallbackIndex": 1 if provider != "qwen_plus" else 0,
        "fallbackReason": "primary timeout" if provider != "qwen_plus" else "",
    }


def _candidate(endpoint, suffix="成功响应"):
    return {"endpoint_id": endpoint.id, "case": _case_payload(endpoint, suffix)}


def _service(session_factory, gateway, *, batch_size=10):
    from task_server.api_testing.services.ai_service import AiCaseService

    return AiCaseService(
        session_factory,
        gateway_client=gateway,
        batch_size=batch_size,
        gateway_timeout_seconds=30,
    )


def test_json_fence_unwrapper_accepts_only_one_complete_json_document():
    from task_server.api_testing.services.ai_service import AiCaseService

    document = '{"candidates": []}'

    assert AiCaseService._unwrap_json_fence(f"```json\n{document}\n```") == document
    assert AiCaseService._unwrap_json_fence(f"说明\n```json\n{document}\n```") != document


def test_submit_deduplicates_in_order_and_creates_bounded_batches(
    session_factory, ai_context
):
    gateway = FakeGateway()
    service = _service(session_factory, gateway, batch_size=2)
    endpoints = ai_context["endpoints"]
    ordered = [
        endpoints["favoriteList"].id,
        endpoints["favoriteAdd"].id,
        endpoints["favoriteList"].id,
        endpoints["favoriteCancel"].id,
    ]

    job = service.submit(
        ordered,
        ai_context["environment"].revision_id,
        "admin",
        {"providerId": "qwen_plus", "model": "qwen3.6-plus"},
        "覆盖我的收藏查询、添加和取消",
    )

    assert job.state == "queued"
    assert job.endpoint_ids == tuple(dict.fromkeys(ordered))
    assert [batch.endpoint_ids for batch in job.batches] == [
        tuple(job.endpoint_ids[:2]),
        tuple(job.endpoint_ids[2:]),
    ]
    assert job.requested_provider_id == "qwen_plus"
    assert job.requested_model == "qwen3.6-plus"


def test_submit_rejects_inactive_cross_project_and_oversized_context(
    session_factory, ai_context
):
    from task_server.api_testing.services.ai_service import AiJobInputError
    from task_server.api_testing.models.environment import (
        ApiEnvironment,
        ApiEnvironmentRevision,
    )
    from task_server.api_testing.models.source import ApiSourceEndpoint, ApiSourceRevision

    service = _service(session_factory, FakeGateway())
    endpoint = ai_context["endpoints"]["favoriteList"]
    with session_factory.begin() as session:
        foreign_project = ApiProject(
            name="Foreign Task 7 Project " + os.urandom(4).hex(),
            slug="foreign-task-7-" + os.urandom(4).hex(),
            description="foreign",
            **_audit(),
        )
        session.add(foreign_project)
        session.flush()
        foreign_environment = ApiEnvironment(
            project_id=foreign_project.id,
            source_id=None,
            name="Foreign environment " + os.urandom(4).hex(),
            **_audit(),
        )
        session.add(foreign_environment)
        session.flush()
        foreign_revision = ApiEnvironmentRevision(
            environment_id=foreign_environment.id,
            source_revision_id=None,
            revision_number=1,
            name=foreign_environment.name,
            default_headers={},
            **_audit(),
        )
        session.add(foreign_revision)
        session.flush()
        foreign_environment.active_revision_id = foreign_revision.id
        clones = []
        for index in range(61):
            clone = ApiSourceEndpoint(
                revision_id=ai_context["source_revision"].id,
                stable_key=f"task7-extra-{index:03d}-{os.urandom(3).hex()}",
                operation_id=f"extra{index}",
                method="GET",
                path=f"/extra/{index}",
                normalized_path=f"/extra/{index}",
                summary=f"extra {index}",
                tags=["extra"],
                operation={"responses": {"200": {"description": "ok"}}},
                **_audit(),
            )
            session.add(clone)
            clones.append(clone)
        session.flush()
        duplicate_ids = [item.id for item in clones]

    with pytest.raises(AiJobInputError, match="same project"):
        service.submit([endpoint.id], foreign_revision.id, "admin")

    with pytest.raises(AiJobInputError, match="at most 60"):
        service.submit(
            duplicate_ids,
            ai_context["environment"].revision_id,
            "admin",
        )

    with session_factory.begin() as session:
        source_revision = session.get(ApiSourceRevision, ai_context["source_revision"].id)
        source_revision.status = "superseded"
    with pytest.raises(AiJobInputError, match="active source revision"):
        service.submit(
            [endpoint.id], ai_context["environment"].revision_id, "admin"
        )


def test_process_generates_three_favorites_drafts_without_secret_context(
    session_factory, ai_context
):
    endpoints = ai_context["endpoints"]
    candidates = [
        _candidate(endpoints["favoriteList"]),
        _candidate(endpoints["favoriteAdd"]),
        _candidate(endpoints["favoriteCancel"]),
    ]
    gateway = FakeGateway(_gateway_response(candidates, model="qwen3.6-plus"))
    service = _service(session_factory, gateway)
    job = service.submit(
        [item.id for item in endpoints.values()],
        ai_context["environment"].revision_id,
        "admin",
        {"providerId": "qwen_plus", "model": "qwen3.6-plus"},
        "覆盖我的收藏正向、边界和鉴权风险",
    )

    completed = service.process(job.id)
    drafts = service.list_generated_drafts(job.id)

    assert completed.state == "completed"
    assert len(drafts) == 3
    assert {item.endpoint_id for item in drafts} == {item.id for item in endpoints.values()}
    assert all(item.origin == "ai" and item.status == "draft" for item in drafts)
    assert completed.actual_provider_id == "qwen_plus"
    assert completed.actual_model == "qwen3.6-plus"
    assert completed.fallback_used is False
    call = gateway.calls[0]
    assert call["provider_id"] == "qwen_plus"
    assert call["model"] == "qwen3.6-plus"
    prompt = json.dumps(call["messages"], ensure_ascii=False)
    prompt_payload = json.loads(call["messages"][1]["content"])
    assert SYNTHETIC_SECRET not in prompt
    assert SYNTHETIC_PUBLIC_VALUE not in prompt
    assert "fingerprint" not in prompt.lower()
    assert "ciphertext" not in prompt.lower()
    assert "ZXBToken" in prompt
    assert prompt_payload["environment"]["variable_names"] == [
        "Biz",
        "pageNum",
        "ZXBToken",
    ]
    assert {item["name"]: item["resolved"] for item in prompt_payload["environment"]["services"]}["optional"] is False


def test_prompt_keeps_safe_body_examples_and_omits_runtime_headers(
    session_factory, ai_context
):
    endpoint = ai_context["endpoints"]["favoriteAdd"]
    with session_factory.begin() as session:
        stored = session.get(ApiSourceEndpoint, endpoint.id)
        operation = copy.deepcopy(stored.operation)
        operation["parameters"] = [
            {
                "name": "Biz",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
                "example": SYNTHETIC_PUBLIC_VALUE,
            },
            {
                "name": "locale",
                "in": "query",
                "required": False,
                "schema": {"type": "string"},
                "example": "zh-CN",
            },
        ]
        operation["resolved_dependencies"] = {
            **operation.get("resolved_dependencies", {}),
            "#/components/parameters/Biz": {
                "name": "Biz",
                "in": "header",
                "required": True,
                "schema": {"type": "string"},
            },
            "#/components/securitySchemes/BearerAuth": {
                "type": "http",
                "scheme": "bearer",
            },
            "#/components/headers/TraceId": {
                "description": "response trace header",
                "schema": {"type": "string"},
            },
        }
        operation["responses"]["200"]["headers"] = {
            "X-Trace-Id": {"$ref": "#/components/headers/TraceId"}
        }
        media = operation["requestBody"]["content"]["application/json"]
        media["example"] = {
            "modelSn": "m001",
            "accessToken": SYNTHETIC_SECRET,
        }
        stored.operation = operation

    gateway = FakeGateway(_gateway_response([_candidate(endpoint)]))
    service = _service(session_factory, gateway)
    job = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )

    assert service.process(job.id).state == "completed"
    payload = json.loads(gateway.calls[0]["messages"][1]["content"])
    contract = payload["endpoints"][0]
    operation = contract["operation"]
    parameters = operation["parameters"]
    body_example = operation["requestBody"]["content"]["application/json"][
        "example"
    ]

    assert contract["runtime_headers_managed_by_environment"] is True
    assert [(item["in"], item["name"]) for item in parameters] == [
        ("query", "locale")
    ]
    assert "path_parameters" not in operation
    assert "#/components/parameters/Biz" not in operation.get(
        "resolved_dependencies", {}
    )
    assert "#/components/securitySchemes/BearerAuth" not in operation.get(
        "resolved_dependencies", {}
    )
    assert "#/components/headers/TraceId" not in operation.get(
        "resolved_dependencies", {}
    )
    assert body_example == {"modelSn": "m001", "accessToken": "<redacted>"}
    assert SYNTHETIC_SECRET not in json.dumps(payload, ensure_ascii=False)


def test_prompt_redacts_additional_short_sensitive_body_example_fields(
    session_factory, ai_context
):
    endpoint = ai_context["endpoints"]["favoriteAdd"]
    with session_factory.begin() as session:
        stored = session.get(ApiSourceEndpoint, endpoint.id)
        operation = copy.deepcopy(stored.operation)
        operation["requestBody"]["content"]["application/json"]["example"] = {
            "modelSn": "m001",
            "sessionId": "short-session",
            "credential": "short-credential",
            "privateKey": "short-key",
            "pin": "1234",
        }
        stored.operation = operation

    gateway = FakeGateway(_gateway_response([_candidate(endpoint)]))
    service = _service(session_factory, gateway)
    job = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )

    assert service.process(job.id).state == "completed"
    payload = json.loads(gateway.calls[0]["messages"][1]["content"])
    example = payload["endpoints"][0]["operation"]["requestBody"]["content"][
        "application/json"
    ]["example"]

    assert example == {
        "modelSn": "m001",
        "sessionId": "<redacted>",
        "credential": "<redacted>",
        "privateKey": "<redacted>",
        "pin": "<redacted>",
    }


def test_generated_case_headers_are_runtime_managed_by_environment(
    session_factory, ai_context
):
    endpoint = ai_context["endpoints"]["favoriteList"]
    candidate = _candidate(endpoint)
    candidate["case"]["request"]["headers"] = {
        "Biz": "{{Biz}}",
        "Authorization": "{{ZXBToken}}",
    }
    gateway = FakeGateway(_gateway_response([candidate]))
    service = _service(session_factory, gateway)
    job = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )

    result = service.process(job.id)
    drafts = service.list_generated_drafts(job.id)

    assert result.state == "completed"
    assert len(drafts) == 1
    assert drafts[0].request["headers"] == {"Biz": "{{Biz}}"}


def test_schema_exists_output_is_normalized_to_json_root_exists(
    session_factory, ai_context
):
    endpoint = ai_context["endpoints"]["favoriteList"]
    candidate = _candidate(endpoint)
    candidate["case"]["assertions"].append(
        {
            "type": "schema",
            "operator": "exists",
            "path": "$",
            "enabled": True,
        }
    )
    service = _service(
        session_factory,
        FakeGateway(_gateway_response([candidate])),
    )
    job = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )

    completed = service.process(job.id)
    drafts = service.list_generated_drafts(job.id)

    assert completed.state == "completed"
    assert len(drafts) == 1
    assertion = drafts[0].assertions[1]
    assert assertion.type == "json_path"
    assert assertion.operator == "exists"
    assert assertion.path == "$"
    assert assertion.expected is None


def test_single_markdown_json_fence_is_unwrapped_but_unknown_output_is_rejected(
    session_factory, ai_context
):
    endpoint = ai_context["endpoints"]["favoriteList"]
    fenced = _gateway_response([_candidate(endpoint)])
    fenced["content"] = "```json\n" + fenced["content"] + "\n```"
    gateway = FakeGateway(
        fenced,
        _gateway_response([_candidate(endpoint)]) | {"content": '{"cases": []}'},
    )
    service = _service(session_factory, gateway)

    first = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )
    assert service.process(first.id).state == "completed"
    assert len(service.list_generated_drafts(first.id)) == 1

    second = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )
    failed = service.process(second.id)
    assert failed.state == "failed_validation"
    assert failed.summary["invalid_candidates"] == 1
    assert failed.batches[0].actual_provider_id == "qwen_plus"
    assert failed.batches[0].actual_model == "qwen3.7-plus"
    assert failed.batches[0].fallback_used is False


def test_invalid_candidate_is_atomic_and_valid_candidate_survives(
    session_factory, ai_context
):
    endpoint = ai_context["endpoints"]["favoriteList"]
    valid = _candidate(endpoint)
    invalid = _candidate(endpoint, "绝对地址")
    invalid["case"]["request"]["path"] = "https://attacker.invalid/steal"
    gateway = FakeGateway(_gateway_response([valid, invalid]))
    service = _service(session_factory, gateway)
    job = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )

    result = service.process(job.id)
    drafts = service.list_generated_drafts(job.id)

    assert result.state == "partial"
    assert len(drafts) == 1
    assert result.summary["invalid_candidates"] == 1
    with session_factory() as session:
        assert session.scalar(
            select(func.count(ApiCase.id)).where(ApiCase.project_id == result.project_id)
        ) == 1


def test_missing_selected_endpoint_is_reported_as_partial_coverage(
    session_factory, ai_context
):
    endpoints = ai_context["endpoints"]
    covered = endpoints["favoriteList"]
    missing = endpoints["favoriteAdd"]
    service = _service(
        session_factory,
        FakeGateway(_gateway_response([_candidate(covered)])),
    )
    job = service.submit(
        [covered.id, missing.id],
        ai_context["environment"].revision_id,
        "admin",
    )

    result = service.process(job.id)

    assert result.state == "partial"
    assert result.batches[0].state == "partial"
    assert len(service.list_generated_drafts(job.id)) == 1
    assert any(
        item["code"] == "missing_endpoint_coverage"
        and item["endpoint_id"] == missing.id
        for item in result.batches[0].validation_errors
    )


def test_absolute_url_and_arbitrary_processing_are_rejected_independently(
    session_factory, ai_context
):
    endpoint = ai_context["endpoints"]["favoriteList"]
    absolute = _candidate(endpoint, "绝对地址")
    absolute["case"]["request"]["path"] = "https://attacker.invalid/steal"
    script = _candidate(endpoint, "任意脚本")
    script["case"]["processing"]["pre"] = [
        {"action": "python", "source": "import os"}
    ]
    gateway = FakeGateway(
        _gateway_response([absolute]),
        _gateway_response([script]),
    )
    service = _service(session_factory, gateway)
    absolute_job = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )
    absolute_result = service.process(absolute_job.id)
    script_job = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )
    script_result = service.process(script_job.id)

    assert absolute_result.state == "failed_validation"
    assert absolute_result.summary["invalid_candidates"] == 1
    assert script_result.state == "failed_validation"
    assert script_result.summary["invalid_candidates"] == 1
    assert service.list_generated_drafts(absolute_job.id) == ()
    assert service.list_generated_drafts(script_job.id) == ()


def test_later_gateway_timeout_keeps_completed_batch_and_process_is_idempotent(
    session_factory, ai_context
):
    endpoints = ai_context["endpoints"]
    ordered = [
        endpoints["favoriteList"],
        endpoints["favoriteAdd"],
        endpoints["favoriteCancel"],
    ]
    gateway = FakeGateway(
        _gateway_response(
            [_candidate(ordered[0]), _candidate(ordered[1])],
            model="qwen3.6-plus",
        ),
        TimeoutError(f"synthetic gateway timeout {SYNTHETIC_SECRET}"),
    )
    service = _service(session_factory, gateway, batch_size=2)
    job = service.submit(
        [item.id for item in ordered],
        ai_context["environment"].revision_id,
        "admin",
        {"providerId": "qwen_plus", "model": "qwen3.6-plus"},
    )

    partial = service.process(job.id)
    first_drafts = service.list_generated_drafts(job.id)
    replayed = service.process(job.id)
    second_drafts = service.list_generated_drafts(job.id)

    assert partial.state == "partial"
    assert len(first_drafts) == 2
    assert [item.id for item in second_drafts] == [item.id for item in first_drafts]
    assert replayed.state == "partial"
    assert len(gateway.calls) == 2
    assert {
        (item["provider_id"], item["model"]) for item in gateway.calls
    } == {("qwen_plus", "qwen3.6-plus")}
    assert [batch.state for batch in replayed.batches] == [
        "completed",
        "failed_gateway",
    ]
    with session_factory() as session:
        stored = session.scalar(
            select(ApiAiJobBatch).where(
                ApiAiJobBatch.job_id == job.id,
                ApiAiJobBatch.state == "failed_gateway",
            )
        )
        assert SYNTHETIC_SECRET not in json.dumps(stored.error)


def test_gateway_fallback_evidence_is_persisted_per_batch(
    session_factory, ai_context
):
    endpoint = ai_context["endpoints"]["favoriteList"]
    gateway = FakeGateway(
        _gateway_response(
            [_candidate(endpoint)],
            provider="highway_gpt5_mini",
            model="gpt-5-mini",
        )
    )
    service = _service(session_factory, gateway)
    job = service.submit(
        [endpoint.id],
        ai_context["environment"].revision_id,
        "admin",
        {"providerId": "qwen_plus", "model": "qwen3.6-plus"},
    )

    result = service.process(job.id)

    assert result.state == "completed"
    assert result.actual_provider_id == "highway_gpt5_mini"
    assert result.actual_model == "gpt-5-mini"
    assert result.fallback_used is True
    assert result.batches[0].fallback_reason == "primary timeout"
    with session_factory() as session:
        stored = session.scalar(select(ApiAiJobBatch).where(ApiAiJobBatch.job_id == job.id))
        assert stored.requested_model == "qwen3.6-plus"
        assert stored.actual_model == "gpt-5-mini"
        assert stored.result["model_evidence"]["requested_provider_id"] == "qwen_plus"
        assert stored.result["model_evidence"]["actual_provider_id"] == "highway_gpt5_mini"
        assert stored.result["model_evidence"]["fallback_index"] == 1


def test_default_gateway_client_posts_to_api_case_generation_endpoint(monkeypatch):
    from task_server.api_testing.services.ai_service import AiGatewayClient

    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(
                {
                    "success": True,
                    "content": '{"candidates": []}',
                    "providerId": "qwen_plus",
                    "model": "qwen3.6-plus",
                    "fallbackUsed": False,
                    "fallbackIndex": 0,
                    "fallbackReason": "",
                }
            ).encode()

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return Response()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = AiGatewayClient("http://127.0.0.1:8090")
    result = client.chat(
        messages=[{"role": "user", "content": "contract"}],
        provider_id="qwen_plus",
        model="qwen3.6-plus",
        timeout_seconds=25,
    )

    assert captured["url"] == "http://127.0.0.1:8090/ai/api-case-generation"
    assert captured["payload"]["providerId"] == "qwen_plus"
    assert captured["payload"]["model"] == "qwen3.6-plus"
    assert captured["payload"]["messages"][0]["content"] == "contract"
    assert result["providerId"] == "qwen_plus"


def test_default_model_selection_is_delegated_to_gateway_route(
    session_factory, ai_context
):
    endpoint = ai_context["endpoints"]["favoriteList"]
    gateway = FakeGateway(_gateway_response([_candidate(endpoint)]))
    service = _service(session_factory, gateway)

    job = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )
    completed = service.process(job.id)

    assert job.requested_provider_id == ""
    assert job.requested_model == ""
    assert gateway.calls[0]["provider_id"] == ""
    assert gateway.calls[0]["model"] == ""
    assert completed.state == "completed"
    assert completed.actual_provider_id == "qwen_plus"
    assert completed.fallback_used is False


def test_prompt_redacts_credential_shapes_from_all_contract_strings(
    session_factory, ai_context
):
    endpoint = ai_context["endpoints"]["favoriteList"]
    with session_factory.begin() as session:
        stored = session.get(ApiSourceEndpoint, endpoint.id)
        stored.summary = f"收藏查询 {SYNTHETIC_JWT}"
        stored.tags = ["收藏", SYNTHETIC_FERNET]
        stored.operation = {
            "description": f"description {SYNTHETIC_FINGERPRINT}",
            "x-notes": [SYNTHETIC_JWT, {"text": SYNTHETIC_FERNET}],
            "responses": {"200": {"description": "ok"}},
        }

    gateway = FakeGateway(_gateway_response([_candidate(endpoint)]))
    service = _service(session_factory, gateway)
    job = service.submit(
        [endpoint.id],
        ai_context["environment"].revision_id,
        "admin",
        intent=f"intent {SYNTHETIC_JWT}",
    )

    assert service.process(job.id).state == "completed"
    prompt = json.dumps(gateway.calls[0]["messages"], ensure_ascii=False)
    for secret in (SYNTHETIC_JWT, SYNTHETIC_FERNET, SYNTHETIC_FINGERPRINT):
        assert secret not in prompt
    assert prompt.count("<redacted>") >= 4


def test_prompt_redacts_short_named_credentials_without_dropping_schema_fields(
    session_factory, ai_context
):
    endpoint = ai_context["endpoints"]["favoriteList"]
    with session_factory.begin() as session:
        stored = session.get(ApiSourceEndpoint, endpoint.id)
        stored.summary = "password=pw7"
        stored.tags = ["cookie: x", "authorization: Basic dGVzdA=="]
        stored.operation = {
            "description": "api_key=test123 token: tiny",
            "requestBody": {
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "properties": {
                                "password": {
                                    "type": "string",
                                    "example": "short-secret",
                                    "default": "short-default",
                                }
                            },
                        }
                    }
                }
            },
        }
    gateway = FakeGateway(_gateway_response([_candidate(endpoint)]))
    service = _service(session_factory, gateway)
    job = service.submit(
        [endpoint.id],
        ai_context["environment"].revision_id,
        "admin",
        intent="Authorization: Bearer short-token",
    )

    assert service.process(job.id).state == "completed"
    payload = json.loads(gateway.calls[0]["messages"][1]["content"])
    prompt = json.dumps(payload, ensure_ascii=False)
    for secret in (
        "pw7",
        "cookie: x",
        "dGVzdA==",
        "test123",
        "token: tiny",
        "short-token",
        "short-secret",
        "short-default",
    ):
        assert secret not in prompt
    password_schema = payload["endpoints"][0]["operation"]["requestBody"][
        "content"
    ]["application/json"]["schema"]["properties"]["password"]
    assert password_schema == {
        "type": "string",
        "example": "<redacted>",
        "default": "<redacted>",
    }


def test_literal_credentials_are_rejected_before_case_service(
    session_factory, ai_context
):
    endpoint = ai_context["endpoints"]["favoriteList"]
    candidates = []
    header = _candidate(endpoint, "header secret")
    header["case"]["request"]["headers"]["Authorization"] = (
        f"Bearer {SYNTHETIC_JWT}"
    )
    candidates.append(header)
    cookie = _candidate(endpoint, "cookie secret")
    cookie["case"]["request"]["cookies"]["session"] = SYNTHETIC_JWT
    candidates.append(cookie)
    body = _candidate(endpoint, "body secret")
    body["case"]["request"]["body"] = {"apiKey": "sk-syntheticTask7Secret123456"}
    candidates.append(body)
    row = _candidate(endpoint, "row secret")
    row["case"]["data_rows"] = [
        {"name": "secret row", "values": {"token": SYNTHETIC_FERNET}, "enabled": True}
    ]
    candidates.append(row)
    processing = _candidate(endpoint, "processing secret")
    processing["case"]["processing"]["pre"] = [
        {"action": "set_variable", "name": "ZXBToken", "value": SYNTHETIC_JWT}
    ]
    candidates.append(processing)

    service = _service(
        session_factory, FakeGateway(_gateway_response(candidates))
    )
    job = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )
    result = service.process(job.id)

    assert result.state == "failed_validation"
    assert result.summary["invalid_candidates"] == len(candidates)
    assert service.list_generated_drafts(job.id) == ()
    stored_text = json.dumps(
        [dict(item) for item in result.batches[0].validation_errors],
        ensure_ascii=False,
    )
    for secret in (SYNTHETIC_JWT, SYNTHETIC_FERNET, "sk-syntheticTask7Secret123456"):
        assert secret not in stored_text


def test_short_sensitive_output_values_require_full_placeholders(
    session_factory, ai_context
):
    endpoint = ai_context["endpoints"]["favoriteList"]
    candidates = []
    authorization = _candidate(endpoint, "short authorization")
    authorization["case"]["request"]["headers"]["Authorization"] = "x"
    candidates.append(authorization)
    proxy_authorization = _candidate(endpoint, "short proxy authorization")
    proxy_authorization["case"]["request"]["headers"]["Proxy-Authorization"] = "x"
    candidates.append(proxy_authorization)
    cookie = _candidate(endpoint, "short cookie")
    cookie["case"]["request"]["cookies"]["session"] = "x"
    candidates.append(cookie)
    body = _candidate(endpoint, "short body secret")
    body["case"]["request"]["body"] = {"password": "x"}
    candidates.append(body)
    row = _candidate(endpoint, "short row secret")
    row["case"]["data_rows"] = [
        {"name": "row", "values": {"api_key": "x"}, "enabled": True}
    ]
    candidates.append(row)
    processing = _candidate(endpoint, "short processing secret")
    processing["case"]["processing"]["pre"] = [
        {"action": "set_variable", "name": "token", "value": "x"}
    ]
    candidates.append(processing)
    service = _service(session_factory, FakeGateway(_gateway_response(candidates)))
    job = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )

    result = service.process(job.id)

    assert result.state == "failed_validation"
    assert result.summary["invalid_candidates"] == len(candidates)
    assert service.list_generated_drafts(job.id) == ()


@pytest.mark.parametrize(
    "header_name",
    (
        "api_key",
        "API-KEY",
        "X-Api-Key",
        "access_token",
        "Client-Secret",
        "PASSWORD",
        "Cookie",
    ),
)
def test_sensitive_header_key_variants_require_full_placeholders(
    session_factory, ai_context, header_name
):
    endpoint = ai_context["endpoints"]["favoriteList"]
    candidate = _candidate(endpoint, f"sensitive header {header_name}")
    candidate["case"]["request"]["headers"][header_name] = "test123"
    service = _service(
        session_factory, FakeGateway(_gateway_response([candidate]))
    )
    job = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )

    result = service.process(job.id)

    assert result.state == "failed_validation"
    assert result.summary["invalid_candidates"] == 1
    assert service.list_generated_drafts(job.id) == ()


def test_sensitive_non_authorization_header_accepts_full_placeholder(
    session_factory, ai_context
):
    endpoint = ai_context["endpoints"]["favoriteList"]
    candidate = _candidate(endpoint, "sensitive header placeholder")
    candidate["case"]["request"]["headers"]["X-Api-Key"] = "{{ZXBToken}}"
    service = _service(
        session_factory, FakeGateway(_gateway_response([candidate]))
    )
    job = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )

    result = service.process(job.id)

    assert result.state == "completed"
    assert len(service.list_generated_drafts(job.id)) == 1


def test_sensitive_output_placeholders_are_accepted(
    session_factory, ai_context
):
    endpoint = ai_context["endpoints"]["favoriteList"]
    candidate = _candidate(endpoint, "placeholder secrets")
    candidate["case"]["request"]["headers"]["Authorization"] = "{{ZXBToken}}"
    candidate["case"]["request"]["cookies"]["session"] = "{{ZXBToken}}"
    candidate["case"]["request"]["body"] = {"password": "{{ZXBToken}}"}
    candidate["case"]["data_rows"] = [
        {
            "name": "placeholder row",
            "values": {"api_key": "{{ZXBToken}}"},
            "enabled": True,
        }
    ]
    candidate["case"]["processing"]["pre"] = [
        {
            "action": "set_variable",
            "name": "token",
            "value": "{{ZXBToken}}",
        }
    ]
    service = _service(
        session_factory, FakeGateway(_gateway_response([candidate]))
    )
    job = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )

    result = service.process(job.id)

    assert result.state == "completed"
    assert len(service.list_generated_drafts(job.id)) == 1


def test_fallback_evidence_is_redacted_and_must_be_consistent(
    session_factory, ai_context
):
    endpoint = ai_context["endpoints"]["favoriteList"]
    valid_fallback = _gateway_response(
        [_candidate(endpoint)], provider="highway_gpt5_mini", model="gpt-5-mini"
    )
    valid_fallback["fallbackReason"] = (
        f"Authorization: Bearer {SYNTHETIC_JWT}; api_key=test123"
    )
    changed_without_index = copy.deepcopy(valid_fallback)
    changed_without_index["fallbackIndex"] = 0
    unchanged_claimed_fallback = _gateway_response([_candidate(endpoint)])
    unchanged_claimed_fallback.update(
        {"fallbackUsed": True, "fallbackIndex": 1, "fallbackReason": "timeout"}
    )
    gateway = FakeGateway(
        valid_fallback, changed_without_index, unchanged_claimed_fallback
    )
    service = _service(session_factory, gateway)

    first = service.submit(
        [endpoint.id],
        ai_context["environment"].revision_id,
        "admin",
        {"providerId": "qwen_plus", "model": "qwen3.6-plus"},
    )
    completed = service.process(first.id)
    assert completed.state == "completed"
    assert SYNTHETIC_JWT not in completed.batches[0].fallback_reason
    assert "test123" not in completed.batches[0].fallback_reason
    assert completed.batches[0].fallback_reason.count("<redacted>") == 2

    second = service.submit(
        [endpoint.id],
        ai_context["environment"].revision_id,
        "admin",
        {"providerId": "qwen_plus", "model": "qwen3.6-plus"},
    )
    assert service.process(second.id).state == "failed_gateway"

    third = service.submit(
        [endpoint.id],
        ai_context["environment"].revision_id,
        "admin",
        {"providerId": "qwen_plus", "model": "qwen3.7-plus"},
    )
    assert service.process(third.id).state == "failed_gateway"


def test_strict_json_schema_rejects_missing_required_nested_field(
    session_factory, ai_context
):
    endpoint = ai_context["endpoints"]["favoriteList"]
    candidate = _candidate(endpoint)
    candidate["case"]["assertions"][0].pop("enabled", None)
    service = _service(
        session_factory, FakeGateway(_gateway_response([candidate]))
    )
    job = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )

    result = service.process(job.id)

    assert result.state == "failed_validation"
    assert service.list_generated_drafts(job.id) == ()


def test_interrupted_batch_recovers_from_checkpoint_without_duplicate_drafts(
    session_factory, ai_context, monkeypatch
):
    endpoint = ai_context["endpoints"]["favoriteList"]
    candidates = [
        _candidate(endpoint, "first checkpoint"),
        _candidate(endpoint, "second checkpoint"),
    ]
    gateway = FakeGateway(
        _gateway_response(candidates), _gateway_response(candidates)
    )
    service = _service(session_factory, gateway)
    original = service._create_validated_draft
    calls = {"count": 0}

    def interrupt_second(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise KeyboardInterrupt("synthetic worker exit")
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_create_validated_draft", interrupt_second)
    job = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )

    with pytest.raises(KeyboardInterrupt, match="synthetic worker exit"):
        service.process(job.id)
    assert len(service.list_generated_drafts(job.id)) == 1

    monkeypatch.setattr(service, "_create_validated_draft", original)
    recovered = service.process(job.id)
    drafts = service.list_generated_drafts(job.id)

    assert recovered.state == "completed"
    assert len(drafts) == 2
    assert len({item.id for item in drafts}) == 2
    assert len(gateway.calls) == 2


def test_unexpected_exception_converges_instead_of_leaving_running_state(
    session_factory, ai_context, monkeypatch
):
    endpoint = ai_context["endpoints"]["favoriteList"]
    service = _service(
        session_factory, FakeGateway(_gateway_response([_candidate(endpoint)]))
    )
    monkeypatch.setattr(
        service,
        "_create_validated_draft",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("synthetic failure api_key=test123")
        ),
    )
    job = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )

    result = service.process(job.id)

    assert result.state == "failed_validation"
    assert result.batches[0].state == "failed_validation"
    assert "synthetic failure" in result.batches[0].validation_errors[0]["message"]
    assert "test123" not in result.batches[0].validation_errors[0]["message"]


def test_concurrent_process_uses_one_gateway_call_and_creates_one_draft(
    session_factory, ai_context
):
    endpoint = ai_context["endpoints"]["favoriteList"]
    entered = threading.Event()
    release = threading.Event()

    class BlockingGateway(FakeGateway):
        def chat(self, **kwargs):
            self.calls.append(copy.deepcopy(kwargs))
            entered.set()
            assert release.wait(timeout=5)
            return _gateway_response([_candidate(endpoint)])

    gateway = BlockingGateway()
    service = _service(session_factory, gateway)
    job = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )
    completed = []
    failures = []

    def run_first():
        try:
            completed.append(service.process(job.id))
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=run_first)
    worker.start()
    assert entered.wait(timeout=5)
    observed = service.process(job.id)
    release.set()
    worker.join(timeout=5)

    assert failures == []
    assert worker.is_alive() is False
    assert observed.state == "running"
    assert completed[0].state == "completed"
    assert len(gateway.calls) == 1
    assert len(service.list_generated_drafts(job.id)) == 1


def test_advisory_lock_key_is_stable_signed_64_bit():
    from task_server.api_testing.services.ai_service import AiCaseService

    first = AiCaseService._advisory_lock_key("job-123")
    second = AiCaseService._advisory_lock_key("job-123")

    assert first == second
    assert -(2**63) <= first < 2**63


def test_skill_schema_and_eval_define_a_strict_chinese_contract():
    root = Path(__file__).resolve().parents[2]
    skill = (root / "ai_skills" / "api_case_generation.v1.md").read_text(
        encoding="utf-8"
    )
    schema = json.loads(
        (root / "ai_skills" / "schemas" / "api_case_generation.v1.json").read_text(
            encoding="utf-8"
        )
    )
    evaluation = json.loads(
        (root / "ai_skills" / "evals" / "cases" / "api_case_generation.json").read_text(
            encoding="utf-8"
        )
    )

    assert "只输出" in skill and "不得" in skill and "接口测试" in skill
    assert schema["additionalProperties"] is False
    assert schema["properties"]["candidates"]["items"]["additionalProperties"] is False
    assert evaluation["language"] == "zh-CN"
    assert evaluation["cases"][0]["endpoint_operation_ids"] == [
        "favoriteList",
        "favoriteAdd",
        "favoriteCancel",
    ]


def test_failure_analyzer_returns_model_evidence_without_sending_bearer_secret():
    from task_server.api_testing.services.ai_service import AiFailureAnalyzer

    secret = "synthetic-failure-analysis-secret"
    gateway = FakeGateway({
        "success": True,
        "providerId": "qwen_plus",
        "model": "qwen3.7-plus",
        "fallbackUsed": False,
        "fallbackIndex": 0,
        "fallbackReason": "",
        "content": json.dumps({
            "summary": "收藏接口业务码与预期不一致",
            "root_cause": "服务返回业务失败",
            "recommendations": ["核对收藏对象状态"],
            "evidence": ["HTTP 200，但 $.code 为 4009"],
        }, ensure_ascii=False),
    })

    result = AiFailureAnalyzer(gateway_client=gateway).analyze({
        "status": "FAILED",
        "failure_category": "product_assertion",
        "sanitized_request": {"headers": {"Authorization": f"Bearer {secret}"}},
        "sanitized_response": {"status_code": 200, "json": {"code": 4009}},
    })

    assert result["analyzer"] == "ai_gateway"
    assert result["model"] == "qwen3.7-plus"
    assert result["analysis"]["summary"] == "收藏接口业务码与预期不一致"
    assert result["analysis"]["model_evidence"]["actual_provider_id"] == "qwen_plus"
    assert secret not in json.dumps(gateway.calls, ensure_ascii=False)
