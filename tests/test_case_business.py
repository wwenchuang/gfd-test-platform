import pytest

from task_server import router as task_router
from task_server.services import case_service, knowledge_service, yaml_service


def test_task_case_info_reads_business_from_case_metadata(monkeypatch):
    monkeypatch.setattr(case_service, "resolve_app_package", lambda *_args, **_kwargs: "com.kfb.model")
    monkeypatch.setattr(case_service, "extract_baseline_meta_from_block", lambda _block: {"case_id": "case-1"})
    monkeypatch.setattr(case_service, "_get_task_app_map", lambda: {"com.kfb.model": {"name": "智小白3D"}})
    monkeypatch.setattr(case_service, "_get_task_key", lambda: (lambda module, file: f"{module}::{file}"))
    monkeypatch.setattr(case_service, "_get_load_task_meta", lambda: {
        "打印::共享.yaml": {
            "status": "baseline",
            "case_businesses": {"case-1": "shared"},
        },
    })

    row = case_service.task_case_info(
        "打印",
        "共享.yaml",
        "android: {}\ntasks: []",
        {"name": "共享打印", "start": 0, "block": "# baseline.case_id: case-1"},
    )

    assert row["business"] == "shared"


def test_update_task_case_business_persists_by_stable_case_id(monkeypatch):
    monkeypatch.setattr(case_service, "find_task_case_asset", lambda _case_id: {
        "case_id": "case-1", "module": "打印", "file": "共享.yaml", "task_name": "共享打印",
        "app_package": "com.kfb.model",
    })
    monkeypatch.setattr(case_service, "configured_test_application", lambda *_args, **_kwargs: {
        "package": "com.kfb.model", "enabled": True, "historical_only": False,
    }, raising=False)
    monkeypatch.setattr(case_service, "_get_task_key", lambda: (lambda module, file: f"{module}::{file}"))
    monkeypatch.setattr(case_service, "_get_load_task_meta", lambda: {
        "打印::共享.yaml": {"case_businesses": {"case-old": "home"}},
    })
    saved = []
    monkeypatch.setattr(case_service, "_get_update_task_meta", lambda module, file, patch: saved.append((module, file, patch)) or patch)

    result = case_service.update_task_case_business("case-1", "shared")

    assert saved == [("打印", "共享.yaml", {
        "case_businesses": {"case-old": "home", "case-1": "shared"},
    })]
    assert result["business"] == "shared"


def test_update_task_case_business_validates_against_case_application(monkeypatch):
    monkeypatch.setattr(case_service, "find_task_case_asset", lambda _case_id: {
        "case_id": "case-1",
        "module": "校园打印",
        "file": "校园.yaml",
        "task_name": "校园打印",
        "app_package": "com.example.school",
    })
    monkeypatch.setattr(case_service, "_get_task_key", lambda: (lambda module, file: f"{module}::{file}"))
    monkeypatch.setattr(case_service, "_get_load_task_meta", lambda: {})
    monkeypatch.setattr(case_service, "_get_update_task_meta", lambda *_args: {})
    monkeypatch.setattr(case_service, "configured_test_application", lambda *_args, **_kwargs: {
        "package": "com.example.school", "enabled": True, "historical_only": False,
    }, raising=False)
    calls = []

    def resolve_business(value, **kwargs):
        calls.append((value, kwargs))
        return "campus"

    monkeypatch.setattr(case_service, "business_line_id", resolve_business)

    result = case_service.update_task_case_business("case-1", "校园版")

    assert result["business"] == "campus"
    assert calls == [("校园版", {"app_package": "com.example.school", "require_active": True})]


@pytest.mark.parametrize("application", [
    {"package": "com.example.school", "enabled": False, "historical_only": False},
    {"package": "com.example.school", "enabled": True, "historical_only": True},
])
def test_update_task_case_business_rejects_non_creatable_application(monkeypatch, application):
    monkeypatch.setattr(case_service, "find_task_case_asset", lambda _case_id: {
        "case_id": "case-1",
        "module": "校园打印",
        "file": "校园.yaml",
        "task_name": "校园打印",
        "app_package": "com.example.school",
    })
    monkeypatch.setattr(
        case_service,
        "configured_test_application",
        lambda *_args, **_kwargs: application,
        raising=False,
    )
    monkeypatch.setattr(case_service, "business_line_id", lambda *_args, **_kwargs: "campus")
    monkeypatch.setattr(case_service, "_get_task_key", lambda: (lambda module, file: f"{module}::{file}"))
    monkeypatch.setattr(case_service, "_get_load_task_meta", lambda: {})
    monkeypatch.setattr(case_service, "_get_update_task_meta", lambda *_args: {})

    with pytest.raises(ValueError, match="应用.*停用|历史"):
        case_service.update_task_case_business("case-1", "校园业务")


@pytest.mark.parametrize("package, expected", [
    ("", "YAML 缺少有效的应用启动步骤"),
    ("com.unconfigured.app", "YAML 的应用包名未在平台配置"),
])
def test_update_task_case_business_explains_missing_or_unconfigured_launch(monkeypatch, package, expected):
    monkeypatch.setattr(case_service, "find_task_case_asset", lambda _case_id: {"app_package": package})
    monkeypatch.setattr(case_service, "configured_test_application", lambda *_args, **_kwargs: {})
    with pytest.raises(ValueError, match=expected):
        case_service.update_task_case_business("case-1", "家用")


def test_task_case_info_resolves_metadata_business_in_its_application(monkeypatch):
    monkeypatch.setattr(case_service, "resolve_app_package", lambda *_args, **_kwargs: "com.example.school")
    monkeypatch.setattr(case_service, "extract_baseline_meta_from_block", lambda _block: {"case_id": "case-1"})
    monkeypatch.setattr(case_service, "_get_task_app_map", lambda: {"com.example.school": {"name": "校园助手"}})
    monkeypatch.setattr(case_service, "_get_task_key", lambda: (lambda module, file: f"{module}::{file}"))
    monkeypatch.setattr(case_service, "_get_load_task_meta", lambda: {
        "校园打印::校园.yaml": {"case_businesses": {"case-1": "campus"}},
    })
    calls = []
    monkeypatch.setattr(
        case_service,
        "business_line_id",
        lambda value, **kwargs: calls.append((value, kwargs)) or "campus",
    )

    row = case_service.task_case_info(
        "校园打印",
        "校园.yaml",
        "android: {}\ntasks: []",
        {"name": "校园打印", "start": 0, "block": "# baseline.case_id: case-1"},
    )

    assert row["app_name"] == "校园助手"
    assert row["business"] == "campus"
    assert calls == [("campus", {"app_package": "com.example.school"})]


@pytest.mark.parametrize(("value", "expected"), [
    ("home", "home"),
    ("家用", "home"),
    ("shared", "shared"),
    ("共享", "shared"),
])
def test_normalize_ui_case_business_accepts_supported_values(value, expected):
    assert yaml_service.normalize_ui_case_business(value) == expected


def test_resolve_ui_generation_business_requires_explicit_configured_value_for_new_batch(monkeypatch):
    monkeypatch.setattr(yaml_service, "configured_test_application", lambda *_args, **_kwargs: {
        "package": "com.kfb.model", "enabled": True, "historical_only": False,
    }, raising=False)
    monkeypatch.setattr(
        yaml_service,
        "business_line_id",
        lambda value, **kwargs: "biz_school" if value in {"biz_school", "校园版"} else (_ for _ in ()).throw(ValueError("请选择有效的所属业务")),
    )
    with pytest.raises(ValueError, match="请选择所属业务"):
        yaml_service.resolve_ui_generation_business({})

    assert yaml_service.resolve_ui_generation_business({"business": "校园版"}) == "biz_school"

    with pytest.raises(ValueError, match="请选择有效的所属业务"):
        yaml_service.resolve_ui_generation_business({"business": "enterprise"})


def test_ui_generation_business_helpers_validate_against_second_application(monkeypatch):
    calls = []

    def resolve_business(value, **kwargs):
        calls.append((value, kwargs))
        return "campus"

    monkeypatch.setattr(yaml_service, "business_line_id", resolve_business)
    monkeypatch.setattr(yaml_service, "configured_test_application", lambda *_args, **_kwargs: {
        "package": "com.example.school", "enabled": True, "historical_only": False,
    }, raising=False)
    request = {"business": "校园业务", "app_package": "com.example.school"}
    payload = {"cases": [{"case_id": "case-campus"}]}

    assert yaml_service.resolve_ui_generation_business(request) == "campus"
    assert yaml_service.apply_ui_case_business(
        payload,
        "campus",
        app_package="com.example.school",
    )["cases"][0]["business"] == "campus"
    assert yaml_service.generated_case_business_meta_patch(
        {"case_id": "case-campus"},
        "campus",
        app_package="com.example.school",
    ) == {"case_businesses": {"case-campus": "campus"}}
    assert calls == [
        ("校园业务", {"app_package": "com.example.school", "require_active": True}),
        ("campus", {"app_package": "com.example.school", "require_active": False}),
        ("campus", {"app_package": "com.example.school", "require_active": False}),
    ]


@pytest.mark.parametrize("application", [
    {"package": "com.example.school", "enabled": False, "historical_only": False},
    {"package": "com.example.school", "enabled": True, "historical_only": True},
])
def test_ui_generation_rejects_non_creatable_application(monkeypatch, application):
    monkeypatch.setattr(
        yaml_service,
        "configured_test_application",
        lambda *_args, **_kwargs: application,
        raising=False,
    )
    monkeypatch.setattr(yaml_service, "business_line_id", lambda *_args, **_kwargs: "campus")

    with pytest.raises(ValueError, match="应用.*停用|历史"):
        yaml_service.resolve_ui_generation_business({
            "business": "校园业务",
            "app_package": "com.example.school",
        })


def test_async_ui_generation_threads_second_application_to_business_resolution(monkeypatch):
    calls = []
    saved = []

    def resolve_business(request, persisted_meta=None, *, app_package):
        calls.append((dict(request), persisted_meta, app_package))
        return "campus"

    class Thread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            return None

    class Handler:
        def __init__(self):
            self.payload = None
            self.status = None

        def _body(self):
            return {
                "title": "校园助手第二应用生成",
                "business": "校园业务",
                "app_package": "com.example.school",
            }

        def _json(self, payload, status=200):
            self.payload = payload
            self.status = status

    monkeypatch.setattr(task_router, "resolve_ui_generation_business", resolve_business)
    monkeypatch.setattr(task_router, "generate_job_id", lambda: "gen-second-app")
    monkeypatch.setattr(task_router, "save_generate_job", lambda job: saved.append(job))
    monkeypatch.setattr(task_router, "sanitize_generate_job_for_client", lambda job: job)
    monkeypatch.setattr(task_router.threading, "Thread", Thread)

    handler = Handler()
    task_router._post_ui_generate_yaml_async(handler, {})

    assert handler.status == 200
    assert handler.payload["job_id"] == "gen-second-app"
    assert calls == [({
        "title": "校园助手第二应用生成",
        "business": "校园业务",
        "app_package": "com.example.school",
    }, None, "com.example.school")]
    assert saved[0]["request_data"]["app_package"] == "com.example.school"
    assert saved[0]["request_data"]["business"] == "campus"


def test_regenerate_yaml_inherits_saved_application_before_mutating_assets(monkeypatch):
    saved_jobs = []
    appended_files = []
    resolution_calls = []

    class Thread:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def start(self):
            return None

    class Handler:
        status = None
        payload = None

        def _body(self):
            return {
                "case_set_id": "batch-school",
                "supplement": "补充校园业务校验",
            }

        def _json(self, payload, status=200):
            self.payload = payload
            self.status = status

    def read_json(path, default=None):
        if "generation-summary" in str(path):
            return {
                "title": "校园助手回归",
                "module": "校园模块",
                "app_package": "com.example.school",
                "business": "campus",
            }
        return {
            "title": "校园助手回归",
            "module": "校园模块",
            "files": [{"name": "requirement.md"}],
            "app_package": "com.example.school",
            "business": "campus",
        }

    def resolve_business(request, persisted_meta=None, *, app_package):
        resolution_calls.append((dict(request), dict(persisted_meta or {}), app_package))
        if app_package != "com.example.school":
            raise ValueError("所属业务不属于当前应用")
        return "campus"

    monkeypatch.setattr(task_router, "read_json_file", read_json)
    monkeypatch.setattr(task_router, "generation_summary_path", lambda value: f"generation-summary/{value}")
    monkeypatch.setattr(task_router, "asset_meta_path", lambda value: f"asset-meta/{value}")
    monkeypatch.setattr(task_router, "find_figma_url_for_case_set", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(task_router, "resolve_ui_generation_business", resolve_business)
    monkeypatch.setattr(
        task_router,
        "append_asset_files",
        lambda case_set_id, title, module, files: appended_files.append((case_set_id, title, module, files)) or read_json("asset-meta"),
    )
    monkeypatch.setattr(task_router, "update_asset_request_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(task_router, "generate_job_id", lambda: "regen-school")
    monkeypatch.setattr(task_router, "save_generate_job", lambda job: saved_jobs.append(job))
    monkeypatch.setattr(task_router, "sanitize_generate_job_for_client", lambda job: job)
    monkeypatch.setattr(task_router.threading, "Thread", Thread)

    handler = Handler()
    task_router._post_ui_regenerate_yaml_async(handler, {})

    assert handler.status == 200
    assert resolution_calls[0][2] == "com.example.school"
    assert saved_jobs[0]["request_data"]["app_package"] == "com.example.school"
    assert appended_files and appended_files[0][0] == "batch-school"


def test_regenerate_yaml_validation_failure_does_not_append_assets(monkeypatch):
    appended_files = []

    class Handler:
        status = None
        payload = None

        def _body(self):
            return {"case_set_id": "batch-disabled", "supplement": "不应保存"}

        def _json(self, payload, status=200):
            self.payload = payload
            self.status = status

    def read_json(path, default=None):
        if "generation-summary" in str(path):
            return {
                "title": "停用应用回归",
                "module": "历史模块",
                "app_package": "com.example.disabled",
                "business": "history",
            }
        return {
            "files": [{"name": "requirement.md"}],
            "app_package": "com.example.disabled",
            "business": "history",
        }

    monkeypatch.setattr(task_router, "read_json_file", read_json)
    monkeypatch.setattr(task_router, "generation_summary_path", lambda value: f"generation-summary/{value}")
    monkeypatch.setattr(task_router, "asset_meta_path", lambda value: f"asset-meta/{value}")
    monkeypatch.setattr(task_router, "find_figma_url_for_case_set", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        task_router,
        "resolve_ui_generation_business",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("应用已停用")),
    )
    monkeypatch.setattr(
        task_router,
        "append_asset_files",
        lambda *args, **kwargs: appended_files.append((args, kwargs)) or read_json("asset-meta"),
    )

    handler = Handler()
    task_router._post_ui_regenerate_yaml_async(handler, {})

    assert handler.status == 400
    assert handler.payload["error"] == "应用已停用"
    assert appended_files == []


def test_resolve_ui_generation_business_inherits_regenerated_batch():
    assert yaml_service.resolve_ui_generation_business(
        {"regenerate": True, "reuse_assets": True},
        {"business": "shared"},
    ) == "shared"

    with pytest.raises(ValueError, match="历史生成批次未标注业务"):
        yaml_service.resolve_ui_generation_business(
            {"regenerate": True, "reuse_assets": True},
            {},
        )


def test_sync_reuse_validates_saved_application_before_any_asset_mutation(monkeypatch):
    mutations = []
    saved_meta = {
        "case_set_id": "batch-disabled",
        "title": "历史回归",
        "module": "历史模块",
        "files": [{"name": "requirement.md"}],
        "app_package": "com.example.disabled",
        "business": "history",
    }
    monkeypatch.setattr(yaml_service, "asset_meta_path", lambda value: f"asset-meta/{value}")
    monkeypatch.setattr(yaml_service, "read_json_file", lambda *_args, **_kwargs: dict(saved_meta))
    monkeypatch.setattr(yaml_service, "save_asset_files", lambda *_args, **_kwargs: mutations.append("save") or dict(saved_meta))
    monkeypatch.setattr(yaml_service, "update_asset_request_context", lambda *_args, **_kwargs: mutations.append("update") or dict(saved_meta))
    monkeypatch.setattr(yaml_service, "write_json_file", lambda *_args, **_kwargs: mutations.append("write"))
    monkeypatch.setattr(yaml_service, "configured_test_application", lambda package, include_disabled=True: {
        "package": package,
        "name": "历史应用",
        "enabled": False,
        "historical_only": False,
    })

    with pytest.raises(ValueError, match="当前应用已停用"):
        yaml_service.generate_ui_yaml_from_request({
            "case_set_id": "batch-disabled",
            "reuse_assets": True,
            "regenerate": True,
            "files": [{"name": "supplement.md", "content": "补充说明"}],
        })

    assert mutations == []


def test_apply_ui_case_business_marks_payload_and_task_metadata():
    payload = {
        "cases": [
            {"case_id": "case-home-1", "title": "首页"},
            {"caseId": "case-home-2", "title": "设备"},
        ],
        "manual_cases": [{"case_id": "manual-1", "title": "人工确认"}],
    }

    marked = yaml_service.apply_ui_case_business(payload, "home")
    patch = yaml_service.generated_case_business_meta_patch(
        {"case_id": "case-home-1"},
        "home",
    )

    assert marked["business"] == "home"
    assert [row["business"] for row in marked["cases"]] == ["home", "home"]
    assert marked["manual_cases"][0]["business"] == "home"
    assert patch == {"case_businesses": {"case-home-1": "home"}}


def test_asset_request_context_persists_generation_business(tmp_path, monkeypatch):
    monkeypatch.setattr(knowledge_service, "ASSET_DIR", str(tmp_path))

    meta = knowledge_service.update_asset_request_context(
        "batch-1",
        {"business": "shared", "app_package": "com.kfb.model"},
    )

    assert meta["business"] == "shared"
    assert knowledge_service.read_json_file(
        knowledge_service.asset_meta_path("batch-1"),
        default={},
    )["business"] == "shared"
