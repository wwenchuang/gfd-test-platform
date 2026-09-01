from __future__ import annotations

import json
import threading
import time

import pytest

from task_server.services import feishu_service
from task_server import router
from task_server.auth import create_session_token
from task_server.config import TASK_ADMIN_USER


@pytest.fixture()
def draft_store(tmp_path, monkeypatch):
    path = tmp_path / "feishu-drafts.json"
    monkeypatch.setattr(feishu_service, "_FEISHU_DRAFTS_FILE", str(path))
    return path


def test_create_feishu_draft_persists_and_upserts_by_draft_id(draft_store):
    created = feishu_service.create_feishu_draft(
        {
            "draftId": "run-101",
            "title": "登录按钮无响应",
            "description": "点击登录后页面没有进入工作台",
            "appPackage": "com.example.demo",
            "sourceRunId": "run-101",
            "failedJobs": [{"jobId": "job-1", "failureType": "ASSERTION_FAILED"}],
            "modelTrace": {"provider": "gateway", "model": "qwen3"},
        }
    )
    updated = feishu_service.create_feishu_draft(
        {
            "draftId": "run-101",
            "title": "登录按钮持续无响应",
            "description": "连续点击仍停留在登录页",
            "appPackage": "com.example.demo",
            "sourceRunId": "run-101",
        }
    )

    assert created["ok"] is True
    assert created["draft"]["status"] == "DRAFT"
    assert updated["draft"]["title"] == "登录按钮持续无响应"
    assert updated["draft"]["updatedAt"]
    persisted = json.loads(draft_store.read_text(encoding="utf-8"))["drafts"]
    assert len(persisted) == 1
    assert persisted[0]["sourceRunId"] == "run-101"
    assert persisted[0]["failedJobs"][0]["jobId"] == "job-1"
    assert persisted[0]["modelTrace"]["model"] == "qwen3"


def test_list_feishu_drafts_filters_status_and_sorts_newest(draft_store):
    draft_store.write_text(
        json.dumps(
            {
                "drafts": [
                    {"draftId": "old", "status": "REJECTED", "createdAt": "2026-08-26T09:00:00"},
                    {"draftId": "new", "status": "DRAFT", "createdAt": "2026-08-26T11:00:00"},
                    {"draftId": "middle", "status": "DRAFT", "createdAt": "2026-08-26T10:00:00"},
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert [item["draftId"] for item in feishu_service.list_feishu_drafts()] == ["new", "middle", "old"]
    assert [item["draftId"] for item in feishu_service.list_feishu_drafts(status="draft")] == ["new", "middle"]


def test_reject_feishu_draft_records_operator_and_reason(draft_store):
    feishu_service.create_feishu_draft({"draftId": "reject-me", "title": "误报", "description": "环境波动"})

    result = feishu_service.reject_feishu_draft("reject-me", user="admin", reason="确认是环境问题")

    assert result["ok"] is True
    rejected = feishu_service.get_feishu_draft("reject-me")
    assert rejected["status"] == "REJECTED"
    assert rejected["rejectedBy"] == "admin"
    assert rejected["rejectReason"] == "确认是环境问题"


def test_submit_feishu_draft_uses_app_webhook_and_updates_state(draft_store, monkeypatch):
    feishu_service.create_feishu_draft(
        {
            "draftId": "submit-me",
            "title": "模型列表为空",
            "description": "打开模型页后没有返回任何数据",
            "appPackage": "com.example.demo",
            "sourceRunId": "run-202",
        }
    )
    sent = {}

    monkeypatch.setattr(feishu_service, "_webhook_for_draft", lambda draft: "https://open.feishu.cn/open-apis/bot/v2/hook/test")

    def fake_send(payload, webhook=None):
        sent["payload"] = payload
        sent["webhook"] = webhook
        return {"StatusCode": 0}

    monkeypatch.setattr(feishu_service, "send_feishu_notification", fake_send)

    result = feishu_service.submit_feishu_draft("submit-me", user="admin")

    assert result == {"ok": True, "status": "SUBMITTED", "draftId": "submit-me"}
    assert sent["webhook"].endswith("/test")
    assert "模型列表为空" in json.dumps(sent["payload"], ensure_ascii=False)
    submitted = feishu_service.get_feishu_draft("submit-me")
    assert submitted["submittedBy"] == "admin"
    assert submitted["submitError"] == ""


def test_submit_feishu_draft_blocks_missing_app_instead_of_using_default_group(draft_store, monkeypatch):
    feishu_service.create_feishu_draft(
        {"draftId": "unlinked", "title": "未关联应用", "description": "不能发送到默认群"}
    )
    sent = []
    monkeypatch.setattr(
        feishu_service,
        "send_feishu_notification",
        lambda payload, webhook=None: sent.append(webhook) or {"code": 0},
    )

    result = feishu_service.submit_feishu_draft("unlinked", user="admin")

    assert result["ok"] is False
    assert "未关联平台应用" in result["error"]
    assert sent == []
    assert feishu_service.get_feishu_draft("unlinked")["status"] == "DRAFT"


def test_draft_webhook_resolves_the_apps_list_inside_platform_config(draft_store, monkeypatch):
    from task_server.services import job_service

    monkeypatch.setattr(
        job_service,
        "load_task_apps",
        lambda: {"apps": [{"package": "com.example.demo", "feishu_webhook": "https://open.feishu.cn/open-apis/bot/v2/hook/app"}]},
    )

    assert feishu_service._webhook_for_draft({"appPackage": "com.example.demo"}).endswith("/app")
    with pytest.raises(ValueError, match="不在平台应用配置"):
        feishu_service._webhook_for_draft({"appPackage": "com.unknown"})


@pytest.mark.parametrize("response", ({"code": 0}, {"code": "0"}, {"StatusCode": "0"}))
def test_submit_feishu_draft_accepts_feishu_success_code_variants(draft_store, monkeypatch, response):
    feishu_service.create_feishu_draft(
        {"draftId": "code-variant", "title": "报告跳转失败", "description": "报告按钮没有打开当前执行"}
    )
    monkeypatch.setattr(feishu_service, "_webhook_for_draft", lambda draft: "https://open.feishu.cn/open-apis/bot/v2/hook/test")
    monkeypatch.setattr(feishu_service, "send_feishu_notification", lambda payload, webhook=None: response)

    result = feishu_service.submit_feishu_draft("code-variant", user="admin")

    assert result["ok"] is True
    assert feishu_service.get_feishu_draft("code-variant")["status"] == "SUBMITTED"


def test_concurrent_submit_sends_a_draft_only_once(draft_store, monkeypatch):
    feishu_service.create_feishu_draft(
        {"draftId": "submit-once", "title": "重复通知", "description": "同一草稿不能重复发送"}
    )
    monkeypatch.setattr(feishu_service, "_webhook_for_draft", lambda draft: "https://open.feishu.cn/open-apis/bot/v2/hook/test")
    send_calls = []
    first_send_started = threading.Event()
    release_first_send = threading.Event()

    def fake_send(payload, webhook=None):
        send_calls.append(webhook)
        first_send_started.set()
        release_first_send.wait(timeout=1)
        return {"code": 0}

    monkeypatch.setattr(feishu_service, "send_feishu_notification", fake_send)
    results = []

    def submit():
        try:
            results.append(feishu_service.submit_feishu_draft("submit-once", user="admin"))
        except Exception as exc:
            results.append(exc)

    first = threading.Thread(target=submit)
    second = threading.Thread(target=submit)
    first.start()
    assert first_send_started.wait(timeout=1)
    second.start()
    time.sleep(0.05)
    release_first_send.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert len(send_calls) == 1
    assert sum(isinstance(item, dict) and item.get("ok") is True for item in results) == 1
    assert sum(isinstance(item, ValueError) for item in results) == 1


def test_external_draft_actions_require_an_interactive_admin_session():
    class Handler:
        def __init__(self, headers):
            self.headers = headers
            self.response = None

        def _json(self, payload, status=200):
            self.response = (status, payload)

    runner = Handler({"x-token": router.TOKEN})
    assert router._require_admin_session(runner) is True
    assert runner.response[0] == 401

    admin = Handler({"Authorization": f"Bearer {create_session_token()}"})
    assert router._require_admin_session(admin) is False
    assert router._authenticated_user(admin) == TASK_ADMIN_USER
