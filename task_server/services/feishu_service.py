"""Feishu (Lark) notifications and locally managed defect drafts."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from typing import Any, Dict, List, Optional

from task_server.core.http_client import read_response_bytes

try:
    import fcntl
except ImportError:  # pragma: no cover - production server is Linux
    fcntl = None


# ---------------------------------------------------------------------------
# Webhook configuration helpers (migrated verbatim from midscene-upload.py)
# ---------------------------------------------------------------------------

def _env_key_for_package(prefix: str, package: str) -> str:
    """Mirror midscene-upload.env_key_for_package for FEISHU_WEBHOOK_<pkg> lookup."""
    safe = "".join(ch.upper() if ch.isalnum() else "_" for ch in str(package or ""))
    return f"{prefix}{safe}".rstrip("_")


def validate_feishu_webhook(webhook: str) -> str:
    """Reject malformed Feishu webhook URLs (raises ValueError on bad input)."""
    value = str(webhook or "").strip()
    if not value:
        return ""
    if any(marker in value for marker in ("\r", "\n", "\t", "export ", "export\t")):
        raise ValueError("飞书 Webhook 配置异常：只能填写单行机器人地址，不能包含换行或 export 配置")
    if value[:1] in "\"'\u201c\u201d\u2018\u2019" or value[-1:] in "\"'\u201c\u201d\u2018\u2019":
        raise ValueError("飞书 Webhook 配置异常：请去掉地址外层引号，尤其不要使用中文引号")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("飞书 Webhook 配置异常：请填写完整的 http/https 机器人地址")
    return value


def default_feishu_webhook_for_package(package: str) -> str:
    """Resolve the per-package default webhook from environment variables."""
    return (
        os.getenv(_env_key_for_package("FEISHU_WEBHOOK_", package))
        or os.getenv("FEISHU_WEBHOOK_DEFAULT", "")
        or ""
    )


def task_app_feishu_webhook(app: Optional[Dict[str, Any]]) -> str:
    """Pick the right webhook for a task-app dict (legacy semantics)."""
    if not app:
        return validate_feishu_webhook(os.getenv("FEISHU_WEBHOOK_DEFAULT", ""))
    return validate_feishu_webhook(
        app.get("feishu_webhook")
        or app.get("feishuWebhook")
        or default_feishu_webhook_for_package(app.get("package", ""))
        or ""
    )


def task_app_feishu_delivery_status(app: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Return non-secret delivery readiness for application-management UIs."""
    app = app or {}
    package = str(app.get("package") or "").strip()
    explicit = app.get("feishu_webhook") or app.get("feishuWebhook") or ""
    package_key = _env_key_for_package("FEISHU_WEBHOOK_", package) if package else ""
    package_default = os.getenv(package_key, "") if package_key else ""
    platform_default = os.getenv("FEISHU_WEBHOOK_DEFAULT", "")
    if explicit:
        source, value, label = "app", explicit, "专属群"
    elif package_default:
        source, value, label = "package_default", package_default, "应用默认群"
    elif platform_default:
        source, value, label = "platform_default", platform_default, "平台默认群"
    else:
        return {"feishu_ready": False, "feishu_source": "missing", "feishu_target_label": "未配置"}
    try:
        ready = bool(validate_feishu_webhook(value))
    except ValueError as exc:
        return {
            "feishu_ready": False,
            "feishu_source": "invalid",
            "feishu_target_label": "配置无效",
            "feishu_config_error": str(exc),
        }
    return {"feishu_ready": ready, "feishu_source": source, "feishu_target_label": label}


# ---------------------------------------------------------------------------
# Sending primitives
# ---------------------------------------------------------------------------

def _post_to_webhook(webhook: str, payload: Dict[str, Any], timeout: int = 15) -> Dict[str, Any]:
    """Low-level POST helper (migrated from post_feishu_card)."""
    webhook = validate_feishu_webhook(webhook)
    if not webhook:
        raise ValueError("未配置应用对应的飞书机器人 Webhook")
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = read_response_bytes(resp, 1024 * 1024, "飞书机器人").decode(
            "utf-8", errors="replace"
        )
        return json.loads(raw) if raw else {"ok": True}


def send_feishu_card(card_data: Dict[str, Any], webhook: Optional[str] = None) -> Dict[str, Any]:
    """发送飞书卡片消息。

    *card_data* is the full Feishu interactive card payload (``msg_type``
    / ``card`` envelope).  *webhook* defaults to ``FEISHU_WEBHOOK_DEFAULT``
    when omitted.
    """
    target = webhook or os.getenv("FEISHU_WEBHOOK_DEFAULT", "")
    return _post_to_webhook(target, card_data)


def send_feishu_notification(
    payload: Dict[str, Any],
    webhook: Optional[str] = None,
) -> Dict[str, Any]:
    """发送飞书通知。

    Accepts either a raw Feishu envelope (``{"msg_type": ..., ...}``) or a
    convenience shape ``{"text": "..."}`` / ``{"title": ..., "content": ...}``.
    Convenience shapes are converted into a minimal text/post message so
    callers don't have to construct the envelope themselves.
    """
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是 dict")

    target = webhook or payload.get("webhook") or os.getenv("FEISHU_WEBHOOK_DEFAULT", "")

    if "msg_type" in payload:
        envelope = {k: v for k, v in payload.items() if k != "webhook"}
    elif "text" in payload:
        envelope = {
            "msg_type": "text",
            "content": {"text": str(payload.get("text") or "")},
        }
    elif "card" in payload:
        envelope = {"msg_type": "interactive", "card": payload["card"]}
    else:
        # TODO: extend with richer message shapes (post / image / share_chat)
        # once product flows define the canonical schema.
        raise ValueError("payload 必须包含 msg_type / text / card 之一")

    return _post_to_webhook(target, envelope)


# ---------------------------------------------------------------------------
# Defect draft persistence and manual submission
# ---------------------------------------------------------------------------

_FEISHU_DRAFTS_FILE = os.path.join(
    os.getenv("LEARNING_DIR", "/opt/midscene-learning"),
    "feishu-drafts.json",
)

_FEISHU_DRAFT_STATUSES = {"DRAFT", "SUBMITTED", "REJECTED", "EXPIRED"}
_FEISHU_DRAFT_THREAD_LOCK = threading.RLock()


@contextmanager
def _feishu_draft_store_lock():
    """Serialize draft mutations across request threads and worker processes."""
    directory = os.path.dirname(_FEISHU_DRAFTS_FILE)
    if directory:
        os.makedirs(directory, exist_ok=True)
    lock_path = f"{_FEISHU_DRAFTS_FILE}.lock"
    with _FEISHU_DRAFT_THREAD_LOCK:
        with open(lock_path, "a+", encoding="utf-8") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_feishu_drafts() -> List[Dict[str, Any]]:
    """加载飞书草稿列表。"""
    try:
        with open(_FEISHU_DRAFTS_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data.get("drafts") or []
        if isinstance(data, list):
            return data
        raise RuntimeError("飞书缺陷草稿文件格式无效")
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"读取飞书缺陷草稿失败：{exc}") from exc


def _save_feishu_drafts(drafts: List[Dict[str, Any]]) -> None:
    """保存飞书草稿列表。"""
    directory = os.path.dirname(_FEISHU_DRAFTS_FILE)
    if directory:
        os.makedirs(directory, exist_ok=True)
    temp_path = f"{_FEISHU_DRAFTS_FILE}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    try:
        with open(temp_path, "w", encoding="utf-8") as f:
            json.dump({"drafts": drafts}, f, ensure_ascii=False, indent=2)
        os.replace(temp_path, _FEISHU_DRAFTS_FILE)
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def create_feishu_draft(content: Dict[str, Any]) -> Dict[str, Any]:
    """Create or update a local defect draft without sending it to Feishu."""
    if not isinstance(content, dict):
        raise ValueError("content 必须是 dict")
    title = str(content.get("title") or "").strip()
    description = str(content.get("description") or content.get("summary") or "").strip()
    if not title:
        raise ValueError("缺陷草稿标题不能为空")
    if not description:
        raise ValueError("缺陷草稿描述不能为空")

    draft_id = str(content.get("draftId") or content.get("draft_id") or uuid.uuid4().hex).strip()
    with _feishu_draft_store_lock():
        drafts = _load_feishu_drafts()
        existing = next(
            (item for item in drafts if str(item.get("draftId") or item.get("draft_id") or "") == draft_id),
            None,
        )
        now = _now_iso()
        if existing and str(existing.get("status") or "DRAFT").upper() != "DRAFT":
            raise ValueError(f"草稿当前状态不可修改：{existing.get('status')}")

        record = dict(existing or {})
        for key in (
            "title", "description", "summary", "severity", "priority", "appPackage",
            "appName", "sourceRunId", "sourceJobId", "reportUrl", "steps", "expected",
            "actual", "attachments", "failureType", "type", "failedJobs", "modelTrace",
        ):
            if key in content:
                record[key] = content.get(key)
        record.update({
            "draftId": draft_id,
            "title": title,
            "description": description,
            "status": "DRAFT",
            "createdAt": record.get("createdAt") or now,
            "updatedAt": now,
            "submitError": "",
        })
        if existing:
            existing.clear()
            existing.update(record)
        else:
            drafts.append(record)
        _save_feishu_drafts(drafts)
        return {"ok": True, "status": "DRAFT", "draft": record}


def get_feishu_draft(draft_id: str) -> Optional[Dict[str, Any]]:
    """查询单个飞书缺陷草稿。

    Args:
        draft_id: 草稿 ID。

    Returns:
        草稿字典；未找到时返回 ``None``。
    """
    draft_id = str(draft_id or "").strip()
    if not draft_id:
        return None
    for draft in _load_feishu_drafts():
        if draft.get("draftId") == draft_id or draft.get("draft_id") == draft_id:
            return draft
    return None


def list_feishu_drafts(
    status: Optional[str] = None,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    """列出飞书缺陷草稿。

    Args:
        status: 可选，按状态过滤（DRAFT / SUBMITTED / REJECTED / EXPIRED）。
        limit: 最大返回条数，默认 20。

    Returns:
        草稿列表，按最近更新时间倒序。
    """
    drafts = sorted(
        _load_feishu_drafts(),
        key=lambda item: str(item.get("updatedAt") or item.get("createdAt") or ""),
        reverse=True,
    )
    if status:
        status = str(status).strip().upper()
        drafts = [d for d in drafts if str(d.get("status", "")).upper() == status]
    limit = max(1, min(200, int(limit or 20)))
    return drafts[:limit]


def _webhook_for_draft(draft: Dict[str, Any]) -> str:
    """Resolve a draft's app-specific webhook at send time."""
    package = str(draft.get("appPackage") or draft.get("package") or "").strip()
    if not package:
        raise ValueError("缺陷草稿未关联平台应用，不能发送到默认通知群")
    from task_server.services.job_service import load_task_apps

    configured = load_task_apps()
    apps = configured.get("apps") if isinstance(configured, dict) else configured
    apps = apps if isinstance(apps, list) else []
    app = next(
        (item for item in apps if isinstance(item, dict) and str(item.get("package") or "").strip() == package),
        None,
    )
    if not app:
        raise ValueError(f"缺陷草稿应用 {package} 不在平台应用配置中")
    return task_app_feishu_webhook(app)


def reject_feishu_draft(
    draft_id: str,
    user: Optional[str] = None,
    reason: Optional[str] = None,
) -> Dict[str, Any]:
    """Reject a pending draft while preserving it for audit."""
    with _feishu_draft_store_lock():
        drafts = _load_feishu_drafts()
        draft = next(
            (item for item in drafts if str(item.get("draftId") or item.get("draft_id") or "") == str(draft_id or "")),
            None,
        )
        if not draft:
            raise ValueError("飞书缺陷草稿不存在")
        if str(draft.get("status") or "").upper() != "DRAFT":
            raise ValueError(f"草稿当前状态不可拒绝：{draft.get('status')}")
        draft.update({
            "status": "REJECTED",
            "rejectedAt": _now_iso(),
            "rejectedBy": user or "",
            "rejectReason": str(reason or "").strip(),
            "updatedAt": _now_iso(),
        })
        _save_feishu_drafts(drafts)
        return {"ok": True, "status": "REJECTED", "draftId": draft_id}


def _feishu_response_succeeded(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    if result.get("ok") is True:
        return True
    for key in ("code", "StatusCode", "statusCode"):
        if key in result and str(result.get(key)).strip() == "0":
            return True
    return False


def submit_feishu_draft(
    draft_id: str,
    user: Optional[str] = None,
) -> Dict[str, Any]:
    """提交飞书缺陷草稿为正式缺陷（需人工确认）。

    仅状态为 ``DRAFT`` 的草稿可提交；提交后状态变更为 ``SUBMITTED``。
    Args:
        draft_id: 草稿 ID。
        user: 提交操作人。

    Returns:
        提交结果字典。

    Raises:
        ValueError: 草稿不存在或状态不可提交。
    """
    with _feishu_draft_store_lock():
        drafts = _load_feishu_drafts()
        draft = next(
            (item for item in drafts if str(item.get("draftId") or item.get("draft_id") or "") == str(draft_id or "")),
            None,
        )
        if not draft:
            raise ValueError("飞书缺陷草稿不存在")
        if str(draft.get("status", "")).upper() != "DRAFT":
            raise ValueError(f"草稿当前状态不可提交：{draft.get('status')}")

        submitted = False
        submit_error = ""
        try:
            card_payload = {
                "msg_type": "interactive",
                "card": {
                    "header": {
                        "title": {"tag": "plain_text", "content": f"缺陷提交：{draft.get('title', '')}"},
                    },
                    "elements": [
                        {"tag": "div", "text": {"tag": "plain_text", "content": str(draft.get("description", ""))[:2000]}},
                        {"tag": "div", "text": {"tag": "plain_text", "content": f"提交人：{user or '系统'}"}},
                    ],
                },
            }
            result = send_feishu_notification(card_payload, webhook=_webhook_for_draft(draft))
            submitted = _feishu_response_succeeded(result)
            if not submitted and isinstance(result, dict):
                submit_error = str(result.get("msg") or result.get("message") or result.get("error") or "")
        except Exception as exc:
            submit_error = str(exc)

        if submitted:
            draft["status"] = "SUBMITTED"
            draft["submittedAt"] = _now_iso()
            draft["submittedBy"] = user or ""
            draft["submitError"] = ""
        else:
            draft["submitError"] = submit_error or "飞书 API 调用失败"
        draft["updatedAt"] = _now_iso()
        _save_feishu_drafts(drafts)

        if submitted:
            return {"ok": True, "status": "SUBMITTED", "draftId": draft_id}
        return {
            "ok": False,
            "status": "SUBMIT_FAILED",
            "error": draft["submitError"],
            "draftId": draft_id,
        }


# ---------------------------------------------------------------------------
# Legacy-compatible alias (from midscene-upload.py:post_feishu_card)
# ---------------------------------------------------------------------------

def post_feishu_card(webhook: str, card: Dict[str, Any]) -> Dict[str, Any]:
    """发送飞书卡片消息（兼容旧版签名）。

    Migrated from ``midscene-upload.py:post_feishu_card``。
    与 ``_post_to_webhook`` 相同，但参数顺序为 ``(webhook, card)``
    而非 ``_post_to_webhook(webhook, payload)``。
    """
    return _post_to_webhook(webhook, card)
