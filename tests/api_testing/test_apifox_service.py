import json
import subprocess

import pytest

from task_server.api_testing.adapters.apifox_discovery import (
    ApifoxDiscoveryAdapter,
    ApifoxDiscoveryError,
)
from task_server.api_testing.adapters.apifox_openapi import (
    ApifoxOpenApiAdapter,
    ApifoxOpenApiError,
)
from task_server.api_testing.services.apifox_service import (
    ApifoxService,
    build_environment_candidate,
)


TOKEN = "afxp_adapter_test_secret"


class FakeRunner:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, arguments, **kwargs):
        self.calls.append((list(arguments), dict(kwargs)))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return subprocess.CompletedProcess(
            arguments,
            response.get("returncode", 0),
            stdout=response.get("stdout", ""),
            stderr=response.get("stderr", ""),
        )


def envelope(data):
    return json.dumps({"success": True, "data": data}, ensure_ascii=False)


def test_discovery_passes_token_only_on_login_stdin_and_parses_projects():
    runner = FakeRunner(
        [
            {"stdout": "2.2.8"},
            {"stdout": "登录成功"},
            {
                "stdout": envelope(
                    [
                        {"id": 5904970, "name": "3D", "team": {"name": "研发"}},
                        {"id": 12, "name": "共享"},
                    ]
                )
            },
        ]
    )

    projects = ApifoxDiscoveryAdapter(
        runner=runner, cli_resolver=lambda _: "/usr/bin/apifox"
    ).list_projects(TOKEN)

    assert [item.name for item in projects] == ["3D", "共享"]
    assert runner.calls[1][1]["input"] == TOKEN + "\n"
    for arguments, options in runner.calls:
        assert TOKEN not in " ".join(arguments)
        assert TOKEN not in " ".join(
            "%s=%s" % item for item in options["env"].items()
        )


@pytest.mark.parametrize("preferred", ["", "12"])
def test_discovery_does_not_fetch_every_environment_detail(preferred):
    responses = [
        {"stdout": "2.2.8"}, {"stdout": "登录成功"},
        {"stdout": envelope({"id": 5904970, "name": "3D"})},
        {"stdout": envelope([])},
        {"stdout": envelope([{"id": i, "name": f"环境{i}"} for i in range(1, 21)])},
    ]
    if preferred:
        responses.append({"stdout": envelope({"id": 12, "services": [{"name": "default", "url": "https://api.example.test"}]})})
    runner = FakeRunner(responses)
    context = ApifoxDiscoveryAdapter(runner=runner, cli_resolver=lambda _: "/usr/bin/apifox").get_context(
        TOKEN, "5904970", preferred_environment_id=preferred,
    )
    assert len(context.environments) == 20
    detail_calls = [call[0] for call in runner.calls if call[0][1:3] == ["environment", "get"]]
    assert [call[3] for call in detail_calls] == ([preferred] if preferred else [])
    if preferred:
        assert next(item for item in context.environments if item.id == preferred).services


def test_discovery_parses_branches_environments_services_and_secret_placeholders():
    runner = FakeRunner(
        [
            {"stdout": "2.2.8"},
            {"stdout": "登录成功"},
            {"stdout": envelope({"id": 5904970, "name": "3D"})},
            {
                "stdout": envelope(
                    [
                        {"id": 1, "name": "main", "isDefault": True},
                        {"id": 2, "name": "release"},
                    ]
                )
            },
            {
                "stdout": envelope(
                    [{"id": 33831678, "name": "生产环境（新）-腾讯云"}]
                )
            },
            {
                "stdout": envelope(
                    {
                        "id": 33831678,
                        "name": "生产环境（新）-腾讯云",
                        "services": [
                            {
                                "id": "default",
                                "name": "默认服务",
                                "url": "https://print.example.test/app",
                            },
                            {"id": "model", "name": "模型服务", "url": ""},
                        ],
                        "variables": [
                            {"name": "Biz", "value": "ZXB"},
                            {"name": "ZXBToken", "value": "must-not-leak", "sensitive": True},
                        ],
                    }
                )
            },
        ]
    )
    adapter = ApifoxDiscoveryAdapter(
        runner=runner, cli_resolver=lambda _: "/usr/bin/apifox"
    )

    context = adapter.get_context(TOKEN, "5904970")

    assert context.project.name == "3D"
    assert context.branches[0].id == ""
    assert context.branches[0].is_default is True
    assert context.branches[1].name == "release"
    environment = context.environments[0]
    assert environment.name == "生产环境（新）-腾讯云"
    assert environment.services[0].base_url == "https://print.example.test/app"
    assert environment.services[1].base_url is None
    assert environment.variables[0].value == "ZXB"
    assert environment.variables[1].sensitive is True
    assert environment.variables[1].value == ""
    assert "must-not-leak" not in repr(context)


def test_discovery_errors_are_stable_and_redacted():
    runner = FakeRunner(
        [
            {"stdout": "2.2.8"},
            {"returncode": 1, "stderr": "401 invalid token " + TOKEN},
        ]
    )
    adapter = ApifoxDiscoveryAdapter(
        runner=runner, cli_resolver=lambda _: "/usr/bin/apifox"
    )

    with pytest.raises(ApifoxDiscoveryError, match="访问令牌") as error:
        adapter.list_projects(TOKEN)

    assert error.value.code == "AUTH_FAILED"
    assert TOKEN not in str(error.value)


class FakeResponse:
    def __init__(self, payload, status=200, headers=None):
        self.payload = payload
        self.status = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, _limit):
        return self.payload


def test_export_uses_official_endpoint_and_bearer_header():
    requests = []

    def opener(request, timeout):
        requests.append((request, timeout))
        return FakeResponse(
            json.dumps(
                {
                    "openapi": "3.0.1",
                    "info": {"title": "3D"},
                    "paths": {"/favorites": {"get": {"responses": {"200": {}}}}},
                }
            ).encode("utf-8")
        )

    document = ApifoxOpenApiAdapter(opener=opener).export(
        TOKEN, "5904970", branch_id="2", environment_id="33831678"
    )

    request, timeout = requests[0]
    assert request.full_url == "https://api.apifox.com/v1/projects/5904970/export-openapi?locale=zh-CN"
    assert request.get_header("Authorization") == "Bearer " + TOKEN
    assert request.method == "POST"
    payload = json.loads(request.data.decode("utf-8"))
    assert payload["branchId"] == 2
    assert payload["environmentIds"] == [33831678]
    assert payload["options"]["addFoldersToTags"] is True
    assert timeout == 30
    assert document["openapi"] == "3.0.1"


def test_export_rejects_empty_or_malformed_documents_without_token_leak():
    adapter = ApifoxOpenApiAdapter(
        opener=lambda *_args, **_kwargs: FakeResponse(b'{"message":"' + TOKEN.encode() + b'"}')
    )

    with pytest.raises(ApifoxOpenApiError, match="paths") as error:
        adapter.export(TOKEN, "5904970")

    assert TOKEN not in str(error.value)


class FakeProviderService:
    def require_apifox_token(self, owner_id):
        assert owner_id == "admin"
        return TOKEN


class FakeDiscoveryAdapter:
    def __init__(self, context):
        self.context = context

    def get_context(self, token, project_id, preferred_environment_id=""):
        assert token == TOKEN
        assert project_id == "5904970"
        assert preferred_environment_id == "33831678"
        return self.context


class FakeOpenApiAdapter:
    def export(self, token, project_id, branch_id="", environment_id=""):
        assert (token, project_id, branch_id, environment_id) == (
            TOKEN,
            "5904970",
            "2",
            "33831678",
        )
        return {
            "openapi": "3.0.1",
            "info": {"title": "3D"},
            "paths": {"/favorites": {"get": {"responses": {"200": {}}}}},
        }


class FakeSourceService:
    def preview_refresh(self, project_id, source_id, document, actor_id):
        assert (project_id, source_id, actor_id) == ("local-project", None, "admin")
        assert document["info"]["title"] == "3D"
        return type(
            "Preview",
            (),
            {
                "id": "preview-id",
                "candidate_revision_id": "candidate-revision-id",
                "added_count": 1,
            },
        )()


def test_service_preview_pairs_source_diff_with_trusted_environment_candidate():
    discovery = ApifoxDiscoveryAdapter(
        runner=FakeRunner(
            [
                {"stdout": "2.2.8"},
                {"stdout": "登录成功"},
                {"stdout": envelope({"id": 5904970, "name": "3D"})},
                {"stdout": envelope([])},
                {"stdout": envelope([{"id": 33831678, "name": "生产环境"}])},
                {
                    "stdout": envelope(
                        {
                            "id": 33831678,
                            "name": "生产环境",
                            "services": [
                                {"name": "default", "url": "https://print.example.test/app"}
                            ],
                            "variables": [
                                {"name": "Biz", "value": "ZXB"},
                                {"name": "ZXBToken", "value": TOKEN, "sensitive": True},
                            ],
                        }
                    )
                },
            ]
        ),
        cli_resolver=lambda _: "/usr/bin/apifox",
    ).get_context(TOKEN, "5904970", preferred_environment_id="33831678")
    stored = []
    service = ApifoxService(
        FakeProviderService(),
        FakeDiscoveryAdapter(discovery),
        FakeOpenApiAdapter(),
        FakeSourceService(),
        preview_metadata_store=lambda preview_id, value, actor_id: stored.append(
            (preview_id, value, actor_id)
        ),
    )

    result = service.preview_refresh(
        "admin",
        {
            "project_id": "local-project",
            "source_id": None,
            "apifox_project_id": "5904970",
            "branch_id": "2",
            "environment_id": "33831678",
        },
        "admin",
    )

    assert result.source_preview.id == "preview-id"
    assert result.environment_candidate["name"] == "生产环境"
    assert result.environment_candidate["variables"] == {"Biz": "ZXB"}
    assert result.environment_candidate["secret_placeholders"] == ["ZXBToken"]
    assert TOKEN not in repr(result)
    assert stored[0][0] == "preview-id"
    assert stored[0][2] == "admin"


def test_build_environment_candidate_requires_selected_environment():
    with pytest.raises(ValueError, match="环境"):
        build_environment_candidate("local-project", "source", "revision", None)
