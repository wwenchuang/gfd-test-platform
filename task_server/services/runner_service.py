"""Runner 管理服务层。

从 ``midscene-upload.py`` 中抽取 Runner 注册、心跳、列表查询与任务分发的业务
逻辑，提供线程安全的纯函数接口。本模块**不直接处理 HTTP** —— 由 router/HTTP
处理层负责鉴权、参数解析与响应封装。

设计原则：
- 仅依赖 ``task_server.config`` / ``task_server.storage``，不反向依赖
  ``midscene-upload.py``。
- 所有读写 Runner 注册表的操作必须持有 :data:`RUNNER_LOCK`。
- Runner 在线状态通过 ``last_seen_ts`` 与当前时间的差值判断；超时阈值默认与原实
  现保持一致（45 秒），同时暴露 ``ONLINE_TIMEOUT_SECONDS`` 常量便于调用方覆盖。
"""

from __future__ import annotations

import os
import time
import urllib.parse
from typing import Any, Dict, Iterable, List, Optional, Set

from ..config import (
    DEFAULT_DASHSCOPE_BASE_URL,
    DEFAULT_REPLANNING_CYCLE_LIMIT,
    DEFAULT_TEXT_MODEL,
    DEFAULT_VL_MODEL,
    JOB_LOCK,
    JOBS_FILE,
    PORT,
    RUNNER_LOCK,
    RUNNERS_FILE,
    TASK_DIR,
    dashscope_api_key,
    dashscope_text_model,
)
from ..storage import read_json_cached, read_json_file, read_text_file, safe_join, write_json_file
from .job_service import job_allows_auto_device, load_task_meta, normalize_device_strategy

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
# 与 midscene-upload.py 中 ``all_online_devices`` 保持一致：45s 内有心跳视为在线。
ONLINE_TIMEOUT_SECONDS = 45


# ---------------------------------------------------------------------------
# 注册表持久化
# ---------------------------------------------------------------------------

def load_runners() -> Dict[str, Dict[str, Any]]:
    """加载 runner 注册表。

    Returns:
        以 ``runner_id`` 为键的字典；若文件缺失或损坏，返回空字典。
    """
    data = read_json_file(RUNNERS_FILE, default={})
    return data if isinstance(data, dict) else {}


def save_runners(runners: Dict[str, Dict[str, Any]]) -> None:
    """原子化保存 runner 注册表到磁盘。"""
    write_json_file(RUNNERS_FILE, runners)


# ---------------------------------------------------------------------------
# 设备列表归一化（与 midscene-upload.py 中同名函数保持一致）
# ---------------------------------------------------------------------------

def normalize_device_list(devices: Optional[Iterable[Any]]) -> List[Dict[str, Any]]:
    """将 runner 上报的设备列表统一为标准结构。

    支持字符串数组与对象数组两种输入；缺少 ``device_id`` 的条目会被丢弃。
    """
    result: List[Dict[str, str]] = []
    for item in devices or []:
        if isinstance(item, str):
            device_id = item
            status = "online"
            meta: Dict[str, Any] = {}
        elif isinstance(item, dict):
            device_id = item.get("device_id") or item.get("deviceId") or item.get("id")
            status = item.get("status", "online")
            meta = item
        else:
            continue
        if not device_id:
            continue
        row: Dict[str, Any] = {
            "device_id": str(device_id),
            "status": status,
            "label": meta.get("label") or meta.get("model") or str(device_id),
            "brand": meta.get("brand", ""),
            "model": meta.get("model", ""),
        }
        for key in (
            "adb_path", "adbPath", "android_version", "androidVersion", "sdk",
            "resolution", "density", "installed_apps", "installedApps",
            "app_versions", "appVersions", "preflight", "preflight_status",
            "preflightStatus",
        ):
            if isinstance(meta, dict) and key in meta:
                row[key] = meta.get(key)
        result.append(row)
    return result


def runner_device_ids(runner: Dict[str, Any]) -> Set[str]:
    """返回 runner 当前 ``online`` 状态的设备 ID 集合。"""
    return {
        dev.get("device_id")
        for dev in runner.get("devices", [])
        if dev.get("status") == "online" and dev.get("device_id")
    }


def _is_runner_online(runner: Dict[str, Any], now: Optional[float] = None) -> bool:
    """通过 ``last_seen_ts`` 判断 runner 是否仍在线。"""
    now = time.time() if now is None else now
    last_seen_ts = runner.get("last_seen_ts") or 0
    try:
        return (now - float(last_seen_ts)) <= ONLINE_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Runner 注册 / 心跳
# ---------------------------------------------------------------------------

def _build_runner_record(runner_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """根据上报 payload 构造一条标准 runner 记录。"""
    devices = normalize_device_list(payload.get("devices") or [])
    now = time.time()
    return {
        "runner_id": runner_id,
        "devices": devices,
        "workspace": payload.get("workspace", ""),
        "hostname": payload.get("hostname", ""),
        "last_seen": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
        "last_seen_ts": now,
    }


def register_runner(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Runner 注册或心跳更新。

    Args:
        payload: 来自 runner 的请求体，需至少包含 ``runner_id``（兼容
            ``runnerId``）。其余字段：``devices``、``workspace``、``hostname``。

    Returns:
        持久化后的 runner 记录。
    """
    runner_id = payload.get("runner_id") or payload.get("runnerId") or "runner"
    record = _build_runner_record(runner_id, payload)
    with RUNNER_LOCK:
        runners = load_runners()
        runners[runner_id] = record
        save_runners(runners)
    return record


def runner_heartbeat(payload: Dict[str, Any]) -> Dict[str, Any]:
    """处理 runner 心跳包，更新在线状态与能力（设备）信息。

    与 :func:`register_runner` 同义；保留独立入口以便 HTTP 层语义清晰，并便于
    未来扩展（如轻量心跳只更新 ``last_seen_ts``）。
    """
    return register_runner(payload)


# ---------------------------------------------------------------------------
# 列表查询
# ---------------------------------------------------------------------------

def list_runners(include_online_flag: bool = True) -> Dict[str, Dict[str, Any]]:
    """返回所有 runner 的注册表副本。

    Args:
        include_online_flag: 若为 ``True``，为每个 runner 注入 ``online`` 字段。

    Returns:
        新字典；调用方可安全修改返回结果而不影响磁盘状态。
    """
    with RUNNER_LOCK:
        runners = load_runners()
    if not include_online_flag:
        return {k: dict(v) for k, v in runners.items()}
    now = time.time()
    snapshot: Dict[str, Dict[str, Any]] = {}
    for runner_id, runner in runners.items():
        row = dict(runner)
        row["online"] = _is_runner_online(runner, now=now)
        snapshot[runner_id] = row
    return snapshot


def get_online_runners() -> Dict[str, Dict[str, Any]]:
    """返回当前在线的 runner 字典。"""
    now = time.time()
    with RUNNER_LOCK:
        runners = load_runners()
    return {
        runner_id: dict(runner)
        for runner_id, runner in runners.items()
        if _is_runner_online(runner, now=now)
    }


def all_online_devices() -> List[Dict[str, Any]]:
    """汇总所有 runner 的设备列表，并标注 runner 在线状态。"""
    with RUNNER_LOCK:
        runners = load_runners()
    devices: List[Dict[str, Any]] = []
    now = time.time()
    for runner_id, runner in runners.items():
        online = _is_runner_online(runner, now=now)
        for dev in runner.get("devices", []):
            row = dict(dev)
            row["runner_id"] = runner_id
            row["runner_online"] = online
            row["last_seen"] = runner.get("last_seen", "")
            devices.append(row)
    return devices


# ---------------------------------------------------------------------------
# 任务分发
# ---------------------------------------------------------------------------

def _load_jobs() -> List[Dict[str, Any]]:
    """读取 jobs 文件；保持与 midscene-upload.py 的存储格式一致。"""
    data = read_json_cached(JOBS_FILE, default=[])
    if isinstance(data, list):
        return list(data)
    if isinstance(data, dict):
        jobs = data.get("jobs")
        return list(jobs) if isinstance(jobs, list) else []
    return []


def _save_jobs(jobs: List[Dict[str, Any]]) -> None:
    write_json_file(JOBS_FILE, jobs)


def assign_job(
    runner_id: str,
    extra_device_ids: Optional[Iterable[str]] = None,
) -> Optional[Dict[str, Any]]:
    """为指定 runner 选取并占用下一个待执行 job。

    选择规则（与 midscene-upload.py 中 ``/api/runner/jobs/next`` 保持一致）：

    - 仅考虑 ``status == "pending"`` 的 job。
    - 若 job 指定 ``target_runner_id``，需匹配 ``runner_id``。
    - 若 job 指定 ``device_id``，需在 runner 当前可用设备集合内。
    - 若 job 未指定设备，runner 必须有至少一台在线设备。

    Args:
        runner_id: 申请任务的 runner 标识。
        extra_device_ids: 通过查询参数额外声明的设备 id 集合，将与注册表中的
            在线设备合并使用。

    Returns:
        被占用的 job 字典副本（``status`` 已置为 ``running``）；若无可分配任务
        则返回 ``None``。 *不* 负责加载 YAML 内容 —— 由 HTTP 层处理。
    """
    extra: Set[str] = {d for d in (extra_device_ids or []) if d}
    with RUNNER_LOCK:
        runners = load_runners()
        runner_info = runners.get(runner_id, {})
    available_devices = runner_device_ids(runner_info) | extra

    selected: Optional[Dict[str, Any]] = None
    with JOB_LOCK:
        jobs = _load_jobs()
        for job in jobs:
            if job.get("status") != "pending":
                continue
            target_runner = job.get("target_runner_id") or ""
            target_device = job.get("device_id") or ""
            auto_device = job_allows_auto_device(job)
            if target_runner and target_runner != runner_id:
                continue
            if target_device and target_device not in available_devices:
                continue
            if not target_device and not auto_device:
                continue
            if not target_device and auto_device and not available_devices:
                continue
            selected = job
            break

        if selected is not None:
            selected["status"] = "running"
            selected["runner_id"] = runner_id
            if not selected.get("device_id") and job_allows_auto_device(selected) and available_devices:
                selected["device_id"] = sorted(available_devices)[0]
            selected["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            _save_jobs(jobs)
            return dict(selected)
    return None


# ---------------------------------------------------------------------------
# Runner 上报结果（精简版）
# ---------------------------------------------------------------------------

def update_runner_result(
    runner_id: str,
    job_id: str,
    result: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """将 runner 上报的 job 执行结果写回 jobs 表。

    完整的结果处理（写日志文件、保存截图、复检失败、自动修复等）依然保留在
    ``midscene-upload.py`` 的 ``/api/runner/jobs/<id>/result`` 处理流程中。本函数
    仅承担 jobs.json 状态更新这一最核心的步骤，便于其他模块在必要时调用，避免
    重复实现状态机。

    Args:
        runner_id: 上报结果的 runner 标识；会写入 job 的 ``runner_id`` 字段。
        job_id: 目标 job 的 id。
        result: 包含 ``status`` / ``stdout`` / ``stderr`` / ``report_url`` /
            ``progress`` 等字段的字典；缺省字段保留原有值。

    Returns:
        更新后的 job 字典副本；若未找到匹配 job 则返回 ``None``。
    """
    status = (result.get("status") or "").strip() or "failed"
    finished_at = time.strftime("%Y-%m-%d %H:%M:%S")

    with JOB_LOCK:
        jobs = _load_jobs()
        target: Optional[Dict[str, Any]] = None
        for job in jobs:
            if job.get("job_id") == job_id:
                target = job
                break
        if target is None:
            return None
        target["status"] = status
        target["runner_id"] = runner_id or target.get("runner_id", "")
        target["finished_at"] = finished_at
        if "progress" in result:
            try:
                target["progress"] = int(result["progress"])
            except (TypeError, ValueError):
                pass
        elif status == "success":
            target["progress"] = 100
        for key_in, key_out in (
            ("stdout", "stdout_tail"),
            ("stderr", "stderr_tail"),
        ):
            value = result.get(key_in)
            if isinstance(value, str):
                target[key_out] = value[-2000:]
        for key in (
            "report_url",
            "local_report_path",
            "report_upload_error",
            "report_upload_pending",
            "report_missing_reason",
            "upload_warning",
            "device_id",
        ):
            if key in result:
                target[key] = result[key]
        _save_jobs(jobs)
        return dict(target)


__all__ = [
    "ONLINE_TIMEOUT_SECONDS",
    "all_online_devices",
    "annotate_job_queue_state",
    "assign_job",
    "get_available_runner",
    "get_online_runners",
    "list_runners",
    "load_runners",
    "normalize_device_list",
    "register_runner",
    "runner_device_ids",
    "runner_heartbeat",
    "runner_summary",
    "save_runners",
    "update_runner_result",
]


# ---------------------------------------------------------------------------
# Runner 查找
# ---------------------------------------------------------------------------

def get_available_runner(
    platform: Optional[str] = None,
    device_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """根据平台 / 设备查找可用 runner。

    遍历当前在线 runner，返回第一个满足条件的 runner 记录。
    优先匹配 ``device_id``；若只指定 ``platform``，则匹配 runner
    的 ``workspace`` 字段包含对应平台标识（android / ios）的 runner；
    均不指定时返回任意一台在线 runner。

    Args:
        platform: 目标平台（如 ``"android"`` / ``"ios"``）。
        device_id: 目标设备 ID。

    Returns:
        匹配的 runner 字典副本；无匹配时返回 ``None``。
    """
    online = get_online_runners()
    if not online:
        return None

    # 精确匹配 device_id
    if device_id:
        device_id = str(device_id).strip()
        for runner_id, runner in online.items():
            for dev in runner.get("devices", []):
                if str(dev.get("device_id") or "") == device_id:
                    return dict(runner)

    # 匹配 platform
    if platform:
        platform = str(platform).strip().lower()
        for runner_id, runner in online.items():
            workspace = str(runner.get("workspace", "")).lower()
            # workspace 可能包含 "android" / "ios" 等平台标识
            if platform in workspace:
                return dict(runner)
            # 也检查设备的 model / brand 中是否包含平台标识
            for dev in runner.get("devices", []):
                dev_label = str(dev.get("label") or dev.get("model") or "").lower()
                if platform in dev_label:
                    return dict(runner)

    # 兜底：返回任意一台在线 runner（优先选设备最多的）
    best = None
    best_count = -1
    for runner_id, runner in online.items():
        dev_count = len(runner.get("devices", []))
        if dev_count > best_count:
            best_count = dev_count
            best = runner
    return dict(best) if best else None


# ---------------------------------------------------------------------------
# runner_summary — 源自 midscene-upload.py L4973
# ---------------------------------------------------------------------------

def runner_summary() -> Dict[str, Any]:
    """返回 Runner 汇总信息。

    源自 midscene-upload.py L4973 的 ``runner_summary`` 函数。
    包含在线/离线 Runner 统计、设备列表及在线设备计数。

    Returns:
        包含 ``total`` / ``online`` / ``devices`` / ``online_devices`` 的字典。
    """
    with RUNNER_LOCK:
        runners = load_runners()
    now = time.time()
    online = 0
    devices: List[Dict[str, Any]] = []
    for runner_id, runner in runners.items():
        is_online = _is_runner_online(runner, now=now)
        if is_online:
            online += 1
        for device in normalize_device_list(runner.get("devices", [])):
            devices.append({
                **device,
                "runner_id": runner_id,
                "runner_online": is_online,
            })
    return {
        "total": len(runners),
        "online": online,
        "devices": devices,
        "online_devices": len([
            d for d in devices
            if d.get("runner_online") and d.get("status") in ("online", "device")
        ]),
    }


# ---------------------------------------------------------------------------
# annotate_job_queue_state — 源自 midscene-upload.py L8221
# ---------------------------------------------------------------------------

def annotate_job_queue_state(
    job: Dict[str, Any],
    runners: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """为 pending 状态的 job 标注队列等待原因。

    源自 midscene-upload.py L8221 的 ``annotate_job_queue_state`` 函数。
    根据 job 的 ``target_runner_id`` / ``device_id`` 和当前 Runner 在线状态，
    推断 job 仍在队列中的原因，写入 ``queue_message`` 字段。

    Args:
        job: job 字典。
        runners: 可选的 runner 注册表（不传则从磁盘读取）。

    Returns:
        带有 ``queue_message`` 字段的 job 副本（仅 pending 状态才会标注）。
    """
    row = dict(job)
    if row.get("status") != "pending":
        return row
    with RUNNER_LOCK:
        runners = runners if runners is not None else load_runners()
    now = time.time()
    target_runner = row.get("target_runner_id") or ""
    target_device = row.get("device_id") or ""
    device_strategy = normalize_device_strategy(
        row.get("device_strategy") or row.get("deviceStrategy"),
        device_id=target_device,
        runner_id=target_runner,
    )

    online_runners = {
        runner_id: runner
        for runner_id, runner in runners.items()
        if _is_runner_online(runner, now=now)
    }

    if target_runner:
        runner = online_runners.get(target_runner)
        if not runner:
            row["queue_message"] = f"等待 Runner 在线：{target_runner}"
            return row
        devices = runner_device_ids(runner)
        if target_device and target_device not in devices:
            row["queue_message"] = f"等待目标设备在线：{target_device}"
            return row
        row["queue_message"] = f"等待 Runner 拉取任务：{target_runner}"
        return row

    if target_device:
        for runner in online_runners.values():
            if target_device in runner_device_ids(runner):
                row["queue_message"] = f"等待可用 Runner 拉取设备任务：{target_device}"
                return row
        row["queue_message"] = f"等待任一 Runner 上报目标设备：{target_device}"
        return row

    if device_strategy != "auto":
        row["queue_message"] = "等待选择执行设备；如需平台分配，请明确选择“自动选择在线设备”"
    elif not online_runners:
        row["queue_message"] = "等待 Runner 在线"
    else:
        row["queue_message"] = "已允许自动选择在线设备，等待任一在线 Runner 拉取任务"
    return row




# ---------------------------------------------------------------------------
# Migrated from midscene-upload.py
# ---------------------------------------------------------------------------

def platform_preflight_dashboard(include_sonic_scan=False):
    checks = []

    def add_check(key, title, ok, status, detail="", action=""):
        checks.append({
            "key": key,
            "title": title,
            "ok": bool(ok),
            "status": status,
            "detail": detail,
            "action": action,
        })

    add_check("task_service", "Task 服务", True, "normal", f"端口 {PORT}，服务在线")
    dashscope_key_ok = bool(dashscope_api_key(required=False))
    add_check("dashscope", "模型配置", dashscope_key_ok, "normal" if dashscope_key_ok else "error", dashscope_text_model())
    sonic_ok = False
    sonic_detail = ""
    project_count = 0
    from .sonic_service import sonic_auth_preview, sonic_base_url, sonic_list_projects, sonic_probe_token, sonic_scan_midscene_cases, sonic_token
    if sonic_token():
        token_probe = sonic_probe_token()
        if token_probe.get("ok"):
            try:
                projects = sonic_list_projects()
                project_count = len(projects)
            except Exception:
                project_count = 0
            sonic_ok = True
            sonic_detail = f"{sonic_base_url()}，项目 {project_count} 个"
        else:
            auth = sonic_auth_preview()
            sonic_detail = token_probe.get("error") or token_probe.get("message") or "Sonic token 未通过鉴权"
            if auth.get("login_configured") and auth.get("login_error"):
                sonic_detail += f"；自动登录失败：{auth['login_error']}"
    else:
        auth = sonic_auth_preview()
        sonic_detail = "未配置 Sonic 自动登录凭据或可用 Token"
        if auth.get("login_configured") and auth.get("login_error"):
            sonic_detail = f"自动登录失败：{auth['login_error']}"
    add_check("sonic", "Sonic 连接", sonic_ok, "normal" if sonic_ok else "error", sonic_detail, "配置 SONIC_BASE_URL / SONIC_USERNAME / SONIC_PASSWORD")

    runners = runner_summary()
    add_check("runner", "Runner", runners["online"] > 0, "normal" if runners["online"] > 0 else "warn", f"在线 Runner {runners['online']}/{runners['total']}，在线设备 {runners['online_devices']} 台", "启动 Windows/Mac Runner")

    bridge_path = os.getenv("SONIC_BRIDGE_GROOVY_PATH", "/opt/sonic-midscene-task-runner.groovy")
    bridge_exists = bool(read_text_file(bridge_path, "") or read_text_file(os.path.join(os.getcwd(), "sonic-midscene-task-runner.groovy"), ""))
    add_check("bridge", "Sonic 桥接脚本", bridge_exists, "normal" if bridge_exists else "error", bridge_path if bridge_exists else "未找到 Groovy 桥接脚本", "部署 sonic-midscene-task-runner.groovy")

    assets = task_asset_summary()
    add_check("assets", "用例资产", assets["files"] > 0, "normal" if assets["files"] > 0 else "warn", f"{assets['modules']} 个模块，{assets['files']} 个 YAML")

    legacy = {"total": 0, "migratable": 0, "manual": 0, "error": ""}
    if include_sonic_scan and sonic_ok:
        try:
            rows = sonic_scan_midscene_cases()
            legacy = {
                "total": len(rows),
                "migratable": len([row for row in rows if row.get("action") == "migrate"]),
                "manual": len([row for row in rows if row.get("action") == "manual"]),
                "error": "",
            }
        except Exception as e:
            legacy["error"] = str(e)

    if include_sonic_scan:
        add_check(
            "legacy",
            "旧/重复 Sonic 脚本",
            legacy["total"] == 0,
            "normal" if legacy["total"] == 0 else "warn",
            legacy.get("error") or f"待清理 {legacy['total']} 条，可自动处理 {legacy['migratable']} 条，需要确认 {legacy['manual']} 条",
            "扫描并清理旧/重复脚本"
        )

    return {
        "ok": all(item["ok"] for item in checks if item["key"] in ("task_service", "dashscope", "sonic", "bridge")),
        "checks": checks,
        "sonic": {
            "base_url": sonic_base_url(),
            "token_configured": bool(sonic_token()),
            "project_count": project_count,
        },
        "runners": runners,
        "assets": assets,
        "legacy": legacy,
    }



def runtime_env_preview(env):
    preview = dict(env or {})
    for key in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY"):
        if preview.get(key):
            value = str(preview[key])
            preview[key] = f"{value[:6]}...{value[-4:]} len={len(value)}"
    return preview



def midscene_runtime_env():
    api_key = dashscope_api_key(required=False)
    base_url = (os.getenv("DASHSCOPE_BASE_URL") or os.getenv("OPENAI_BASE_URL") or DEFAULT_DASHSCOPE_BASE_URL).strip()
    text_model = (os.getenv("DASHSCOPE_MODEL") or DEFAULT_TEXT_MODEL).strip()
    vl_model = (os.getenv("DASHSCOPE_VL_MODEL") or os.getenv("MIDSCENE_MODEL_NAME") or DEFAULT_VL_MODEL).strip()
    app_package = (os.getenv("APP_PACKAGE") or "").strip()
    env = {
        "DASHSCOPE_API_KEY": api_key,
        "OPENAI_API_KEY": api_key,
        "DASHSCOPE_BASE_URL": base_url,
        "OPENAI_BASE_URL": base_url,
        "DASHSCOPE_MODEL": text_model,
        "DASHSCOPE_VL_MODEL": vl_model,
        "MIDSCENE_MODEL_NAME": vl_model,
        "MIDSCENE_USE_QWEN_VL": "1",
        "MIDSCENE_SKIP_CONFIG_CHECK": "1",
        "MIDSCENE_REPLANNING_CYCLE_LIMIT": DEFAULT_REPLANNING_CYCLE_LIMIT,
        "NODE_TLS_REJECT_UNAUTHORIZED": "0",
        "APP_PACKAGE": app_package,
    }
    return {key: value for key, value in env.items() if value}



def public_report_url(filename):
    return f"http://101.34.197.12:8088/reports/{urllib.parse.quote(filename)}"



def task_asset_summary():
    modules = {}
    total_files = 0
    if os.path.exists(TASK_DIR):
        for mod in sorted(os.listdir(TASK_DIR)):
            module_dir = safe_join(TASK_DIR, mod)
            if not os.path.isdir(module_dir):
                continue
            files = [f for f in os.listdir(module_dir) if f.endswith((".yaml", ".yml"))]
            modules[mod] = len(files)
            total_files += len(files)
    meta = load_task_meta()
    statuses = {}
    for row in meta.values():
        status = row.get("status") or "draft"
        statuses[status] = statuses.get(status, 0) + 1
    return {
        "modules": len(modules),
        "files": total_files,
        "by_module": modules,
        "statuses": statuses,
    }



def parse_time(value):
    if not value:
        return 0
    try:
        return time.mktime(time.strptime(value, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0



def dedupe_keep_order(items):
    result = []
    seen = set()
    for item in items or []:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result



def extract_page_items(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("records", "content", "list", "items"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    nested = data.get("data")
    if isinstance(nested, list):
        return nested
    if isinstance(nested, dict):
        return extract_page_items(nested)
    return []
