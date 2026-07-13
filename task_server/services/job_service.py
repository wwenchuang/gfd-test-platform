"""Job 管理服务。

从 midscene-upload.py 抽取并重构 Job 持久化与状态相关逻辑：

- 使用 read_json_cached 加速读，write_json_file 原子写并失效缓存。
- load_jobs 默认按创建时间倒序仅返回最近 50 条摘要，避免全量加载性能问题。
- 所有写操作使用 JOB_LOCK 保证线程安全。
- 字段命名兼容旧记录：job_id, target_task_name, current_task_name,
  report_url, failure_review 等同时支持驼峰别名。

本模块只负责 Job 记录的 CRUD/标准化，不引入 HTTP/AI 相关依赖。
"""

from __future__ import annotations

import os
import re
import shutil
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from ..config import (
    AGENT_RUN_LOCK,
    AGENT_RUNS_FILE,
    JOB_LOCK,
    JOB_TIMEOUT_SECONDS,
    JOBS_FILE,
    TASK_APPS_FILE,
    TASK_DIR,
    TASK_META_FILE,
    safe_bool,
)
from ..schemas import (
    ALL_JOB_STATUSES,
    JOB_STATUS_CANCELLED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JOB_STATUS_SUCCESS,
    JOB_STATUS_TIMEOUT,
    TERMINAL_JOB_STATUSES,
)
from ..storage import (
    clean_filename,
    read_json_cached,
    read_json_file,
    safe_join,
    unique_millis_id,
    write_json_file,
)
from .feishu_service import validate_feishu_webhook

# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

_TIME_FMT = "%Y-%m-%d %H:%M:%S"
DEVICE_STRATEGY_FIXED = "fixed"
DEVICE_STRATEGY_AUTO = "auto"
DEVICE_STRATEGY_MANUAL_REQUIRED = "manual_required"


def _trace_time_text() -> str:
    return time.strftime(_TIME_FMT)


def normalize_device_strategy(value: Any = "", device_id: str = "", runner_id: str = "") -> str:
    """标准化执行设备策略。

    auto 必须由调用方显式传入；只传了设备或 Runner 时视为固定选择。
    空选择保持 manual_required，避免后端偷偷把任务分配给第一台在线设备。
    """
    raw = str(value or "").strip().lower()
    if raw in {"auto", "automatic", "any", "runner_auto"}:
        return DEVICE_STRATEGY_AUTO
    if raw in {"fixed", "manual", "device", "selected"}:
        return DEVICE_STRATEGY_FIXED
    if str(device_id or "").strip() or str(runner_id or "").strip():
        return DEVICE_STRATEGY_FIXED
    return DEVICE_STRATEGY_MANUAL_REQUIRED


def job_allows_auto_device(job: Dict[str, Any]) -> bool:
    """是否允许 Runner 在拉取任务时自动选择在线设备。"""
    return normalize_device_strategy(job.get("device_strategy") or job.get("deviceStrategy")) == DEVICE_STRATEGY_AUTO


def _now_str() -> str:
    return time.strftime(_TIME_FMT)


def _parse_time(value: Optional[str]) -> float:
    if not value:
        return 0.0
    try:
        return time.mktime(time.strptime(value, _TIME_FMT))
    except Exception:
        return 0.0


def _read_jobs_raw() -> List[Dict[str, Any]]:
    """直接从存储读取 jobs 列表，使用 TTL 缓存。"""
    data = read_json_cached(JOBS_FILE, default=[])
    if not isinstance(data, list):
        return []
    # 过滤掉非 dict 元素，避免脏数据导致下游崩溃
    return [item for item in data if isinstance(item, dict)]


def _new_job_id() -> str:
    return unique_millis_id("job")


# ---------------------------------------------------------------------------
# 状态 / 记录标准化
# ---------------------------------------------------------------------------

# 旧值 -> 标准状态的别名映射
_STATUS_ALIASES = {
    "passed": JOB_STATUS_SUCCESS,
    "pass": JOB_STATUS_SUCCESS,
    "ok": JOB_STATUS_SUCCESS,
    "succeeded": JOB_STATUS_SUCCESS,
    "succes": JOB_STATUS_SUCCESS,
    "fail": JOB_STATUS_FAILED,
    "error": JOB_STATUS_FAILED,
    "errored": JOB_STATUS_FAILED,
    "cancel": JOB_STATUS_CANCELLED,
    "canceled": JOB_STATUS_CANCELLED,
    "timeout": JOB_STATUS_TIMEOUT,
    "timed_out": JOB_STATUS_TIMEOUT,
    "queued": JOB_STATUS_PENDING,
    "waiting": JOB_STATUS_PENDING,
    "in_progress": JOB_STATUS_RUNNING,
    "started": JOB_STATUS_RUNNING,
}


def normalize_job_status(status: Any) -> str:
    """标准化 job 状态字符串。

    保留 schemas 中的合法状态名，未知值统一回退到 ``failed``。
    """
    text = str(status or "").strip().lower()
    if not text:
        return JOB_STATUS_FAILED
    if text in ALL_JOB_STATUSES:
        return text
    if text in _STATUS_ALIASES:
        return _STATUS_ALIASES[text]
    return JOB_STATUS_FAILED


def normalize_job_record(job: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """标准化 job 记录，确保关键字段存在并补齐驼峰别名。

    规则：
    - 同时填充 snake_case 与 camelCase 两套字段（如 job_id/jobId）。
    - 未知/缺失的状态会被 :func:`normalize_job_status` 统一化。
    - 不主动加载 repair_draft（避免循环依赖），由调用方按需注入。
    """
    job = dict(job or {})

    job_id = str(
        job.get("job_id") or job.get("jobId") or job.get("id") or ""
    ).strip()
    run_id = job.get("run_id") or job.get("runId") or job_id
    trace_id = job.get("trace_id") or job.get("traceId") or ""
    status = normalize_job_status(job.get("status") or job.get("state"))

    report_url = (
        job.get("report_url")
        or job.get("reportUrl")
        or job.get("sonic_report_url")
        or ""
    )
    failure_review = job.get("failure_review") or job.get("failureReview") or {}
    repair_draft = job.get("repair_draft") or job.get("repairDraft") or {}
    target_task_name = (
        job.get("target_task_name")
        or job.get("targetTaskName")
        or ""
    )
    current_task_name = (
        job.get("current_task_name")
        or job.get("currentTaskName")
        or ""
    )
    task_name_display = (
        target_task_name
        or current_task_name
        or job.get("task_name")
        or job.get("file")
        or ""
    )
    recent_error = (
        job.get("error")
        or job.get("message")
        or job.get("error_message")
        or job.get("recentError")
        or ""
    )
    device_id = str(job.get("device_id") or job.get("deviceId") or "").strip()
    target_runner_id = str(
        job.get("target_runner_id")
        or job.get("targetRunnerId")
        or job.get("runner_id")
        or job.get("runnerId")
        or ""
    ).strip()
    device_strategy = normalize_device_strategy(
        job.get("device_strategy") or job.get("deviceStrategy"),
        device_id=device_id,
        runner_id=target_runner_id,
    )

    job.update({
        "job_id": job_id,
        "jobId": job_id,
        "run_id": run_id,
        "runId": run_id,
        "trace_id": trace_id,
        "traceId": trace_id,
        "status": status,
        "standardStatus": status,
        "currentStep": (
            job.get("currentStep")
            or job.get("step")
            or job.get("current_step")
            or ""
        ),
        "target_task_name": target_task_name,
        "targetTaskName": target_task_name,
        "current_task_name": current_task_name,
        "currentTaskName": current_task_name,
        "taskName": task_name_display,
        "report_url": report_url,
        "reportUrl": report_url,
        "failure_review": failure_review,
        "failureReview": failure_review,
        "repair_draft": repair_draft,
        "repairDraft": repair_draft,
        "recentError": recent_error,
        "device_id": device_id,
        "deviceId": device_id,
        "target_runner_id": target_runner_id,
        "targetRunnerId": target_runner_id,
        "device_strategy": device_strategy,
        "deviceStrategy": device_strategy,
    })
    return job


# ---------------------------------------------------------------------------
# 加载 / 保存
# ---------------------------------------------------------------------------

def load_jobs(limit: Optional[int] = 50, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """加载 jobs 列表。

    Args:
        limit: 最多返回多少条，按 ``created_at`` 倒序截断。``None`` 表示不限制。
        status: 仅返回该状态的 job；接受任意别名，会调用 ``normalize_job_status``。

    Returns:
        list[dict]: 已经过 :func:`normalize_job_record` 标准化的 job 列表。
    """
    jobs = _read_jobs_raw()
    if not jobs:
        return []

    if status:
        target_status = normalize_job_status(status)
        jobs = [j for j in jobs if normalize_job_status(j.get("status")) == target_status]

    # 按创建时间倒序；缺失 created_at 视为最旧
    jobs.sort(key=lambda j: _parse_time(j.get("created_at")), reverse=True)

    if limit is not None and limit > 0:
        jobs = jobs[: int(limit)]

    return [normalize_job_record(j) for j in jobs]


def save_jobs(jobs: Iterable[Dict[str, Any]]) -> None:
    """保存 jobs 列表到文件（原子写 + 缓存失效）。

    调用方需自行持有 ``JOB_LOCK`` 以避免并发覆盖。
    """
    payload = list(jobs or [])
    write_json_file(JOBS_FILE, payload)


# ---------------------------------------------------------------------------
# 单条 Job CRUD
# ---------------------------------------------------------------------------

def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    """获取单个 job 详情。

    返回经过标准化的副本，找不到时返回 ``None``。
    """
    job_id = str(job_id or "").strip()
    if not job_id:
        return None
    for job in _read_jobs_raw():
        if str(job.get("job_id") or job.get("jobId") or job.get("id") or "").strip() == job_id:
            return normalize_job_record(job)
    return None


def update_job(job_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """合并更新 job 字段。

    Args:
        job_id: 目标 job 标识。
        patch: 需要写入的字段；``None`` 值字段会被忽略。

    Returns:
        更新后的标准化 job，未找到时返回 ``None``。
    """
    job_id = str(job_id or "").strip()
    if not job_id or not isinstance(patch, dict):
        return None

    cleaned = {k: v for k, v in patch.items() if v is not None}

    with JOB_LOCK:
        jobs = _read_jobs_raw()
        target_index = -1
        for index, job in enumerate(jobs):
            if str(job.get("job_id") or job.get("jobId") or job.get("id") or "").strip() == job_id:
                target_index = index
                break
        if target_index < 0:
            return None

        job = dict(jobs[target_index])
        job.update(cleaned)
        if "status" in cleaned:
            job["status"] = normalize_job_status(cleaned["status"])
            if job["status"] in TERMINAL_JOB_STATUSES and not job.get("finished_at"):
                job["finished_at"] = _now_str()
        job["updated_at"] = _now_str()
        jobs[target_index] = job
        save_jobs(jobs)

    return normalize_job_record(job)


def create_job(payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """创建一条新的 job 记录。

    ``payload`` 可包含 ``module``/``file``/``target_task_name`` 等字段；缺省字段会
    填默认值。调用方仅需给出必要业务参数，其余由本函数补全。
    """
    payload = dict(payload or {})

    job_id = str(payload.get("job_id") or payload.get("jobId") or "").strip() or _new_job_id()
    status = normalize_job_status(payload.get("status") or JOB_STATUS_PENDING)
    created_at = payload.get("created_at") or _now_str()

    target_task_name = (
        payload.get("target_task_name")
        or payload.get("targetTaskName")
        or ""
    )
    task_names = payload.get("task_names") or []
    if isinstance(task_names, str):
        task_names = [task_names]
    task_names = [str(name) for name in task_names if str(name).strip()]
    if target_task_name and not task_names:
        task_names = [target_task_name]

    job: Dict[str, Any] = {
        "job_id": job_id,
        "module": payload.get("module") or "",
        "file": payload.get("file") or "",
        "status": status,
        "created_at": created_at,
        "attempt": int(payload.get("attempt") or 1),
        "max_attempt": int(payload.get("max_attempt") or 2),
        "auto_optimize": bool(payload.get("auto_optimize") or False),
        "run_mode": payload.get("run_mode") or "test",
        "parent_job_id": payload.get("parent_job_id") or "",
        "device_id": payload.get("device_id") or "",
        "target_runner_id": payload.get("target_runner_id") or payload.get("runner_id") or "",
        "device_strategy": normalize_device_strategy(
            payload.get("device_strategy") or payload.get("deviceStrategy"),
            device_id=payload.get("device_id") or "",
            runner_id=payload.get("target_runner_id") or payload.get("runner_id") or "",
        ),
        "target_task_name": target_task_name,
        "current_task_name": payload.get("current_task_name") or (task_names[0] if task_names else ""),
        "current_task_index": int(payload.get("current_task_index") or 0),
        "completed_task_count": int(payload.get("completed_task_count") or 0),
        "total_task_count": int(payload.get("total_task_count") or len(task_names)),
        "task_names": task_names[:100],
        "progress": int(payload.get("progress") or 0),
        "events": [],
    }

    # 透传调用方提供的额外字段（如 trace_id/run_id/extra metadata）
    for key, value in payload.items():
        if key in job or value is None:
            continue
        job[key] = value

    with JOB_LOCK:
        jobs = _read_jobs_raw()
        jobs.append(job)
        save_jobs(jobs)

    return normalize_job_record(job)


def append_job_event(job_id: str, event: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """向 job 追加事件记录。

    事件至少包含 ``ts`` 与 ``type`` 字段；调用方传入的字段会原样保留。
    返回更新后的标准化 job；找不到 job 时返回 ``None``。
    """
    job_id = str(job_id or "").strip()
    if not job_id:
        return None

    record = dict(event or {})
    record.setdefault("ts", _now_str())
    record.setdefault("type", record.get("event") or "log")

    with JOB_LOCK:
        jobs = _read_jobs_raw()
        target_index = -1
        for index, job in enumerate(jobs):
            if str(job.get("job_id") or job.get("jobId") or job.get("id") or "").strip() == job_id:
                target_index = index
                break
        if target_index < 0:
            return None

        job = dict(jobs[target_index])
        events = job.get("events")
        if not isinstance(events, list):
            events = []
        events.append(record)
        # 防止事件无限增长，保留最近 500 条
        if len(events) > 500:
            events = events[-500:]
        job["events"] = events
        job["updated_at"] = _now_str()
        jobs[target_index] = job
        save_jobs(jobs)

    return normalize_job_record(job)


# ---------------------------------------------------------------------------
# 超时检查
# ---------------------------------------------------------------------------

def check_job_timeout(job: Dict[str, Any]) -> bool:
    """检查 job 是否超时。

    超时定义：状态为 ``running`` 且自 ``started_at``（或 ``created_at``）以来
    超过 :data:`JOB_TIMEOUT_SECONDS` 秒。返回 ``True`` 表示已超时。
    """
    if not isinstance(job, dict):
        return False
    if normalize_job_status(job.get("status")) != JOB_STATUS_RUNNING:
        return False

    started = _parse_time(job.get("started_at")) or _parse_time(job.get("created_at"))
    if not started:
        return False
    return (time.time() - started) > JOB_TIMEOUT_SECONDS


__all__ = [
    "load_jobs",
    "save_jobs",
    "get_job",
    "update_job",
    "create_job",
    "create_pending_job",
    "append_job_event",
    "normalize_device_strategy",
    "job_allows_auto_device",
    "normalize_job_record",
    "normalize_job_status",
    "check_job_timeout",
    "mark_job_status",
    "attach_report",
    "attach_failure_review",
    "attach_repair_draft",
    "recover_timed_out_jobs",
    "find_job",
    "runner_job_wait_timeout_seconds",
    "wait_jobs_finished",
]


# ---------------------------------------------------------------------------
# 便捷快捷方法
# ---------------------------------------------------------------------------

def mark_job_status(job_id: str, status: str, error: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """标记 job 状态的快捷方法。

    等价于 ``update_job(job_id, {'status': status, 'error': error})``，
    但自动跳过 ``None`` 值的 error 字段。

    当状态变为终态（SUCCESS / FAILED）时，自动调用
    ``knowledge_service.record_execution_result`` 记录用例执行历史，
    知识记录失败不影响主流程。

    Args:
        job_id: 目标 job 标识。
        status: 目标状态（会经 :func:`normalize_job_status` 标准化）。
        error: 可选的错误信息。

    Returns:
        更新后的标准化 job，未找到时返回 ``None``。
    """
    patch: Dict[str, Any] = {"status": status}
    if error is not None:
        patch["error"] = error
    result = update_job(job_id, patch)

    # 知识记录钩子：终态时自动记录执行历史
    normalized_status = normalize_job_status(status)
    if result and normalized_status in TERMINAL_JOB_STATUSES:
        try:
            from task_server.services import knowledge_service
            knowledge_service.record_execution_result(
                yaml_file=result.get("file") or result.get("target_task_name") or result.get("targetTaskName") or "",
                module=result.get("module") or "",
                job_id=job_id,
                status=normalized_status,
                duration_ms=result.get("durationMs") or result.get("duration_ms") or 0,
                error=error,
            )
        except Exception:
            pass  # 知识记录失败不影响主流程

    return result


def attach_report(job_id: str, report: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """关联报告到 job。

    将报告摘要信息写入 job 记录的 ``report`` 字段，同时更新
    ``report_url``（若报告中包含）。

    Args:
        job_id: 目标 job 标识。
        report: 报告字典，应包含 ``report_url`` / ``status`` / ``summary`` 等。

    Returns:
        更新后的标准化 job，未找到时返回 ``None``。
    """
    if not isinstance(report, dict):
        return None
    patch: Dict[str, Any] = {"report": report}
    report_url = report.get("report_url") or report.get("reportUrl") or ""
    if report_url:
        patch["report_url"] = report_url
        patch["reportUrl"] = report_url
    return update_job(job_id, patch)


def attach_failure_review(job_id: str, review: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """关联失败分析到 job。

    将分析结果写入 job 记录的 ``failure_review`` 字段。

    Args:
        job_id: 目标 job 标识。
        review: 失败分析字典，通常包含 ``failureType`` / ``summary`` / ``suggestion``。

    Returns:
        更新后的标准化 job，未找到时返回 ``None``。
    """
    if not isinstance(review, dict):
        return None
    return update_job(job_id, {
        "failure_review": review,
        "failureReview": review,
    })


def attach_repair_draft(job_id: str, draft: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """关联修复草稿到 job。

    将修复草稿摘要写入 job 记录的 ``repair_draft`` 字段。
    注意：草稿本身由 :mod:`task_server.services.repair_service` 独立持久化，
    此处仅存储引用/摘要，避免数据冗余。

    Args:
        job_id: 目标 job 标识。
        draft: 修复草稿字典，通常包含 ``draftId`` / ``status`` / ``riskLevel``。

    Returns:
        更新后的标准化 job，未找到时返回 ``None``。
    """
    if not isinstance(draft, dict):
        return None
    # 只保留摘要字段，避免大体积 YAML 内容在 job 列表中重复存储
    summary = {
        k: draft.get(k)
        for k in ("draftId", "draft_id", "status", "failureType", "riskLevel", "riskHits")
        if draft.get(k) is not None
    }
    return update_job(job_id, {
        "repair_draft": summary,
        "repairDraft": summary,
    })


# ---------------------------------------------------------------------------
# Task Meta 辅助（源自 midscene-upload.py）
# ---------------------------------------------------------------------------

def _task_key(module: str, file: str) -> str:
    """生成 task 唯一键。"""
    from ..storage import clean_filename
    return f"{module}::{clean_filename(file)}"


def _load_task_meta() -> Dict[str, Any]:
    """加载 task-meta 元数据。"""
    data = read_json_cached(TASK_META_FILE, default={})
    return data if isinstance(data, dict) else {}


def _save_task_meta(data: Dict[str, Any]) -> None:
    """保存 task-meta 元数据。"""
    write_json_file(TASK_META_FILE, data)


def _update_task_meta(module: str, file: str, patch: Dict[str, Any]) -> Dict[str, Any]:
    """更新 task 元数据。"""
    from ..storage import clean_filename
    data = _load_task_meta()
    key = _task_key(module, file)
    row = data.get(key, {"module": module, "file": clean_filename(file), "status": "draft"})
    row.update({k: v for k, v in patch.items() if v is not None})
    row["module"] = module
    row["file"] = clean_filename(file)
    row["updated_at"] = _now_str()
    data[key] = row
    _save_task_meta(data)
    return row


# ---------------------------------------------------------------------------
# create_pending_job — 源自 midscene-upload.py L8271
# ---------------------------------------------------------------------------

def _yaml_task_names_local(yaml_text: str) -> List[str]:
    """从 YAML 内容提取所有 task name。

    使用正则解析，避免 import yaml_service 产生循环依赖。
    与 yaml_service.yaml_task_names 逻辑完全一致。
    """
    names: List[str] = []
    name_re = re.compile(r"^\s*-\s+name:\s*(.+?)\s*$")
    for line in (yaml_text or "").splitlines():
        m = name_re.match(line)
        if m:
            value = m.group(1).strip()
            if (value.startswith('"') and value.endswith('"')) or (
                value.startswith("'") and value.endswith("'")
            ):
                value = value[1:-1]
            value = value.replace('\"', '"').strip()
            names.append(value)
    return names


def create_pending_job(
    module: str,
    file: str,
    auto_optimize: bool = False,
    max_attempt: int = 2,
    attempt: int = 1,
    parent_job_id: str = "",
    device_id: str = "",
    runner_id: str = "",
    device_strategy: str = "",
    run_mode: str = "test",
    target_task_name: str = "",
    parent_run_id: str = "",
) -> Dict[str, Any]:
    """创建一个 pending 状态的 Job，并读取 YAML 解析 task_names。

    源自 midscene-upload.py L8271 的 ``create_pending_job`` 函数。
    额外会更新 task-meta 元数据中的 last_job_id / last_status 等字段。

    Args:
        module: 模块目录名。
        file: YAML 文件名。
        auto_optimize: 是否自动优化。
        max_attempt: 最大重试次数。
        attempt: 当前尝试序号。
        parent_job_id: 父 Job ID（子任务场景）。
        device_id: 目标设备 ID。
        runner_id: 目标 Runner ID。
        device_strategy: 设备选择策略；auto 表示用户明确允许平台选择在线设备。
        run_mode: 运行模式（test / real）。
        target_task_name: 指定执行的单个 task 名称。
        parent_run_id: 创建该任务的 Agent Run ID，用于生命周期级联管理。

    Returns:
        新创建的 job 字典。
    """
    task_names: List[str] = []
    try:
        yaml_path = safe_join(TASK_DIR, module, file)
        with open(yaml_path, encoding="utf-8") as f:
            task_names = _yaml_task_names_local(f.read())
    except Exception:
        task_names = []
    if target_task_name:
        task_names = [target_task_name]

    job: Dict[str, Any] = {
        "job_id": _new_job_id(),
        "module": module,
        "file": file,
        "status": JOB_STATUS_PENDING,
        "created_at": _now_str(),
        "attempt": attempt,
        "auto_optimize": safe_bool(auto_optimize),
        "run_mode": run_mode or "test",
        "max_attempt": max_attempt,
        "parent_job_id": parent_job_id,
        "parent_run_id": parent_run_id,
        "device_id": device_id,
        "target_runner_id": runner_id,
        "device_strategy": normalize_device_strategy(device_strategy, device_id=device_id, runner_id=runner_id),
        "target_task_name": target_task_name or "",
        "progress": 0,
        "current_task_name": task_names[0] if task_names else "",
        "current_task_index": 0,
        "completed_task_count": 0,
        "total_task_count": len(task_names),
        "task_names": task_names[:100],
    }
    with JOB_LOCK:
        jobs = _read_jobs_raw()
        jobs.append(job)
        save_jobs(jobs)

    # 更新 task-meta 元数据
    try:
        _update_task_meta(module, file, {
            "last_job_id": job["job_id"],
            "last_status": JOB_STATUS_PENDING,
            "last_target_task_name": target_task_name or "",
            "last_run_at": job["created_at"],
        })
    except Exception:
        pass  # task-meta 更新失败不影响主流程

    return job


# ---------------------------------------------------------------------------
# recover_timed_out_jobs — 源自 midscene-upload.py L5405
# ---------------------------------------------------------------------------

def recover_timed_out_jobs() -> None:
    """扫描所有 running 状态的 job，将超时未回传结果的 job 标记为 failed。

    源自 midscene-upload.py L5405 的 ``recover_timed_out_jobs`` 函数。
    超时阈值使用 :data:`JOB_TIMEOUT_SECONDS`，超过该时间仍未回传结果
    的 running job 会被自动回收为 failed 状态。
    """
    now = time.time()
    changed = False
    with JOB_LOCK:
        jobs = _read_jobs_raw()
        for job in jobs:
            if job.get("status") != JOB_STATUS_RUNNING:
                continue
            started = _parse_time(job.get("started_at")) or _parse_time(job.get("created_at"))
            if started and now - started > JOB_TIMEOUT_SECONDS:
                job["status"] = JOB_STATUS_FAILED
                job["finished_at"] = _now_str()
                job["timeout_recovered"] = True
                job["stderr_tail"] = f"任务执行超过 {JOB_TIMEOUT_SECONDS} 秒未回传结果，已自动回收"
                changed = True
        if changed:
            save_jobs(jobs)


# ---------------------------------------------------------------------------
# find_job — 源自 midscene-upload.py L5424
# ---------------------------------------------------------------------------

def find_job(job_id: str) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    """在 jobs 列表中查找指定 job，返回 (job, jobs) 元组。

    源自 midscene-upload.py L5424 的 ``find_job`` 函数。
    与 :func:`get_job` 不同的是，本函数同时返回完整的 jobs 列表，
    便于调用方在持有锁的前提下直接修改 job 并 save_jobs。

    Args:
        job_id: 目标 job 标识。

    Returns:
        (job_dict_or_None, jobs_list) 元组。找不到 job 时第一个元素为 None。
    """
    jobs = _read_jobs_raw()
    for job in jobs:
        if job.get("job_id") == job_id:
            return job, jobs
    return None, jobs


def _agent_run_progress_from_steps(run: Dict[str, Any]) -> int:
    """Derive a visible Agent progress value from step states.

    The Agent UI should never stay at 0% once a later step is running.  This
    helper is intentionally local to avoid importing agent_service from the job
    layer and creating a circular dependency.
    """
    steps = run.get("steps") if isinstance(run, dict) else []
    if not isinstance(steps, list) or not steps:
        return int(run.get("progress") or 0) if isinstance(run, dict) else 0
    status = str(run.get("status") or "").upper()
    if status in ("DONE", "FINISH"):
        return 100

    done_states = {"SUCCESS", "SKIPPED", "PARTIAL_FAILED", "FAILED", "WAIT_CONFIRM"}
    done = 0
    running_index = -1
    for idx, step in enumerate(steps):
        state = str((step or {}).get("status") or "").upper()
        if state in done_states:
            done += 1
        elif state == "RUNNING":
            running_index = idx
    total = max(1, len(steps))
    progress = int((done / total) * 100)
    if running_index >= 0:
        progress = max(progress, int(((running_index + 0.35) / total) * 100))
    if status in ("FAILED", "CANCELLED"):
        progress = max(progress, int(run.get("progress") or 0), 1)
        return min(progress, 99)
    return max(int(run.get("progress") or 0), min(progress, 99))


def runner_job_wait_timeout_seconds(job_count: int = 1) -> int:
    """Return the Agent-side wait window for Runner jobs.

    ``MIDSCENE_JOB_TIMEOUT_SECONDS`` is the canonical Runner timeout.  Older
    Agent code used a hard-coded 600 seconds and could mark long real-device
    runs as failed before the report callback arrived.  Keep a dedicated
    override for Agent waits, but default to the platform Runner timeout.
    """
    try:
        count = max(1, int(job_count or 1))
    except Exception:
        count = 1
    def env_int(name: str, default: int) -> int:
        try:
            return int(os.getenv(name, str(default)) or default)
        except Exception:
            return default

    base = max(
        60,
        env_int(
            "MIDSCENE_AGENT_RUNNER_JOB_WAIT_TIMEOUT_SECONDS",
            env_int("AGENT_RUNNER_JOB_WAIT_TIMEOUT_SECONDS", JOB_TIMEOUT_SECONDS),
        ),
    )
    per_job_default = min(max(300, JOB_TIMEOUT_SECONDS // 2), JOB_TIMEOUT_SECONDS)
    per_job = max(
        0,
        env_int(
            "MIDSCENE_AGENT_RUNNER_JOB_WAIT_TIMEOUT_PER_JOB_SECONDS",
            env_int("AGENT_RUNNER_JOB_WAIT_TIMEOUT_PER_JOB_SECONDS", per_job_default),
        ),
    )
    cap = max(
        base,
        env_int(
            "MIDSCENE_AGENT_RUNNER_JOB_WAIT_TIMEOUT_MAX_SECONDS",
            env_int("AGENT_RUNNER_JOB_WAIT_TIMEOUT_MAX_SECONDS", max(base, 7200)),
        ),
    )
    if per_job:
        return min(cap, max(base, base + max(0, count - 1) * per_job))
    return base


def _agent_step_for_job_progress(run: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not isinstance(run, dict):
        return None
    current = str(run.get("currentStep") or "").upper()
    aliases = {current, "RUN_SONIC", "RUN_TASK"}
    for step in run.get("steps") or []:
        if str((step or {}).get("step") or "").upper() in aliases:
            return step
    for step in run.get("steps") or []:
        if str((step or {}).get("status") or "").upper() == "RUNNING":
            return step
    return None


def _persist_agent_run(run: Dict[str, Any]) -> None:
    if not isinstance(run, dict) or not run.get("runId"):
        return
    try:
        with AGENT_RUN_LOCK:
            all_runs = read_json_cached(AGENT_RUNS_FILE, default={"runs": []})
            run_list = all_runs if isinstance(all_runs, list) else (all_runs.get("runs") or [])
            for i, r in enumerate(run_list):
                if r.get("runId") == run.get("runId"):
                    run_list[i] = run
                    break
            write_json_file(AGENT_RUNS_FILE, {"runs": run_list})
    except Exception:
        pass


def _job_label(job: Dict[str, Any]) -> str:
    return str(
        job.get("target_task_name")
        or job.get("current_task_name")
        or job.get("file")
        or job.get("job_id")
        or ""
    )


def _job_int(job: Dict[str, Any], key: str, default: int = 0) -> int:
    try:
        return int(job.get(key, default) or default)
    except Exception:
        return default


def _job_tail_line(text: str, limit: int = 160) -> str:
    lines = [line.strip() for line in str(text or "").splitlines() if line.strip()]
    return (lines[-1] if lines else "")[-limit:]


def _compact_job_text(text: Any, limit: int = 72) -> str:
    """Compress noisy Runner/Midscene progress into a readable one-line reason."""
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return ""
    low = raw.lower()
    if "replanned" in low and "exceed" in low:
        raw = "Midscene 重规划超限"
    elif "timeout after" in low:
        match = re.search(r"timeout after\s+(\d+)s", raw, re.I)
        raw = f"Runner 单任务超时 {match.group(1)}s" if match else "Runner 单任务超时"
    elif "report finalized" in low:
        raw = "报告已生成"
    elif "failed files" in low:
        raw = "Midscene 报告存在失败文件"
    elif "adb " in low or "screencap" in low or "pull " in low:
        raw = "ADB 截图/拉取中"
    elif "agent 等待 runner 报告超时" in raw.lower():
        raw = "等待 Runner 报告回传"

    raw = re.sub(r"[A-Za-z]:\\[^ ]+", lambda m: os.path.basename(m.group(0).replace("\\", "/")), raw)
    raw = re.sub(r"/[^ ]{20,}", lambda m: os.path.basename(m.group(0)), raw)
    if len(raw) > limit:
        raw = raw[: max(0, limit - 1)].rstrip() + "…"
    return raw


def _short_job_label(text: Any, limit: int = 26) -> str:
    label = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(label) > limit:
        return label[: max(0, limit - 1)].rstrip() + "…"
    return label


def _job_entry_from_record(job: Dict[str, Any], jid: str, status: str) -> Dict[str, Any]:
    return {
        "job_id": jid,
        "status": status,
        "module": job.get("module", ""),
        "file": job.get("file", ""),
        "target_task_name": job.get("target_task_name", ""),
        "current_task_name": job.get("current_task_name", ""),
        "taskName": job.get("target_task_name") or job.get("current_task_name") or job.get("task_name") or "",
        "current_task_index": _job_int(job, "current_task_index", 0),
        "completed_task_count": _job_int(job, "completed_task_count", 0),
        "total_task_count": _job_int(job, "total_task_count", 0),
        "progress": _job_int(job, "progress", 0),
        "progress_message": job.get("progress_message", ""),
        "stdout_tail": job.get("stdout_tail", ""),
        "stderr_tail": job.get("stderr_tail", ""),
        "started_at": job.get("started_at", ""),
        "updated_at": job.get("updated_at", ""),
        "runner_id": job.get("runner_id", ""),
        "device_id": job.get("device_id", ""),
        "report_url": job.get("report_url") or job.get("reportUrl", ""),
        "report_upload_pending": safe_bool(job.get("report_upload_pending")),
        "report_upload_error": job.get("report_upload_error", ""),
        "error": job.get("error", ""),
        "failure_review": job.get("failure_review") or job.get("failureReview") or {},
    }


def _job_progress_detail(job: Dict[str, Any]) -> str:
    parts = []
    progress = _job_int(job, "progress", 0)
    if progress:
        parts.append(f"{progress}%")
    completed = _job_int(job, "completed_task_count", 0)
    total = _job_int(job, "total_task_count", 0)
    if total:
        parts.append(f"{completed}/{total}")
    current = _short_job_label(_job_label(job))
    if current:
        parts.append(f"当前 {current}")
    message = str(job.get("progress_message") or "").strip()
    tail = _job_tail_line(job.get("stdout_tail") or job.get("stderr_tail") or "")
    if message:
        parts.append(_compact_job_text(message))
    elif tail:
        parts.append(_compact_job_text(tail))
    updated_at = str(job.get("updated_at") or "").strip()
    if updated_at:
        parts.append(f"更新 {updated_at}")
        updated_ts = _parse_time(updated_at)
        if updated_ts:
            stale_seconds = int(time.time() - updated_ts)
            if stale_seconds >= 90:
                parts.append(f"进度停滞 {stale_seconds}s")
    return " · ".join(parts)


def _agent_runner_phase_label(phase_key: str, *, running: bool, wait_timeout: bool = False) -> str:
    lower = str(phase_key or "").lower()
    if "dry-run" in lower or "dry_run" in lower:
        base = "Runner dry-run"
    elif "smoke" in lower:
        base = "首批冒烟"
    elif "expand" in lower or "remaining" in lower:
        base = "扩展执行"
    else:
        base = "Runner"
    if wait_timeout:
        return f"{base}等待报告超时"
    return f"{base}执行中" if running else f"{base}执行结束"


def _update_agent_job_progress_trace(
    run: Dict[str, Any],
    *,
    completed: List[Dict[str, Any]],
    failed: List[Dict[str, Any]],
    running: List[Dict[str, Any]],
    wait_timeout_jobs: Optional[List[Dict[str, Any]]] = None,
    elapsed: int,
    timeout: int,
    phase: str = "",
    force: bool = False,
    status: str = "RUNNING",
) -> None:
    """Write visible Runner progress onto the current Agent timeline step."""
    artifacts = run.setdefault("artifacts", {})
    phase_key = str(phase or "runner").strip() or "runner"
    last_trace_map = artifacts.setdefault("jobProgressLastTraceAtByPhase", {})
    last_trace_at = int(last_trace_map.get(phase_key, -999) or -999)
    should_trace = force or elapsed <= 1 or (elapsed - last_trace_at) >= 15 or not running
    running_names = [_short_job_label(_job_label(item)) for item in running[:3]]
    wait_timeout_jobs = wait_timeout_jobs or []
    state_label = _agent_runner_phase_label(phase_key, running=bool(running), wait_timeout=bool(wait_timeout_jobs))
    phase_prefix = f"{phase_key}：" if phase_key != "runner" else ""
    summary = (
        f"{phase_prefix}{state_label}：{len(completed)} 成功 / {len(failed)} 失败 / "
        f"{len(running)} 运行中，已等待 {elapsed}s / 上限 {timeout}s"
    )
    if wait_timeout_jobs:
        summary += f"；{len(wait_timeout_jobs)} 个任务仍在等待 Runner 报告回传"
    if running_names:
        summary += "；当前：" + "、".join(name for name in running_names if name)
    details = [_job_progress_detail(item) for item in (running[:3] + wait_timeout_jobs[:2] + failed[:2])]
    details = [item for item in details if item]
    if details:
        summary += "；" + "；".join(details)
    step = _agent_step_for_job_progress(run)
    if step:
        step["summary"] = summary
        step["outputSummary"] = summary
        if should_trace:
            step.setdefault("liveTrace", []).append({
                "time": _trace_time_text(),
                "message": summary,
                "status": status,
            })
            del step["liveTrace"][:-50]
            last_trace_map[phase_key] = elapsed
            artifacts["jobProgressLastTraceAt"] = elapsed
    trace = run.setdefault("trace", [])
    if should_trace:
        trace.append({
            "time": _trace_time_text(),
            "message": summary,
        })
        del trace[:-120]


# ---------------------------------------------------------------------------
# wait_jobs_finished — 源自 midscene-upload.py L2669
# ---------------------------------------------------------------------------

def wait_jobs_finished(
    job_ids: List[str],
    run: Dict[str, Any],
    timeout: int = 600,
    interval: int = 5,
    phase: str = "",
) -> Dict[str, List[Dict[str, Any]]]:
    """等待 job 列表全部进入终态，期间更新 Agent Run 进度。

    源自 midscene-upload.py L2669 的 ``wait_jobs_finished`` 函数。

    Args:
        job_ids: 需要等待的 job_id 列表。
        run: Agent Run 字典，进度会写入其 ``artifacts.jobProgress`` 字段。
        timeout: 最大等待秒数，默认 600。
        interval: 轮询间隔秒数，默认 5。

    Returns:
        {"completed": [...], "failed": [...], "running": [...], "timeout": [...]}
    """
    if not job_ids:
        return {"completed": [], "failed": [], "running": [], "timeout": []}

    start_time = time.time()

    while True:
        elapsed = time.time() - start_time
        if elapsed >= timeout:
            break

        with JOB_LOCK:
            jobs = _read_jobs_raw()

        completed: List[Dict[str, Any]] = []
        failed: List[Dict[str, Any]] = []
        running: List[Dict[str, Any]] = []

        for jid in job_ids:
            job = next((j for j in jobs if j.get("job_id") == jid), None)
            if not job:
                failed.append({"job_id": jid, "status": "not_found", "error": "任务不存在"})
                continue
            status = (job.get("status") or "").lower()
            if status in TERMINAL_JOB_STATUSES:
                entry = _job_entry_from_record(job, jid, status)
                if status == JOB_STATUS_SUCCESS:
                    completed.append(entry)
                else:
                    failed.append(entry)
            else:
                running.append(_job_entry_from_record(job, jid, status))

        # 更新 Agent Run 的执行进度
        artifacts = run.setdefault("artifacts", {})
        artifacts["jobProgress"] = {
            "phase": phase or "runner",
            "total": len(job_ids),
            "completed": len(completed),
            "failed": len(failed),
            "running": len(running),
            "elapsed": int(elapsed),
            "timeout": timeout,
            "jobs": completed + failed + running,
        }
        artifacts.setdefault("jobProgressByPhase", {})[phase or "runner"] = artifacts["jobProgress"]
        run["progress"] = _agent_run_progress_from_steps(run)
        run["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        _update_agent_job_progress_trace(
            run,
            completed=completed,
            failed=failed,
            running=running,
            elapsed=int(elapsed),
            timeout=timeout,
            phase=phase,
            force=not running,
            status="SUCCESS" if not running and not failed else ("PARTIAL_FAILED" if not running else "RUNNING"),
        )
        # 持久化进度（让前端能看到）
        _persist_agent_run(run)

        # 全部结束则退出
        if not running:
            return {"completed": completed, "failed": failed, "running": [], "timeout": []}

        time.sleep(interval)

    # Agent 等待超时只表示报告尚未回传，不能改写 Runner job 终态。
    # Runner 之后仍可能回传 success/failed；这里保留原 job 状态，避免前端出现
    # “先失败、后成功”的假性翻转。
    with JOB_LOCK:
        jobs = _read_jobs_raw()

    timeout_jobs: List[Dict[str, Any]] = []
    for jid in job_ids:
        job = next((j for j in jobs if j.get("job_id") == jid), None)
        if job and (job.get("status") or "").lower() not in TERMINAL_JOB_STATUSES:
            error = job.get("error") or "Agent 等待 Runner 报告超时，任务可能仍在执行或报告尚未回传"
            entry = _job_entry_from_record(job, jid, (job.get("status") or JOB_STATUS_RUNNING).lower())
            entry.update({
                "agent_wait_timeout": True,
                "error": error,
                "report_missing_reason": error,
            })
            timeout_jobs.append({
                **entry,
            })

    # 重新统计最终状态
    completed = []
    failed = []
    for jid in job_ids:
        job = next((j for j in jobs if j.get("job_id") == jid), None)
        if not job:
            continue
        status = (job.get("status") or "").lower()
        entry = _job_entry_from_record(job, jid, status)
        if status == JOB_STATUS_SUCCESS:
            completed.append(entry)
        elif status in TERMINAL_JOB_STATUSES:
            failed.append(entry)

    artifacts = run.setdefault("artifacts", {})
    artifacts["jobProgress"] = {
        "phase": phase or "runner",
        "total": len(job_ids),
        "completed": len(completed),
        "failed": len(failed),
        "running": len(timeout_jobs),
        "timeout": len(timeout_jobs),
        "elapsed": int(time.time() - start_time),
        "timeoutSeconds": timeout,
        "jobs": completed + failed + timeout_jobs,
        "agentWaitTimeout": True,
    }
    artifacts.setdefault("jobProgressByPhase", {})[phase or "runner"] = artifacts["jobProgress"]
    run["progress"] = _agent_run_progress_from_steps(run)
    run["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    _update_agent_job_progress_trace(
        run,
        completed=completed,
        failed=failed,
        running=timeout_jobs,
        wait_timeout_jobs=timeout_jobs,
        elapsed=int(time.time() - start_time),
        timeout=timeout,
        phase=phase,
        force=True,
        status="WARNING",
    )
    _persist_agent_run(run)

    return {"completed": completed, "failed": failed, "running": timeout_jobs, "timeout": timeout_jobs}





# ---------------------------------------------------------------------------
# Migrated from midscene-upload.py
# ---------------------------------------------------------------------------

def load_task_meta():
    data = read_json_file(TASK_META_FILE, default={})
    return data if isinstance(data, dict) else {}



def update_task_meta(module, file, patch):
    data = load_task_meta()
    key = task_key(module, file)
    row = data.get(key, {"module": module, "file": clean_filename(file), "status": "draft"})
    row.update({k: v for k, v in patch.items() if v is not None})
    row["module"] = module
    row["file"] = clean_filename(file)
    row["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    data[key] = row
    save_task_meta(data)
    return row



def load_task_apps():
    data = read_json_file(TASK_APPS_FILE, default={"apps": []})
    if isinstance(data, list):
        data = {"apps": data}
    if not isinstance(data, dict):
        return {"apps": []}
    apps = data.get("apps") or []
    return {"apps": apps if isinstance(apps, list) else []}



def save_task_apps(data):
    write_json_file(TASK_APPS_FILE, data)



def new_job_id():
    return unique_millis_id("job")



def normalize_task_app(payload):
    package = (payload.get("package") or payload.get("app_package") or payload.get("appPackage") or "").strip()
    name = (payload.get("name") or payload.get("app_name") or payload.get("appName") or package or "未命名应用").strip()
    modules = payload.get("modules") or []
    if isinstance(modules, str):
        modules = [item.strip() for item in modules.split(",") if item.strip()]
    modules = sorted(set(str(item).strip() for item in modules if str(item).strip()))
    if not package:
        raise ValueError("包名不能为空")
    app = {
        "package": package,
        "name": name,
        "modules": modules,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    for src, dst in (
        ("sonic_project_id", "sonic_project_id"),
        ("sonicProjectId", "sonic_project_id"),
        ("sonic_project_name", "sonic_project_name"),
        ("sonicProjectName", "sonic_project_name"),
        ("sonic_suite_id", "sonic_suite_id"),
        ("sonicSuiteId", "sonic_suite_id"),
        ("sonic_suite_name", "sonic_suite_name"),
        ("sonicSuiteName", "sonic_suite_name"),
        ("feishu_webhook", "feishu_webhook"),
        ("feishuWebhook", "feishu_webhook"),
        ("feishu_bot", "feishu_webhook"),
        ("feishuBot", "feishu_webhook"),
    ):
        if payload.get(src) not in (None, ""):
            app[dst] = str(payload.get(src)).strip()
    if app.get("feishu_webhook"):
        app["feishu_webhook"] = validate_feishu_webhook(app["feishu_webhook"])
    return app



def app_package_for_module(module):
    try:
        from .sonic_service import sonic_notify_known_apps
        apps = sonic_notify_known_apps()
        for app in apps:
            if module in (app.get("modules") or []):
                package = (app.get("package") or "").strip()
                if package:
                    return package
    except Exception:
        pass
    return ""



def task_app_map_by_package():
    result = {}
    try:
        from .sonic_service import sonic_notify_known_apps
        for app in sonic_notify_known_apps():
            package = (app.get("package") or "").strip()
            if package:
                result[package] = app
    except Exception:
        pass
    return result



def copy_or_move_task_file(src_module, src_file, dst_module, dst_file, move=False, overwrite=False):
    from .yaml_service import save_file_version
    src_file = clean_filename(src_file)
    dst_file = clean_filename(dst_file or src_file)
    src_path = safe_join(TASK_DIR, src_module, src_file)
    if not os.path.exists(src_path):
        raise FileNotFoundError("源 YAML 文件不存在")
    dst_dir = safe_join(TASK_DIR, dst_module)
    os.makedirs(dst_dir, exist_ok=True)
    dst_path = safe_join(dst_dir, dst_file)
    if os.path.exists(dst_path) and not overwrite:
        raise FileExistsError("目标文件已存在，如需覆盖请勾选覆盖")
    if os.path.exists(dst_path):
        save_file_version(dst_module, dst_file, reason="overwrite")
    if move:
        save_file_version(src_module, src_file, reason="before_move")
    if move:
        if os.path.abspath(src_path) == os.path.abspath(dst_path):
            return dst_file
        shutil.move(src_path, dst_path)
    else:
        shutil.copyfile(src_path, dst_path)
    return dst_file



def save_task_meta(data):
    write_json_file(TASK_META_FILE, data)



def task_key(module, file):
    return f"{module}::{clean_filename(file)}"
