import pytest

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
    })
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
    calls = []

    def resolve_business(value, **kwargs):
        calls.append((value, kwargs))
        return "campus"

    monkeypatch.setattr(case_service, "business_line_id", resolve_business)

    result = case_service.update_task_case_business("case-1", "校园版")

    assert result["business"] == "campus"
    assert calls == [("校园版", {"app_package": "com.example.school", "require_active": True})]


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
