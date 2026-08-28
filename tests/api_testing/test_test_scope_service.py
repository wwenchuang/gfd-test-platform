from types import SimpleNamespace

import pytest

from task_server.api_testing.services.test_scope_service import (
    InactiveTestScopeError,
    ensure_active_case_version_scopes,
)
from task_server.services import business_line_service


def test_legacy_case_with_unique_business_inherits_configured_application(tmp_path, monkeypatch):
    path = tmp_path / "task-apps.json"
    path.write_text(
        '{"apps":[{"package":"com.kfb.model","name":"智小白3D","enabled":true,'
        '"business_lines":[{"id":"home","name":"家用","enabled":true}]}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(business_line_service, "TASK_APPS_FILE", str(path))

    ensure_active_case_version_scopes([
        SimpleNamespace(request_template={"app_package": "", "app_name": "", "business": "home"})
    ])


def test_legacy_case_stays_blocked_when_application_cannot_be_resolved(tmp_path, monkeypatch):
    path = tmp_path / "task-apps.json"
    path.write_text(
        '{"apps":[{"package":"com.a","name":"应用甲","enabled":true,'
        '"business_lines":[{"id":"common","name":"通用业务","enabled":true}]},'
        '{"package":"com.b","name":"应用乙","enabled":true,'
        '"business_lines":[{"id":"common","name":"通用业务","enabled":true}]}]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(business_line_service, "TASK_APPS_FILE", str(path))

    with pytest.raises(InactiveTestScopeError, match="应用未配置"):
        ensure_active_case_version_scopes([
            SimpleNamespace(request_template={"app_package": "", "app_name": "", "business": "common"})
        ])
