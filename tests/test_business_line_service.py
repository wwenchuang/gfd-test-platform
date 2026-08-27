import json

import pytest

from task_server.services import business_line_service, job_service


def _write_apps(path, apps):
    path.write_text(json.dumps({"apps": apps}, ensure_ascii=False), encoding="utf-8")


def test_default_business_lines_use_chinese_names_and_legacy_stable_ids():
    assert business_line_service.default_business_lines() == [
        {"id": "home", "name": "家用", "enabled": True},
        {"id": "shared", "name": "共享", "enabled": True},
    ]


def test_normalize_business_lines_generates_hidden_id_for_new_chinese_name():
    rows = business_line_service.normalize_business_lines([
        {"name": "企业版", "enabled": True},
    ])

    assert rows[0]["name"] == "企业版"
    assert rows[0]["enabled"] is True
    assert rows[0]["id"].startswith("biz_")
    assert rows[0]["id"] != "企业版"


def test_normalize_business_lines_preserves_id_when_name_changes():
    rows = business_line_service.normalize_business_lines([
        {"id": "biz_123", "name": "校园版", "enabled": True},
    ])

    assert rows == [{"id": "biz_123", "name": "校园版", "enabled": True}]


@pytest.mark.parametrize("name", ["home", "enterprise", "123"])
def test_normalize_business_lines_requires_a_chinese_display_name(name):
    with pytest.raises(ValueError, match="中文"):
        business_line_service.normalize_business_lines([{"name": name, "enabled": True}])


def test_configured_business_lines_hide_disabled_for_new_creation_but_resolve_history(tmp_path, monkeypatch):
    path = tmp_path / "task-apps.json"
    _write_apps(path, [{
        "package": "com.kfb.model",
        "business_lines": [
            {"id": "home", "name": "家庭版", "enabled": False},
            {"id": "biz_school", "name": "校园版", "enabled": True},
        ],
    }])
    monkeypatch.setattr(business_line_service, "TASK_APPS_FILE", str(path))

    assert business_line_service.configured_business_lines() == [
        {"id": "biz_school", "name": "校园版", "enabled": True},
    ]
    assert business_line_service.business_line_name("home") == "家庭版"
    assert business_line_service.business_line_id("家庭版") == "home"
    with pytest.raises(ValueError, match="已停用"):
        business_line_service.business_line_id("home", require_active=True)
    assert business_line_service.business_line_id("校园版", require_active=True) == "biz_school"


def test_normalize_business_lines_rejects_duplicate_names_and_all_disabled():
    with pytest.raises(ValueError, match="不能重复"):
        business_line_service.normalize_business_lines([
            {"name": "家用", "enabled": True},
            {"name": "家用", "enabled": True},
        ])

    with pytest.raises(ValueError, match="至少保留一个启用"):
        business_line_service.normalize_business_lines([
            {"id": "home", "name": "家用", "enabled": False},
        ])


def test_task_app_normalization_persists_business_lines_and_preserves_ids_on_rename():
    created = job_service.normalize_task_app({
        "package": "com.kfb.model",
        "name": "智小白3D",
        "business_lines": [{"name": "企业版", "enabled": True}],
    })
    line_id = created["business_lines"][0]["id"]

    renamed = job_service.normalize_task_app({
        "package": "com.kfb.model",
        "name": "智小白3D",
        "business_lines": [{"id": line_id, "name": "校园版", "enabled": True}],
    }, existing_app=created)

    assert line_id.startswith("biz_")
    assert created["name"] == "智小白3D"
    assert renamed["business_lines"] == [{"id": line_id, "name": "校园版", "enabled": True}]


def test_configured_test_applications_prefer_saved_name_and_resolve_disabled_history(tmp_path, monkeypatch):
    path = tmp_path / "task-apps.json"
    _write_apps(path, [
        {
            "package": "com.kfb.model",
            "name": "创想智造",
            "enabled": True,
            "business_lines": [{"id": "maker", "name": "创客业务", "enabled": True}],
        },
        {
            "package": "com.example.school",
            "name": "校园打印",
            "enabled": False,
            "business_lines": [{"id": "school", "name": "校园业务", "enabled": True}],
        },
    ])
    monkeypatch.setattr(business_line_service, "TASK_APPS_FILE", str(path))

    assert business_line_service.configured_test_applications() == [
        {
            "package": "com.kfb.model",
            "name": "创想智造",
            "enabled": True,
            "business_lines": [{"id": "maker", "name": "创客业务", "enabled": True}],
        },
    ]
    assert business_line_service.configured_test_application("com.example.school") == {
        "package": "com.example.school",
        "name": "校园打印",
        "enabled": False,
        "business_lines": [{"id": "school", "name": "校园业务", "enabled": True}],
    }
    assert business_line_service.test_application_name("com.kfb.model", "旧智小白") == "创想智造"
    assert business_line_service.test_application_name("com.unknown", "历史应用") == "历史应用"


def test_task_app_normalization_requires_chinese_name_and_persists_enabled_state():
    with pytest.raises(ValueError, match="中文"):
        job_service.normalize_task_app({
            "package": "com.example.school",
            "name": "School App",
            "business_lines": [{"name": "校园业务", "enabled": True}],
        })

    app = job_service.normalize_task_app({
        "package": "com.example.school",
        "name": "校园打印",
        "enabled": False,
        "business_lines": [{"name": "校园业务", "enabled": True}],
    })

    assert app["enabled"] is False


def test_non_primary_app_without_business_lines_never_falls_back_to_primary_defaults(tmp_path, monkeypatch):
    path = tmp_path / "task-apps.json"
    _write_apps(path, [{
        "package": "com.example.school",
        "name": "校园打印",
        "enabled": True,
    }])
    monkeypatch.setattr(business_line_service, "TASK_APPS_FILE", str(path))

    assert business_line_service.configured_business_lines("com.example.school") == []
    assert business_line_service.business_line_name("home", "com.example.school") == "home"
    with pytest.raises(ValueError, match="已配置且启用"):
        business_line_service.business_line_id("home", "com.example.school", require_active=True)
