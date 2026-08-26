import json
from types import SimpleNamespace

import pytest

from task_server.api_testing.repositories.execution_repository import ExecutionRepository
from task_server.api_testing.repositories.notification_repository import NotificationRepository
from task_server.api_testing.services.notification_service import (
    NotificationInputError,
    NotificationService,
    _validate_api_testing_feishu_webhook,
)


def _execution(**overrides):
    values = {
        "id": "execution-1",
        "project_id": "project-1",
        "execution_type": "baseline_regression",
        "request_snapshot": {"task": {"id": "task-1", "name": "收藏链路回归"}},
        "summary": {
            "total": 2,
            "passed": 1,
            "failed": 1,
            "broken": 0,
            "skipped": 0,
            "cancelled": 0,
        },
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_feishu_report_card_contains_report_link_and_readable_summary(monkeypatch):
    monkeypatch.setenv("API_TESTING_REPORT_BASE_URL", "http://qa.example.test")

    child = SimpleNamespace(
        status="FAILED",
        case_version_id="version-1",
        endpoint_id="endpoint-1",
        failure_category="assertion",
    )
    card = NotificationService._card(
        _execution(),
        [child],
        {
            "project_name": "智小白3D",
            "environment_name": "生产环境（新）-腾讯云",
            "versions": {"version-1": SimpleNamespace(case_id="case-1")},
            "cases": {"case-1": SimpleNamespace(name="添加收藏 - 正常流程")},
            "endpoints": {"endpoint-1": SimpleNamespace(summary="添加收藏", path="/print3d/api/v1/collection/add")},
        },
    )

    text = json.dumps(card, ensure_ascii=False)
    assert "❌ 智小白3D｜API 基线测试｜未通过" in text
    assert "API 基线测试未通过" in text
    assert "应用：** 智小白3D" in text
    assert "收藏链路回归" in text
    assert "生产环境（新）-腾讯云" in text
    assert "通过率：50%" in text
    assert "用例统计：** 总数 2｜通过 1 / 失败 1 / 异常 0 / 跳过 0 / 取消 0" in text
    assert "失败摘要" in text
    assert "失败 · 添加收藏 - 正常流程 · 断言失败" in text
    assert "查看当前执行报告" in text
    assert "http://qa.example.test/api-test/#/reports?project_id=project-1&execution_id=execution-1" in text
    assert "查看报告" not in text
    assert "执行：" not in text
    assert "Sonic" not in text
    assert "设备：" not in text


def test_feishu_report_card_marks_scheduled_job_type():
    card = NotificationService._card(
        _execution(request_snapshot={
            "task": {
                "id": "job-1",
                "name": "每日基线回归",
                "type": "scheduled_job",
                "source": "scheduled_job",
            },
        }),
        [],
        {"environment_name": "生产环境"},
    )

    text = json.dumps(card, ensure_ascii=False)
    assert "每日基线回归" in text
    assert "任务类型" in text
    assert "定时任务" in text


def test_feishu_report_card_never_rounds_an_imperfect_run_to_100_percent():
    card = NotificationService._card(
        _execution(summary={
            "total": 240, "passed": 239, "failed": 1, "broken": 0,
            "skipped": 0, "cancelled": 0,
        }),
        [],
        {"project_name": "智小白3D", "environment_name": "生产环境"},
    )

    text = json.dumps(card, ensure_ascii=False)
    assert "通过率：99.6%" in text
    assert "通过率：100%" not in text


def test_feishu_report_card_rejects_an_inconsistent_perfect_summary():
    card = NotificationService._card(
        _execution(summary={
            "total": 240, "passed": 241, "failed": 0, "broken": 0,
            "skipped": 0, "cancelled": 0,
        }),
        [],
        {"project_name": "智小白3D", "environment_name": "生产环境"},
    )

    text = json.dumps(card, ensure_ascii=False)
    assert "通过率：99.9%" in text
    assert "通过率：100%" not in text


def test_feishu_report_card_labels_dependency_skip_for_readers():
    child = SimpleNamespace(
        status="SKIPPED",
        case_version_id="version-1",
        endpoint_id="endpoint-1",
        failure_category="dependency",
    )
    card = NotificationService._card(
        _execution(summary={
            "total": 1, "passed": 0, "failed": 0, "broken": 0,
            "skipped": 1, "cancelled": 0,
        }),
        [child],
        {
            "versions": {"version-1": SimpleNamespace(case_id="case-1")},
            "cases": {"case-1": SimpleNamespace(name="取消收藏")},
            "endpoints": {"endpoint-1": SimpleNamespace(summary="取消收藏", path="/collection/remove")},
        },
    )

    assert "跳过 · 取消收藏 · 前置依赖" in json.dumps(card, ensure_ascii=False)


def test_display_metadata_includes_project_name_for_feishu_card():
    repository = ExecutionRepository.__new__(ExecutionRepository)
    repository.get_project = lambda project_id: SimpleNamespace(name="智小白3D")
    repository.get_case_versions = lambda _ids: {}
    repository.get_cases = lambda _ids: {}
    repository.get_endpoints = lambda _ids: {}
    repository.get_environment_revision = lambda _id: SimpleNamespace(name="生产环境")
    repository.latest_failure_analyses = lambda _ids: {}
    repository.read_events = lambda _execution_id, _after_id: []

    metadata = repository.display_metadata(
        SimpleNamespace(id="execution-1", project_id="project-1", environment_revision_id="env-1"),
        [],
    )

    assert metadata["project_name"] == "智小白3D"


def test_report_url_uses_first_configured_public_base(monkeypatch):
    monkeypatch.delenv("API_TESTING_REPORT_BASE_URL", raising=False)
    monkeypatch.setenv("MIDSCENE_PUBLIC_BASE_URL", "http://task.example.test/")

    assert (
        NotificationService._report_url("execution-2", "project-2")
        == "http://task.example.test/api-test/#/reports?project_id=project-2&execution_id=execution-2"
    )


def test_feishu_configuration_test_sends_a_distinct_project_card(monkeypatch):
    record = SimpleNamespace(
        project_id="project-1",
        channel_type="feishu",
        name="接口回归通知",
        enabled=True,
        ciphertext="encrypted-webhook",
    )

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def scalar(self, _statement):
            return SimpleNamespace(id="project-1", name="智小白3D", owner_id="owner-a")

    monkeypatch.setattr(NotificationRepository, "get", lambda *_args, **_kwargs: record)
    monkeypatch.setattr(
        "task_server.api_testing.services.notification_service.decrypt_secret",
        lambda ciphertext: "https://open.feishu.cn/open-apis/bot/v2/hook/test",
    )
    sent = []
    monkeypatch.setattr(
        "task_server.api_testing.services.notification_service.send_feishu_notification",
        lambda payload, webhook: sent.append((payload, webhook)),
    )

    result = NotificationService(lambda: FakeSession()).test_feishu("project-1", "owner-a")

    assert result.project_id == "project-1"
    assert result.channel_type == "feishu"
    assert result.sent is True
    assert result.message == "飞书测试通知已发"
    assert sent[0][1].endswith("/test")
    card = json.dumps(sent[0][0], ensure_ascii=False)
    assert "API 通知配置验证" in card
    assert "智小白3D" in card
    assert "不关联测试执行" in card


@pytest.mark.parametrize(
    "webhook",
    [
        "http://open.feishu.cn/open-apis/bot/v2/hook/test",
        "https://127.0.0.1/open-apis/bot/v2/hook/test",
        "https://example.com/open-apis/bot/v2/hook/test",
        "https://open.feishu.cn.evil.test/open-apis/bot/v2/hook/test",
        "https://open.feishu.cn/internal/test",
    ],
)
def test_api_testing_feishu_webhook_rejects_unsafe_targets(webhook):
    with pytest.raises(NotificationInputError):
        _validate_api_testing_feishu_webhook(webhook)


def test_api_testing_feishu_webhook_accepts_official_feishu_and_lark_hosts():
    assert _validate_api_testing_feishu_webhook(
        "https://open.feishu.cn/open-apis/bot/v2/hook/test"
    )
    assert _validate_api_testing_feishu_webhook(
        "https://open.larksuite.com/open-apis/bot/v2/hook/test"
    )
