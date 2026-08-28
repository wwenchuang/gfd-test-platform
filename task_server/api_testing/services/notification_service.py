"""API testing report notification orchestration."""

from dataclasses import dataclass
from datetime import datetime
import os
from typing import Optional
from urllib.parse import urlencode, urlparse

from sqlalchemy import select

from task_server.services.feishu_service import (
    send_feishu_notification,
    validate_feishu_webhook,
)
from task_server.services.notification_presentation import (
    canonical_test_scope_summary,
)

from ..crypto import decrypt_secret, encrypt_secret, secret_fingerprint
from ..models.project import ApiProject
from ..repositories.execution_repository import ExecutionRepository
from ..repositories.notification_repository import NotificationRepository


FEISHU_CHANNEL = "feishu"
_FEISHU_BOT_HOSTS = {"open.feishu.cn", "open.larksuite.com"}
_FEISHU_BOT_PATH_PREFIX = "/open-apis/bot/v2/hook/"


class NotificationInputError(ValueError):
    pass


class NotificationNotConfiguredError(LookupError):
    pass


@dataclass(frozen=True)
class NotificationChannelView:
    project_id: str
    channel_type: str
    name: str
    enabled: bool
    configured: bool
    fingerprint: str
    updated_at: Optional[datetime]


@dataclass(frozen=True)
class NotificationSendView:
    execution_id: str
    channel_type: str
    sent: bool
    message: str


@dataclass(frozen=True)
class NotificationTestView:
    project_id: str
    channel_type: str
    sent: bool
    message: str


def _text(value, field, maximum):
    if not isinstance(value, str) or len(value.strip()) > maximum:
        raise NotificationInputError(f"{field} is invalid")
    return value.strip()


def _pass_rate_text(passed, total, *, has_issues=False):
    if total <= 0:
        return "0%"
    rate = max(0.0, min(100.0, (passed / total) * 100))
    if has_issues or passed != total:
        rate = min(rate, 99.9)
    return f"{rate:.0f}%" if rate.is_integer() else f"{rate:.1f}%"


def _validate_api_testing_feishu_webhook(webhook):
    try:
        value = validate_feishu_webhook(webhook)
        parsed = urlparse(value)
        port = parsed.port
    except (ValueError, TypeError) as exc:
        raise NotificationInputError(str(exc)) from exc
    token = parsed.path.removeprefix(_FEISHU_BOT_PATH_PREFIX)
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").lower() not in _FEISHU_BOT_HOSTS
        or port is not None
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith(_FEISHU_BOT_PATH_PREFIX)
        or not token
        or "/" in token
        or parsed.query
        or parsed.fragment
    ):
        raise NotificationInputError(
            "Feishu webhook must be an official HTTPS bot address"
        )
    return value


class NotificationService:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    def get_feishu(self, project_id, actor_id):
        with self.session_factory() as session:
            record = NotificationRepository(session).get(
                actor_id, project_id, FEISHU_CHANNEL
            )
            return self._view(project_id, record)

    def save_feishu(self, project_id, payload, actor_id):
        name = _text(payload.get("name", "接口回归通知"), "name", 200) or "接口回归通知"
        enabled = bool(payload.get("enabled", False))
        webhook = str(payload.get("webhook") or "").strip()
        with self.session_factory.begin() as session:
            repository = NotificationRepository(session)
            record = repository.get(actor_id, project_id, FEISHU_CHANNEL, for_update=True)
            if record is None:
                record = repository.create(
                    actor_id,
                    project_id,
                    FEISHU_CHANNEL,
                    name,
                    actor_id,
                )
            record.name = name
            record.enabled = enabled
            if webhook:
                webhook = _validate_api_testing_feishu_webhook(webhook)
                record.ciphertext = encrypt_secret(webhook)
                record.fingerprint = secret_fingerprint(webhook)
                record.key_version = 1
            if enabled and not record.ciphertext:
                raise NotificationInputError("Feishu webhook is required before enabling")
            record.updated_by = actor_id
            repository.flush()
            return self._view(project_id, record)

    def send_execution_report(self, execution_id, actor_id):
        with self.session_factory() as session:
            execution_repository = ExecutionRepository(session)
            execution = execution_repository.get_execution(execution_id)
            if execution is None or execution.owner_id != actor_id:
                raise NotificationNotConfiguredError("execution was not found")
            notification = NotificationRepository(session).get(
                execution.owner_id,
                execution.project_id,
                FEISHU_CHANNEL,
            )
            if notification is None or not notification.enabled or not notification.ciphertext:
                raise NotificationNotConfiguredError("Feishu notification is not configured")
            children = execution_repository.get_execution_cases(
                execution.id,
                include_evidence=False,
            )
            metadata = execution_repository.display_metadata(
                execution,
                children,
                include_details=False,
            )
            webhook = _validate_api_testing_feishu_webhook(
                decrypt_secret(notification.ciphertext)
            )
            send_feishu_notification(
                {"card": self._card(execution, children, metadata)},
                webhook=webhook,
            )
            return NotificationSendView(
                execution_id=execution.id,
                channel_type=FEISHU_CHANNEL,
                sent=True,
                message="飞书通知已发",
            )

    def test_feishu(self, project_id, actor_id):
        with self.session_factory() as session:
            project = session.scalar(
                select(ApiProject).where(
                    ApiProject.id == project_id,
                    ApiProject.owner_id == actor_id,
                )
            )
            notification = NotificationRepository(session).get(
                actor_id,
                project_id,
                FEISHU_CHANNEL,
            )
            if project is None or notification is None or not notification.enabled or not notification.ciphertext:
                raise NotificationNotConfiguredError("Feishu notification is not configured")
            webhook = _validate_api_testing_feishu_webhook(
                decrypt_secret(notification.ciphertext)
            )
            send_feishu_notification(
                {"card": self._test_card(project.name, notification.name)},
                webhook=webhook,
            )
            return NotificationTestView(
                project_id=project_id,
                channel_type=FEISHU_CHANNEL,
                sent=True,
                message="飞书测试通知已发",
            )

    @staticmethod
    def _test_card(project_name, channel_name):
        sent_at = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": "blue",
                "title": {"tag": "plain_text", "content": "API 通知配置验证"},
            },
            "elements": [
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**项目：** {project_name}"}},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**通知配置：** {channel_name}"}},
                {"tag": "div", "text": {"tag": "lark_md", "content": f"**发送时间：** {sent_at}"}},
                {"tag": "hr"},
                {
                    "tag": "div",
                    "text": {
                        "tag": "lark_md",
                        "content": "机器人连接正常。此消息仅验证项目通知配置，不关联测试执行。",
                    },
                },
            ],
        }

    @staticmethod
    def _view(project_id, record):
        if record is None:
            return NotificationChannelView(
                project_id=project_id,
                channel_type=FEISHU_CHANNEL,
                name="接口回归通知",
                enabled=False,
                configured=False,
                fingerprint="",
                updated_at=None,
            )
        return NotificationChannelView(
            project_id=record.project_id,
            channel_type=record.channel_type,
            name=record.name,
            enabled=record.enabled,
            configured=bool(record.ciphertext),
            fingerprint=record.fingerprint,
            updated_at=record.updated_at,
        )

    @staticmethod
    def _report_url(execution_id, project_id=""):
        base_url = ""
        for key in (
            "API_TESTING_REPORT_BASE_URL",
            "API_TESTING_PUBLIC_BASE_URL",
            "MIDSCENE_PUBLIC_BASE_URL",
            "PUBLIC_BASE_URL",
        ):
            value = os.getenv(key, "").strip()
            if value:
                base_url = value
                break
        if not base_url:
            return ""
        query = {"execution_id": execution_id}
        if project_id:
            query = {"project_id": project_id, **query}
        return (
            f"{base_url.rstrip('/')}/api-test/#/reports?"
            f"{urlencode(query)}"
        )

    @staticmethod
    def _card(execution, children, metadata):
        summary = execution.summary or {}
        total = int(summary.get("total") or len(children))
        passed = int(summary.get("passed") or 0)
        failed = int(summary.get("failed") or 0)
        broken = int(summary.get("broken") or 0)
        skipped = int(summary.get("skipped") or 0)
        cancelled = int(summary.get("cancelled") or 0)
        issue_count = failed + broken + skipped + cancelled
        conclusion = "通过" if total and issue_count == 0 and passed == total else "未通过"
        pass_rate = _pass_rate_text(passed, total, has_issues=issue_count > 0)
        icon = "✅" if conclusion == "通过" else "❌"
        color = "green" if conclusion == "通过" else "red"
        raw_project_name = str(metadata.get("project_name") or "").strip() or "未命名项目"
        environment_name = metadata.get("environment_name") or "未命名环境"
        snapshot = getattr(execution, "request_snapshot", {}) or {}
        task = snapshot.get("task", {}) if isinstance(snapshot, dict) else {}
        task_name = task.get("name") if isinstance(task, dict) else ""
        if not task_name:
            task_name = "未保存任务"
        case_versions = snapshot.get("case_versions", []) if isinstance(snapshot, dict) else []
        project_name, business_name = canonical_test_scope_summary(
            case_versions,
            raw_project_name,
            task_name,
        )
        scene_label = NotificationService._execution_scene_label(execution)
        trigger_label = NotificationService._trigger_type_label(execution, task)
        title = f"{icon} {project_name}｜{business_name}｜API 测试｜{scene_label}{conclusion}"
        template = "green" if conclusion == "通过" else "red"
        context_lines = [
            f"**应用：** {project_name}",
            f"**业务：** {business_name}",
            "**测试类型：** API 测试",
            f"**执行场景：** {scene_label}",
            f"**任务名称：** {task_name}",
            f"**触发方式：** {trigger_label}",
            f"**环境：** {environment_name}",
        ]
        elements = [
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**结论：** <font color='{color}'>{icon} {scene_label}{conclusion}</font>",
                },
            },
            {"tag": "div", "text": {"tag": "lark_md", "content": "\n".join(context_lines)}},
            {"tag": "hr"},
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**通过率：{pass_rate}**"}},
            {
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": f"**用例统计：** 总数 {total}｜通过 {passed}｜失败 {failed}｜异常 {broken}｜跳过 {skipped}｜取消 {cancelled}",
                },
            },
            {"tag": "div", "text": {"tag": "lark_md", "content": f"**执行范围：** 共 {total} 条用例"}},
        ]
        issues = [item for item in children if item.status != "PASSED"][:5]
        if issues:
            versions = metadata.get("versions") or {}
            cases = metadata.get("cases") or {}
            endpoints = metadata.get("endpoints") or {}
            issue_lines = []
            for item in issues:
                version = versions.get(item.case_version_id)
                case = cases.get(version.case_id) if version is not None else None
                endpoint = endpoints.get(item.endpoint_id)
                name = (
                    getattr(case, "name", "")
                    or getattr(endpoint, "summary", "")
                    or getattr(endpoint, "path", "")
                    or "未命名用例"
                )
                issue_lines.append(
                    f"- {NotificationService._status_label(item.status)} · {name} · {NotificationService._failure_category_label(item.failure_category)}"
                )
            if failed + broken + skipped + cancelled > len(issues):
                issue_lines.append(
                    f"- 还有 {failed + broken + skipped + cancelled - len(issues)} 条异常结果，请打开当前执行报告查看"
                )
            elements.extend(
                [
                    {"tag": "hr"},
                    {
                        "tag": "div",
                        "text": {
                            "tag": "lark_md",
                            "content": "**失败摘要**\n" + "\n".join(issue_lines),
                        },
                    },
                ]
            )
        report_url = NotificationService._report_url(execution.id, execution.project_id)
        if report_url:
            elements.extend(
                [
                    {"tag": "hr"},
                    {
                        "tag": "action",
                        "actions": [
                            {
                                "tag": "button",
                                "text": {"tag": "plain_text", "content": "查看当前执行报告"},
                                "type": "primary",
                                "url": report_url,
                            }
                        ],
                    },
                ]
            )
        return {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": template,
                "title": {"tag": "plain_text", "content": title},
            },
            "elements": elements,
        }

    @staticmethod
    def _status_label(status):
        return {
            "PASSED": "通过",
            "FAILED": "失败",
            "BROKEN": "异常",
            "SKIPPED": "跳过",
            "CANCELLED": "取消",
            "QUEUED": "等待中",
            "RUNNING": "执行中",
        }.get(str(status or "").upper(), str(status or "").strip() or "未知")

    @staticmethod
    def _failure_category_label(category):
        value = str(category or "").strip()
        if not value:
            return "未分类"
        return {
            "assertion": "断言失败",
            "http_error": "HTTP 错误",
            "timeout": "请求超时",
            "network": "网络异常",
            "script_error": "脚本异常",
            "system": "系统异常",
            "dependency": "前置依赖",
            "cancelled": "已取消",
        }.get(value, value)

    @staticmethod
    def _execution_scene_label(execution):
        execution_type = str(getattr(execution, "execution_type", "") or "").strip()
        return {
            "baseline_regression": "基线回归",
            "regression": "回归测试",
            "debug": "在线调试",
            "scheduled": "基线回归",
        }.get(execution_type, "测试执行")

    @staticmethod
    def _trigger_type_label(execution, task):
        task_type = ""
        source = ""
        if isinstance(task, dict):
            task_type = str(task.get("type") or "").strip()
            source = str(task.get("source") or "").strip()
        execution_source = str(getattr(execution, "execution_source", "") or "").strip()
        if task_type == "scheduled_job" or source == "scheduled_job" or execution_source == "scheduled_job":
            return "定时触发"
        if task_type == "api_test_task" or source == "task":
            return "任务触发"
        return "手动触发"

    @staticmethod
    def _message(execution, children, metadata):
        summary = execution.summary or {}
        total = int(summary.get("total") or len(children))
        passed = int(summary.get("passed") or 0)
        failed = int(summary.get("failed") or 0)
        broken = int(summary.get("broken") or 0)
        cancelled = int(summary.get("cancelled") or 0)
        issue_count = failed + broken + cancelled
        conclusion = "通过" if total and issue_count == 0 and passed == total else "未通过"
        environment_name = metadata.get("environment_name") or "未命名环境"
        lines = [
            f"API 基线回归报告：{conclusion}",
            f"环境：{environment_name}",
            f"执行：{execution.id}",
            f"用例：共 {total} 条，通过 {passed}，失败 {failed}，异常 {broken}，取消 {cancelled}",
        ]
        cases = metadata.get("cases") or {}
        endpoints = metadata.get("endpoints") or {}
        issues = [item for item in children if item.status != "PASSED"][:5]
        if issues:
            lines.append("问题摘要：")
            versions = metadata.get("versions") or {}
            cases = metadata.get("cases") or {}
            endpoints = metadata.get("endpoints") or {}
            for item in issues:
                version = versions.get(item.case_version_id)
                case = cases.get(version.case_id) if version is not None else None
                endpoint = endpoints.get(item.endpoint_id)
                name = getattr(case, "name", "") or getattr(endpoint, "summary", "") or getattr(endpoint, "path", "")
                lines.append(f"- {item.status} · {name} · {item.failure_category or '未分类'}")
        return "\n".join(lines)
