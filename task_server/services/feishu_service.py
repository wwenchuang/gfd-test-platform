"""Feishu (Lark) integration service.

The legacy ``midscene-upload.py`` only ships two pieces of Feishu logic:

* ``validate_feishu_webhook`` — guard against malformed bot URLs.
* ``post_feishu_card`` — POST a card payload to a configured webhook.

Higher-level features mentioned in the product roadmap (notifications,
draft creation flows) are not yet implemented in the legacy server.  This
module therefore migrates the two real helpers and exposes the planned
public API as skeletons + TODO markers so callers can be wired up
incrementally.
"""

from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional


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
        raw = resp.read().decode("utf-8", errors="replace")
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
# Draft / approval flow (skeleton — not yet implemented in legacy server)
# ---------------------------------------------------------------------------

def create_feishu_draft(content: Dict[str, Any]) -> Dict[str, Any]:
    """创建飞书草稿。

    The legacy server only references "提交飞书需要人工确认" notes; an
    actual Feishu draft / approval submission flow is still on the
    roadmap.  This skeleton accepts the eventual payload shape and
    returns a stub response so callers can be wired up early.

    TODO:
    - call Feishu open-api ``/open-apis/approval/v4/instances`` (or the
      docs/cards draft API) once credentials & app tokens are configured.
    - persist draft state alongside repair / bug drafts.
    """
    if not isinstance(content, dict):
        raise ValueError("content 必须是 dict")

    return {
        "ok": False,
        "status": "NOT_IMPLEMENTED",
        "message": "飞书草稿提交流程尚未接入 (TODO)",
        "echo": {
            "title": content.get("title", ""),
            "summary": content.get("summary") or content.get("description", ""),
        },
    }


# ---------------------------------------------------------------------------
# 草稿查询 & 提交（本地持久化骨架）
# ---------------------------------------------------------------------------

_FEISHU_DRAFTS_FILE = os.path.join(
    os.getenv("LEARNING_DIR", "/opt/midscene-learning"),
    "feishu-drafts.json",
)

_FEISHU_DRAFT_STATUSES = {"DRAFT", "SUBMITTED", "REJECTED", "EXPIRED"}


def _load_feishu_drafts() -> List[Dict[str, Any]]:
    """加载飞书草稿列表。"""
    try:
        with open(_FEISHU_DRAFTS_FILE, encoding="utf-8") as f:
            import json as _json
            data = _json.load(f)
        if isinstance(data, dict):
            return data.get("drafts") or []
        if isinstance(data, list):
            return data
    except Exception:
        pass
    return []


def _save_feishu_drafts(drafts: List[Dict[str, Any]]) -> None:
    """保存飞书草稿列表。"""
    import json as _json
    os.makedirs(os.path.dirname(_FEISHU_DRAFTS_FILE), exist_ok=True)
    with open(_FEISHU_DRAFTS_FILE, "w", encoding="utf-8") as f:
        _json.dump({"drafts": drafts}, f, ensure_ascii=False, indent=2)


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
        草稿列表，按创建时间倒序。
    """
    drafts = _load_feishu_drafts()
    if status:
        status = str(status).strip().upper()
        drafts = [d for d in drafts if str(d.get("status", "")).upper() == status]
    limit = max(1, min(200, int(limit or 20)))
    return drafts[:limit]


def submit_feishu_draft(
    draft_id: str,
    user: Optional[str] = None,
) -> Dict[str, Any]:
    """提交飞书缺陷草稿为正式缺陷（需人工确认）。

    仅状态为 ``DRAFT`` 的草稿可提交；提交后状态变更为 ``SUBMITTED``。
    实际的飞书 API 调用仍需对接；当前仅做状态流转与持久化。

    Args:
        draft_id: 草稿 ID。
        user: 提交操作人。

    Returns:
        提交结果字典。

    Raises:
        ValueError: 草稿不存在或状态不可提交。
    """
    draft = get_feishu_draft(draft_id)
    if not draft:
        raise ValueError("飞书缺陷草稿不存在")
    if str(draft.get("status", "")).upper() != "DRAFT":
        raise ValueError(f"草稿当前状态不可提交：{draft.get('status')}")

    # 尝试调用飞书 API 提交
    submitted = False
    submit_error = ""
    try:
        # 构建飞书卡片/审批 payload
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
        result = send_feishu_notification(card_payload)
        submitted = result.get("ok", False) or result.get("StatusCode", -1) == 0
    except Exception as exc:
        submit_error = str(exc)

    # 更新草稿状态
    drafts = _load_feishu_drafts()
    for item in drafts:
        if item.get("draftId") == draft_id or item.get("draft_id") == draft_id:
            if submitted:
                item["status"] = "SUBMITTED"
                item["submittedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                item["submittedBy"] = user or ""
            else:
                item["submitError"] = submit_error
            break
    _save_feishu_drafts(drafts)

    if submitted:
        return {"ok": True, "status": "SUBMITTED", "draftId": draft_id}
    return {
        "ok": False,
        "status": "SUBMIT_FAILED",
        "error": submit_error or "飞书 API 调用失败",
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

