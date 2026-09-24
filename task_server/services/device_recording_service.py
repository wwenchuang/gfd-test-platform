"""Persistent recording sessions for Sonic-assisted Midscene case creation."""

from __future__ import annotations

import copy
import logging
import base64
import io
import hashlib
import secrets
import threading
import time
import uuid
import os
import re
import shutil
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
FINISHED_EVIDENCE_GRACE_SECONDS = 2 * 60
PRE_ACTION_FRAME_RETRY_SECONDS = 3
MAX_PRE_ACTION_FRAME_ATTEMPTS = 3
POST_ACTION_FRAME_SETTLE_SECONDS = 1.5
PRE_ACTION_FRAME_MAX_AGE_SECONDS = 60
PRE_ACTION_FRAME_IDLE_REFRESH_SECONDS = 45
PRE_ACTION_FRAME_TIMEOUT_SECONDS = 120
_LOCK = threading.RLock()
_RECOGNITION_SLOTS = threading.BoundedSemaphore(4)
_RECOGNITION_WORKERS = set()


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


def _recording_token(row: Dict[str, Any], recording_token: str, timestamp: float) -> None:
    if not secrets.compare_digest(str(row.get("recording_token_hash") or ""), _token_hash(recording_token)):
        raise PermissionError("录制令牌无效")
    if timestamp > float(row.get("recording_token_expires_ts") or 0):
        raise PermissionError("录制令牌已过期")


def _request_pre_action_frame(row: Dict[str, Any], timestamp: Optional[float] = None, *, retry: bool = False) -> None:
    row["pre_action_frame_status"] = "pending"
    row["pre_action_frame_stage"] = "waiting_runner"
    row.pop("pre_action_frame_picked_up_ts", None)
    row.pop("pre_action_frame_timed_out", None)
    row["pre_action_frame_refresh_pending"] = False
    row["pre_action_frame_request_id"] = uuid.uuid4().hex
    row["pre_action_frame_requested_ts"] = float(time.time() if timestamp is None else timestamp)
    row["pre_action_frame_attempts"] = int(row.get("pre_action_frame_attempts") or 0) + 1 if retry else 1
    row.pop("pre_action_frame_error", None)


def _refresh_pre_action_frame(row: Dict[str, Any], timestamp: float) -> None:
    """Refresh an idle frame without discarding it while it is still valid."""
    row["pre_action_frame_request_id"] = uuid.uuid4().hex
    row["pre_action_frame_requested_ts"] = timestamp
    row["pre_action_frame_refresh_pending"] = True
    row["pre_action_frame_stage"] = "waiting_runner"
    row.pop("pre_action_frame_picked_up_ts", None)
    row.pop("pre_action_frame_error", None)


def _expire_pre_action_frame(row: Dict[str, Any], timestamp: float) -> bool:
    if row.get("status") != "recording":
        return False
    if row.get("pre_action_frame_status") != "pending" and not row.get("pre_action_frame_refresh_pending"):
        return False
    requested = float(row.get("pre_action_frame_requested_ts") or 0)
    if not requested or timestamp - requested < PRE_ACTION_FRAME_TIMEOUT_SECONDS:
        return False
    row["pre_action_frame_status"] = "failed"
    row["pre_action_frame_stage"] = "failed"
    row["pre_action_frame_timed_out"] = True
    row["pre_action_frame_refresh_pending"] = False
    row["pre_action_frame_error"] = "真实点击前画面准备超过 120 秒，请检查 Runner/ADB 后手动重试"
    return True


def _public(row: Dict[str, Any]) -> Dict[str, Any]:
    value = copy.deepcopy(row)
    value.pop("recording_token_hash", None)
    return value


def _png_dimensions(content: bytes) -> tuple[int, int]:
    """Read PNG dimensions from IHDR without adding an image dependency."""
    if len(content) >= 24 and content.startswith(b"\x89PNG\r\n\x1a\n") and content[12:16] == b"IHDR":
        return int.from_bytes(content[16:20], "big"), int.from_bytes(content[20:24], "big")
    return 0, 0


def _scaled_point(point: Dict[str, Any], row: Dict[str, Any]) -> tuple[Dict[str, int], Optional[Dict[str, int]], str]:
    raw = {"x": int(point.get("x") or 0), "y": int(point.get("y") or 0)}
    source_width = int(row.get("pre_action_frame_coordinate_width") or 0)
    source_height = int(row.get("pre_action_frame_coordinate_height") or 0)
    image_width = int(row.get("pre_action_frame_image_width") or 0)
    image_height = int(row.get("pre_action_frame_image_height") or 0)
    if not all((source_width, source_height, image_width, image_height)):
        return raw, None, ""
    mapped = {
        "x": max(0, min(image_width - 1, round(raw["x"] * image_width / source_width))),
        "y": max(0, min(image_height - 1, round(raw["y"] * image_height / source_height))),
    }
    transform = f"{source_width}x{source_height}->{image_width}x{image_height}"
    return mapped, raw if mapped != raw else None, transform


def _point_in_ui_xml(step: Dict[str, Any], point: Dict[str, Any]) -> Dict[str, int]:
    """UIAutomator bounds use device pixels; recorded points use screenshot pixels."""
    transform = str(step.get("coordinate_transform") or "")
    match = re.fullmatch(r"(\d+)x(\d+)->(\d+)x(\d+)", transform)
    if not match:
        return point
    source_width, source_height, image_width, image_height = map(int, match.groups())
    if not image_width or not image_height:
        return point
    return {
        "x": max(0, min(source_width - 1, round(int(point.get("x") or 0) * source_width / image_width))),
        "y": max(0, min(source_height - 1, round(int(point.get("y") or 0) * source_height / image_height))),
    }


def create_recording_session(
    user: str,
    runner_id: str,
    device_id: str,
    app_package: str,
    *,
    module_name: str = "",
    store_path: Optional[str] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    user = str(user or "").strip()
    runner_id = str(runner_id or "").strip()
    device_id = str(device_id or "").strip()
    app_package = str(app_package or "").strip()
    if not user or not app_package:
        raise ValueError("用户和应用包名不能为空")
    if bool(runner_id) != bool(device_id):
        raise ValueError("Runner 和手机必须同时指定，或都由 Sonic 实际连接后绑定")
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
                if device_id
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
            "module_name": str(module_name or "").strip(),
            "created_at": _stamp(timestamp),
            "created_ts": timestamp,
            "updated_at": _stamp(timestamp),
            "updated_ts": timestamp,
            "heartbeat_ts": timestamp,
            "recording_token_hash": _token_hash(recording_token),
            "recording_token_expires_ts": timestamp + RECORDING_TOKEN_TTL_SECONDS,
            "steps": [],
        }
        if runner_id and device_id:
            _request_pre_action_frame(row, timestamp)
        data["sessions"].append(row)
        write_json_file(path, data)
        return {**_public(row), "recording_token": recording_token}


def bind_recording_device(
    session_id: str,
    user: str,
    runner_id: str,
    device_id: str,
    *,
    store_path: Optional[str] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    runner_id = str(runner_id or "").strip()
    device_id = str(device_id or "").strip()
    if not runner_id or not device_id:
        raise ValueError("Runner 和手机不能为空")
    if device_id.upper() in NON_MOBILE_DEVICE_IDS:
        raise ValueError("操作录制只能绑定 Sonic Android 手机")
    timestamp = float(time.time() if now is None else now)
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        row = _find(data, session_id)
        _owner(row, user)
        if row.get("status") != "recording":
            raise ValueError("录制会话当前不能绑定手机")
        if row.get("device_id"):
            if row.get("runner_id") == runner_id and row.get("device_id") == device_id:
                if not row.get("pre_action_frame_status"):
                    _request_pre_action_frame(row, timestamp)
                    write_json_file(path, data)
                return _public(row)
            raise ValueError("录制会话已经绑定另一台手机")
        conflict = next((item for item in data["sessions"] if item.get("id") != row.get("id") and item.get("status") in ACTIVE_STATUSES and item.get("runner_id") == runner_id and item.get("device_id") == device_id), None)
        if conflict:
            raise ValueError(f"设备正在录制，会话发起人：{conflict.get('created_by') or '未知'}")
        row["runner_id"] = runner_id
        row["device_id"] = device_id
        row["bound_at"] = _stamp(timestamp)
        row["updated_at"] = _stamp(timestamp)
        row["updated_ts"] = timestamp
        _request_pre_action_frame(row, timestamp)
        write_json_file(path, data)
        return _public(row)


def bridge_recording_device(
    session_id: str,
    recording_token: str,
    runner_id: str,
    device_id: str,
    *,
    refresh_evidence: bool = False,
    store_path: Optional[str] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Bind/poll a Sonic phone using only the short-lived recording capability."""
    runner_id = str(runner_id or "").strip()
    device_id = str(device_id or "").strip()
    if not runner_id or not device_id:
        raise ValueError("Runner 和手机不能为空")
    if device_id.upper() in NON_MOBILE_DEVICE_IDS:
        raise ValueError("操作录制只能绑定 Sonic Android 手机")
    timestamp = float(time.time() if now is None else now)
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        _pause_stale(data, timestamp)
        row = _find(data, session_id)
        _recording_token(row, recording_token, timestamp)
        if _expire_pre_action_frame(row, timestamp):
            write_json_file(path, data)
        if row.get("status") != "recording":
            raise ValueError("录制会话当前不能绑定手机")
        if row.get("device_id"):
            if row.get("runner_id") != runner_id or row.get("device_id") != device_id:
                raise ValueError("录制会话已经绑定另一台手机")
            frame_age = timestamp - float(row.get("pre_action_frame_captured_ts") or timestamp)
            if refresh_evidence or not row.get("pre_action_frame_status"):
                _request_pre_action_frame(row, timestamp)
                write_json_file(path, data)
            elif row.get("pre_action_frame_status") == "ready" and frame_age >= PRE_ACTION_FRAME_MAX_AGE_SECONDS:
                if not row.get("pre_action_frame_refresh_pending"):
                    _request_pre_action_frame(row, timestamp)
                else:
                    row["pre_action_frame_status"] = "pending"
                    row["pre_action_frame_refresh_pending"] = False
                write_json_file(path, data)
            elif (row.get("pre_action_frame_status") == "ready"
                  and frame_age >= PRE_ACTION_FRAME_IDLE_REFRESH_SECONDS
                  and not row.get("pre_action_frame_refresh_pending")):
                _refresh_pre_action_frame(row, timestamp)
                write_json_file(path, data)
            return _public(row)
        conflict = next((item for item in data["sessions"] if item.get("id") != row.get("id") and item.get("status") in ACTIVE_STATUSES and item.get("runner_id") == runner_id and item.get("device_id") == device_id), None)
        if conflict:
            raise ValueError(f"设备正在录制，会话发起人：{conflict.get('created_by') or '未知'}")
        row["runner_id"] = runner_id
        row["device_id"] = device_id
        row["bound_at"] = _stamp(timestamp)
        row["updated_at"] = _stamp(timestamp)
        row["updated_ts"] = timestamp
        _request_pre_action_frame(row, timestamp)
        write_json_file(path, data)
        return _public(row)


def get_recording_session(
    session_id: str, *, store_path: Optional[str] = None, now: Optional[float] = None
) -> Dict[str, Any]:
    timestamp = float(time.time() if now is None else now)
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        changed = _pause_stale(data, timestamp)
        row = _find(data, session_id)
        changed = _expire_pre_action_frame(row, timestamp) or changed
        if changed:
            write_json_file(path, data)
        return _public(row)


def list_recording_sessions(user: str, *, store_path: Optional[str] = None, limit: int = 30,
                            offset: int = 0, summary: bool = False) -> list[Dict[str, Any]]:
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        if _pause_stale(data, time.time()):
            write_json_file(path, data)
        rows = [
            row for row in data["sessions"]
            if str(row.get("created_by") or "") == str(user or "")
        ]
        rows.sort(key=lambda row: float(row.get("updated_ts") or 0), reverse=True)
        offset = max(0, int(offset))
        rows = rows[offset:offset + max(1, min(int(limit or 30), 100))]
        if summary:
            return [{
                **{key: row.get(key, "") for key in (
                    "id", "status", "app_package", "module_name", "device_id", "created_at", "updated_at", "finished_at")},
                "step_count": len(row.get("steps") or []),
                "has_generated_yaml": bool((row.get("generated_result") or {}).get("yaml")),
                "task_name": (row.get("generated_result") or {}).get("task_name", ""),
            } for row in rows]
        return [_public(row) for row in rows]


def set_recording_module(session_id: str, user: str, module_name: str, *,
                         store_path: Optional[str] = None) -> Dict[str, Any]:
    """Persist a module already validated against the app by the API boundary."""
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        row = _find(data, session_id)
        _owner(row, user)
        row["module_name"] = str(module_name or "").strip()
        row["updated_ts"] = time.time()
        row["updated_at"] = _stamp(row["updated_ts"])
        write_json_file(path, data)
        return _public(row)


def delete_recording_session(
    session_id: str,
    user: str,
    *,
    store_path: Optional[str] = None,
    evidence_dir: Optional[str] = None,
) -> Dict[str, Any]:
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        row = _find(data, session_id)
        _owner(row, user)
        if row.get("status") in {"recording", "generating"}:
            raise ValueError("录制仍在进行，请先取消后再删除")
        deleted = _public(row)
        data["sessions"] = [item for item in data["sessions"] if item is not row]
        write_json_file(path, data)
        root = os.path.abspath(os.path.join(evidence_dir or DEVICE_RECORDING_EVIDENCE_DIR, str(session_id)))
        parent = os.path.abspath(evidence_dir or DEVICE_RECORDING_EVIDENCE_DIR)
        if os.path.dirname(root) == parent and os.path.isdir(root):
            shutil.rmtree(root)
        return deleted


def save_generated_recording_result(session_id: str, user: str, result: Dict[str, Any], *, store_path: Optional[str] = None) -> Dict[str, Any]:
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        row = _find(data, session_id)
        _owner(row, user)
        row["generated_result"] = copy.deepcopy(result)
        row["generated_at"] = _stamp(time.time())
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
        _expire_pre_action_frame(row, timestamp)
        row["heartbeat_ts"] = timestamp
        row["updated_ts"] = timestamp
        row["updated_at"] = _stamp(timestamp)
        row.pop("pause_reason", None)
        if (
            row.get("pre_action_frame_status") == "failed"
            and not row.get("pre_action_frame_timed_out")
            and int(row.get("pre_action_frame_attempts") or 0) < MAX_PRE_ACTION_FRAME_ATTEMPTS
            and timestamp - float(row.get("pre_action_frame_requested_ts") or 0) >= PRE_ACTION_FRAME_RETRY_SECONDS
        ):
            _request_pre_action_frame(row, timestamp, retry=True)
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
        if not any(step.get("type") != "checkpoint" for step in row.get("steps") or []):
            raise ValueError("还没有记录到手机操作，不能结束为有效录制；可以取消本次录制")
        if row.get("status") != "finished":
            row["status"] = "finished"
            row["finished_at"] = _stamp(timestamp)
            row["updated_at"] = _stamp(timestamp)
            row["updated_ts"] = timestamp
            write_json_file(path, data)
        return _public(row)


def cancel_recording_session(
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
        if row.get("status") == "finished":
            raise ValueError("已结束的录制不能取消")
        row["status"] = "cancelled"
        row["cancelled_at"] = _stamp(timestamp)
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
    """Attach a human-reviewed description without changing the recorded action."""
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
        step["semantic_description"] = description
        step["semantic_confirmed_by"] = user
        step["semantic_confirmed_at"] = _stamp(timestamp)
        step["semantic_source"] = "manual"
        step.pop("semantic_recognition_request_id", None)
        step["semantic_recognition_status"] = "recognized"
        step.pop("semantic_recognition_error", None)
        step.pop("semantic_confidence", None)
        row.pop("generated_result", None)
        row["updated_ts"] = timestamp
        row["updated_at"] = _stamp(timestamp)
        write_json_file(path, data)
        return _public(row)


def update_recorded_step_point(
    session_id: str,
    user: str,
    step_id: str,
    point: Optional[Dict[str, Any]] = None,
    *,
    reset: bool = False,
    store_path: Optional[str] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    timestamp = float(time.time() if now is None else now)
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        row = _find(data, session_id)
        _owner(row, user)
        step = next((item for item in row.get("steps") or [] if item.get("id") == str(step_id)), None)
        if not step or step.get("type") != "tap":
            raise ValueError("只能校正点击步骤的位置")
        original = step.get("recorded_point") if isinstance(step.get("recorded_point"), dict) else step.get("point")
        if reset:
            target = copy.deepcopy(original or {})
        elif isinstance(point, dict):
            target = {"x": int(point.get("x") or 0), "y": int(point.get("y") or 0)}
        else:
            raise ValueError("点击坐标不能为空")
        screenshot = str(step.get("screenshot_path") or "")
        if not screenshot or not os.path.isfile(screenshot):
            raise ValueError("该步骤没有可校正的截图证据")
        with open(screenshot, "rb") as handle:
            image_width, image_height = _png_dimensions(handle.read(32))
        if not image_width or not image_height:
            raise ValueError("无法读取截图尺寸")
        target["x"] = max(0, min(image_width - 1, target["x"]))
        target["y"] = max(0, min(image_height - 1, target["y"]))
        step["point"] = target
        step["point_manually_adjusted"] = target != original
        xml_path = str(step.get("ui_xml_path") or "")
        xml_text = ""
        if xml_path and os.path.isfile(xml_path):
            with open(xml_path, encoding="utf-8", errors="replace") as handle:
                xml_text = handle.read()
        step["ui_node"] = _node_at_point(xml_text, _point_in_ui_xml(step, target))
        for key in (
            "semantic_description", "semantic_source", "semantic_confidence",
            "semantic_confirmed_by", "semantic_confirmed_at", "semantic_recognition_error", "semantic_recognition_request_id",
        ):
            step.pop(key, None)
        step["semantic_recognition_status"] = "pending"
        row.pop("generated_result", None)
        row["updated_ts"] = timestamp
        row["updated_at"] = _stamp(timestamp)
        write_json_file(path, data)
        return _public(row)


def delete_recorded_step(
    session_id: str,
    user: str,
    step_id: str,
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
        steps = list(row.get("steps") or [])
        if not any(item.get("id") == str(step_id) for item in steps):
            raise ValueError("录制步骤不存在")
        row["steps"] = [item for item in steps if item.get("id") != str(step_id)]
        row.pop("generated_result", None)
        for sequence, item in enumerate(row["steps"], 1):
            item["sequence"] = sequence
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
    evidence_dir: Optional[str] = None,
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
        _recording_token(row, recording_token, timestamp)
        if row.get("status") != "recording":
            raise ValueError("录制会话当前不可接收动作")
        if device_id != row.get("device_id"):
            raise ValueError("动作设备与录制设备不一致")
        existing = next((step for step in row.get("steps") or [] if step.get("event_id") == event_id), None)
        if existing:
            return {**copy.deepcopy(existing), "duplicate": True}
        if action_type != "checkpoint" and row.get("pre_action_frame_status") != "ready":
            raise ValueError("真实点击前画面尚未准备，当前操作未记录；请等待平台提示后重试")
        if (action_type != "checkpoint" and not action.get("evidence_content_base64")
                and timestamp - float(row.get("pre_action_frame_captured_ts") or 0) > PRE_ACTION_FRAME_MAX_AGE_SECONDS):
            raise ValueError("真实点击前画面已过期，当前操作未记录；请等待 Runner 更新截图")
        browser_png = None
        browser_frame_error = ""
        encoded_frame = str(action.get("evidence_content_base64") or "")
        if action_type != "checkpoint" and encoded_frame:
            try:
                if len(encoded_frame) > 17 * 1024 * 1024:
                    raise ValueError("画面过大")
                browser_png = base64.b64decode(encoded_frame, validate=True)
                width, height = _png_dimensions(browser_png)
                if len(browser_png) > 12 * 1024 * 1024 or not (100 <= width <= 4096 and 100 <= height <= 8192):
                    raise ValueError("画面尺寸或格式无效")
            except (ValueError, base64.binascii.Error) as exc:
                browser_png = None
                browser_frame_error = f"Sonic 当前画面采集失败：{exc}"
        scale_row = row
        if browser_png:
            scale_row = dict(row)
            scale_row["pre_action_frame_image_width"], scale_row["pre_action_frame_image_height"] = _png_dimensions(browser_png)
            if not scale_row.get("pre_action_frame_coordinate_width") or not scale_row.get("pre_action_frame_coordinate_height"):
                scale_row["pre_action_frame_coordinate_width"] = row.get("pre_action_frame_image_width")
                scale_row["pre_action_frame_coordinate_height"] = row.get("pre_action_frame_image_height")
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
            normalized["point"], raw_point, transform = _scaled_point(point, scale_row)
            normalized["recorded_point"] = copy.deepcopy(normalized["point"])
            if raw_point:
                normalized["raw_point"] = raw_point
                normalized["coordinate_transform"] = transform
        elif action_type == "swipe":
            normalized["start"], raw_start, transform = _scaled_point(action.get("start") or {}, scale_row)
            normalized["end"], raw_end, _ = _scaled_point(action.get("end") or {}, scale_row)
            if raw_start or raw_end:
                normalized["raw_start"] = raw_start or copy.deepcopy(normalized["start"])
                normalized["raw_end"] = raw_end or copy.deepcopy(normalized["end"])
                normalized["coordinate_transform"] = transform
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
        if action_type != "checkpoint" and row.get("pre_action_frame_status") == "ready":
            source_png = str(row.get("pre_action_frame_path") or "")
            source_xml = str(row.get("pre_action_frame_xml_path") or "")
            cached_frame_age = timestamp - float(row.get("pre_action_frame_captured_ts") or timestamp)
            if browser_png or os.path.isfile(source_png):
                root = os.path.join(evidence_dir or DEVICE_RECORDING_EVIDENCE_DIR, session_id)
                png_path = os.path.join(root, f"{normalized['id']}.png")
                xml_path = os.path.join(root, f"{normalized['id']}.xml")
                if browser_png:
                    png = browser_png
                    normalized["evidence_source"] = "sonic_live_frame"
                else:
                    with open(source_png, "rb") as handle:
                        png = handle.read()
                    normalized["evidence_source"] = "runner_cached_frame"
                xml_text = ""
                if not browser_png and source_xml and os.path.isfile(source_xml):
                    with open(source_xml, encoding="utf-8", errors="replace") as handle:
                        xml_text = handle.read()
                write_bytes_file(png_path, png)
                write_text_file(xml_path, xml_text)
                normalized["evidence_status"] = "captured"
                normalized["screenshot_path"] = png_path
                normalized["screenshot_sha256"] = hashlib.sha256(png).hexdigest()
                normalized["ui_xml_path"] = xml_path
                if row.get("pre_action_frame_ui_xml_error"):
                    normalized["ui_xml_error"] = row["pre_action_frame_ui_xml_error"]
                target_point = normalized.get("point") or normalized.get("end") or {}
                normalized["ui_node"] = _node_at_point(xml_text, _point_in_ui_xml(normalized, target_point))
                if browser_frame_error or (not browser_png and cached_frame_age > PRE_ACTION_FRAME_MAX_AGE_SECONDS):
                    normalized["evidence_status"] = "failed"
                    normalized["evidence_warning"] = browser_frame_error or "点击前截图已过期，不能据此识别控件；请人工核对"
                    normalized["ui_node"] = {}
        row.setdefault("steps", []).append(normalized)
        if action_type != "checkpoint":
            _request_pre_action_frame(row, timestamp)
        row["heartbeat_ts"] = timestamp
        row["updated_ts"] = timestamp
        row["updated_at"] = _stamp(timestamp)
        write_json_file(path, data)
        return {**copy.deepcopy(normalized), "duplicate": False}


def pending_recording_evidence_requests(
    runner_id: str,
    *,
    store_path: Optional[str] = None,
    now: Optional[float] = None,
) -> list:
    path = _path(store_path)
    timestamp = float(time.time() if now is None else now)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        requests = []
        changed = False
        for row in data["sessions"]:
            changed = _expire_pre_action_frame(row, timestamp) or changed
            within_finished_grace = (
                row.get("status") == "finished"
                and timestamp - float(row.get("updated_ts") or 0) <= FINISHED_EVIDENCE_GRACE_SECONDS
            )
            if (row.get("status") != "recording" and not within_finished_grace) or row.get("runner_id") != runner_id:
                continue
            # Sonic sends the action while the touch is still being dispatched. Capturing
            # the next frame immediately can freeze a page-transition animation and then
            # mistakenly mark that mixed frame as ready for the following tap.
            last_action_ts = max((float(step.get("recorded_ts") or 0) for step in row.get("steps") or []), default=0)
            frame_settled = not last_action_ts or timestamp - last_action_ts >= POST_ACTION_FRAME_SETTLE_SECONDS
            if (row.get("status") == "recording"
                    and (row.get("pre_action_frame_status") == "pending" or row.get("pre_action_frame_refresh_pending"))
                    and row.get("pre_action_frame_stage") != "capturing"
                    and frame_settled):
                requests.append({
                    "request_id": row.get("pre_action_frame_request_id"), "kind": "pre_action_frame",
                    "session_id": row["id"], "step_id": "", "device_id": row["device_id"], "sequence": 0,
                })
            for step in row.get("steps") or []:
                if step.get("evidence_status") == "pending":
                    requests.append({
                        "request_id": step["id"], "session_id": row["id"], "step_id": step["id"],
                        "device_id": row["device_id"], "sequence": step["sequence"],
                    })
        if changed:
            write_json_file(path, data)
        return requests[:10]


def _control_label(value: Any) -> str:
    label = str(value or "").strip()
    if len(label) > 200 or (len(label) >= 24 and re.fullmatch(r"[A-Za-z0-9+/=]+", label)):
        return ""
    return label


def _visual_image_assets(screenshot_path: str, point: Dict[str, Any]) -> list[Dict[str, str]]:
    with open(screenshot_path, "rb") as handle:
        png = handle.read()
    try:
        from PIL import Image, ImageDraw
        image = Image.open(io.BytesIO(png)).convert("RGB")
        x, y = int(point.get("x") or 0), int(point.get("y") or 0)
        # Keep the target closeup first, with an unmarked full frame for modal
        # context and text that the marker could cover.
        side = min(image.width, image.height, max(80, round(image.width * 0.28)))
        left = max(0, min(image.width - side, x - side // 2))
        top = max(0, min(image.height - side, y - side // 2))
        crop = image.crop((left, top, left + side, top + side))
        cx, cy = x - left, y - top
        draw = ImageDraw.Draw(crop)
        radius = max(5, round(side * 0.04))
        draw.ellipse((cx - radius, cy - radius, cx + radius, cy + radius), outline="#ff2929", width=2)
        draw.line((cx - radius // 2, cy, cx + radius // 2, cy), fill="#ff2929", width=1)
        draw.line((cx, cy - radius // 2, cx, cy + radius // 2), fill="#ff2929", width=1)
        crop = crop.resize((side * 4, side * 4), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        crop.save(output, format="PNG")
        return [
            {"name": "tap-target-closeup.png", "mime": "image/png", "base64": base64.b64encode(output.getvalue()).decode("ascii")},
            {"name": "phone-context.png", "mime": "image/png", "base64": base64.b64encode(png).decode("ascii")},
        ]
    except (ImportError, OSError, ValueError):
        return [{"name": os.path.basename(screenshot_path), "mime": "image/png", "base64": base64.b64encode(png).decode("ascii")}]


def _screen_visually_unchanged(before_path: str, after_png: bytes) -> bool:
    """Flag only near-identical frames; a visual difference cannot prove navigation."""
    try:
        from PIL import Image, ImageChops
        with Image.open(before_path) as before, Image.open(io.BytesIO(after_png)) as after:
            before_ratio = before.width / before.height
            after_ratio = after.width / after.height
            if abs(before_ratio - after_ratio) > 0.08:
                return False
            size = (72, 144)
            previous = before.convert("L").resize(size, Image.Resampling.BILINEAR)
            current = after.convert("L").resize(size, Image.Resampling.BILINEAR)
            pixels = ImageChops.difference(previous, current).tobytes()
            return sum(pixels) / len(pixels) < 4 and sum(value > 20 for value in pixels) / len(pixels) < 0.06
    except (ImportError, OSError, ValueError, ZeroDivisionError):
        return False


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
        "text": _control_label(attrs.get("text")), "content_desc": _control_label(attrs.get("content-desc")),
        "resource_id": _control_label(attrs.get("resource-id")), "class": attrs.get("class", ""),
        "bounds": attrs.get("bounds", ""),
    }


def save_recording_evidence(runner_id: str, payload: Dict[str, Any], *, store_path: Optional[str] = None, evidence_dir: Optional[str] = None, now: Optional[float] = None) -> Dict[str, Any]:
    session_id = str(payload.get("session_id") or payload.get("sessionId") or "")
    step_id = str(payload.get("step_id") or payload.get("stepId") or "")
    request_id = str(payload.get("request_id") or payload.get("requestId") or "")
    device_id = str(payload.get("device_id") or payload.get("deviceId") or "")
    timestamp = float(time.time() if now is None else now)
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        row = _find(data, session_id)
        if row.get("runner_id") != runner_id or row.get("device_id") != device_id:
            raise ValueError("证据不属于该 Runner 或设备")
        if not step_id:
            if _expire_pre_action_frame(row, timestamp):
                write_json_file(path, data)
            if (row.get("status") in FINAL_STATUSES
                    or request_id != str(row.get("pre_action_frame_request_id") or "")
                    or (row.get("pre_action_frame_status") == "failed" and row.get("pre_action_frame_timed_out"))
                    or (row.get("pre_action_frame_status") == "ready" and not row.get("pre_action_frame_refresh_pending"))):
                return _public(row)
            if payload.get("phase") == "capturing":
                if (row.get("pre_action_frame_status") == "pending" or row.get("pre_action_frame_refresh_pending")) and row.get("pre_action_frame_stage") != "capturing":
                    row["pre_action_frame_stage"] = "capturing"
                    row["pre_action_frame_picked_up_ts"] = timestamp
                    write_json_file(path, data)
                return _public(row)
            error = str(payload.get("error") or "").strip()[:500]
            if error:
                row["pre_action_frame_status"] = "failed"
                row["pre_action_frame_stage"] = "failed"
                row["pre_action_frame_error"] = error
                row["pre_action_frame_refresh_pending"] = False
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
                png_path = os.path.join(root, "_pre_action.png")
                xml_path = os.path.join(root, "_pre_action.xml")
                write_bytes_file(png_path, png)
                write_text_file(xml_path, xml_text)
                row["pre_action_frame_status"] = "ready"
                row["pre_action_frame_stage"] = "ready"
                row["pre_action_frame_refresh_pending"] = False
                row["pre_action_frame_captured_ts"] = timestamp
                row["pre_action_frame_path"] = png_path
                row["pre_action_frame_xml_path"] = xml_path
                row["pre_action_frame_sha256"] = hashlib.sha256(png).hexdigest()
                image_width, image_height = _png_dimensions(png)
                row["pre_action_frame_image_width"] = image_width
                row["pre_action_frame_image_height"] = image_height
                row["pre_action_frame_coordinate_width"] = max(0, int(payload.get("coordinate_width") or payload.get("coordinateWidth") or 0))
                row["pre_action_frame_coordinate_height"] = max(0, int(payload.get("coordinate_height") or payload.get("coordinateHeight") or 0))
                ui_xml_error = str(payload.get("ui_xml_error") or payload.get("uiXmlError") or "").strip()[:500]
                if ui_xml_error:
                    row["pre_action_frame_ui_xml_error"] = ui_xml_error
                else:
                    row.pop("pre_action_frame_ui_xml_error", None)
                previous_step = next((item for item in reversed(row.get("steps") or []) if item.get("type") != "checkpoint"), None)
                if previous_step and previous_step.get("evidence_status") == "captured":
                    previous_xml_path = str(previous_step.get("ui_xml_path") or "")
                    compared_xml = False
                    if previous_xml_path and os.path.isfile(previous_xml_path) and xml_text.strip():
                        with open(previous_xml_path, encoding="utf-8", errors="replace") as handle:
                            previous_xml = handle.read()
                        if previous_xml.strip():
                            compared_xml = True
                            unchanged = previous_xml.strip() == xml_text.strip()
                            previous_step["screen_change_status"] = "unchanged" if unchanged else "changed"
                            if unchanged and previous_step.get("type") == "tap":
                                previous_step["evidence_warning"] = "点击前后页面结构未变化；控件虽已识别，手机是否执行点击仍需核对，可重试或删除该步"
                    if not compared_xml and previous_step.get("type") == "tap" and _screen_visually_unchanged(
                        str(previous_step.get("screenshot_path") or ""), png
                    ):
                        previous_step["screen_change_status"] = "unchanged"
                        previous_step["evidence_warning"] = "点击前后手机画面几乎未变化；控件虽已识别，手机是否执行点击仍需核对，可重试或删除该步"
            write_json_file(path, data)
            return _public(row)
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
            step["screenshot_sha256"] = hashlib.sha256(png).hexdigest()
            if any(item is not step and item.get("screenshot_sha256") == step["screenshot_sha256"] for item in row.get("steps") or []):
                step["evidence_warning"] = "该截图与前一步骤重复，可能因连续点击过快导致证据滞后，请核对或手工标记"
            step["ui_xml_path"] = xml_path
            ui_xml_error = str(payload.get("ui_xml_error") or payload.get("uiXmlError") or "").strip()[:500]
            if ui_xml_error:
                step["ui_xml_error"] = ui_xml_error
            target_point = step.get("point") or step.get("end") or {}
            step["ui_node"] = _node_at_point(xml_text, _point_in_ui_xml(step, target_point))
        write_json_file(path, data)
        return _public(row)


def confirmed_recording_description(step: Dict[str, Any]) -> str:
    """XML may describe obscured background nodes; it is not visual confirmation."""
    if step.get("semantic_source") == "ui_xml" or step.get("semantic_recognition_status") in {"pending", "running", "failed"}:
        return ""
    return _control_label(step.get("semantic_description"))[:200]


def schedule_recording_recognition(session: Dict[str, Any], *, store_path: Optional[str] = None):
    """Recognize from authenticated Sonic polls even when the platform tab sleeps.

    At most four sessions run concurrently; busy sessions retry on their next poll.
    The existing recognition request IDs prevent stale or duplicate publication.
    """
    if session.get("status") != "recording" or not any(
        step.get("type") in {"tap", "text"} and step.get("evidence_status") == "captured"
        and step.get("semantic_recognition_status") not in {"running", "failed"}
        and not confirmed_recording_description(step) for step in session.get("steps", [])
    ):
        return None
    key = (_path(store_path), session["id"])
    with _LOCK:
        if key in _RECOGNITION_WORKERS or not _RECOGNITION_SLOTS.acquire(blocking=False):
            return None
        _RECOGNITION_WORKERS.add(key)

    def work():
        try:
            current = get_recording_session(session["id"], store_path=store_path)
            if current.get("status") == "recording":
                recognize_recording_semantics(session["id"], session["created_by"], store_path=store_path)
        except Exception:
            logging.getLogger(__name__).exception("Background recording recognition failed: %s", session["id"])
        finally:
            with _LOCK:
                _RECOGNITION_WORKERS.discard(key)
                _RECOGNITION_SLOTS.release()

    worker = threading.Thread(target=work, name="recording-recognition", daemon=True)
    try:
        worker.start()
    except Exception:
        with _LOCK:
            _RECOGNITION_WORKERS.discard(key)
            _RECOGNITION_SLOTS.release()
        raise
    return worker


def recognize_recording_semantics(
    session_id: str,
    user: str,
    *,
    store_path: Optional[str] = None,
    model_call=None,
    step_id: str = "",
    force: bool = False,
) -> Dict[str, Any]:
    """Name ambiguous tap/input targets from their captured phone screenshot."""
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        row = _find(data, session_id)
        _owner(row, user)
        candidates = []
        changed = False
        for step in row.get("steps") or []:
            if step_id and str(step.get("id") or "") != str(step_id):
                continue
            if force and step.get("type") in {"tap", "text"} and step.get("evidence_status") == "captured":
                xml_path = str(step.get("ui_xml_path") or "")
                if xml_path and os.path.isfile(xml_path):
                    with open(xml_path, encoding="utf-8", errors="replace") as handle:
                        xml_text = handle.read()
                    point = step.get("point") or step.get("end") or {}
                    step["ui_node"] = _node_at_point(xml_text, _point_in_ui_xml(step, point))
            if (
                step.get("type") in {"tap", "text"}
                and step.get("evidence_status") == "captured"
                and (force or not confirmed_recording_description(step))
                and (force or step.get("semantic_recognition_status") not in {"running", "failed"})
                and os.path.isfile(str(step.get("screenshot_path") or ""))
            ):
                for key in ("semantic_description", "semantic_source", "semantic_confidence"):
                    step.pop(key, None)
                step["semantic_recognition_status"] = "running"
                step["semantic_recognition_request_id"] = uuid.uuid4().hex
                changed = True
                candidates.append({
                    "id": step["id"], "type": step.get("type"),
                    "recognition_request_id": step["semantic_recognition_request_id"],
                    "point": copy.deepcopy(step.get("point") or step.get("end") or {}),
                    "screenshot_path": step["screenshot_path"],
                })
        if changed:
            row.pop("generated_result", None)
            write_json_file(path, data)
    if not candidates:
        return _public(row)


    if model_call is None:
        from .ai_skill_service import dashscope_chat_content
        model_call = dashscope_chat_content
    results = {}
    for item in candidates:
        try:
            point = item["point"]
            image_assets = _visual_image_assets(item["screenshot_path"], point)
            image_guide = (
                "第一张图只截取点击点附近，红色圆圈和十字标出实际点击点；第二张是未标注的完整截图，用于查看被红点遮挡的文字及前景弹窗。仅根据点击点处可见的文字或图标命名。"
                if image_assets[0]["name"] == "tap-target-closeup.png" else "这张图是整张手机截图，请严格根据点击坐标判断目标。"
            )
            prompt = f"""你是手机操作录制的控件识别器。截图来自真实 Android 手机，操作类型是{item['type']}，点击坐标为 x={int(point.get('x') or 0)}, y={int(point.get('y') or 0)}（坐标基于整张手机截图）。
{image_guide}必须识别红点命中的控件，不能因为局部图里另一个图标更显眼就回答它。
只识别该坐标实际命中的可见控件，用适合 Midscene aiTap/aiInput 的简短中文名称回答。如果存在弹窗，命中弹窗内的点只能识别前景弹窗的按钮，禁止用遮罩下的日期、列表文字或控件代替。不要描述整页，不要猜测不可见功能，也不要沿用之前步骤的控件名称。
只输出包含 semantic_description（控件短名称或空字符串）与 confidence（0 到 1 数值）的 JSON。无法确认时 semantic_description 为空字符串。"""
            raw = model_call(
                prompt,
                image_assets=image_assets,
                temperature=0.0,
                timeout=90,
                image_limit=2,
                retry_count=0,
                max_tokens=300,
            )
            from .yaml_service import normalize_model_json
            parsed = normalize_model_json(raw)
            description = str(parsed.get("semantic_description") or parsed.get("semanticDescription") or "").strip()[:200]
            confidence = max(0.0, min(1.0, float(parsed.get("confidence") or 0)))
            results[item["id"]] = {"description": description, "confidence": confidence}
        except Exception as exc:
            results[item["id"]] = {"error": str(exc)[:500]}

    request_ids = {item["id"]: item["recognition_request_id"] for item in candidates}
    with _LOCK, file_mutation_lock(path):
        data = _load(path)
        row = _find(data, session_id)
        _owner(row, user)
        for step in row.get("steps") or []:
            result = results.get(step.get("id"))
            if not result or step.get("semantic_recognition_request_id") != request_ids.get(step.get("id")):
                continue
            step.pop("semantic_recognition_request_id", None)
            step.pop("semantic_recognition_error", None)
            if result.get("description") and result.get("confidence", 0) >= 0.6:
                step["semantic_description"] = result["description"]
                step["semantic_source"] = "ai_visual"
                step["semantic_confidence"] = result["confidence"]
                step["semantic_recognition_status"] = "recognized"
            else:
                step["semantic_recognition_status"] = "failed"
                step["semantic_recognition_error"] = result.get("error") or "视觉模型无法确认点击控件"
        row["updated_ts"] = time.time()
        row["updated_at"] = _stamp(row["updated_ts"])
        write_json_file(path, data)
        return _public(row)


def recording_evidence_path(session_id: str, step_id: str, user: str, *, store_path: Optional[str] = None) -> str:
    path = _path(store_path)
    with _LOCK, file_mutation_lock(path):
        row = _find(_load(path), session_id)
        _owner(row, user)
        step = next((item for item in row.get("steps") or [] if str(item.get("id")) == str(step_id)), None)
        screenshot = str((step or {}).get("screenshot_path") or "")
        if not screenshot or not os.path.isfile(screenshot):
            raise ValueError("该步骤没有可查看的截图证据")
        return screenshot
