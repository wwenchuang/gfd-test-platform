import pytest

from task_server.router import validate_file_app_module


def test_file_save_requires_enabled_application_module_membership():
    apps = [
        {"package": "com.a", "enabled": True, "modules": ["A"]},
        {"package": "com.b", "enabled": False, "modules": ["B"]},
    ]
    assert validate_file_app_module("com.a", "A", apps) == apps[0]
    with pytest.raises(ValueError, match="不属于所选应用"):
        validate_file_app_module("com.a", "B", apps)
    with pytest.raises(ValueError, match="未启用"):
        validate_file_app_module("com.b", "B", apps)
    with pytest.raises(ValueError, match="必须明确选择"):
        validate_file_app_module("", "A", apps)
