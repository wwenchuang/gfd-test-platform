import copy
import json
import os
from pathlib import Path

from alembic import command
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
import pytest

from task_server.api_testing.models.case import ApiAiJob, ApiAiJobBatch, ApiCase
from task_server.api_testing.models.project import ApiProject
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
            {"type": "status_code", "operator": "equals", "expected": 200}
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
    assert "ZXBToken" not in prompt
    assert prompt_payload["environment"]["variable_names"] == ["Biz", "pageNum"]
    assert {item["name"]: item["resolved"] for item in prompt_payload["environment"]["services"]}["optional"] is False


def test_code_fence_is_cleaned_but_unknown_output_is_not_guessed(
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
    invalid = _candidate(endpoint, "危险脚本")
    invalid["case"]["request"]["path"] = "https://attacker.invalid/steal"
    invalid["case"]["script"] = "import os"
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
    gateway = FakeGateway(_gateway_response([absolute, script]))
    service = _service(session_factory, gateway)
    job = service.submit(
        [endpoint.id], ai_context["environment"].revision_id, "admin"
    )

    result = service.process(job.id)

    assert result.state == "failed_validation"
    assert result.summary["invalid_candidates"] == 2
    assert service.list_generated_drafts(job.id) == ()


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


def test_default_gateway_client_posts_to_existing_chat_endpoint(monkeypatch):
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

    assert captured["url"] == "http://127.0.0.1:8090/ai/chat"
    assert captured["payload"]["providerId"] == "qwen_plus"
    assert captured["payload"]["model"] == "qwen3.6-plus"
    assert captured["payload"]["messages"][0]["content"] == "contract"
    assert result["providerId"] == "qwen_plus"


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
