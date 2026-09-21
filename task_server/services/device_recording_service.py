"""Persistent recording sessions for Sonic-assisted Midscene case creation."""

from __future__ import annotations

import copy
import base64
import hashlib
import secrets
import threading
import time
import uuid
import os
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, Optional

from ..config import DEVICE_RECORDINGS_FILE, DEVICE_RECORDING_STALE_SECONDS, DEVICE_RECORDING_EVIDENCE_DIR
from ..storage import file_mutation_lock, read_json_file, write_json_file, write_bytes_file, write_text_file

NON_MOBILE_DEVICE_IDS = {"9888E0094F2A", "18CEDF5BA7B2"}
RECORDING_STALE_SECONDS = DEVICE_RECORDING_STALE_SECONDS
ACTIVE_STATUSES = {"recording"}
FINAL_STATUSES = {"finished", "cancelled"}
ALL_STATUSES = ACTIVE_STATUSES | {"paused", "generating"} | FINAL_STATUSES
ALLOWED_ACTION_TYPES = {"tap", "swipe", "text", "key", "launch", "checkpoint"}
MAX_INPUT_TEXT_LENGTH = 500
RECORDING_TOKEN_TTL_SECONDS = 2 * 60 * 60
_LOCK = threading.RLock()


def _path(store_path: Optional[str]) -> str:
    return str(store_path or DEVICE_RECORDINGS_FILE)


def _load(path: str) -> Dict[str, Any]:
    value = read_json_file(path, default={"sessions": []})
    if not isinstance(value, dict) or not isinstance(value.get("sessions"), list):
        return {"sessions": []}
    return value


def _stamp(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))


def _pause_stale(data: Dict[str, Any], now: float) -> bool:
    changed = False
    for row in data["sessions"]:
        if row.get("status") != "recording":
            continue
        heartbeat = float(row.get("heartbeat_ts") or row.get("created_ts") or 0)
        if now - heartbeat > RECORDING_STALE_SECONDS:
            row["status"] = "paused"
            row["paused_at"] = _stamp(now)
            row["updated_at"] = _stamp(now)
            row["updated_ts"] = now
            row["pause_reason"] = "录制心跳超时"
            changed = True
    return changed


def _find(data: Dict[str, Any], session_id: str) -> Dict[str, Any]:
    row = next((item for item in data["sessions"] if str(item.get("id")) == str(session_id)), None)
    if not row:
        raise ValueError("录制会话不存在")
    return row


def _owner(row: Dict[str, Any], user: str) -> None:
    if not user or str(row.get("created_by") or "") != str(user):
        raise PermissionError("只有录制发起人可以操作该会话")


def _token_hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _public(row: Dict[str, Any]) -> Dict[str, Any]:
    value = copy.deepcopy(row)
    value.pop("recording_token_hash", None)
    return value


def create_recording_session(
    user: str,
    runner_id: str,
    device_id: str,
    app_package: str,
    *,
    store_path: Optional[str] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    user = str(user or "").strip()
    runner_id = str(runner_id or "").strip()
    device_id = str(device_id or "").strip()
    app_package = str(app_package or "").strip()
    if not user or not runner_id or not app_package:
        raise ValueError("用户、Runner 和应用包名不能为空")
    if not device_id:
        raise ValueError("必须选择一台在线 Android 手机")
    if device_id.upper() in NON_MOBILE_DEVICE_IDS:
        raise ValueError("操作录制只能选择 Sonic Android 手机，不能使用业务打印机编号")
    timestamp = float(time.time() if now is None else now)
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        _pause_stale(data, timestamp)
        existing = next(
            (
                item for item in data["sessions"]
                if item.get("status") in ACTIVE_STATUSES
                and str(item.get("runner_id")) == runner_id
                and str(item.get("device_id")) == device_id
            ),
            None,
        )
        if existing:
            raise ValueError(f"设备正在录制，会话发起人：{existing.get('created_by') or '未知'}")
        session_id = uuid.uuid4().hex
        recording_token = secrets.token_urlsafe(32)
        row = {
            "id": session_id,
            "status": "recording",
            "created_by": user,
            "runner_id": runner_id,
            "device_id": device_id,
            "app_package": app_package,
            "created_at": _stamp(timestamp),
            "created_ts": timestamp,
            "updated_at": _stamp(timestamp),
            "updated_ts": timestamp,
            "heartbeat_ts": timestamp,
            "recording_token_hash": _token_hash(recording_token),
            "recording_token_expires_ts": timestamp + RECORDING_TOKEN_TTL_SECONDS,
            "steps": [],
        }
        data["sessions"].append(row)
        write_json_file(path, data)
        return {**_public(row), "recording_token": recording_token}


def get_recording_session(
    session_id: str, *, store_path: Optional[str] = None, now: Optional[float] = None
) -> Dict[str, Any]:
    timestamp = float(time.time() if now is None else now)
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        changed = _pause_stale(data, timestamp)
        row = _find(data, session_id)
        if changed:
            write_json_file(path, data)
        return _public(row)


def active_recording_for_device(
    runner_id: str,
    device_id: str,
    *,
    store_path: Optional[str] = None,
    now: Optional[float] = None,
) -> Optional[Dict[str, Any]]:
    timestamp = float(time.time() if now is None else now)
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        changed = _pause_stale(data, timestamp)
        row = next(
            (
                item for item in data["sessions"]
                if item.get("status") in ACTIVE_STATUSES
                and str(item.get("runner_id")) == str(runner_id)
                and str(item.get("device_id")) == str(device_id)
            ),
            None,
        )
        if changed:
            write_json_file(path, data)
        return _public(row) if row else None


def touch_recording_session(
    session_id: str,
    user: str,
    *,
    store_path: Optional[str] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    timestamp = float(time.time() if now is None else now)
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        _pause_stale(data, timestamp)
        row = _find(data, session_id)
        _owner(row, user)
        if row.get("status") in FINAL_STATUSES:
            raise ValueError("录制会话已经结束")
        conflicting = next(
            (
                item for item in data["sessions"]
                if item.get("id") != row.get("id")
                and item.get("status") in ACTIVE_STATUSES
                and item.get("runner_id") == row.get("runner_id")
                and item.get("device_id") == row.get("device_id")
            ),
            None,
        )
        if conflicting:
            raise ValueError("设备已经被其他录制会话占用")
        row["status"] = "recording"
        row["heartbeat_ts"] = timestamp
        row["updated_ts"] = timestamp
        row["updated_at"] = _stamp(timestamp)
        row.pop("pause_reason", None)
        write_json_file(path, data)
        return _public(row)


def finish_recording_session(
    session_id: str,
    user: str,
    *,
    store_path: Optional[str] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    timestamp = float(time.time() if now is None else now)
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        row = _find(data, session_id)
        _owner(row, user)
        if row.get("status") == "cancelled":
            raise ValueError("已取消的录制不能完成")
        if row.get("status") != "finished":
            row["status"] = "finished"
            row["finished_at"] = _stamp(timestamp)
            row["updated_at"] = _stamp(timestamp)
            row["updated_ts"] = timestamp
            write_json_file(path, data)
        return _public(row)


def update_recorded_step(
    session_id: str,
    user: str,
    step_id: str,
    semantic_description: str,
    *,
    store_path: Optional[str] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Attach a human-reviewed semantic target without changing the recorded action."""
    description = str(semantic_description or "").strip()
    if not description:
        raise ValueError("控件说明不能为空")
    if len(description) > 200:
        raise ValueError("控件说明不能超过 200 个字符")
    timestamp = float(time.time() if now is None else now)
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        row = _find(data, session_id)
        _owner(row, user)
        step = next((item for item in row.get("steps") or [] if item.get("id") == str(step_id)), None)
        if not step:
            raise ValueError("录制步骤不存在")
        if step.get("type") not in {"tap", "text"}:
            raise ValueError("该步骤不需要补充控件说明")
        step["semantic_description"] = description
        step["semantic_confirmed_by"] = user
        step["semantic_confirmed_at"] = _stamp(timestamp)
        row["updated_ts"] = timestamp
        row["updated_at"] = _stamp(timestamp)
        write_json_file(path, data)
        return _public(row)


def append_recorded_action(
    session_id: str,
    recording_token: str,
    action: Dict[str, Any],
    *,
    store_path: Optional[str] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    if not isinstance(action, dict):
        raise ValueError("录制动作格式无效")
    timestamp = float(time.time() if now is None else now)
    event_id = str(action.get("event_id") or action.get("eventId") or "").strip()[:120]
    action_type = str(action.get("type") or "").strip().lower()
    device_id = str(action.get("device_id") or action.get("deviceId") or "").strip()
    if not event_id:
        raise ValueError("动作 event_id 不能为空")
    if action_type not in ALLOWED_ACTION_TYPES:
        raise ValueError("不支持的录制动作类型")
    if action_type == "text" and len(str(action.get("text") or "")) > MAX_INPUT_TEXT_LENGTH:
        raise ValueError("输入文本超过长度限制")
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        _pause_stale(data, timestamp)
        row = _find(data, session_id)
        if not secrets.compare_digest(str(row.get("recording_token_hash") or ""), _token_hash(recording_token)):
            raise PermissionError("录制令牌无效")
        if timestamp > float(row.get("recording_token_expires_ts") or 0):
            raise PermissionError("录制令牌已过期")
        if row.get("status") != "recording":
            raise ValueError("录制会话当前不可接收动作")
        if device_id != row.get("device_id"):
            raise ValueError("动作设备与录制设备不一致")
        existing = next((step for step in row.get("steps") or [] if step.get("event_id") == event_id), None)
        if existing:
            return {**copy.deepcopy(existing), "duplicate": True}
        sequence = max([int(step.get("sequence") or 0) for step in row.get("steps") or []] or [0]) + 1
        normalized = {
            "id": uuid.uuid4().hex,
            "event_id": event_id,
            "sequence": sequence,
            "type": action_type,
            "device_id": device_id,
            "recorded_at": _stamp(timestamp),
            "recorded_ts": timestamp,
            "evidence_status": "pending",
        }
        if action_type == "tap":
            point = action.get("point") if isinstance(action.get("point"), dict) else {}
            normalized["point"] = {"x": int(point.get("x") or 0), "y": int(point.get("y") or 0)}
        elif action_type == "swipe":
            normalized["start"] = copy.deepcopy(action.get("start") or {})
            normalized["end"] = copy.deepcopy(action.get("end") or {})
            normalized["duration_ms"] = min(5000, max(0, int(action.get("duration_ms") or action.get("durationMs") or 0)))
        elif action_type == "text":
            normalized["text"] = str(action.get("text") or "")
        elif action_type == "key":
            key = str(action.get("key") or "").upper()
            if key not in {"BACK", "ENTER", "HOME"}:
                raise ValueError("不支持的按键")
            normalized["key"] = key
        elif action_type == "launch":
            normalized["package"] = str(action.get("package") or row.get("app_package") or "")[:200]
        elif action_type == "checkpoint":
            normalized["description"] = str(action.get("description") or "").strip()[:500]
            normalized["checkpoint_kind"] = "assert" if action.get("checkpoint_kind") == "assert" else "wait"
        row.setdefault("steps", []).append(normalized)
        row["heartbeat_ts"] = timestamp
        row["updated_ts"] = timestamp
        row["updated_at"] = _stamp(timestamp)
        write_json_file(path, data)
        return {**copy.deepcopy(normalized), "duplicate": False}


def pending_recording_evidence_requests(runner_id: str, *, store_path: Optional[str] = None) -> list:
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        requests = []
        for row in data["sessions"]:
            if row.get("status") != "recording" or row.get("runner_id") != runner_id:
                continue
            for step in row.get("steps") or []:
                if step.get("evidence_status") == "pending":
                    requests.append({
                        "request_id": step["id"], "session_id": row["id"], "step_id": step["id"],
                        "device_id": row["device_id"], "sequence": step["sequence"],
                    })
        return requests[:10]


def _node_at_point(xml_text: str, point_value: Dict[str, Any]) -> Dict[str, Any]:
    x, y = int(point_value.get("x") or 0), int(point_value.get("y") or 0)
    candidates = []
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return {}
    for node in root.iter("node"):
        match = re.fullmatch(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", node.attrib.get("bounds", ""))
        if not match:
            continue
        left, top, right, bottom = map(int, match.groups())
        if left <= x <= right and top <= y <= bottom:
            candidates.append(((right - left) * (bottom - top), node.attrib))
    if not candidates:
        return {}
    attrs = min(candidates, key=lambda item: item[0])[1]
    return {
        "text": attrs.get("text", ""), "content_desc": attrs.get("content-desc", ""),
        "resource_id": attrs.get("resource-id", ""), "class": attrs.get("class", ""),
        "bounds": attrs.get("bounds", ""),
    }


def save_recording_evidence(runner_id: str, payload: Dict[str, Any], *, store_path: Optional[str] = None, evidence_dir: Optional[str] = None) -> Dict[str, Any]:
    session_id = str(payload.get("session_id") or payload.get("sessionId") or "")
    step_id = str(payload.get("step_id") or payload.get("stepId") or "")
    device_id = str(payload.get("device_id") or payload.get("deviceId") or "")
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        row = _find(data, session_id)
        if row.get("runner_id") != runner_id or row.get("device_id") != device_id:
            raise ValueError("证据不属于该 Runner 或设备")
        step = next((item for item in row.get("steps") or [] if item.get("id") == step_id), None)
        if not step:
            raise ValueError("录制步骤不存在")
        if step.get("evidence_status") == "captured":
            return _public(row)
        error = str(payload.get("error") or "").strip()[:500]
        if error:
            step["evidence_status"] = "failed"
            step["evidence_error"] = error
        else:
            xml_text = str(payload.get("ui_xml") or payload.get("uiXml") or "")
            encoded = str(payload.get("content_base64") or payload.get("contentBase64") or "")
            if len(xml_text.encode("utf-8")) > 2 * 1024 * 1024:
                raise ValueError("页面结构超过大小限制")
            try:
                png = base64.b64decode(encoded, validate=True)
            except Exception as exc:
                raise ValueError("证据截图编码无效") from exc
            if len(png) > 12 * 1024 * 1024 or not png.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("证据截图无效或超过大小限制")
            root = os.path.join(evidence_dir or DEVICE_RECORDING_EVIDENCE_DIR, session_id)
            png_path = os.path.join(root, f"{step_id}.png")
            xml_path = os.path.join(root, f"{step_id}.xml")
            write_bytes_file(png_path, png)
            write_text_file(xml_path, xml_text)
            step["evidence_status"] = "captured"
            step["screenshot_path"] = png_path
            step["ui_xml_path"] = xml_path
            target_point = step.get("point") or step.get("end") or {}
            step["ui_node"] = _node_at_point(xml_text, target_point)
        write_json_file(path, data)
        return _public(row)
