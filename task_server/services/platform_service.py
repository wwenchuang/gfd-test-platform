"""平台健康监控服务。

聚合各子系统（Task Server、AI Gateway、Sonic、Runner、存储）的健康状态，
返回统一的结构化健康报告。本模块不直接处理 HTTP —— 由 router 层调用。
"""

from __future__ import annotations

import json
import os
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List

from task_server.config import (
    AI_SKILLS_DIR,
    DEFAULT_DASHSCOPE_BASE_URL,
    DEFAULT_TEXT_MODEL,
    DEFAULT_VL_MODEL,
    FALLBACK_DASHSCOPE_API_KEY,
    REPORT_DIR,
)
from task_server.storage import runtime_path_status
from .ai_gateway_client import ai_gateway_health
from .agent_service import load_agent_runs
from .job_service import load_jobs
from .report_service import get_report_stats
from .runner_service import get_online_runners, list_runners
from .sonic_service import sonic_health

try:
    import yaml as pyyaml
except Exception:
    pyyaml = None

# ---------------------------------------------------------------------------
# 版本常量
# ---------------------------------------------------------------------------

PLATFORM_VERSION = "1.0.0"

# ---------------------------------------------------------------------------
# 进程启动时间（模块加载时记录）
# ---------------------------------------------------------------------------

_PROCESS_START_TIME = time.time()

# ---------------------------------------------------------------------------
# 子系统检查
# ---------------------------------------------------------------------------


def _check_ai_gateway() -> Dict[str, Any]:
    """检查 AI Gateway 健康状态。"""
    try:
        result = ai_gateway_health()
        ok = bool(result.get("ok"))
        return {
            "status": "healthy" if ok else "degraded",
            "ok": ok,
            "url": result.get("url", "http://127.0.0.1:8090"),
            "elapsed_ms": result.get("elapsed_ms", 0),
            "error": result.get("error", ""),
        }
    except Exception as exc:
        return {
            "status": "unreachable",
            "ok": False,
            "url": "http://127.0.0.1:8090",
            "elapsed_ms": 0,
            "error": str(exc),
        }


def _check_ai_gateway_raw() -> str:
    """轻量级 AI Gateway 健康检查，仅返回状态字符串。"""
    try:
        req = urllib.request.urlopen("http://127.0.0.1:8090/health", timeout=3)
        return "healthy" if req.status == 200 else "degraded"
    except Exception:
        return "unreachable"


def _check_sonic() -> Dict[str, Any]:
    """检查 Sonic 连接健康状态。"""
    try:
        result = sonic_health()
        ok = bool(result.get("ok"))
        return {
            "status": "healthy" if ok else "unhealthy",
            "ok": ok,
            "connected": ok,
            "base_url": result.get("base_url", ""),
            "project_count": result.get("project_count", 0),
            "elapsed_ms": result.get("elapsed_ms", 0),
            "error": result.get("error", ""),
        }
    except Exception as exc:
        return {
            "status": "unreachable",
            "ok": False,
            "connected": False,
            "base_url": "",
            "project_count": 0,
            "elapsed_ms": 0,
            "error": str(exc),
        }


def _count_runners() -> Dict[str, Any]:
    """统计 Runner 在线情况与设备数量。"""
    try:
        all_runners = list_runners(include_online_flag=True)
        online = get_online_runners()
        total = len(all_runners)
        online_count = len(online)

        # 统计设备数
        device_count = 0
        for runner in online.values():
            devices = runner.get("devices") or []
            device_count += sum(
                1 for d in devices if d.get("status") == "online"
            )

        return {
            "online": online_count,
            "total": total,
            "devices": device_count,
        }
    except Exception:
        return {
            "online": 0,
            "total": 0,
            "devices": 0,
        }


def _recent_job_stats() -> Dict[str, Any]:
    """统计最近 Job 执行情况（取最近 100 条）。"""
    try:
        jobs = load_jobs(limit=100)
        total = len(jobs)
        success = sum(1 for j in jobs if str(j.get("status", "")).lower() == "success")
        failed = sum(
            1 for j in jobs
            if str(j.get("status", "")).lower() in ("failed", "timeout", "cancelled")
        )
        rate = f"{success / total * 100:.0f}%" if total > 0 else "N/A"
        return {
            "total": total,
            "success": success,
            "failed": failed,
            "successRate": rate,
        }
    except Exception:
        return {
            "total": 0,
            "success": 0,
            "failed": 0,
            "successRate": "N/A",
        }


def _recent_agent_run_stats() -> Dict[str, Any]:
    """统计最近 Agent Run 执行情况。"""
    try:
        runs = load_agent_runs()
        # 只统计最近 100 条
        recent_runs = runs[:100] if isinstance(runs, list) else []
        total = len(recent_runs)
        success = sum(
            1 for r in recent_runs
            if str(r.get("status", "")).lower() in ("done", "success", "completed")
        )
        failed = sum(
            1 for r in recent_runs
            if str(r.get("status", "")).lower() in ("failed", "error", "timeout")
        )
        rate = f"{success / total * 100:.0f}%" if total > 0 else "N/A"
        return {
            "total": total,
            "success": success,
            "failed": failed,
            "successRate": rate,
        }
    except Exception:
        return {
            "total": 0,
            "success": 0,
            "failed": 0,
            "successRate": "N/A",
        }


def _storage_info() -> Dict[str, Any]:
    """收集存储与磁盘信息。"""
    from ..config import LEARNING_DIR, REPORT_DIR

    # 磁盘剩余空间
    disk_free = "unknown"
    try:
        usage = shutil.disk_usage("/")
        free_gb = usage.free / (1024 ** 3)
        disk_free = f"{free_gb:.1f} GB"
    except Exception:
        pass

    # 日志目录大小
    logs_size = "unknown"
    try:
        log_dir = os.path.join(os.path.dirname(REPORT_DIR), "logs")
        if not os.path.isdir(log_dir):
            # 尝试 AI Gateway 的 logs 目录
            log_dir = os.path.join(
                os.path.dirname(os.path.abspath(__file__)),
                "..", "..", "ai-gateway", "logs",
            )
        if os.path.isdir(log_dir):
            total_bytes = 0
            for dirpath, _dirnames, filenames in os.walk(log_dir):
                for fname in filenames:
                    try:
                        total_bytes += os.path.getsize(os.path.join(dirpath, fname))
                    except Exception:
                        pass
            logs_size = f"{total_bytes / (1024 ** 2):.1f} MB"
        else:
            logs_size = "0 MB"
    except Exception:
        pass

    # 报告数量
    reports_count = 0
    try:
        stats = get_report_stats()
        reports_count = stats.get("total", 0)
    except Exception:
        pass

    return {
        "diskFree": disk_free,
        "logsSize": logs_size,
        "reportsCount": reports_count,
    }


def _collect_recent_errors(limit: int = 5) -> List[Dict[str, str]]:
    """收集最近错误日志条目。"""
    errors: List[Dict[str, str]] = []

    # 从 jobs 中提取最近失败的错误
    try:
        jobs = load_jobs(limit=50)
        for job in jobs:
            if str(job.get("status", "")).lower() not in ("failed", "timeout", "cancelled"):
                continue
            error_msg = (
                job.get("recentError")
                or job.get("error")
                or job.get("stderr_tail")
                or ""
            )
            if not error_msg:
                continue
            errors.append({
                "time": str(job.get("finished_at") or job.get("updated_at") or ""),
                "source": f"job:{job.get('jobId', '')}",
                "message": str(error_msg)[:300],
            })
            if len(errors) >= limit:
                break
    except Exception:
        pass

    return errors[:limit]


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------


def get_platform_status() -> Dict[str, Any]:
    """返回平台健康状态。

    聚合各子系统健康检查结果，包括：
    - Task Server 自身状态（版本、运行时间）
    - AI Gateway 连接状态
    - Sonic 连接状态
    - Runner 在线统计
    - 最近 Job / Agent Run 执行统计
    - 存储与磁盘信息
    - 最近错误日志
    """
    uptime_seconds = int(time.time() - _PROCESS_START_TIME)
    uptime_str = f"{uptime_seconds // 3600}h{(uptime_seconds % 3600) // 60}m"

    ai_gw = _check_ai_gateway()
    sonic = _check_sonic()
    runners = _count_runners()

    return {
        "taskServer": {
            "status": "healthy",
            "uptime": uptime_str,
            "version": PLATFORM_VERSION,
        },
        "aiGateway": {
            "status": ai_gw.get("status", "unreachable"),
            "ok": ai_gw.get("ok", False),
            "url": ai_gw.get("url", "http://127.0.0.1:8090"),
            "elapsed_ms": ai_gw.get("elapsed_ms", 0),
            "error": ai_gw.get("error", ""),
        },
        "sonic": {
            "status": sonic.get("status", "unreachable"),
            "ok": sonic.get("ok", False),
            "connected": sonic.get("connected", False),
            "base_url": sonic.get("base_url", ""),
            "project_count": sonic.get("project_count", 0),
            "elapsed_ms": sonic.get("elapsed_ms", 0),
            "error": sonic.get("error", ""),
        },
        "runners": runners,
        "recentJobs": _recent_job_stats(),
        "recentAgentRuns": _recent_agent_run_stats(),
        "storage": _storage_info(),
        "recentErrors": _collect_recent_errors(),
        "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


__all__ = [
    "PLATFORM_VERSION",
    "get_platform_status",
]




# ---------------------------------------------------------------------------
# Migrated from midscene-upload.py
# ---------------------------------------------------------------------------

def dashscope_api_key(required=True):
    value = (
        os.getenv("DASHSCOPE_API_KEY")
        or os.getenv("OPENAI_API_KEY")
        or os.getenv("MIDSCENE_API_KEY")
        or FALLBACK_DASHSCOPE_API_KEY
        or ""
    ).strip().strip("\"'")
    if required and not value:
        raise ValueError("未配置 DASHSCOPE_API_KEY/OPENAI_API_KEY/MIDSCENE_API_KEY")
    return value



def dashscope_base_url():
    return (os.getenv("DASHSCOPE_BASE_URL") or os.getenv("OPENAI_BASE_URL") or os.getenv("MIDSCENE_BASE_URL") or DEFAULT_DASHSCOPE_BASE_URL).rstrip("/")



def dashscope_text_model():
    return (os.getenv("DASHSCOPE_MODEL") or DEFAULT_TEXT_MODEL).strip()



def dashscope_vl_model():
    return (os.getenv("DASHSCOPE_VL_MODEL") or os.getenv("MIDSCENE_MODEL_NAME") or DEFAULT_VL_MODEL).strip()



def ai_skills_status():
    prompt_dir = os.path.join(AI_SKILLS_DIR, "prompts")
    schema_dir = os.path.join(AI_SKILLS_DIR, "schemas")
    reference_dir = os.path.join(AI_SKILLS_DIR, "references")
    shared_schema_names = {"cases_payload"}
    prompt_names = set()
    schema_names = set()
    reference_names = []
    if os.path.isdir(prompt_dir):
        for name in os.listdir(prompt_dir):
            if name.endswith(".v1.md"):
                prompt_names.add(name[:-len(".v1.md")])
    if os.path.isdir(schema_dir):
        for name in os.listdir(schema_dir):
            if name.endswith(".schema.json"):
                schema_names.add(name[:-len(".schema.json")])
    if os.path.isdir(reference_dir):
        reference_names = sorted(name for name in os.listdir(reference_dir) if name.endswith(".md"))
    callable_schema_names = schema_names - shared_schema_names
    missing_prompts = sorted(callable_schema_names - prompt_names)
    missing_schemas = sorted(prompt_names - schema_names)
    return {
        **runtime_path_status(AI_SKILLS_DIR),
        "prompt_count": len(prompt_names),
        "schema_count": len(schema_names),
        "reference_count": len(reference_names),
        "skills": sorted(prompt_names | callable_schema_names),
        "shared_schemas": sorted(schema_names & shared_schema_names),
        "references": reference_names,
        "missing_prompts": missing_prompts,
        "missing_schemas": missing_schemas,
        "ready": os.path.isdir(prompt_dir) and os.path.isdir(schema_dir) and not missing_prompts and not missing_schemas,
    }



def read_json(path, default=None):
    try:
        path = Path(path)
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return default



def read_text(path, default=""):
    try:
        path = Path(path)
        if not path.exists():
            return default
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return default
