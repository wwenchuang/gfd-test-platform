"""API testing report notification orchestration."""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from task_server.services.feishu_service import (
    send_feishu_notification,
    validate_feishu_webhook,
)

from ..crypto import decrypt_secret, encrypt_secret, secret_fingerprint
from ..repositories.execution_repository import ExecutionRepository
from ..repositories.notification_repository import NotificationRepository


FEISHU_CHANNEL = "feishu"


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


def _text(value, field, maximum):
    if not isinstance(value, str) or len(value.strip()) > maximum:
        raise NotificationInputError(f"{field} is invalid")
    return value.strip()


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
                webhook = validate_feishu_webhook(webhook)
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
            children = execution_repository.get_execution_cases(execution.id)
            metadata = execution_repository.display_metadata(execution, children)
            webhook = decrypt_secret(notification.ciphertext)
            send_feishu_notification(
                {"text": self._message(execution, children, metadata)},
                webhook=webhook,
            )
            return NotificationSendView(
                execution_id=execution.id,
                channel_type=FEISHU_CHANNEL,
                sent=True,
                message="飞书报告已发送",
            )

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
