import importlib
import json

import pytest

from task_server.services import business_line_service


def _presentation_module():
    try:
        return importlib.import_module("task_server.services.notification_presentation")
    except ModuleNotFoundError:
        pytest.fail("notification presentation helper must exist")


def _canonical_name(value, package=""):
    return _presentation_module().canonical_test_application_name(value, package)


def _business_name(*values):
    return _presentation_module().canonical_test_business_name(*values)


def _business_summary(values, *fallback):
    return _presentation_module().canonical_test_business_summary(values, *fallback)


def _scope_summary(items, *fallback, fallback_package=""):
    return _presentation_module().canonical_test_scope_summary(
        items,
        *fallback,
        fallback_package=fallback_package,
    )


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        ("智小白3D APP", "智小白3D"),
        ("3D 打印", "智小白3D"),
        ("家用", "智小白3D"),
        ("智小白3D家用", "智小白3D"),
        ("共享", "智小白3D"),
        ("智小白3D共享", "智小白3D"),
    ],
)
def test_canonical_test_application_name_normalizes_known_aliases(raw_name, expected):
    assert _canonical_name(raw_name) == expected


def test_canonical_test_application_name_preserves_unknown_project_names():
    assert _canonical_name("  海外业务   回归项目  ") == "海外业务 回归项目"


def test_canonical_test_application_name_uses_known_package_as_empty_name_fallback():
    assert _canonical_name("", "com.kfb.model") == "智小白3D"


@pytest.mark.parametrize(
    ("raw_name", "expected"),
    [
        ("家用", "家用"),
        ("3D家用", "家用"),
        ("共享", "共享"),
        ("智小白3D共享", "共享"),
    ],
)
def test_canonical_test_business_name_separates_home_and_shared(raw_name, expected):
    assert _business_name(raw_name) == expected


def test_canonical_test_business_name_prefers_shared_context_over_app_default():
    assert _business_name("智小白3D", "3D共享打印") == "共享"


def test_canonical_test_business_name_defaults_plain_app_context_to_home():
    assert _business_name("智小白3D") == "家用"


def test_canonical_test_business_name_accepts_internal_values():
    assert _business_name("home") == "家用"
    assert _business_name("shared") == "共享"


def test_canonical_test_business_summary_preserves_mixed_scope():
    assert _business_summary(["home", "shared"], "智小白3D") == "家用、共享"


def test_notification_uses_configured_chinese_business_name_for_internal_id(tmp_path, monkeypatch):
    path = tmp_path / "task-apps.json"
    path.write_text(json.dumps({"apps": [{
        "package": "com.kfb.model",
        "business_lines": [{"id": "biz_school", "name": "校园版", "enabled": True}],
    }]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(business_line_service, "TASK_APPS_FILE", str(path))

    assert _business_name("biz_school") == "校园版"
    assert _business_name("智小白3D") == "校园版"
    assert _business_summary(["biz_school"], "智小白3D") == "校园版"


def test_notification_scope_uses_current_name_for_renamed_disabled_application(tmp_path, monkeypatch):
    path = tmp_path / "task-apps.json"
    path.write_text(json.dumps({"apps": [{
        "package": "com.example.retired",
        "name": "历史应用新名称",
        "enabled": False,
        "business_lines": [{"id": "retired", "name": "历史业务", "enabled": False}],
    }]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(business_line_service, "TASK_APPS_FILE", str(path))

    assert _scope_summary([{
        "app_package": "com.example.retired",
        "app_name": "历史应用旧名称",
        "business": "retired",
    }]) == ("历史应用新名称", "历史业务")


def test_notification_scope_resolves_same_business_id_with_each_application_package(tmp_path, monkeypatch):
    path = tmp_path / "task-apps.json"
    path.write_text(json.dumps({"apps": [
        {
            "package": "com.example.home",
            "name": "家庭应用",
            "enabled": True,
            "business_lines": [{"id": "shared", "name": "家庭共享", "enabled": True}],
        },
        {
            "package": "com.example.school",
            "name": "校园应用",
            "enabled": True,
            "business_lines": [{"id": "shared", "name": "校园共享", "enabled": True}],
        },
    ]}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(business_line_service, "TASK_APPS_FILE", str(path))

    assert _scope_summary([
        {"app_package": "com.example.home", "app_name": "家庭应用", "business": "shared"},
        {"app_package": "com.example.school", "app_name": "校园应用", "business": "shared"},
    ]) == ("家庭应用、校园应用", "家庭共享、校园共享")
