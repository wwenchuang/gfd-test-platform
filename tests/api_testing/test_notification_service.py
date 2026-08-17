import json
from types import SimpleNamespace

from task_server.api_testing.services.notification_service import NotificationService


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

    card = NotificationService._card(
        _execution(),
        [],
        {"environment_name": "生产环境（新）-腾讯云"},
    )

    text = json.dumps(card, ensure_ascii=False)
    assert "API 基线回归报告：未通过" in text
    assert "收藏链路回归" in text
    assert "生产环境（新）-腾讯云" in text
    assert "查看报告" in text
    assert "http://qa.example.test/api-test/#/reports?project_id=project-1&execution_id=execution-1" in text
    assert "执行：" not in text


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


def test_report_url_uses_first_configured_public_base(monkeypatch):
    monkeypatch.delenv("API_TESTING_REPORT_BASE_URL", raising=False)
    monkeypatch.setenv("MIDSCENE_PUBLIC_BASE_URL", "http://task.example.test/")

    assert (
        NotificationService._report_url("execution-2", "project-2")
        == "http://task.example.test/api-test/#/reports?project_id=project-2&execution_id=execution-2"
    )
