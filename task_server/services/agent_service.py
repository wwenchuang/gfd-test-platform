"""Agent 运行框架服务。

从 midscene-upload.py 迁移 Agent 状态机、工具调用和运行管理逻辑。
与 midscene-upload.py 中的 _execute_agent_step / _execute_agent_steps 保持一致。
"""

import json
import os
import re
import secrets
import threading
import time
import traceback
import urllib.parse
import uuid
import base64
import io
import unicodedata
from typing import Any, Dict, List, Optional

from task_server.core.http_client import http_client

try:
    import yaml as pyyaml
except Exception:
    pyyaml = None

from task_server.config import (
    AGENT_RUNS_FILE,
    AGENT_RUN_LOCK,
    AI_GATEWAY_URL,
    JOB_LOCK,
    LEARNING_DIR,
    PORT,
    SONIC_SUITE_RESULTS_FILE,
    TASK_DIR,
    dashscope_api_key,
    dashscope_base_url,
    dashscope_text_model,
    safe_int,
)
from task_server.schemas import AGENT_STATE_STEPS, HIGH_RISK_KEYWORDS, MIDSCENE_FLOW_ACTIONS
from task_server.storage import (
    clean_id,
    clean_filename,
    read_json_cached,
    read_json_file,
    read_text_file,
    safe_join,
    unique_millis_id,
    write_text_file,
    write_json_file,
)
from task_server.services.yaml_service import extract_midscene_tasks, slug_for_file, validate_midscene_yaml_executability
from task_server.services.yaml_executable_scorer import (
    assertion_tap_to_wait_prompt,
    rank_executable_yaml_refs,
    score_midscene_yaml_executable,
    tap_prompt_looks_assertion,
)
from task_server.services.yaml_execution_plan import (
    build_generated_yaml_execution_plan,
    classify_generated_yaml_failure_bucket,
    classify_generated_yaml_smoke_blocker,
    update_execution_plan_after_smoke,
)
from task_server.prompts import get_prompt_center

# ---------------------------------------------------------------------------
# Agent Tool Registry & Constants (migrated from midscene-upload.py)
# ---------------------------------------------------------------------------

AGENT_TOOL_CALLS_FILE = os.path.join(LEARNING_DIR, "agent-tool-calls.json")
AGENT_TOOL_CALL_LOCK = threading.Lock()
AGENT_DRAFT_DIR = os.path.join(LEARNING_DIR, "agent-drafts")
AGENT_CANCEL_DIR = os.path.join(LEARNING_DIR, "agent-cancel")
AGENT_LEARNING_FILE = os.path.join(LEARNING_DIR, "agent-learning.json")
AGENT_LEARNING_LOCK = threading.Lock()
AGENT_ACTIVE_WORKERS = set()
AGENT_ACTIVE_WORKERS_LOCK = threading.Lock()

AGENT_RUN_STEPS = AGENT_STATE_STEPS

AGENT_RISK_KEYWORDS = HIGH_RISK_KEYWORDS

AUTO_AGENT_RISK_KEYWORDS = AGENT_RISK_KEYWORDS

AGENT_SERVICE_STARTED_TS = time.time()

AGENT_DEFAULT_BUSINESS_FLOW = ["进入稳定起点", "执行核心业务动作", "校验业务结果"]

AGENT_GENERATED_RUNNER_SMOKE_LIMIT = max(
    1,
    min(10, safe_int(os.getenv("MIDSCENE_AGENT_GENERATED_RUNNER_SMOKE_LIMIT"), 8)),
)
AGENT_GENERATED_RUNNER_FIRST_SMOKE_LIMIT = max(
    1,
    min(3, safe_int(os.getenv("MIDSCENE_AGENT_GENERATED_RUNNER_FIRST_SMOKE_LIMIT"), 3)),
)
AGENT_GENERATED_RUNNER_EXPAND_LIMIT = max(
    AGENT_GENERATED_RUNNER_SMOKE_LIMIT,
    min(100, safe_int(os.getenv("MIDSCENE_AGENT_GENERATED_RUNNER_EXPAND_LIMIT"), 30)),
)
AGENT_GENERATED_RUNNER_EXPAND_BATCH_LIMIT = max(
    1,
    min(
        AGENT_GENERATED_RUNNER_EXPAND_LIMIT,
        safe_int(
            os.getenv("MIDSCENE_AGENT_GENERATED_RUNNER_EXPAND_BATCH_LIMIT"),
            max(AGENT_GENERATED_RUNNER_SMOKE_LIMIT, min(16, AGENT_GENERATED_RUNNER_SMOKE_LIMIT * 2)),
        ),
    ),
)


def _trace_time_text():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _agent_cancel_marker_path(run_id):
    return os.path.join(AGENT_CANCEL_DIR, f"{clean_id(str(run_id or ''), 'run')}.cancel")


def _mark_agent_run_cancel_requested(run_id, reason="用户取消"):
    try:
        os.makedirs(AGENT_CANCEL_DIR, exist_ok=True)
        write_text_file(_agent_cancel_marker_path(run_id), str(reason or "用户取消"))
    except Exception:
        pass


def _agent_run_cancel_requested(run):
    if not isinstance(run, dict):
        return False
    if str(run.get("status") or "").upper() == "CANCELLED":
        return True
    run_id = str(run.get("runId") or "").strip()
    return bool(run_id and os.path.exists(_agent_cancel_marker_path(run_id)))


def _run_agent_steps_guarded(run_id):
    try:
        _execute_agent_steps(run_id)
    finally:
        with AGENT_ACTIVE_WORKERS_LOCK:
            AGENT_ACTIVE_WORKERS.discard(str(run_id or ""))


def _start_agent_worker(run_id):
    """Start one background executor per run in the current service process."""
    run_id = str(run_id or "").strip()
    if not run_id:
        return False
    with AGENT_ACTIVE_WORKERS_LOCK:
        if run_id in AGENT_ACTIVE_WORKERS:
            return False
        AGENT_ACTIVE_WORKERS.add(run_id)
    try:
        worker = threading.Thread(target=_run_agent_steps_guarded, args=(run_id,), daemon=True)
        worker.start()
        return True
    except Exception:
        with AGENT_ACTIVE_WORKERS_LOCK:
            AGENT_ACTIVE_WORKERS.discard(run_id)
        return False

AGENT_TOOLS = {
    # READ_TOOLS
    "list_cases": {"name": "list_cases", "title": "读取用例列表", "category": "READ", "riskLevel": "low", "write": False, "requiresConfirm": False},
    "read_yaml": {"name": "read_yaml", "title": "读取 YAML 文件", "category": "READ", "riskLevel": "low", "write": False, "requiresConfirm": False},
    "list_jobs": {"name": "list_jobs", "title": "读取执行记录", "category": "READ", "riskLevel": "low", "write": False, "requiresConfirm": False},
    "read_report": {"name": "read_report", "title": "读取执行报告", "category": "READ", "riskLevel": "low", "write": False, "requiresConfirm": False},
    "read_model_strategy": {"name": "read_model_strategy", "title": "读取模型策略", "category": "READ", "riskLevel": "low", "write": False, "requiresConfirm": False},
    "list_runners": {"name": "list_runners", "title": "读取 Runner 列表", "category": "READ", "riskLevel": "low", "write": False, "requiresConfirm": False},
    # AI_TOOLS
    "analyze_goal": {"name": "analyze_goal", "title": "分析测试目标", "category": "AI", "riskLevel": "low", "write": False, "requiresConfirm": False},
    "generate_cases": {"name": "generate_cases", "title": "生成测试用例", "category": "AI", "riskLevel": "low", "write": False, "requiresConfirm": False},
    "generate_yaml": {"name": "generate_yaml", "title": "生成 YAML", "category": "AI", "riskLevel": "low", "write": False, "requiresConfirm": False},
    "analyze_failure": {"name": "analyze_failure", "title": "分析失败原因", "category": "AI", "riskLevel": "low", "write": False, "requiresConfirm": False},
    "generate_repair_draft": {"name": "generate_repair_draft", "title": "生成修复草稿", "category": "AI", "riskLevel": "low", "write": False, "requiresConfirm": False},
    "generate_bug_draft": {"name": "generate_bug_draft", "title": "生成缺陷草稿", "category": "AI", "riskLevel": "low", "write": False, "requiresConfirm": False},
    "generate_summary": {"name": "generate_summary", "title": "生成总结报告", "category": "AI", "riskLevel": "low", "write": False, "requiresConfirm": False},
    # SONIC_TOOLS
    "sonic_list_projects": {"name": "sonic_list_projects", "title": "查询 Sonic 项目", "category": "SONIC", "riskLevel": "low", "write": False, "requiresConfirm": False},
    "sonic_list_suites": {"name": "sonic_list_suites", "title": "查询 Sonic 测试套", "category": "SONIC", "riskLevel": "low", "write": False, "requiresConfirm": False},
    "sonic_sync_case": {"name": "sonic_sync_case", "title": "同步单条用例到 Sonic", "category": "SONIC", "riskLevel": "medium", "write": True, "requiresConfirm": False},
    "sonic_sync_batch": {"name": "sonic_sync_batch", "title": "批量同步 Sonic 用例", "category": "SONIC", "riskLevel": "high", "write": True, "requiresConfirm": True},
    "sonic_run_suite": {"name": "sonic_run_suite", "title": "执行 Sonic 测试套", "category": "SONIC", "riskLevel": "medium", "write": True, "requiresConfirm": False},
    "sonic_read_result": {"name": "sonic_read_result", "title": "读取 Sonic 执行结果", "category": "SONIC", "riskLevel": "low", "write": False, "requiresConfirm": False},
    "sonic_read_report": {"name": "sonic_read_report", "title": "读取 Sonic 报告", "category": "SONIC", "riskLevel": "low", "write": False, "requiresConfirm": False},
    # TASK_TOOLS
    "create_runner_job": {"name": "create_runner_job", "title": "创建 Runner 任务", "category": "TASK", "riskLevel": "medium", "write": True, "requiresConfirm": False},
    "run_midscene_task": {"name": "run_midscene_task", "title": "执行 Midscene 任务", "category": "TASK", "riskLevel": "medium", "write": True, "requiresConfirm": False},
    "retry_failed_job": {"name": "retry_failed_job", "title": "重跑失败任务", "category": "TASK", "riskLevel": "medium", "write": True, "requiresConfirm": False},
    "save_repair_draft": {"name": "save_repair_draft", "title": "保存修复草稿", "category": "TASK", "riskLevel": "low", "write": True, "requiresConfirm": False},
    "apply_repair_after_confirm": {"name": "apply_repair_after_confirm", "title": "应用修复（需确认）", "category": "TASK", "riskLevel": "high", "write": True, "requiresConfirm": True},
    # KNOWLEDGE_TOOLS
    "query_page_knowledge": {"name": "query_page_knowledge", "title": "查询页面元素和导航路径", "category": "KNOWLEDGE", "riskLevel": "low", "write": False, "requiresConfirm": False},
    "query_failure_knowledge": {"name": "query_failure_knowledge", "title": "匹配历史失败模式", "category": "KNOWLEDGE", "riskLevel": "low", "write": False, "requiresConfirm": False},
    "query_case_history": {"name": "query_case_history", "title": "查询用例执行历史", "category": "KNOWLEDGE", "riskLevel": "low", "write": False, "requiresConfirm": False},
    # CONFIRM_TOOLS
    "confirm_high_risk_action": {"name": "confirm_high_risk_action", "title": "确认高风险动作", "category": "CONFIRM", "riskLevel": "high", "write": True, "requiresConfirm": True},
    "confirm_apply_yaml": {"name": "confirm_apply_yaml", "title": "确认应用 YAML", "category": "CONFIRM", "riskLevel": "high", "write": True, "requiresConfirm": True},
    "confirm_rerun": {"name": "confirm_rerun", "title": "确认重新执行", "category": "CONFIRM", "riskLevel": "medium", "write": True, "requiresConfirm": True},
    "confirm_baseline_update": {"name": "confirm_baseline_update", "title": "确认覆盖基线", "category": "CONFIRM", "riskLevel": "high", "write": True, "requiresConfirm": True},
    "confirm_bug_submit": {"name": "confirm_bug_submit", "title": "确认提交缺陷", "category": "CONFIRM", "riskLevel": "medium", "write": True, "requiresConfirm": True},
}

AGENT_PERMISSION_LEVELS = {
    "READ_ONLY": {"allowed_categories": {"READ", "KNOWLEDGE"}, "max_auto_risk": "low"},
    "AUTO_SAFE": {"allowed_categories": {"READ", "AI", "SONIC", "TASK", "CONFIRM", "KNOWLEDGE"}, "max_auto_risk": "medium"},
    "FULL_AUTO": {"allowed_categories": {"READ", "AI", "SONIC", "TASK", "CONFIRM", "KNOWLEDGE"}, "max_auto_risk": "medium"},
}

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


class AgentContext:
    """Normalized Agent input wrapper for text/Figma/image/mixed sources."""

    def __init__(self, request):
        request = request if isinstance(request, dict) else {}
        source_refs = request.get("sourceRefs") or request.get("source_refs") or {}
        if not isinstance(source_refs, dict):
            source_refs = {}
        source_inputs = request.get("sourceInputs") or request.get("source_inputs") or {}
        if not isinstance(source_inputs, dict):
            source_inputs = {}
        self.text = str(
            request.get("text")
            or request.get("target")
            or request.get("goal")
            or request.get("requirementText")
            or source_inputs.get("requirementText")
            or request.get("requirement")
            or ""
        ).strip()
        self.figma_url = str(
            request.get("figmaUrl")
            or request.get("figma_url")
            or source_refs.get("figmaUrl")
            or source_refs.get("figma_url")
            or source_inputs.get("figmaUrl")
            or source_inputs.get("figma_url")
            or ""
        ).strip()
        images = (
            request.get("images")
            or request.get("imageRefs")
            or source_inputs.get("images")
            or source_refs.get("images")
            or []
        )
        self.images = images if isinstance(images, list) else []
        files = request.get("files") or source_inputs.get("files") or []
        self.files = files if isinstance(files, list) else []
        self.requirement_text = str(
            request.get("requirementText")
            or source_inputs.get("requirementText")
            or ""
        ).strip()
        self.source_inputs = source_inputs
        self.raw = request

    def to_dict(self):
        return {
            "text": self.text,
            "figmaUrl": self.figma_url,
            "requirementText": self.requirement_text,
            "images": self.images,
            "files": self.files,
            "sourceInputs": self.source_inputs,
            "sourceType": str(self.raw.get("sourceType") or self.raw.get("source_type") or "manual").strip().lower(),
            "runnerId": str(self.raw.get("runnerId") or self.raw.get("runner_id") or "").strip(),
            "deviceId": str(self.raw.get("deviceId") or self.raw.get("device_id") or "").strip(),
            "deviceStrategy": str(self.raw.get("deviceStrategy") or self.raw.get("device_strategy") or "").strip(),
        }


class ToolRegistry:
    """Small wrapper around the Agent tool whitelist."""

    def __init__(self, tools):
        self.tools = tools if isinstance(tools, dict) else {}

    def get(self, name, default=None):
        item = self.tools.get(name, default)
        return dict(item) if isinstance(item, dict) else item

    def items(self):
        for name, item in self.tools.items():
            yield name, dict(item) if isinstance(item, dict) else item

    def to_category_list(self):
        categories: Dict[str, List[Dict[str, Any]]] = {}
        for tool_name, tool_def in self.items():
            tool_def = tool_def if isinstance(tool_def, dict) else {}
            cat = tool_def.get("category", "UNKNOWN")
            categories.setdefault(cat, []).append({
                "name": tool_def.get("name", tool_name),
                "title": tool_def.get("title", ""),
                "riskLevel": tool_def.get("riskLevel", "low"),
                "requiresConfirm": tool_def.get("requiresConfirm", False),
            })
        category_order = ["READ", "KNOWLEDGE", "AI", "SONIC", "TASK", "CONFIRM"]
        result: List[Dict[str, Any]] = []
        for cat in category_order:
            if cat in categories:
                result.append({"category": cat, "tools": categories[cat]})
        for cat in sorted(categories):
            if cat not in category_order:
                result.append({"category": cat, "tools": categories[cat]})
        return result


TOOL_REGISTRY = ToolRegistry(AGENT_TOOLS)


def normalize_agent_input(request):
    """Return the canonical Agent input shape used by UI, DAG, and tools."""
    return AgentContext(request).to_dict()

# ---------------------------------------------------------------------------
# Agent Runs CRUD
# ---------------------------------------------------------------------------


def load_agent_runs():
    """加载 Agent 运行历史。"""
    data = read_json_file(AGENT_RUNS_FILE, default={"runs": []})
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("runs") or []
    return []


def save_agent_runs(runs):
    """保存 Agent 运行历史。"""
    write_json_file(AGENT_RUNS_FILE, {"runs": runs})


AGENT_INLINE_BLOB_KEYS = {
    "contentBase64", "base64", "dataUrl", "data", "bytes", "arrayBuffer",
    "fileContent", "rawContent", "blob",
}


def _compact_agent_upload_item(item):
    """Drop raw uploaded bytes from persisted Agent history after source parsing."""
    if not isinstance(item, dict):
        return item, False
    compacted = dict(item)
    changed = False
    removed = {}
    for key in list(compacted.keys()):
        value = compacted.get(key)
        should_remove = key in AGENT_INLINE_BLOB_KEYS
        if key == "content" and isinstance(value, str) and len(value) > 1000:
            should_remove = True
        if key == "text" and isinstance(value, str) and len(value) > 4000:
            compacted["textPreview"] = _clean_agent_source_text(value, limit=1200)
            compacted["hasText"] = True
            should_remove = True
        if not should_remove:
            continue
        if isinstance(value, str):
            removed[key] = len(value)
        else:
            try:
                removed[key] = len(json.dumps(value, ensure_ascii=False))
            except Exception:
                removed[key] = 1
        compacted.pop(key, None)
        changed = True
    if changed:
        compacted["contentRemoved"] = True
        compacted["removedContentBytes"] = int(sum(removed.values()))
        compacted.setdefault("hasBinary", bool(removed))
        compacted.setdefault("note", "原始上传内容已解析并从运行记录移除，避免 Agent 状态刷新变慢")
    return compacted, changed


def _compact_agent_upload_list(items):
    if not isinstance(items, list):
        return items, False
    changed = False
    compacted_items = []
    for item in items:
        compacted, item_changed = _compact_agent_upload_item(item)
        compacted_items.append(compacted)
        changed = changed or item_changed
    return compacted_items, changed


def _compact_agent_run_input_blobs(run):
    """Keep Agent history small once PREPARE_SOURCE has extracted usable context."""
    if not isinstance(run, dict):
        return False
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    if not isinstance(artifacts.get("sourceContext"), dict):
        return False
    changed = False
    containers = [run]
    normalized = run.get("normalizedInput")
    if isinstance(normalized, dict):
        containers.append(normalized)
        source_inputs = normalized.get("sourceInputs")
        if isinstance(source_inputs, dict):
            containers.append(source_inputs)
    source_inputs = run.get("sourceInputs")
    if isinstance(source_inputs, dict):
        containers.append(source_inputs)
    for container in containers:
        if not isinstance(container, dict):
            continue
        for key in ("files", "images", "requirementFiles", "imageRefs"):
            compacted, item_changed = _compact_agent_upload_list(container.get(key))
            if item_changed:
                container[key] = compacted
                changed = True
    if changed:
        artifacts["inputBlobsCompacted"] = True
        artifacts["inputBlobsCompactedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        run["inputSummary"] = _agent_input_summary(run, detailed=True)
    return changed


def _agent_parse_time(value):
    if not value:
        return 0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return time.mktime(time.strptime(str(value)[:19], fmt))
        except Exception:
            continue
    return 0


def _agent_step_by_name(run, step_name):
    for step in run.get("steps") or []:
        if isinstance(step, dict) and step.get("step") == step_name:
            return step
    return None


def _agent_generation_job_is_orphaned(job):
    status = str((job or {}).get("status") or "").strip().lower()
    if status != "running":
        return False
    updated_ts = (
        _agent_parse_time((job or {}).get("updated_at"))
        or _agent_parse_time((job or {}).get("started_at"))
        or _agent_parse_time((job or {}).get("created_at"))
    )
    return bool(updated_ts and updated_ts < AGENT_SERVICE_STARTED_TS - 5)


def _agent_orphaned_generation_message(stage, job):
    """Explain a lost background job without implying the user manually restarted it."""
    stage = str(stage or "生成 YAML").strip()
    raw_message = str((job or {}).get("message") or "").strip()
    is_visual_stage = "视觉" in stage or "校准" in raw_message or "Figma/UI" in raw_message
    if is_visual_stage:
        reason = "视觉模型批次长时间未返回，或生成进程/后台线程被系统中断。"
        action = "可以从生成记录重试；如果仍卡住，建议减少单次送入的 Figma 图片批次或跳过部分视觉校准。"
    else:
        reason = "生成进程/后台线程中断，可能是进程异常退出、系统自动拉起、网络请求长时间无响应或外部模型调用未返回。"
        action = "可以重新发起 Agent，或从生成记录重试。"
    return (
        f"{stage}后台任务已失联，已停止本次 Agent 生成。"
        f"{reason}"
        f"{action}"
    )


def _sync_agent_generation_job_state(run):
    """同步共享生成任务状态，收敛后台线程中断、超时或取消后的 Agent timeline。"""
    if not isinstance(run, dict) or run.get("status") != "RUNNING":
        return False
    current_step = str(run.get("currentStep") or "").strip()
    step = _agent_step_by_name(run, "GENERATE_YAML")
    if current_step != "GENERATE_YAML" and not (step and step.get("status") == "RUNNING"):
        return False

    job_id = _agent_generate_progress_job_id(run)
    try:
        from task_server.services.yaml_service import load_generate_job, update_generate_job
        job = load_generate_job(job_id)
    except Exception:
        job = None
        update_generate_job = None
    if not isinstance(job, dict):
        stall_seconds = safe_int(os.getenv("MIDSCENE_AGENT_TOOL_DISPATCH_STALL_SECONDS"), 180)
        started_ts = _agent_parse_time((step or {}).get("startedAt") or run.get("updatedAt"))
        if started_ts and time.time() - started_ts >= max(60, stall_seconds):
            now = time.strftime("%Y-%m-%dT%H:%M:%S")
            message = (
                "生成 YAML 步骤已进入运行态，但后台生成任务没有创建。"
                "这通常表示 Agent 调度在工具调用前被阻塞或中断，请部署最新修复后重新发起。"
            )
            if step:
                step["status"] = "FAILED"
                step["endedAt"] = now
                step.setdefault("startedAt", now)
                step["summary"] = message
                step["error"] = message
                trace = step.setdefault("liveTrace", [])
                trace.append({
                    "time": _trace_time_text(),
                    "message": message,
                    "status": "FAILED",
                })
                del trace[:-30]
            for later in run.get("steps") or []:
                if isinstance(later, dict) and later.get("status") == "PENDING":
                    later["status"] = "SKIPPED"
                    later["summary"] = "生成 YAML 没有创建后台任务，自动跳过后续步骤"
                    later["startedAt"] = now
                    later["endedAt"] = now
            artifacts = run.setdefault("artifacts", {})
            pipeline = artifacts.setdefault("generationPipeline", {})
            pipeline.update({
                "progressJobId": job_id,
                "error": message,
                "errorDetail": {
                    "type": "tool_dispatch_stalled",
                    "stage": "生成 YAML",
                    "message": message,
                    "suggestion": "部署最新 Agent 调度修复后重新发起；如果仍卡住，查看服务端日志中该 runId 的异常。",
                },
                "jobStatus": "missing",
            })
            artifacts["diagnosis"] = make_diagnosis(
                "Agent 生成 YAML 调度中断",
                "后台生成任务没有创建，后续 YAML 校验和 Runner 调试不会执行。",
                ["部署最新修复后重新发起", "查看服务端日志中该 runId 的异常", "确认 Agent 生成任务目录可写"],
                progressJobId=job_id,
                generationStatus="missing",
                generationStage="生成 YAML",
            )
            run["status"] = "FAILED"
            run["currentStep"] = "GENERATE_YAML"
            run["error"] = message[:500]
            run["updatedAt"] = now
            run.setdefault("logs", []).append({"time": now, "message": message})
            del run["logs"][:-200]
            _refresh_agent_run_progress(run)
            return True
        return False

    status = str(job.get("status") or "").strip().lower()
    orphaned = _agent_generation_job_is_orphaned(job)
    if orphaned and update_generate_job:
        stage = str(job.get("step") or "生成 YAML").strip()
        message = _agent_orphaned_generation_message(stage, job)
        job = update_generate_job(
            job_id,
            status="failed",
            ok=False,
            step=f"{stage}中断",
            message=message,
            error=message,
            error_detail={
                "type": "worker_lost_or_model_stalled",
                "stage": stage,
                "message": message,
                "last_job_message": job.get("message") or "",
                "service_started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(AGENT_SERVICE_STARTED_TS)),
                "job_updated_at": job.get("updated_at") or job.get("started_at") or job.get("created_at") or "",
                "suggestion": "从生成记录重试；如果是大 Figma/长文档，减少无关页面或降低视觉批次数后重试。",
            },
        )
        status = "failed"

    if status not in ("failed", "timeout", "cancelled"):
        return False

    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    stage = str(job.get("step") or "生成 YAML").strip()
    message = str(job.get("error") or job.get("message") or f"{stage}未完成").strip()
    if not message:
        message = f"{stage}未完成"
    detail = job.get("error_detail") or job.get("failure_detail") or {}
    if not isinstance(detail, dict):
        detail = {"message": str(detail)}
    if step:
        step["status"] = "FAILED"
        step["endedAt"] = now
        step.setdefault("startedAt", now)
        step["summary"] = message[:300]
        step["error"] = message[:500]
        trace = step.setdefault("liveTrace", [])
        trace.append({
            "time": _trace_time_text(),
            "message": message,
            "status": "FAILED",
            "progress": job.get("progress"),
        })
        del trace[:-30]
    for later in run.get("steps") or []:
        if isinstance(later, dict) and later.get("status") == "PENDING":
            later["status"] = "SKIPPED"
            later["summary"] = "生成 YAML 未完成，自动跳过后续步骤"
            later["startedAt"] = now
            later["endedAt"] = now
    artifacts = run.setdefault("artifacts", {})
    pipeline = artifacts.setdefault("generationPipeline", {})
    pipeline.update({
        "progressJobId": job_id,
        "error": message,
        "errorDetail": detail,
        "interruptedByWorkerLost": bool(orphaned or detail.get("type") in ("service_restart_interrupted", "worker_lost_or_model_stalled")),
        "interruptedByServiceRestart": bool(detail.get("type") == "service_restart_interrupted"),
        "jobStatus": status,
    })
    artifacts["diagnosis"] = make_diagnosis(
        "Agent 生成 YAML 后台任务中断或超时",
        "当前 Agent 无法继续执行后续 YAML 校验和 Runner 调试。",
        ["从生成记录重试", "必要时减少无关 Figma 页面或大文件后重试", "如果连续卡在同一批视觉校准，降低视觉批次大小或改为只用关键截图"],
        progressJobId=job_id,
        generationStatus=status,
        generationStage=stage,
    )
    run["status"] = "FAILED"
    run["currentStep"] = "GENERATE_YAML"
    run["error"] = message[:500]
    run["updatedAt"] = now
    run.setdefault("logs", []).append({
        "time": now,
        "message": message,
    })
    del run["logs"][:-200]
    return True


def _latest_step_trace_ts(step):
    for item in reversed((step or {}).get("liveTrace") or []):
        ts = _agent_parse_time((item or {}).get("time"))
        if ts:
            return ts
    return _agent_parse_time((step or {}).get("startedAt"))


def _agent_next_pending_step_name(run):
    for item in (run or {}).get("steps") or []:
        if isinstance(item, dict) and item.get("status") == "PENDING":
            return item.get("step") or ""
    return ""


def _recover_completed_running_step(run):
    """Finish a RUNNING step when its tool call already returned.

    This prevents the UI from staying on a completed tool card forever if the
    worker is interrupted between persisting the tool result and finalising the
    step status.
    """
    if not isinstance(run, dict) or run.get("status") != "RUNNING":
        return False, False
    stall_seconds = safe_int(os.getenv("MIDSCENE_AGENT_STEP_FINISH_STALL_SECONDS"), 120)
    now_ts = time.time()
    for step in run.get("steps") or []:
        if not isinstance(step, dict) or step.get("status") != "RUNNING":
            continue
        calls = [item for item in (step.get("toolCalls") or []) if isinstance(item, dict)]
        if not calls:
            continue
        last_call = calls[-1]
        call_status = str(last_call.get("status") or "").upper()
        if call_status not in ("SUCCESS", "SKIPPED", "PARTIAL_FAILED", "WAIT_CONFIRM", "FAILED"):
            continue
        last_ts = _latest_step_trace_ts(step)
        if last_ts and now_ts - last_ts < max(30, stall_seconds):
            continue
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        if call_status == "FAILED":
            step["status"] = "FAILED"
            step["error"] = str(last_call.get("error") or last_call.get("outputSummary") or "工具调用失败")[:500]
        elif call_status == "WAIT_CONFIRM":
            step["status"] = "WAIT_CONFIRM"
            run["status"] = "WAIT_CONFIRM"
            run["currentStep"] = "WAIT_CONFIRM"
        else:
            step["status"] = "PARTIAL_FAILED" if call_status == "PARTIAL_FAILED" else ("SKIPPED" if call_status == "SKIPPED" else "SUCCESS")
        step["endedAt"] = now
        step["durationMs"] = _compute_duration(step)
        step["summary"] = (
            last_call.get("outputSummary")
            or last_call.get("summary")
            or last_call.get("message")
            or step.get("summary")
            or f"{step.get('step') or 'Agent 步骤'} 已完成"
        )
        step.setdefault("liveTrace", []).append({
            "time": _trace_time_text(),
            "message": "工具已返回结果，自动补齐步骤完成状态并继续后续步骤。",
            "status": step.get("status"),
        })
        del step["liveTrace"][:-30]
        if run.get("status") == "RUNNING":
            next_step = _agent_next_pending_step_name(run)
            run["currentStep"] = next_step or "DONE"
        run["updatedAt"] = now
        _refresh_agent_run_progress(run)
        return True, bool(run.get("status") == "RUNNING" and _agent_next_pending_step_name(run))
    return False, False


def _recover_stalled_tool_dispatch_step(run):
    """Requeue a RUNNING step that stalled before the tool was called."""
    if not isinstance(run, dict) or run.get("status") != "RUNNING":
        return False, False
    stall_seconds = safe_int(os.getenv("MIDSCENE_AGENT_TOOL_DISPATCH_STALL_SECONDS"), 180)
    now_ts = time.time()
    for step in run.get("steps") or []:
        if not isinstance(step, dict) or step.get("status") != "RUNNING":
            continue
        if step.get("step") == "GENERATE_YAML":
            continue
        if step.get("toolCalls"):
            continue
        trace = step.get("liveTrace") or []
        messages = [str((item or {}).get("message") or "") for item in trace if isinstance(item, dict)]
        prepared = any("准备调用工具" in item for item in messages)
        called = any("调用工具" in item and "准备调用工具" not in item for item in messages)
        if not prepared or called:
            continue
        last_ts = _latest_step_trace_ts(step)
        if last_ts and now_ts - last_ts < max(30, stall_seconds):
            continue
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        step["status"] = "PENDING"
        step["summary"] = "工具调用前中断，已自动重新排队"
        step["startedAt"] = None
        step["endedAt"] = None
        step["durationMs"] = 0
        step.setdefault("liveTrace", []).append({
            "time": _trace_time_text(),
            "message": "工具调用前长时间没有进入实际调用，自动重新排队并继续执行。",
            "status": "PENDING",
        })
        del step["liveTrace"][:-30]
        run["currentStep"] = step.get("step") or run.get("currentStep")
        run["updatedAt"] = now
        _refresh_agent_run_progress(run)
        return True, True
    return False, False


def recover_stale_agent_runs(limit=None):
    """收敛服务重启/超时后遗留的 RUNNING Agent，避免 UI 假运行。"""
    resume_ids = []
    with AGENT_RUN_LOCK:
        runs = load_agent_runs()
        changed = False
        scan_count = len(runs) if limit is None else max(1, min(len(runs), int(limit or 20)))
        for run in runs[:scan_count]:
            if _sync_agent_generation_job_state(run):
                changed = True
            if _compact_agent_run_input_blobs(run):
                changed = True
            recovered, should_resume = _recover_completed_running_step(run)
            if recovered:
                changed = True
            if should_resume and run.get("runId"):
                resume_ids.append(run.get("runId"))
            recovered, should_resume = _recover_stalled_tool_dispatch_step(run)
            if recovered:
                changed = True
            if should_resume and run.get("runId"):
                resume_ids.append(run.get("runId"))
        if changed:
            save_agent_runs(runs)
    for run_id in dict.fromkeys(resume_ids):
        _start_agent_worker(run_id)
    return runs


def _persisted_agent_run_is_cancelled(run_id):
    """Lightweight cancel check for compatibility callers."""
    run_id = str(run_id or "").strip()
    if not run_id:
        return False
    if os.path.exists(_agent_cancel_marker_path(run_id)):
        return True
    try:
        data = read_json_file(AGENT_RUNS_FILE, default={"runs": []})
        runs = data if isinstance(data, list) else (data.get("runs") or [])
        for item in runs:
            if isinstance(item, dict) and item.get("runId") == run_id:
                return str(item.get("status") or "").upper() == "CANCELLED"
    except Exception:
        return False
    return False


def make_diagnosis(root_cause="", impact="", next_actions=None, **extra):
    diagnosis = {
        "rootCause": str(root_cause or "暂未定位根因"),
        "impact": str(impact or "当前步骤无法安全继续。"),
        "nextActions": [str(item) for item in (next_actions or []) if str(item or "").strip()],
    }
    diagnosis.update({k: v for k, v in extra.items() if v not in (None, "", [])})
    return diagnosis


def attach_diagnosis(target, diagnosis):
    if not isinstance(diagnosis, dict):
        return target
    target["diagnosis"] = diagnosis
    if diagnosis.get("rootCause") and not target.get("error"):
        target["error"] = diagnosis.get("rootCause")
    return target


def get_agent_run(run_id):
    """获取单个 Agent 运行详情。"""
    runs = recover_stale_agent_runs()
    run = next((r for r in runs if r.get("runId") == run_id), None)
    return _agent_run_with_input_summary(run, detailed=True) if run else None


def _agent_cancel_progress_job(run_id, reason="用户取消"):
    try:
        from task_server.services.yaml_service import load_generate_job, update_generate_job
        job_id = f"agent-generate-{_agent_safe_run_file_id({'runId': run_id})}"
        if not load_generate_job(job_id):
            return
        update_generate_job(
            job_id,
            status="cancelled",
            progress=99,
            step="已取消",
            message=str(reason or "用户取消"),
            cancel_reason=str(reason or "用户取消"),
        )
    except Exception:
        pass


def _apply_agent_cancel_state(run, reason="用户取消"):
    if not isinstance(run, dict):
        return run
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    run["status"] = "CANCELLED"
    run["currentStep"] = "CANCELLED"
    run["pendingConfirmations"] = []
    run["error"] = str(reason or "用户取消")
    run["updatedAt"] = now
    for step in run.get("steps") or []:
        state = str(step.get("status") or "").upper()
        if state == "RUNNING":
            step["status"] = "CANCELLED"
            step["summary"] = str(reason or "用户取消")
            step["endedAt"] = now
            step.setdefault("liveTrace", []).append({
                "time": _trace_time_text(),
                "message": str(reason or "用户取消"),
                "status": "CANCELLED",
            })
            del step["liveTrace"][:-30]
        elif state == "PENDING":
            step["status"] = "SKIPPED"
            step["summary"] = "Agent 已取消，跳过"
            step["endedAt"] = now
    _refresh_agent_run_progress(run)
    return run


def create_agent_run(payload):
    """创建新 Agent 运行。

    payload 支持的字段:
        - target / goal: 测试目标描述
        - mode: AUTO_SAFE | FULL_AUTO | SEMI_AUTO
        - appName: 应用名称
        - appPackage/app_package: 应用包名，用于知识库、安装包和 Runner 版本校验
        - platform: android | ios
        - scope: smoke | regression | ...
        - sourceType: manual | requirement | figma | failed_job
        - sourceRefs: 输入来源引用，如 generateJobId / caseSetId / figmaUrl / failedJobId
        - executionMode: RUNNER_JOB | SONIC_SUITE
        - failedJobId: (可选) 关联的失败任务 ID
    """
    run_id = f"agent-{int(time.time() * 1000)}-{secrets.token_hex(4)}"
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    mode = str(payload.get("mode") or "AUTO_SAFE").upper()
    if mode not in ("AUTO_SAFE", "FULL_AUTO", "SEMI_AUTO"):
        mode = "AUTO_SAFE"
    execution_mode = str(payload.get("executionMode") or payload.get("execution_mode") or "RUNNER_JOB").strip().upper()
    if execution_mode not in ("RUNNER_JOB", "SONIC_SUITE"):
        execution_mode = "RUNNER_JOB"
    goal = str(payload.get("target") or payload.get("goal") or "").strip()
    source_type = str(payload.get("sourceType") or payload.get("source_type") or "manual").strip().lower()
    if source_type not in ("manual", "requirement", "figma", "failed_job"):
        source_type = "manual"
    source_refs = payload.get("sourceRefs") or payload.get("source_refs") or {}
    if not isinstance(source_refs, dict):
        source_refs = {}
    risk_hits = [kw for kw in AGENT_RISK_KEYWORDS if kw in goal]
    risk_level = "high" if risk_hits else "low"
    normalized_input = normalize_agent_input(payload)
    if normalized_input.get("figmaUrl") and not source_refs.get("figmaUrl"):
        source_refs["figmaUrl"] = normalized_input.get("figmaUrl")
    app_package = str(
        payload.get("appPackage")
        or payload.get("app_package")
        or source_refs.get("appPackage")
        or source_refs.get("app_package")
        or ""
    ).strip()
    model_provider_id = str(payload.get("modelProviderId") or payload.get("aiProviderId") or "").strip()
    selected_ai_model = str(payload.get("aiModel") or payload.get("model") or "").strip()
    runner_id = str(payload.get("runnerId") or payload.get("runner_id") or "").strip()
    device_id = str(payload.get("deviceId") or payload.get("device_id") or "").strip()
    try:
        from task_server.services import job_service
        device_strategy = job_service.normalize_device_strategy(
            payload.get("deviceStrategy") or payload.get("device_strategy"),
            device_id=device_id,
            runner_id=runner_id,
        )
    except Exception:
        device_strategy = "fixed" if (runner_id or device_id) else "auto"
    steps = [
        {"step": s, "status": "PENDING", "startedAt": None, "endedAt": None, "summary": "", "artifactRefs": []}
        for s in AGENT_RUN_STEPS if s not in ("IDLE", "DONE", "FAILED", "WAIT_CONFIRM")
    ]
    run = {
        "runId": run_id,
        "mode": mode,
        "target": goal,
        "appName": str(payload.get("appName") or "").strip(),
        "appPackage": app_package,
        "app_package": app_package,
        "platform": str(payload.get("platform") or "android").strip(),
        "scope": str(payload.get("scope") or "smoke").strip(),
        "executionMode": execution_mode,
        "runnerId": runner_id,
        "deviceId": device_id,
        "deviceStrategy": device_strategy,
        "sourceType": source_type,
        "sourceRefs": source_refs,
        "normalizedInput": normalized_input,
        "model": str(payload.get("model") or "").strip(),
        "modelProviderId": model_provider_id,
        "aiProviderId": model_provider_id,
        "aiModel": selected_ai_model,
        "status": "RUNNING",
        "currentStep": "PLAN",
        "progress": 0,
        "createdAt": now,
        "updatedAt": now,
        "steps": steps,
        "artifacts": {
            "plan": None,
            "sourceContext": None,
            "matchedCases": [],
            "generatedYaml": None,
            "generatedYamlPath": "",
            "draftPath": "",
            "yamlRefs": [],
            "yamlValidation": None,
            "caseRetrieval": None,
            "impactAnalysis": None,
            "executionPrecheck": None,
            "sonicJob": None,
            "report": None,
            "failureAnalysis": None,
            "diagnosis": None,
            "repairDraft": None,
            "summary": None,
            "bugDraft": None,
        },
        "pendingConfirmations": [],
        "riskLevel": risk_level,
        "riskHits": risk_hits,
        "error": None,
    }
    run["inputSummary"] = _agent_input_summary(run, detailed=True)
    _ensure_business_flow_constraint(run)
    _checkpoint_agent_state(run, "created", "PLAN", "RUNNING")
    # 如果指定了 failedJobId，附加到 run 上
    failed_job_id = payload.get("failedJobId") or payload.get("failed_job_id") or source_refs.get("failedJobId") or source_refs.get("jobId")
    if failed_job_id:
        run["failedJobId"] = str(failed_job_id).strip()

    with AGENT_RUN_LOCK:
        runs = load_agent_runs()
        runs.insert(0, run)
        save_agent_runs(runs[:200])
    return run


def advance_agent_run(run_id):
    """推进 Agent 运行状态机。

    启动后台线程执行步骤，与 midscene-upload.py 保持一致。
    Runner 测试机执行遇到业务风险词只提醒；平台级写操作仍进入 WAIT_CONFIRM。
    """
    with AGENT_RUN_LOCK:
        runs = load_agent_runs()
        run = next((r for r in runs if r.get("runId") == run_id), None)
        if not run:
            return None
        if run.get("status") in ("DONE", "FAILED", "CANCELLED"):
            return run

        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        run["currentStep"] = "PLAN"
        run["status"] = "RUNNING"
        run["progress"] = 0
        run["updatedAt"] = now
        save_agent_runs(runs)

    # Start background step execution
    _start_agent_worker(run_id)
    return run


def preview_agent_plan(payload):
    """预览 Agent 执行计划。"""
    goal = str(payload.get("target") or payload.get("goal") or "").strip()
    app_name = str(payload.get("appName") or "").strip() or "智小白3D APP"
    platform = str(payload.get("platform") or "android").strip()
    scope = str(payload.get("scope") or "smoke").strip()
    mode = str(payload.get("mode") or "AUTO_SAFE").upper()
    risk_hits = [kw for kw in AGENT_RISK_KEYWORDS if kw in goal]
    return {
        "mode": mode,
        "appName": app_name,
        "platform": platform,
        "scope": scope,
        "riskHits": risk_hits,
        "steps": [
            "1. 分析测试目标",
            "2. 整理输入来源",
            "3. 匹配已有用例或生成新用例",
            "4. 生成并校验 Midscene YAML",
            "5. 通过 Windows/Mac Runner 执行已确认 YAML",
            "6. 收集报告并分析失败",
            "7. SCRIPT_ISSUE 生成修复草稿；PRODUCT_BUG 生成缺陷草稿",
            "8. Runner 测试动作风险仅提醒；平台级写操作进入 WAIT_CONFIRM",
            "9. 生成总结报告",
        ],
    }


def cancel_agent_run(run_id, reason="用户取消"):
    """取消 Agent 运行。"""
    _mark_agent_run_cancel_requested(run_id, reason or "用户取消")
    with AGENT_RUN_LOCK:
        runs = load_agent_runs()
        run = next((r for r in runs if r.get("runId") == run_id), None)
        if not run:
            return None
        if run.get("status") in ("DONE", "FAILED", "CANCELLED"):
            return run
        _apply_agent_cancel_state(run, reason or "用户取消")
        save_agent_runs(runs)
    _agent_cancel_progress_job(run_id, reason or "用户取消")
    return run


def delete_agent_run(run_id):
    """Delete a terminal Agent run record from history."""
    run_id = str(run_id or "").strip()
    if not run_id:
        return {"ok": False, "status": 400, "error": "Agent Run ID 不能为空"}
    with AGENT_RUN_LOCK:
        runs = load_agent_runs()
        index = next((idx for idx, item in enumerate(runs) if isinstance(item, dict) and item.get("runId") == run_id), -1)
        if index < 0:
            return {"ok": False, "status": 404, "error": "Agent Run 不存在"}
        run = runs[index]
        status = str(run.get("status") or "").upper()
        if status not in {"DONE", "FINISH", "FAILED", "CANCELLED"}:
            return {
                "ok": False,
                "status": 409,
                "error": "运行中或待确认的 Agent 任务不能直接删除，请先取消运行",
                "run": run,
            }
        removed = runs.pop(index)
        save_agent_runs(runs)
        return {"ok": True, "deleted": True, "runId": run_id, "run": removed}


def _normalize_case_selection_value(value):
    text = str(value or "").replace("\\", "/").strip()
    text = re.sub(r"/+", "/", text)
    return text.strip("/")


def _case_selection_variants(path):
    raw = _normalize_case_selection_value(path)
    variants = {raw} if raw else set()
    if path:
        variants.add(_normalize_case_selection_value(os.path.basename(str(path))))
        module, file = _task_dir_for_path(path)
        if module and file:
            variants.add(_normalize_case_selection_value(f"{module}/{file}"))
        try:
            variants.add(_normalize_case_selection_value(os.path.relpath(str(path), TASK_DIR)))
        except Exception:
            pass
    return {item for item in variants if item}


def _case_matches_selection(path, selected_set):
    if not selected_set:
        return True
    variants = _case_selection_variants(path)
    for selected in selected_set:
        for variant in variants:
            if selected == variant or selected.endswith("/" + variant) or variant.endswith("/" + selected):
                return True
    return False


def _repo_base_dir():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _candidate_search_roots():
    base_dir = _repo_base_dir()
    roots = [TASK_DIR, os.path.join(base_dir, "server-tasks"), os.path.join(base_dir, "midscene-tasks")]
    unique = []
    for root in roots:
        root = os.path.abspath(str(root or ""))
        if root and root not in unique:
            unique.append(root)
    return unique


def _candidate_path_exists(path):
    return bool(path and os.path.exists(path) and os.path.isfile(path))


def _resolve_case_candidate_path(item):
    """Resolve a retrieval candidate into a real YAML path.

    Candidate rel_path values may come from production (/opt/midscene-tasks),
    local fixtures (server-tasks), or UI-shortened paths such as
    ../midscene-tasks/module/file.yaml.  Confirmation should trust the current
    candidate list, not only artifacts.matchedCases.
    """
    if not isinstance(item, dict):
        return ""

    raw_values = []
    for key in ("abs_path", "path", "filePath", "file_path", "rel_path", "relativePath"):
        if item.get(key):
            raw_values.append(str(item.get(key)))
    module = str(item.get("dir_name") or item.get("module") or "").strip()
    file_name = str(item.get("file_name") or item.get("file") or "").strip()
    if module and file_name:
        raw_values.append(f"{module}/{file_name}")
    elif file_name:
        raw_values.append(file_name)

    roots = _candidate_search_roots()
    for raw in raw_values:
        norm = str(raw or "").replace("\\", "/").strip()
        norm = re.sub(r"/+", "/", norm)
        if not norm:
            continue

        if os.path.isabs(norm) and _candidate_path_exists(norm):
            return os.path.abspath(norm)

        marker_resolved = False
        for marker in ("midscene-tasks/", "server-tasks/", "server-tasks-all/"):
            idx = norm.find(marker)
            if idx < 0:
                continue
            rel = norm[idx + len(marker):].strip("/")
            marker_roots = roots
            if marker.startswith("server-tasks"):
                marker_roots = [os.path.join(_repo_base_dir(), marker.rstrip("/"))] + roots
            for root in marker_roots:
                try:
                    candidate = safe_join(root, rel)
                except Exception:
                    continue
                if _candidate_path_exists(candidate):
                    return candidate
            marker_resolved = True
        if marker_resolved:
            continue

        rel = norm.lstrip("./")
        for root in roots:
            try:
                candidate = safe_join(root, rel)
            except Exception:
                continue
            if _candidate_path_exists(candidate):
                return candidate

        base_candidate = os.path.abspath(os.path.join(_repo_base_dir(), norm))
        if _candidate_path_exists(base_candidate):
            return base_candidate

    return ""


def _collect_confirmation_candidate_paths(artifacts, confirmation):
    paths = []

    def add_path(path):
        if not path or path in paths:
            return
        paths.append(path)

    for path in artifacts.get("matchedCases") or []:
        if isinstance(path, str) and path.strip() and not _looks_like_yaml_text(path):
            add_path(os.path.abspath(path) if os.path.isabs(path) else path)

    for ref in artifacts.get("yamlRefs") or []:
        if isinstance(ref, dict) and ref.get("path"):
            add_path(os.path.abspath(ref.get("path")) if os.path.isabs(ref.get("path")) else ref.get("path"))

    retrieval = artifacts.get("caseRetrieval") if isinstance(artifacts.get("caseRetrieval"), dict) else {}
    candidate_items = []
    candidate_items.extend(retrieval.get("candidates") or [])
    candidate_items.extend(retrieval.get("candidateDetails") or [])
    if isinstance(confirmation, dict):
        candidate_items.extend(confirmation.get("candidates") or [])
        if isinstance(confirmation.get("candidate"), dict):
            candidate_items.append(confirmation.get("candidate"))

    for item in candidate_items:
        resolved = _resolve_case_candidate_path(item)
        if resolved:
            add_path(resolved)

    return paths


def _yaml_refs_from_paths(paths):
    refs = []
    seen = set()
    for path in paths:
        if not isinstance(path, str) or not path.strip() or path in seen:
            continue
        module, file_name = _task_dir_for_path(path)
        refs.append({
            "type": "file",
            "module": module,
            "file": file_name,
            "path": path,
            "content": "",
            "confirmed": True,
        })
        seen.add(path)
    return refs


def _confirm_agent_yaml_content(run, artifacts, content, draft_path=""):
    check = validate_agent_yaml_content(content)
    if not check.get("ok"):
        return None, "YAML 草稿校验未通过：" + "；".join(check.get("issues") or [])
    executable_score = score_midscene_yaml_executable(content, generated=True)
    module = clean_agent_module_name(run)
    file_name = clean_agent_yaml_name(run)
    target_path = safe_join(TASK_DIR, module, file_name)
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    write_text_file(target_path, content)
    artifacts["generatedYaml"] = content
    artifacts["generatedYamlPath"] = target_path
    if draft_path:
        artifacts["draftPath"] = draft_path
    artifacts["draftConfirmed"] = True
    artifacts["yamlRefs"] = [{
        "type": "file",
        "module": module,
        "file": file_name,
        "path": target_path,
        "content": "",
        "confirmed": True,
        "executionLevel": executable_score.get("executionLevel"),
        "executableScore": executable_score,
    }]
    artifacts["yamlValidation"] = {
        "ok": True,
        "results": [{**artifacts["yamlRefs"][0], **check, "executableScore": executable_score}],
        "issues": [],
        "executionGate": executable_score,
    }
    _sync_agent_generated_case_groups(artifacts)
    return target_path, ""


def _agent_yaml_validation_state(value=None):
    """Normalize historical yamlValidation payloads before dict merging."""
    if isinstance(value, dict):
        state = dict(value)
        if not isinstance(state.get("results"), list):
            state["results"] = []
        if not isinstance(state.get("issues"), list):
            state["issues"] = [str(state.get("issues"))] if state.get("issues") else []
        state.setdefault("ok", not bool(state.get("issues")))
        return state
    if isinstance(value, list):
        return {
            "ok": False,
            "issues": ["历史 YAML 校验状态不是对象，已自动归一化"],
            "results": value,
        }
    return {"ok": True, "issues": [], "results": []}


def _agent_execution_level(value):
    level = str(value or "").strip().lower()
    return level if level in {"executable", "needs_review", "draft", "manual"} else "draft"


def _agent_case_group_item_from_validation(result):
    result = result if isinstance(result, dict) else {}
    score = result.get("executableScore") if isinstance(result.get("executableScore"), dict) else {}
    level = _agent_execution_level(score.get("level") or score.get("executionLevel") or result.get("level") or result.get("executionLevel"))
    reasons = score.get("reasons")
    if not isinstance(reasons, list):
        reasons = list(score.get("errors") or []) + list(score.get("warnings") or [])
    task_scores = [item for item in (score.get("taskScores") or []) if isinstance(item, dict)]
    first_task = task_scores[0] if task_scores else {}
    explicit_smoke = bool(result.get("smoke") or result.get("is_smoke") or result.get("isSmoke"))
    runner_candidate = bool(
        explicit_smoke
        or result.get("smokeCandidate")
        or result.get("runnerCandidate")
        or score.get("smokeCandidate")
        or first_task.get("smokeCandidate")
    )
    return {
        "name": first_task.get("name") or result.get("target_task_name") or result.get("file") or "未命名用例",
        "module": result.get("module") or "",
        "file": result.get("file") or "",
        "path": result.get("path") or "",
        "ok": bool(result.get("ok", True)),
        "score": int(score.get("score") or 0),
        "level": level,
        "executionLevel": level,
        "priority": first_task.get("priority") or "",
        "smoke": explicit_smoke,
        "smokeCandidate": runner_candidate,
        "runnerCandidate": runner_candidate,
        "mainBusinessChain": bool(first_task.get("mainBusinessChain")),
        "baselineEvidence": bool(score.get("baselineEvidence") or first_task.get("baselineEvidence")),
        "scopeReview": result.get("scopeReview") if isinstance(result.get("scopeReview"), dict) else score.get("scopeReview") if isinstance(score.get("scopeReview"), dict) else {},
        "reasons": [str(item) for item in reasons if str(item or "").strip()][:8],
    }


def _sync_agent_generated_case_groups(artifacts, validation_results=None):
    """Expose generated YAML as executable / review / draft / manual buckets."""
    if not isinstance(artifacts, dict):
        return {}
    if validation_results is None:
        validation = artifacts.get("yamlValidation") if isinstance(artifacts.get("yamlValidation"), dict) else {}
        validation_results = validation.get("results") or []
    groups = {
        "executable_cases": [],
        "needs_review_cases": [],
        "draft_cases": [],
        "manual_cases": [],
    }
    for result in validation_results or []:
        if not isinstance(result, dict):
            continue
        item = _agent_case_group_item_from_validation(result)
        bucket = {
            "executable": "executable_cases",
            "needs_review": "needs_review_cases",
            "manual": "manual_cases",
        }.get(item["level"], "draft_cases")
        groups[bucket].append(item)

    generated_cases = artifacts.get("generatedCases") if isinstance(artifacts.get("generatedCases"), dict) else {}
    existing_manual = generated_cases.get("manual_cases") or generated_cases.get("manualCases") or []
    seen_manual = {
        str(item.get("name") or item.get("title") or item.get("file") or item)[:180]
        for item in groups["manual_cases"]
        if isinstance(item, dict)
    }
    for case in existing_manual if isinstance(existing_manual, list) else []:
        if isinstance(case, dict):
            label = str(case.get("name") or case.get("title") or case.get("case_name") or "人工用例").strip()
            reasons = case.get("reasons") if isinstance(case.get("reasons"), list) else [str(case.get("reason") or "需要人工判断")]
            item = {
                **case,
                "name": label,
                "level": "manual",
                "executionLevel": "manual",
                "score": int(case.get("score") or 0),
                "reasons": reasons,
            }
        else:
            label = str(case or "人工用例").strip()
            item = {"name": label, "level": "manual", "executionLevel": "manual", "score": 0, "reasons": ["需要人工判断"]}
        key = label[:180]
        if key and key not in seen_manual:
            groups["manual_cases"].append(item)
            seen_manual.add(key)

    grouped = {
        **groups,
        "counts": {
            "executable": len(groups["executable_cases"]),
            "needs_review": len(groups["needs_review_cases"]),
            "draft": len(groups["draft_cases"]),
            "manual": len(groups["manual_cases"]),
        },
        "rule": "只有 executable_cases 允许自动下发 Runner；needs_review/draft/manual 只展示或人工处理。",
    }
    artifacts["generatedCaseGroups"] = grouped
    if isinstance(generated_cases, dict):
        generated_cases.update(groups)
        generated_cases["execution_level_counts"] = grouped["counts"]
        artifacts["generatedCases"] = generated_cases
    validation = artifacts.get("yamlValidation")
    if isinstance(validation, dict):
        validation["executionGroups"] = grouped
    return grouped


def _confirm_agent_yaml_content_as_files(run, artifacts, content, draft_path="", reason="auto_confirmed_yaml"):
    """Save an executable generated YAML as confirmed files, splitting multi-task drafts."""
    check = validate_agent_yaml_content(content)
    if not check.get("ok"):
        return [], "YAML 草稿校验未通过：" + "；".join(check.get("issues") or [])
    if pyyaml is None:
        target_path, err = _confirm_agent_yaml_content(run, artifacts, content, draft_path=draft_path)
        return (artifacts.get("yamlRefs") or []) if target_path and not err else [], err
    try:
        parsed = pyyaml.safe_load(str(content or ""))
    except Exception as exc:
        return [], f"YAML 解析失败：{exc}"
    platform, tasks = extract_midscene_tasks(parsed)
    if not tasks:
        return [], "YAML 没有可执行 tasks"
    if len(tasks) <= 1:
        target_path, err = _confirm_agent_yaml_content(run, artifacts, content, draft_path=draft_path)
        if err:
            return [], err
        validation = _agent_yaml_validation_state(artifacts.get("yamlValidation"))
        artifacts["yamlValidation"] = {
            **validation,
            "ok": True,
            "issues": [],
            "results": validation.get("results") or [{**(artifacts.get("yamlRefs") or [{}])[0], **check}],
            "autoConfirmed": True,
            "autoConfirmedFallback": bool(reason and "fallback" in reason),
            "confirmReason": reason,
        }
        _sync_agent_generated_case_groups(artifacts)
        return artifacts.get("yamlRefs") or [], ""

    module = clean_agent_module_name(run)
    base_name = os.path.splitext(clean_agent_yaml_name(run))[0]
    module_dir = safe_join(TASK_DIR, module)
    os.makedirs(module_dir, exist_ok=True)
    refs = []
    results = []
    used_files = set()
    for index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            continue
        task_name = str(task.get("name") or f"用例{index}").strip()
        file_name = clean_filename(f"{base_name}-{index:02d}-{slug_for_file(task_name)}.yaml")
        if file_name in used_files:
            stem, ext = os.path.splitext(file_name)
            file_name = clean_filename(f"{stem}-{index}{ext or '.yaml'}")
        used_files.add(file_name)
        payload = {"tasks": [task]} if platform == "root" else {platform: {"tasks": [task]}}
        yaml_text = pyyaml.safe_dump(payload, allow_unicode=True, sort_keys=False)
        item_check = validate_agent_yaml_content(yaml_text)
        executable_score = score_midscene_yaml_executable(yaml_text, generated=True)
        result = {
            "type": "file",
            "module": module,
            "file": file_name,
            "path": safe_join(module_dir, file_name),
            **item_check,
            "executionLevel": executable_score.get("executionLevel"),
            "executableScore": executable_score,
        }
        results.append(result)
        if not item_check.get("ok"):
            continue
        write_text_file(result["path"], yaml_text)
        refs.append({
            "type": "file",
            "module": module,
            "file": file_name,
            "path": result["path"],
            "content": "",
            "confirmed": True,
            "reason": reason,
            "executionLevel": executable_score.get("executionLevel"),
            "executableScore": executable_score,
        })
    if not refs:
        issues = []
        for result in results:
            issues.extend([f"{result.get('file')}: {issue}" for issue in (result.get("issues") or [])])
        return [], "YAML 文件拆分后校验未通过：" + "；".join(issues or ["没有可确认的 YAML 文件"])
    if draft_path:
        artifacts["draftPath"] = draft_path
    artifacts["generatedYaml"] = content
    artifacts["generatedYamlPath"] = refs[0]["path"]
    artifacts["generatedYamlPaths"] = [item["path"] for item in refs]
    artifacts["draftConfirmed"] = True
    artifacts["requiresConfirm"] = False
    artifacts["yamlRefs"] = refs
    artifacts["yamlValidation"] = {
        "ok": True,
        "results": results,
        "issues": [],
        "autoConfirmed": True,
        "autoConfirmedFallback": bool(reason and "fallback" in reason),
        "confirmReason": reason,
        "splitFileCount": len(refs),
        "taskCount": sum(int(item.get("taskCount") or 0) for item in results if item.get("ok")),
        "executionGate": {
            "executableCount": sum(1 for item in results if (item.get("executableScore") or {}).get("executionLevel") == "executable"),
            "needsReviewCount": sum(1 for item in results if (item.get("executableScore") or {}).get("executionLevel") == "needs_review"),
            "draftCount": sum(1 for item in results if (item.get("executableScore") or {}).get("executionLevel") == "draft"),
            "manualCount": sum(1 for item in results if (item.get("executableScore") or {}).get("executionLevel") == "manual"),
            "results": results,
        },
    }
    _sync_agent_generated_case_groups(artifacts, results)
    return refs, ""


def _confirm_agent_yaml_files(run, artifacts, file_items):
    refs = []
    results = []
    issues = []
    module_default = clean_agent_module_name(run)
    for item in file_items or []:
        if not isinstance(item, dict):
            continue
        module = str(item.get("module") or module_default or "").strip()
        file_name = str(item.get("file") or "").strip()
        path = str(item.get("path") or "").strip()
        if not path and module and file_name:
            path = safe_join(TASK_DIR, module, file_name)
        if not path or not os.path.exists(path):
            issues.append(f"{file_name or path or '未命名 YAML'} 不存在")
            continue
        content = read_text_file(path, "")
        check = validate_agent_yaml_content(content)
        executable_score = score_midscene_yaml_executable(content, generated=True)
        result = {
            "module": module,
            "file": file_name or os.path.basename(path),
            "path": path,
            **check,
            "executionLevel": executable_score.get("executionLevel"),
            "executableScore": executable_score,
        }
        results.append(result)
        if not check.get("ok"):
            issues.extend([f"{result['file']}：{issue}" for issue in (check.get("issues") or ["校验未通过"])])
            continue
        refs.append({
            "type": "file",
            "module": module,
            "file": result["file"],
            "path": path,
            "content": "",
            "confirmed": True,
            "executionLevel": executable_score.get("executionLevel"),
            "executableScore": executable_score,
        })
    if not refs:
        return [], "YAML 文件校验未通过：" + "；".join(issues or ["没有可确认的 YAML 文件"])
    artifacts["generatedYaml"] = ""
    artifacts["generatedYamlPath"] = refs[0]["path"]
    artifacts["generatedYamlPaths"] = [item["path"] for item in refs]
    artifacts["draftConfirmed"] = True
    artifacts["yamlRefs"] = refs
    artifacts["yamlValidation"] = {
        "ok": not issues,
        "results": results,
        "issues": issues,
        "executionGate": {
            "executableCount": sum(1 for item in results if (item.get("executableScore") or {}).get("executionLevel") == "executable"),
            "needsReviewCount": sum(1 for item in results if (item.get("executableScore") or {}).get("executionLevel") == "needs_review"),
            "draftCount": sum(1 for item in results if (item.get("executableScore") or {}).get("executionLevel") == "draft"),
            "manualCount": sum(1 for item in results if (item.get("executableScore") or {}).get("executionLevel") == "manual"),
        },
    }
    _sync_agent_generated_case_groups(artifacts, results)
    return refs, "" if not issues else "；".join(issues)


def confirm_agent_step(run_id, step_id, decision, payload=None):
    """确认 Agent 待确认步骤。

    Args:
        run_id: Agent 运行 ID
        step_id: 待确认步骤 ID (confirmation id)
        decision: "approve" | "reject"
    """
    payload = payload if isinstance(payload, dict) else {}
    with AGENT_RUN_LOCK:
        runs = load_agent_runs()
        run = next((r for r in runs if r.get("runId") == run_id), None)
        if not run:
            return None
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        confirmation = next(
            (c for c in run.get("pendingConfirmations", []) if c.get("id") == step_id),
            None,
        )

        def next_pending_step_after(anchor_step=""):
            order = {name: idx for idx, name in enumerate(_STEP_ORDER)}
            start = order.get(str(anchor_step or ""), -1)
            for name in _STEP_ORDER[start + 1:]:
                step = next((s for s in run.get("steps", []) if s.get("step") == name), None)
                if step and step.get("status") == "PENDING":
                    return name
            for name in _STEP_ORDER:
                step = next((s for s in run.get("steps", []) if s.get("step") == name), None)
                if step and step.get("status") == "PENDING":
                    return name
            return "DONE"

        decision_key = str(decision or "").strip().lower()
        approve_keys = (
            "approve", "approved", "confirm", "confirmed", "yes", "true", "1",
            "continue", "confirm_case_reuse", "confirm_yaml_draft",
            "confirm_run", "confirm_bug", "confirm_bug_draft",
            "apply_baseline", "apply_repair_and_rerun",
        )
        if not confirmation:
            artifacts = run.setdefault("artifacts", {})
            if run.get("status") == "WAIT_CONFIRM" and (artifacts.get("draftPath") or artifacts.get("generatedYaml")):
                confirmation = {
                    "id": step_id or f"confirm-{int(time.time())}",
                    "type": "generated_yaml_draft",
                    "action": "confirm_yaml_draft",
                    "draftPath": artifacts.get("draftPath") or "",
                    "createdAt": now,
                }
            elif decision_key in approve_keys and not (run.get("pendingConfirmations") or []):
                next_step = next_pending_step_after(run.get("lastConfirmedStep") or run.get("currentStep") or "")
                if next_step == "DONE":
                    run["status"] = "DONE"
                    run["currentStep"] = "DONE"
                    run["progress"] = 100
                else:
                    run["status"] = "RUNNING"
                    run["currentStep"] = next_step
                run["updatedAt"] = now
                save_agent_runs(runs)
                return run
            else:
                return {"error": "确认项不存在", "run": run}
        generate_draft_keys = (
            "generate_yaml_draft", "generate_draft", "new_yaml",
            "create_yaml_draft", "reject_case_reuse",
        )
        approved = decision_key in approve_keys
        draft_requested = decision_key in generate_draft_keys
        rejected = decision_key in ("reject", "rejected", "cancel", "cancelled", "no", "false", "0")
        confirmation["decision"] = "generate_draft" if draft_requested else ("approve" if approved else ("reject" if rejected else decision))
        confirmation["decidedAt"] = now
        ctype = str(confirmation.get("type") or "")

        def mark_step_skipped(step_name, summary):
            for step in run.get("steps", []):
                if step.get("step") == step_name and step.get("status") == "PENDING":
                    step["status"] = "SKIPPED"
                    step["summary"] = summary
                    step["startedAt"] = now
                    step["endedAt"] = now
                    step["liveTrace"] = [{
                        "time": _trace_time_text(),
                        "message": summary,
                        "status": "SKIPPED",
                    }]

        def mark_step_success(step_name, summary):
            for step in run.get("steps", []):
                if step.get("step") == step_name:
                    step["status"] = "SUCCESS"
                    step["summary"] = summary
                    step["endedAt"] = now
                    step.setdefault("startedAt", now)
                    trace = step.setdefault("liveTrace", [])
                    trace.append({
                        "time": _trace_time_text(),
                        "message": summary,
                        "status": "SUCCESS",
                    })
                    break

        if draft_requested and ctype in ("case_retrieval_confirm", "case_match_uncertain"):
            artifacts = run.setdefault("artifacts", {})
            artifacts["matchedCases"] = []
            artifacts["matchedCount"] = 0
            artifacts["yamlRefs"] = []
            artifacts["generatedYamlPath"] = ""
            artifacts["matchReason"] = "用户选择不复用匹配到的已有用例，改为生成新的 YAML 草稿"
            retrieval = artifacts.setdefault("caseRetrieval", {})
            retrieval["decision"] = "generate_draft_by_user"
            retrieval["userDecision"] = "generate_yaml_draft"
            mark_step_skipped("MATCH_CASES", "用户选择不复用已有用例，跳过旧匹配逻辑，直接进入 YAML 草稿生成")
            run["pendingConfirmations"] = [
                c for c in run.get("pendingConfirmations", []) if c.get("id") != step_id
            ]
            run["status"] = "RUNNING"
            run["currentStep"] = "GENERATE_YAML"
            run["updatedAt"] = now
        elif approved:
            artifacts = run.setdefault("artifacts", {})
            if ctype == "generated_yaml_draft":
                draft_path = confirmation.get("draftPath") or artifacts.get("draftPath") or ""
                if draft_path and os.path.exists(draft_path):
                    content = read_text_file(draft_path, "")
                else:
                    content = artifacts.get("generatedYaml") or ""
                if not content.strip():
                    return {"error": "YAML 草稿不存在，无法确认", "run": run}
                refs, err = _confirm_agent_yaml_content_as_files(
                    run,
                    artifacts,
                    content,
                    draft_path=draft_path,
                    reason="manual_confirmed_yaml_draft",
                )
                if err:
                    return {"error": err, "run": run}
                mark_step_success("GENERATE_YAML", f"已人工确认 YAML 草稿，转为 {len(refs)} 个正式 YAML，继续校验并交给 Runner 执行")
            elif ctype in ("case_retrieval_confirm", "case_match_uncertain"):
                selected_cases = payload.get("selectedCases")
                if isinstance(selected_cases, list):
                    selected_set = {
                        _normalize_case_selection_value(item)
                        for item in selected_cases
                        if _normalize_case_selection_value(item)
                    }
                    if not selected_set:
                        return {"error": "请至少选择一条要回归的用例", "run": run}

                    candidate_paths = _collect_confirmation_candidate_paths(artifacts, confirmation)
                    matched_paths = [
                        path for path in candidate_paths
                        if _case_matches_selection(path, selected_set)
                    ]
                    existing_refs = [
                        ref for ref in artifacts.get("yamlRefs") or []
                        if _case_matches_selection(
                            ref.get("path") or "/".join([str(ref.get("module") or ""), str(ref.get("file") or "")]),
                            selected_set,
                        )
                    ]
                    if not matched_paths and not existing_refs:
                        return {
                            "error": "选择的用例不在当前候选清单中，可能是确认项已过期或候选路径无法解析，请刷新确认项后重试",
                            "run": run,
                        }
                    if matched_paths:
                        existing_refs = _yaml_refs_from_paths(matched_paths)
                    artifacts["matchedCases"] = matched_paths
                    artifacts["yamlRefs"] = existing_refs
                    artifacts["matchedCount"] = len(matched_paths) if matched_paths else len(existing_refs)
                    retrieval = artifacts.setdefault("caseRetrieval", {})
                    retrieval["selectedCases"] = sorted(selected_set)
                    retrieval["selectedResolvedPaths"] = matched_paths
                    retrieval["selectedCount"] = artifacts["matchedCount"]

                refs = normalize_yaml_refs(run)
                for ref in refs:
                    if ref.get("type") == "file":
                        ref["confirmed"] = True
                artifacts["yamlRefs"] = refs
                artifacts["caseReuseConfirmed"] = True
                mark_step_skipped("MATCH_CASES", "已确认复用 Case Retrieval 命中的 YAML，跳过旧匹配逻辑")
            elif ctype == "high_risk_action":
                run["riskConfirmed"] = True
                run["lastConfirmedStep"] = "RISK_REVIEW"
            elif ctype == "unknown_failure":
                run["unknownFailureConfirmed"] = True
                run["lastConfirmedStep"] = "ANALYZE_FAILURE"
            run["pendingConfirmations"] = [
                c for c in run.get("pendingConfirmations", []) if c.get("id") != step_id
            ]
            run["status"] = "RUNNING"
            if ctype == "high_risk_action":
                run["currentStep"] = next_pending_step_after("RISK_REVIEW")
            elif ctype == "unknown_failure":
                run["currentStep"] = next_pending_step_after("ANALYZE_FAILURE")
            elif run.get("currentStep") == "WAIT_CONFIRM":
                run["currentStep"] = next_pending_step_after("GENERATE_YAML")
            run["updatedAt"] = now
        elif rejected:
            _apply_agent_cancel_state(run, f"用户拒绝确认：{confirmation.get('type', '')}")
            _agent_cancel_progress_job(run.get("runId", ""), run.get("error") or "用户拒绝确认")

        save_agent_runs(runs)
        return run


# ---------------------------------------------------------------------------
# Agent Tool Calls CRUD
# ---------------------------------------------------------------------------


def load_agent_tool_calls(run_id=None):
    """加载 Agent 工具调用记录。

    Args:
        run_id: 可选，过滤指定 run 的调用记录
    """
    data = read_json_file(AGENT_TOOL_CALLS_FILE, default={"calls": []})
    if isinstance(data, list):
        calls = data
    elif isinstance(data, dict):
        calls = data.get("calls") or []
    else:
        calls = []
    if run_id:
        calls = [c for c in calls if c.get("runId") == run_id]
    return calls


def save_agent_tool_calls(calls):
    """保存 Agent 工具调用记录。"""
    write_json_file(AGENT_TOOL_CALLS_FILE, {"calls": calls if isinstance(calls, list) else []})


def create_tool_call(run_id, tool_name, input_data, risk_level=None):
    """创建工具调用记录。"""
    tool_def = TOOL_REGISTRY.get(tool_name, {})
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    call = {
        "callId": f"tc-{int(time.time() * 1000)}-{secrets.token_hex(3)}",
        "runId": run_id,
        "traceId": f"trace-{secrets.token_hex(6)}",
        "toolName": tool_name,
        "category": tool_def.get("category", "UNKNOWN"),
        "riskLevel": risk_level or tool_def.get("riskLevel", "low"),
        "requiresConfirm": tool_def.get("requiresConfirm", False),
        "status": "RUNNING",
        "input": input_data if isinstance(input_data, dict) else {},
        "outputSummary": "",
        "error": None,
        "startedAt": now,
        "endedAt": None,
    }
    return call


def complete_tool_call(call, status, output_summary, error=None):
    """完成工具调用记录（与 midscene-upload.py 签名一致）。"""
    call["status"] = status
    call["outputSummary"] = output_summary if isinstance(output_summary, str) else json.dumps(output_summary, ensure_ascii=False)[:500]
    call["error"] = error
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return call


# ---------------------------------------------------------------------------
# Tool permission helpers
# ---------------------------------------------------------------------------


def tool_requires_confirm(tool_def, run):
    """判断工具是否需要人工确认才能执行。"""
    if tool_def.get("requiresConfirm"):
        return True
    if run.get("riskHits") and tool_def.get("write"):
        tool_name = str(tool_def.get("name") or "").strip()
        if _agent_execution_mode(run) == "RUNNER_JOB" and tool_name in {
            "create_runner_job", "run_midscene_task", "retry_failed_job", "save_repair_draft"
        }:
            return False
        return True
    risk = tool_def.get("riskLevel", "low")
    perm = AGENT_PERMISSION_LEVELS.get(
        run.get("permissionLevel", run.get("mode", "AUTO_SAFE")),
        AGENT_PERMISSION_LEVELS["AUTO_SAFE"],
    )
    if RISK_ORDER.get(risk, 0) > RISK_ORDER.get(perm.get("max_auto_risk", "medium"), 1):
        return True
    return False


def can_execute_tool(tool_def, run):
    """判断当前权限是否允许执行指定工具。"""
    perm = AGENT_PERMISSION_LEVELS.get(
        run.get("permissionLevel", run.get("mode", "AUTO_SAFE")),
        AGENT_PERMISSION_LEVELS["AUTO_SAFE"],
    )
    cat = tool_def.get("category", "UNKNOWN")
    if cat not in perm.get("allowed_categories", set()):
        return False
    eligibility = _record_tool_eligibility(run, tool_def)
    if not eligibility.get("allowed", True):
        return False
    return True


# ---------------------------------------------------------------------------
# Tool execution entry point
# ---------------------------------------------------------------------------


def execute_tool(run, tool_name, input_data):
    """Execute a whitelisted tool and return the call record.

    与 midscene-upload.py 的 execute_tool 保持一致。
    """
    tool_def = TOOL_REGISTRY.get(tool_name)
    if not tool_def:
        raise ValueError(f"未知工具：{tool_name}")
    if not can_execute_tool(tool_def, run):
        raise ValueError(
            f"权限不足：{run.get('permissionLevel', run.get('mode'))} 不允许调用 {tool_name}"
        )
    call = create_tool_call(run.get("runId", ""), tool_name, input_data)
    constraint = _ensure_business_flow_constraint(run)
    call["businessFlowConstraint"] = _compact_business_flow_constraint(constraint)
    call["toolEligibility"] = (run.get("artifacts") or {}).get("toolEligibility", {}).get(tool_name)
    handler = AGENT_TOOL_HANDLERS.get(tool_name)
    try:
        if handler:
            result = handler(run, input_data)
        else:
            result = {"message": f"{tool_name} 已执行", "toolName": tool_name}
        complete_tool_call(call, "SUCCESS", result)
    except Exception as e:
        complete_tool_call(call, "FAILED", "", str(e))
    # Persist tool call
    with AGENT_TOOL_CALL_LOCK:
        calls = load_agent_tool_calls()
        calls.insert(0, call)
        save_agent_tool_calls(calls[:500])
    return call


# ---------------------------------------------------------------------------
# Helper functions (consistent with midscene-upload.py)
# ---------------------------------------------------------------------------

def _compute_duration(step_or_call):
    """计算步骤或调用的耗时（毫秒）。"""
    started = step_or_call.get("startedAt") or step_or_call.get("started_at", "")
    ended = step_or_call.get("endedAt") or step_or_call.get("ended_at", "")
    if not started or not ended:
        return 0
    try:
        fmt = "%Y-%m-%dT%H:%M:%S"
        t1 = time.mktime(time.strptime(started[:19], fmt))
        t2 = time.mktime(time.strptime(ended[:19], fmt))
        return int((t2 - t1) * 1000)
    except Exception:
        return 0


def _log_tool_call(call, run_id):
    """写入工具调用审计日志到 /opt/midscene-task-data/agent-tool-calls.jsonl。"""
    call_record = dict(call)
    call_record["runId"] = run_id
    call_record["traceId"] = run_id
    if "input" in call_record and isinstance(call_record["input"], dict):
        for key in ["apiKey", "token", "password", "sonicToken", "secret", "signature"]:
            call_record["input"].pop(key, None)
    log_path = "/opt/midscene-task-data/agent-tool-calls.jsonl"
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(call_record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _agent_log(run, message):
    """Append a lightweight trace entry to an Agent run without failing the step."""
    if not isinstance(run, dict):
        return
    trace = run.setdefault("trace", [])
    trace.append({
        "time": _trace_time_text(),
        "message": str(message or ""),
    })
    del trace[:-80]


def _persist_agent_run_snapshot(run):
    """Persist the in-memory Agent run so the page can show live progress."""
    if not isinstance(run, dict) or not run.get("runId"):
        return
    try:
        runs = load_agent_runs()
        for i, item in enumerate(runs):
            if item.get("runId") == run.get("runId"):
                runs[i] = run
                break
        save_agent_runs(runs)
    except Exception:
        pass


def _append_step_trace(run, step, message, **extra):
    """Record a visible trace line on the current timeline step."""
    if not isinstance(step, dict):
        return
    row = {
        "time": _trace_time_text(),
        "message": str(message or ""),
    }
    if extra:
        row.update({k: v for k, v in extra.items() if v not in (None, "")})
    step.setdefault("liveTrace", []).append(row)
    del step["liveTrace"][:-30]
    _agent_log(run, f"{step.get('step', '')}: {message}")
    _persist_agent_run_snapshot(run)


def _risk_match_snippet(text, keyword, radius=70):
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    keyword = str(keyword or "").strip()
    if not compact:
        return ""
    if not keyword:
        return compact[: radius * 2]
    idx = compact.find(keyword)
    if idx < 0:
        return compact[: radius * 2]
    start = max(0, idx - radius)
    end = min(len(compact), idx + len(keyword) + radius)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(compact) else ""
    return f"{prefix}{compact[start:end]}{suffix}"


def _risk_yaml_source_label(ref):
    module = str(ref.get("module") or "").strip()
    file_name = str(ref.get("file") or "").strip()
    path = str(ref.get("path") or "").strip()
    ref_type = str(ref.get("type") or "").strip()
    if module or file_name:
        joined = "/".join(part for part in (module, file_name) if part)
        return f"YAML：{joined}"
    if path:
        return f"YAML：{os.path.basename(path)}"
    return "生成 YAML 草稿" if ref_type in ("draft", "text") else "YAML 内容"


def _risk_source_items(run):
    items = []
    seen = set()

    def push(label, text):
        value = str(text or "").strip()
        if not value:
            return
        key = (label, value[:500])
        if key in seen:
            return
        seen.add(key)
        items.append({"source": label, "text": value})

    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    source_context = artifacts.get("sourceContext") if isinstance(artifacts.get("sourceContext"), dict) else {}
    normalized = run.get("normalizedInput") if isinstance(run.get("normalizedInput"), dict) else {}
    push("测试目标", run.get("target") or run.get("goal") or run.get("summary"))
    push("需求说明", source_context.get("requirementText") or normalized.get("requirementText") or run.get("requirementText"))
    push("Figma 文本", source_context.get("figmaText"))
    try:
        for ref in normalize_yaml_refs(run):
            push(_risk_yaml_source_label(ref), _yaml_ref_content(ref)[:12000])
    except Exception:
        pass
    return items


def _evaluate_risk_detail(run):
    """评估风险等级，并返回命中来源，避免只显示一个孤立关键词。"""
    sources = _risk_source_items(run)
    non_blocking_detail = None
    for kw in AUTO_AGENT_RISK_KEYWORDS:
        for item in sources:
            text = item.get("text") or ""
            if kw in text:
                snippet = _risk_match_snippet(text, kw)
                if _risk_hit_is_requirement_background(kw, item.get("source"), snippet):
                    non_blocking_detail = non_blocking_detail or {
                        "level": "LOW",
                        "keyword": kw,
                        "source": item.get("source") or "未知来源",
                        "snippet": snippet,
                        "blocking": False,
                        "classification": "requirement_background",
                        "reason": "这是产品改版/需求背景描述，不是 Runner 将执行的危险动作，不阻断执行。",
                    }
                    continue
                return {
                    "level": "HIGH",
                    "keyword": kw,
                    "source": item.get("source") or "未知来源",
                    "snippet": snippet,
                    "blocking": True,
                }
    if non_blocking_detail:
        return non_blocking_detail
    return {"level": "LOW", "keyword": "", "source": "", "snippet": ""}


def _risk_hit_is_requirement_background(keyword, source, snippet):
    """把需求背景里的产品改版词从执行高风险里剥离出来。"""
    kw = str(keyword or "").strip()
    src = str(source or "").strip()
    text = str(snippet or "").strip()
    if kw != "删除" or src not in ("需求说明", "测试目标", "Figma 文本"):
        return False
    compact = re.sub(r"\s+", "", text)
    action_markers = (
        "点击删除", "点删除", "删除按钮", "确认删除", "执行删除", "批量删除",
        "删除作品", "删除记录", "删除文件", "删除任务", "删除数据", "删除账号",
        "删除订单", "删除模型", "删除素材", "删除草稿", "清空", "重置",
    )
    if any(marker in compact for marker in action_markers):
        return False
    requirement_markers = (
        "新增模块", "删除老模块", "删除旧模块", "老模块", "旧模块", "原模块",
        "入口", "导航栏", "首页", "卡片", "模块", "功能区", "页面",
        "整合为", "合并", "改为", "替换", "下线", "隐藏", "去掉",
        "文案", "排序", "设计稿", "需求", "调整", "迁移", "改版",
    )
    return any(marker in compact for marker in requirement_markers)


def _risk_detail_summary(detail, fallback_keyword=""):
    keyword = str((detail or {}).get("keyword") or fallback_keyword or "").strip()
    if not keyword:
        return "无高风险动作"
    source = str((detail or {}).get("source") or "未知来源").strip()
    snippet = str((detail or {}).get("snippet") or "").strip()
    if (detail or {}).get("blocking") is False:
        reason = str((detail or {}).get("reason") or "仅作为需求背景记录，不阻断执行。").strip()
        summary = f"需求背景关键词：{keyword}；来源：{source}；说明：{reason}"
    else:
        summary = f"命中高风险动作：{keyword}；来源：{source}"
    if snippet:
        summary += f"；触发片段：{snippet}"
    return summary


def _evaluate_risk(run):
    """评估风险等级。"""
    detail = _evaluate_risk_detail(run)
    return detail.get("level") or "LOW", detail.get("keyword") or None


def _runner_precheck_should_warn_risk(run, hit_kw):
    """Runner 测试机执行的业务风险词只提醒，不阻断。

    这里处理的是 App 内的测试步骤（例如删除旧模块、清空筛选、重置表单）。
    覆盖基线、批量同步 Sonic、应用修复等平台级写操作仍由对应确认入口拦截。
    """
    if _agent_execution_mode(run) != "RUNNER_JOB":
        return False
    hit = str(hit_kw or "").strip()
    if not hit:
        return False
    return True


def _agent_compact_runner_text(text, limit=180):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return ""
    lowered = text.lower()
    if "replanned" in lowered and "exceed" in lowered:
        text = "Midscene 重规划超限：replanningCycleLimit 已达到上限"
    elif "timeout after 300s" in lowered:
        text = "Runner 单任务超时：Midscene 300s 内未完成"
    elif "failed to locate element" in lowered:
        text = "元素定位失败：" + text[text.lower().find("failed to locate element"):]
    elif "waitfor timeout" in lowered:
        text = "等待目标超时：" + text[text.lower().find("waitfor timeout"):]
    elif "assertion failed" in lowered:
        text = "断言不通过：" + text[text.lower().find("assertion failed"):]
    elif "report finalized" in lowered:
        text = "报告已生成，但任务结果失败"
    elif "adb " in lowered or "screencap" in lowered or "pull " in lowered:
        text = "ADB 截图/拉取中，Runner 尚未回传最终结果"

    text = re.sub(r"[A-Za-z]:\\[^ ]+", lambda m: os.path.basename(m.group(0).replace("\\", "/")), text)
    text = re.sub(r"/[^ ]{20,}", lambda m: os.path.basename(m.group(0)), text)
    if len(text) > limit:
        text = text[: max(0, limit - 1)].rstrip() + "…"
    return text


def _agent_job_log_tail(value, limit=180):
    text = str(value or "").replace("\r\n", "\n").strip()
    if not text:
        return ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        text = " / ".join(lines[-6:])
    return _agent_compact_runner_text(text, limit=limit)


def _agent_job_error_excerpt(value, limit=180):
    text = str(value or "").replace("\r\n", "\n").strip()
    if not text:
        return ""
    lowered = text.lower()
    patterns = [
        "failed to locate element",
        "waitfor timeout",
        "assertion failed",
        "invalid parameters",
        "replanned",
        "serviceerror",
        "error:",
    ]
    for pattern in patterns:
        idx = lowered.find(pattern)
        if idx < 0:
            continue
        snippet = text[idx:idx + limit]
        return _agent_compact_runner_text(snippet, limit=limit)
    return _agent_job_log_tail(text, limit=limit)


def _agent_job_field(job, snake_key, camel_key=None):
    if not isinstance(job, dict):
        return ""
    if snake_key in job and job.get(snake_key) not in (None, ""):
        return job.get(snake_key)
    if camel_key and camel_key in job and job.get(camel_key) not in (None, ""):
        return job.get(camel_key)
    return ""


def _agent_job_failure_reason(job):
    candidates = [
        ("error", None, "错误"),
        ("report_upload_error", "reportUploadError", "报告上传"),
        ("report_missing_reason", "reportMissingReason", "报告缺失"),
        ("upload_warning", "uploadWarning", "报告警告"),
        ("stderr_tail", "stderrTail", "Runner 错误"),
        ("stdout_tail", "stdoutTail", "Runner 日志"),
        ("progress_message", "progressMessage", "执行进度"),
    ]
    status = str(_agent_job_field(job, "status") or "").strip()
    raw_text = "\n".join(
        str(_agent_job_field(job, snake_key, camel_key) or "")
        for snake_key, camel_key, _label in candidates
    )
    failure_type = _agent_job_failure_type(raw_text)
    for snake_key, camel_key, label in candidates:
        raw_reason = _agent_job_field(job, snake_key, camel_key)
        reason = _agent_job_error_excerpt(raw_reason) if snake_key in ("stderr_tail", "stdout_tail") else _agent_job_log_tail(raw_reason)
        if not reason:
            continue
        if snake_key == "progress_message" and reason.lower() in {"failed", "fail", "error", "timeout"}:
            continue
        prefix = f"{failure_type}：" if failure_type else ""
        return f"{prefix}{label}: {reason}"
    if status:
        return f"Runner 回传状态：{status}"
    return "Runner 已回传失败，但未带具体错误；请打开报告或查看 Runner 控制台日志。"


def _agent_job_failure_type(text):
    blob = str(text or "")
    lowered = blob.lower()
    if "replanned 5 times" in lowered or "replanningcyclelimit" in lowered:
        return "Midscene 重规划超限"
    if "timeout after 300s" in lowered:
        return "Runner 单任务超时"
    if "failed to locate element" in lowered:
        return "元素定位失败"
    if "waitfor timeout" in lowered or "assertion failed" in lowered:
        if any(term in blob for term in ("并未出现", "未出现", "无法确认", "陈述为假", "StatementIsTruthy", "当前页面", "截图内容")):
            return "断言/页面状态不匹配"
        return "等待目标超时"
    if "adb" in lowered and ("device" in lowered or "offline" in lowered):
        return "ADB/设备异常"
    return ""


def _agent_job_failure_target(job):
    task_name = _agent_job_field(job, "target_task_name", "targetTaskName") or _agent_job_field(job, "current_task_name", "currentTaskName")
    module = str(_agent_job_field(job, "module") or "").strip()
    file_name = str(_agent_job_field(job, "file") or "").strip()
    if task_name:
        return str(task_name)
    if module or file_name:
        return "/".join(part for part in (module, file_name) if part)
    return str(_agent_job_field(job, "job_id", "jobId") or "Runner 任务")


def _agent_job_failure_reasons(jobs, limit=5):
    reasons = []
    for job in jobs or []:
        if not isinstance(job, dict):
            continue
        reason = _agent_job_failure_reason(job)
        raw_text = "\n".join(
            str(_agent_job_field(job, key, camel) or "")
            for key, camel in (
                ("error", None),
                ("stderr_tail", "stderrTail"),
                ("stdout_tail", "stdoutTail"),
                ("report_missing_reason", "reportMissingReason"),
                ("report_upload_error", "reportUploadError"),
            )
        )
        reasons.append({
            "jobId": _agent_job_field(job, "job_id", "jobId"),
            "target": _agent_job_failure_target(job),
            "reason": reason,
            "failureType": _agent_job_failure_type(raw_text),
            "status": _agent_job_field(job, "status"),
            "runnerId": _agent_job_field(job, "runner_id", "runnerId"),
            "deviceId": _agent_job_field(job, "device_id", "deviceId"),
            "reportUrl": _agent_job_field(job, "report_url", "reportUrl"),
        })
        if len(reasons) >= limit:
            break
    return reasons


def _agent_smoke_failure_bucket(failure_reasons, dry_run_blocked=None):
    return classify_generated_yaml_failure_bucket(failure_reasons, dry_run_blocked)


def _agent_smoke_execution_blocker(failure_reasons, dry_run_blocked=None, smoke_total=0, smoke_failed=0, timeout_count=0):
    """Return whether smoke execution proves the generated YAML is not runnable.

    A smoke case does not have to pass as a product result. It must be able to
    pass local/Runner dry-run, get dispatched, run on the device, and produce a
    concrete result. Product assertions or page-state mismatches are execution
    results, not automatic blockers for the rest of the generated suite.
    """
    return classify_generated_yaml_smoke_blocker(
        failure_reasons,
        dry_run_blocked,
        smoke_total=smoke_total,
        smoke_failed=smoke_failed,
        timeout_count=timeout_count,
    )


def _ai_gateway_available():
    """检查 AI Gateway 是否可用。"""
    try:
        url = AI_GATEWAY_URL.rstrip("/") + "/health"
        return http_client.get(url, timeout=3).status == 200
    except Exception:
        return False


def _ai_gateway_post(path, payload, timeout=30):
    """对 AI Gateway 发起 POST 请求（默认 30 秒超时）。"""
    url = AI_GATEWAY_URL.rstrip("/") + path
    try:
        resp = http_client.post_json(url, payload if isinstance(payload, dict) else {}, timeout=timeout)
        return resp.json(default={}) if resp.ok else None
    except Exception as e:
        return None


def _probe_agent_ai_health(run=None):
    """Return AI availability without exposing secrets."""
    health = {
        "gatewayUrl": AI_GATEWAY_URL,
        "gatewayReachable": False,
        "dashscopeConfigured": bool(dashscope_api_key(required=False)),
        "dashscopeBaseUrl": dashscope_base_url(),
        "textModel": dashscope_text_model(),
        "selectedProviderId": "",
        "selectedModel": "",
        "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "errors": [],
    }
    if isinstance(run, dict):
        health["selectedProviderId"] = str(run.get("modelProviderId") or run.get("aiProviderId") or "")
        health["selectedModel"] = str(run.get("aiModel") or run.get("model") or "")
    try:
        url = AI_GATEWAY_URL.rstrip("/") + "/health"
        resp = http_client.get(url, timeout=3)
        health["gatewayReachable"] = resp.status == 200
        if not health["gatewayReachable"]:
            health["errors"].append(f"AI Gateway HTTP {resp.status}")
    except Exception as exc:
        health["errors"].append(f"AI Gateway 不可用：{str(exc)[:160]}")
    health["ready"] = bool(health["gatewayReachable"] or health["dashscopeConfigured"])
    if isinstance(run, dict):
        run.setdefault("artifacts", {})["agentAiHealth"] = health
    return health


def _record_agent_ai_decision(run, stage, source, ok, summary="", **extra):
    """Persist a compact AI decision trail for Agent observability."""
    if not isinstance(run, dict):
        return {}
    decision = {
        "stage": str(stage or ""),
        "source": str(source or "unknown"),
        "ok": bool(ok),
        "summary": str(summary or "")[:500],
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    decision.update({k: v for k, v in extra.items() if v not in (None, "", [])})
    trail = run.setdefault("artifacts", {}).setdefault("agentAiDecisions", [])
    trail.append(decision)
    del trail[:-50]
    return decision


def _checkpoint_agent_state(run, label, step_name="", status=""):
    """Persist compact checkpoints for resumability and postmortem debugging."""
    if not isinstance(run, dict):
        return {}
    artifacts = run.setdefault("artifacts", {})
    checkpoint = {
        "label": str(label or ""),
        "step": str(step_name or run.get("currentStep") or ""),
        "status": str(status or run.get("status") or ""),
        "progress": _safe_int_local(run.get("progress"), 0),
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "matchedCount": _safe_int_local(artifacts.get("matchedCount"), 0),
        "pendingConfirmations": len(run.get("pendingConfirmations") or []),
        "aiDecisionCount": len(artifacts.get("agentAiDecisions") or []),
        "businessFlowSource": (artifacts.get("businessFlowConstraint") or {}).get("source", "default"),
    }
    artifacts.setdefault("agentCheckpoints", []).append(checkpoint)
    del artifacts["agentCheckpoints"][:-80]
    return checkpoint


def _record_agent_quality_gate(run, gate_name, passed, reason="", **extra):
    """Record deterministic guardrail/evaluator results beside AI decisions."""
    if not isinstance(run, dict):
        return {}
    gate = {
        "gate": str(gate_name or ""),
        "passed": bool(passed),
        "reason": str(reason or "")[:500],
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    gate.update({k: v for k, v in extra.items() if v not in (None, "", [])})
    gates = run.setdefault("artifacts", {}).setdefault("agentQualityGates", [])
    gates.append(gate)
    del gates[:-80]
    return gate


def _evaluate_agent_quality_gate(run, stage, payload):
    """A deterministic evaluator layer inspired by production agent guardrails."""
    payload = payload if isinstance(payload, dict) else {}
    artifacts = run.setdefault("artifacts", {}) if isinstance(run, dict) else {}
    constraint = _ensure_business_flow_constraint(run)
    flow_keywords = _business_flow_keywords(constraint)
    if stage == "plan":
        steps = payload.get("steps") if isinstance(payload.get("steps"), list) else []
        passed = bool(steps) and bool(constraint.get("businessFlow"))
        reason = "计划包含步骤且已绑定业务主链" if passed else "计划缺少步骤或业务主链"
        return _record_agent_quality_gate(
            run,
            "plan_grounding",
            passed,
            reason,
            stepCount=len(steps),
            businessFlowKeywords=flow_keywords,
        )
    if stage == "case_retrieval":
        decision = str(payload.get("decision") or "").strip()
        confidence = float(payload.get("confidence") or 0)
        matched_count = len(payload.get("matched") or [])
        matched_keywords = payload.get("matchedKeywords") if isinstance(payload.get("matchedKeywords"), list) else []
        ai_used = bool(payload.get("aiUsed"))
        passed = True
        reasons = []
        if decision == "reuse" and confidence < 0.72:
            passed = False
            reasons.append("复用置信度低于自动复用阈值")
        if decision == "reuse" and not matched_count:
            passed = False
            reasons.append("复用决策没有匹配 YAML")
        if decision == "reuse" and flow_keywords and not set(flow_keywords) & set(matched_keywords):
            passed = False
            reasons.append("复用依据未命中业务主链关键词")
        if decision == "reuse" and not ai_used and confidence < 0.85:
            passed = False
            reasons.append("规则兜底复用需要更高置信度")
        reason = "；".join(reasons) if reasons else "Case Retrieval 决策通过质量门禁"
        gate = _record_agent_quality_gate(
            run,
            "case_retrieval_decision",
            passed,
            reason,
            decision=decision,
            confidence=confidence,
            matchedCount=matched_count,
            aiUsed=ai_used,
            businessFlowKeywords=flow_keywords,
            matchedKeywords=matched_keywords,
        )
        artifacts.setdefault("caseRetrievalQuality", gate)
        return gate
    return _record_agent_quality_gate(run, str(stage or "unknown"), True, "无专用质量门禁")


def _normalize_agent_goal_analysis(value, rule_result, source):
    """Validate and normalize model output for goal analysis."""
    if not isinstance(value, dict):
        return None, "AI 返回不是 JSON 对象"
    result = dict(value)
    module = str(result.get("module") or "").strip()
    raw_keywords = result.get("keywords") or []
    if not isinstance(raw_keywords, list):
        raw_keywords = [raw_keywords]
    keywords = _dedupe_business_terms(raw_keywords, limit=10)
    scope = str(result.get("scope") or rule_result.get("scope") or "auto").strip()
    risk_level = str(result.get("riskLevel") or rule_result.get("riskLevel") or "low").strip().lower()
    if risk_level not in ("low", "medium", "high"):
        risk_level = rule_result.get("riskLevel", "low")
    normalized = {
        **rule_result,
        **result,
        "module": module,
        "keywords": keywords,
        "matchAll": bool(result.get("matchAll", rule_result.get("matchAll", False))),
        "scope": scope,
        "riskLevel": risk_level,
        "riskHits": rule_result.get("riskHits", []),
        "target": rule_result.get("target", ""),
        "summary": str(result.get("summary") or rule_result.get("summary") or "").strip(),
        "aiSource": source,
        "validated": True,
    }
    if not normalized["summary"]:
        normalized["summary"] = f"执行目标：{normalized['target']}"
    return normalized, ""


# ---------------------------------------------------------------------------
# APP 目录映射 (migrated from midscene-upload.py)
# ---------------------------------------------------------------------------

APP_DIR_KEYWORDS = {
    '智小白3D': ['3D打印基线', '3D打印'],
    '小白学习': ['小白学习'],
}

APP_PACKAGE_BY_KEY = {
    '智小白3D': 'com.kfb.model',
    '小白学习': 'com.xbxxhz.box',
}


def get_available_apps():
    """扫描 server-tasks 目录，返回可用应用及其模块列表。"""
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidate_bases = [
        os.path.join(base_dir, 'server-tasks'),
        os.path.join(base_dir, 'server-tasks-all'),
        '/opt/midscene-task-platform/server-tasks',
        '/opt/midscene-task-platform/server-tasks-all',
    ]

    # 收集所有模块目录
    all_modules = set()
    for base in candidate_bases:
        if not os.path.isdir(base):
            continue
        for item in os.listdir(base):
            if os.path.isdir(os.path.join(base, item)):
                all_modules.add(item)

    # 按 APP_DIR_KEYWORDS 归类
    apps = []
    classified = set()
    for app_key, keywords in APP_DIR_KEYWORDS.items():
        modules = sorted([m for m in all_modules if any(kw in m for kw in keywords)])
        if modules:
            apps.append({
                "key": app_key,
                "name": f"{app_key} APP" if "APP" not in app_key else app_key,
                "package": APP_PACKAGE_BY_KEY.get(app_key, ""),
                "modules": modules
            })
            classified.update(modules)

    # 未归类的模块放到"其他"
    unclassified = sorted(all_modules - classified)
    if unclassified:
        apps.append({
            "key": "其他",
            "name": "其他应用",
            "modules": unclassified
        })

    return {"apps": apps}


def _get_search_dirs_for_app(app_name, base_dir):
    """根据应用名确定搜索目录，优先正式 TASK_DIR，再用 server-tasks 补充。"""
    dir_keywords = None
    for app_key, keywords in APP_DIR_KEYWORDS.items():
        if app_key in app_name:
            dir_keywords = keywords
            break

    search_dirs = []

    # 多个候选base目录（支持不同部署方式）。
    # Agent 默认是复用可执行/已入库用例，所以正式 TASK_DIR 必须排在草稿目录前面。
    candidate_bases = [
        TASK_DIR,
        os.path.join(base_dir, 'midscene-tasks'),
        os.path.join(base_dir, 'server-tasks'),
        os.path.join(base_dir, 'server-tasks-all'),
        '/opt/midscene-tasks',
        '/opt/midscene-task-platform/server-tasks',
        '/opt/midscene-task-platform/server-tasks-all',
    ]
    # 去重
    seen_bases = set()

    for base in candidate_bases:
        if base in seen_bases or not os.path.isdir(base):
            continue
        seen_bases.add(base)
        if dir_keywords:
            try:
                for item in os.listdir(base):
                    item_path = os.path.join(base, item)
                    if os.path.isdir(item_path):
                        if any(kw in item for kw in dir_keywords):
                            search_dirs.append(item_path)
            except OSError:
                continue
        else:
            search_dirs.append(base)

    return search_dirs


# ---------------------------------------------------------------------------
# Agent Tool Handlers (real implementations, migrated from midscene-upload.py)
# ---------------------------------------------------------------------------


def tool_list_cases(run, inp):
    """Read case list from task directory."""
    modules = []
    if os.path.isdir(TASK_DIR):
        for mod in sorted(os.listdir(TASK_DIR)):
            mod_path = os.path.join(TASK_DIR, mod)
            if os.path.isdir(mod_path) and not mod.startswith("."):
                files = [fn for fn in os.listdir(mod_path) if fn.endswith(".yaml") or fn.endswith(".yml")]
                modules.append({"module": mod, "fileCount": len(files)})
    return {"modules": modules, "totalModules": len(modules)}


def tool_read_yaml(run, inp):
    """Read a YAML file content."""
    mod = inp.get("module", "")
    fn = inp.get("file", "")
    if not mod or not fn:
        return {"ok": False, "error": "module 和 file 不能为空"}
    try:
        fpath = safe_join(TASK_DIR, mod, fn)
        txt = read_text_file(fpath, "")
        if not txt:
            return {"ok": False, "error": "文件不存在或为空"}
        return {"ok": True, "module": mod, "file": fn, "content": txt[:5000], "size": len(txt)}
    except ValueError:
        return {"ok": False, "error": "非法路径"}


def tool_list_jobs(run, inp):
    """Read execution job list."""
    from task_server.services import job_service
    jobs = job_service.load_jobs()
    summary = []
    for j in jobs[:30]:
        summary.append({
            "jobId": j.get("job_id", ""),
            "status": j.get("status", ""),
            "module": j.get("module", ""),
            "file": j.get("file", ""),
            "taskName": j.get("task_name", ""),
            "createdAt": j.get("created_at", ""),
        })
    return {"jobs": summary, "total": len(jobs)}


def tool_read_report(run, inp):
    """Read execution report summary."""
    reports_dir = os.getenv("MIDSCENE_REPORTS_DIR", "/opt/midscene-reports")
    if not os.path.isdir(reports_dir):
        return {"reports": [], "total": 0}
    files = sorted([f for f in os.listdir(reports_dir) if f.endswith(".html")], reverse=True)
    return {"reports": files[:20], "total": len(files)}


def tool_read_model_strategy(run, inp):
    """Read model strategy from AI Gateway config."""
    try:
        # AI Gateway 目录默认位于项目根/ai-gateway
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        ai_gw_dir = os.path.join(base_dir, "ai-gateway")
        router = read_json_file(os.path.join(ai_gw_dir, "config", "model-router.json"), default={})
        providers = read_json_file(os.path.join(ai_gw_dir, "config", "providers.json"), default={"providers": []})
        prov_source = providers.get("providers", []) if isinstance(providers, dict) else []
        if isinstance(prov_source, dict):
            safe_providers = [
                {
                    "id": str(pid),
                    "name": p.get("name", str(pid)) if isinstance(p, dict) else str(pid),
                    "model": p.get("model", "") if isinstance(p, dict) else "",
                }
                for pid, p in prov_source.items()
            ]
        else:
            safe_providers = [
                {"id": p.get("id", ""), "name": p.get("name", ""), "model": p.get("model", "")}
                for p in prov_source
                if isinstance(p, dict)
            ]
        return {"router": router, "providers": safe_providers}
    except Exception as e:
        return {"error": str(e)}


def tool_list_runners(run, inp):
    """Read runner list."""
    try:
        from task_server.services import sonic_service
        apps = sonic_service._load_task_apps()
    except Exception:
        apps = {"apps": []}
    return {"apps": [{"package": a.get("package", ""), "name": a.get("name", "")} for a in (apps.get("apps", []) or []) if isinstance(a, dict)]}


def tool_analyze_goal(run, inp):
    """Analyze test goal using AI (Qwen) with rule-based fallback.

    三级调用策略：
    1. AI Gateway /ai/chat
    2. DashScope 直连
    3. 规则兜底
    """
    target = inp.get("target", run.get("target", ""))
    scope = run.get("scope", "smoke")
    app_name = run.get("appName", "智小白3D APP")
    risk_hits = [kw for kw in AGENT_RISK_KEYWORDS if kw in target]
    artifacts = run.setdefault("artifacts", {})
    ai_health = _probe_agent_ai_health(run)
    business_constraint = _ensure_business_flow_constraint(run)

    # Rule-based fallback result
    rule_result = {
        "target": target,
        "module": "",
        "keywords": [],
        "matchAll": _agent_wants_all_existing_cases(target),
        "scope": scope,
        "riskLevel": "high" if risk_hits else "low",
        "riskHits": risk_hits,
        "summary": f"执行目标：{target}",
        "suggestedSteps": [
            "匹配或生成用例",
            "生成 YAML",
            "校验 YAML",
            "风险检查" + ("（命中高风险）" if risk_hits else ""),
            "通过 Windows/Mac Runner 执行测试",
            "收集报告",
            "分析失败（如有）",
            "生成总结",
        ],
    }
    rule_result["businessFlow"] = business_constraint.get("businessFlow") or []

    # 根据 app_name 获取对应模块列表
    app_info = get_available_apps()
    modules_for_app = []
    for app in app_info.get("apps", []):
        if app["key"] in app_name or app_name in app.get("name", ""):
            modules_for_app = app["modules"]
            break
    if not modules_for_app:
        # fallback: 列出全部模块
        for app in app_info.get("apps", []):
            modules_for_app.extend(app["modules"])

    modules_list_text = "\n".join(f"- {m}" for m in modules_for_app)

    try:
        business_prompt = get_prompt_center().get("agent", {
            **(run if isinstance(run, dict) else {}),
            "target": target,
            "scope": scope,
            "appName": app_name,
        })
    except Exception:
        business_prompt = ""

    prompt = f"""{business_prompt}

你是测试任务意图解析器。必须优先用语义理解识别用户真实测试目标，规则只能作为校验和兜底，不能机械按关键词扩大范围。

用户输入：{target}
应用名称：{app_name}
执行范围：{scope}
业务主链（必须遵守）：{business_constraint.get("businessFlowText", "")}

可用模块目录：
{modules_list_text}

请输出严格JSON（不要markdown不要解释）：
{{
  "module": "匹配的模块目录名，如无法判断则为空字符串",
  "keywords": ["业务关键词数组，用于匹配具体用例文件名"],
  "matchAll": true或false,
  "scope": "回归/冒烟/单条",
  "riskLevel": "low/high",
  "summary": "一句话描述用户意图"
}}

规则：
- 只有用户明确说"所有用例"、"全部用例"、"全量基线"、"整套用例"、"执行所有"等，matchAll=true，keywords为空
- 如果用户指定了具体业务点或用例名，如"关节龙"、"姓名牌"、"我的收藏"，matchAll=false，keywords只包含业务名词
- "回归"、"基线"、"执行一下"不是全量信号，不能单独触发 matchAll
- 应用名（智小白3D、小白学习）不是业务关键词
- module 应该严格对应上面列出的可用模块目录名"""

    messages = [{"role": "user", "content": prompt}]

    artifacts["agentAiHealth"] = ai_health
    # === Strategy 1: AI Gateway /ai/chat ===
    try:
        if ai_health.get("gatewayReachable"):
            gw_result = _ai_gateway_post("/ai/chat", {
                "messages": messages,
                "temperature": 0.1,
                "providerId": run.get("modelProviderId") or run.get("aiProviderId") or "",
            }, timeout=15)
        else:
            gw_result = None
        if gw_result and isinstance(gw_result, dict):
            content = gw_result.get("content", "")
            if content:
                # Strip markdown fence if present
                content = re.sub(r'^\s*```(?:json)?\s*', '', content)
                content = re.sub(r'\s*```\s*$', '', content)
                ai_result = json.loads(content)
                normalized, issue = _normalize_agent_goal_analysis(ai_result, rule_result, "ai_gateway")
                if normalized:
                    artifacts["goalAnalysis"] = normalized
                    _record_agent_ai_decision(run, "analyze_goal", "ai_gateway", True, normalized.get("summary", ""), keywords=normalized.get("keywords"), businessFlow=business_constraint.get("businessFlow"))
                    return normalized
                _record_agent_ai_decision(run, "analyze_goal", "ai_gateway", False, issue)
        elif ai_health.get("gatewayReachable"):
            _record_agent_ai_decision(run, "analyze_goal", "ai_gateway", False, "AI Gateway 返回空结果")
    except (json.JSONDecodeError, KeyError, TypeError, Exception) as exc:
        _record_agent_ai_decision(run, "analyze_goal", "ai_gateway", False, str(exc)[:200])

    # === Strategy 2: Direct DashScope OpenAI-compatible API ===
    try:
        api_key = dashscope_api_key(required=False)
        if api_key:
            base_url = dashscope_base_url()
            model = dashscope_text_model()
            req_body = json.dumps({
                "model": model,
                "messages": messages,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            }, ensure_ascii=False).encode("utf-8")
            resp = http_client.request(
                f"{base_url}/chat/completions",
                method="POST",
                data=req_body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                timeout=15,
            )
            if resp.ok:
                data = resp.json(default={})
                content = data["choices"][0]["message"]["content"]
                ai_result = json.loads(content)
                normalized, issue = _normalize_agent_goal_analysis(ai_result, rule_result, f"dashscope/{model}")
                if normalized:
                    artifacts["goalAnalysis"] = normalized
                    _record_agent_ai_decision(run, "analyze_goal", f"dashscope/{model}", True, normalized.get("summary", ""), keywords=normalized.get("keywords"), businessFlow=business_constraint.get("businessFlow"))
                    return normalized
                _record_agent_ai_decision(run, "analyze_goal", f"dashscope/{model}", False, issue)
            else:
                _record_agent_ai_decision(run, "analyze_goal", f"dashscope/{model}", False, f"HTTP {resp.status}")
        else:
            _record_agent_ai_decision(run, "analyze_goal", "dashscope", False, "未配置 DASHSCOPE_API_KEY")
    except (json.JSONDecodeError, KeyError, TypeError, Exception) as exc:
        _record_agent_ai_decision(run, "analyze_goal", "dashscope", False, str(exc)[:200])

    # === Strategy 3: Rule-based fallback ===
    _record_agent_ai_decision(run, "analyze_goal", "rule_fallback", True, "AI 不可用或返回无效，使用规则兜底", businessFlow=business_constraint.get("businessFlow"))
    artifacts["goalAnalysis"] = rule_result
    return rule_result


def _ensure_agent_goal_analysis(run):
    """Ensure target understanding is AI-first and reusable by later steps."""
    artifacts = run.setdefault("artifacts", {}) if isinstance(run, dict) else {}
    existing = artifacts.get("goalAnalysis")
    if isinstance(existing, dict) and existing.get("validated"):
        return existing
    analysis = tool_analyze_goal(run, {
        "target": run.get("target", ""),
        "scope": run.get("scope", "auto"),
    })
    if isinstance(analysis, dict):
        artifacts["goalAnalysis"] = analysis
        return analysis
    fallback = {
        "target": run.get("target", ""),
        "module": "",
        "keywords": [],
        "matchAll": _agent_wants_all_existing_cases(run.get("target", "")),
        "scope": run.get("scope", "auto"),
        "riskLevel": run.get("riskLevel", "low"),
        "summary": f"执行目标：{run.get('target', '')}",
        "aiSource": "rule_fallback",
        "validated": True,
    }
    artifacts["goalAnalysis"] = fallback
    _record_agent_ai_decision(run, "analyze_goal", "rule_fallback", True, "AI 目标识别不可用，使用规则兜底", matchAll=fallback["matchAll"])
    return fallback


def tool_generate_cases(run, inp):
    """Generate test cases via AI Gateway."""
    target = inp.get("target") or inp.get("goal") or run.get("target", "")
    if not target:
        return {"ok": False, "error": "target 不能为空"}
    try:
        ai_gw = os.getenv("AI_GATEWAY_URL", "http://localhost:3200")
        prompt_ctx = get_prompt_center().enrich({**(run if isinstance(run, dict) else {}), **(inp if isinstance(inp, dict) else {}), "target": target})
        resp = http_client.post_json(f"{ai_gw}/api/ai/generate", {
            "target": target,
            "type": "generate_cases",
            "businessContext": prompt_ctx.get("businessContext"),
            "promptCenter": prompt_ctx.get("promptCenter"),
        }, timeout=30)
        result = resp.json(default={}) if resp.ok else {}
        return {"ok": resp.ok, "cases": result.get("cases", []), "casesGenerated": len(result.get("cases", []))}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "casesGenerated": 0}


def tool_generate_yaml(run, inp):
    """Generate YAML via AI Gateway."""
    target = inp.get("target") or inp.get("goal") or run.get("target", "")
    module = inp.get("module", "")
    if not target:
        return {"ok": False, "error": "target 不能为空"}
    try:
        ai_gw = os.getenv("AI_GATEWAY_URL", "http://localhost:3200")
        prompt_ctx = get_prompt_center().enrich({**(run if isinstance(run, dict) else {}), **(inp if isinstance(inp, dict) else {}), "target": target, "module": module})
        resp = http_client.post_json(f"{ai_gw}/api/ai/generate", {
            "prompt": target,
            "module": module,
            "type": "generate_yaml",
            "businessContext": prompt_ctx.get("businessContext"),
            "promptCenter": prompt_ctx.get("promptCenter"),
        }, timeout=30)
        result = resp.json(default={}) if resp.ok else {}
        return {"ok": resp.ok, "yaml": result.get("yaml", ""), "yamlGenerated": bool(result.get("yaml"))}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "yamlGenerated": False}


def tool_analyze_failure(run, inp):
    """Analyze failure from job data."""
    job_id = inp.get("jobId") or run.get("failedJobId", "")
    if not job_id:
        return {"message": "无失败任务需要分析"}
    from task_server.services import job_service
    jobs = job_service.load_jobs()
    job = next((j for j in jobs if j.get("job_id") == job_id), None)
    if not job:
        return {"error": f"找不到任务 {job_id}"}
    return {
        "jobId": job_id,
        "status": job.get("status", "failed"),
        "failureType": job.get("failure_type", "UNKNOWN"),
        "summary": (job.get("error") or "")[:500],
    }


def tool_generate_repair_draft(run, inp):
    """Generate a repair draft based on failure analysis."""
    failure = inp.get("failureAnalysis") or (run.get("artifacts") or {}).get("failureAnalysis") or {}
    ft = failure.get("failureType", "UNKNOWN")
    if ft == "PRODUCT_BUG":
        return {"message": "PRODUCT_BUG 不生成 YAML 修复，仅生成缺陷草稿", "type": "PRODUCT_BUG"}
    if ft == "ENV_ISSUE":
        return {"message": "ENV_ISSUE 不自动修复，请检查环境", "type": "ENV_ISSUE"}
    if ft == "UNKNOWN":
        return {"message": "未知失败类型，进入人工复核", "type": "UNKNOWN"}
    draft_id = unique_millis_id("repair")
    return {"draftId": draft_id, "type": ft, "suggestion": "建议修复定位器或等待条件"}


def tool_generate_bug_draft(run, inp):
    """Generate a bug draft."""
    failure = inp.get("failureAnalysis") or (run.get("artifacts") or {}).get("failureAnalysis") or {}
    return {
        "title": f"[{run.get('appName', '')}] {run.get('target', '')[:50]}",
        "description": f"失败分析：{failure.get('summary', '')[:300]}",
        "status": "DRAFT",
        "note": "草稿已生成，提交飞书需要人工确认"
    }


def tool_generate_summary(run, inp):
    """Generate agent run summary."""
    steps = run.get("steps", [])
    completed = sum(1 for s in steps if s.get("status") == "SUCCESS")
    failed = sum(1 for s in steps if s.get("status") == "FAILED")
    return {
        "totalSteps": len(steps),
        "completed": completed,
        "failed": failed,
        "mode": run.get("mode", ""),
        "riskLevel": run.get("riskLevel", ""),
        "message": f"Agent 执行完成：{completed}/{len(steps)} 步骤成功"
    }


def tool_sonic_list_projects(run, inp):
    """List Sonic projects (read-only, no token exposed)."""
    try:
        from task_server.services import sonic_service
        resp = sonic_service.sonic_request("GET", "/projects", timeout=15)
        data = sonic_service.sonic_response_data(resp) or {}
        projects = data.get("data", []) if isinstance(data, dict) else []
        safe = [{"id": p.get("id"), "name": p.get("name", "")} for p in projects if isinstance(p, dict)]
        return {"projects": safe, "total": len(safe)}
    except Exception as e:
        return {"projects": [], "total": 0, "error": str(e)}


def tool_sonic_list_suites(run, inp):
    """List Sonic test suites."""
    try:
        from task_server.services import sonic_service
        project_id = inp.get("projectId") or ""
        params = {"id": project_id} if project_id else {}
        resp = sonic_service.sonic_request("GET", "/testSuites", params=params, timeout=15)
        data = sonic_service.sonic_response_data(resp) or {}
        suites = data.get("data", []) if isinstance(data, dict) else []
        safe = [{"id": s.get("id"), "name": s.get("name", ""), "caseCount": len(s.get("testCases", []))} for s in suites if isinstance(s, dict)]
        return {"suites": safe, "total": len(safe)}
    except Exception as e:
        return {"suites": [], "total": 0, "error": str(e)}


def tool_sonic_sync_case(run, inp):
    """Sync a single case to Sonic."""
    mod = inp.get("module", "")
    fn = inp.get("file", "")
    task_name = inp.get("taskName", "")
    if not mod or not fn:
        return {"ok": False, "error": "module 和 file 不能为空"}
    from task_server.services import sonic_service
    result = sonic_service.sonic_publish_yaml({"module": mod, "file": fn, "taskName": task_name, "dryRun": inp.get("dryRun", False), "force": True})
    return {k: v for k, v in (result or {}).items() if k not in ("token", "sonicToken", "password")}


def tool_sonic_run_suite(run, inp):
    """Trigger Sonic suite execution (requires confirm if high risk)."""
    if run.get("riskHits"):
        raise RuntimeError(f"命中高风险关键词：{', '.join(run['riskHits'])}，需要人工确认后才能执行")
    suite_id = inp.get("suiteId") or ""
    if not suite_id:
        return {"ok": False, "error": "suiteId 不能为空"}
    result = sonic_force_run_suite(suite_id)
    return {
        "ok": result.get("ok", False),
        "suiteId": suite_id,
        "resultId": result.get("resultId"),
        "message": "Sonic 测试套执行已触发" if result.get("ok") else f"触发失败: {result.get('error', '未知错误')}",
    }


def tool_sonic_read_result(run, inp):
    """Read Sonic execution results."""
    results = read_json_file(SONIC_SUITE_RESULTS_FILE, default={"results": []})
    items = results.get("results", []) if isinstance(results, dict) else []
    return {"results": items[:20], "total": len(items)}


def tool_sonic_read_report(run, inp):
    """Read Sonic report."""
    results = read_json_file(SONIC_SUITE_RESULTS_FILE, default={"results": []})
    items = results.get("results", []) if isinstance(results, dict) else []
    report_id = inp.get("reportId") or inp.get("resultId") or ""
    if report_id:
        item = next((r for r in items if str(r.get("id", "")) == str(report_id)), None)
        return item or {"error": "报告不存在"}
    return {"results": items[:10]}


def tool_create_runner_job(run, inp):
    """Create a runner job."""
    module = inp.get("module", "")
    file = inp.get("file", "")
    if not module or not file:
        return {"ok": False, "error": "module 和 file 不能为空"}
    try:
        from task_server.services import job_service
        job = job_service.create_job({
            "module": module,
            "file": file,
            "target_task_name": inp.get("taskName") or inp.get("task_name") or "",
        })
        return {
            "ok": True,
            "jobId": job.get("job_id", ""),
            "status": "pending",
            "message": f"Runner 任务已创建: {job.get('job_id', '')}",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def tool_run_midscene_task(run, inp):
    """Run a midscene task by creating a runner job."""
    module = inp.get("module", "")
    file = inp.get("file", "")
    yaml_path = inp.get("yamlPath") or inp.get("yaml_path") or ""
    if not module and yaml_path:
        # 从路径提取 module/file
        parts = yaml_path.replace("\\", "/").split("/")
        if len(parts) >= 2:
            module = parts[-2]
            file = parts[-1]
    if not module or not file:
        return {"ok": False, "error": "module 和 file 不能为空（或提供 yamlPath）"}
    try:
        from task_server.services import job_service
        job = job_service.create_job({
            "module": module,
            "file": file,
            "target_task_name": inp.get("taskName") or file.replace(".yaml", "").replace(".yml", ""),
        })
        return {
            "ok": True,
            "jobId": job.get("job_id", ""),
            "module": module,
            "file": file,
            "message": f"Midscene 任务已创建，等待 Runner 执行: {job.get('job_id', '')}",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def tool_retry_failed_job(run, inp):
    """Retry a failed job by creating a new pending job with same params."""
    job_id = inp.get("jobId") or inp.get("job_id") or ""
    if not job_id:
        return {"ok": False, "error": "jobId 不能为空"}
    try:
        from task_server.services import job_service
        with JOB_LOCK:
            jobs = job_service.load_jobs()
            old_job = next((j for j in jobs if j.get("job_id") == job_id), None)
        if not old_job:
            return {"ok": False, "error": f"任务 {job_id} 不存在"}
        module = old_job.get("module", "")
        file = old_job.get("file", "")
        if not module or not file:
            return {"ok": False, "error": "原任务缺少 module/file 信息"}
        attempt = (old_job.get("attempt") or 1) + 1
        new_job = job_service.create_pending_job(
            module,
            file,
            auto_optimize=False,
            max_attempt=safe_int(old_job.get("max_attempt"), 2),
            attempt=attempt,
            parent_job_id=job_id,
            device_id=old_job.get("device_id", ""),
            runner_id=old_job.get("target_runner_id") or old_job.get("runner_id", ""),
            device_strategy=old_job.get("device_strategy") or old_job.get("deviceStrategy") or "",
            run_mode=old_job.get("run_mode", "test"),
            target_task_name=old_job.get("target_task_name") or old_job.get("current_task_name") or "",
        )
        return {
            "ok": True,
            "newJobId": new_job.get("job_id", ""),
            "originalJobId": job_id,
            "attempt": attempt,
            "message": f"任务重跑已创建: {new_job.get('job_id', '')} (第{attempt}次)",
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


def tool_save_repair_draft(run, inp):
    """Save a repair draft."""
    from task_server.services import repair_service
    draft = dict(inp)
    if not draft.get("draftId"):
        draft["draftId"] = unique_millis_id("repair")
    draft.setdefault("status", "DRAFTED")
    drafts = repair_service.load_repair_drafts()
    existing = next((d for d in drafts if d.get("draftId") == draft["draftId"]), None)
    if existing:
        existing.update(draft)
    else:
        drafts.append(repair_service.normalize_repair_draft(draft))
    repair_service.save_repair_drafts(drafts)
    return {"draftId": draft["draftId"], "status": "DRAFTED"}


def tool_apply_repair_after_confirm(run, inp):
    """Apply a repair draft - update draft status and write optimized YAML."""
    from task_server.services import repair_service
    draft_id = inp.get("draftId") or inp.get("draft_id") or ""
    if not draft_id:
        return {"ok": False, "error": "draftId 不能为空"}
    try:
        drafts = repair_service.load_repair_drafts()
        draft = next((d for d in drafts if d.get("draftId") == draft_id), None)
        if not draft:
            return {"ok": False, "error": f"修复草稿 {draft_id} 不存在"}
        # 更新状态
        draft["status"] = "APPLIED"
        draft["applied_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        repair_service.save_repair_drafts(drafts)
        # 如果有 optimized_yaml，写回文件
        optimized = draft.get("optimized_yaml") or draft.get("optimizedYaml") or ""
        target_file = draft.get("target_file") or draft.get("targetFile") or ""
        wrote_file = False
        if optimized and target_file:
            try:
                full_path = target_file if os.path.isabs(target_file) else safe_join(TASK_DIR, target_file)
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(optimized)
                wrote_file = True
            except Exception:
                pass
        return {
            "ok": True,
            "draftId": draft_id,
            "status": "APPLIED",
            "wroteFile": wrote_file,
            "message": f"修复草稿已应用" + (f"，YAML已写入 {target_file}" if wrote_file else ""),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


# Knowledge tool handlers
def _tool_query_page_knowledge(run, inp):
    """查询页面元素和导航路径。"""
    try:
        from task_server.services import knowledge_service
        app_id = str(inp.get("appId") or inp.get("app_id") or "").strip()
        page_name = str(inp.get("pageName") or inp.get("page_name") or "").strip()
        result = knowledge_service.query_page_elements(app_id, page_name)
        if inp.get("fromPage") and inp.get("toPage"):
            nav = knowledge_service.query_navigation_path(
                app_id, str(inp.get("fromPage")), str(inp.get("toPage"))
            )
            result["navigation"] = nav
        return result
    except Exception as exc:
        return {"error": str(exc), "ok": False}


def _tool_query_failure_knowledge(run, inp):
    """匹配历史失败模式。"""
    try:
        from task_server.services import knowledge_service
        log_text = str(inp.get("logText") or inp.get("log") or "").strip()
        top_k = int(inp.get("topK") or 3)
        return {"matches": knowledge_service.match_failure_pattern(log_text, top_k=top_k), "ok": True}
    except Exception as exc:
        return {"error": str(exc), "ok": False}


def _tool_query_case_history(run, inp):
    """查询用例执行历史。"""
    try:
        from task_server.services import knowledge_service
        yaml_file = str(inp.get("yamlFile") or inp.get("file") or "").strip()
        limit = int(inp.get("limit") or 10)
        return knowledge_service.get_case_history(yaml_file, limit=limit)
    except Exception as exc:
        return {"error": str(exc), "ok": False}


# Agent Tool Handlers registry
AGENT_TOOL_HANDLERS = {
    "list_cases": tool_list_cases,
    "read_yaml": tool_read_yaml,
    "list_jobs": tool_list_jobs,
    "read_report": tool_read_report,
    "read_model_strategy": tool_read_model_strategy,
    "list_runners": tool_list_runners,
    "analyze_goal": tool_analyze_goal,
    "generate_cases": tool_generate_cases,
    "generate_yaml": tool_generate_yaml,
    "analyze_failure": tool_analyze_failure,
    "generate_repair_draft": tool_generate_repair_draft,
    "generate_bug_draft": tool_generate_bug_draft,
    "generate_summary": tool_generate_summary,
    "sonic_list_projects": tool_sonic_list_projects,
    "sonic_list_suites": tool_sonic_list_suites,
    "sonic_sync_case": tool_sonic_sync_case,
    "sonic_run_suite": tool_sonic_run_suite,
    "sonic_read_result": tool_sonic_read_result,
    "sonic_read_report": tool_sonic_read_report,
    "create_runner_job": tool_create_runner_job,
    "run_midscene_task": tool_run_midscene_task,
    "retry_failed_job": tool_retry_failed_job,
    "save_repair_draft": tool_save_repair_draft,
    "apply_repair_after_confirm": tool_apply_repair_after_confirm,
    # KNOWLEDGE tool handlers
    "query_page_knowledge": _tool_query_page_knowledge,
    "query_failure_knowledge": _tool_query_failure_knowledge,
    "query_case_history": _tool_query_case_history,
}


# ---------------------------------------------------------------------------
# Agent Step Tool Functions (real service integration)
# ---------------------------------------------------------------------------

def _tool_agent_plan(run):
    """调用 AI Gateway 生成计划；不可用则本地生成。"""
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "analyze_goal",
        "category": "AI",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {"target": run.get("target", ""), "scope": run.get("scope", "smoke")},
    }
    try:
        ai_health = _probe_agent_ai_health(run)
        business_constraint = _ensure_business_flow_constraint(run)
        try:
            prompt_ctx = get_prompt_center().enrich(run if isinstance(run, dict) else {})
        except Exception:
            prompt_ctx = {}
        if ai_health.get("gatewayReachable"):
            try:
                resp = _ai_gateway_post("/ai/generate-case", {
                    "target": run.get("target", ""),
                    "scope": run.get("scope", "smoke"),
                    "mode": run.get("mode", "AUTO_SAFE"),
                    "appName": run.get("appName", ""),
                    "platform": run.get("platform", "android"),
                    "providerId": run.get("modelProviderId") or run.get("aiProviderId") or "",
                    "businessFlowConstraint": business_constraint,
                    "businessContext": prompt_ctx.get("businessContext"),
                    "promptCenter": prompt_ctx.get("promptCenter"),
                })
                plan_steps = resp.get("steps", []) if isinstance(resp, dict) else []
                if not plan_steps:
                    plan_steps = [
                        "1. 分析测试目标",
                        "2. 匹配已有用例或生成新用例",
                        "3. 生成并校验 Midscene YAML",
                        "4. 通过 Windows/Mac Runner 执行已确认 YAML",
                        "5. 收集报告并分析失败",
                        "6. 生成修复草稿或缺陷草稿",
                        "7. Runner 测试动作风险仅提醒；平台级写操作进入 WAIT_CONFIRM",
                        "8. 生成总结报告",
                    ]
                plan = {
                    "steps": plan_steps,
                    "mode": run.get("mode", "AUTO_SAFE"),
                    "target": run.get("target", ""),
                    "riskLevel": run.get("riskLevel", "low"),
                    "businessFlowConstraint": _compact_business_flow_constraint(business_constraint),
                    "businessContext": prompt_ctx.get("businessContext"),
                    "aiHealth": ai_health,
                }
                call["status"] = "SUCCESS"
                call["outputSummary"] = "AI Gateway 生成计划完成"
                _record_agent_ai_decision(run, "plan", "ai_gateway", True, call["outputSummary"], stepCount=len(plan_steps))
            except Exception as e:
                call["status"] = "SKIPPED"
                call["outputSummary"] = f"AI Gateway 调用失败：{str(e)[:200]}"
                _record_agent_ai_decision(run, "plan", "ai_gateway", False, call["outputSummary"])
                plan = {
                    "steps": [
                        "1. 分析测试目标",
                        "2. 匹配已有用例或生成新用例",
                        "3. 生成并校验 Midscene YAML",
                        "4. 通过 Windows/Mac Runner 执行已确认 YAML",
                        "5. 收集报告并分析失败",
                        "6. 生成修复草稿或缺陷草稿",
                        "7. Runner 测试动作风险仅提醒；平台级写操作进入 WAIT_CONFIRM",
                        "8. 生成总结报告",
                    ],
                    "mode": run.get("mode", "AUTO_SAFE"),
                    "target": run.get("target", ""),
                    "riskLevel": run.get("riskLevel", "low"),
                    "businessFlowConstraint": _compact_business_flow_constraint(business_constraint),
                    "aiHealth": ai_health,
                }
        else:
            call["status"] = "SKIPPED"
            call["outputSummary"] = "AI Gateway 不可用，使用本地默认计划"
            _record_agent_ai_decision(run, "plan", "local_default", True, call["outputSummary"], aiHealth=ai_health)
            plan = {
                "steps": [
                    "1. 分析测试目标",
                    "2. 匹配已有用例或生成新用例",
                    "3. 生成并校验 Midscene YAML",
                    "4. 通过 Windows/Mac Runner 执行已确认 YAML",
                    "5. 收集报告并分析失败",
                    "6. 生成修复草稿或缺陷草稿",
                    "7. Runner 测试动作风险仅提醒；平台级写操作进入 WAIT_CONFIRM",
                    "8. 生成总结报告",
                ],
                "mode": run.get("mode", "AUTO_SAFE"),
                "target": run.get("target", ""),
                "riskLevel": run.get("riskLevel", "low"),
                "businessFlowConstraint": _compact_business_flow_constraint(business_constraint),
                "aiHealth": ai_health,
            }
        plan.setdefault("dispatchPolicy", {
            "decisionOwner": "AI",
            "aiDecisions": [
                "需求/目标理解",
                "已有用例语义检索与置信度判断",
                "复用/待确认/生成 YAML 草稿分流",
                "失败类型分析与修复/缺陷草稿建议",
            ],
            "safetyGates": [
                "YAML 强校验",
                "平台级高风险确认",
                "Runner/Sonic/Bridge 执行前体检",
                "草稿或未确认 YAML 禁止自动执行",
            ],
            "note": "AI 负责调度决策，平台保留不可跳过的安全门禁。",
        })
        try:
            goal_analysis = _ensure_agent_goal_analysis(run)
            plan["goalAnalysis"] = {
                "keywords": goal_analysis.get("keywords") or [],
                "matchAll": bool(goal_analysis.get("matchAll")),
                "summary": goal_analysis.get("summary") or "",
                "aiSource": goal_analysis.get("aiSource") or "",
            }
        except Exception as exc:
            _record_agent_ai_decision(run, "analyze_goal", "plan_hook", False, str(exc)[:160])
        plan["qualityGate"] = _evaluate_agent_quality_gate(run, "plan", plan)
        run.setdefault("artifacts", {})["plan"] = plan
        if prompt_ctx.get("promptCenter"):
            run.setdefault("artifacts", {})["promptCenter"] = prompt_ctx.get("promptCenter")
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def _ai_select_cases(target: str, scope: str, app_name: str, yaml_list_text: str, all_yamls: list, model: str = "", provider_id: str = "") -> dict:
    """AI 直选用例：将用户目标和全部用例列表发给 AI，由 AI 直接返回应执行的用例。

    三级调用策略：
    1. AI Gateway /ai/chat
    2. DashScope 直连
    3. 返回 None 表示 AI 不可用，调用方使用兜底逻辑
    """
    prompt = f"""你是测试用例匹配引擎。用户输入了一个测试目标，请从下方用例列表中选择应该执行的用例。

用户目标：{target}
应用名称：{app_name}
前端传入的执行范围：{scope}

可用用例列表（格式：模块目录/文件名）：
{yaml_list_text}

请输出严格JSON（不要markdown不要解释）：
{{
  "matched": ["模块目录/文件名", ...],
  "scope": "回归/冒烟/单条",
  "reason": "一句话说明匹配理由"
}}

规则（按优先级）：
1. 如果用户提到了具体用例名称（如"保龄球打印"、"关节龙"、"客服对话"等），则只匹配名称相关的用例，scope根据数量判断
2. "回归基线" + 具体名称 = 只匹配包含该名称的用例，scope="回归"
3. "回归基线"（无具体名称） = 该模块全部用例，scope="回归"
4. "冒烟" = 抓取最核心的3条，scope="冒烟"
5. "跑一下"/"执行" + 具体名称 = 匹配对应用例

重要（必须严格遵守）：
- 用户说"回归一下保龄球打印基线" → 只匹配含"保龄球"的用例（不是全模块14条！）
- 用户说"回归一下关节龙" → 只匹配含"关节龙"的用例
- "回归" 不等于 "全部"！只有用户明确说"全部回归"或"整个模块回归"时才选全部
- 关键判断：用户是否提到了具体的用例名称？如果提到了，就只选那个，忽略"回归"二字
- matched 中的值必须严格来自上方列表，不要编造
- 如果无法确定，返回空数组
"""

    messages = [{"role": "user", "content": prompt}]
    _ai_errors = []  # 记录 AI 调用失败原因

    # 策略1: AI Gateway
    try:
        if _ai_gateway_available():
            gw_result = _ai_gateway_post("/ai/chat", {
                "messages": messages,
                "temperature": 0.1,
                "providerId": provider_id,
            }, timeout=20)
            if gw_result and isinstance(gw_result, dict):
                content = gw_result.get("content", "")
                if content:
                    parsed = _parse_ai_match_response(content, all_yamls)
                    if parsed:
                        parsed["ai_source"] = "ai_gateway"
                        return parsed
                    else:
                        _ai_errors.append(f"AI Gateway 返回了内容但解析失败: {content[:200]}")
                else:
                    _ai_errors.append("AI Gateway 返回空内容")
            else:
                _ai_errors.append(f"AI Gateway 返回异常: {type(gw_result)}")
        else:
            _ai_errors.append("AI Gateway 不可用(http://127.0.0.1:8090/health 连接失败)")
    except Exception as e:
        _ai_errors.append(f"AI Gateway 异常: {str(e)[:200]}")

    # 策略2: DashScope 直连
    try:
        api_key = dashscope_api_key(required=False)
        if api_key:
            base_url = dashscope_base_url()
            model = model or dashscope_text_model()
            req_body = json.dumps({
                "model": model,
                "messages": messages,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            }, ensure_ascii=False).encode("utf-8")
            resp = http_client.request(
                f"{base_url}/chat/completions",
                method="POST",
                data=req_body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                timeout=45,
            )
            if resp.ok:
                data = resp.json(default={})
                content = data["choices"][0]["message"]["content"]
                parsed = _parse_ai_match_response(content, all_yamls)
                if parsed:
                    parsed["ai_source"] = f"dashscope/{model}"
                    return parsed
                else:
                    _ai_errors.append(f"DashScope 返回了内容但解析失败: {content[:200]}")
            else:
                _ai_errors.append(f"DashScope HTTP {resp.status}: {resp.body[:200]}")
        else:
            _ai_errors.append("未配置 DASHSCOPE_API_KEY")
    except Exception as e:
        _ai_errors.append(f"DashScope 异常: {str(e)[:200]}")

    # 记录 AI 不可用的详细原因（方便调试）
    return {"_ai_errors": _ai_errors} if _ai_errors else None


def _parse_ai_match_response(content: str, all_yamls: list) -> dict:
    """解析 AI 返回的用例匹配 JSON，映射为 abs_path 列表。"""
    try:
        # 剥离 Qwen3 思考模式的 <think>...</think> 标签
        content = re.sub(r'<think>[\s\S]*?</think>\s*', '', content)
        # 剥离 markdown code block
        content = re.sub(r'^\s*```(?:json)?\s*', '', content)
        content = re.sub(r'\s*```\s*$', '', content)
        content = content.strip()
        result = json.loads(content)
        if not isinstance(result, dict):
            return None
        matched_keys = result.get("matched", [])
        if not isinstance(matched_keys, list):
            return None
        # 映射 "模块/文件名" 到 abs_path
        yaml_map = {f"{y['dir_name']}/{y['file_name']}": y["abs_path"] for y in all_yamls}
        matched_paths = []
        for key in matched_keys:
            key = key.strip()
            if key in yaml_map:
                matched_paths.append(yaml_map[key])
            else:
                # 模糊匹配：文件名包含
                for yk, yp in yaml_map.items():
                    if key in yk or yk.endswith("/" + key) or key.replace(".yaml", "") in yk:
                        matched_paths.append(yp)
                        break
        if not matched_paths:
            return None
        return {
            "matched_paths": matched_paths,
            "scope": result.get("scope", ""),
            "reason": result.get("reason", "AI 智能匹配"),
        }
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _strip_ai_json_content(content: str) -> str:
    content = re.sub(r'<think>[\s\S]*?</think>\s*', '', str(content or ""))
    content = re.sub(r'^\s*```(?:json)?\s*', '', content)
    content = re.sub(r'\s*```\s*$', '', content)
    return content.strip()


def _normalize_ai_case_decision(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in ("reuse", "use_existing", "confirm_reuse", "auto_reuse", "复用", "自动复用"):
        return "reuse"
    if text in ("wait_confirm", "confirm", "manual_confirm", "need_confirm", "人工确认", "待确认"):
        return "wait_confirm"
    if text in ("generate_draft", "generate", "new_yaml", "create_yaml", "生成草稿", "新建"):
        return "generate_draft"
    return ""


def _case_candidate_lookup_key(value: Any) -> str:
    text = str(value or "").strip()
    text = text.replace("\\", "/")
    if not text:
        return ""
    return text


def _case_candidate_map(candidates: list) -> dict:
    mapping = {}
    for item in candidates or []:
        if not isinstance(item, dict):
            continue
        keys = [
            item.get("rel_path"),
            item.get("abs_path"),
            f"{item.get('dir_name', '')}/{item.get('file_name', '')}",
            item.get("file_name"),
            item.get("task_name"),
        ]
        for key in keys:
            normalized = _case_candidate_lookup_key(key)
            if normalized:
                mapping.setdefault(normalized, item)
    return mapping


def _resolve_ai_case_candidate(value: Any, candidate_map: dict) -> Optional[dict]:
    key = _case_candidate_lookup_key(value)
    if not key:
        return None
    if key in candidate_map:
        return candidate_map[key]
    key_no_yaml = re.sub(r"\.(yaml|yml)$", "", key)
    for candidate_key, item in candidate_map.items():
        candidate_no_yaml = re.sub(r"\.(yaml|yml)$", "", candidate_key)
        if key_no_yaml and (
            key_no_yaml == candidate_no_yaml
            or key_no_yaml in candidate_no_yaml
            or candidate_no_yaml in key_no_yaml
            or candidate_key.endswith("/" + key)
        ):
            return item
    return None


def _parse_ai_case_retrieval_response(content: str, candidates: list) -> Optional[dict]:
    try:
        data = json.loads(_strip_ai_json_content(content))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    candidate_map = _case_candidate_map(candidates)
    ai_candidates = []
    raw_candidates = data.get("candidates")
    if not isinstance(raw_candidates, list):
        raw_candidates = []
    for raw in raw_candidates:
        if not isinstance(raw, dict):
            continue
        raw_path = raw.get("path") or raw.get("rel_path") or raw.get("file") or raw.get("yaml") or raw.get("name")
        item = _resolve_ai_case_candidate(raw_path, candidate_map)
        if not item:
            continue
        try:
            confidence = max(0.0, min(1.0, float(raw.get("confidence", 0))))
        except Exception:
            confidence = 0.0
        merged = {
            **item,
            "confidence": round(confidence, 2),
            "ai_confidence": round(confidence, 2),
            "ai_reason": str(raw.get("reason") or raw.get("why") or "").strip(),
            "ai_matched_keywords": [str(k).strip() for k in (raw.get("matchedKeywords") or raw.get("matched_keywords") or []) if str(k).strip()],
        }
        ai_candidates.append(merged)
    matched = []
    raw_matched = data.get("matched")
    if isinstance(raw_matched, list):
        for raw_path in raw_matched:
            item = _resolve_ai_case_candidate(raw_path, candidate_map)
            if item and item.get("abs_path") not in matched:
                matched.append(item.get("abs_path"))
    if not matched and ai_candidates:
        best = sorted(ai_candidates, key=lambda item: -float(item.get("confidence") or 0))[0]
        matched = [best.get("abs_path")]

    try:
        confidence = max(0.0, min(1.0, float(data.get("confidence", 0))))
    except Exception:
        confidence = 0.0
    if not confidence and ai_candidates:
        confidence = float(ai_candidates[0].get("confidence") or 0)
    decision = _normalize_ai_case_decision(data.get("decision") or data.get("action"))
    if not decision:
        decision = "wait_confirm" if matched else "generate_draft"
    return {
        "decision": decision,
        "confidence": round(confidence, 2),
        "matched": matched,
        "scope": str(data.get("scope") or "").strip(),
        "reason": str(data.get("reason") or data.get("summary") or "AI 语义复核").strip(),
        "matchedKeywords": [str(k).strip() for k in (data.get("matchedKeywords") or data.get("matched_keywords") or []) if str(k).strip()],
        "candidates": sorted(ai_candidates, key=lambda item: -float(item.get("confidence") or 0)),
    }


def _ai_rerank_case_candidates(target: str, source_text: str, scope: str, app_name: str, candidates: list, model: str = "", provider_id: str = "", business_constraint: Optional[Dict[str, Any]] = None) -> dict:
    """Let the model judge semantic case reuse. Rules only provide candidate recall."""
    if not candidates:
        return {}
    compact_candidates = []
    for idx, item in enumerate(candidates[:12], start=1):
        compact_candidates.append({
            "index": idx,
            "path": item.get("rel_path") or f"{item.get('dir_name', '')}/{item.get('file_name', '')}",
            "module": item.get("dir_name", ""),
            "file": item.get("file_name", ""),
            "taskName": item.get("task_name", ""),
            "ruleConfidence": item.get("confidence", 0),
            "ruleReasons": item.get("reasons") or [],
            "ruleMatchedKeywords": item.get("matched_keywords") or [],
            "yamlExcerpt": str(item.get("yaml_text") or "")[:1200],
        })
    business_constraint = business_constraint if isinstance(business_constraint, dict) else {}
    prompt = f"""你是自动化测试平台的 Case Retrieval 语义判定器。规则召回已经给出候选 YAML，但规则分可能不准；请你根据测试目标、业务主链、输入资料和 YAML 内容判断是否应该复用已有用例。

测试目标：{target}
应用：{app_name}
执行范围：{scope or "auto"}
业务主链（必须优先覆盖，不能跳出主链）：
{business_constraint.get("businessFlowText") or "未提供"}
输入资料摘要：
{source_text[:2500] or "无"}

候选 YAML（只能从这里选择，不能编造路径）：
{json.dumps(compact_candidates, ensure_ascii=False)}

判断要求：
1. 重点判断业务语义是否一致，不要机械看关键词数量。例如“回归一下我的收藏基线用例”和“我的收藏查看.yaml”应认为高度相关。
2. “回归/基线/测试/用例/查看/页面/截图/文件/链接”等泛词不能作为主要依据。
3. 如果已有 YAML 能覆盖用户目标，decision=复用；如果相似但可能误跑，decision=待确认；如果没有合适用例，decision=生成草稿。
4. confidence 是你对“复用已有用例是否正确”的把握，0 到 1；不是规则分。
5. 若用户明确只说一个业务点，不要扩大成整套回归。
6. 如果候选 YAML 没有覆盖业务主链中的核心节点，应返回 generate_draft 或 wait_confirm，不要强行复用。

请只输出严格 JSON：
{{
  "decision": "reuse | wait_confirm | generate_draft",
  "confidence": 0.0,
  "matched": ["候选 path", "..."],
  "scope": "single | regression | smoke",
  "matchedKeywords": ["真正支撑判断的业务词"],
  "reason": "一句话说明为什么这么判断",
  "candidates": [
    {{"path": "候选 path", "confidence": 0.0, "reason": "语义理由", "matchedKeywords": ["业务词"]}}
  ]
}}
"""
    messages = [{"role": "user", "content": prompt}]
    errors = []
    try:
        if _ai_gateway_available():
            gw_result = _ai_gateway_post("/ai/chat", {
                "messages": messages,
                "temperature": 0.1,
                "providerId": provider_id,
            }, timeout=25)
            content = gw_result.get("content", "") if isinstance(gw_result, dict) else ""
            parsed = _parse_ai_case_retrieval_response(content, candidates)
            if parsed:
                parsed["ai_source"] = "ai_gateway"
                return parsed
            errors.append(f"AI Gateway 返回无法解析: {str(content)[:200]}")
        else:
            errors.append("AI Gateway 不可用")
    except Exception as e:
        errors.append(f"AI Gateway 异常: {str(e)[:200]}")

    try:
        api_key = dashscope_api_key(required=False)
        if api_key:
            req_body = json.dumps({
                "model": model or dashscope_text_model(),
                "messages": messages,
                "temperature": 0.1,
                "response_format": {"type": "json_object"},
            }, ensure_ascii=False).encode("utf-8")
            resp = http_client.request(
                f"{dashscope_base_url()}/chat/completions",
                method="POST",
                data=req_body,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                timeout=45,
            )
            if resp.ok:
                data = resp.json(default={})
                content = data["choices"][0]["message"]["content"]
                parsed = _parse_ai_case_retrieval_response(content, candidates)
                if parsed:
                    parsed["ai_source"] = f"dashscope/{model or dashscope_text_model()}"
                    return parsed
                errors.append(f"DashScope 返回无法解析: {str(content)[:200]}")
            else:
                errors.append(f"DashScope HTTP {resp.status}: {resp.body[:200]}")
        else:
            errors.append("未配置 DASHSCOPE_API_KEY")
    except Exception as e:
        errors.append(f"DashScope 异常: {str(e)[:200]}")
    return {"_ai_errors": errors}


def _task_dir_for_path(path):
    norm_path = os.path.normpath(str(path or ""))
    parts = norm_path.split(os.sep)
    for marker in ("midscene-tasks", "server-tasks", "server-tasks-all"):
        if marker in parts:
            idx = parts.index(marker)
            if len(parts) > idx + 2:
                return parts[idx + 1], parts[-1]
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    tasks_dir = TASK_DIR if os.path.isdir(TASK_DIR) else os.path.join(base_dir, "server-tasks")
    try:
        rel = os.path.relpath(path, tasks_dir)
        if rel.startswith(".."):
            return "", os.path.basename(path)
        parts = rel.split(os.sep)
        if len(parts) >= 2:
            return parts[0], parts[-1]
    except Exception:
        pass
    return (parts[-2], parts[-1]) if len(parts) >= 2 else ("", os.path.basename(path or ""))


def _safe_agent_slug(value, default):
    text = re.sub(r"[\\/:*?\"<>|\r\n\t]+", "_", str(value or "").strip())
    text = re.sub(r"\s+", "_", text).strip("._ ")
    return text[:80] or default


def clean_agent_module_name(run):
    module = str(run.get("module") or "").strip()
    if not module:
        module = "AI Agent 草稿"
    return _safe_agent_slug(module, "AI Agent 草稿")


def clean_agent_yaml_name(run):
    title = str(run.get("target") or run.get("runId") or "agent_case").strip()
    name = _safe_agent_slug(title, "agent_case")
    if not name.endswith((".yaml", ".yml")):
        name += ".yaml"
    return name


def _agent_execution_mode(run):
    execution_mode = str((run or {}).get("executionMode") or (run or {}).get("execution_mode") or "RUNNER_JOB").strip().upper()
    return execution_mode if execution_mode in ("RUNNER_JOB", "SONIC_SUITE") else "RUNNER_JOB"


def _looks_like_yaml_text(value):
    text = str(value or "")
    return bool("\n" in text and re.search(r"^\s*(android|ios|tasks)\s*:", text, flags=re.M))


def normalize_yaml_refs(run):
    artifacts = run.setdefault("artifacts", {})
    quarantined_paths = {
        os.path.normpath(str(item.get("path") or ""))
        for item in artifacts.get("quarantinedYamlRefs") or []
        if isinstance(item, dict) and str(item.get("path") or "").strip()
    }

    def is_quarantined_path(path):
        return bool(path and os.path.normpath(str(path)) in quarantined_paths)

    refs = []
    for item in artifacts.get("yamlRefs") or []:
        if isinstance(item, dict):
            if is_quarantined_path(item.get("path") or ""):
                continue
            ref = {
                "type": item.get("type") or "file",
                "source": item.get("source") or "",
                "generated": bool(item.get("generated")),
                "validationMode": item.get("validationMode") or "",
                "module": item.get("module") or "",
                "file": item.get("file") or "",
                "path": item.get("path") or "",
                "content": item.get("content") or "",
                "confirmed": bool(item.get("confirmed")),
                "reason": item.get("reason") or "",
                "executionLevel": item.get("executionLevel") or "",
                "executableScore": item.get("executableScore") if isinstance(item.get("executableScore"), dict) else {},
                "scopeReview": item.get("scopeReview") if isinstance(item.get("scopeReview"), dict) else {},
                "smoke": bool(item.get("smoke")),
                "smokeCandidate": bool(item.get("smoke")),
                "runnerCandidate": bool(item.get("runnerCandidate")),
            }
            if ref["path"] and not (ref["module"] and ref["file"]):
                ref["module"], ref["file"] = _task_dir_for_path(ref["path"])
            refs.append(ref)

    known_paths = {item.get("path") for item in refs if item.get("path")}
    for path in artifacts.get("matchedCases") or []:
        if not isinstance(path, str) or not path.strip() or _looks_like_yaml_text(path):
            continue
        if path in known_paths:
            continue
        module, file = _task_dir_for_path(path)
        refs.append({"type": "file", "source": "baseline", "validationMode": "baseline", "module": module, "file": file, "path": path, "content": "", "confirmed": True})
        known_paths.add(path)

    draft_path = artifacts.get("draftPath") or ""
    if draft_path and draft_path not in known_paths and not is_quarantined_path(draft_path):
        module, file = _task_dir_for_path(draft_path)
        refs.append({"type": "draft", "source": "generated", "generated": True, "validationMode": "generated", "module": module, "file": file, "path": draft_path, "content": "", "confirmed": False})
        known_paths.add(draft_path)

    generated_path = artifacts.get("generatedYamlPath") or ""
    if (
        generated_path
        and generated_path not in known_paths
        and not is_quarantined_path(generated_path)
        and not _looks_like_yaml_text(generated_path)
    ):
        module, file = _task_dir_for_path(generated_path)
        refs.append({"type": "file", "source": "generated", "generated": True, "validationMode": "generated", "module": module, "file": file, "path": generated_path, "content": "", "confirmed": True})
        known_paths.add(generated_path)

    generated = artifacts.get("generatedYaml")
    if isinstance(generated, str) and generated.strip() and _looks_like_yaml_text(generated):
        if not any(item.get("type") == "text" and item.get("content") == generated for item in refs):
            refs.append({"type": "text", "source": "generated", "generated": True, "validationMode": "generated", "module": "", "file": "", "path": "", "content": generated, "confirmed": False})

    artifacts["yamlRefs"] = refs
    return refs


def _yaml_ref_content(ref):
    if ref.get("content"):
        return str(ref.get("content") or "")
    path = ref.get("path") or ""
    return read_text_file(path, "") if path else ""


def _agent_norm_path(value):
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return os.path.normpath(text)
    except Exception:
        return text


def _agent_yaml_ref_source(run, ref):
    """Classify YAML refs so generated gates do not quarantine formal baselines."""
    ref = ref if isinstance(ref, dict) else {}
    artifacts = (run or {}).get("artifacts") if isinstance(run, dict) else {}
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    explicit = str(ref.get("source") or "").strip().lower()
    if explicit in ("generated", "draft", "ai_generated", "agent_generated"):
        return "generated"
    if explicit in ("baseline", "matched", "existing", "manual", "formal"):
        return "baseline"
    if ref.get("generated") is True:
        return "generated"
    ref_type = str(ref.get("type") or "").strip().lower()
    if ref_type in ("draft", "text"):
        return "generated"

    path = _agent_norm_path(ref.get("path") or "")
    generated_paths = set()
    for item in artifacts.get("generatedYamlPaths") or []:
        if isinstance(item, str) and item.strip():
            generated_paths.add(_agent_norm_path(item))
    if artifacts.get("generatedYamlPath"):
        generated_paths.add(_agent_norm_path(artifacts.get("generatedYamlPath")))
    if artifacts.get("draftPath"):
        generated_paths.add(_agent_norm_path(artifacts.get("draftPath")))
    if path and path in generated_paths:
        return "generated"

    matched_paths = {
        _agent_norm_path(item)
        for item in artifacts.get("matchedCases") or []
        if isinstance(item, str) and item.strip() and not _looks_like_yaml_text(item)
    }
    if path and path in matched_paths:
        return "baseline"
    if path and not _agent_is_generated_yaml_run(run):
        return "baseline"
    return "generated" if _agent_is_generated_yaml_run(run) else "baseline"


def _agent_yaml_ref_is_generated(run, ref):
    return _agent_yaml_ref_source(run, ref) == "generated"


AGENT_TRANSITION_ACTIONS = {"aiTap", "aiInput", "ai", "aiAction", "aiAct", "aiScroll"}
AGENT_FOLLOWUP_ACTIONS = {"aiWaitFor", "sleep", "aiAssert", "ai", "aiAction"}
AGENT_ACTION_PREFIX_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*:\s*(.*)$", re.S)


def _agent_dynamic_recent_tasks_cleanup_script():
    """Keep generated cleanup screen-size aware without Midscene `${...}` interpolation."""
    return (
        "input keyevent 3; sleep 1; "
        "size=$(wm size | grep -oE '[0-9]+x[0-9]+' | tail -1); "
        "if [ -n \"$size\" ]; then "
        "w=$(echo \"$size\" | cut -d x -f 1); h=$(echo \"$size\" | cut -d x -f 2); "
        "x=$((w/2)); y1=$((h*82/100)); y2=$((h*18/100)); "
        "input keyevent 187; sleep 1; "
        "input swipe $x $y1 $x $y2 300; input swipe $x $y1 $x $y2 300; input swipe $x $y1 $x $y2 300; "
        "input keyevent 3; else input keyevent 3; fi"
    )


def _agent_normalize_prefixed_action_step(step):
    if not isinstance(step, dict):
        return None
    action_keys = [key for key in list(step.keys()) if key in MIDSCENE_FLOW_ACTIONS]
    if len(action_keys) != 1:
        return None
    action = action_keys[0]
    value = step.get(action)
    if action == "runAdbShell" and isinstance(value, str) and ("${size%x*}" in value or "${size#*x}" in value):
        step[action] = _agent_dynamic_recent_tasks_cleanup_script()
        return {"changed": "replace unsafe runAdbShell recent-task cleanup"}
    if not isinstance(value, str):
        return None
    match = AGENT_ACTION_PREFIX_RE.match(value)
    if not match:
        return None
    prefixed_action = match.group(1)
    if prefixed_action not in MIDSCENE_FLOW_ACTIONS:
        return None
    payload = match.group(2).strip()
    if prefixed_action == action:
        step[action] = safe_int(payload, 800) if action == "sleep" else payload
        return {"changed": f"strip duplicated action prefix {action}"}
    step.pop(action, None)
    if prefixed_action == "sleep":
        step[prefixed_action] = safe_int(payload, 800)
    else:
        step[prefixed_action] = payload
    for child_key in ("timeout", "keyName", "direction", "distance", "scrollType"):
        if prefixed_action in ("sleep", "runAdbShell", "launch"):
            step.pop(child_key, None)
    if prefixed_action != "aiInput":
        step.pop("value", None)
    if prefixed_action == "aiWaitFor":
        step.setdefault("timeout", 60000)
    return {"changed": f"convert {action} value prefix to {prefixed_action}", "from": action, "to": prefixed_action}


def _agent_flow_has_followup_wait(flow, index):
    for nxt in flow[index + 1:index + 4]:
        if isinstance(nxt, dict) and any(action in nxt for action in AGENT_FOLLOWUP_ACTIONS):
            return True
    return False


def _agent_step_prompt_text(step):
    if not isinstance(step, dict):
        return ""
    parts = []
    for value in step.values():
        if isinstance(value, (str, int, float)):
            parts.append(str(value))
    return " ".join(parts)


def _agent_followup_wait_text(step):
    text = _agent_step_prompt_text(step)
    compact = re.sub(r"\s+", "", text)
    if "返回" in compact:
        return "返回后的目标页面已加载完成，关键入口或列表状态可见"
    if any(word in compact for word in ("上传", "选择图片", "相册", "图库")):
        return "图片选择或上传后的页面状态已稳定，可继续下一步"
    if any(word in compact for word in ("确认", "下一步", "生成", "提交", "完成")):
        return "当前操作后的页面已完成加载，下一步入口或结果状态可见"
    if any(word in compact for word in ("滑动", "滚动", "横向")):
        return "滑动后的目标区域已稳定显示"
    return "当前操作后的页面状态已稳定，可继续下一步"


def _agent_assertion_tap_to_wait_prompt(prompt):
    return assertion_tap_to_wait_prompt(prompt)


def _agent_repair_missing_interaction_followups(yaml_text):
    """Locally repair generated YAML executable-gate issues.

    This fixes structure only. It does not add business assertions, expand
    scenarios, or change the intended flow.
    """
    if pyyaml is None or not str(yaml_text or "").strip():
        return {"changed": False, "content": yaml_text, "changes": []}
    try:
        parsed = pyyaml.safe_load(str(yaml_text or ""))
    except Exception:
        return {"changed": False, "content": yaml_text, "changes": []}
    platform, tasks = extract_midscene_tasks(parsed)
    if not tasks:
        return {"changed": False, "content": yaml_text, "changes": []}
    changes = []
    for task_index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            continue
        flow = task.get("flow")
        if not isinstance(flow, list):
            continue
        index = 0
        while index < len(flow):
            step = flow[index]
            if not isinstance(step, dict):
                index += 1
                continue
            normalized_action = _agent_normalize_prefixed_action_step(step)
            if normalized_action:
                normalized_action.update({
                    "task": task.get("name") or f"tasks[{task_index}]",
                    "flowIndex": index,
                })
                changes.append(normalized_action)
            action_keys = [key for key in step.keys() if key in MIDSCENE_FLOW_ACTIONS]
            if "aiTap" in action_keys and tap_prompt_looks_assertion(_agent_step_prompt_text(step)):
                prompt = str(step.get("aiTap") or "").strip()
                if prompt:
                    wait_prompt = _agent_assertion_tap_to_wait_prompt(prompt)
                    step.pop("aiTap", None)
                    step["aiWaitFor"] = wait_prompt
                    step.setdefault("timeout", 60000)
                    changes.append({
                        "task": task.get("name") or f"tasks[{task_index}]",
                        "flowIndex": index,
                        "changed": "aiTap -> aiWaitFor",
                        "prompt": prompt[:180],
                        "waitFor": wait_prompt[:180],
                    })
                    action_keys = [key for key in step.keys() if key in MIDSCENE_FLOW_ACTIONS]
            if (
                any(action in AGENT_TRANSITION_ACTIONS for action in action_keys)
                and not _agent_flow_has_followup_wait(flow, index)
            ):
                wait_text = _agent_followup_wait_text(step)
                wait_step = {"aiWaitFor": wait_text, "timeout": 60000}
                flow.insert(index + 1, wait_step)
                changes.append({
                    "task": task.get("name") or f"tasks[{task_index}]",
                    "afterFlowIndex": index + 1,
                    "inserted": wait_text,
                })
                index += 2
                continue
            index += 1
    if not changes:
        return {"changed": False, "content": yaml_text, "changes": []}
    try:
        content = pyyaml.safe_dump(parsed, allow_unicode=True, sort_keys=False, width=100000)
    except Exception:
        return {"changed": False, "content": yaml_text, "changes": []}
    return {"changed": True, "content": content, "changes": changes}


def _agent_write_repaired_yaml_ref(ref, content):
    path = str(ref.get("path") or "").strip()
    if path:
        write_text_file(path, content)
    else:
        ref["content"] = content
    return ref


def validate_agent_yaml_content(yaml_text):
    """Agent 强校验统一走 yaml_service，避免迁移后各处规则不一致。"""
    check = validate_midscene_yaml_executability(yaml_text)
    return {
        "ok": bool(check.get("ok")),
        "issues": check.get("issues") or [],
        "taskCount": int(check.get("taskCount") or 0),
        "platform": check.get("platform") or "",
        "riskHits": check.get("riskHits") or [],
    }


def _agent_is_generated_yaml_run(run):
    artifacts = (run or {}).setdefault("artifacts", {})
    pipeline = artifacts.get("generationPipeline") if isinstance(artifacts.get("generationPipeline"), dict) else {}
    if pipeline.get("source") in ("ui_yaml_pipeline", "agent_generate_yaml"):
        return True
    if pipeline.get("fallbackAutoConfirmed") or pipeline.get("progressJobId"):
        return True
    validation = artifacts.get("yamlValidation") if isinstance(artifacts.get("yamlValidation"), dict) else {}
    if validation.get("autoConfirmed") or validation.get("autoConfirmedFallback"):
        return True
    return bool(artifacts.get("generatedYamlPaths") and not artifacts.get("matchedCases"))


def _score_agent_yaml_ref_for_execution(run, ref):
    content = _yaml_ref_content(ref)
    source = _agent_yaml_ref_source(run, ref)
    generated_ref = source == "generated"
    score = score_midscene_yaml_executable(content, generated=generated_ref)
    score = {**score, "validationMode": source, "generated": generated_ref}
    scope_review = ref.get("scopeReview") if isinstance(ref.get("scopeReview"), dict) else {}
    if scope_review.get("ok") is False:
        score = dict(score)
        reasons = list(scope_review.get("reasons") or []) + list(score.get("reasons") or [])
        score["score"] = min(int(score.get("score") or 0), 74)
        score["executionLevel"] = "needs_review"
        score["level"] = "needs_review"
        score["ok"] = False
        score["scopeReview"] = scope_review
        score["reasons"] = [str(item) for item in reasons if str(item or "").strip()][:8]
    level = score.get("level") or score.get("executionLevel") or ref.get("executionLevel") or ""
    task_scores = [task for task in (score.get("taskScores") or []) if isinstance(task, dict)]
    runner_candidate = bool(
        ref.get("smoke")
        or ref.get("is_smoke")
        or ref.get("isSmoke")
        or ref.get("smokeCandidate")
        or ref.get("runnerCandidate")
        or score.get("smokeCandidate")
        or any(task.get("smokeCandidate") for task in task_scores)
    )
    return {
        **ref,
        "source": ref.get("source") or source,
        "generated": generated_ref,
        "validationMode": source,
        "executableScore": score,
        "level": level,
        "executionLevel": level,
        "scopeReview": scope_review,
        "smokeCandidate": runner_candidate,
        "runnerCandidate": runner_candidate,
    }


def _agent_yaml_ref_key(ref):
    if not isinstance(ref, dict):
        return ("", "", "", "")
    path = str(ref.get("path") or "").strip()
    if path:
        return ("path", os.path.normpath(path), "", "")
    return (
        "logical",
        str(ref.get("type") or "file"),
        str(ref.get("module") or ""),
        str(ref.get("file") or ""),
    )


def _agent_update_yaml_ref_artifact(run, original_ref, repaired_ref):
    artifacts = (run or {}).setdefault("artifacts", {})
    refs = artifacts.get("yamlRefs") if isinstance(artifacts.get("yamlRefs"), list) else []
    original_key = _agent_yaml_ref_key(original_ref)
    repaired_key = _agent_yaml_ref_key(repaired_ref)
    updated = False
    next_refs = []
    for item in refs:
        if isinstance(item, dict) and _agent_yaml_ref_key(item) in (original_key, repaired_key):
            next_refs.append({**item, **repaired_ref})
            updated = True
        else:
            next_refs.append(item)
    if updated:
        artifacts["yamlRefs"] = next_refs


def _agent_ref_needs_local_execution_repair(scored_ref):
    score = scored_ref.get("executableScore") if isinstance(scored_ref.get("executableScore"), dict) else {}
    reason_text = "；".join(str(item) for item in (score.get("reasons") or []))
    return any(fragment in reason_text for fragment in (
        "交互动作后缺少等待或终态判断",
        "aiTap 描述像检查/断言",
        "动作前缀",
        "${...}",
        "shell 参数展开",
    ))


def _agent_repair_yaml_ref_for_execution(run, ref, *, reason="execution_gate"):
    """Apply deterministic generated-YAML repair before any execution gate.

    WHartTest's useful pattern here is staged validation: repair/load/score must
    happen before a case is selected or rejected for execution. This helper keeps
    that order consistent for validate, precheck and Runner dispatch.
    """
    if not isinstance(ref, dict):
        return ref, None
    content = _yaml_ref_content(ref)
    if not str(content or "").strip():
        return ref, None
    scored_before = _score_agent_yaml_ref_for_execution(run, ref)
    if not _agent_yaml_ref_is_generated(run, ref):
        return scored_before, None
    if not _agent_ref_needs_local_execution_repair(scored_before):
        return scored_before, None
    repaired = _agent_repair_missing_interaction_followups(content)
    if not repaired.get("changed"):
        return scored_before, None

    repaired_ref = {**ref}
    _agent_write_repaired_yaml_ref(repaired_ref, repaired.get("content") or content)
    scored_after = _score_agent_yaml_ref_for_execution(run, repaired_ref)
    dry_after = _agent_yaml_dry_run_for_ref(run, repaired_ref)
    dry_compact = _compact_yaml_dry_run_result(dry_after)
    after_score = scored_after.get("executableScore") if isinstance(scored_after.get("executableScore"), dict) else {}
    auto_repair = {
        "type": "local_yaml_execution_repair",
        "reason": reason,
        "changed": True,
        "changes": list(repaired.get("changes") or [])[:12],
        "ok": bool(dry_compact.get("ok")) and after_score.get("executionLevel") == "executable",
        "before": {
            "executionLevel": (scored_before.get("executableScore") or {}).get("executionLevel"),
            "reasons": list((scored_before.get("executableScore") or {}).get("reasons") or [])[:6],
        },
        "after": {
            "executionLevel": after_score.get("executionLevel"),
            "dryRunOk": bool(dry_compact.get("ok")),
            "reasons": list(after_score.get("reasons") or [])[:6],
        },
        "dryRun": dry_compact,
    }
    scored_after["autoRepair"] = auto_repair
    _agent_update_yaml_ref_artifact(run, ref, scored_after)
    artifacts = (run or {}).setdefault("artifacts", {})
    repairs = artifacts.get("yamlExecutionRepairs") if isinstance(artifacts.get("yamlExecutionRepairs"), list) else []
    repairs.append({
        "module": scored_after.get("module") or "",
        "file": scored_after.get("file") or "",
        "path": scored_after.get("path") or "",
        **auto_repair,
    })
    artifacts["yamlExecutionRepairs"] = repairs[-50:]
    return scored_after, auto_repair


def _agent_repair_yaml_refs_for_execution(run, refs, *, reason="execution_gate"):
    repaired_refs = []
    repairs = []
    for ref in refs or []:
        repaired_ref, repair = _agent_repair_yaml_ref_for_execution(run, ref, reason=reason)
        repaired_refs.append(repaired_ref)
        if repair:
            repairs.append(repair)
    return repaired_refs, repairs


def _agent_generated_runner_smoke_limit(run):
    """Return dynamic smoke batch size for newly generated YAML.

    Generation computes this from requirement size. The environment variable is
    only an upper bound, not a fixed batch size for every requirement.
    """
    artifacts = (run or {}).get("artifacts") if isinstance(run, dict) else {}
    if not isinstance(artifacts, dict):
        artifacts = {}
    candidates = []
    pipeline = artifacts.get("generationPipeline") if isinstance(artifacts.get("generationPipeline"), dict) else {}
    review = pipeline.get("review") if isinstance(pipeline.get("review"), dict) else {}
    candidates.append(review.get("generation_targets") if isinstance(review, dict) else None)
    generated = artifacts.get("generatedCases") if isinstance(artifacts.get("generatedCases"), dict) else {}
    generated_review = generated.get("review") if isinstance(generated.get("review"), dict) else {}
    candidates.append(generated_review.get("generation_targets") if isinstance(generated_review, dict) else None)
    for item in candidates:
        if not isinstance(item, dict):
            continue
        limit = safe_int(item.get("smoke_cases"), 0)
        if limit > 0:
            return max(1, min(AGENT_GENERATED_RUNNER_FIRST_SMOKE_LIMIT, limit))
    return AGENT_GENERATED_RUNNER_FIRST_SMOKE_LIMIT


def _select_agent_runner_refs(run, refs):
    """Gate Agent-generated YAML before Runner creation.

    Hand-maintained or user-selected baseline YAML is not limited here. Generated
    YAML must prove it is executable. Only the first smoke batch is sent first;
    the remaining executable YAML is preserved for full execution after smoke
    proves it can be dispatched and run.
    """
    refs = [ref for ref in refs or [] if isinstance(ref, dict)]
    artifacts = (run or {}).setdefault("artifacts", {})
    if not refs or not _agent_is_generated_yaml_run(run):
        return refs, {
            "enabled": False,
            "reason": "非 Agent 新生成 YAML，不启用自动冒烟限流",
            "selectedCount": len(refs),
            "blockedCount": 0,
            "results": [],
            "blocked": [],
        }
    refs, repairs = _agent_repair_yaml_refs_for_execution(run, refs, reason="runner_gate")
    scored = [_score_agent_yaml_ref_for_execution(run, ref) for ref in refs]
    _sync_agent_generated_case_groups(artifacts, scored)
    smoke_limit = _agent_generated_runner_smoke_limit(run)
    selected, blocked = rank_executable_yaml_refs(scored, limit=smoke_limit)
    deferred = [
        item for item in blocked
        if str(item.get("gateReason") or "").startswith("超过自动冒烟首批上限")
        or str(item.get("gateReason") or "").startswith("非首批冒烟候选")
    ]
    blocking = [item for item in blocked if item not in deferred]
    gate = {
        "enabled": True,
        "limit": smoke_limit,
        "firstSmokeLimit": AGENT_GENERATED_RUNNER_FIRST_SMOKE_LIMIT,
        "maxLimit": AGENT_GENERATED_RUNNER_SMOKE_LIMIT,
        "expandLimit": AGENT_GENERATED_RUNNER_EXPAND_LIMIT,
        "expandBatchLimit": AGENT_GENERATED_RUNNER_EXPAND_BATCH_LIMIT,
        "totalCount": len(scored),
        "selectedCount": len(selected),
        "blockedCount": len(blocked),
        "blockingCount": len(blocking),
        "deferredCount": len(deferred),
        "executableCount": sum(1 for item in scored if (item.get("executableScore") or {}).get("executionLevel") == "executable"),
        "needsReviewCount": sum(1 for item in scored if (item.get("executableScore") or {}).get("executionLevel") == "needs_review"),
        "draftCount": sum(1 for item in scored if (item.get("executableScore") or {}).get("executionLevel") == "draft"),
        "manualCount": sum(1 for item in scored if (item.get("executableScore") or {}).get("executionLevel") == "manual"),
        "fallbackSmokeSelection": bool(selected) and any(item.get("fallbackSmokeSelection") for item in selected),
        "results": scored,
        "selected": selected,
        "blocked": blocked,
        "blocking": blocking,
        "deferred": deferred,
        "autoRepairCount": len(repairs),
        "rule": "Agent 新生成 YAML 首批优先下发 executable 冒烟候选；没有候选时按 executable 评分兜底选择首批。首批冒烟用于验证 YAML 能下发、能运行、能产生日志；只有脚本/YAML/定位/超时类问题会阻断扩展，产品结果失败会记录后继续按批执行。",
    }
    execution_plan = build_generated_yaml_execution_plan(
        scored,
        selected,
        deferred,
        blocking,
        smoke_limit=smoke_limit,
        first_smoke_upper=AGENT_GENERATED_RUNNER_FIRST_SMOKE_LIMIT,
        expand_limit=AGENT_GENERATED_RUNNER_EXPAND_LIMIT,
        expand_batch_limit=AGENT_GENERATED_RUNNER_EXPAND_BATCH_LIMIT,
        repairs=repairs,
    )
    gate["executionPlan"] = execution_plan
    gate["executionReadiness"] = execution_plan.get("readiness") or {}
    artifacts["runnerExecutionGate"] = gate
    artifacts["generatedYamlExecutionPlan"] = execution_plan
    return selected, gate


def _agent_yaml_dry_run_for_ref(run, ref):
    from task_server.services.yaml_service import dry_run_midscene_yaml

    module = str(ref.get("module") or "").strip()
    file = str(ref.get("file") or "").strip()
    path = str(ref.get("path") or "").strip()
    if path and not (module and file):
        module, file = _task_dir_for_path(path)
    content = _yaml_ref_content(ref)
    dry = dry_run_midscene_yaml(content, module=module, file=file, app_package=_agent_app_package(run))
    return dry


def _runner_supports_yaml_dry_run(runner_id):
    runner_id = str(runner_id or "").strip()
    if not runner_id:
        return False, "未指定 Runner，使用本地 dry-run"
    try:
        from task_server.services.runner_service import list_runners

        runner = (list_runners() or {}).get(runner_id) or {}
        caps = runner.get("capabilities") if isinstance(runner.get("capabilities"), dict) else {}
        if not runner.get("online"):
            return False, f"Runner {runner_id} 不在线，使用本地 dry-run"
        if caps.get("yaml_dry_run"):
            return True, f"Runner {runner_id} 支持真实 YAML dry-run"
        return False, f"Runner {runner_id} 未上报 yaml_dry_run 能力，使用本地 dry-run"
    except Exception as exc:
        return False, f"读取 Runner 能力失败：{str(exc)[:120]}"


def _compact_yaml_dry_run_result(dry):
    return {
        "ok": bool((dry or {}).get("ok")),
        "mode": (dry or {}).get("mode") or "mock_dry_run",
        "executionLevel": (dry or {}).get("executionLevel") or "",
        "taskCount": (dry or {}).get("taskCount") or 0,
        "errors": list((dry or {}).get("errors") or [])[:12],
        "warnings": list((dry or {}).get("warnings") or [])[:8],
        "normalizedChanged": bool((dry or {}).get("normalizedChanged")),
        "guardChanges": list((dry or {}).get("guardChanges") or [])[:12],
        "message": (dry or {}).get("message") or "",
    }


def _agent_merge_runner_wait_results(*results):
    merged = {"completed": [], "failed": [], "timeout": []}
    for result in results:
        if not isinstance(result, dict):
            continue
        for key in ("completed", "failed", "timeout"):
            merged[key].extend(list(result.get(key) or []))
    return merged


def _agent_create_runner_jobs_for_refs(
    run,
    refs,
    selected_runner_id,
    selected_device_id,
    selected_device_strategy,
    *,
    runner_dry_run_enabled=False,
    dry_run_timeout=120,
    initial_blocked=None,
    phase="smoke",
):
    from task_server.services import job_service

    job_ids = []
    dry_run_results = []
    dry_run_blocked = list(initial_blocked or [])
    runner_dry_run_jobs = []
    for ref in refs or []:
        try:
            ref, pre_dispatch_repair = _agent_repair_yaml_ref_for_execution(run, ref, reason=f"runner_dispatch:{phase}")
            full_path = str(ref.get("path") or "")
            if not os.path.exists(full_path):
                dry_run_blocked.append({
                    "module": ref.get("module") or "",
                    "file": ref.get("file") or os.path.basename(full_path),
                    "path": full_path,
                    "phase": phase,
                    "reason": "YAML 文件不存在，未创建 Runner 任务",
                })
                continue
            mod = ref.get("module") or _task_dir_for_path(full_path)[0]
            fn = ref.get("file") or os.path.basename(full_path)
            dry = _agent_yaml_dry_run_for_ref(run, {**ref, "module": mod, "file": fn, "path": full_path})
            dry_compact = _compact_yaml_dry_run_result(dry)
            dry_row = {"module": mod, "file": fn, "path": full_path, "phase": phase, **dry_compact}
            if pre_dispatch_repair:
                dry_row["autoRepair"] = pre_dispatch_repair
            dry_run_results.append(dry_row)
            if not dry_compact.get("ok"):
                dry_run_blocked.append({
                    "module": mod,
                    "file": fn,
                    "path": full_path,
                    "phase": phase,
                    "reason": "Runner 下发前 dry-run 未通过",
                    "errors": list(dry_compact.get("errors") or [])[:8],
                })
                continue
            if runner_dry_run_enabled:
                dry_task_names = _agent_yaml_task_names_for_runner(full_path)
                dry_job = job_service.create_job({
                    "module": mod,
                    "file": fn,
                    "target_task_name": dry_task_names[0] if len(dry_task_names) == 1 else "",
                    "task_names": dry_task_names,
                    "current_task_name": dry_task_names[0] if dry_task_names else "",
                    "runner_id": selected_runner_id,
                    "device_id": selected_device_id,
                    "device_strategy": "fixed" if selected_device_id else "auto",
                    "job_type": "yaml_dry_run",
                    "type": "yaml_dry_run",
                    "run_mode": "yaml_dry_run",
                    "dry_run": True,
                    "parent_run_id": run.get("runId", ""),
                    "phase": phase,
                })
                dry_job_id = dry_job.get("job_id") if dry_job else ""
                if dry_job_id:
                    runner_dry_run_jobs.append(dry_job_id)
                    dry_row["runnerDryRunJobId"] = dry_job_id
                    wait_dry = job_service.wait_jobs_finished(
                        [dry_job_id],
                        run,
                        timeout=dry_run_timeout,
                        interval=3,
                        phase=f"{phase}-dry-run",
                    )
                    dry_completed = list(wait_dry.get("completed") or [])
                    dry_failed = list(wait_dry.get("failed") or [])
                    dry_timeout = list(wait_dry.get("timeout") or [])
                    dry_row["runnerDryRun"] = {
                        "completed": len(dry_completed),
                        "failed": len(dry_failed),
                        "timeout": len(dry_timeout),
                        "waitTimedOut": bool(dry_timeout),
                        "inconclusive": bool(dry_timeout and not dry_failed),
                        "jobId": dry_job_id,
                    }
                    if dry_failed:
                        dry_run_blocked.append({
                            "module": mod,
                            "file": fn,
                            "path": full_path,
                            "phase": phase,
                            "reason": "Runner 真实 dry-run 未通过",
                            "job_id": dry_job_id,
                            "errors": [
                                str(item.get("error") or item.get("stderr_tail") or item.get("status") or "dry-run 失败")[:220]
                                for item in dry_failed[:5]
                            ],
                        })
                        continue
                    if dry_timeout and not dry_completed:
                        dry_row.setdefault("warnings", []).append(
                            "Runner 真实 dry-run 等待超时，未判定 YAML 失败；继续按本地 dry-run 下发正式任务"
                        )
            task_names = _agent_yaml_task_names_for_runner(full_path)
            target_task_name = task_names[0] if len(task_names) == 1 else ""
            job = job_service.create_job({
                "module": mod,
                "file": fn,
                "target_task_name": target_task_name,
                "task_names": task_names,
                "current_task_name": target_task_name or (task_names[0] if task_names else ""),
                "runner_id": selected_runner_id,
                "device_id": selected_device_id,
                "device_strategy": selected_device_strategy,
                "parent_run_id": run.get("runId", ""),
                "phase": phase,
            })
            if job and job.get("job_id"):
                job_ids.append(job["job_id"])
        except Exception as exc:
            dry_run_blocked.append({
                "module": ref.get("module") or "",
                "file": ref.get("file") or os.path.basename(str(ref.get("path") or "")),
                "path": str(ref.get("path") or ""),
                "phase": phase,
                "reason": f"创建 Runner 任务前异常：{str(exc)[:180]}",
            })
    return {
        "jobIds": job_ids,
        "dryRunResults": dry_run_results,
        "dryRunBlocked": dry_run_blocked,
        "runnerDryRunJobs": runner_dry_run_jobs,
    }


def _agent_yaml_dry_run_rows(run, refs):
    results = []
    issues = []
    ok_count = 0
    for ref in refs:
        source = _agent_yaml_ref_source(run, ref)
        generated_ref = source == "generated"
        ref, auto_repair = _agent_repair_yaml_ref_for_execution(run, ref, reason="yaml_dry_run")
        label = ref.get("path") or ref.get("file") or ref.get("type")
        content = _yaml_ref_content(ref)
        strong_check = validate_agent_yaml_content(content)
        scored_ref = _score_agent_yaml_ref_for_execution(run, ref)
        executable_score = scored_ref.get("executableScore") if isinstance(scored_ref.get("executableScore"), dict) else {}
        dry = _agent_yaml_dry_run_for_ref(run, ref)
        dry_compact = _compact_yaml_dry_run_result(dry)
        executable_reason_text = "；".join(str(item) for item in (executable_score.get("reasons") or []))
        needs_repair = (
            generated_ref
            and
            bool(content)
            and (
                not dry_compact.get("ok")
                or "交互动作后缺少等待或终态判断" in executable_reason_text
                or "aiTap 描述像检查/断言" in executable_reason_text
                or "动作前缀" in executable_reason_text
            )
        )
        if needs_repair:
            repaired = _agent_repair_missing_interaction_followups(content)
            if repaired.get("changed"):
                repaired_ref = dict(ref)
                _agent_write_repaired_yaml_ref(repaired_ref, repaired.get("content") or content)
                strong_check_after = validate_agent_yaml_content(repaired.get("content") or "")
                scored_ref_after = _score_agent_yaml_ref_for_execution(run, repaired_ref)
                executable_score_after = scored_ref_after.get("executableScore") if isinstance(scored_ref_after.get("executableScore"), dict) else {}
                dry_after = _agent_yaml_dry_run_for_ref(run, repaired_ref)
                dry_compact_after = _compact_yaml_dry_run_result(dry_after)
                auto_repair = {
                    "type": "local_yaml_executable_gate_repair",
                    "changed": True,
                    "changes": list(repaired.get("changes") or [])[:12],
                    "ok": bool(dry_compact_after.get("ok")) and executable_score_after.get("executionLevel") == "executable",
                    "before": {
                        "dryRunOk": bool(dry_compact.get("ok")),
                        "executionLevel": executable_score.get("executionLevel"),
                    },
                    "after": {
                        "dryRunOk": bool(dry_compact_after.get("ok")),
                        "executionLevel": executable_score_after.get("executionLevel"),
                    },
                }
                ref = repaired_ref
                content = repaired.get("content") or content
                strong_check = strong_check_after
                scored_ref = scored_ref_after
                executable_score = executable_score_after
                dry_compact = dry_compact_after
        scope_review = scored_ref.get("scopeReview") if isinstance(scored_ref.get("scopeReview"), dict) else {}
        scope_issues = list(scope_review.get("reasons") or []) if scope_review.get("ok") is False else []
        if generated_ref:
            ref_issues = scope_issues or dry_compact.get("errors") or strong_check.get("issues") or []
        else:
            ref_issues = scope_issues or dry_compact.get("errors") or strong_check.get("issues") or []
        if generated_ref and not ref_issues and executable_score.get("executionLevel") != "executable":
            ref_issues = list(executable_score.get("reasons") or [])[:5] or [f"执行等级为 {executable_score.get('executionLevel') or 'unknown'}"]
        row_ok = bool(dry_compact.get("ok")) and bool(strong_check.get("ok"))
        if generated_ref:
            row_ok = row_ok and executable_score.get("executionLevel") == "executable"
        elif row_ok:
            executable_score = {
                **executable_score,
                "ok": True,
                "score": max(int(executable_score.get("score") or 0), 80),
                "executionLevel": "executable",
                "level": "executable",
                "validationMode": "baseline",
                "baselineValidation": True,
                "rule": "正式/匹配基线 YAML 使用基线校验模式：平台加载与 dry-run 通过即可继续执行；生成质量评分仅作为提示。",
            }
            scored_ref = {
                **scored_ref,
                "source": "baseline",
                "validationMode": "baseline",
                "generated": False,
                "executionLevel": "executable",
                "level": "executable",
                "executableScore": executable_score,
            }
            ref_issues = []
        row = {
            **scored_ref,
            "ok": row_ok,
            "issues": ref_issues,
            "taskCount": dry_compact.get("taskCount") or strong_check.get("taskCount", 0),
            "executionLevel": executable_score.get("executionLevel") or dry_compact.get("executionLevel"),
            "validationMode": source,
            "generated": generated_ref,
            "executableScore": executable_score,
            "dryRun": dry_compact,
            "strongCheck": strong_check,
        }
        if auto_repair:
            row["autoRepair"] = auto_repair
        results.append(row)
        if row.get("ok"):
            ok_count += 1
        else:
            issues.append(f"{label}: {'; '.join(str(item) for item in ref_issues[:5])}")
    return results, issues, ok_count


def _source_ref_value(refs, *names):
    refs = refs if isinstance(refs, dict) else {}
    for name in names:
        value = refs.get(name)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _agent_normalized_input(run):
    data = run.get("normalizedInput") if isinstance(run, dict) else {}
    return data if isinstance(data, dict) else {}


def _agent_source_inputs(run):
    data = run.get("sourceInputs") if isinstance(run, dict) else {}
    return data if isinstance(data, dict) else {}


def _agent_source_files(run):
    normalized = _agent_normalized_input(run)
    source_inputs = _agent_source_inputs(run)
    items = []
    for files in (normalized.get("files"), source_inputs.get("files"), run.get("files") if isinstance(run, dict) else None):
        if isinstance(files, list):
            items.extend(item for item in files if isinstance(item, dict))
    return items


def _agent_source_images(run):
    normalized = _agent_normalized_input(run)
    source_inputs = _agent_source_inputs(run)
    images = []
    for raw_images in (normalized.get("images"), source_inputs.get("images"), run.get("images") if isinstance(run, dict) else None):
        if isinstance(raw_images, list):
            images.extend(item for item in raw_images if isinstance(item, dict))
    image_items = []
    seen = set()

    def image_key(item):
        return (
            str(item.get("name") or "").strip(),
            str(item.get("size") or "").strip(),
            str(item.get("kind") or item.get("type") or "").strip(),
        )

    for item in images:
        if not isinstance(item, dict):
            continue
        key = image_key(item)
        if key in seen:
            continue
        seen.add(key)
        image_items.append(item)
    for item in _agent_source_files(run):
        name = str(item.get("name") or "")
        kind = str(item.get("kind") or "")
        content_type = str(item.get("type") or "")
        if kind == "screenshot" or content_type.startswith("image/") or re.search(r"\.(png|jpe?g)$", name, re.I):
            key = image_key(item)
            if key in seen:
                continue
            seen.add(key)
            image_items.append(item)
    return image_items


def _agent_file_kind(item):
    kind = str(item.get("kind") or "").strip()
    if kind:
        return kind
    name = str(item.get("name") or "")
    content_type = str(item.get("type") or "")
    if content_type.startswith("image/") or re.search(r"\.(png|jpe?g)$", name, re.I):
        return "screenshot"
    if re.search(r"\.(txt|md|json|mm|ya?ml)$", name, re.I):
        return "requirement_text"
    return "requirement_file"


def _agent_pdf_text_from_base64(item, limit=6000):
    raw = str(item.get("contentBase64") or "").strip()
    if not raw:
        return ""
    try:
        data = base64.b64decode(raw, validate=False)
        import pypdf
        reader = pypdf.PdfReader(io.BytesIO(data))
        parts = []
        total = 0
        for page in reader.pages[:12]:
            text = page.extract_text() or ""
            if not text.strip():
                continue
            parts.append(text)
            total += len(text)
            if total >= limit:
                break
        return "\n".join(parts).strip()[:limit]
    except Exception:
        return ""


def _clean_agent_source_text(text, limit=0):
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = value.replace("\u2efa", "页").replace("\u2fb3", "首")
    value = value.translate(str.maketrans({
        "⼀": "一", "⼊": "入", "⼉": "儿", "⼝": "口", "⼤": "大", "⼩": "小",
        "⼦": "子", "⽂": "文", "⽚": "片", "⽣": "生", "⽤": "用", "⽬": "目",
        "⽰": "示", "⾃": "自", "⾏": "行", "⾼": "高", "⾳": "音", "⾸": "首",
        "⻓": "长", "⻚": "页", "⻰": "龙",
    }))
    value = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", value)
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r"\n{3,}", "\n\n", value)
    value = value.strip()
    return value[:limit] if limit and limit > 0 else value


def _agent_file_text(item, limit=1800):
    content = item.get("content")
    if isinstance(content, str) and content.strip():
        return _clean_agent_source_text(content, limit=limit)
    text = item.get("text")
    if isinstance(text, str) and text.strip():
        return _clean_agent_source_text(text, limit=limit)
    name = str(item.get("name") or "")
    content_type = str(item.get("type") or "")
    if re.search(r"\.pdf$", name, re.I) or "pdf" in content_type.lower():
        return _clean_agent_source_text(_agent_pdf_text_from_base64(item, limit=limit), limit=limit)
    return ""


def _agent_file_meta(item):
    name = str(item.get("name") or "未命名资料").strip()
    kind = _agent_file_kind(item)
    return {
        "name": name,
        "kind": kind,
        "type": str(item.get("type") or "").strip(),
        "size": int(item.get("size") or 0) if str(item.get("size") or "").isdigit() else item.get("size") or 0,
        "hasText": bool(_agent_file_text(item, limit=20)),
        "hasBinary": bool(item.get("contentBase64")),
        "skippedContent": bool(item.get("skippedContent")),
        "note": str(item.get("note") or "").strip(),
    }


def _agent_text_preview(value, limit=500):
    text = _clean_agent_source_text(value or "")
    if not text:
        return ""
    limit = max(40, int(limit or 500))
    return text if len(text) <= limit else f"{text[:limit].rstrip()}..."


def _agent_list_length(value):
    return len(value) if isinstance(value, list) else 0


def _agent_file_label(kind):
    kind = str(kind or "").strip()
    if kind == "screenshot":
        return "截图"
    if kind == "requirement_text":
        return "文本"
    if kind == "requirement_file":
        return "文档"
    return kind or "资料"


def _agent_public_file_meta(item):
    meta = dict(item or {}) if isinstance(item, dict) else {}
    name = str(meta.get("name") or "未命名资料").strip()
    kind = str(meta.get("kind") or _agent_file_kind(meta) or "requirement_file").strip()
    return {
        "name": name,
        "kind": kind,
        "kindLabel": _agent_file_label(kind),
        "type": str(meta.get("type") or "").strip(),
        "size": meta.get("size") or 0,
        "hasText": bool(meta.get("hasText") or meta.get("content") or meta.get("text")),
        "hasBinary": bool(meta.get("hasBinary") or meta.get("contentBase64")),
        "skippedContent": bool(meta.get("skippedContent")),
        "note": str(meta.get("note") or "").strip(),
    }


def _agent_public_file_list(items, limit=10):
    result = []
    seen = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        meta = _agent_public_file_meta(item)
        key = (meta.get("name"), str(meta.get("size")), meta.get("kind"))
        if key in seen:
            continue
        seen.add(key)
        result.append(meta)
        if len(result) >= limit:
            break
    return result


def _agent_figma_page_brief(page):
    if not isinstance(page, dict):
        return {}
    figma = page.get("figma") if isinstance(page.get("figma"), dict) else {}
    return {
        "name": str(page.get("page_name") or page.get("pageName") or figma.get("page_name") or "Figma 页面").strip(),
        "nodeId": str(page.get("node_id") or page.get("nodeId") or figma.get("node_id") or "").strip(),
        "score": page.get("relevance_score", figma.get("relevance_score")),
        "reason": _agent_text_preview(page.get("relevance_reason") or figma.get("relevance_reason") or "", 160),
        "image": str(page.get("screenshot") or page.get("image_name") or figma.get("screenshot") or "").strip(),
    }


def _agent_input_summary(run, detailed=False):
    """Return a user-readable, content-safe summary of the original Agent input."""
    run = run if isinstance(run, dict) else {}
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    source_context = artifacts.get("sourceContext") if isinstance(artifacts.get("sourceContext"), dict) else {}
    normalized = _agent_normalized_input(run)
    source_inputs = _agent_source_inputs(run) or (normalized.get("sourceInputs") if isinstance(normalized.get("sourceInputs"), dict) else {})
    refs = run.get("sourceRefs") if isinstance(run.get("sourceRefs"), dict) else {}

    target = str(run.get("target") or run.get("goal") or normalized.get("text") or "").strip()
    requirement_text = (
        source_context.get("requirementText")
        or normalized.get("requirementText")
        or source_inputs.get("requirementText")
        or run.get("requirementText")
        or run.get("requirement")
        or ""
    )
    figma_url = (
        source_context.get("figmaUrl")
        or normalized.get("figmaUrl")
        or source_inputs.get("figmaUrl")
        or _source_ref_value(refs, "figmaUrl", "figma_url")
    )
    uploaded_files_raw = source_context.get("uploadedFiles") if isinstance(source_context.get("uploadedFiles"), list) else _agent_source_files(run)
    uploaded_images_raw = source_context.get("uploadedImages") if isinstance(source_context.get("uploadedImages"), list) else _agent_source_images(run)
    uploaded_files = _agent_public_file_list(uploaded_files_raw or [], 200)
    uploaded_images = _agent_public_file_list(uploaded_images_raw or [], 80)
    file_count = int(source_context.get("fileCount") or len(uploaded_files_raw or []))
    screenshot_count = int(source_context.get("imageCount") or len(uploaded_images_raw or []))
    requirement_file_count = int(
        source_context.get("requirementFileCount")
        or len([f for f in (uploaded_files or []) if str(f.get("kind") or "") != "screenshot"])
    )
    figma_used = source_context.get("figmaUsedPages") or source_context.get("uiDesigns") or []
    figma_ignored = source_context.get("figmaIgnoredPages") or []
    figma_image_count = int(
        source_context.get("figmaImageCount")
        or _agent_list_length(source_context.get("uiDesignAssets"))
        or 0
    )
    source_type = str(source_context.get("sourceType") or run.get("sourceType") or normalized.get("sourceType") or "manual").strip().lower()
    source_type_text = {
        "manual": "直接输入",
        "requirement": "需求资料",
        "figma": "Figma 设计稿",
        "failed_job": "失败任务",
    }.get(source_type, source_type or "直接输入")

    app_name = str(run.get("appName") or "").strip()
    app_package = _agent_app_package(run)
    platform = str(run.get("platform") or "android").strip()
    runner_id = str(run.get("runnerId") or normalized.get("runnerId") or "").strip()
    device_id = str(run.get("deviceId") or normalized.get("deviceId") or "").strip()
    device_strategy = str(run.get("deviceStrategy") or normalized.get("deviceStrategy") or "").strip()
    model = str(run.get("aiModel") or run.get("model") or "").strip()
    execution_mode = str(run.get("executionMode") or "").strip().upper()

    badges = [
        f"来源：{source_type_text}",
        f"Figma：{'1 个链接' if figma_url else '未提供'}",
        f"Figma 页面：{_agent_list_length(figma_used)}",
        f"Figma UI 图：{figma_image_count}",
        f"上传文档：{requirement_file_count}",
        f"上传截图：{screenshot_count}",
    ]
    if app_name:
        badges.append(f"应用：{app_name}")
    if device_strategy == "auto":
        badges.append("设备：自动选择")
    elif runner_id or device_id:
        badges.append(f"设备：{runner_id or '任意 Runner'} / {device_id or '任意设备'}")
    if model:
        badges.append(f"模型：{model}")

    summary = {
        "target": target,
        "sourceType": source_type,
        "sourceTypeText": source_type_text,
        "requirementTextPreview": _agent_text_preview(requirement_text, 1500 if detailed else 420),
        "figmaUrl": str(figma_url or "").strip(),
        "sourceSummary": str(source_context.get("sourceSummary") or "").strip(),
        "figmaPageCount": _agent_list_length(figma_used),
        "figmaIgnoredCount": _agent_list_length(figma_ignored),
        "figmaUiImageCount": figma_image_count,
        "fileCount": file_count,
        "requirementFileCount": requirement_file_count,
        "screenshotCount": screenshot_count,
        "files": uploaded_files[:20 if detailed else 6],
        "images": uploaded_images[:12 if detailed else 4],
        "appName": app_name,
        "appPackage": app_package,
        "platform": platform,
        "scope": str(run.get("scope") or "").strip(),
        "mode": str(run.get("mode") or "").strip(),
        "executionMode": execution_mode,
        "runnerId": runner_id,
        "deviceId": device_id,
        "deviceStrategy": device_strategy,
        "model": model,
        "badges": badges,
        "compactLine": "；".join(badges[:6]),
    }
    if detailed:
        summary["figmaUsedPages"] = [_agent_figma_page_brief(item) for item in figma_used[:30] if isinstance(item, dict)]
        summary["figmaIgnoredPages"] = [_agent_figma_page_brief(item) for item in figma_ignored[:20] if isinstance(item, dict)]
        summary["sourceRefs"] = {
            key: str(value)
            for key, value in refs.items()
            if key in ("generateJobId", "caseSetId", "figmaUrl", "failedJobId", "jobId") and value not in (None, "")
        }
    return summary


def _agent_run_with_input_summary(run, detailed=False):
    if not isinstance(run, dict):
        return run
    enriched = dict(run)
    enriched["inputSummary"] = _agent_input_summary(run, detailed=detailed)
    return enriched


def _agent_source_material_context(run):
    normalized = _agent_normalized_input(run)
    source_inputs = _agent_source_inputs(run)
    files = _agent_source_files(run)
    images = _agent_source_images(run)
    text_parts = []
    for raw_text in (normalized.get("requirementText"), source_inputs.get("requirementText"), run.get("requirementText") if isinstance(run, dict) else None):
        if raw_text:
            text_parts.append(str(raw_text or "").strip())
    for item in files:
        text = _agent_file_text(item, limit=6000)
        if text:
            text_parts.append(f"【{item.get('name') or '资料'}】\n{text}")
    metas = [_agent_file_meta(item) for item in files]
    figma_url = (
        normalized.get("figmaUrl")
        or source_inputs.get("figmaUrl")
        or (run.get("figmaUrl") if isinstance(run, dict) else "")
    )
    return {
        "figmaUrl": str(figma_url or "").strip(),
        "requirementText": "\n\n".join(part for part in text_parts if part).strip(),
        "uploadedFiles": metas,
        "uploadedImages": [_agent_file_meta(item) for item in images],
        "fileCount": len(files),
        "imageCount": len(images),
        "requirementFileCount": len([m for m in metas if m.get("kind") != "screenshot"]),
    }


def _agent_truthy(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "on", "enabled", "启用", "使用", "是"}


def _agent_use_saved_knowledge_context(run, refs=None):
    """Agent 新需求默认只使用本次输入资料，不自动混入历史页面知识。"""
    refs = refs if isinstance(refs, dict) else {}
    normalized = _agent_normalized_input(run)
    source_inputs = _agent_source_inputs(run)
    containers = (normalized, source_inputs, refs, run if isinstance(run, dict) else {})
    for data in containers:
        if not isinstance(data, dict):
            continue
        for key in ("useKnowledgeContext", "use_knowledge_context", "includeKnowledge", "include_knowledge"):
            if key in data:
                return _agent_truthy(data.get(key))
        if data.get("knowledge_page_ids") or data.get("knowledgePageIds"):
            return True
    return False


def _infer_agent_source_type(source_type, material, refs=None):
    source_type = str(source_type or "manual").lower()
    refs = refs if isinstance(refs, dict) else {}
    if source_type != "manual":
        return source_type
    has_requirement = bool(
        material.get("requirementText")
        or material.get("requirementFileCount")
        or _source_ref_value(refs, "generateJobId", "jobId", "caseSetId", "case_set_id")
    )
    has_figma = bool(material.get("figmaUrl") or _source_ref_value(refs, "figmaUrl", "figma_url"))
    if has_requirement:
        return "requirement"
    if has_figma:
        return "figma"
    return source_type


def _agent_explicit_reuse_requested(run, source_type=""):
    text = " ".join([
        str(run.get("target") or ""),
        str(run.get("scope") or ""),
        str(source_type or run.get("sourceType") or ""),
    ])
    return bool(re.search(r"(回归|基线|复用|已有用例|旧用例|失败任务|failed[_ -]?job|regression|reuse|baseline)", text, re.I))


def _agent_is_new_requirement_run(run, source_context=None):
    source_context = source_context if isinstance(source_context, dict) else (run.get("artifacts") or {}).get("sourceContext") or {}
    source_type = str(source_context.get("sourceType") or run.get("sourceType") or "manual").lower()
    has_requirement = bool(source_context.get("requirementText") or source_context.get("figmaUrl") or source_context.get("uiDesigns"))
    return source_type in ("requirement", "figma") and has_requirement and not _agent_explicit_reuse_requested(run, source_type)


def _find_generate_job(generate_job_id="", case_set_id=""):
    """Find a generation job by job_id or case_set_id without starting a new generation."""
    try:
        from task_server.services import yaml_service
        if generate_job_id:
            job = yaml_service.load_generate_job(generate_job_id)
            if isinstance(job, dict):
                return job
        if case_set_id:
            for job in yaml_service.list_generate_jobs(limit=300):
                result = job.get("result") or {}
                summary = job.get("summary") or {}
                if case_set_id in (
                    str(job.get("case_set_id") or ""),
                    str(result.get("case_set_id") or ""),
                    str(summary.get("case_set_id") or ""),
                    str(job.get("id") or ""),
                ):
                    full = yaml_service.load_generate_job(job.get("job_id") or "") or job
                    return full
    except Exception:
        return None
    return None


def _find_job_for_agent(job_id=""):
    if not job_id:
        return None
    try:
        from task_server.services import job_service
        jobs = job_service.load_jobs()
        return next((j for j in jobs if str(j.get("job_id") or "") == str(job_id)), None)
    except Exception:
        return None


def _agent_app_package(run):
    refs = run.get("sourceRefs") if isinstance(run.get("sourceRefs"), dict) else {}
    explicit = str(run.get("appPackage") or run.get("app_package") or refs.get("appPackage") or refs.get("app_package") or "").strip()
    if explicit:
        return explicit
    app_name = str(run.get("appName") or "").strip()
    for app_key, package in APP_PACKAGE_BY_KEY.items():
        if app_key and app_key in app_name:
            return package
    return os.getenv("APP_PACKAGE", "com.kfb.model").strip() or "com.kfb.model"


def _agent_safe_run_file_id(run):
    raw = str((run or {}).get("runId") or unique_millis_id("agent"))
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._-")
    return safe[:96] or "agent"


def _agent_prepared_figma_context_path(run):
    return safe_join(AGENT_DRAFT_DIR, f"{_agent_safe_run_file_id(run)}-figma-context.json")


def _agent_figma_page_key(item):
    if not isinstance(item, dict):
        return ""
    figma = item.get("figma") or {}
    for key in (
        figma.get("node_id"),
        item.get("node_id"),
        item.get("page_id"),
        item.get("pageId"),
        item.get("screenshot"),
        item.get("image_name"),
    ):
        value = str(key or "").strip()
        if value:
            return value
    return " ".join(str(item.get(key) or "").strip() for key in ("page_name", "route", "description")).strip()


def _agent_figma_image_key(item):
    if not isinstance(item, dict):
        return ""
    for key in ("asset_id", "assetId", "name", "image_name", "screenshot"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    image_b64 = str(item.get("base64") or item.get("contentBase64") or "").strip()
    return image_b64[:96]


def _dedupe_agent_figma_pages(items):
    result = []
    seen = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = _agent_figma_page_key(item)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        result.append(item)
    return result


def _dedupe_agent_figma_images(items):
    result = []
    seen = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        image_b64 = item.get("base64") or item.get("contentBase64")
        if not image_b64:
            continue
        key = _agent_figma_image_key(item)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        result.append({
            **item,
            "base64": image_b64,
            "name": item.get("name") or "figma-design.png",
            "mime": item.get("mime") or "image/png",
        })
    return result


def _normalize_agent_prepared_figma_context(raw, fallback_figma_url=""):
    if not isinstance(raw, dict):
        return {}
    text_assets = [str(item) for item in (raw.get("textAssets") or raw.get("text_assets") or []) if str(item or "").strip()]
    used_pages = _dedupe_agent_figma_pages(raw.get("usedPages") or raw.get("used_pages") or [])
    image_assets = _dedupe_agent_figma_images(raw.get("imageAssets") or raw.get("image_assets") or [])
    if used_pages and len(image_assets) > len(used_pages):
        page_image_names = {
            str(page.get(key) or "").strip()
            for page in used_pages
            for key in ("screenshot", "image_name", "name")
            if str(page.get(key) or "").strip()
        }
        matched_images = [item for item in image_assets if str(item.get("name") or "").strip() in page_image_names]
        image_assets = matched_images or image_assets[:len(used_pages)]
    ignored_pages = _dedupe_agent_figma_pages(raw.get("ignoredPages") or raw.get("ignored_pages") or [])
    saved_designs = _dedupe_agent_figma_pages(raw.get("savedDesigns") or raw.get("saved_designs") or [])
    if not (text_assets or image_assets or used_pages):
        return {}
    return {
        "source": raw.get("source") or "agent_prepare_source",
        "figmaUrl": raw.get("figmaUrl") or raw.get("figma_url") or fallback_figma_url or "",
        "textAssets": text_assets,
        "imageAssets": image_assets,
        "usedPages": used_pages,
        "ignoredPages": ignored_pages,
        "savedDesigns": saved_designs,
    }


def _persist_agent_prepared_figma_context(run, figma_url, text_assets, image_assets, used_pages, ignored_pages, saved_designs):
    payload = _normalize_agent_prepared_figma_context({
        "version": 1,
        "source": "agent_prepare_source",
        "figmaUrl": figma_url or "",
        "textAssets": text_assets or [],
        "imageAssets": image_assets or [],
        "usedPages": used_pages or [],
        "ignoredPages": ignored_pages or [],
        "savedDesigns": saved_designs or [],
    }, fallback_figma_url=figma_url)
    payload.update({
        "version": 1,
        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })
    path = _agent_prepared_figma_context_path(run)
    write_json_file(path, payload)
    return path, payload


def _agent_prepared_figma_context_from_source(source_context):
    if not isinstance(source_context, dict):
        return {}
    raw = source_context.get("preparedFigmaContext") or source_context.get("prepared_figma_context")
    if not isinstance(raw, dict):
        path = str(source_context.get("preparedFigmaContextPath") or source_context.get("prepared_figma_context_path") or "").strip()
        raw = read_json_file(path, default={}) if path else {}
    if not isinstance(raw, dict):
        return {}
    return _normalize_agent_prepared_figma_context(raw, fallback_figma_url=source_context.get("figmaUrl") or "")


def _agent_generate_progress_job_id(run):
    return f"agent-generate-{_agent_safe_run_file_id(run)}"


def _watch_agent_generate_yaml_progress(run, step, job_id, stop_event):
    """Mirror shared generation-job progress into the Agent timeline."""
    if not isinstance(step, dict):
        return
    try:
        from task_server.services.yaml_service import generate_job_path
    except Exception:
        return
    last_key = None
    while not stop_event.wait(2.0):
        job = read_json_file(generate_job_path(job_id), default={}) or {}
        message = str(job.get("message") or "").strip()
        stage = str(job.get("step") or "").strip() or "生成进度"
        progress = job.get("progress")
        status = str(job.get("status") or "running").upper()
        if not message and not stage:
            continue
        key = (stage, message, progress, status)
        if key == last_key:
            continue
        last_key = key
        progress_text = f"{progress}%" if progress not in (None, "") else ""
        suffix = f"（{progress_text}）" if progress_text else ""
        _append_step_trace(run, step, f"{stage}{suffix}：{message or status}", status="RUNNING", progress=progress)


def _load_figma_context_for_agent(run, context):
    """Use the shared Figma requirement-filter pipeline for Agent source context."""
    figma_url = str(context.get("figmaUrl") or "").strip()
    if not figma_url:
        return context
    context["figmaExtracted"] = False
    context["figmaUsedPages"] = []
    context["figmaIgnoredPages"] = []
    context["figmaImageCount"] = 0
    if not (os.getenv("FIGMA_TOKEN") or os.getenv("FIGMA_ACCESS_TOKEN")):
        context["figmaExtractError"] = "未配置 FIGMA_TOKEN，已保留 Figma 链接作为文本参考"
        context.setdefault("warnings", []).append("Figma 页面未提取：未配置 FIGMA_TOKEN")
        return context
    try:
        from task_server.services.knowledge_service import load_figma_generation_context
        refs = run.get("sourceRefs") if isinstance(run.get("sourceRefs"), dict) else {}
        normalized = _agent_normalized_input(run)
        case_set_id = _source_ref_value(refs, "caseSetId", "case_set_id")
        reference_limit = max(1, safe_int(normalized.get("figmaReferenceLimit") or refs.get("figmaReferenceLimit") or 36, 36))
        explicit_max_reference = normalized.get("figmaMaxReferenceLimit") or refs.get("figmaMaxReferenceLimit")
        max_reference_limit = max(reference_limit, safe_int(explicit_max_reference, 72)) if explicit_max_reference not in (None, "") else 72
        file_name_query = " ".join(
            str((item or {}).get("name") or "")
            for item in context.get("uploadedFiles") or []
            if isinstance(item, dict)
        )
        query_text = "\n".join([
            str(run.get("target") or ""),
            file_name_query,
        ]).strip()
        if not query_text:
            query_text = str(context.get("requirementText") or "")[:1200]
        request_data = {
            "figma_url": figma_url,
            "figma_mode": normalized.get("figmaMode") or refs.get("figmaMode") or "smart",
            "figma_limit": normalized.get("figmaLimit") or refs.get("figmaLimit") or 80,
            "figma_reference_limit": reference_limit,
            "figma_max_reference_limit": max_reference_limit,
            "direct_scope_only": True,
        }
        text_assets, image_assets, used_pages, ignored_pages, saved_designs = load_figma_generation_context(
            request_data,
            _agent_app_package(run),
            run.get("runId", ""),
            query_text,
            case_set_id,
            run.get("target", ""),
            run.get("module", ""),
        )
        if text_assets:
            context["figmaText"] = "\n\n".join(text_assets)[:12000]
        prepared_path = ""
        try:
            prepared_path, prepared = _persist_agent_prepared_figma_context(
                run,
                figma_url,
                text_assets,
                image_assets,
                used_pages,
                ignored_pages,
                saved_designs,
            )
        except Exception as persist_exc:
            prepared = _normalize_agent_prepared_figma_context({
                "version": 2,
                "source": "agent_prepare_source",
                "figmaUrl": figma_url,
                "textAssets": text_assets,
                "imageAssets": image_assets,
                "usedPages": used_pages,
                "ignoredPages": ignored_pages,
                "savedDesigns": saved_designs,
            }, fallback_figma_url=figma_url)
            context.setdefault("warnings", []).append(
                f"Figma 已解析但缓存保存失败，不影响本次使用：{str(persist_exc)[:80]}"
            )
        text_assets = prepared.get("textAssets") or []
        image_assets = prepared.get("imageAssets") or []
        used_pages = prepared.get("usedPages") or []
        ignored_pages = prepared.get("ignoredPages") or []
        context["preparedFigmaContextPath"] = prepared_path
        context["figmaParseVersion"] = "direct-scope-v2"
        context["figmaScopeQuery"] = query_text[:500]
        context["figmaTextAssetCount"] = len(text_assets or [])
        context["uiDesigns"] = used_pages
        context["figmaUsedPages"] = used_pages
        context["figmaIgnoredPages"] = ignored_pages
        context["figmaReferenceLimit"] = reference_limit
        context["figmaMaxReferenceLimit"] = max_reference_limit
        context["figmaImageAssets"] = [
            {
                "name": item.get("name") or f"figma-{idx + 1}.png",
                "mime": item.get("mime") or "",
                "hasContent": bool(item.get("base64")),
            }
            for idx, item in enumerate(image_assets or [])
            if isinstance(item, dict)
        ][:50]
        context["figmaImageCount"] = len(image_assets or [])
        context["figmaExtracted"] = True
        context["figmaExtractError"] = ""
    except Exception as exc:
        context["figmaExtractError"] = str(exc)[:240]
        context.setdefault("warnings", []).append(f"Figma 页面提取失败，已保留链接：{str(exc)[:120]}")
    return context


def _load_knowledge_pages_for_agent(run, query="", limit=20):
    """Load relevant page knowledge for Agent source preparation."""
    try:
        from task_server.services import knowledge_service
        app_package = _agent_app_package(run)
        pages = knowledge_service.list_knowledge_pages(app_package, tier="all")
        terms = [t for t in re.split(r"[\s,，/、;；:：|]+", str(query or "")) if len(t) >= 2]
        scored = []
        for page in pages:
            text = knowledge_service.knowledge_page_text(page)
            haystack = f"{page.get('page_name','')} {page.get('route','')} {page.get('description','')} {page.get('tags','')} {text}"
            score = sum(1 for term in terms if term and term in haystack)
            if score or not terms:
                scored.append((score, page, text))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [{
            "pageId": page.get("page_id") or page.get("pageId") or "",
            "pageName": page.get("page_name") or page.get("pageName") or "",
            "route": page.get("route") or "",
            "tier": page.get("tier") or "",
            "score": score,
            "text": text[:1200],
        } for score, page, text in scored[:limit]]
    except Exception:
        return []


def _build_source_text(source_context):
    if not isinstance(source_context, dict):
        return ""
    parts = [
        source_context.get("target", ""),
        source_context.get("requirementText", ""),
        source_context.get("figmaText", ""),
        source_context.get("failedJobText", ""),
        source_context.get("sourceSummary", ""),
    ]
    for item in source_context.get("uploadedFiles") or []:
        if isinstance(item, dict):
            parts.append(item.get("name", ""))
            parts.append(item.get("note", ""))
    for page in source_context.get("knowledgePages") or []:
        if isinstance(page, dict):
            parts.append(page.get("pageName", ""))
            parts.append(page.get("route", ""))
            parts.append(page.get("text", ""))
    for item in source_context.get("uiDesigns") or []:
        if isinstance(item, dict):
            parts.append(item.get("title", "") or item.get("page_name", ""))
            parts.append(" ".join(item.get("matched_keywords") or item.get("keywords") or []))
    return "\n".join(str(p) for p in parts if p)


CASE_MATCH_GENERIC_KEYWORDS = {
    "android", "yaml", "yml", "midscene", "sonic", "agent", "app", "http", "https", "com", "model",
    "页面", "截图", "图片", "文件", "链接", "需求", "文档", "资料", "上传", "生成", "自动化",
    "测试", "用例", "基线", "回归", "执行", "查看", "记录", "打印", "任务", "平台", "当前",
    "所有", "全部", "全量", "整套", "全套", "3d",
    "相关", "匹配", "确认", "选择", "按钮", "文本", "页面知识", "设计稿", "figma",
    "输入来源", "上传资料", "其中截图", "说明文件", "需求说明文件", "进入稳定起点",
    "执行核心业务动作", "校验业务结果", "稳定起点", "核心业务动作", "业务动作", "业务结果",
    "pdf", "时间", "创建", "背景", "需求介绍", "需求文档", "建模页需求文档", "ai建模需求",
    "生成并", "ai", "p0", "状态", "版本号", "负责人", "产品经理", "赵子寒",
}


def _agent_wants_all_existing_cases(text):
    """Whether the user explicitly asks to run/reuse all existing cases."""
    value = str(text or "").strip().lower()
    compact = re.sub(r"\s+", "", value)
    if not compact:
        return False
    all_word = r"(所有|全部|全量|整套|全套|all)"
    case_word = r"(用例|基线|任务|case|cases|yaml)"
    return bool(
        re.search(all_word + r".{0,12}" + case_word, compact, re.I)
        or re.search(case_word + r".{0,12}" + all_word, compact, re.I)
        or re.search(r"(跑|执行|回归|验证).{0,8}(全部|所有|全量)", compact, re.I)
    )

CASE_MATCH_META_KEYWORD_PARTS = {
    "figma", "链接", "页面", "忽略", "截图", "上传", "资料", "说明文件", "需求说明",
    "页面知识", "输入来源", "来源", "整理", "文件个", "截图张",
}


def _business_keyword_core(term):
    text = str(term or "").strip()
    if not text:
        return ""
    core = _normalize_case_match_text(text) if "_normalize_case_match_text" in globals() else text.lower()
    for prefix in ("查看", "验证", "检查"):
        if core.startswith(prefix) and len(core) > len(prefix) + 1:
            core = core[len(prefix):]
    for suffix in ("查看", "测试", "用例", "打印"):
        if core.endswith(suffix) and len(core) > len(suffix) + 1:
            core = core[:-len(suffix)]
    return core


def _is_business_keyword(term):
    text = str(term or "").strip()
    if not text:
        return False
    low = text.lower()
    core = _business_keyword_core(text)
    if re.fullmatch(r"\d{4,}", core) or re.fullmatch(r"p\d+", core, re.I):
        return False
    if low in CASE_MATCH_GENERIC_KEYWORDS or text in CASE_MATCH_GENERIC_KEYWORDS or core in CASE_MATCH_GENERIC_KEYWORDS:
        return False
    if any(part in low or part in text or part in core for part in CASE_MATCH_META_KEYWORD_PARTS):
        return False
    if len(core) < 2:
        return False
    if len(core) <= 2 and core in {"页面", "截图", "文件", "链接", "查看", "记录", "打印", "测试"}:
        return False
    return True


def _keyword_source_text(source_context):
    """Return only user/business text for keyword extraction, excluding platform summaries."""
    if not isinstance(source_context, dict):
        return ""
    parts = [
        source_context.get("target", ""),
        source_context.get("requirementText", ""),
        source_context.get("failedJobText", ""),
    ]
    for item in source_context.get("uploadedFiles") or []:
        if isinstance(item, dict):
            parts.append(item.get("note", ""))
    for page in source_context.get("knowledgePages") or []:
        if isinstance(page, dict):
            parts.append(page.get("pageName", ""))
            parts.append(page.get("text", ""))
    return "\n".join(str(p) for p in parts if str(p or "").strip())


def _business_keyword_candidates(text):
    text = _clean_agent_source_text(text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"(迭代负责人|产品经理|优先级|状态|版本号|变更人|主要变更内容|创建|需求基本信息|文档变更日志|前言)", " ", text)
    cleaned = re.sub(r"(回归一下|回归|基线测试|测试基线|基线|测试用例|用例|帮我|请|一下|看看|验证|检查|执行|跑一下|跑下)", " ", text)
    candidates = []
    for raw in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9_]{2,}", cleaned):
        for part in re.split(r"(?:和|及|与|、|/|，|,|；|;|\s+)", raw):
            part = str(part or "").strip()
            if part:
                candidates.append(part)
    return candidates


def _source_keywords(source_context, limit=12):
    text = _keyword_source_text(source_context)
    compact_text = re.sub(r"\s+", "", text)
    priority_terms = []
    for term in (
        "AI建模", "开始创作", "图片建模", "语音创作", "大家都在做", "我的作品",
        "模型生成通知", "导航栏", "首页入口", "模型库", "搜索引擎", "直接生成模型",
    ):
        if term.lower() in text.lower() or term in text or term in compact_text:
            priority_terms.append(term)
    raw_terms = priority_terms + _business_keyword_candidates(text)
    terms = []
    for term in raw_terms:
        if not _is_business_keyword(term):
            continue
        display_term = str(term).strip() if term in priority_terms else (_business_keyword_core(term) or str(term).strip())
        if display_term in terms:
            continue
        terms.append(display_term)
        if len(terms) >= limit:
            break
    return terms


def _dedupe_business_terms(items, limit=12):
    terms = []
    for item in items or []:
        term = _business_keyword_core(item)
        if not _is_business_keyword(term):
            continue
        if term in terms:
            continue
        terms.append(term)
        if len(terms) >= limit:
            break
    return terms


BUSINESS_FLOW_ACTION_TERMS = (
    "进入", "点击", "选择", "上传", "输入", "长按", "语音", "图片", "开始创作",
    "AI建模", "ai建模", "首页", "导航", "入口", "弹窗", "生成模型", "查看", "作品",
    "发送", "关闭", "结果",
)
BUSINESS_FLOW_META_TERMS = (
    "token", "api key", "apikey", "password", "成本", "消耗", "提高ai", "提升ai",
    "优化ai", "一模一样", "ip样子", "ip形象", "模型效果", "算法", "准确率", "性能",
)


def _normalize_business_flow_text(value):
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.translate(str.maketrans({"⻓": "长", "⻚": "页", "⾳": "音"}))
    return re.sub(r"\s+", " ", text).strip(" -:：>→")


def _clean_business_flow_node(value):
    text = _normalize_business_flow_text(value)
    if not text:
        return ""
    compact = re.sub(r"\s+", "", text).lower()
    if any(term in compact for term in BUSINESS_FLOW_META_TERMS):
        return ""
    if not any(term.lower() in compact or term in text for term in BUSINESS_FLOW_ACTION_TERMS):
        return ""
    return text[:40]


def _fallback_business_flow_from_text(value):
    compact = re.sub(r"\s+", "", _normalize_business_flow_text(value))
    if any(term in compact for term in ("AI建模", "ai建模", "开始创作", "图片建模", "语音创作", "语音输入")):
        flow = ["进入 AI建模页"]
        if "开始创作" in compact:
            flow.append("点击开始创作")
        if "图片建模" in compact or "上传" in compact:
            flow.append("选择图片建模并上传图片")
        if "语音创作" in compact or "语音输入" in compact or "长按" in compact:
            flow.append("选择语音创作并长按输入")
        flow.append("生成模型并查看结果")
        return flow
    return []


def _compact_business_flow_constraint(constraint):
    constraint = constraint if isinstance(constraint, dict) else {}
    flow = constraint.get("businessFlow") if isinstance(constraint.get("businessFlow"), list) else []
    return {
        "required": bool(constraint.get("required", True)),
        "strict": bool(constraint.get("strict", True)),
        "source": str(constraint.get("source") or "default"),
        "businessFlow": [str(item) for item in flow[:8] if str(item or "").strip()],
    }


def _ensure_business_flow_constraint(run):
    """Build and persist the business-flow backbone used by Agent decisions."""
    if not isinstance(run, dict):
        return {
            "required": True,
            "strict": True,
            "source": "default",
            "businessFlow": list(AGENT_DEFAULT_BUSINESS_FLOW),
            "businessFlowText": "\n".join(f"{idx + 1}. {item}" for idx, item in enumerate(AGENT_DEFAULT_BUSINESS_FLOW)),
        }
    artifacts = run.setdefault("artifacts", {})
    source_context = artifacts.get("sourceContext") if isinstance(artifacts.get("sourceContext"), dict) else {}
    normalized_input = run.get("normalizedInput") if isinstance(run.get("normalizedInput"), dict) else {}
    current = artifacts.get("businessFlowConstraint") if isinstance(artifacts.get("businessFlowConstraint"), dict) else {}
    prompt_ctx = {}
    business_ctx = run.get("businessContext") if isinstance(run.get("businessContext"), dict) else {}
    requirement_text = (
        source_context.get("requirementText")
        or normalized_input.get("requirementText")
        or normalized_input.get("text")
        or run.get("target", "")
    )
    try:
        prompt_ctx = get_prompt_center().enrich({
            **run,
            "sourceContext": source_context,
            "requirementText": requirement_text,
        })
        business_ctx = prompt_ctx.get("businessContext") if isinstance(prompt_ctx.get("businessContext"), dict) else business_ctx
    except Exception:
        business_ctx = business_ctx if isinstance(business_ctx, dict) else {}

    business_flow = business_ctx.get("business_flow") if isinstance(business_ctx.get("business_flow"), list) else []
    if not business_flow:
        business_flow = current.get("businessFlow") if isinstance(current.get("businessFlow"), list) else []
    expanded_flow = []
    for item in business_flow:
        for part in re.split(r"\s*(?:->|→|>|，|,|；|;|\n)\s*", str(item or "")):
            part = _clean_business_flow_node(part)
            if part and part not in expanded_flow:
                expanded_flow.append(part)
    business_flow = expanded_flow
    fallback_flow = _fallback_business_flow_from_text("\n".join([
        str(run.get("target") or ""),
        str(requirement_text or ""),
        str(source_context.get("sourceSummary") or ""),
    ]))
    flow_joined = " ".join(business_flow)
    if fallback_flow and (not business_flow or len(business_flow) < 3 or "AI建模" not in flow_joined):
        merged_flow = []
        for item in fallback_flow + business_flow:
            if item and item not in merged_flow:
                merged_flow.append(item)
        business_flow = merged_flow
    if not business_flow:
        business_flow = list(AGENT_DEFAULT_BUSINESS_FLOW)
    business_flow_text = business_ctx.get("business_flow_text") or "\n".join(
        f"{idx + 1}. {item}" for idx, item in enumerate(business_flow)
    )
    business_flow_text = "\n".join(f"{idx + 1}. {item}" for idx, item in enumerate(business_flow))
    constraint = {
        "required": True,
        "strict": True,
        "source": str(business_ctx.get("business_flow_source") or current.get("source") or "default"),
        "businessFlow": business_flow[:12],
        "businessFlowText": business_flow_text,
        "guardrails": [
            "工具选择必须服务于业务主链",
            "用例生成不得跳出业务主链节点",
            "异常和修复只能挂载到主链相关节点",
        ],
    }
    artifacts["businessFlowConstraint"] = constraint
    if prompt_ctx.get("promptCenter"):
        artifacts["promptCenter"] = prompt_ctx.get("promptCenter")
    if business_ctx:
        run["businessContext"] = business_ctx
    return constraint


def _business_flow_keywords(constraint, limit=10):
    constraint = constraint if isinstance(constraint, dict) else {}
    if str(constraint.get("source") or "") == "default":
        return []
    flow = constraint.get("businessFlow") if isinstance(constraint.get("businessFlow"), list) else []
    return _dedupe_business_terms(flow, limit=limit)


def _record_tool_eligibility(run, tool_def):
    """Record the business-flow filter decision for tool selection observability."""
    constraint = _ensure_business_flow_constraint(run)
    tool_def = tool_def if isinstance(tool_def, dict) else {}
    category = tool_def.get("category", "UNKNOWN")
    tool_name = tool_def.get("name", "")
    flow_keywords = _business_flow_keywords(constraint)
    allowed = bool(constraint.get("businessFlow"))
    reason = "业务主链已建立，允许工具围绕主链执行" if allowed else "缺少业务主链，暂停工具选择"
    if category in ("READ", "KNOWLEDGE"):
        reason = "读取/知识工具允许用于补齐业务主链上下文"
        allowed = True
    eligibility = {
        "toolName": tool_name,
        "category": category,
        "allowed": allowed,
        "reason": reason,
        "businessFlowSource": constraint.get("source", "default"),
        "businessFlowKeywords": flow_keywords,
    }
    run.setdefault("artifacts", {}).setdefault("toolEligibility", {})[tool_name] = eligibility
    return eligibility


CASE_MATCH_NOISE_WORDS = [
    "回归一下", "回归", "测试基线", "基线测试", "基线", "测试用例", "用例",
    "自动化", "测试", "执行一下", "跑一下", "跑下", "执行", "帮我", "请",
    "一下", "看看", "验证", "检查", "单条", "套件", "任务",
]


def _normalize_case_match_text(text):
    value = str(text or "").lower()
    value = re.sub(r"\.(yaml|yml)$", "", value)
    value = re.sub(r"[^0-9a-z\u4e00-\u9fa5]+", "", value)
    for noise in CASE_MATCH_NOISE_WORDS:
        value = value.replace(noise.lower(), "")
    return value


def _char_ngrams(text, size=2):
    text = _normalize_case_match_text(text)
    if len(text) < size:
        return {text} if text else set()
    return {text[i:i + size] for i in range(0, len(text) - size + 1)}


def _case_match_score(query_text, yaml_item, keywords=None):
    query = _normalize_case_match_text(query_text)
    if not query:
        return 0
    hay = _normalize_case_match_text(" ".join([
        yaml_item.get("dir_name", ""),
        yaml_item.get("file_name", ""),
        yaml_item.get("task_name", ""),
        str(yaml_item.get("yaml_text", ""))[:3000],
    ]))
    if not hay:
        return 0
    if query in hay or hay in query:
        return 100

    score = 0
    query_terms = [_business_keyword_core(k) for k in (keywords or []) if _is_business_keyword(k)]
    query_terms = [k for k in query_terms if len(k) >= 2]
    for term in query_terms:
        if term and term in hay:
            score += min(30, 8 + len(term) * 2)

    q2 = _char_ngrams(query, 2)
    h2 = _char_ngrams(hay, 2)
    if q2 and h2:
        overlap = len(q2 & h2)
        score += int(70 * overlap / max(1, len(q2)))
        if overlap >= 3:
            score += 15

    q3 = _char_ngrams(query, 3)
    h3 = _char_ngrams(hay, 3)
    if q3 and h3:
        score += int(40 * len(q3 & h3) / max(1, len(q3)))
    return score


def _fuzzy_match_cases(query_text, all_yamls, keywords=None, limit=5):
    scored = []
    for item in all_yamls:
        score = _case_match_score(query_text, item, keywords)
        if score >= 55:
            scored.append((score, item))
    scored.sort(key=lambda pair: (-pair[0], pair[1].get("rel_path", "")))
    if not scored:
        return [], []
    best_score = scored[0][0]
    selected = [item for score, item in scored if score >= max(55, best_score - 18)]
    return selected[:limit], scored[:10]


def _candidate_keyword_reasons(query_text, yaml_item, keywords=None):
    """Return concrete business keywords/reasons for a candidate YAML match."""
    haystack = " ".join([
        yaml_item.get("dir_name", ""),
        yaml_item.get("file_name", ""),
        yaml_item.get("task_name", ""),
        yaml_item.get("yaml_text", "")[:3000],
    ])
    norm_hay = _normalize_case_match_text(haystack)
    reasons = []
    matched = []

    def add_term(term, reason):
        term = str(term or "").strip()
        if len(term) < 2 or term in CASE_MATCH_NOISE_WORDS or not _is_business_keyword(term):
            return
        if term not in matched:
            matched.append(term)
            reasons.append(reason)

    for kw in keywords or []:
        if not _is_business_keyword(kw):
            continue
        display_kw = _business_keyword_core(kw) or str(kw).strip()
        if display_kw and display_kw in haystack:
            add_term(display_kw, f"命中关键词「{display_kw}」")
            continue
        core = _business_keyword_core(kw)
        if core and core in norm_hay:
            add_term(core, f"命中核心词「{core}」")

    query_core = _normalize_case_match_text(query_text)
    if query_core and query_core not in matched:
        # 中文短语经常出现“查看打印记录” vs “打印记录查看”这种词序差异，
        # 用最长公共子串补充实际命中的业务词，便于用户理解匹配来源。
        for size in range(min(8, len(query_core)), 1, -1):
            for idx in range(0, len(query_core) - size + 1):
                part = query_core[idx:idx + size]
                if part in norm_hay and not any(part in old or old in part for old in matched):
                    add_term(part, f"词序容错命中「{part}」")
            if len(matched) >= 4:
                break

    return {"matchedKeywords": matched[:8], "reasons": reasons[:8]}


def _collect_candidate_yamls(run):
    target = run.get("target", "")
    app_name = run.get("appName", "智小白3D APP")
    module = run.get("module", "")
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    search_dirs = _get_search_dirs_for_app(app_name, base_dir)
    all_yamls = []
    seen = set()
    for search_dir in search_dirs:
        if not os.path.isdir(search_dir):
            continue
        for root, dirs, files in os.walk(search_dir):
            dir_name = os.path.basename(root)
            if module and module not in dir_name:
                continue
            for f in files:
                if not f.endswith((".yaml", ".yml")):
                    continue
                dedup_key = (dir_name, f)
                if dedup_key in seen:
                    continue
                seen.add(dedup_key)
                abs_path = os.path.join(root, f)
                yaml_text = read_text_file(abs_path, "")[:5000]
                all_yamls.append({
                    "abs_path": abs_path,
                    "rel_path": os.path.relpath(abs_path, base_dir),
                    "dir_name": dir_name,
                    "file_name": f,
                    "task_name": f.replace(".yaml", "").replace(".yml", ""),
                    "yaml_text": yaml_text,
                })
    return all_yamls


def _tool_impact_analysis(run):
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "impact_analysis",
        "category": "READ",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {"target": run.get("target", ""), "sourceType": run.get("sourceType", "manual")},
    }
    try:
        artifacts = run.setdefault("artifacts", {})
        source_context = artifacts.get("sourceContext") or {}
        source_text = _build_source_text(source_context)
        goal_analysis = _ensure_agent_goal_analysis(run)
        ai_keywords = goal_analysis.get("keywords") if isinstance(goal_analysis.get("keywords"), list) else []
        fallback_keywords = [
            term for term in re.findall(r"[\u4e00-\u9fa5A-Za-z0-9_]{2,}", run.get("target", ""))
            if _is_business_keyword(term)
        ][:8]
        keywords = _dedupe_business_terms(list(ai_keywords or []) + (_source_keywords(source_context) or []) + fallback_keywords, limit=16)
        analysis = {
            "sourceType": source_context.get("sourceType") or run.get("sourceType") or "manual",
            "keywords": keywords,
            "aiKeywords": ai_keywords,
            "matchAll": bool(goal_analysis.get("matchAll") or _agent_wants_all_existing_cases(run.get("target", ""))),
            "hasFigma": bool(source_context.get("figmaUrl") or source_context.get("figmaText")),
            "hasRequirement": bool(source_context.get("requirementText") or source_text),
            "uploadedFileCount": len(source_context.get("uploadedFiles") or []),
            "uploadedImageCount": len(source_context.get("uploadedImages") or []),
            "sourceSummary": source_context.get("sourceSummary") or "",
            "suggestion": "先复用现有 YAML；低置信度再生成草稿。",
        }
        artifacts["impactAnalysis"] = analysis
        call["status"] = "SUCCESS"
        kw_text = "、".join(keywords[:8]) if keywords else "无"
        call["keywords"] = keywords
        input_text = analysis.get("sourceSummary") or "未上传额外资料"
        intent_text = "；识别到全量执行意图" if analysis["matchAll"] else ""
        call["outputSummary"] = f"影响分析完成，关键词：{kw_text}{intent_text}；{input_text}；优先复用现有用例"
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)
        attach_diagnosis(call, make_diagnosis("影响分析失败", "无法判断应复用还是新建用例。", ["检查输入资料", "重新启动 Agent"]))
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def _tool_case_retrieval(run):
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "case_retrieval",
        "category": "READ",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {"target": run.get("target", ""), "sourceType": run.get("sourceType", "manual")},
    }
    try:
        artifacts = run.setdefault("artifacts", {})
        ai_health = _probe_agent_ai_health(run)
        business_constraint = _ensure_business_flow_constraint(run)
        flow_keywords = _business_flow_keywords(business_constraint)
        call["businessFlowConstraint"] = _compact_business_flow_constraint(business_constraint)
        call["agentAiHealth"] = ai_health
        all_yamls = _collect_candidate_yamls(run)
        source_context = artifacts.get("sourceContext") or {}
        matched_yaml = source_context.get("matchedYaml") if isinstance(source_context.get("matchedYaml"), dict) else {}
        if matched_yaml.get("module") and matched_yaml.get("file"):
            exact_path = safe_join(TASK_DIR, matched_yaml.get("module"), matched_yaml.get("file"))
            if os.path.exists(exact_path):
                artifacts["caseRetrieval"] = {
                    "decision": "reuse",
                    "confidence": 1.0,
                    "strategy": "failedJobId_exact",
                    "keywords": (artifacts.get("impactAnalysis") or {}).get("keywords") or [],
                    "matchedKeywords": (artifacts.get("impactAnalysis") or {}).get("keywords") or [],
                    "candidates": [{
                        "rel_path": os.path.relpath(exact_path, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
                        "dir_name": matched_yaml.get("module"),
                        "file_name": matched_yaml.get("file"),
                        "confidence": 1.0,
                        "reasons": ["失败任务精确关联到该 YAML"],
                    }],
                }
                artifacts["matchedCases"] = [exact_path]
                artifacts["matchedCount"] = 1
                artifacts["matchReason"] = "failedJobId 精确匹配已有 YAML，自动复用"
                normalize_yaml_refs(run)
                call["status"] = "SUCCESS"
                call["outputSummary"] = artifacts["matchReason"]
                call["matchedKeywords"] = artifacts["caseRetrieval"]["matchedKeywords"]
                call["artifactRefs"] = [artifacts["caseRetrieval"]["candidates"][0]["rel_path"]]
                call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                call["durationMs"] = _compute_duration(call)
                _log_tool_call(call, run.get("runId", ""))
                return call
        if _agent_is_new_requirement_run(run, source_context):
            artifacts["matchedCases"] = []
            artifacts["matchedCount"] = 0
            artifacts["matchReason"] = "检测到需求/Figma 新需求输入，跳过旧基线复用匹配，直接生成新 YAML 草稿"
            artifacts["caseRetrieval"] = {
                "decision": "generate_draft",
                "confidence": 1.0,
                "strategy": "new_requirement_source",
                "keywords": _source_keywords(source_context, limit=16),
                "matchedKeywords": [],
                "candidates": [],
                "reason": artifacts["matchReason"],
            }
            artifacts["allowDraftGeneration"] = True
            call["status"] = "SUCCESS"
            call["outputSummary"] = artifacts["matchReason"]
            call["keywords"] = artifacts["caseRetrieval"]["keywords"]
            call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            call["durationMs"] = _compute_duration(call)
            _log_tool_call(call, run.get("runId", ""))
            return call
        goal_analysis = _ensure_agent_goal_analysis(run)
        impact_analysis = artifacts.get("impactAnalysis") if isinstance(artifacts.get("impactAnalysis"), dict) else {}
        wants_all_cases = bool(
            goal_analysis.get("matchAll")
            or impact_analysis.get("matchAll")
            or _agent_wants_all_existing_cases(run.get("target", ""))
        )
        if wants_all_cases and all_yamls:
            matched = [item["abs_path"] for item in all_yamls if item.get("abs_path")]
            flow_keywords = _business_flow_keywords(business_constraint)
            all_case_keywords = _dedupe_business_terms(list(flow_keywords or []) + ["全量用例"], limit=12) or ["全量用例"]
            artifacts["matchedCases"] = matched
            artifacts["matchedCount"] = len(matched)
            artifacts["matchReason"] = "识别到全量已有用例执行意图，复用当前应用下全部 YAML"
            artifacts["caseRetrieval"] = {
                "decision": "reuse",
                "confidence": 0.98,
                "ruleConfidence": 0.98,
                "confidenceSource": goal_analysis.get("aiSource") or "explicit_all_cases_intent",
                "aiUsed": bool(goal_analysis.get("aiSource")),
                "aiSource": goal_analysis.get("aiSource") or "",
                "aiReason": goal_analysis.get("summary") or "用户明确要求执行所有用例",
                "aiErrors": [],
                "aiHealth": ai_health,
                "businessFlowConstraint": _compact_business_flow_constraint(business_constraint),
                "businessFlowKeywords": flow_keywords,
                "qualityGate": _evaluate_agent_quality_gate(run, "case_retrieval", {
                    "decision": "reuse",
                    "confidence": 0.98,
                    "matched": matched,
                    "matchedKeywords": all_case_keywords,
                    "aiUsed": bool(goal_analysis.get("aiSource")),
                }),
                "scope": "regression",
                "keywords": all_case_keywords,
                "matchedKeywords": all_case_keywords,
                "candidates": [{k: item.get(k) for k in ("rel_path", "dir_name", "file_name")} for item in all_yamls[:50]],
                "candidateDetails": [{
                    "rel_path": item.get("rel_path"),
                    "confidence": 0.98,
                    "reasons": ["全量已有用例执行意图"],
                    "matchedKeywords": all_case_keywords,
                } for item in all_yamls[:50]],
            }
            artifacts["aiDispatchPolicy"] = {
                "caseRetrieval": {
                    "decision": "reuse",
                    "confidence": 0.98,
                    "source": artifacts["caseRetrieval"]["confidenceSource"],
                    "reason": artifacts["caseRetrieval"]["aiReason"],
                    "matchedCount": len(matched),
                },
                "safetyGates": ["VALIDATE_YAML", "RISK_REVIEW", "EXECUTION_PRECHECK"],
                "note": "全量执行只复用已有 YAML，不生成新 YAML 草稿。",
            }
            normalize_yaml_refs(run)
            call["status"] = "SUCCESS"
            call["keywords"] = all_case_keywords
            call["matchedKeywords"] = all_case_keywords
            call["artifactRefs"] = [item.get("rel_path") for item in all_yamls[:5]]
            call["outputSummary"] = f"识别到全量执行意图，复用已有 YAML {len(matched)} 个；不生成 YAML 草稿"
            _record_agent_ai_decision(
                run,
                "case_retrieval",
                artifacts["caseRetrieval"]["confidenceSource"],
                True,
                call["outputSummary"],
                confidence=0.98,
                decision="reuse",
                matchedCount=len(matched),
                matchedKeywords=all_case_keywords,
            )
            call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            call["durationMs"] = _compute_duration(call)
            _log_tool_call(call, run.get("runId", ""))
            return call
        query_text = "\n".join([
            run.get("target", ""),
            business_constraint.get("businessFlowText", ""),
            _build_source_text(source_context),
        ])
        base_keywords = (artifacts.get("impactAnalysis") or {}).get("keywords") or _source_keywords(source_context)
        if not isinstance(base_keywords, list):
            base_keywords = [base_keywords]
        keywords = _dedupe_business_terms(base_keywords + flow_keywords, limit=16)
        artifacts["businessFlowKeywords"] = flow_keywords
        scope = str(run.get("scope") or "").strip().lower()
        if not scope or scope == "auto":
            target_text = str(run.get("target") or "")
            if "冒烟" in target_text or "smoke" in target_text.lower():
                scope = "smoke"
            elif "回归" in target_text or "regression" in target_text.lower() or "基线" in target_text:
                scope = "regression"
            else:
                scope = "debug"
            run["scope"] = scope
        yaml_list_text = "\n".join(f"- {item['dir_name']}/{item['file_name']}" for item in all_yamls[:200])
        ai_direct = _ai_select_cases(
            run.get("target", ""),
            scope,
            run.get("appName", "智小白3D APP"),
            yaml_list_text,
            all_yamls,
            model=run.get("model", ""),
            provider_id=run.get("modelProviderId") or run.get("aiProviderId") or "",
        ) if all_yamls else {}
        if isinstance(ai_direct, dict) and ai_direct.get("matched_paths"):
            matched = [p for p in ai_direct.get("matched_paths") or [] if p]
            matched_set = set(matched)
            selected_items = [item for item in all_yamls if item.get("abs_path") in matched_set]
            matched_keywords = _dedupe_business_terms(
                list((goal_analysis.get("keywords") if isinstance(goal_analysis.get("keywords"), list) else []) or [])
                + list((impact_analysis.get("keywords") if isinstance(impact_analysis.get("keywords"), list) else []) or []),
                limit=12,
            )
            confidence = 0.88 if matched else 0.0
            if len(matched) > 8 and scope not in ("smoke", "冒烟"):
                decision = "wait_confirm"
                run["status"] = "WAIT_CONFIRM"
                run["currentStep"] = "WAIT_CONFIRM"
            else:
                decision = "reuse"
            quality_gate = _evaluate_agent_quality_gate(run, "case_retrieval", {
                "decision": decision,
                "confidence": confidence,
                "matched": matched,
                "matchedKeywords": matched_keywords,
                "aiUsed": True,
            })
            if decision == "reuse" and not quality_gate.get("passed"):
                decision = "wait_confirm"
                run["status"] = "WAIT_CONFIRM"
                run["currentStep"] = "WAIT_CONFIRM"
            if decision == "wait_confirm":
                run.setdefault("pendingConfirmations", []).append({
                    "id": f"confirm-{int(time.time())}",
                    "type": "case_retrieval_confirm",
                    "title": "确认 AI 直选用例范围",
                    "action": "confirm_case_reuse",
                    "message": f"AI 已选择 {len(matched)} 个已有 YAML，需确认后执行。理由：{ai_direct.get('reason') or 'AI 语义直选'}",
                    "candidate": {
                        "count": len(matched),
                        "confidence": confidence,
                        "confidenceSource": ai_direct.get("ai_source") or "ai_direct_case_selection",
                        "scope": scope,
                        "matchedKeywords": matched_keywords,
                    },
                    "candidates": [{k: item.get(k) for k in ("rel_path", "dir_name", "file_name")} for item in selected_items[:20]],
                    "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "decision": None,
                })
            artifacts["matchedCases"] = matched
            artifacts["matchedCount"] = len(matched)
            artifacts["matchReason"] = f"AI 直选已有 YAML，匹配 {len(matched)} 个"
            artifacts["caseRetrieval"] = {
                "decision": decision,
                "confidence": confidence,
                "ruleConfidence": 0,
                "confidenceSource": ai_direct.get("ai_source") or "ai_direct_case_selection",
                "aiUsed": True,
                "aiSource": ai_direct.get("ai_source") or "",
                "aiReason": ai_direct.get("reason") or "AI 语义直选",
                "aiErrors": ai_direct.get("_ai_errors") or [],
                "aiHealth": ai_health,
                "businessFlowConstraint": _compact_business_flow_constraint(business_constraint),
                "businessFlowKeywords": flow_keywords,
                "qualityGate": quality_gate,
                "scope": ai_direct.get("scope") or scope,
                "keywords": matched_keywords,
                "matchedKeywords": matched_keywords,
                "candidates": [{k: item.get(k) for k in ("rel_path", "dir_name", "file_name")} for item in selected_items[:50]],
                "candidateDetails": [{
                    "rel_path": item.get("rel_path"),
                    "confidence": confidence,
                    "reasons": [ai_direct.get("reason") or "AI 语义直选"],
                    "matchedKeywords": matched_keywords,
                } for item in selected_items[:50]],
            }
            artifacts["aiDispatchPolicy"] = {
                "caseRetrieval": {
                    "decision": decision,
                    "confidence": confidence,
                    "source": artifacts["caseRetrieval"]["confidenceSource"],
                    "reason": artifacts["caseRetrieval"]["aiReason"],
                    "matchedCount": len(matched),
                },
                "safetyGates": ["VALIDATE_YAML", "RISK_REVIEW", "EXECUTION_PRECHECK"],
                "note": "AI 先直选已有 YAML，规则召回只作为兜底。",
            }
            normalize_yaml_refs(run)
            call["status"] = "SUCCESS"
            call["keywords"] = matched_keywords
            call["matchedKeywords"] = matched_keywords
            call["artifactRefs"] = [item.get("rel_path") for item in selected_items[:5]]
            call["outputSummary"] = f"AI 直选已有 YAML {len(matched)} 个；来源：{artifacts['caseRetrieval']['confidenceSource']}；理由：{artifacts['caseRetrieval']['aiReason']}"
            _record_agent_ai_decision(
                run,
                "case_retrieval",
                artifacts["caseRetrieval"]["confidenceSource"],
                True,
                call["outputSummary"],
                confidence=confidence,
                decision=decision,
                matchedCount=len(matched),
                matchedKeywords=matched_keywords,
            )
            call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            call["durationMs"] = _compute_duration(call)
            _log_tool_call(call, run.get("runId", ""))
            return call
        if isinstance(ai_direct, dict) and ai_direct.get("_ai_errors"):
            _record_agent_ai_decision(
                run,
                "case_retrieval",
                "ai_direct_case_selection",
                False,
                "；".join(ai_direct.get("_ai_errors")[:3]),
                fallback="rule_recall",
            )
        scored = []
        for item in all_yamls:
            score = _case_match_score(query_text, item, keywords)
            confidence = min(1.0, round(score / 100.0, 2))
            if confidence >= 0.25:
                reason_info = _candidate_keyword_reasons(query_text, item, keywords)
                reasons = reason_info.get("reasons") or []
                if item.get("yaml_text"):
                    reasons.append("已读取 YAML 内容参与匹配")
                scored.append({
                    **item,
                    "confidence": confidence,
                    "score": score,
                    "reasons": reasons[:6],
                    "matched_keywords": reason_info.get("matchedKeywords") or [],
                })
        scored.sort(key=lambda item: (-item["confidence"], item.get("rel_path", "")))
        top = scored[0] if scored else None
        rule_confidence = float(top.get("confidence", 0)) if top else 0.0
        rule_matched_keywords = []
        for item in scored[:10]:
            for kw in item.get("matched_keywords") or []:
                if kw not in rule_matched_keywords:
                    rule_matched_keywords.append(kw)

        decision = "generate_draft"
        matched = []
        confidence = rule_confidence
        matched_keywords = list(rule_matched_keywords)
        ai_review = {}
        ai_used = False
        ai_source = ""
        ai_reason = ""
        ai_errors = []
        confidence_source = "rule_fallback"

        if scored:
            ai_review = _ai_rerank_case_candidates(
                target=run.get("target", ""),
                source_text=_build_source_text(source_context),
                scope=scope,
                app_name=run.get("appName", "智小白3D APP"),
                candidates=scored[:12],
                model=run.get("model", ""),
                provider_id=run.get("modelProviderId") or run.get("aiProviderId") or "",
                business_constraint=business_constraint,
            ) or {}
            ai_errors = ai_review.get("_ai_errors") or []
            if ai_review.get("decision") and not ai_errors:
                ai_used = True
                confidence_source = ai_review.get("ai_source") or "ai_semantic"
                ai_source = confidence_source
                decision = ai_review.get("decision")
                confidence = float(ai_review.get("confidence") or 0)
                matched = [p for p in (ai_review.get("matched") or []) if p]
                scope = ai_review.get("scope") or scope
                ai_reason = ai_review.get("reason") or ""
                matched_keywords = ai_review.get("matchedKeywords") or matched_keywords
                ai_candidates = ai_review.get("candidates") or []
                if ai_candidates:
                    merged = []
                    seen_paths = set()
                    for item in ai_candidates + scored:
                        path_key = item.get("abs_path") or item.get("rel_path")
                        if not path_key or path_key in seen_paths:
                            continue
                        seen_paths.add(path_key)
                        merged.append(item)
                    scored = merged
                    top = scored[0] if scored else top
                if not matched and decision in ("reuse", "wait_confirm") and top:
                    matched = [top["abs_path"]]
                if not matched and decision == "reuse":
                    decision = "generate_draft"
                    ai_reason = (ai_reason + "；但 AI 未返回可用候选路径，已降级为生成草稿").strip("；")
                _record_agent_ai_decision(
                    run,
                    "case_retrieval",
                    ai_source,
                    True,
                    ai_reason or f"AI 语义复核：{decision}",
                    confidence=confidence,
                    decision=decision,
                    matchedKeywords=matched_keywords,
                    businessFlow=business_constraint.get("businessFlow"),
                )
            else:
                ai_reason = "AI 语义复核不可用，已使用规则召回兜底"
                _record_agent_ai_decision(
                    run,
                    "case_retrieval",
                    "ai_semantic",
                    False,
                    "；".join(ai_errors[:3]) or ai_reason,
                    businessFlow=business_constraint.get("businessFlow"),
                )

        pre_gate_decision = {
            "decision": decision,
            "confidence": confidence,
            "matched": matched,
            "matchedKeywords": matched_keywords,
            "aiUsed": ai_used,
        }
        quality_gate = _evaluate_agent_quality_gate(run, "case_retrieval", pre_gate_decision)
        if decision == "reuse" and not quality_gate.get("passed"):
            decision = "wait_confirm"
            ai_reason = (ai_reason + f"；质量门禁要求确认：{quality_gate.get('reason')}").strip("；")
            _record_agent_ai_decision(
                run,
                "case_retrieval_quality_gate",
                "deterministic_guardrail",
                False,
                quality_gate.get("reason", ""),
                previousDecision="reuse",
                nextDecision="wait_confirm",
                confidence=confidence,
            )

        if not ai_used:
            if confidence >= 0.75:
                matched = [item["abs_path"] for item in scored if item["confidence"] >= max(0.75, confidence - 0.18)]
                decision = "reuse"
            elif confidence >= 0.45 and top:
                matched = [top["abs_path"]]
                decision = "wait_confirm"
            else:
                matched = []
                decision = "generate_draft"

            post_rule_gate = _evaluate_agent_quality_gate(run, "case_retrieval", {
                "decision": decision,
                "confidence": confidence,
                "matched": matched,
                "matchedKeywords": matched_keywords,
                "aiUsed": ai_used,
            })
            quality_gate = post_rule_gate
            if decision == "reuse" and not post_rule_gate.get("passed"):
                decision = "wait_confirm"
                ai_reason = (ai_reason + f"；规则复用被质量门禁转为确认：{post_rule_gate.get('reason')}").strip("；")

        if decision == "reuse":
            if scope in ("smoke", "冒烟"):
                matched = matched[:3]
            elif scope in ("regression", "回归") and len(matched) > 8:
                matched = matched[:10]
                decision = "wait_confirm"
                ai_reason = ai_reason or "匹配范围较大，为避免误跑整套，进入人工确认"
            else:
                matched = matched[:5]

        if decision == "wait_confirm":
            if not matched and top:
                matched = [top["abs_path"]]
            run["status"] = "WAIT_CONFIRM"
            run["currentStep"] = "WAIT_CONFIRM"
            selected_top = next((item for item in scored if item.get("abs_path") in set(matched)), top)
            confirm_title = "确认复用已有用例"
            if scope in ("regression", "回归") and len(matched) > 1:
                confirm_title = "确认回归执行范围"
            confirm_message = (
                f"AI 判断可复用已有用例，但需要你确认执行范围；confidence={confidence:.2f}。"
                if ai_used else
                f"找到疑似用例 {selected_top.get('dir_name')}/{selected_top.get('file_name')}，置信度 {confidence:.2f}，请确认是否复用。"
            )
            if ai_reason:
                confirm_message += f" 理由：{ai_reason}"
            run.setdefault("pendingConfirmations", []).append({
                "id": f"confirm-{int(time.time())}",
                "type": "case_retrieval_confirm",
                "title": confirm_title,
                "action": "confirm_case_reuse",
                "message": confirm_message,
                "candidate": {
                    "count": len(matched),
                    "confidence": confidence,
                    "confidenceSource": confidence_source,
                    "scope": scope,
                    "matchedKeywords": matched_keywords,
                    **({k: selected_top.get(k) for k in ("rel_path", "dir_name", "file_name", "reasons")} if selected_top else {}),
                },
                "candidates": [{k: item.get(k) for k in (
                    "rel_path", "dir_name", "file_name", "confidence", "ai_confidence",
                    "reasons", "matched_keywords", "ai_reason", "ai_matched_keywords"
                )} for item in scored[:10]],
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "decision": None,
            })
        elif decision == "generate_draft":
            matched = []

        artifacts["caseRetrieval"] = {
            "decision": decision,
            "confidence": confidence,
            "ruleConfidence": rule_confidence,
            "confidenceSource": confidence_source,
            "aiUsed": ai_used,
            "aiSource": ai_source,
            "aiReason": ai_reason,
            "aiErrors": ai_errors,
            "aiHealth": ai_health,
            "businessFlowConstraint": _compact_business_flow_constraint(business_constraint),
            "businessFlowKeywords": flow_keywords,
            "qualityGate": quality_gate,
            "scope": scope,
            "keywords": keywords or [],
            "matchedKeywords": matched_keywords,
            "candidates": [{k: item.get(k) for k in (
                "rel_path", "dir_name", "file_name", "confidence", "ai_confidence",
                "score", "reasons", "matched_keywords", "ai_reason", "ai_matched_keywords"
            )} for item in scored[:10]],
            "candidateDetails": [{
                "rel_path": item.get("rel_path"),
                "confidence": item.get("confidence"),
                "ruleConfidence": item.get("score"),
                "aiConfidence": item.get("ai_confidence"),
                "reasons": item.get("reasons") or [],
                "aiReason": item.get("ai_reason") or "",
                "matchedKeywords": item.get("matched_keywords") or [],
                "aiMatchedKeywords": item.get("ai_matched_keywords") or [],
            } for item in scored[:10]],
        }
        artifacts["matchedCases"] = matched
        artifacts["matchedCount"] = len(matched)
        artifacts["aiDispatchPolicy"] = {
            "caseRetrieval": {
                "decision": decision,
                "confidence": confidence,
                "source": confidence_source,
                "reason": ai_reason or "规则兜底",
                "matchedCount": len(matched),
            },
            "safetyGates": ["VALIDATE_YAML", "RISK_REVIEW", "EXECUTION_PRECHECK"],
            "note": "AI 负责复用/确认/生成决策，平台执行安全门禁。",
        }
        if ai_used:
            artifacts["matchReason"] = (
                f"AI 语义复核自动复用，confidence={confidence:.2f}" if decision == "reuse"
                else f"AI 语义复核待确认，confidence={confidence:.2f}" if decision == "wait_confirm"
                else f"AI 语义复核建议生成 YAML 草稿，confidence={confidence:.2f}"
            )
        else:
            artifacts["matchReason"] = (
                f"Case Retrieval 规则兜底自动复用，confidence={confidence:.2f}" if decision == "reuse"
                else f"Case Retrieval 规则兜底待确认，confidence={confidence:.2f}" if decision == "wait_confirm"
                else f"Case Retrieval 规则兜底未达到复用阈值，confidence={confidence:.2f}，允许生成 YAML 草稿"
            )
        normalize_yaml_refs(run)
        call["status"] = "SUCCESS"
        kw_text = "、".join(matched_keywords[:8]) if matched_keywords else "未命中明确关键词"
        call["matchedKeywords"] = matched_keywords
        source_text = f"；来源：{confidence_source}" if confidence_source else ""
        reason_text = f"；理由：{ai_reason}" if ai_reason else ""
        if ai_errors and not ai_used:
            reason_text += f"；AI 复核失败：{'；'.join(ai_errors[:2])}"
        call["outputSummary"] = f"{artifacts['matchReason']}{source_text}{reason_text}；匹配关键词：{kw_text}"
        call["artifactRefs"] = [item.get("rel_path") for item in scored[:5]]
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)
        attach_diagnosis(call, make_diagnosis("Case Retrieval 执行失败", "无法判断是否复用现有用例。", ["检查用例库目录", "检查输入目标", "必要时人工选择 YAML"]))
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def _tool_prepare_source(run):
    """Normalize Agent input sources before case matching."""
    refs = run.get("sourceRefs") if isinstance(run.get("sourceRefs"), dict) else {}
    source_type = str(run.get("sourceType") or refs.get("sourceType") or "manual").lower()
    material = _agent_source_material_context(run)
    use_saved_knowledge = _agent_use_saved_knowledge_context(run, refs)
    inferred_source_type = _infer_agent_source_type(source_type, material, refs)
    if inferred_source_type != source_type:
        source_type = inferred_source_type
        run["sourceType"] = source_type
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "prepare_source",
        "category": "READ",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {"sourceType": source_type, "refs": refs, "target": run.get("target", "")},
    }
    context = {
        "sourceType": source_type,
        "sourceRefs": refs,
        "target": run.get("target", ""),
        "requirementText": "",
        "figmaUrl": "",
        "figmaText": "",
        "figmaExtracted": False,
        "figmaExtractError": "",
        "figmaUsedPages": [],
        "figmaIgnoredPages": [],
        "figmaImageCount": 0,
        "failedJobText": "",
        "knowledgePages": [],
        "useSavedKnowledge": use_saved_knowledge,
        "uiDesigns": [],
        "uploadedFiles": [],
        "uploadedImages": [],
        "sourceSummary": "",
        "matchedYaml": None,
        "requiresConfirm": False,
        "warnings": [],
    }
    try:
        if source_type == "failed_job":
            job_id = run.get("failedJobId") or _source_ref_value(refs, "failedJobId", "jobId")
            job = _find_job_for_agent(job_id)
            if job:
                context["failedJob"] = {
                    "jobId": job.get("job_id", ""),
                    "module": job.get("module", ""),
                    "file": job.get("file", ""),
                    "taskName": job.get("task_name") or job.get("target_task_name") or "",
                    "status": job.get("status", ""),
                    "error": job.get("error") or job.get("stderr_tail") or "",
                }
                context["failedJobText"] = "\n".join(str(v) for v in context["failedJob"].values() if v)
                context["matchedYaml"] = {"module": job.get("module", ""), "file": job.get("file", "")}
            else:
                context["warnings"].append(f"未找到失败任务 {job_id}")

        elif source_type in ("requirement", "figma"):
            has_generation_ref = bool(_source_ref_value(refs, "generateJobId", "jobId") or _source_ref_value(refs, "caseSetId", "case_set_id"))
            job = _find_generate_job(
                _source_ref_value(refs, "generateJobId", "jobId"),
                _source_ref_value(refs, "caseSetId", "case_set_id"),
            )
            if job:
                result = job.get("result") or {}
                summary = result.get("summary") or job.get("summary") or {}
                request = job.get("request_data") or job.get("requestData") or {}
                context["generateJob"] = {
                    "jobId": job.get("job_id", ""),
                    "caseSetId": job.get("case_set_id") or result.get("case_set_id") or summary.get("case_set_id") or "",
                    "title": summary.get("title") or request.get("title") or job.get("title") or "",
                    "module": summary.get("module") or request.get("module") or job.get("module") or "",
                    "yamlFile": summary.get("yaml_file") or job.get("file") or "",
                    "status": job.get("status") or job.get("ok") or "",
                }
                context["requirementText"] = "\n".join([
                    str(context["generateJob"].get("title") or ""),
                    str(request.get("requirement") or request.get("text") or request.get("description") or ""),
                    str(summary.get("business_goal") or summary.get("businessGoal") or ""),
                    json.dumps(summary.get("requirements") or summary.get("scenarios") or [], ensure_ascii=False)[:3000],
                ]).strip()
                context["uiDesigns"] = summary.get("ui_designs") or summary.get("uiDesigns") or []
                context["figmaText"] = json.dumps(summary.get("knowledge_pages") or summary.get("used_reference_pages") or [], ensure_ascii=False)[:3000]
            elif has_generation_ref:
                context["warnings"].append("未找到关联生成记录")
            context["figmaUrl"] = _source_ref_value(refs, "figmaUrl", "figma_url")
            if use_saved_knowledge:
                context["knowledgePages"] = _load_knowledge_pages_for_agent(run, f"{run.get('target','')} {context.get('requirementText','')}")
        else:
            context["requirementText"] = run.get("target", "")
            if use_saved_knowledge:
                context["knowledgePages"] = _load_knowledge_pages_for_agent(run, run.get("target", ""), limit=8)

        material_req = material.get("requirementText") or ""
        if material_req:
            context["requirementText"] = "\n\n".join(
                part for part in (context.get("requirementText", ""), material_req) if str(part or "").strip()
            ).strip()
        if material.get("figmaUrl") and not context.get("figmaUrl"):
            context["figmaUrl"] = material.get("figmaUrl")
        context["uploadedFiles"] = material.get("uploadedFiles") or []
        context["uploadedImages"] = material.get("uploadedImages") or []
        context = _load_figma_context_for_agent(run, context)
        context["sourceSummary"] = (
            f"Figma 链接 {1 if context.get('figmaUrl') else 0} 个；"
            f"Figma 页面 {len(context.get('figmaUsedPages') or [])} 个，"
            f"忽略 {len(context.get('figmaIgnoredPages') or [])} 个，"
            f"Figma UI 图 {context.get('figmaImageCount') or 0} 张；"
            f"历史页面知识 {'已启用' if use_saved_knowledge else '未使用'}；"
            f"上传资料 {material.get('fileCount') or 0} 个，其中上传截图 {material.get('imageCount') or 0} 张、"
            f"需求/说明文件 {material.get('requirementFileCount') or 0} 个。"
        )
        if context.get("figmaUrl") and not context.get("figmaText"):
            context["figmaText"] = f"Figma 设计稿链接：{context['figmaUrl']}"
        if source_type == "figma" and not (
            context.get("figmaUrl")
            or context.get("figmaText")
            or context.get("uiDesigns")
            or context.get("knowledgePages")
            or context.get("uploadedImages")
        ):
            context["warnings"].append("未找到可用 Figma/UI 资料")
        if use_saved_knowledge and material.get("fileCount") and not context.get("knowledgePages"):
            context["knowledgePages"] = _load_knowledge_pages_for_agent(
                run,
                f"{run.get('target','')} {context.get('requirementText','')}",
                limit=8,
            )
        context["keywords"] = _source_keywords(context)
        run.setdefault("artifacts", {})["sourceContext"] = context
        call["status"] = "SUCCESS" if not context["warnings"] else "PARTIAL_FAILED"
        warning_text = f"，提醒：{'；'.join(context['warnings'][:2])}" if context["warnings"] else ""
        call["sourceSummary"] = context["sourceSummary"]
        call["outputSummary"] = (
            f"已整理 {source_type} 输入来源，关键词 {len(context['keywords'])} 个，"
            f"页面知识 {len(context['knowledgePages'])} 条，{context['sourceSummary']}{warning_text}"
        )
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def _tool_match_cases(run):
    """从 server-tasks 目录匹配用例 - AI 直选优先。

    支持策略：
    1. failedJobId 精确匹配
    2. AI 直选：将用例列表给 AI，AI 直接返回应执行的文件
    3. AI 降级：tool_analyze_goal 提取关键词 + 子串匹配
    去重 key=(dir_name, f)
    """
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "list_cases",
        "category": "READ",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {"target": run.get("target", ""), "module": run.get("module", ""),
                  "appName": run.get("appName", "智小白3D APP")},
    }
    try:
        retrieval = (run.get("artifacts") or {}).get("caseRetrieval") or {}
        if retrieval:
            matched = (run.get("artifacts") or {}).get("matchedCases") or []
            decision = retrieval.get("decision") or ""
            keywords = retrieval.get("matchedKeywords") or retrieval.get("keywords") or []
            kw_text = "、".join(str(kw) for kw in keywords[:8]) if keywords else "未命中明确关键词"
            call["status"] = "SUCCESS"
            call["outputSummary"] = f"复用 Case Retrieval 结果：{decision}，匹配 {len(matched)} 个；匹配关键词：{kw_text}"
            call["keywords"] = keywords
            call["matchedKeywords"] = keywords
            call["candidateDetails"] = retrieval.get("candidateDetails") or retrieval.get("candidates") or []
            call["artifactRefs"] = [os.path.relpath(p, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))) for p in matched[:5]]
            call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            call["durationMs"] = _compute_duration(call)
            _log_tool_call(call, run.get("runId", ""))
            return call
        target = run.get("target", "")
        app_name = run.get("appName", "智小白3D APP")
        module = run.get("module", "")
        scope = run.get("scope", "")
        artifacts = run.setdefault("artifacts", {})
        source_context = artifacts.get("sourceContext") or {}
        source_type = str(source_context.get("sourceType") or run.get("sourceType") or "manual").lower()
        source_text = _build_source_text(source_context) or target
        source_keywords = source_context.get("keywords") or _source_keywords(source_context)
        business_constraint = _ensure_business_flow_constraint(run)
        flow_keywords = _business_flow_keywords(business_constraint)
        source_keywords = _dedupe_business_terms(list(source_keywords or []) + flow_keywords, limit=16)
        artifacts["businessFlowKeywords"] = flow_keywords
        call["businessFlowConstraint"] = _compact_business_flow_constraint(business_constraint)
        call["businessFlowKeywords"] = flow_keywords
        # scope="auto" 或为空时，从用户目标文本中推断 scope
        if not scope or scope == "auto":
            target_lower = target.lower()
            if "回归" in target or "regression" in target_lower or "基线" in target:
                scope = "regression"
            elif "冒烟" in target or "smoke" in target_lower:
                scope = "smoke"
            else:
                scope = "regression"  # 默认回归（跑全部匹配用例）
            run["scope"] = scope
        failed_job_id = run.get("failedJobId") or run.get("failed_job_id") or ""
        if source_type == "failed_job" and not failed_job_id:
            failed_job_id = _source_ref_value(run.get("sourceRefs") or {}, "failedJobId", "jobId")
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

        # === 策略1: 如果有 failedJobId，只匹配对应YAML ===
        if failed_job_id:
            from task_server.services import job_service
            with JOB_LOCK:
                jobs = job_service.load_jobs()
                old_job = next((j for j in jobs if j.get("job_id") == failed_job_id), None)
            if old_job:
                job_module = old_job.get("module", "")
                job_file = old_job.get("file", "")
                tasks_dir = TASK_DIR if os.path.isdir(TASK_DIR) else os.path.join(base_dir, "server-tasks")
                full_path = safe_join(tasks_dir, job_module, job_file)
                if os.path.exists(full_path):
                    matched = [full_path]
                    match_reason = f"精确匹配失败任务 {failed_job_id} 对应的 YAML"
                else:
                    matched = []
                    match_reason = f"失败任务 {failed_job_id} 的 YAML 文件不存在"
            else:
                matched = []
                match_reason = f"未找到 job {failed_job_id}"
            # 直接返回
            run.setdefault("artifacts", {})["matchedCases"] = matched
            run["artifacts"]["matchReason"] = match_reason
            run["artifacts"]["matchedCount"] = len(matched)
            call["status"] = "SUCCESS" if matched else "FAILED"
            call["outputSummary"] = f"匹配到 {len(matched)} 个用例（{match_reason}）"
            call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            call["durationMs"] = _compute_duration(call)
            _log_tool_call(call, run.get("runId", ""))
            return call

        source_yaml = source_context.get("matchedYaml") if isinstance(source_context, dict) else None
        if isinstance(source_yaml, dict) and source_yaml.get("module") and source_yaml.get("file"):
            tasks_dir = TASK_DIR if os.path.isdir(TASK_DIR) else os.path.join(base_dir, "server-tasks")
            full_path = safe_join(tasks_dir, source_yaml.get("module", ""), source_yaml.get("file", ""))
            matched = [full_path] if os.path.exists(full_path) else []
            match_reason = "根据输入来源精确匹配 YAML" if matched else "输入来源指向的 YAML 不存在"
            artifacts["matchedCases"] = matched
            artifacts["matchReason"] = match_reason
            artifacts["matchedCount"] = len(matched)
            call["status"] = "SUCCESS" if matched else "FAILED"
            call["outputSummary"] = f"匹配到 {len(matched)} 个用例（{match_reason}）"
            call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            call["durationMs"] = _compute_duration(call)
            _log_tool_call(call, run.get("runId", ""))
            return call

        # 确定搜索目录
        search_dirs = _get_search_dirs_for_app(app_name, base_dir)
        if not search_dirs:
            call["status"] = "FAILED"
            call["error"] = f"未找到用例目录"
            call["outputSummary"] = "未找到用例目录"
            call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            call["durationMs"] = _compute_duration(call)
            _log_tool_call(call, run.get("runId", ""))
            return call

        # === 收集所有候选YAML ===
        all_yamls = []
        seen = set()  # key: (module_dir_name, filename)
        for search_dir in search_dirs:
            if not os.path.isdir(search_dir):
                continue
            for root, dirs, files in os.walk(search_dir):
                dir_name = os.path.basename(root)
                # 策略2: 如果指定了module，只匹配该module目录
                if module and module not in dir_name:
                    continue
                for f in files:
                    if f.endswith(".yaml") or f.endswith(".yml"):
                        dedup_key = (dir_name, f)
                        if dedup_key in seen:
                            continue
                        seen.add(dedup_key)
                        abs_path = os.path.join(root, f)
                        rel_path = os.path.relpath(abs_path, base_dir)
                        all_yamls.append({
                            "abs_path": abs_path,
                            "rel_path": rel_path,
                            "dir_name": dir_name,
                            "file_name": f,
                            "task_name": f.replace(".yaml", "").replace(".yml", ""),
                        })

        # === AI 直选用例匹配（全流程 AI 驱动）===
        yaml_list_text = "\n".join(f"- {y['dir_name']}/{y['file_name']}" for y in all_yamls)
        match_reason = ""
        matched = []
        skipped = []

        run_model = run.get("model", "")
        run_provider_id = run.get("modelProviderId") or run.get("aiProviderId") or ""
        match_target = (
            f"{target}\n\n"
            f"业务主链（必须优先匹配）：\n{business_constraint.get('businessFlowText', '')}\n\n"
            f"输入来源上下文：\n{source_text[:6000]}"
        )
        ai_match_result = _ai_select_cases(match_target, scope, app_name, yaml_list_text, all_yamls, model=run_model, provider_id=run_provider_id)
        if ai_match_result and ai_match_result.get("matched_paths"):
            matched = ai_match_result.get("matched_paths", [])
            skipped = [y["rel_path"] for y in all_yamls if y["abs_path"] not in matched]
            match_reason = ai_match_result.get("reason", "AI 智能匹配")
            ai_source = ai_match_result.get("ai_source", "")
            if ai_source:
                match_reason += f"（{ai_source}）"
            # AI 返回的 scope 覆盖默认值
            ai_scope = ai_match_result.get("scope", "")
            if ai_scope and ai_scope != scope:
                scope = ai_scope
                run["scope"] = scope
        else:
            # 记录 AI 不可用的详细原因
            ai_errors = ai_match_result.get("_ai_errors", []) if isinstance(ai_match_result, dict) else []
            run.setdefault("artifacts", {})["aiMatchErrors"] = ai_errors
            # AI 完全不可用时的兜底：尝试 tool_analyze_goal
            goal = (run.get("artifacts") or {}).get("goalAnalysis") or {}
            if not goal:
                try:
                    goal = tool_analyze_goal(run, {"target": target})
                except Exception:
                    goal = {}
            ai_module = goal.get("module", "")
            ai_keywords = goal.get("keywords", [])
            ai_scope = goal.get("scope", "")
            if ai_scope and ai_scope != scope:
                scope = ai_scope
                run["scope"] = scope
            if ai_module and not module:
                module = ai_module
                all_yamls = [y for y in all_yamls if y["dir_name"] == module or module in y["dir_name"]]
            if not ai_keywords and source_keywords:
                ai_keywords = source_keywords
            if ai_keywords:
                for y in all_yamls:
                    if any(kw in y["file_name"] or kw in y["task_name"] or kw in y["dir_name"] for kw in ai_keywords):
                        matched.append(y["abs_path"])
                    else:
                        skipped.append(y["rel_path"])
                match_reason = f"AI关键词匹配「{'、'.join(ai_keywords)}」（降级模式）"
                if not matched:
                    fuzzy_items, fuzzy_scored = _fuzzy_match_cases(target, all_yamls, ai_keywords, limit=5)
                    if fuzzy_items:
                        matched = [item["abs_path"] for item in fuzzy_items]
                        matched_set = set(matched)
                        skipped = [y["rel_path"] for y in all_yamls if y["abs_path"] not in matched_set]
                        top_score = fuzzy_scored[0][0] if fuzzy_scored else 0
                        match_reason = f"AI关键词未直接命中，已按词序容错模糊匹配（最高分 {top_score}，降级模式）"
            elif module:
                matched = [y["abs_path"] for y in all_yamls]
                match_reason = f"匹配模块「{module}」全部用例（降级模式）"
            else:
                fuzzy_items, fuzzy_scored = _fuzzy_match_cases(target, all_yamls, source_keywords, limit=5)
                if fuzzy_items:
                    matched = [item["abs_path"] for item in fuzzy_items]
                    matched_set = set(matched)
                    skipped = [y["rel_path"] for y in all_yamls if y["abs_path"] not in matched_set]
                    top_score = fuzzy_scored[0][0] if fuzzy_scored else 0
                    match_reason = f"AI不可用或未返回有效用例，已按目标文本模糊匹配（最高分 {top_score}，降级模式）"
                else:
                    ai_err_detail = "; ".join(ai_errors[:3]) if ai_errors else "未知"
                    matched = []
                    skipped = [y["rel_path"] for y in all_yamls[:20]]
                    match_reason = f"AI不可用或输入不明确（{ai_err_detail}），未扩大匹配范围，等待人工确认"
                    artifacts["requiresConfirm"] = True
                    artifacts["allowDraftGeneration"] = source_type in ("figma", "requirement")

        # === scope限制 ===
        if scope in ("冒烟", "smoke"):
            limit = min(3, len(matched))  # 冒烟最多3条
            if len(matched) > limit:
                skipped += [os.path.relpath(p, base_dir) for p in matched[limit:]]
                matched = matched[:limit]
                match_reason += f"（冒烟模式限制 {limit} 条）"
        elif scope in ("回归", "regression"):
            # 回归模式：如果超过20条，标记为预览计划
            if len(matched) > 20:
                match_reason += f"（回归模式，共 {len(matched)} 条，建议分批执行）"

        # 保存结果
        if not matched and artifacts.get("requiresConfirm") and not artifacts.get("allowDraftGeneration"):
            run["status"] = "WAIT_CONFIRM"
            run["currentStep"] = "WAIT_CONFIRM"
            run.setdefault("pendingConfirmations", []).append({
                "id": f"confirm-{int(time.time())}",
                "type": "case_match_uncertain",
                "message": "Agent 没有找到足够明确的用例匹配结果，请确认输入来源或改用生成草稿。",
                "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "decision": None,
            })
        run.setdefault("artifacts", {})["matchedCases"] = matched[:50]
        run["artifacts"]["matchReason"] = match_reason
        run["artifacts"]["matchedCount"] = len(matched)
        run["artifacts"]["skippedCases"] = skipped[:20]

        call["status"] = "SUCCESS"
        call["outputSummary"] = f"匹配到 {len(matched)} 个用例（{match_reason}）"
        call["artifactRefs"] = [os.path.relpath(p, base_dir) for p in matched[:5]]
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def _agent_requirement_bullets(text, limit=12):
    text = _clean_agent_source_text(text)
    if not text:
        return []
    candidates = []
    patterns = [
        r"(导航栏[^。\n；]{0,80}AI建模[^。\n；]{0,80})",
        r"(首页[^。\n；]{0,120}AI建模[^。\n；]{0,80})",
        r"(开始创作[^。\n；]{0,100})",
        r"(图片建模[^。\n；]{0,100})",
        r"(语音创作[^。\n；]{0,140})",
        r"(大家都在做[^。\n；]{0,160})",
        r"(我的作品[^。\n；]{0,120})",
        r"(模型生成通知[^。\n；]{0,120})",
        r"(匹配模型库[^。\n；]{0,120})",
        r"(模型切换[^。\n；]{0,120})",
    ]
    for pattern in patterns:
        for item in re.findall(pattern, text, flags=re.I):
            item = _clean_agent_source_text(item).strip(" ：:。；")
            if 6 <= len(item) <= 160 and item not in candidates:
                candidates.append(item)
            if len(candidates) >= limit:
                return candidates[:limit]
    for item in re.split(r"[。；\n]", text):
        item = _clean_agent_source_text(item).strip(" ：:。；")
        if (
            8 <= len(item) <= 120
            and not re.search(r"(负责人|产品经理|版本号|变更|优先级|状态|链接|设计稿|需求文档)", item)
            and item not in candidates
        ):
            candidates.append(item)
        if len(candidates) >= limit:
            break
    return candidates[:limit]


def _yaml_double_quote(value):
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", str(value or ""))
    text = re.sub(r"\s+", " ", text).strip()
    return text.replace("\\", "\\\\").replace('"', '\\"')


def _agent_safe_assert_text(value):
    text = _yaml_double_quote(value)
    for keyword in HIGH_RISK_KEYWORDS:
        if keyword:
            text = text.replace(keyword, "相关高风险词")
    return text


def _agent_yaml_step(action, value, indent="      ", timeout=None):
    line = f'{indent}- {action}: "{_agent_safe_assert_text(value)}"'
    if timeout:
        line += f"\n{indent}  timeout: {int(timeout)}"
    return line


def _agent_yaml_task(name, steps):
    lines = [
        f'  - name: "{_yaml_double_quote(name[:60])}"',
        "    # agent.generated: fallback_requirement_draft",
        "    # agent.note: AI Gateway 生成失败或输出不可执行时，按需求/Figma 资料生成的多任务可确认草稿。",
        "    flow:",
        '      - runAdbShell: "am force-stop com.kfb.model"',
        "      - sleep: 1500",
        "      - launch: com.kfb.model",
        _agent_yaml_step("aiWaitFor", "App 首页已加载完成，底部导航或首页核心内容可见", timeout=60000),
    ]
    lines.extend(steps)
    lines.extend([
        _agent_yaml_step("aiAssert", "当前任务未出现加载失败、网络错误、空白页或异常弹窗"),
        '      - runAdbShell: "am force-stop com.kfb.model"',
        "      - sleep: 800",
    ])
    return "\n".join(lines)


def _agent_requirement_task_specs(source_text, source_context):
    text = _clean_agent_source_text(source_text)
    specs = []

    def add(key, name, steps):
        if key not in [item["key"] for item in specs]:
            specs.append({"key": key, "name": name, "steps": steps})

    add("home_entry", "AI建模首页入口与导航入口验收", [
        _agent_yaml_step("aiAssert", "首页底部导航中间入口已展示为 AI建模 或 3D/AI建模入口"),
        _agent_yaml_step("aiAssert", "首页存在 AI建模能力入口，且入口排序位于主要学习入口之后的核心区域"),
        _agent_yaml_step("aiTap", "首页 AI建模入口或底部中间 AI建模入口"),
        _agent_yaml_step("aiWaitFor", "进入 AI建模页，页面标题或核心模块可见", timeout=60000),
    ])
    if re.search(r"开始创作|描述你想要做", text):
        add("start_create", "AI建模开始创作入口验收", [
            _agent_yaml_step("aiTap", "首页 AI建模入口或底部中间 AI建模入口"),
            _agent_yaml_step("aiWaitFor", "AI建模页已打开，开始创作模块可见", timeout=60000),
            _agent_yaml_step("aiAssert", "开始创作模块展示描述输入提示、图片建模入口和语音创作入口"),
            _agent_yaml_step("aiTap", "AI建模页的开始创作按钮"),
            _agent_yaml_step("aiWaitFor", "进入 AI建模内页，能看到描述输入区域或生成模型相关操作区", timeout=60000),
        ])
    if re.search(r"图片建模|图⽚建模|上传", text):
        add("image_modeling", "AI建模图片建模入口验收", [
            _agent_yaml_step("aiTap", "首页 AI建模入口或底部中间 AI建模入口"),
            _agent_yaml_step("aiWaitFor", "AI建模页已打开，图片建模入口可见", timeout=60000),
            _agent_yaml_step("aiTap", "图片建模入口"),
            _agent_yaml_step("aiWaitFor", "进入图片建模上传页或图片建模弹窗，上传图片相关操作可见", timeout=60000),
            _agent_yaml_step("aiAssert", "图片建模页面包含上传图片、直接生成模型或返回关闭等核心控件"),
        ])
    if re.search(r"语音创作|语⾳创作|正在听你说|听清楚啦|发送", text):
        add("voice_create", "AI建模语音创作弹窗验收", [
            _agent_yaml_step("aiTap", "首页 AI建模入口或底部中间 AI建模入口"),
            _agent_yaml_step("aiWaitFor", "AI建模页已打开，语音创作入口可见", timeout=60000),
            _agent_yaml_step("aiTap", "语音创作入口"),
            _agent_yaml_step("aiWaitFor", "语音创作弹窗打开，能看到语音输入提示或发送按钮", timeout=60000),
            _agent_yaml_step("aiAssert", "语音创作弹窗包含正在听取、完成听取、发送或关闭等可见状态"),
        ])
    if re.search(r"大家都在做|模型标题|儿童友好|emoji", text, re.I):
        add("popular_models", "AI建模大家都在做内容验收", [
            _agent_yaml_step("aiTap", "首页 AI建模入口或底部中间 AI建模入口"),
            _agent_yaml_step("aiWaitFor", "AI建模页已打开，大家都在做模块可见", timeout=60000),
            _agent_yaml_step("aiAssert", "大家都在做模块展示模型标题或推荐内容，标题简短且适合儿童理解"),
            _agent_yaml_step("aiAssert", "推荐内容区域没有空白占位、加载失败或明显不适合儿童的内容"),
        ])
    if re.search(r"我的作品|倒序|所有.*ai建模", text, re.I):
        add("my_works", "AI建模我的作品模块验收", [
            _agent_yaml_step("aiTap", "首页 AI建模入口或底部中间 AI建模入口"),
            _agent_yaml_step("aiWaitFor", "AI建模页已打开，我的作品模块或查看全部入口可见", timeout=60000),
            _agent_yaml_step("aiAssert", "我的作品模块按时间维度展示 AI建模作品列表或无作品空态"),
            _agent_yaml_step("aiTap", "我的作品模块的查看全部入口"),
            _agent_yaml_step("aiWaitFor", "进入我的作品列表页，列表或空态说明可见", timeout=60000),
        ])
    if re.search(r"模型生成通知|应用内弹窗|消息栏", text):
        add("model_notice", "AI建模生成通知入口验收", [
            _agent_yaml_step("aiTap", "首页 AI建模入口或底部中间 AI建模入口"),
            _agent_yaml_step("aiWaitFor", "AI建模页或 AI建模内页已打开", timeout=60000),
            _agent_yaml_step("aiAssert", "模型生成相关状态能通过应用内提示、通知入口或任务状态区域反馈给用户"),
        ])
    figma_hint = (source_context or {}).get("figmaUrl") or ""
    if figma_hint:
        add("figma_visual", "AI建模Figma关键视觉还原验收", [
            _agent_yaml_step("aiTap", "首页 AI建模入口或底部中间 AI建模入口"),
            _agent_yaml_step("aiWaitFor", "AI建模页已打开，核心模块可见", timeout=60000),
            _agent_yaml_step("aiAssert", f"页面核心结构与 Figma 关键区域一致，参考 {figma_hint}"),
        ])
    if len(specs) < 5:
        for item in _agent_requirement_bullets(text, limit=8):
            add(f"req_{len(specs)}", f"AI建模需求点验收-{len(specs) + 1}", [
                _agent_yaml_step("aiTap", "首页 AI建模入口或底部中间 AI建模入口"),
                _agent_yaml_step("aiWaitFor", "AI建模页已打开，核心业务区域可见", timeout=60000),
                _agent_yaml_step("aiAssert", f"需求点验收：{item}"),
            ])
            if len(specs) >= 6:
                break
    return specs[:10]


def _finish_agent_tool_call(call, run):
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def _agent_fallback_yaml_draft(run, source_context, source_text):
    target = str(run.get("target") or "AI 建模需求验收").strip()
    specs = _agent_requirement_task_specs(source_text or target, source_context or {})
    if not specs:
        specs = _agent_requirement_task_specs("AI建模页 开始创作 图片建模 语音创作 大家都在做 我的作品", source_context or {})
    tasks_yaml = "\n".join(_agent_yaml_task(spec["name"], spec["steps"]) for spec in specs)
    return f"""android:
  tasks:
{tasks_yaml}
"""


def _agent_has_rich_requirement_material(source_context):
    if not isinstance(source_context, dict):
        return False
    requirement_text = str(source_context.get("requirementText") or "")
    return bool(
        source_context.get("figmaUrl")
        or source_context.get("figmaUsedPages")
        or source_context.get("preparedFigmaContextPath")
        or source_context.get("uploadedFiles")
        or len(requirement_text) >= 500
    )


def _agent_yaml_task_names_for_runner(path):
    text = read_text_file(path, "")
    names = []
    if text and pyyaml is not None:
        try:
            parsed = pyyaml.safe_load(text)
            _platform, tasks = extract_midscene_tasks(parsed)
            names = [
                str(task.get("name") or "").strip()
                for task in (tasks or [])
                if isinstance(task, dict) and str(task.get("name") or "").strip()
            ]
        except Exception:
            names = []
    if not names:
        for line in text.splitlines():
            match = re.match(r"^\s*-\s+name:\s*(.+?)\s*$", line)
            if match:
                names.append(match.group(1).strip().strip("\"'"))
    return names


def _agent_source_files_for_generation(run):
    files = []
    for item in _agent_source_files(run):
        if not isinstance(item, dict):
            continue
        raw = dict(item)
        if raw.get("kind") == "screenshot":
            continue
        if not (raw.get("content") or raw.get("text") or raw.get("contentBase64")):
            continue
        files.append(raw)
    return files


def _agent_generate_yaml_from_ui_pipeline(run, source_context, source_text):
    """Reuse the mature requirement/Figma -> cases/mindmap/YAML pipeline for Agent drafts."""
    from task_server.services.yaml_service import (
        generate_ui_yaml_from_request,
        update_generate_job,
    )

    case_set_id = f"agent-{run.get('runId') or unique_millis_id('agent')}"
    title = str(run.get("target") or source_context.get("target") or "AI Agent 新需求").strip()
    module = clean_agent_module_name(run)
    files = _agent_source_files_for_generation(run)
    if source_text and not files:
        files = [{
            "name": "agent-requirement.md",
            "type": "text/markdown",
            "kind": "requirement_text",
            "content": source_text,
            "source": "agent-source-context",
        }]
    prepared_figma_context = _agent_prepared_figma_context_from_source(source_context)
    request_data = {
        "case_set_id": case_set_id,
        "title": title,
        "module": module,
        "files": files,
        "figma_url": source_context.get("figmaUrl") or "",
        "figmaUrl": source_context.get("figmaUrl") or "",
        "prepared_figma_context": prepared_figma_context,
        "app_package": _agent_app_package(run),
        "use_knowledge_context": False,
        "source": "agent",
    }
    progress_job_id = _agent_generate_progress_job_id(run)
    step = next((item for item in (run.get("steps") or []) if item.get("step") == "GENERATE_YAML"), None)
    if prepared_figma_context and step:
        _append_step_trace(
            run,
            step,
            "复用已解析 Figma："
            f"{len(prepared_figma_context.get('usedPages') or [])} 个页面，"
            f"{len(prepared_figma_context.get('imageAssets') or [])} 张 UI 图",
            status="RUNNING",
        )
    stop_event = threading.Event()
    watcher = None
    update_generate_job(
        progress_job_id,
        status="running",
        type="agent_generate_yaml",
        progress=5,
        step="准备生成",
        message="Agent 已进入需求解析、脑图和 YAML 生成链路",
        run_id=run.get("runId", ""),
        case_set_id=case_set_id,
    )
    if step:
        watcher = threading.Thread(
            target=_watch_agent_generate_yaml_progress,
            args=(run, step, progress_job_id, stop_event),
            daemon=True,
        )
        watcher.start()
    try:
        result = generate_ui_yaml_from_request(request_data, job_id=progress_job_id)
        update_generate_job(
            progress_job_id,
            status="success",
            progress=100,
            step="生成完成",
            message="已完成 Agent YAML 生成",
        )
    except Exception as exc:
        error_trace = traceback.format_exc()
        try:
            from task_server.services.yaml_service import generation_failure_detail, load_generate_job
            current_job = load_generate_job(progress_job_id) or {}
            error_detail = generation_failure_detail(exc, current_job)
        except Exception:
            error_detail = {
                "type": "generation_error",
                "message": str(exc)[:300],
                "error": str(exc)[:1000],
            }
        update_generate_job(
            progress_job_id,
            status="failed",
            ok=False,
            step="生成失败",
            message=str(exc)[:200],
            error=str(exc)[:1000],
            error_detail=error_detail,
            error_trace=error_trace[-5000:],
        )
        raise
    finally:
        stop_event.set()
        if watcher:
            watcher.join(timeout=0.5)
    cases_payload = result.get("cases") if isinstance(result, dict) else {}
    if not isinstance(cases_payload, dict):
        cases_payload = {}
    yaml_files = result.get("yamlFiles") or result.get("files") or []
    yaml_file_items = []
    generated_group_by_file = {}
    generated_groups = result.get("generatedCaseGroups") if isinstance(result.get("generatedCaseGroups"), dict) else {}
    for group_key in ("executable_cases", "needs_review_cases", "draft_cases", "manual_cases"):
        for row in generated_groups.get(group_key) or []:
            if isinstance(row, dict) and row.get("file"):
                generated_group_by_file[str(row.get("file"))] = row
    for file_name in yaml_files:
        if isinstance(file_name, dict):
            name = str(file_name.get("file") or "").strip()
        else:
            name = str(file_name or "").strip()
        if not name:
            continue
        row_meta = generated_group_by_file.get(name) or {}
        item = {
            "module": module,
            "file": name,
            "path": safe_join(TASK_DIR, module, name),
        }
        if row_meta:
            item.update({
                "executionLevel": row_meta.get("executionLevel") or row_meta.get("level") or "",
                "level": row_meta.get("level") or row_meta.get("executionLevel") or "",
                "score": row_meta.get("score") or 0,
                "smoke": bool(row_meta.get("smoke")),
                "smokeCandidate": bool(row_meta.get("smoke")),
                "runnerCandidate": bool(row_meta.get("runnerCandidate")),
                "scopeReview": row_meta.get("scopeReview") if isinstance(row_meta.get("scopeReview"), dict) else {},
                "reasons": row_meta.get("reasons") if isinstance(row_meta.get("reasons"), list) else [],
                "case_id": row_meta.get("case_id") or "",
            })
        yaml_file_items.append(item)
    yaml_validation_results = [
        validate_agent_yaml_content(read_text_file(item["path"], ""))
        for item in yaml_file_items
        if item.get("path")
    ]
    yaml_executability = {
        "ok": bool(yaml_file_items) and all(item.get("ok") for item in yaml_validation_results),
        "mode": "split_by_case",
        "fileCount": len(yaml_file_items),
        "taskCount": sum(int(item.get("taskCount") or 0) for item in yaml_validation_results),
    }
    artifacts = run.setdefault("artifacts", {})
    artifacts["generatedCases"] = cases_payload
    if isinstance(result.get("generatedCaseGroups"), dict):
        artifacts["generatedCaseGroups"] = result.get("generatedCaseGroups")
    artifacts["generationPipeline"] = {
        "source": "ui_yaml_pipeline",
        "caseSetId": result.get("case_set_id"),
        "caseCount": result.get("caseCount"),
        "manualCaseCount": result.get("manualCaseCount"),
        "scenarioCount": result.get("scenarioCount"),
        "yamlFiles": [item.get("file") for item in yaml_file_items],
        "yamlFileCount": len(yaml_file_items),
        "summaryFiles": result.get("summaryFiles"),
        "yamlCheck": result.get("yamlCheck") or {},
        "yamlStaticValidation": result.get("yamlStaticValidation") or {},
        "yamlExecutability": yaml_executability,
        "yamlExecutableScores": result.get("yamlExecutableScores") or [],
        "generatedCaseGroups": result.get("generatedCaseGroups") or {},
        "review": result.get("review") or {},
        "coverageAudit": result.get("coverageAudit") or {},
        "progressJobId": progress_job_id,
        "reusedPreparedFigma": bool(prepared_figma_context),
    }
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    if summary:
        artifacts["generationSummary"] = summary
        if summary.get("ui_design_assets"):
            source_context["uiDesignAssets"] = summary.get("ui_design_assets") or []
        if summary.get("ignored_figma_pages"):
            source_context["figmaIgnoredPages"] = summary.get("ignored_figma_pages") or source_context.get("figmaIgnoredPages") or []
        if summary.get("knowledge_pages") or summary.get("used_reference_pages"):
            source_context["generationReferencePages"] = summary.get("knowledge_pages") or summary.get("used_reference_pages") or []
    artifacts["qualityReport"] = _build_agent_quality_report(run, result, yaml_file_items, yaml_executability)
    return yaml_file_items, result


def _as_list(value):
    if isinstance(value, list):
        return value
    if value in (None, ""):
        return []
    return [value]


def _quality_points_from_payload(cases_payload):
    if not isinstance(cases_payload, dict):
        return []
    analysis = cases_payload.get("analysis") if isinstance(cases_payload.get("analysis"), dict) else {}
    candidates = (
        analysis.get("requirement_points")
        or analysis.get("requirementPoints")
        or analysis.get("test_points")
        or analysis.get("testPoints")
        or cases_payload.get("requirement_points")
        or []
    )
    return [str(item).strip() for item in _as_list(candidates) if str(item).strip()]


def _build_agent_quality_report(run, generation_result, yaml_file_items=None, yaml_executability=None):
    """Summarise generated artifacts into a reviewer-friendly quality report."""
    result = generation_result if isinstance(generation_result, dict) else {}
    artifacts = run.setdefault("artifacts", {})
    cases_payload = result.get("cases") if isinstance(result.get("cases"), dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    coverage = result.get("coverageAudit") if isinstance(result.get("coverageAudit"), dict) else {}
    review = cases_payload.get("review") if isinstance(cases_payload.get("review"), dict) else {}
    if not coverage and isinstance(review.get("coverage_audit"), dict):
        coverage = review.get("coverage_audit") or {}

    requirement_points = _quality_points_from_payload(cases_payload)
    yaml_items = yaml_file_items or []
    ui_assets = _as_list(summary.get("ui_design_assets")) + _as_list(summary.get("hidden_ui_design_assets"))
    ignored_figma = _as_list(summary.get("ignored_figma_pages")) + _as_list(summary.get("excluded_figma_nodes"))
    manual_cases = _as_list(result.get("manual_cases") or cases_payload.get("manual_cases"))
    auto_case_count = _safe_int_local(result.get("caseCount"), len(_as_list(cases_payload.get("cases"))))
    manual_case_count = _safe_int_local(result.get("manualCaseCount"), len(manual_cases))
    scenario_count = _safe_int_local(result.get("scenarioCount"), len(_as_list(cases_payload.get("scenarios"))))
    requirement_count = _safe_int_local(coverage.get("requirement_point_count"), len(requirement_points))
    executable = yaml_executability if isinstance(yaml_executability, dict) else {}
    executable_task_count = _safe_int_local(executable.get("taskCount"), len(yaml_items))
    yaml_file_count = _safe_int_local(result.get("yamlFileCount"), len(yaml_items))

    warnings: List[str] = []
    blockers: List[str] = []
    if requirement_count <= 0:
        warnings.append("未识别到可追溯需求点，请检查需求解析是否命中真实文档。")
    if scenario_count <= 0:
        warnings.append("未形成业务场景，生成结果可能偏薄。")
    if auto_case_count <= 0:
        blockers.append("没有可自动化用例，不能生成可执行 YAML。")
    if yaml_file_count <= 0 or executable_task_count <= 0:
        blockers.append("没有可执行 YAML 文件或 android/ios tasks 为空。")
    missing_case_points = [str(item) for item in _as_list(coverage.get("missing_case_points")) if str(item).strip()]
    missing_scenario_points = [str(item) for item in _as_list(coverage.get("missing_scenario_points")) if str(item).strip()]
    generic_assertions = [str(item) for item in _as_list(coverage.get("generic_assertion_cases")) if str(item).strip()]
    if missing_case_points:
        warnings.append(f"仍有 {len(missing_case_points)} 个需求点未进入自动化或人工用例。")
    if missing_scenario_points:
        warnings.append(f"仍有 {len(missing_scenario_points)} 个需求点未映射到场景。")
    if generic_assertions:
        warnings.append(f"{len(generic_assertions)} 条用例断言偏泛，需要补充更明确验收点。")
    total_designed = auto_case_count + manual_case_count
    if requirement_count >= 3 and total_designed < max(8, requirement_count * 2):
        warnings.append("完整用例数量偏少，建议补齐边界、异常和人工验证场景。")
    if (summary.get("figma_url") or result.get("figma_url") or (artifacts.get("sourceContext") or {}).get("figmaUrl")) and not ui_assets:
        warnings.append("提供了 Figma 链接，但没有可展示的解析图片，请检查 Figma Token 或具体 Frame 链接。")

    status = "pass"
    if blockers:
        status = "blocked"
    elif warnings:
        status = "warn"

    summary_files = result.get("summaryFiles") or summary.get("summaryFiles") or {}
    report = {
        "status": status,
        "statusText": {"pass": "通过", "warn": "需关注", "blocked": "阻断"}[status],
        "caseSetId": result.get("case_set_id") or summary.get("case_set_id") or "",
        "requirementPointCount": requirement_count,
        "scenarioCount": scenario_count,
        "automationCaseCount": auto_case_count,
        "manualCaseCount": manual_case_count,
        "totalCaseCount": total_designed,
        "yamlFileCount": yaml_file_count,
        "executableTaskCount": executable_task_count,
        "figmaImageCount": len(ui_assets),
        "ignoredFigmaCount": len(ignored_figma),
        "coverageOk": bool(coverage.get("ok")) if coverage else not (missing_case_points or generic_assertions),
        "coverage": {
            "missingCasePoints": missing_case_points[:20],
            "missingScenarioPoints": missing_scenario_points[:20],
            "genericAssertionCases": generic_assertions[:20],
        },
        "warnings": warnings[:20],
        "blockers": blockers[:20],
        "artifacts": {
            "mindmap": summary_files.get("mindmap") or summary_files.get("mm") or "",
            "markdown": summary_files.get("markdown") or "",
            "json": summary_files.get("json") or "",
            "yamlFiles": [item.get("file") for item in yaml_items if isinstance(item, dict) and item.get("file")],
        },
        "layers": [
            {"name": "完整测试用例 .mm", "count": total_designed, "ready": bool(summary_files.get("mindmap") or summary_files.get("mm"))},
            {"name": "可自动化 YAML", "count": yaml_file_count, "ready": yaml_file_count > 0 and executable_task_count > 0},
            {"name": "人工确认/人工用例", "count": manual_case_count + len(missing_case_points), "ready": True},
            {"name": "Figma 解析图片", "count": len(ui_assets), "ready": len(ui_assets) > 0},
        ],
    }
    return report


def _save_agent_yaml_draft(run, artifacts, yaml_text, draft_reason="generated"):
    os.makedirs(AGENT_DRAFT_DIR, exist_ok=True)
    draft_path = os.path.join(AGENT_DRAFT_DIR, f"{run.get('runId')}.yaml")
    write_text_file(draft_path, yaml_text)
    check = validate_agent_yaml_content(yaml_text)
    artifacts["generatedYaml"] = yaml_text
    artifacts["draftPath"] = draft_path
    artifacts["draftConfirmed"] = False
    artifacts["generatedYamlPath"] = ""
    artifacts["yamlRefs"] = [{
        "type": "draft",
        "module": "",
        "file": os.path.basename(draft_path),
        "path": draft_path,
        "content": "",
        "confirmed": False,
        "reason": draft_reason,
    }]
    artifacts["qualityReport"] = {
        "status": "warn" if check.get("ok") else "blocked",
        "statusText": "草稿待确认" if check.get("ok") else "草稿不可执行",
        "requirementPointCount": 0,
        "scenarioCount": 0,
        "automationCaseCount": 0,
        "manualCaseCount": 0,
        "totalCaseCount": 0,
        "yamlFileCount": 0,
        "executableTaskCount": int(check.get("taskCount") or 0),
        "figmaImageCount": len(((artifacts.get("sourceContext") or {}).get("uiDesignAssets") or [])),
        "ignoredFigmaCount": len(((artifacts.get("sourceContext") or {}).get("figmaIgnoredPages") or [])),
        "coverageOk": False,
        "coverage": {
            "missingCasePoints": [],
            "missingScenarioPoints": [],
            "genericAssertionCases": [],
        },
        "warnings": ["当前是可确认 YAML 草稿，还不是正式拆分后的完整用例资产。"] if check.get("ok") else [],
        "blockers": [] if check.get("ok") else (check.get("issues") or ["YAML 草稿校验未通过"]),
        "artifacts": {"mindmap": "", "markdown": "", "json": "", "yamlFiles": []},
        "layers": [
            {"name": "完整测试用例 .mm", "count": 0, "ready": False},
            {"name": "可自动化 YAML", "count": 0, "ready": False},
            {"name": "人工确认/人工用例", "count": 1, "ready": True},
            {"name": "Figma 解析图片", "count": len(((artifacts.get("sourceContext") or {}).get("uiDesignAssets") or [])), "ready": bool(((artifacts.get("sourceContext") or {}).get("uiDesignAssets") or []))},
        ],
    }
    artifacts["requiresConfirm"] = True
    run["status"] = "WAIT_CONFIRM"
    run["currentStep"] = "WAIT_CONFIRM"
    if not any(item.get("type") == "generated_yaml_draft" for item in run.get("pendingConfirmations") or []):
        run.setdefault("pendingConfirmations", []).append({
            "id": f"confirm-{int(time.time())}",
            "type": "generated_yaml_draft",
            "title": "确认 YAML 草稿",
            "action": "confirm_yaml_draft",
            "message": "Agent 已生成 YAML 草稿。确认后会先校验 YAML，并在 Runner 模式下直接进入执行；只有基线回归/测试套模式才需要同步至 Sonic 平台。",
            "draftPath": draft_path,
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "decision": None,
        })
    return draft_path


def _tool_generate_yaml(run):
    """调用 AI Gateway 生成 YAML；已有 YAML 则跳过。"""
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "generate_yaml",
        "category": "AI",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {"target": run.get("target", "")},
    }
    try:
        artifacts = run.setdefault("artifacts", {})
        refs = normalize_yaml_refs(run)
        file_refs = [item for item in refs if item.get("type") == "file" and item.get("confirmed")]
        if file_refs:
            call["status"] = "SKIPPED"
            call["outputSummary"] = f"已有 {len(file_refs)} 个可复用 YAML，跳过生成"
            artifacts["generatedYaml"] = None
            call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            call["durationMs"] = _compute_duration(call)
            _log_tool_call(call, run.get("runId", ""))
            return call
        source_context = artifacts.get("sourceContext") or {}
        source_type = str(source_context.get("sourceType") or run.get("sourceType") or "manual").lower()
        source_text = _build_source_text(source_context)
        try:
            prompt_ctx = get_prompt_center().enrich({
                **(run if isinstance(run, dict) else {}),
                "requirementText": source_text,
                "sourceContext": source_context,
            })
            artifacts["promptCenter"] = prompt_ctx.get("promptCenter")
        except Exception:
            prompt_ctx = {}
        if _agent_is_new_requirement_run(run, source_context):
            pipeline_error = ""
            try:
                yaml_file_items, pipeline_result = _agent_generate_yaml_from_ui_pipeline(run, source_context, source_text)
                refs, err = _confirm_agent_yaml_files(run, artifacts, yaml_file_items)
                if refs and not err:
                    check = _agent_yaml_validation_state(artifacts.get("yamlValidation"))
                    if err:
                        raise ValueError(err)
                    artifacts["yamlValidation"] = {**check, "autoConfirmed": True}
                    call["status"] = "SUCCESS"
                    call["outputSummary"] = (
                        "已调用需求解析/脑图生成/Figma解析/YAML生成主链按用例拆分生成 YAML，"
                        f"用例 {pipeline_result.get('caseCount') or len(refs)} 条，"
                        f"场景 {pipeline_result.get('scenarioCount') or 0} 个，"
                        f"YAML 文件 {len(refs)} 个；可执行校验通过，已自动确认进入下一步"
                    )
                    call["artifactRefs"] = [
                        *[str(item.get("path") or "") for item in refs[:20]],
                        str((pipeline_result.get("summaryFiles") or {}).get("mindmap") or ""),
                        str((pipeline_result.get("summaryFiles") or {}).get("markdown") or ""),
                    ]
                    return _finish_agent_tool_call(call, run)
                artifacts.setdefault("generationPipeline", {})["yamlInvalid"] = artifacts.get("yamlValidation") or {"error": err}
                if err:
                    raise ValueError(err)
            except Exception as e:
                pipeline_error = str(e)[:500]
                pipeline = artifacts.setdefault("generationPipeline", {})
                pipeline["error"] = pipeline_error
                pipeline["errorType"] = e.__class__.__name__
                pipeline["errorTrace"] = traceback.format_exc()[-5000:]
                attach_diagnosis(call, make_diagnosis(
                    "需求解析/脑图/YAML生成主链失败",
                    "不会自动采用少量兜底 YAML，避免复杂需求覆盖不足。",
                    ["查看 generationPipeline.error", "检查 AI Skills / Figma Token", "重新生成或人工修正主链 YAML"],
                    error=str(e)[:300],
                ))
                if _agent_has_rich_requirement_material(source_context):
                    artifacts["yamlValidation"] = {
                        "ok": False,
                        "issues": ["完整生成主链失败，已禁止自动采用兜底 YAML"],
                        "fallbackDisabled": True,
                        "pipelineError": pipeline_error,
                    }
                    call["status"] = "FAILED"
                    call["error"] = f"完整生成主链失败，未采用兜底 YAML：{pipeline_error}"
                    call["outputSummary"] = "完整生成主链失败，未采用兜底 YAML；请查看主链错误并重新生成"
                    return _finish_agent_tool_call(call, run)
            fallback_yaml = _agent_fallback_yaml_draft(run, source_context, source_text)
            fallback_check = validate_agent_yaml_content(fallback_yaml)
            if fallback_check.get("ok"):
                if _agent_execution_mode(run) == "RUNNER_JOB":
                    os.makedirs(AGENT_DRAFT_DIR, exist_ok=True)
                    draft_path = os.path.join(AGENT_DRAFT_DIR, f"{run.get('runId')}.yaml")
                    write_text_file(draft_path, fallback_yaml)
                    refs, err = _confirm_agent_yaml_content_as_files(
                        run,
                        artifacts,
                        fallback_yaml,
                        draft_path=draft_path,
                        reason="fallback_after_ui_yaml_pipeline",
                    )
                    if refs and not err:
                        artifacts.setdefault("generationPipeline", {})["fallbackAutoConfirmed"] = True
                        validation = _agent_yaml_validation_state(artifacts.get("yamlValidation"))
                        artifacts["yamlValidation"] = {
                            **validation,
                            "ok": True,
                            "issues": [],
                            "pipelineIssues": ["需求解析/脑图/YAML生成主链未产出可执行 YAML"],
                            "pipelineError": pipeline_error,
                            "fallbackOk": True,
                            "autoConfirmedFallback": True,
                            "results": validation.get("results") or [{"type": "fallback", **fallback_check}],
                        }
                        quality = artifacts.setdefault("qualityReport", {})
                        quality["status"] = "warn"
                        quality["statusText"] = "已采用兜底 YAML"
                        quality["yamlFileCount"] = len(refs)
                        quality["executableTaskCount"] = int(fallback_check.get("taskCount") or len(refs))
                        existing_warnings = [str(item).strip() for item in _as_list(quality.get("warnings")) if str(item).strip()]
                        quality["warnings"] = [
                            *existing_warnings,
                            "需求解析主链未成功返回正式 YAML，已自动采用可执行兜底 YAML 继续 Runner 执行。",
                        ][:20]
                        call["status"] = "SUCCESS"
                        call["outputSummary"] = (
                            "需求解析/脑图/YAML生成主链未产出可执行 YAML，"
                            f"已自动拆分并采用多任务兜底 YAML（{len(refs)} 个文件 / {fallback_check.get('taskCount')} 条任务），继续校验和 Runner 执行"
                        )
                        call["artifactRefs"] = [str(item.get("path") or "") for item in refs[:20]]
                        return _finish_agent_tool_call(call, run)
                    artifacts.setdefault("generationPipeline", {})["fallbackAutoConfirmError"] = err
                _save_agent_yaml_draft(run, artifacts, fallback_yaml, draft_reason="fallback_after_ui_yaml_pipeline")
                artifacts["yamlValidation"] = {
                    "ok": False,
                    "issues": ["需求解析/脑图/YAML生成主链未产出可执行 YAML"],
                    "pipelineError": pipeline_error,
                    "fallbackOk": True,
                    "results": [{"type": "fallback", **fallback_check}],
                }
                call["status"] = "WAIT_CONFIRM"
                call["outputSummary"] = f"需求解析/脑图/YAML生成主链未产出可执行 YAML，已生成多任务兜底草稿（{fallback_check.get('taskCount')} 条），等待确认后继续"
                return _finish_agent_tool_call(call, run)
            call["status"] = "FAILED"
            call["error"] = "需求解析主链和兜底草稿均未产出可执行 YAML：" + "；".join(fallback_check.get("issues") or [])
            return _finish_agent_tool_call(call, run)
        if _ai_gateway_available():
            try:
                resp = _ai_gateway_post("/ai/generate-yaml", {
                    "target": run.get("target", ""),
                    "requirement": source_text[:8000],
                    "sourceType": source_type,
                    "sourceContext": source_context,
                    "appName": run.get("appName", ""),
                    "platform": run.get("platform", "android"),
                    "businessContext": prompt_ctx.get("businessContext"),
                    "promptCenter": prompt_ctx.get("promptCenter"),
                })
                yaml_text = resp.get("yaml", "") if isinstance(resp, dict) else ""
                artifacts["generatedYaml"] = yaml_text if yaml_text else None
                if yaml_text:
                    check = validate_agent_yaml_content(yaml_text)
                    if not check.get("ok"):
                        fallback_yaml = _agent_fallback_yaml_draft(run, source_context, source_text)
                        fallback_check = validate_agent_yaml_content(fallback_yaml)
                        if fallback_check.get("ok"):
                            _save_agent_yaml_draft(run, artifacts, fallback_yaml, draft_reason="fallback_after_invalid_ai_yaml")
                            artifacts["yamlValidation"] = {
                                "ok": False,
                                "issues": check.get("issues") or [],
                                "fallbackOk": True,
                                "results": [{"type": "ai", **check}, {"type": "fallback", **fallback_check}],
                            }
                            call["status"] = "WAIT_CONFIRM"
                            call["outputSummary"] = "AI YAML 未通过强校验，已生成可确认兜底草稿"
                            attach_diagnosis(call, make_diagnosis(
                                "AI 生成 YAML 为空 tasks 或结构不可执行",
                                "已基于需求/Figma 生成兜底草稿，但执行前仍需人工确认。",
                                ["检查兜底草稿步骤", "结合 Figma 调整入口和断言", "确认后继续校验并执行"],
                                failedYaml=check.get("issues") or [],
                            ))
                            return _finish_agent_tool_call(call, run)
                        artifacts["yamlValidation"] = {"ok": False, "issues": check.get("issues") or [], "results": [{"type": "text", **check}]}
                        call["status"] = "FAILED"
                        call["error"] = "AI 生成 YAML 未通过强校验：" + "；".join(check.get("issues") or [])
                        attach_diagnosis(call, make_diagnosis(
                            "AI 生成 YAML 为空 tasks 或结构不可执行",
                            "兜底草稿也未通过强校验，不能进入执行。",
                            ["重新生成 YAML", "补充需求或 Figma 页面", "人工编辑 YAML 草稿后再确认"],
                            failedYaml=check.get("issues") or [],
                        ))
                        return _finish_agent_tool_call(call, run)
                    _save_agent_yaml_draft(run, artifacts, yaml_text, draft_reason="ai_gateway")
                call["status"] = "WAIT_CONFIRM" if yaml_text else "FAILED"
                call["outputSummary"] = "YAML 草稿生成完成，等待人工确认" if yaml_text else "YAML 生成返回空"
                if not yaml_text:
                    fallback_yaml = _agent_fallback_yaml_draft(run, source_context, source_text)
                    fallback_check = validate_agent_yaml_content(fallback_yaml)
                    if fallback_check.get("ok") and source_text:
                        _save_agent_yaml_draft(run, artifacts, fallback_yaml, draft_reason="fallback_after_empty_ai_yaml")
                        artifacts["yamlValidation"] = {"ok": False, "issues": ["AI Gateway 返回空 YAML"], "fallbackOk": True, "results": [{"type": "fallback", **fallback_check}]}
                        call["status"] = "WAIT_CONFIRM"
                        call["outputSummary"] = "AI 返回空 YAML，已基于需求/Figma 生成可确认兜底草稿"
                        attach_diagnosis(call, make_diagnosis(
                            "AI 生成 YAML 为空",
                            "已生成可人工确认的兜底草稿，避免链路直接中断。",
                            ["检查兜底草稿步骤", "必要时人工补充关键路径", "确认后继续校验并执行"],
                        ))
                    else:
                        attach_diagnosis(call, make_diagnosis(
                            "AI 生成 YAML 为空",
                            "没有可审核的 YAML 草稿，不能继续执行。",
                            ["重新生成 YAML", "补充需求资料", "检查 AI Gateway 返回"],
                        ))
            except Exception as e:
                call["status"] = "SKIPPED"
                call["outputSummary"] = f"AI Gateway YAML 生成失败：{str(e)[:200]}"
                artifacts["generatedYaml"] = None
        else:
            call["status"] = "SKIPPED"
            call["outputSummary"] = "AI Gateway 不可用，跳过 YAML 生成"
            run.setdefault("artifacts", {})["generatedYaml"] = None
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)
        attach_diagnosis(call, make_diagnosis("YAML 生成失败", "无法形成可审核的 YAML 草稿。", ["检查 AI Gateway", "补充需求/Figma", "稍后重试"]))
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def _tool_validate_yaml(run):
    """校验 YAML 格式正确性。"""
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "validate_yaml",
        "category": "READ",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {},
    }
    try:
        artifacts = run.setdefault("artifacts", {})
        refs = normalize_yaml_refs(run)
        if not refs:
            call["status"] = "SKIPPED"
            call["outputSummary"] = "无 YAML 需要校验"
            call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            call["durationMs"] = _compute_duration(call)
            _log_tool_call(call, run.get("runId", ""))
            return call

        results, issues, ok_count = _agent_yaml_dry_run_rows(run, refs)
        passed_results = [row for row in results if isinstance(row, dict) and row.get("ok")]
        failed_results = [row for row in results if isinstance(row, dict) and not row.get("ok")]
        repaired_results = [
            row for row in results
            if isinstance(row, dict)
            and isinstance(row.get("autoRepair"), dict)
            and row.get("autoRepair", {}).get("ok")
        ]
        passed_refs = [
            {
                "type": row.get("type") or "file",
                "source": row.get("source") or row.get("validationMode") or "",
                "generated": bool(row.get("generated")),
                "validationMode": row.get("validationMode") or "",
                "module": row.get("module") or "",
                "file": row.get("file") or "",
                "path": row.get("path") or "",
                "content": row.get("content") or "",
                "confirmed": bool(row.get("confirmed", True)),
                "reason": row.get("reason") or "",
                "executionLevel": row.get("executionLevel") or "",
                "executableScore": row.get("executableScore") if isinstance(row.get("executableScore"), dict) else {},
                "scopeReview": row.get("scopeReview") if isinstance(row.get("scopeReview"), dict) else {},
                "smoke": bool(row.get("smoke")),
                "smokeCandidate": bool(row.get("smokeCandidate")),
                "runnerCandidate": bool(row.get("runnerCandidate")),
            }
            for row in passed_results
        ]
        quarantined_refs = [
            {
                "type": row.get("type") or "file",
                "source": row.get("source") or row.get("validationMode") or "",
                "generated": bool(row.get("generated")),
                "validationMode": row.get("validationMode") or "",
                "module": row.get("module") or "",
                "file": row.get("file") or "",
                "path": row.get("path") or "",
                "executionLevel": row.get("executionLevel") or "draft",
                "issues": list(row.get("issues") or [])[:8],
                "dryRun": row.get("dryRun") if isinstance(row.get("dryRun"), dict) else {},
                "executableScore": row.get("executableScore") if isinstance(row.get("executableScore"), dict) else {},
                "reason": "YAML dry-run 或可执行性准入未通过，已隔离，不下发 Runner。",
            }
            for row in failed_results
        ]
        if passed_refs and (failed_results or repaired_results):
            artifacts["yamlRefs"] = passed_refs
            passed_paths = [ref.get("path") for ref in passed_refs if ref.get("path")]
            if passed_paths:
                artifacts["generatedYamlPath"] = passed_paths[0]
                artifacts["generatedYamlPaths"] = passed_paths
            artifacts["quarantinedYamlRefs"] = quarantined_refs
        elif not failed_results:
            passed_paths = [ref.get("path") for ref in passed_refs if ref.get("path")]
            if passed_paths:
                artifacts["generatedYamlPath"] = passed_paths[0]
                artifacts["generatedYamlPaths"] = passed_paths
            artifacts["quarantinedYamlRefs"] = []
        artifacts["yamlValidation"] = {
            "ok": bool(passed_refs) and not failed_results,
            "partialOk": bool(passed_refs) and bool(failed_results),
            "results": results,
            "issues": issues,
            "passedCount": len(passed_results),
            "failedCount": len(failed_results),
            "quarantinedRefs": quarantined_refs,
            "autoRepairedCount": len(repaired_results),
            "autoRepairs": [row.get("autoRepair") for row in repaired_results],
        }
        _sync_agent_generated_case_groups(artifacts, results)
        if failed_results and not passed_refs:
            call["status"] = "FAILED"
            call["error"] = issues[0][:300] if issues else "YAML dry-run 全部未通过"
            attach_diagnosis(call, make_diagnosis(
                "YAML dry-run 全部未通过",
                "没有可继续下发 Runner 的 YAML。",
                ["查看 dry-run 错误", "重新生成 YAML", "人工编辑 YAML 草稿", "保存为正式 YAML 后再执行"],
                failedYaml=failed_results[0].get("path") or failed_results[0].get("type") if failed_results else "",
            ))
        elif failed_results:
            call["status"] = "PARTIAL_FAILED"
            call["partialFailed"] = True
            call["quarantinedYamlRefs"] = quarantined_refs
            call["outputSummary"] = (
                f"dry-run 校验 {len(refs)} 个 YAML，{ok_count} 个通过；"
                f"{len(failed_results)} 个已隔离，不阻断后续执行"
                + (f"；自动修复 {len(repaired_results)} 个" if repaired_results else "")
            )
        else:
            call["status"] = "SUCCESS"
            call["outputSummary"] = (
                f"dry-run 校验 {len(refs)} 个 YAML，{ok_count} 个通过"
                + (f"；自动修复 {len(repaired_results)} 个" if repaired_results else "")
            )
        if not call.get("outputSummary"):
            call["outputSummary"] = f"dry-run 校验 {len(refs)} 个 YAML，{ok_count} 个通过" + (f"；问题：{'; '.join(issues[:3])}" if issues else "")
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)
        attach_diagnosis(call, make_diagnosis("YAML 校验异常", "无法确认 YAML 是否可执行。", ["检查服务端 PyYAML", "检查 YAML 格式", "重新生成或人工编辑"]))
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def _tool_risk_review(run):
    """扫描 target 和 YAML 内容，评估风险。"""
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "confirm_high_risk_action",
        "category": "CONFIRM",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {"target": run.get("target", "")},
    }
    try:
        risk_detail = _evaluate_risk_detail(run)
        risk_level = risk_detail.get("level") or "LOW"
        hit_kw = risk_detail.get("keyword")
        run["riskLevel"] = risk_level
        run["riskDetail"] = risk_detail
        run.setdefault("artifacts", {})["riskReview"] = risk_detail
        if risk_level == "HIGH":
            run["riskHits"] = [hit_kw] if hit_kw else run.get("riskHits", [])
            call["status"] = "SUCCESS"
            summary_text = _risk_detail_summary(risk_detail, hit_kw)
            if _runner_precheck_should_warn_risk(run, hit_kw):
                summary_text = f"Runner 测试机风险提示，不阻断执行；{summary_text}"
            call["outputSummary"] = summary_text
            call["riskLevel"] = "high"
            call["riskDetail"] = risk_detail
        else:
            run["riskHits"] = []
            call["status"] = "SUCCESS"
            call["outputSummary"] = _risk_detail_summary(risk_detail, hit_kw) if hit_kw else "风险检查通过，无高风险关键词"
            call["riskLevel"] = "low"
            call["riskDetail"] = risk_detail
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def _tool_execution_precheck(run):
    """执行前体检：YAML、Sonic、Runner、Bridge、风险动作全部过关才继续。"""
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "execution_precheck",
        "category": "READ",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {"target": run.get("target", ""), "mode": run.get("mode", "")},
    }
    checks = []
    blockers = []
    warnings = []

    def add(name, ok, detail="", severity="blocker"):
        row = {"name": name, "ok": bool(ok), "detail": str(detail or ""), "severity": severity}
        checks.append(row)
        if not ok:
            (warnings if severity == "warning" else blockers).append(row)
        try:
            artifacts = run.setdefault("artifacts", {})
            artifacts["executionPrecheck"] = {
                "checks": checks,
                "blockers": blockers,
                "warnings": warnings,
                "riskReview": artifacts.get("riskReview"),
                "diagnosis": None,
            }
            current_step = next((s for s in run.get("steps", []) if s.get("step") == "EXECUTION_PRECHECK"), None)
            if current_step and current_step.get("status") == "RUNNING":
                _append_step_trace(
                    run,
                    current_step,
                    f"{'通过' if ok else '未通过'}：{name} - {detail}",
                    status="SUCCESS" if ok else ("WARNING" if severity == "warning" else "FAILED"),
                )
        except Exception:
            pass

    try:
        artifacts = run.setdefault("artifacts", {})
        execution_mode = str(run.get("executionMode") or run.get("execution_mode") or "RUNNER_JOB").strip().upper()
        if execution_mode not in ("RUNNER_JOB", "SONIC_SUITE"):
            execution_mode = "RUNNER_JOB"
        should_require_sonic = execution_mode == "SONIC_SUITE"
        sonic_severity = "blocker" if should_require_sonic else "warning"
        call["input"]["executionMode"] = execution_mode
        refs = normalize_yaml_refs(run)
        file_refs = [ref for ref in refs if ref.get("type") == "file" and ref.get("confirmed")]
        draft_refs = [ref for ref in refs if ref.get("type") in ("draft", "text") and not ref.get("confirmed")]
        add("yamlRefs", bool(refs), f"{len(refs)} 个 YAML 引用")
        add("confirmed_file_yaml", bool(file_refs), f"{len(file_refs)} 个已确认正式 YAML")
        if draft_refs and not file_refs:
            add("draft_confirmed", False, "存在未确认 YAML 草稿", "blocker")

        selected_refs, execution_gate = _select_agent_runner_refs(run, file_refs)
        if execution_gate.get("enabled"):
            severity = "blocker" if not selected_refs else "warning"
            add(
                "generated_yaml_executable_gate",
                bool(selected_refs),
                (
                    f"首批可执行 {execution_gate.get('selectedCount', 0)}/{execution_gate.get('totalCount', 0)}；"
                    f"executable {execution_gate.get('executableCount', 0)}，"
                    f"需复核 {execution_gate.get('needsReviewCount', 0)}，草稿 {execution_gate.get('draftCount', 0)}，"
                    f"人工 {execution_gate.get('manualCount', 0)}，"
                    f"延后 {execution_gate.get('deferredCount', 0)}"
                ),
                severity,
            )

        dry_run_refs = selected_refs if execution_gate.get("enabled") and selected_refs else refs
        dry_run_scope = "首批即将下发 Runner 的 YAML" if execution_gate.get("enabled") and selected_refs else "全部 YAML 引用"
        dry_results, validation_issues, yaml_ok_count = _agent_yaml_dry_run_rows(run, dry_run_refs)
        yaml_dry_ok = not validation_issues and bool(dry_run_refs)
        previous_validation = artifacts.get("yamlValidation") if isinstance(artifacts.get("yamlValidation"), dict) else {}
        artifacts["yamlValidation"] = {
            **previous_validation,
            "executionPrecheckOk": yaml_dry_ok,
            "executionPrecheckIssues": validation_issues,
            "executionPrecheckResults": dry_results,
            "executionPrecheckScope": dry_run_scope,
            "executionGroups": previous_validation.get("executionGroups") or artifacts.get("generatedCaseGroups") or {},
        }
        if _agent_is_generated_yaml_run(run):
            group_results = previous_validation.get("results") if isinstance(previous_validation.get("results"), list) else dry_results
            _sync_agent_generated_case_groups(artifacts, group_results)
        artifacts["yamlDryRun"] = {
            "ok": yaml_dry_ok,
            "checked": len(dry_run_refs),
            "totalRefs": len(refs),
            "scope": dry_run_scope,
            "passed": yaml_ok_count,
            "failed": len(dry_run_refs) - yaml_ok_count,
            "results": dry_results,
        }
        add(
            "yaml_dry_run",
            yaml_dry_ok,
            f"{dry_run_scope}：{yaml_ok_count}/{len(dry_run_refs)} 个通过 dry-run" if yaml_dry_ok else "；".join(validation_issues[:3]),
        )
        quarantined_refs = artifacts.get("quarantinedYamlRefs") if isinstance(artifacts.get("quarantinedYamlRefs"), list) else []
        if quarantined_refs:
            add(
                "yaml_quarantine",
                True,
                f"{len(quarantined_refs)} 个 YAML 未通过准入，已隔离为需人工复核，不下发 Runner",
                "warning",
            )

        if should_require_sonic:
            try:
                from task_server.services import sonic_service
                sonic_service.sonic_request("GET", "/users/list", timeout=5)
                add("sonic_reachable", True, "Sonic API 可访问")
            except Exception as exc:
                add("sonic_reachable", False, f"Sonic API 不可访问：{str(exc)[:180]}", "blocker")
        else:
            add("sonic_reachable", True, "Runner 调试模式不需要访问 Sonic API，已跳过")

        if file_refs and should_require_sonic:
            try:
                from task_server.services import sonic_service
                first_ref = file_refs[0]
                mod = first_ref.get("module") or _task_dir_for_path(first_ref.get("path") or "")[0]
                fn = first_ref.get("file") or os.path.basename(first_ref.get("path") or "")
                publish_precheck = sonic_service.sonic_publish_precheck({"module": mod, "file": fn})
                artifacts["sonicPublishPrecheck"] = publish_precheck
                publish_blockers = publish_precheck.get("blockers") or []
                publish_warnings = publish_precheck.get("warnings") or []
                publish_ok = bool(publish_precheck.get("canPublish"))
                publish_detail = "；".join(str(item) for item in publish_blockers[:3]) if publish_blockers else "应用/Sonic 绑定可发布"
                if not publish_ok and not should_require_sonic:
                    publish_detail += "；Runner 调试模式不阻断，只有同步/执行 Sonic 测试套时才必须处理"
                add(
                    "sonic_project_suite_binding",
                    publish_ok,
                    publish_detail,
                    sonic_severity,
                )
                if publish_warnings:
                    warnings.append({
                        "name": "sonic_publish_warning",
                        "ok": False,
                        "detail": "；".join(str(item) for item in publish_warnings[:3]),
                        "severity": "warning",
                    })
            except Exception as exc:
                detail = f"Sonic 发布预检异常：{str(exc)[:180]}"
                if not should_require_sonic:
                    detail += "；Runner 调试模式不阻断"
                add("sonic_project_suite_binding", False, detail, sonic_severity)
        elif file_refs:
            add("sonic_project_suite_binding", True, "Runner 调试模式已跳过 Sonic 项目/测试套绑定检查")

        public_base = os.getenv("MIDSCENE_PUBLIC_BASE_URL") or os.getenv("TASK_PUBLIC_BASE_URL") or ""
        add("public_base_url", bool(public_base), public_base or "未配置 MIDSCENE_PUBLIC_BASE_URL/TASK_PUBLIC_BASE_URL", "warning")

        token_ok = bool(os.getenv("MIDSCENE_RUNNER_TOKEN", "").strip())
        callback_ok = bool(os.getenv("SONIC_CALLBACK_TOKEN", "").strip())
        add("bridge_token", token_ok, "MIDSCENE_RUNNER_TOKEN 已配置" if token_ok else "MIDSCENE_RUNNER_TOKEN 未配置")
        if should_require_sonic:
            add("callback_token", callback_ok, "SONIC_CALLBACK_TOKEN 已配置" if callback_ok else "SONIC_CALLBACK_TOKEN 未配置", "warning")
            bridge_path = os.getenv("SONIC_BRIDGE_GROOVY_PATH", "/opt/sonic-midscene-task-runner.groovy")
            bridge_text = read_text_file(bridge_path, "") or read_text_file(os.path.join(os.getcwd(), "sonic-midscene-task-runner.groovy"), "")
            bridge_has_endpoint = "/api/sonic/bridge-groovy" in bridge_text
            bridge_has_token = "x-token" in bridge_text
            # /api/sonic/bridge-groovy 是 Sonic 用例中的 bootstrap 地址；本地完整桥接脚本
            # 可能只包含真实执行逻辑，不一定包含该 URL。endpoint 另行探测，避免误判。
            bridge_ok = bool(bridge_text and bridge_has_token)
            if bridge_ok:
                bridge_detail = "桥接脚本包含 x-token"
                if bridge_has_endpoint:
                    bridge_detail += " 和 bridge-groovy"
                else:
                    bridge_detail += "；本地脚本未包含 bootstrap 地址，已改由 bridge-groovy endpoint 单独探测"
            elif not bridge_text:
                bridge_detail = "桥接脚本不存在"
            else:
                bridge_detail = "桥接脚本缺少 x-token"
            add("bridge_groovy", bridge_ok, bridge_detail, sonic_severity)
            bridge_case_id = ""
            try:
                if file_refs:
                    first_yaml = _yaml_ref_content(file_refs[0])
                    match = re.search(r"baseline\.case_id:\s*([A-Za-z0-9_\\-]+)", first_yaml)
                    bridge_case_id = match.group(1) if match else ""
            except Exception:
                bridge_case_id = ""
            try:
                if token_ok:
                    bridge_url = f"http://127.0.0.1:{PORT}/api/sonic/bridge-groovy"
                    if bridge_case_id:
                        bridge_url += "?case_id=" + urllib.parse.quote(bridge_case_id)
                    resp = http_client.get(
                        bridge_url,
                        headers={"x-token": os.getenv("MIDSCENE_RUNNER_TOKEN", "").strip()},
                        timeout=5,
                        read_limit=2000,
                    )
                    body = resp.body
                    body_hint = body.strip()[:80]
                    body_ok = bool(body.strip()) and (
                        "Midscene Sonic Bridge" in body
                        or "bridgeVersion" in body
                        or "runnerToken" in body
                        or "/api/sonic/case" in body
                        or body.lstrip().startswith("import ")
                        or body.lstrip().startswith("//")
                    )
                    add("bridge_groovy_endpoint", resp.status == 200 and body_ok, f"HTTP {resp.status}，已返回桥接脚本" if body_ok else f"HTTP {resp.status}，响应异常：{body_hint}", sonic_severity)
                else:
                    add("bridge_groovy_endpoint", False, "MIDSCENE_RUNNER_TOKEN 未配置，无法验证桥接接口", sonic_severity)
            except Exception as exc:
                detail = f"桥接接口不可达：{str(exc)[:180]}"
                add("bridge_groovy_endpoint", False, detail, sonic_severity)
        else:
            add("callback_token", True, "Runner 调试模式不需要 Sonic 回调 Token，已跳过")
            add("bridge_groovy", True, "Runner 调试模式不使用 Sonic 桥接脚本，已跳过")
            add("bridge_groovy_endpoint", True, "Runner 调试模式不调用 bridge-groovy，已跳过")

        try:
            from task_server.services import runner_service
            runners = runner_service.list_runners()
            online = [rid for rid, item in runners.items() if item.get("online")]
            selected_runner = str(run.get("runnerId") or run.get("runner_id") or "").strip()
            selected_device = str(run.get("deviceId") or run.get("device_id") or "").strip()
            selected_strategy = str(run.get("deviceStrategy") or run.get("device_strategy") or "auto").strip().lower()
            online_devices = [
                d for d in runner_service.all_online_devices()
                if d.get("runner_online") and d.get("status") in ("online", "device")
            ]
            artifacts["runnerSelection"] = {
                "runnerId": selected_runner,
                "deviceId": selected_device,
                "deviceStrategy": selected_strategy,
                "onlineRunners": online[:20],
                "onlineDevices": online_devices[:50],
            }
            if selected_runner:
                runner = runners.get(selected_runner) or {}
                runner_ok = bool(runner.get("online"))
                runner_devices = runner_service.runner_device_ids(runner)
                device_ok = (not selected_device) or selected_device in runner_devices
                detail = f"指定 Runner：{selected_runner}"
                if selected_device:
                    detail += f"，设备：{selected_device}"
                if not runner_ok:
                    detail += "；Runner 不在线"
                elif selected_device and not device_ok:
                    detail += "；目标设备不在线"
                else:
                    detail += "；已在线"
                add("runner_online", runner_ok and device_ok, detail)
            elif selected_device:
                device_ok = any(d.get("device_id") == selected_device for d in online_devices)
                add("runner_online", device_ok, f"指定设备：{selected_device}，{'已在线' if device_ok else '未在线'}")
            elif selected_strategy != "auto":
                add("runner_online", False, "尚未选择执行设备；请在 Agent 表单选择具体设备，或选择自动分配在线设备")
            else:
                add("runner_online", bool(online_devices or online), f"在线 Runner：{', '.join(online[:5])}；在线设备 {len(online_devices)} 台" if online else "Runner 不在线")
        except Exception as exc:
            add("runner_online", False, f"读取 Runner 失败：{str(exc)[:120]}")

        risk_detail = _evaluate_risk_detail(run)
        risk_level = risk_detail.get("level") or "LOW"
        hit_kw = risk_detail.get("keyword")
        run["riskLevel"] = risk_level
        run["riskDetail"] = risk_detail
        artifacts["riskReview"] = risk_detail
        high_risk = risk_level == "HIGH"
        if high_risk and not run.get("riskConfirmed"):
            risk_summary = _risk_detail_summary(risk_detail, hit_kw)
            if _runner_precheck_should_warn_risk(run, hit_kw):
                add("high_risk_confirm", False, f"Runner 调试模式仅提醒，不阻断；{risk_summary}", "warning")
            else:
                add("high_risk_confirm", False, risk_summary, "blocker")
                run["status"] = "WAIT_CONFIRM"
                run["currentStep"] = "WAIT_CONFIRM"
                if not any(c.get("type") == "high_risk_action" for c in run.get("pendingConfirmations", [])):
                    run.setdefault("pendingConfirmations", []).append({
                        "id": f"confirm-{int(time.time())}",
                        "type": "high_risk_action",
                        "title": "确认平台级高风险动作",
                        "action": "confirm_high_risk_action",
                        "message": f"{risk_summary}。请确认是否继续执行。",
                        "riskKeyword": hit_kw,
                        "riskSource": risk_detail.get("source") or "",
                        "riskSnippet": risk_detail.get("snippet") or "",
                        "riskDetail": risk_detail,
                        "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                        "decision": None,
                    })
        else:
            safe_detail = _risk_detail_summary(risk_detail, hit_kw) if hit_kw else "无高风险动作"
            add("high_risk_confirm", True, safe_detail if not high_risk else "已人工确认")

        artifacts["executionPrecheck"] = {
            "checks": checks,
            "blockers": blockers,
            "warnings": warnings,
            "riskReview": artifacts.get("riskReview"),
            "diagnosis": None,
        }
        if blockers:
            root = "执行前体检未通过"
            if draft_refs and not file_refs:
                root = "YAML 草稿未确认，不能同步 Sonic"
            elif any(item["name"] == "runner_online" for item in blockers):
                root = "Runner 不在线，不能继续执行"
            elif any(item["name"] == "bridge_token" for item in blockers):
                root = "Bridge Token 未配置，Sonic 桥接会鉴权失败"
            elif any(item["name"] == "bridge_groovy_endpoint" for item in blockers):
                root = "Sonic 桥接脚本接口不可达或鉴权失败"
            elif any(item["name"] == "sonic_project_suite_binding" for item in blockers):
                root = "应用/Sonic 项目或测试套绑定未通过"
            blocker_text = "；".join(f"{item.get('name')}: {item.get('detail')}" for item in blockers[:5])
            call["status"] = "WAIT_CONFIRM" if run.get("status") == "WAIT_CONFIRM" else "FAILED"
            call["error"] = root
            call["outputSummary"] = f"{root}：{blocker_text}" if blocker_text else root
            next_actions = ["处理体检失败项", "确认 YAML 草稿/平台级高风险动作", "确认 Runner 在线后重试"]
            if any(item["name"] == "runner_online" for item in blockers):
                next_actions = ["启动 Windows/Mac Runner", "确认 Runner 控制台心跳正常", "刷新 Agent 后重试"]
            elif any(item["name"] in ("bridge_token", "bridge_groovy_endpoint") for item in blockers):
                next_actions = ["检查 /opt/midscene.env 的 MIDSCENE_RUNNER_TOKEN", "重新同步/刷新 Sonic 桥接脚本", "访问 /api/sonic/bridge-diagnose 查看详情"]
            elif any(item["name"] == "sonic_project_suite_binding" for item in blockers):
                next_actions = ["在配置页绑定应用的 Sonic 项目/测试套", "确认 YAML 状态为已入库或基线", "重新同步 Sonic 用例"]
            diagnosis = make_diagnosis(
                root,
                "继续执行可能失败或误同步错误 YAML。",
                next_actions,
                failedChecks=blockers,
            )
            artifacts["diagnosis"] = diagnosis
            artifacts["executionPrecheck"]["diagnosis"] = diagnosis
            call["checks"] = checks
            call["blockers"] = blockers
            call["warnings"] = warnings
            attach_diagnosis(call, diagnosis)
        else:
            call["status"] = "SUCCESS"
            call["outputSummary"] = f"执行前体检通过，{len(warnings)} 个提醒"
            call["checks"] = checks
            call["warnings"] = warnings
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)[:500]
        attach_diagnosis(call, make_diagnosis("执行前体检异常", "无法确认 Sonic/Runner/YAML 状态。", ["查看服务端日志", "检查运行配置", "重新触发 Agent"]))
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def _tool_sync_sonic(run):
    """同步已确认的正式 YAML 到 Sonic。

    Agent 只允许同步 normalize_yaml_refs 返回的 confirmed file 引用；
    draft/text 永远不能被当作路径推送。
    """
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "sonic_sync_case",
        "category": "SONIC",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {},
    }
    try:
        artifacts = run.setdefault("artifacts", {})
        refs = normalize_yaml_refs(run)
        blocked_refs = [ref for ref in refs if ref.get("type") in ("draft", "text") and not ref.get("confirmed")]
        file_refs = [ref for ref in refs if ref.get("type") == "file" and ref.get("confirmed")]

        if blocked_refs and not file_refs:
            run["status"] = "WAIT_CONFIRM"
            run["currentStep"] = "WAIT_CONFIRM"
            call["status"] = "WAIT_CONFIRM"
            call["outputSummary"] = "YAML 草稿未确认，不能同步 Sonic"
            attach_diagnosis(call, make_diagnosis(
                "YAML 草稿未确认，不能同步 Sonic",
                "未确认草稿可能覆盖错误用例或执行空任务。",
                ["打开 YAML 草稿并确认", "保存为正式 YAML", "再执行 Sonic 同步"],
                yamlRefs=blocked_refs,
            ))
            call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            call["durationMs"] = _compute_duration(call)
            _log_tool_call(call, run.get("runId", ""))
            return call

        if not file_refs:
            call["status"] = "FAILED"
            call["error"] = "无可同步 YAML 文件"
            call["outputSummary"] = "无可同步 YAML 文件"
            attach_diagnosis(call, make_diagnosis(
                "没有已确认的正式 YAML 文件",
                "Agent 无法同步 Sonic，也无法进入真实执行。",
                ["先匹配已有 YAML", "或确认 AI 生成的 YAML 草稿", "确认后再同步 Sonic"],
                yamlRefs=refs,
            ))
            artifacts["sonicSync"] = {"synced": [], "failed": [], "total": 0, "syncedCount": 0, "failedCount": 0}
            call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            call["durationMs"] = _compute_duration(call)
            _log_tool_call(call, run.get("runId", ""))
            return call

        synced = []
        failed_items = []
        suite_ids = set()

        from task_server.services import sonic_service

        for ref in file_refs:
            try:
                yf = str(ref.get("path") or "")
                if not yf or _looks_like_yaml_text(yf) or not os.path.exists(yf):
                    label = yf[:120] if yf else ref.get("file", "")
                    failed_items.append({
                        "module": ref.get("module", ""),
                        "file": ref.get("file", ""),
                        "taskName": "",
                        "error": f"路径格式异常或文件不存在: {label}",
                        "projectId": None,
                        "suiteId": None,
                    })
                    continue

                mod = ref.get("module") or _task_dir_for_path(yf)[0]
                fn = ref.get("file") or os.path.basename(yf)
                task_name = fn.replace(".yaml", "").replace(".yml", "")

                result = sonic_service.sonic_publish_yaml({"module": mod, "file": fn, "dryRun": False, "force": True})

                if isinstance(result, dict) and result.get("ok"):
                    item = {"module": mod, "file": fn, "taskName": task_name, "path": yf}
                    warnings = []
                    for r in (result.get("results") or []):
                        sid = r.get("sonic_suite_id") or (r.get("suite_sync") or {}).get("suite_id")
                        if sid:
                            suite_ids.add(int(sid))
                        if r.get("warning"):
                            warnings.append(r.get("warning"))
                    if warnings:
                        item["warnings"] = warnings
                    synced.append(item)
                else:
                    error_msg = ""
                    if isinstance(result, dict):
                        error_msg = result.get("error") or ""
                        # 从 precheck 提取更详细原因
                        precheck = result.get("precheck") or {}
                        blockers = precheck.get("blockers") or []
                        if blockers:
                            error_msg += " | blockers: " + "; ".join(str(b) for b in blockers[:3])
                    failed_items.append({
                        "module": mod,
                        "file": fn,
                        "taskName": task_name,
                        "error": error_msg[:500] or "未知错误",
                        "projectId": (result or {}).get("project_id"),
                        "suiteId": (result or {}).get("sonic_suite_id"),
                        "rawResult": str(result)[:300] if result else "",
                        "path": yf,
                    })
            except Exception as e:
                import traceback
                tb_str = traceback.format_exc()
                failed_items.append({
                    "module": ref.get("module", ""),
                    "file": ref.get("file", ""),
                    "taskName": str(ref.get("file") or "").replace(".yaml", "").replace(".yml", ""),
                    "error": str(e)[:500],
                    "traceback": tb_str[:1000],
                    "projectId": None,
                    "suiteId": None,
                })

        # 记录到 artifacts
        artifacts = run.setdefault("artifacts", {})
        artifacts["sonicSync"] = {
            "synced": synced,
            "failed": failed_items,
            "total": len(file_refs),
            "syncedCount": len(synced),
            "failedCount": len(failed_items),
        }
        if suite_ids:
            artifacts["sonicSuiteId"] = list(suite_ids)[0]
            artifacts["sonicSuiteIds"] = list(suite_ids)

        # 根据结果设置状态
        if len(synced) == 0 and len(failed_items) > 0:
            call["status"] = "FAILED"
            call["outputSummary"] = f"Sonic 同步全部失败，0/{len(file_refs)} 成功，{len(failed_items)} 失败"
            call["error"] = failed_items[0].get("error", "")[:200] if failed_items else ""
            attach_diagnosis(call, make_diagnosis(
                "Sonic 同步失败",
                "无法把当前 YAML 发布到 Sonic，后续执行会被阻断。",
                ["检查应用 Sonic 绑定", "刷新 Sonic 桥接脚本", "确认 MIDSCENE_RUNNER_TOKEN 一致", "重新同步该 YAML"],
                failedYaml=failed_items[0] if failed_items else {},
            ))
        elif len(failed_items) > 0:
            call["status"] = "PARTIAL_FAILED"
            call["outputSummary"] = f"Sonic 同步部分成功，{len(synced)}/{len(file_refs)} 成功，{len(failed_items)} 失败"
        else:
            call["status"] = "SUCCESS"
            call["outputSummary"] = f"Sonic 同步完成，{len(synced)}/{len(file_refs)} 全部成功"

    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)[:500]
        call["outputSummary"] = f"Sonic 同步异常：{str(e)[:200]}"
        attach_diagnosis(call, make_diagnosis("Sonic 同步异常", "无法进入后续执行。", ["查看服务端日志", "检查 Sonic 配置", "刷新桥接脚本"]))
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def sonic_force_run_suite(suite_id):
    """触发 Sonic 测试套强制执行，返回 resultId。"""
    if not suite_id:
        return {"ok": False, "error": "suiteId 为空"}
    try:
        from task_server.services import sonic_service
        resp = sonic_service.sonic_request("GET", "/testSuites/runSuite", params={"id": suite_id}, timeout=30)
        data = sonic_service.sonic_response_data(resp)
        if data and isinstance(data, dict):
            result_id = data.get("id") or data.get("resultId") or data.get("result_id")
            return {"ok": True, "resultId": result_id, "data": data}
        # Sonic可能直接返回 resultId 作为整数
        if isinstance(data, (int, str)) and data:
            return {"ok": True, "resultId": data}
        return {"ok": True, "resultId": None, "raw": resp}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}


def _sonic_result_finished(result):
    """Return true when Sonic result detail indicates the suite has finished."""
    if not isinstance(result, dict):
        return False
    if result.get("finished") is True:
        return True
    if result.get("endTime") or result.get("end_time"):
        return True
    send_count = _safe_int_local(result.get("sendMsgCount") or result.get("send_msg_count"), 0)
    receive_count = _safe_int_local(result.get("receiveMsgCount") or result.get("receive_msg_count"), 0)
    return bool(send_count and receive_count >= send_count)


def _safe_int_local(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _sonic_result_status(result):
    """Normalize Sonic result detail into pass/fail/running/warning."""
    if not isinstance(result, dict):
        return "unknown"
    status_value = _safe_int_local(result.get("status"), -1)
    status_text = " ".join(str(result.get(k) or "") for k in (
        "statusText", "status_text", "statusName", "status_name", "result", "message", "msg"
    )).lower()
    if status_value == 1 or any(word in status_text for word in ("success", "passed", "pass", "成功", "通过")):
        return "pass"
    if status_value == 3 or any(word in status_text for word in ("fail", "failed", "失败")):
        return "fail"
    if status_value == 2 or any(word in status_text for word in ("warn", "warning", "异常", "告警")):
        return "warning"
    return "running" if not _sonic_result_finished(result) else "warning"


def _wait_sonic_suite_result(result_id, run, timeout=600, interval=10):
    """Poll Sonic result detail until finished or timeout.

    The Agent path triggers a Sonic suite run and receives resultId directly.
    Polling the result API keeps this flow independent from Feishu callbacks.
    """
    from task_server.services import sonic_service
    start = time.time()
    last_payload = {}
    result_id_text = str(result_id or "").strip()
    while time.time() - start < timeout:
        payload = sonic_service.sonic_read_result(result_id_text)
        last_payload = payload if isinstance(payload, dict) else {"ok": False, "raw": payload}
        result = last_payload.get("result") if isinstance(last_payload, dict) else {}
        if isinstance(result, dict) and result:
            status = _sonic_result_status(result)
            finished = _sonic_result_finished(result)
            project_id = result.get("projectId") or result.get("project_id") or result.get("project")
            report_url = ""
            try:
                report_url = sonic_service.sonic_result_detail_url(project_id, result_id_text)
            except Exception:
                report_url = ""
            summary = {
                "resultId": result_id_text,
                "status": status,
                "finished": finished,
                "sendMsgCount": result.get("sendMsgCount") or result.get("send_msg_count"),
                "receiveMsgCount": result.get("receiveMsgCount") or result.get("receive_msg_count"),
                "createTime": result.get("createTime") or result.get("create_time") or "",
                "endTime": result.get("endTime") or result.get("end_time") or "",
                "reportUrl": report_url,
            }
            run.setdefault("artifacts", {})["sonicExecResult"] = summary
            try:
                with AGENT_RUN_LOCK:
                    all_runs = load_agent_runs()
                    for i, item in enumerate(all_runs):
                        if item.get("runId") == run.get("runId"):
                            all_runs[i] = run
                            break
                    save_agent_runs(all_runs)
            except Exception:
                pass
            if finished:
                return summary
        elif isinstance(last_payload, dict) and not last_payload.get("ok"):
            return {
                "resultId": result_id_text,
                "status": "error",
                "finished": False,
                "summary": last_payload.get("error") or "读取 Sonic result 失败",
            }
        time.sleep(max(1, interval))
    return {
        "resultId": result_id_text,
        "status": "timeout",
        "finished": False,
        "summary": f"等待 Sonic result {result_id_text} 超时",
        "last": last_payload,
    }


def wait_jobs_finished(job_ids, run, timeout=600, interval=5):
    """等待 job 列表全部进入终态，期间更新 Agent Run 进度。

    返回: {"completed": [...], "failed": [...], "running": [...], "timeout": [...]}
    """
    if not job_ids:
        return {"completed": [], "failed": [], "running": [], "timeout": []}

    TERMINAL_STATES = {"success", "failed", "timeout", "cancelled", "error"}
    start_time = time.time()

    while True:
        elapsed = time.time() - start_time
        if elapsed >= timeout:
            break

        from task_server.services import job_service
        with JOB_LOCK:
            jobs = job_service.load_jobs()

        completed = []
        failed = []
        running = []

        for jid in job_ids:
            job = next((j for j in jobs if j.get("job_id") == jid), None)
            if not job:
                failed.append({"job_id": jid, "status": "not_found", "error": "任务不存在"})
                continue
            status = (job.get("status") or "").lower()
            if status in TERMINAL_STATES:
                entry = {
                    "job_id": jid,
                    "status": status,
                    "module": job.get("module", ""),
                    "file": job.get("file", ""),
                    "error": job.get("error", ""),
                    "report_url": job.get("report_url") or job.get("reportUrl", ""),
                }
                if status == "success":
                    completed.append(entry)
                else:
                    failed.append(entry)
            else:
                running.append({"job_id": jid, "status": status, "module": job.get("module", ""), "file": job.get("file", "")})

        # 更新 Agent Run 的执行进度
        artifacts = run.setdefault("artifacts", {})
        artifacts["jobProgress"] = {
            "total": len(job_ids),
            "completed": len(completed),
            "failed": len(failed),
            "running": len(running),
            "elapsed": int(elapsed),
        }
        # 实时更新步骤摘要，让前端时间线能看到当前进度
        progress_parts = [f"等待执行 ({int(elapsed)}s)"]
        if completed:
            progress_parts.append(f"{len(completed)} 完成")
        if failed:
            progress_parts.append(f"{len(failed)} 失败")
        if running:
            running_names = [r.get('file', r.get('job_id', ''))[:20] for r in running[:3]]
            progress_parts.append(f"{len(running)} 运行中: {', '.join(running_names)}")
        # 更新当前步骤的日志
        current_step = None
        for step in (run.get("steps") or []):
            if step.get("toolName") == "execute_tasks" or step.get("title") == "执行任务":
                current_step = step
        if current_step:
            current_step["outputSummary"] = " | ".join(progress_parts)
        # 持久化进度（让前端能看到）
        try:
            with AGENT_RUN_LOCK:
                all_runs = load_agent_runs()
                for i, r in enumerate(all_runs):
                    if r.get("runId") == run.get("runId"):
                        all_runs[i] = run
                        break
                save_agent_runs(all_runs)
        except Exception:
            pass

        # 全部结束则退出
        if not running:
            return {"completed": completed, "failed": failed, "running": [], "timeout": []}

        time.sleep(interval)

    # 超时：仍在运行的标记为 timeout
    from task_server.services import job_service
    with JOB_LOCK:
        jobs = job_service.load_jobs()

    timeout_jobs = []
    for jid in job_ids:
        job = next((j for j in jobs if j.get("job_id") == jid), None)
        if job and (job.get("status") or "").lower() not in TERMINAL_STATES:
            timeout_jobs.append({"job_id": jid, "status": "timeout", "module": job.get("module", ""), "file": job.get("file", "")})

    # 重新统计最终状态
    completed = []
    failed = []
    for jid in job_ids:
        job = next((j for j in jobs if j.get("job_id") == jid), None)
        if not job:
            continue
        status = (job.get("status") or "").lower()
        entry = {"job_id": jid, "status": status, "module": job.get("module", ""), "file": job.get("file", ""), "error": job.get("error", "")}
        if status == "success":
            completed.append(entry)
        elif status in TERMINAL_STATES:
            failed.append(entry)

    return {"completed": completed, "failed": failed, "running": [], "timeout": timeout_jobs}


def _tool_run_sonic(run):
    """按执行模式触发 Runner 单条/多条任务或 Sonic 测试套。

    路径提取：直接从绝对路径取倒数两级 path_parts[-2]=mod, path_parts[-1]=fn
    等待任务执行完成。
    """
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "create_runner_job",
        "category": "TASK",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {"target": run.get("target", "")},
    }
    try:
        from task_server.services import job_service
        artifacts = run.setdefault("artifacts", {})
        refs = normalize_yaml_refs(run)
        file_refs = [ref for ref in refs if ref.get("type") == "file" and ref.get("confirmed")]
        execution_mode = str(run.get("executionMode") or run.get("execution_mode") or "RUNNER_JOB").strip().upper()
        if execution_mode not in ("RUNNER_JOB", "SONIC_SUITE"):
            execution_mode = "RUNNER_JOB"
        should_run_suite = execution_mode == "SONIC_SUITE"
        suite_id = artifacts.get("sonicSuiteId") if should_run_suite else None

        if not file_refs:
            call["status"] = "FAILED"
            call["error"] = "无已确认 YAML 文件，不能执行"
            call["outputSummary"] = "无已确认 YAML 文件，不能执行"
            attach_diagnosis(call, make_diagnosis(
                "没有已确认的正式 YAML 文件",
                "Runner 无法创建执行任务。",
                ["先确认 YAML 草稿", "或选择已有 YAML 用例", "执行前再次校验 YAML"],
            ))
            call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            call["durationMs"] = _compute_duration(call)
            _log_tool_call(call, run.get("runId", ""))
            return call

        # 1. 仅在用户明确选择套件回归时触发 Sonic 测试套。
        # 默认 RUNNER_JOB 模式只执行匹配到的 YAML，避免“匹配 1 条却跑完整套件”。
        sonic_result_id = None
        if should_run_suite and not suite_id:
            call["status"] = "FAILED"
            call["error"] = "执行模式为 SONIC_SUITE，但未找到 Sonic 测试套绑定"
            call["outputSummary"] = call["error"]
            attach_diagnosis(call, make_diagnosis(
                "Sonic 测试套未绑定",
                "无法按用户选择的 SONIC_SUITE 模式执行。",
                ["在应用配置里绑定 Sonic 测试套", "或切换为 Runner 单条/多条调试模式"],
            ))
            call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            call["durationMs"] = _compute_duration(call)
            _log_tool_call(call, run.get("runId", ""))
            return call
        if suite_id:
            try:
                run_result = sonic_force_run_suite(suite_id)
                if run_result.get("ok"):
                    sonic_result_id = run_result.get("resultId")
                else:
                    call["status"] = "FAILED"
                    call["error"] = run_result.get("error") or "Sonic 套件触发失败"
            except Exception as e:
                call["status"] = "FAILED"
                call["error"] = f"Sonic 套件触发异常: {e}"
            if should_run_suite and not sonic_result_id:
                call["outputSummary"] = call.get("error") or "Sonic 套件触发失败"
                attach_diagnosis(call, make_diagnosis(
                    "Sonic 套件触发失败",
                    "Agent 不会自动回退 Runner，避免用户误以为跑的是同一种模式。",
                    ["检查 Sonic 套件是否可执行", "查看 Sonic 登录/token 状态", "确认后手动切换 Runner 模式"],
                ))
                call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                call["durationMs"] = _compute_duration(call)
                _log_tool_call(call, run.get("runId", ""))
                return call

        # 更新 artifacts
        run_artifacts = run.setdefault("artifacts", {})
        job_ids = []
        if sonic_result_id:
            run_artifacts["sonicResultId"] = sonic_result_id

        # === Sonic 套件模式：触发成功后轮询 Sonic 执行结果，不创建本地 Job ===
        if sonic_result_id:
            summary_parts = [f"Sonic 套件已触发 (resultId: {sonic_result_id})", f"共 {len(file_refs)} 个用例"]
            call["outputSummary"] = " | ".join(summary_parts)
            # 持久化一次让前端看到触发状态
            try:
                with AGENT_RUN_LOCK:
                    all_runs = load_agent_runs()
                    for i, r in enumerate(all_runs):
                        if r.get("runId") == run.get("runId"):
                            all_runs[i] = run
                            break
                    save_agent_runs(all_runs)
            except Exception:
                pass
            # 轮询 Sonic 执行结果
            sonic_wait = _wait_sonic_suite_result(sonic_result_id, run, timeout=600, interval=10)
            run_artifacts["sonicExecResult"] = sonic_wait
            if sonic_wait.get("status") == "pass":
                call["status"] = "SUCCESS"
                summary_parts.append("✅ 全部通过")
            elif sonic_wait.get("status") == "timeout":
                call["status"] = "PARTIAL_FAILED"
                summary_parts.append("⚠️ 超时，请在 Sonic 平台查看结果")
            else:
                call["status"] = "PARTIAL_FAILED"
                summary_parts.append(f"❌ {sonic_wait.get('status', 'unknown')}: {sonic_wait.get('summary', '')}")
        else:
            # 默认 Runner Job 模式：只执行匹配到的 YAML；套件触发失败时也回退到这里。
            selected_runner_id = str(run.get("runnerId") or run.get("runner_id") or "").strip()
            selected_device_id = str(run.get("deviceId") or run.get("device_id") or "").strip()
            selected_device_strategy = job_service.normalize_device_strategy(
                run.get("deviceStrategy") or run.get("device_strategy") or "auto",
                device_id=selected_device_id,
                runner_id=selected_runner_id,
            )
            file_refs, execution_gate = _select_agent_runner_refs(run, file_refs)
            gate_blocked = list((execution_gate or {}).get("blocking") or [])
            gate_deferred = list((execution_gate or {}).get("deferred") or [])
            if (execution_gate or {}).get("enabled") and not file_refs:
                run_artifacts["runnerExecutionGate"] = execution_gate
                call["status"] = "FAILED"
                call["error"] = "Agent 生成的 YAML 未达到自动执行准入"
                call["outputSummary"] = (
                    "Agent 生成的 YAML 未达到 executable，未创建 Runner 任务；"
                    f"可执行 {(execution_gate or {}).get('executableCount', 0)} 个，"
                    f"需复核 {(execution_gate or {}).get('needsReviewCount', 0)} 个，"
                    f"草稿 {(execution_gate or {}).get('draftCount', 0)} 个，"
                    f"人工 {(execution_gate or {}).get('manualCount', 0)} 个"
                )
                attach_diagnosis(call, make_diagnosis(
                    "生成 YAML 未通过自动执行准入",
                    "文件可以作为用例资产保留，但还不应该直接下发 Runner。",
                    ["查看 runnerExecutionGate 明细", "优先修复缺少稳定起点/等待的 YAML", "确认后再手工执行或重新生成"],
                    blocked=gate_blocked[:8],
                ))
                call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                call["durationMs"] = _compute_duration(call)
                _log_tool_call(call, run.get("runId", ""))
                return call
            runner_dry_run_enabled, runner_dry_run_reason = _runner_supports_yaml_dry_run(selected_runner_id)
            created = _agent_create_runner_jobs_for_refs(
                run,
                file_refs,
                selected_runner_id,
                selected_device_id,
                selected_device_strategy,
                runner_dry_run_enabled=runner_dry_run_enabled,
                initial_blocked=gate_blocked,
                phase="smoke",
            )
            dry_run_results = created["dryRunResults"]
            dry_run_blocked = created["dryRunBlocked"]
            runner_dry_run_jobs = created["runnerDryRunJobs"]
            job_ids = created["jobIds"]

            dry_run_passed = sum(1 for item in dry_run_results if item.get("ok"))
            run_artifacts["runnerDryRun"] = {
                "ok": not dry_run_blocked,
                "checked": len(dry_run_results),
                "blockedCount": len(dry_run_blocked),
                "createdCount": len(job_ids),
                "mode": "runner_yaml_dry_run" if runner_dry_run_enabled else "mock_dry_run",
                "reason": runner_dry_run_reason,
                "runnerJobIds": runner_dry_run_jobs,
                "results": dry_run_results,
                "blocked": dry_run_blocked,
                "deferred": gate_deferred,
                "deferredCount": len(gate_deferred),
                "executionGate": execution_gate,
            }
            summary_parts = [
                f"Runner 调试模式：{run_artifacts['runnerDryRun']['mode']} 通过 {dry_run_passed} 个，拦截 {len(dry_run_blocked)} 个，创建 {len(job_ids)} 个本地任务"
            ]
            if (execution_gate or {}).get("enabled"):
                summary_parts.append(
                    f"执行准入：首批选择 {execution_gate.get('selectedCount', 0)} / {execution_gate.get('totalCount', 0)} 个 generated YAML，延后 {execution_gate.get('deferredCount', 0)} 个"
                )
            run_artifacts["jobIds"] = job_ids
            if not job_ids:
                call["status"] = "FAILED"
                call["error"] = "Runner 任务创建失败" if not dry_run_blocked else "Runner 下发前 dry-run 未通过"
                attach_diagnosis(call, make_diagnosis(
                    "Runner 任务未创建",
                    "已确认 YAML 未能进入本地执行队列；若 dry-run 已拦截，说明文件在下发前就不满足平台加载要求。",
                    ["查看 runnerDryRun 拦截原因", "修复 YAML 后再重试", "确认任务目录权限和 Runner 在线"],
                    blocked=dry_run_blocked[:8],
                ))

        # === 等待本地 Job 执行完成（仅 Runner 模式） ===
        job_ids = run_artifacts.get("jobIds", [])
        if job_ids:
            # 先持久化当前状态
            try:
                with AGENT_RUN_LOCK:
                    all_runs = load_agent_runs()
                    for i, r in enumerate(all_runs):
                        if r.get("runId") == run.get("runId"):
                            all_runs[i] = run
                            break
                    save_agent_runs(all_runs)
            except Exception:
                pass

            wait_timeout = job_service.runner_job_wait_timeout_seconds(len(job_ids))
            wait_result = job_service.wait_jobs_finished(
                job_ids,
                run,
                timeout=wait_timeout,
                interval=5,
                phase="首批冒烟",
            )
            run_artifacts["jobResult"] = {
                "completedCount": len(wait_result["completed"]),
                "failedCount": len(wait_result["failed"]),
                "timeoutCount": len(wait_result["timeout"]),
                "completed": wait_result["completed"],
                "failed": wait_result["failed"],
                "timeout": wait_result["timeout"],
                "waitTimeoutSeconds": wait_timeout,
                "phases": {
                    "smoke": {
                        "jobIds": list(job_ids),
                        "completedCount": len(wait_result["completed"]),
                        "failedCount": len(wait_result["failed"]),
                        "timeoutCount": len(wait_result["timeout"]),
                        "waitTimeoutSeconds": wait_timeout,
                    }
                },
            }
            failure_reasons = _agent_job_failure_reasons(
                list(wait_result.get("failed") or []) + list(wait_result.get("timeout") or []),
                limit=8,
            )
            if failure_reasons:
                run_artifacts["jobFailureReasons"] = failure_reasons
                run_artifacts["jobResult"]["failureReasons"] = failure_reasons

            if (locals().get("execution_gate") or {}).get("enabled"):
                smoke_total = len(wait_result["completed"]) + len(wait_result["failed"]) + len(wait_result["timeout"])
                smoke_failed = len(wait_result["failed"]) + len(wait_result["timeout"])
                smoke_failure_rate = (smoke_failed / smoke_total) if smoke_total else 0
                gate = run_artifacts.get("runnerExecutionGate") if isinstance(run_artifacts.get("runnerExecutionGate"), dict) else dict(execution_gate or {})
                gate.update({
                    "smokeExecutedCount": smoke_total,
                    "smokeFailedCount": smoke_failed,
                    "smokePassedCount": len(wait_result["completed"]),
                    "smokeFailureRate": round(smoke_failure_rate, 4),
                    "smokePassRate": round(1 - smoke_failure_rate, 4) if smoke_total else 0,
                })
                smoke_blocker = _agent_smoke_execution_blocker(
                    failure_reasons,
                    locals().get("dry_run_blocked", []),
                    smoke_total=smoke_total,
                    smoke_failed=smoke_failed,
                    timeout_count=len(wait_result["timeout"]),
                )
                gate.update({
                    "smokeExecutable": not smoke_blocker.get("block"),
                    "smokeFailureBucket": smoke_blocker.get("bucket") or "",
                    "smokeFailurePolicy": smoke_blocker.get("rule") or "",
                })
                smoke_plan = update_execution_plan_after_smoke(
                    gate.get("executionPlan") or run_artifacts.get("generatedYamlExecutionPlan") or {},
                    smoke_blocker,
                    smoke_total=smoke_total,
                    smoke_passed=len(wait_result["completed"]),
                    smoke_failed=smoke_failed,
                    timeout_count=len(wait_result["timeout"]),
                )
                gate["executionPlan"] = smoke_plan
                gate["executionReadiness"] = smoke_plan.get("readiness") or {}
                run_artifacts["generatedYamlExecutionPlan"] = smoke_plan
                if smoke_blocker.get("block"):
                    stop_reason = smoke_blocker.get("reason") or _agent_smoke_failure_bucket(failure_reasons, locals().get("dry_run_blocked", []))
                    stop_info = {
                        "enabled": True,
                        "stopFurtherExecution": True,
                        "reason": stop_reason,
                        "smokeExecutedCount": smoke_total,
                        "smokeFailedCount": smoke_failed,
                        "smokePassedCount": len(wait_result["completed"]),
                        "smokeFailureRate": round(smoke_failure_rate, 4),
                        "smokePassRate": round(1 - smoke_failure_rate, 4),
                        "rule": smoke_blocker.get("rule") or "首批冒烟不可执行时停止扩展，先修复 YAML 或 Runner 环境。",
                    }
                    gate.update(stop_info)
                    run_artifacts["runnerExecutionGate"] = gate
                    run_artifacts["runnerSmokeGate"] = stop_info
                    summary_parts.append(f"首批冒烟不可执行或不可稳定完成（失败 {smoke_failed}/{smoke_total}），已停止后续批量执行：{stop_reason}")
                elif smoke_total and gate_deferred:
                    expand_batch_limit = max(1, min(AGENT_GENERATED_RUNNER_EXPAND_BATCH_LIMIT, AGENT_GENERATED_RUNNER_EXPAND_LIMIT))
                    pending_deferred = list(gate_deferred)[:AGENT_GENERATED_RUNNER_EXPAND_LIMIT]
                    overflow_deferred = list(gate_deferred)[AGENT_GENERATED_RUNNER_EXPAND_LIMIT:]
                    expanded_batches = []
                    expanded_job_ids_all = []
                    expanded_blocked_all = []
                    expanded_dry_results_all = []
                    expanded_completed_count = 0
                    expanded_failed_count = 0
                    expanded_timeout_count = 0
                    expanded_stop_reason = ""
                    batch_index = 0
                    gate.update({
                        "stopFurtherExecution": False,
                        "expandedExecution": True,
                        "expandedBatchLimit": expand_batch_limit,
                        "expandedPlannedCount": len(pending_deferred),
                        "expandedOverflowCount": len(overflow_deferred),
                    })

                    while pending_deferred:
                        batch_index += 1
                        expand_refs = pending_deferred[:expand_batch_limit]
                        pending_deferred = pending_deferred[expand_batch_limit:]
                        phase_name = f"expanded-{batch_index}"
                        summary_parts.append(
                            f"首批冒烟已完成执行准入，继续第 {batch_index} 批剩余 executable {len(expand_refs)} 个"
                        )
                        expanded_created = _agent_create_runner_jobs_for_refs(
                            run,
                            expand_refs,
                            selected_runner_id,
                            selected_device_id,
                            selected_device_strategy,
                            runner_dry_run_enabled=runner_dry_run_enabled,
                            phase=phase_name,
                        )
                        expanded_job_ids = list(expanded_created.get("jobIds") or [])
                        expanded_blocked = list(expanded_created.get("dryRunBlocked") or [])
                        expanded_dry_results = list(expanded_created.get("dryRunResults") or [])
                        expanded_runner_dry_jobs = list(expanded_created.get("runnerDryRunJobs") or [])
                        expanded_job_ids_all.extend(expanded_job_ids)
                        expanded_blocked_all.extend(expanded_blocked)
                        expanded_dry_results_all.extend(expanded_dry_results)

                        runner_dry_run = run_artifacts.get("runnerDryRun") if isinstance(run_artifacts.get("runnerDryRun"), dict) else {}
                        runner_dry_run.setdefault("results", []).extend(expanded_dry_results)
                        runner_dry_run.setdefault("blocked", []).extend(expanded_blocked)
                        runner_dry_run.setdefault("runnerJobIds", []).extend(expanded_runner_dry_jobs)
                        runner_dry_run["expandedResults"] = expanded_dry_results_all
                        runner_dry_run["expandedBlocked"] = expanded_blocked_all
                        runner_dry_run["expandedCreatedCount"] = len(expanded_job_ids_all)
                        runner_dry_run["expandedChecked"] = len(expanded_dry_results_all)
                        runner_dry_run["checked"] = len(runner_dry_run.get("results") or [])
                        runner_dry_run["blockedCount"] = len(runner_dry_run.get("blocked") or [])
                        runner_dry_run["createdCount"] = len(run_artifacts.get("jobIds") or []) + len(expanded_job_ids)
                        runner_dry_run["ok"] = not runner_dry_run.get("blocked")
                        run_artifacts["runnerDryRun"] = runner_dry_run

                        batch_result = {
                            "batch": batch_index,
                            "phase": phase_name,
                            "plannedCount": len(expand_refs),
                            "createdCount": len(expanded_job_ids),
                            "blockedCount": len(expanded_blocked),
                            "jobIds": expanded_job_ids,
                            "blocked": expanded_blocked[:8],
                        }
                        if expanded_blocked:
                            summary_parts.append(f"第 {batch_index} 批扩展阶段 {len(expanded_blocked)} 个未下发")

                        if expanded_job_ids:
                            run_artifacts["jobIds"] = list(run_artifacts.get("jobIds") or []) + expanded_job_ids
                            _persist_agent_run_snapshot(run)
                            expanded_timeout = job_service.runner_job_wait_timeout_seconds(len(expanded_job_ids))
                            expanded_wait = job_service.wait_jobs_finished(
                                expanded_job_ids,
                                run,
                                timeout=expanded_timeout,
                                interval=5,
                                phase=f"扩展第{batch_index}批",
                            )
                            wait_result = _agent_merge_runner_wait_results(wait_result, expanded_wait)
                            run_artifacts["jobResult"].update({
                                "completedCount": len(wait_result["completed"]),
                                "failedCount": len(wait_result["failed"]),
                                "timeoutCount": len(wait_result["timeout"]),
                                "completed": wait_result["completed"],
                                "failed": wait_result["failed"],
                                "timeout": wait_result["timeout"],
                                "waitTimeoutSeconds": max(wait_timeout, expanded_timeout),
                            })
                            phase_result = {
                                "jobIds": expanded_job_ids,
                                "completedCount": len(expanded_wait.get("completed") or []),
                                "failedCount": len(expanded_wait.get("failed") or []),
                                "timeoutCount": len(expanded_wait.get("timeout") or []),
                                "waitTimeoutSeconds": expanded_timeout,
                            }
                            run_artifacts["jobResult"].setdefault("phases", {})[phase_name] = phase_result
                            batch_result.update(phase_result)
                            batch_total = phase_result["completedCount"] + phase_result["failedCount"] + phase_result["timeoutCount"]
                            batch_failed = phase_result["failedCount"] + phase_result["timeoutCount"]
                            expanded_completed_count += phase_result["completedCount"]
                            expanded_failed_count += phase_result["failedCount"]
                            expanded_timeout_count += phase_result["timeoutCount"]
                            batch_failure_rate = (batch_failed / batch_total) if batch_total else 0
                            batch_result["failureRate"] = round(batch_failure_rate, 4)
                            summary_parts.append(
                                f"第 {batch_index} 批扩展执行 {len(expanded_job_ids)} 个："
                                f"成功 {phase_result['completedCount']}，失败 {phase_result['failedCount']}，超时 {phase_result['timeoutCount']}"
                            )
                            if batch_total and batch_failure_rate > 0.5:
                                expanded_stop_reason = f"第 {batch_index} 批扩展失败率超过 50%，暂停后续扩展"
                        elif expanded_blocked:
                            expanded_stop_reason = f"第 {batch_index} 批扩展 dry-run 拦截 {len(expanded_blocked)} 个，暂停后续扩展"
                        else:
                            expanded_stop_reason = f"第 {batch_index} 批扩展未创建 Runner 任务，暂停后续扩展"

                        expanded_batches.append(batch_result)
                        gate.update({
                            "expandedBatches": expanded_batches,
                            "expandedCreatedCount": len(expanded_job_ids_all),
                            "expandedBlockedCount": len(expanded_blocked_all),
                            "expandedJobIds": expanded_job_ids_all,
                            "expandedCompletedCount": expanded_completed_count,
                            "expandedFailedCount": expanded_failed_count,
                            "expandedTimeoutCount": expanded_timeout_count,
                            "remainingDeferredCount": len(pending_deferred) + len(overflow_deferred),
                        })
                        run_artifacts["runnerExecutionGate"] = gate
                        run_artifacts["runnerSmokeGate"] = gate
                        _persist_agent_run_snapshot(run)
                        if expanded_stop_reason:
                            break

                    remaining_deferred = pending_deferred + overflow_deferred
                    expanded_total = expanded_completed_count + expanded_failed_count + expanded_timeout_count
                    expanded_failed_total = expanded_failed_count + expanded_timeout_count
                    if expanded_total:
                        gate["expandedFailureRate"] = round(expanded_failed_total / expanded_total, 4)
                    if not expanded_stop_reason and overflow_deferred:
                        expanded_stop_reason = f"达到扩展上限 {AGENT_GENERATED_RUNNER_EXPAND_LIMIT}，剩余 {len(overflow_deferred)} 个可手动继续"
                    gate.update({
                        "expandedBatches": expanded_batches,
                        "expandedBatchCount": len(expanded_batches),
                        "expandedCreatedCount": len(expanded_job_ids_all),
                        "expandedBlockedCount": len(expanded_blocked_all),
                        "expandedJobIds": expanded_job_ids_all,
                        "expandedCompletedCount": expanded_completed_count,
                        "expandedFailedCount": expanded_failed_count,
                        "expandedTimeoutCount": expanded_timeout_count,
                        "remainingDeferredCount": len(remaining_deferred),
                        "remainingDeferred": remaining_deferred[:30],
                        "stopFurtherExecution": bool(expanded_stop_reason),
                        "expandedStopReason": expanded_stop_reason,
                    })
                    expanded_plan = dict(gate.get("executionPlan") or run_artifacts.get("generatedYamlExecutionPlan") or {})
                    expanded_readiness = dict(expanded_plan.get("readiness") if isinstance(expanded_plan.get("readiness"), dict) else {})
                    expanded_readiness.update({
                        "expandedExecution": bool(expanded_job_ids_all or expanded_blocked_all),
                        "stopFurtherExecution": bool(expanded_stop_reason),
                        "remainingDeferredCount": len(remaining_deferred),
                    })
                    expanded_plan["readiness"] = expanded_readiness
                    expanded_plan["expandedResult"] = {
                        "batchCount": len(expanded_batches),
                        "created": len(expanded_job_ids_all),
                        "blocked": len(expanded_blocked_all),
                        "passed": expanded_completed_count,
                        "failed": expanded_failed_count,
                        "timeout": expanded_timeout_count,
                        "remainingDeferred": len(remaining_deferred),
                        "stopReason": expanded_stop_reason,
                    }
                    gate["executionPlan"] = expanded_plan
                    gate["executionReadiness"] = expanded_readiness
                    if expanded_stop_reason:
                        summary_parts.append(expanded_stop_reason)
                    elif expanded_job_ids_all or expanded_blocked_all:
                        summary_parts.append(
                            f"扩展阶段完成：共下发 {len(expanded_job_ids_all)} 个，"
                            f"成功 {expanded_completed_count}，失败 {expanded_failed_count}，超时 {expanded_timeout_count}，"
                            f"dry-run 拦截 {len(expanded_blocked_all)} 个"
                        )
                    run_artifacts["runnerExecutionGate"] = gate
                    run_artifacts["runnerSmokeGate"] = gate
                    run_artifacts["generatedYamlExecutionPlan"] = expanded_plan
                    failure_reasons = _agent_job_failure_reasons(
                        list(wait_result.get("failed") or []) + list(wait_result.get("timeout") or []),
                        limit=8,
                    )
                    if failure_reasons:
                        run_artifacts["jobFailureReasons"] = failure_reasons
                        run_artifacts["jobResult"]["failureReasons"] = failure_reasons
                    else:
                        run_artifacts.pop("jobFailureReasons", None)
                        run_artifacts["jobResult"].pop("failureReasons", None)
                else:
                    run_artifacts["runnerExecutionGate"] = gate
                    run_artifacts["runnerSmokeGate"] = gate

            if wait_result["timeout"]:
                call["status"] = "PARTIAL_FAILED"
                effective_wait_timeout = (run_artifacts.get("jobResult") or {}).get("waitTimeoutSeconds") or wait_timeout
                summary_parts.append(f"{len(wait_result['timeout'])} 个超时（等待上限 {effective_wait_timeout}s）")
            elif wait_result["failed"]:
                if wait_result["completed"]:
                    call["status"] = "PARTIAL_FAILED"
                else:
                    call["status"] = "FAILED"
                summary_parts.append(f"{len(wait_result['failed'])} 个失败")
                if failure_reasons:
                    top_reasons = "；".join(
                        f"{item.get('target')}: {item.get('reason')}"
                        for item in failure_reasons[:3]
                    )
                    summary_parts.append(f"主要失败原因：{top_reasons}")
                    attach_diagnosis(call, make_diagnosis(
                        "Runner 任务执行失败",
                        "任务已经下发到 Runner；设备空闲只代表可调度，实际失败来自 Runner/Midscene/ADB/YAML 执行回传。",
                        ["查看失败任务的报告和错误尾巴", "确认手机页面、安装包版本和包名", "修正 YAML 或设备状态后重试"],
                        failureReasons=failure_reasons,
                    ))

            if wait_result["completed"]:
                summary_parts.append(f"{len(wait_result['completed'])} 个成功")

        runner_dry_run = run_artifacts.get("runnerDryRun") or {}
        if runner_dry_run.get("blockedCount") and not call.get("status"):
            call["status"] = "PARTIAL_FAILED"
            summary_parts.append(f"{runner_dry_run.get('blockedCount')} 个未下发")

        job_ids = run_artifacts.get("jobIds", job_ids)
        call["status"] = call.get("status") or "SUCCESS"
        call["outputSummary"] = "，".join(summary_parts)
        call["artifactRefs"] = job_ids[:10]
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def _failed_job_id(item):
    if not isinstance(item, dict):
        return ""
    return str(item.get("jobId") or item.get("job_id") or item.get("id") or "").strip()


def _failed_job_task_name(item):
    if not isinstance(item, dict):
        return ""
    return str(
        item.get("taskName") or item.get("task_name") or
        item.get("target_task_name") or item.get("current_task_name") or
        item.get("name") or ""
    ).strip()


def _normalize_failed_execution_item(item, fallback=None):
    if not isinstance(item, dict):
        return None
    fallback = fallback if isinstance(fallback, dict) else {}
    job_id = _failed_job_id(item) or _failed_job_id(fallback)
    module = str(item.get("module") or fallback.get("module") or "").strip()
    file_name = str(item.get("file") or item.get("filename") or fallback.get("file") or "").strip()
    task_name = _failed_job_task_name(item) or _failed_job_task_name(fallback)
    error = str(item.get("error") or item.get("fail_reason") or fallback.get("error") or "").strip()
    stdout_tail = str(item.get("stdoutTail") or item.get("stdout_tail") or fallback.get("stdoutTail") or fallback.get("stdout_tail") or "").strip()
    stderr_tail = str(item.get("stderrTail") or item.get("stderr_tail") or fallback.get("stderrTail") or fallback.get("stderr_tail") or "").strip()
    reason = str(item.get("failureReason") or item.get("failure_reason") or "").strip()
    if not reason:
        reason = _agent_job_failure_reason({
            **item,
            "error": error,
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
        })
    failure_type = str(item.get("failureType") or item.get("failure_type") or "").strip()
    if not failure_type:
        failure_type = _agent_job_failure_type("\n".join([error, stdout_tail, stderr_tail]))
    return {
        "jobId": job_id,
        "status": str(item.get("status") or fallback.get("status") or "failed").strip() or "failed",
        "module": module,
        "file": file_name,
        "taskName": task_name,
        "reportUrl": item.get("reportUrl") or item.get("report_url") or fallback.get("reportUrl") or fallback.get("report_url") or "",
        "localPath": item.get("localPath") or item.get("local_report_path") or item.get("localReportPath") or fallback.get("localPath") or "",
        "error": error,
        "stdoutTail": stdout_tail[-1200:],
        "stderrTail": stderr_tail[-1600:],
        "failureReason": reason,
        "failureType": failure_type,
    }


def _agent_failed_execution_items(run):
    """Return the single source of truth for failed Runner execution items."""
    artifacts = (run or {}).get("artifacts") or {}
    raw_items = []
    report = artifacts.get("report") if isinstance(artifacts.get("report"), dict) else {}
    if report:
        raw_items.extend(report.get("failedJobs") or [])
        raw_items.extend(report.get("timeoutJobs") or [])
    job_result = artifacts.get("jobResult") if isinstance(artifacts.get("jobResult"), dict) else {}
    if job_result:
        raw_items.extend(job_result.get("failed") or [])
        raw_items.extend(job_result.get("timeout") or [])

    # If report collection did not run yet, fall back to persisted job records.
    try:
        from task_server.services import job_service
        job_ids = [str(jid) for jid in (artifacts.get("jobIds") or []) if str(jid or "").strip()]
        if job_ids:
            jobs = job_service.load_jobs()
            known = {_failed_job_id(item) for item in raw_items if isinstance(item, dict)}
            for jid in job_ids:
                if jid in known:
                    continue
                job = next((j for j in jobs if j.get("job_id") == jid or j.get("jobId") == jid), None)
                if not job:
                    continue
                if str(job.get("status") or "").lower() in ("failed", "error", "timeout", "cancelled"):
                    raw_items.append({
                        **job,
                        "jobId": jid,
                        "taskName": job.get("target_task_name") or job.get("current_task_name") or "",
                        "stderrTail": job.get("stderr_tail") or "",
                        "stdoutTail": job.get("stdout_tail") or "",
                    })
    except Exception:
        pass

    failure_analysis = artifacts.get("failureAnalysis") if isinstance(artifacts.get("failureAnalysis"), dict) else {}
    if failure_analysis and (_failed_job_id(failure_analysis) or failure_analysis.get("file") or failure_analysis.get("taskName")):
        raw_items.append(failure_analysis)

    normalized = []
    by_key = {}
    for item in raw_items:
        norm = _normalize_failed_execution_item(item)
        if not norm:
            continue
        key = norm.get("jobId") or f"{norm.get('module')}::{norm.get('file')}::{norm.get('taskName')}"
        if not key or key in by_key:
            continue
        by_key[key] = norm
        normalized.append(norm)
    return normalized


def _agent_persist_failed_execution_items(run):
    items = _agent_failed_execution_items(run)
    run.setdefault("artifacts", {})["failedExecutionItems"] = items
    return items


def _tool_collect_report(run):
    """收集执行报告 - 基于已完成的job收集真实报告。"""
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "read_report",
        "category": "READ",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {},
    }
    try:
        from task_server.services import job_service
        artifacts = run.get("artifacts") or {}
        normalize_yaml_refs(run)
        job_ids = artifacts.get("jobIds") or []
        job_result = artifacts.get("jobResult") or {}
        sonic_result_id = artifacts.get("sonicResultId")

        # 如果没有任何job也没有sonic result，跳过
        if not job_ids and not sonic_result_id:
            call["status"] = "SKIPPED"
            call["outputSummary"] = "无执行任务，跳过报告收集"
            call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            call["durationMs"] = _compute_duration(call)
            _log_tool_call(call, run.get("runId", ""))
            return call

        execution_reports = []
        yaml_execution_refs = []
        job_statuses = []
        failed_jobs = []
        success_jobs = []
        running_jobs = []
        timeout_jobs = []
        errors = []

        # 1. 从本地 job 收集报告
        if job_ids:
            with JOB_LOCK:
                jobs = job_service.load_jobs()

            for jid in job_ids:
                job = next((j for j in jobs if j.get("job_id") == jid), None)
                if not job:
                    continue

                status = (job.get("status") or "unknown").lower()
                job_entry = {
                        "jobId": jid,
                        "status": status,
                        "module": job.get("module", ""),
                        "file": job.get("file", ""),
                        "taskName": job.get("target_task_name") or job.get("current_task_name", ""),
                        "reportUrl": job.get("report_url") or job.get("reportUrl", ""),
                    }
                job_statuses.append(job_entry)

                if status == "success":
                    # 收集报告URL和路径
                    report_url = job.get("report_url") or job.get("reportUrl", "")
                    local_path = job.get("local_report_path") or job.get("localReportPath", "")
                    yaml_execution_refs.append({
                        "jobId": jid,
                        "module": job.get("module", ""),
                        "file": job.get("file", ""),
                        "taskName": job_entry.get("taskName", ""),
                        "status": "success",
                    })
                    report_entry = {
                        "jobId": jid,
                        "module": job.get("module", ""),
                        "file": job.get("file", ""),
                        "taskName": job_entry.get("taskName", ""),
                        "reportUrl": report_url,
                        "localPath": local_path,
                        "status": "success",
                    }
                    if report_url or str(local_path).lower().endswith((".html", ".htm")):
                        execution_reports.append(report_entry)
                    success_jobs.append(job_entry)
                elif status in ("failed", "error", "timeout", "cancelled"):
                    # 收集失败信息
                    fail_entry = {
                        **job_entry,
                        "error": job.get("error") or job.get("fail_reason", ""),
                        "stderrTail": (job.get("stderr") or job.get("stderr_tail") or "")[-500:],
                        "stdoutTail": (job.get("stdout") or job.get("stdout_tail") or "")[-300:],
                    }
                    fail_entry["failureReason"] = _agent_job_failure_reason(fail_entry)
                    fail_entry["failureType"] = _agent_job_failure_type("\n".join([
                        fail_entry.get("error", ""),
                        fail_entry.get("stderrTail", ""),
                        fail_entry.get("stdoutTail", ""),
                    ]))
                    failed_jobs.append(fail_entry)
                    if status == "timeout":
                        timeout_jobs.append(fail_entry)
                    if fail_entry.get("error"):
                        errors.append(fail_entry.get("error"))
                elif status in ("running", "pending", "queued", "created", "waiting", "assigned"):
                    running_jobs.append(job_entry)

        # 2. 从 Sonic 收集结果
        sonic_results = []
        if sonic_result_id:
            try:
                from task_server.services import sonic_service
                for _ in range(12):
                    resp = sonic_service.sonic_request("GET", "/resultDetail", params={"id": sonic_result_id}, timeout=15)
                    detail = sonic_service.sonic_response_data(resp)
                    if isinstance(detail, dict):
                        status_val = str(detail.get("status") or detail.get("state", "")).upper()
                        if status_val in ("PASS", "FAIL", "WARNING", "2", "3", "4"):
                            sonic_results.append(detail)
                            break
                        elif status_val in ("RUNNING", "PENDING", "0", "1"):
                            time.sleep(5)
                            continue
                        else:
                            sonic_results.append(detail)
                            break
                    break
            except Exception:
                pass

        # 3. 也从 jobResult（wait_jobs_finished的结果）补充
        success_job_ids = {item.get("jobId") for item in success_jobs if item.get("jobId")}
        if job_result:
            for fj in (job_result.get("failed") or []):
                fj_id = fj.get("job_id") or fj.get("jobId")
                if fj_id in success_job_ids:
                    continue
                if not any(f.get("jobId") == fj_id for f in failed_jobs):
                    failed_jobs.append({
                        "jobId": fj_id or "",
                        "status": fj.get("status", "failed"),
                        "module": fj.get("module", ""),
                        "file": fj.get("file", ""),
                        "taskName": fj.get("taskName") or fj.get("task_name") or fj.get("target_task_name") or fj.get("current_task_name") or "",
                        "reportUrl": fj.get("report_url") or fj.get("reportUrl", ""),
                        "stdoutTail": fj.get("stdout_tail") or "",
                        "stderrTail": fj.get("stderr_tail") or "",
                        "error": fj.get("error", ""),
                        "failureReason": _agent_job_failure_reason(fj),
                        "failureType": _agent_job_failure_type("\n".join([
                            str(fj.get("error") or ""),
                            str(fj.get("stderr_tail") or ""),
                            str(fj.get("stdout_tail") or ""),
                        ])),
                    })
                    if fj.get("error"):
                        errors.append(fj.get("error"))
            for tj in (job_result.get("timeout") or []):
                tj_id = tj.get("job_id") or tj.get("jobId")
                if tj_id in success_job_ids:
                    continue
                if not any(f.get("jobId") == tj_id for f in failed_jobs):
                    timeout_entry = {
                        "jobId": tj_id or "",
                        "status": "timeout",
                        "module": tj.get("module", ""),
                        "file": tj.get("file", ""),
                        "taskName": tj.get("taskName") or tj.get("task_name") or tj.get("target_task_name") or tj.get("current_task_name") or "",
                        "reportUrl": tj.get("report_url") or tj.get("reportUrl", ""),
                        "stdoutTail": tj.get("stdout_tail") or "",
                        "stderrTail": tj.get("stderr_tail") or "",
                        "error": tj.get("error") or "Runner 执行等待超时，报告尚未回传",
                    }
                    timeout_entry["failureReason"] = _agent_job_failure_reason(timeout_entry)
                    timeout_entry["failureType"] = _agent_job_failure_type("\n".join([
                        timeout_entry.get("error", ""),
                        timeout_entry.get("stderrTail", ""),
                        timeout_entry.get("stdoutTail", ""),
                    ]))
                    failed_jobs.append(timeout_entry)
                    timeout_jobs.append(timeout_entry)
                    errors.append(timeout_entry["error"])
        if success_job_ids:
            failed_jobs = [item for item in failed_jobs if item.get("jobId") not in success_job_ids]
            timeout_jobs = [item for item in timeout_jobs if item.get("jobId") not in success_job_ids]
        for sf in ((artifacts.get("sonicSync") or {}).get("failed") or []):
            errors.append(sf.get("error") or "")

        # 终态优先：旧快照里可能同时存在 running + timeout/failed。
        # 这里按 jobId 归一，避免最终产物出现“失败、超时、仍在运行”的矛盾状态。
        terminal_by_id = {}
        for item in failed_jobs:
            if item.get("jobId"):
                terminal_by_id[item.get("jobId")] = item
        for item in success_jobs:
            if item.get("jobId"):
                terminal_by_id[item.get("jobId")] = item
        if terminal_by_id:
            running_jobs = [item for item in running_jobs if item.get("jobId") not in terminal_by_id]
            normalized_statuses = []
            seen_status_ids = set()
            for item in job_statuses:
                jid = item.get("jobId")
                if jid in terminal_by_id:
                    replacement = {**item, **terminal_by_id[jid]}
                    normalized_statuses.append(replacement)
                    seen_status_ids.add(jid)
                else:
                    normalized_statuses.append(item)
                    if jid:
                        seen_status_ids.add(jid)
            for jid, item in terminal_by_id.items():
                if jid not in seen_status_ids:
                    normalized_statuses.append(item)
            job_statuses = normalized_statuses

        # 4. 生成摘要
        summary_parts = []
        if execution_reports:
            summary_parts.append(f"{len(execution_reports)} 个执行报告")
        elif success_jobs:
            summary_parts.append("0 个执行报告链接")
        if failed_jobs:
            summary_parts.append(f"{len(failed_jobs)} 个失败")
        if success_jobs:
            summary_parts.append(f"{len(success_jobs)} 个成功")
        if timeout_jobs:
            summary_parts.append(f"{len(timeout_jobs)} 个超时")
        if running_jobs:
            summary_parts.append(f"{len(running_jobs)} 个仍在运行")
        if sonic_results:
            summary_parts.append(f"Sonic 结果 {len(sonic_results)} 条")
        summary = "，".join(summary_parts) if summary_parts else "无报告数据"
        report_status = "complete"
        if failed_jobs or timeout_jobs:
            report_status = "failed"
        elif running_jobs:
            report_status = "waiting"
        elif job_ids and not job_statuses and not sonic_results:
            report_status = "missing"

        # 5. 统一写入 artifacts.report
        artifacts["report"] = {
            "executionMode": str(run.get("executionMode") or run.get("execution_mode") or "RUNNER_JOB").strip().upper(),
            "status": report_status,
            "reports": execution_reports,
            "executionReports": execution_reports,
            "yamlExecutionRefs": yaml_execution_refs,
            "jobStatuses": job_statuses,
            "failedJobs": failed_jobs,
            "successJobs": success_jobs,
            "runningJobs": running_jobs,
            "timeoutJobs": timeout_jobs,
            "sonicResults": sonic_results,
            "errors": [e for e in errors if e],
            "summary": summary,
        }
        artifacts["failedExecutionItems"] = _agent_failed_execution_items(run)
        run["artifacts"] = artifacts

        if report_status in ("failed", "waiting", "missing"):
            call["status"] = "PARTIAL_FAILED"
            if report_status == "waiting":
                call["error"] = f"{len(running_jobs)} 个 Runner 任务仍在运行，最终报告尚未生成"
                attach_diagnosis(call, make_diagnosis(
                    "Runner 任务仍在运行，报告尚未生成",
                    "Agent 不能把本次执行标记为真正完成，否则会误判回归结果。",
                    ["等待 Runner 回传结果后刷新", "检查 Windows/Mac Runner 是否卡住", "查看执行中心中的 job 详情"],
                    runningJobs=running_jobs[:5],
                ))
            elif report_status == "missing":
                call["error"] = "Runner 任务已创建，但没有收集到报告或 Sonic 结果"
                attach_diagnosis(call, make_diagnosis(
                    "执行报告缺失",
                    "无法确认用例执行结果，不能作为有效回归结论。",
                    ["检查 Runner 报告上传", "查看 job stdout/stderr", "必要时重跑该用例"],
                ))
            else:
                call["error"] = "存在失败或超时的 Runner 任务"
                attach_diagnosis(call, make_diagnosis(
                    "Runner 任务失败或超时",
                    "本次回归没有全部通过，需要先查看失败报告或 Runner 日志。",
                    ["打开失败 job 详情", "检查 Midscene 报告", "修复脚本后重跑"],
                    failedJobs=failed_jobs[:5],
                    timeoutJobs=timeout_jobs[:5],
                ))
        else:
            call["status"] = "SUCCESS"
        call["outputSummary"] = f"收集到 {len(execution_reports)} 个执行报告，{len(job_statuses)} 个任务状态"
        if failed_jobs:
            call["outputSummary"] += f"（{len(failed_jobs)} 个失败）"
        if running_jobs:
            call["outputSummary"] += f"（{len(running_jobs)} 个仍在运行）"
        if timeout_jobs:
            call["outputSummary"] += f"（{len(timeout_jobs)} 个超时）"

    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)
        call["outputSummary"] = f"报告收集异常: {str(e)[:200]}"
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def _tool_analyze_failure(run):
    """分析失败原因（基于 artifacts.report.failedJobs 和 sonicSync.failed）。"""
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "analyze_failure",
        "category": "AI",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {"target": run.get("target", "")},
    }
    try:
        artifacts = run.get("artifacts") or {}
        failed_jobs = _agent_persist_failed_execution_items(run)
        sonic_sync = artifacts.get("sonicSync") or {}
        sonic_failed = sonic_sync.get("failed") or []
        precheck_diagnosis = ((artifacts.get("executionPrecheck") or {}).get("diagnosis")
                              or artifacts.get("diagnosis") or {})

        # 判断是否需要分析
        has_job_failures = len(failed_jobs) > 0
        has_sonic_failures = len(sonic_failed) > 0
        has_precheck_failure = bool(precheck_diagnosis and precheck_diagnosis.get("rootCause"))

        if not has_job_failures and not has_sonic_failures and not has_precheck_failure:
            # 全部成功
            artifacts.setdefault("failureAnalysis", {})
            artifacts["failureAnalysis"] = {
                "failureType": "NONE",
                "summary": "无失败任务，全部执行成功",
                "conclusion": "所有用例执行通过",
            }
            call["status"] = "SKIPPED"
            call["outputSummary"] = "无失败任务，跳过分析"
            call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            call["durationMs"] = _compute_duration(call)
            _log_tool_call(call, run.get("runId", ""))
            return call

        # === 有失败需要分析 ===
        failure_context = ""
        failure_type = "UNKNOWN"

        if has_sonic_failures and not has_job_failures:
            # Sonic同步失败但没有执行失败（说明是环境/配置问题）
            failure_type = "ENV_ISSUE"
            failure_context = f"Sonic 同步失败 {len(sonic_failed)} 条:\n"
            for sf in sonic_failed[:5]:
                failure_context += f"- {sf.get('module', '')}/{sf.get('file', '')}：{sf.get('error', '')}\n"
        elif has_precheck_failure:
            failure_type = "ENV_ISSUE"
            failure_context = (
                f"执行前体检失败：{precheck_diagnosis.get('rootCause', '')}\n"
                f"影响：{precheck_diagnosis.get('impact', '')}\n"
                f"建议：{'；'.join(precheck_diagnosis.get('nextActions') or [])}\n"
            )
        elif has_job_failures:
            # 有执行失败
            failure_type = "SCRIPT_ISSUE"
            failure_context = f"执行失败 {len(failed_jobs)} 个任务:\n"
            for fj in failed_jobs[:12]:
                failure_context += f"- {fj.get('module', '')}/{fj.get('file', '')} ({fj.get('status', '')})：{fj.get('error', '')}\n"
                stderr = fj.get("stderrTail") or fj.get("stderr_tail") or ""
                if stderr:
                    failure_context += f"  stderr: {stderr[:200]}\n"

        # 构建本地分析结果
        analysis = {
            "failureType": failure_type,
            "summary": failure_context[:500],
            "conclusion": "",
            "recommendation": "",
        }

        # 尝试调用 AI Gateway 分析
        if _ai_gateway_available():
            try:
                result = _ai_gateway_post("/ai/analyze-failure", {
                    "failureType": failure_type,
                    "context": failure_context[:2000],
                    "failedJobs": [
                        {
                            "jobId": fj.get("jobId", ""),
                            "taskName": fj.get("taskName", ""),
                            "file": fj.get("file", ""),
                            "error": fj.get("error", ""),
                            "failureReason": fj.get("failureReason", ""),
                        }
                        for fj in failed_jobs[:12]
                    ],
                }, timeout=30)
                if isinstance(result, dict):
                    analysis["conclusion"] = result.get("conclusion") or result.get("analysis", "")
                    analysis["recommendation"] = result.get("recommendation") or result.get("suggestion", "")
                    # AI 可能返回更准确的失败类型
                    if result.get("failureType"):
                        analysis["failureType"] = result["failureType"]
            except Exception:
                analysis["conclusion"] = f"AI分析超时，失败类型: {failure_type}"
                analysis["recommendation"] = "请检查失败日志手动分析"
        else:
            analysis["conclusion"] = f"AI分析不可用，失败类型: {failure_type}"
            analysis["recommendation"] = "请检查失败日志手动分析"

        artifacts["failureAnalysis"] = analysis
        artifacts["failedExecutionItems"] = failed_jobs
        run["artifacts"] = artifacts

        call["status"] = "SUCCESS"
        call["failedTaskCount"] = len(failed_jobs)
        call["failedTasks"] = [
            {"jobId": item.get("jobId"), "taskName": item.get("taskName"), "file": item.get("file"), "reason": item.get("failureReason")}
            for item in failed_jobs[:20]
        ]
        call["outputSummary"] = f"分析完成: {failure_type}，{len(failed_jobs)} 个任务失败，{len(sonic_failed)} 个同步失败"

    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)[:200]
        call["outputSummary"] = f"分析失败: {str(e)[:100]}"
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def _tool_generate_repair(run):
    """只对 SCRIPT_ISSUE 类型生成可追溯的 YAML 修复草稿。"""
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "generate_repair_draft",
        "category": "AI",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {},
    }
    try:
        fa = (run.get("artifacts") or {}).get("failureAnalysis") or {}
        ft = str(fa.get("failureType", "UNKNOWN")).upper()
        if ft == "PRODUCT_BUG":
            call["status"] = "SKIPPED"
            call["outputSummary"] = "PRODUCT_BUG 不生成 YAML 修复，仅生成缺陷草稿"
            return call
        if ft == "ENV_ISSUE":
            call["status"] = "SKIPPED"
            call["outputSummary"] = "ENV_ISSUE 不自动修复，请检查环境"
            return call
        if ft == "UNKNOWN":
            call["status"] = "SKIPPED"
            call["outputSummary"] = "未知失败类型，进入人工复核"
            return call
        artifacts = run.setdefault("artifacts", {})
        failed_jobs = _agent_persist_failed_execution_items(run)
        if not failed_jobs:
            call["status"] = "SKIPPED"
            call["outputSummary"] = "没有失败任务，跳过修复草稿"
            return call

        max_items = max(1, safe_int(os.getenv("MIDSCENE_AGENT_REPAIR_MAX_ITEMS"), 20))
        repair_targets = failed_jobs[:max_items]
        all_failure_context = "\n".join(
            f"{idx + 1}. {item.get('taskName') or item.get('file') or item.get('jobId')} | "
            f"{item.get('file') or ''} | {item.get('failureReason') or item.get('error') or ''}"
            for idx, item in enumerate(failed_jobs[:30])
        )
        ai_available = _ai_gateway_available()
        ai_timeout = max(20, safe_int(os.getenv("MIDSCENE_AGENT_REPAIR_TIMEOUT_SECONDS"), 90))
        saved_drafts = []
        summary_items = []
        ai_attempted_count = 0
        ai_used_count = 0
        validation_passed_count = 0
        blocked_count = 0

        try:
            from task_server.services import repair_service
        except Exception:
            repair_service = None

        for index, target_job in enumerate(repair_targets, start=1):
            module = str(target_job.get("module") or fa.get("module") or "").strip()
            file_name = clean_filename(target_job.get("file") or fa.get("file") or "")
            task_name = str(target_job.get("taskName") or fa.get("taskName") or fa.get("task_name") or "").strip()
            original_yaml = ""
            if module and file_name:
                try:
                    original_yaml = read_text_file(safe_join(TASK_DIR, module, file_name), default="")
                except Exception:
                    original_yaml = ""
            evidence_parts = [
                f"失败类型：{ft}",
                f"失败序号：{index}/{len(failed_jobs)}",
                f"Agent 目标：{run.get('target', '')}",
                f"失败用例：{task_name or file_name}",
                f"失败原因：{target_job.get('failureReason') or fa.get('summary') or target_job.get('error') or ''}",
                f"Runner 错误：{target_job.get('error') or ''}",
                f"stderr：{target_job.get('stderrTail') or target_job.get('stderr_tail') or ''}",
                f"stdout：{target_job.get('stdoutTail') or target_job.get('stdout_tail') or ''}",
                f"本次全部失败摘要：\n{all_failure_context}",
            ]
            evidence = "\n".join(part for part in evidence_parts if str(part).strip() and not str(part).endswith("："))
            draft_id = unique_millis_id("repair")
            draft = {
                "draftId": draft_id,
                "jobId": target_job.get("jobId") or "",
                "module": module,
                "file": file_name,
                "taskName": task_name,
                "type": ft,
                "failureType": ft,
                "riskLevel": "medium",
                "analysis": fa.get("conclusion") or fa.get("summary") or "根据失败日志生成修复草稿",
                "suggestion": fa.get("suggestion") or fa.get("recommendation") or "建议修复定位器、等待条件或断言范围",
                "originalYaml": original_yaml,
                "fixedYaml": "",
                "draftYaml": "",
                "evidence": evidence[:5000],
                "repairSource": "not_started",
                "batchIndex": index,
                "batchTotal": len(failed_jobs),
            }
            item_summary = {
                "draftId": draft_id,
                "targetJobId": draft.get("jobId", ""),
                "targetTaskName": task_name,
                "module": module,
                "file": file_name,
                "failureReason": target_job.get("failureReason") or target_job.get("error") or "",
                "aiAttempted": False,
                "aiUsed": False,
                "yamlValidation": {},
                "changes": [],
                "repairSource": "not_started",
            }

            if not original_yaml.strip():
                blocked_count += 1
                item_summary["blockedReason"] = "missing_original_yaml"
                draft["analysis"] = "未找到原始 YAML；仅保留失败证据，无法生成可应用修复。"
                draft["repairSource"] = "diagnosis_only"
            elif ai_available:
                ai_attempted_count += 1
                item_summary["aiAttempted"] = True
                resp = None
                try:
                    resp = _ai_gateway_post("/ai/optimize-yaml", {
                        "yaml": original_yaml,
                        "target": run.get("target", ""),
                        "requirement": run.get("target", ""),
                        "taskName": task_name,
                        "failureAnalysis": evidence,
                        "issues": evidence,
                        "allFailedJobs": failed_jobs[:30],
                    }, timeout=ai_timeout)
                except Exception as e:
                    resp = {"error": str(e)[:300]}
                fixed_yaml = ""
                if isinstance(resp, dict):
                    fixed_yaml = str(resp.get("fixedYaml") or resp.get("fixed_yaml") or resp.get("optimizedYaml") or resp.get("yaml") or "").strip()
                    if resp.get("changes"):
                        changes = resp.get("changes")
                        item_summary["changes"] = changes if isinstance(changes, list) else [str(changes)]
                    if resp.get("diff") or resp.get("diff_summary"):
                        draft["diff"] = resp.get("diff") or resp.get("diff_summary")
                if fixed_yaml:
                    validation = (resp or {}).get("validation") if isinstance(resp, dict) else {}
                    if not isinstance(validation, dict) or "ok" not in validation:
                        validation = validate_midscene_yaml_executability(fixed_yaml)
                    draft["fixedYaml"] = fixed_yaml[:200000]
                    draft["fixed_yaml"] = draft["fixedYaml"]
                    draft["draftYaml"] = draft["fixedYaml"][:5000]
                    draft["validation"] = validation
                    draft["repairSource"] = "ai_gateway"
                    draft["status"] = "WAIT_CONFIRM"
                    item_summary["aiUsed"] = True
                    item_summary["yamlValidation"] = validation
                    item_summary["taskCount"] = validation.get("taskCount")
                    ai_used_count += 1
                    if validation.get("ok"):
                        validation_passed_count += 1
                    else:
                        item_summary["blockedReason"] = "yaml_validation_failed"
                else:
                    blocked_count += 1
                    item_summary["blockedReason"] = "ai_no_yaml"
                    if isinstance(resp, dict) and resp.get("error"):
                        item_summary["aiError"] = resp.get("error")
                        draft["aiError"] = resp.get("error")
                    draft["repairSource"] = "diagnosis_only"
            else:
                blocked_count += 1
                item_summary["blockedReason"] = "ai_gateway_unavailable"
                draft["repairSource"] = "diagnosis_only"

            item_summary["repairSource"] = draft.get("repairSource")
            try:
                saved = repair_service.upsert_repair_draft(draft) if repair_service else draft
            except Exception:
                saved = draft
            saved_drafts.append(saved)
            summary_items.append(item_summary)

        existing = [
            item for item in (artifacts.get("repairDrafts") or [])
            if isinstance(item, dict)
        ]
        new_ids = {item.get("draftId") or item.get("draft_id") for item in saved_drafts if isinstance(item, dict)}
        artifacts["repairDrafts"] = saved_drafts + [
            item for item in existing
            if (item.get("draftId") or item.get("draft_id")) not in new_ids
        ][:max(0, 30 - len(saved_drafts))]
        if saved_drafts:
            artifacts["repairDraft"] = saved_drafts[0]
        repair_summary = {
            "draftIds": [item.get("draftId") or item.get("draft_id") for item in saved_drafts if isinstance(item, dict)],
            "draftCount": len(saved_drafts),
            "failedTaskCount": len(failed_jobs),
            "repairTargetCount": len(repair_targets),
            "repairScope": "all_failed_tasks" if len(repair_targets) == len(failed_jobs) else "partial_failed_tasks",
            "skippedFailedTaskCount": max(0, len(failed_jobs) - len(repair_targets)),
            "blockedCount": blocked_count,
            "aiAttempted": ai_attempted_count > 0,
            "aiAttemptedCount": ai_attempted_count,
            "aiUsed": ai_used_count > 0,
            "aiUsedCount": ai_used_count,
            "validationPassedCount": validation_passed_count,
            "failureType": ft,
            "evidenceSources": ["失败类型", "Agent 目标", "Runner 错误", "stdout/stderr 尾部", "原始 YAML", "整批失败摘要"],
            "items": summary_items,
            "targetTasks": [
                item.get("targetTaskName") or item.get("file") or item.get("targetJobId")
                for item in summary_items
            ],
            "yamlValidation": summary_items[0].get("yamlValidation") if summary_items else {},
            "changes": summary_items[0].get("changes") if summary_items else [],
        }
        artifacts["repairSummary"] = repair_summary
        call["repairDraftIds"] = repair_summary["draftIds"]
        call["repairDraftId"] = repair_summary["draftIds"][0] if repair_summary["draftIds"] else ""
        call["repairSource"] = "batch"
        call["failedTaskCount"] = len(failed_jobs)
        call["repairTargetCount"] = len(repair_targets)
        call["aiAttempted"] = repair_summary.get("aiAttempted")
        call["aiUsed"] = repair_summary.get("aiUsed")
        call["aiAttemptedCount"] = ai_attempted_count
        call["aiUsedCount"] = ai_used_count
        call["yamlValidation"] = repair_summary.get("yamlValidation")
        call["targetTaskName"] = "、".join([str(x) for x in repair_summary["targetTasks"][:3] if x])
        call["artifactRefs"] = ["repair"]
        if ai_used_count:
            call["status"] = "SUCCESS" if validation_passed_count == ai_used_count and blocked_count == 0 else "PARTIAL_FAILED"
            call["outputSummary"] = (
                f"AI 已生成 {ai_used_count} 条可应用修复草稿，覆盖 {len(repair_targets)}/{len(failed_jobs)} 条失败任务"
            )
            if validation_passed_count != ai_used_count:
                call["error"] = f"{ai_used_count - validation_passed_count} 条修复 YAML 未通过校验"
        elif saved_drafts:
            call["status"] = "SKIPPED"
            call["outputSummary"] = f"未生成可应用 YAML，已保存 {len(saved_drafts)} 条诊断草稿，覆盖 {len(repair_targets)}/{len(failed_jobs)} 条失败任务"
        else:
            call["status"] = "SKIPPED"
            call["outputSummary"] = "没有生成修复草稿"
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def _tool_generate_bug_draft(run):
    """对 PRODUCT_BUG 生成飞书缺陷草稿。"""
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "generate_bug_draft",
        "category": "AI",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {},
    }
    try:
        fa = (run.get("artifacts") or {}).get("failureAnalysis") or {}
        ft = str(fa.get("failureType", "UNKNOWN")).upper()
        if ft != "PRODUCT_BUG":
            call["status"] = "SKIPPED"
            call["outputSummary"] = f"非 PRODUCT_BUG（{ft}），跳过缺陷草稿"
            return call
        draft = {
            "type": "PRODUCT_BUG",
            "title": f"[{run.get('appName', '')}] {run.get('target', '')[:50]}",
            "description": f"失败分析：{fa.get('summary', '')[:300]}",
            "status": "DRAFT",
        }
        if _ai_gateway_available():
            try:
                resp = _ai_gateway_post("/ai/generate-bug", {
                    "failureType": "PRODUCT_BUG",
                    "summary": fa.get("summary", ""),
                    "jobId": fa.get("jobId", ""),
                })
                if isinstance(resp, dict):
                    draft["title"] = resp.get("title", draft["title"])
                    draft["description"] = resp.get("description", draft["description"])
                    draft["severity"] = resp.get("severity", "medium")
                call["status"] = "SUCCESS"
                call["outputSummary"] = "缺陷草稿生成完成"
            except Exception as e:
                call["status"] = "SKIPPED"
                call["outputSummary"] = f"AI Gateway 缺陷草稿生成失败：{str(e)[:200]}"
        else:
            call["status"] = "SKIPPED"
            call["outputSummary"] = "AI Gateway 不可用，使用本地缺陷草稿"
        run.setdefault("artifacts", {})["bugDraft"] = draft
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def _agent_repair_drafts_for_rerun(artifacts):
    """Return de-duplicated repair drafts attached to the current Agent run."""
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    candidates = []
    if isinstance(artifacts.get("repairDrafts"), list):
        candidates.extend(item for item in artifacts.get("repairDrafts") if isinstance(item, dict))
    if isinstance(artifacts.get("repairDraft"), dict):
        candidates.append(artifacts.get("repairDraft"))
    result = []
    seen = set()
    for item in candidates:
        key = (
            item.get("draftId") or item.get("draft_id") or
            item.get("jobId") or item.get("job_id") or
            f"{item.get('module')}::{item.get('file')}::{item.get('taskName') or item.get('task_name')}"
        )
        key = str(key or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _agent_repair_draft_fixed_yaml(draft):
    if not isinstance(draft, dict):
        return ""
    for key in ("fixedYaml", "fixed_yaml", "optimizedYaml", "optimized_yaml", "yaml", "content"):
        value = draft.get(key)
        if isinstance(value, str) and value.strip() and value.strip() != str(draft.get("originalYaml") or "").strip():
            return value.strip()
    return ""


def _agent_repair_draft_matches_failed_item(draft, item):
    if not isinstance(draft, dict) or not isinstance(item, dict):
        return False
    draft_job_id = str(draft.get("jobId") or draft.get("job_id") or "").strip()
    item_job_id = _failed_job_id(item)
    if draft_job_id and item_job_id and draft_job_id == item_job_id:
        return True
    draft_file = str(draft.get("file") or "").strip()
    item_file = str(item.get("file") or "").strip()
    draft_task = str(draft.get("taskName") or draft.get("task_name") or "").strip()
    item_task = _failed_job_task_name(item)
    if draft_file and item_file and draft_file == item_file:
        return not draft_task or not item_task or draft_task == item_task
    if draft_task and item_task and draft_task == item_task:
        return True
    return False


def _agent_find_repair_source(draft, failed_items, jobs):
    failed_items = [item for item in (failed_items or []) if isinstance(item, dict)]
    jobs = [item for item in (jobs or []) if isinstance(item, dict)]
    draft_job_id = str(draft.get("jobId") or draft.get("job_id") or "").strip() if isinstance(draft, dict) else ""
    source_item = None
    if draft_job_id:
        source_item = next((item for item in failed_items if _failed_job_id(item) == draft_job_id), None)
    if source_item is None:
        source_item = next((item for item in failed_items if _agent_repair_draft_matches_failed_item(draft, item)), None)
    source_job_id = draft_job_id or _failed_job_id(source_item or {})
    source_job = None
    if source_job_id:
        source_job = next((job for job in jobs if job.get("job_id") == source_job_id or job.get("jobId") == source_job_id), None)
    if source_job is None and source_item:
        source_job = next((
            job for job in jobs
            if str(job.get("module") or "") == str(source_item.get("module") or "")
            and str(job.get("file") or "") == str(source_item.get("file") or "")
        ), None)
    return source_item or {}, source_job or {}


def _agent_repair_yaml_task_names(yaml_text):
    if pyyaml is None:
        return []
    try:
        _platform, tasks = extract_midscene_tasks(pyyaml.safe_load(str(yaml_text or "")))
    except Exception:
        return []
    return [str(task.get("name") or "").strip() for task in tasks if isinstance(task, dict) and str(task.get("name") or "").strip()]


def _agent_prepare_repair_rerun_targets(run, failed_items, jobs):
    """Materialize usable repair drafts as temporary YAML files for safe rerun."""
    artifacts = run.setdefault("artifacts", {})
    repair_summary = artifacts.get("repairSummary") if isinstance(artifacts.get("repairSummary"), dict) else {}
    drafts = _agent_repair_drafts_for_rerun(artifacts)
    has_repair_drafts = bool(drafts) or safe_int(repair_summary.get("draftCount"), 0) > 0
    if not has_repair_drafts:
        return {"hasRepairDrafts": False, "targets": [], "skipped": []}

    run_slug = clean_id(str(run.get("runId") or unique_millis_id("agent")), "agent")[:48]
    module = _safe_agent_slug(f"AI_Agent_修复重跑_{run_slug}", "AI_Agent_修复重跑")
    module_dir = safe_join(TASK_DIR, module)
    os.makedirs(module_dir, exist_ok=True)
    targets = []
    skipped = []
    used_keys = set()

    for index, draft in enumerate(drafts, start=1):
        draft_id = str(draft.get("draftId") or draft.get("draft_id") or f"draft-{index}").strip()
        source_item, source_job = _agent_find_repair_source(draft, failed_items, jobs)
        if failed_items and not source_item:
            skipped.append({
                "draftId": draft_id,
                "jobId": draft.get("jobId") or draft.get("job_id") or "",
                "taskName": draft.get("taskName") or draft.get("task_name") or draft.get("file") or "",
                "status": "not_matched",
                "reason": "修复草稿未匹配到本次失败任务，未参与重跑",
            })
            continue
        source_job_id = str(
            draft.get("jobId") or draft.get("job_id") or
            source_item.get("jobId") or source_item.get("job_id") or
            source_job.get("job_id") or source_job.get("jobId") or ""
        ).strip()
        key = source_job_id or f"{draft.get('file')}::{draft.get('taskName') or draft.get('task_name')}::{draft_id}"
        if key in used_keys:
            continue
        used_keys.add(key)
        fixed_yaml = _agent_repair_draft_fixed_yaml(draft)
        if not fixed_yaml:
            skipped.append({
                "draftId": draft_id,
                "jobId": source_job_id,
                "taskName": draft.get("taskName") or source_item.get("taskName") or draft.get("file") or "",
                "status": "missing_yaml",
                "reason": "修复草稿没有可执行 YAML 内容，未重跑旧脚本",
            })
            continue
        validation = draft.get("validation") or draft.get("yamlValidation") or {}
        if not isinstance(validation, dict) or "ok" not in validation:
            validation = validate_midscene_yaml_executability(fixed_yaml)
        if not validation.get("ok"):
            skipped.append({
                "draftId": draft_id,
                "jobId": source_job_id,
                "taskName": draft.get("taskName") or source_item.get("taskName") or draft.get("file") or "",
                "status": "invalid_yaml",
                "reason": "修复 YAML 未通过可执行校验，未重跑旧脚本",
                "issues": validation.get("issues") or [],
            })
            continue
        source_name = (
            draft.get("taskName") or draft.get("task_name") or
            source_item.get("taskName") or source_item.get("file") or
            draft.get("file") or draft_id or f"repair-{index}"
        )
        file_name = clean_filename(f"{index:02d}-{slug_for_file(source_name)}-{clean_id(draft_id, 'repair')[:8]}.yaml")
        path = safe_join(module_dir, file_name)
        write_text_file(path, fixed_yaml)
        task_names = _agent_repair_yaml_task_names(fixed_yaml)
        targets.append({
            "draftId": draft_id,
            "sourceJobId": source_job_id,
            "sourceModule": source_item.get("module") or source_job.get("module") or draft.get("module") or "",
            "sourceFile": source_item.get("file") or source_job.get("file") or draft.get("file") or "",
            "sourceTaskName": source_item.get("taskName") or source_job.get("target_task_name") or draft.get("taskName") or draft.get("task_name") or "",
            "module": module,
            "file": file_name,
            "path": path,
            "taskNames": task_names,
            "validation": validation,
            "sourceItem": source_item,
            "sourceJob": source_job,
            "failureReason": source_item.get("failureReason") or source_item.get("error") or draft.get("analysis") or "",
        })

    artifacts["rerunRepairYamlRefs"] = [
        {
            "draftId": item.get("draftId"),
            "sourceJobId": item.get("sourceJobId"),
            "sourceModule": item.get("sourceModule"),
            "sourceFile": item.get("sourceFile"),
            "sourceTaskName": item.get("sourceTaskName"),
            "module": item.get("module"),
            "file": item.get("file"),
            "path": item.get("path"),
            "taskNames": item.get("taskNames") or [],
        }
        for item in targets
    ]
    return {
        "hasRepairDrafts": True,
        "draftCount": len(drafts),
        "targets": targets,
        "skipped": skipped,
        "module": module,
    }


def _tool_rerun(run):
    """对失败任务重新创建 Runner job，并等待实际执行结果。"""
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "retry_failed_job",
        "category": "TASK",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {},
    }
    try:
        from task_server.services import job_service
        artifacts = run.setdefault("artifacts", {})
        failed_items = _agent_persist_failed_execution_items(run)
        failed_ids = [_failed_job_id(item) for item in failed_items if _failed_job_id(item)]
        if failed_ids:
            job_ids = failed_ids
        else:
            job_ids = [str(jid) for jid in (artifacts.get("jobIds") or []) if str(jid or "").strip()]
        source_by_id = {item.get("jobId"): item for item in failed_items if item.get("jobId")}
        retried = []
        retry_sources = []
        skipped = []
        jobs = job_service.load_jobs()
        repair_plan = _agent_prepare_repair_rerun_targets(run, failed_items, jobs)
        uses_repair_draft = bool(repair_plan.get("hasRepairDrafts"))
        if uses_repair_draft:
            for target in repair_plan.get("targets") or []:
                j = target.get("sourceJob") if isinstance(target.get("sourceJob"), dict) else {}
                source = target.get("sourceItem") if isinstance(target.get("sourceItem"), dict) else {}
                source_job_id = target.get("sourceJobId") or source.get("jobId") or j.get("job_id") or ""
                new_job = job_service.create_pending_job(
                    target.get("module", ""),
                    target.get("file", ""),
                    auto_optimize=False,
                    max_attempt=safe_int(j.get("max_attempt"), 2),
                    attempt=safe_int(j.get("attempt"), 1) + 1,
                    parent_job_id=source_job_id,
                    device_id=j.get("device_id") or run.get("deviceId") or run.get("device_id") or "",
                    runner_id=j.get("target_runner_id") or j.get("runner_id", ""),
                    device_strategy=j.get("device_strategy") or j.get("deviceStrategy") or "",
                    run_mode=j.get("run_mode", "test"),
                    target_task_name="",
                )
                if new_job and new_job.get("job_id"):
                    retried.append(new_job["job_id"])
                    retry_sources.append({
                        "source": "repair_draft",
                        "draftId": target.get("draftId") or "",
                        "sourceJobId": source_job_id,
                        "newJobId": new_job["job_id"],
                        "module": target.get("module", ""),
                        "file": target.get("file", ""),
                        "path": target.get("path", ""),
                        "sourceModule": target.get("sourceModule", ""),
                        "sourceFile": target.get("sourceFile", ""),
                        "targetTaskName": target.get("sourceTaskName") or (target.get("taskNames") or [""])[0],
                        "repairTaskNames": target.get("taskNames") or [],
                        "failureReason": target.get("failureReason") or source.get("failureReason") or source.get("error") or "",
                        "sourceStatus": j.get("status", ""),
                        "note": "使用 AI 修复草稿生成的临时 YAML 重跑，未覆盖原始 YAML",
                    })
            skipped.extend(repair_plan.get("skipped") or [])
            covered_source_ids = {item.get("sourceJobId") for item in retry_sources if item.get("sourceJobId")}
            for item in failed_items:
                item_id = _failed_job_id(item)
                if item_id and item_id in covered_source_ids:
                    continue
                if any(_agent_repair_draft_matches_failed_item({"jobId": skipped_item.get("jobId"), "file": skipped_item.get("file"), "taskName": skipped_item.get("taskName")}, item) for skipped_item in skipped):
                    continue
                skipped.append({
                    "jobId": item_id,
                    "taskName": _failed_job_task_name(item) or item.get("file") or "",
                    "status": item.get("status") or "failed",
                    "reason": "没有可用修复草稿，未重跑旧 YAML",
                })
        else:
            for jid in job_ids:
                j = next((job for job in jobs if job.get("job_id") == jid or job.get("jobId") == jid), None)
                source = source_by_id.get(jid) or {}
                if j and str(j.get("status", "")).lower() in ("failed", "error", "timeout"):
                    target_task_name = j.get("target_task_name") or j.get("taskName") or j.get("current_task_name") or ""
                    new_job = job_service.create_pending_job(
                        j.get("module", ""),
                        j.get("file", ""),
                        auto_optimize=False,
                        max_attempt=safe_int(j.get("max_attempt"), 2),
                        attempt=safe_int(j.get("attempt"), 1) + 1,
                        parent_job_id=jid,
                        device_id=j.get("device_id", ""),
                        runner_id=j.get("target_runner_id") or j.get("runner_id", ""),
                        device_strategy=j.get("device_strategy") or j.get("deviceStrategy") or "",
                        run_mode=j.get("run_mode", "test"),
                        target_task_name=target_task_name,
                    )
                    if new_job and new_job.get("job_id"):
                        retried.append(new_job["job_id"])
                        retry_sources.append({
                            "source": "original_yaml",
                            "sourceJobId": jid,
                            "newJobId": new_job["job_id"],
                            "module": j.get("module", ""),
                            "file": j.get("file", ""),
                            "targetTaskName": target_task_name,
                            "failureReason": source.get("failureReason") or source.get("error") or "",
                            "sourceStatus": j.get("status", ""),
                        })
                elif j:
                    skipped.append({
                        "jobId": jid,
                        "status": j.get("status", ""),
                        "taskName": source.get("taskName") or j.get("target_task_name") or j.get("current_task_name") or "",
                        "reason": "不是失败/超时终态，不创建重跑任务",
                    })
                else:
                    skipped.append({
                        "jobId": jid,
                        "status": "not_found",
                        "taskName": source.get("taskName") or "",
                        "reason": "原始 job 已不存在",
                    })
        artifacts["retriedJobs"] = retried
        artifacts["rerunSources"] = retry_sources
        artifacts["rerunSkippedJobs"] = skipped
        artifacts["rerunProgress"] = {
            "scope": "failed_tasks",
            "source": "repair_draft" if uses_repair_draft else "original_yaml",
            "usesRepairDraft": uses_repair_draft,
            "repairDraftCount": repair_plan.get("draftCount", 0) if uses_repair_draft else 0,
            "appliedRepairDraftCount": len(repair_plan.get("targets") or []) if uses_repair_draft else 0,
            "notRerunOriginalYaml": uses_repair_draft,
            "sourceFailedCount": len(failed_items),
            "targetCount": len(job_ids),
            "createdCount": len(retried),
            "skippedCount": len(skipped),
            "createdJobIds": retried,
            "sources": retry_sources,
            "skipped": skipped,
            "status": "CREATED" if retried else "SKIPPED",
        }
        call["createdJobIds"] = retried
        call["sourceFailedCount"] = len(failed_items)
        call["targetCount"] = len(job_ids)
        call["skippedCount"] = len(skipped)
        call["usesRepairDraft"] = uses_repair_draft
        call["outputSummary"] = (
            f"基于 {len(failed_items)} 个失败任务，"
            f"{'使用修复草稿' if uses_repair_draft else '使用原始 YAML'}创建 {len(retried)} 个重跑任务"
        )
        if not retried:
            call["status"] = "FAILED" if uses_repair_draft else ("SKIPPED" if not retry_sources else "FAILED")
            call["outputSummary"] = (
                "已有修复草稿但没有可执行 YAML，已阻止重跑原脚本"
                if uses_repair_draft else "没有可重跑的失败任务"
            )
            attach_diagnosis(call, make_diagnosis(
                "没有创建任何重跑任务",
                "安全重跑没有进入 Runner 执行队列；如果已有修复草稿，系统不会再静默重跑旧 YAML。",
                ["查看 repairSummary 中每条草稿的 AI/YAML 校验结果", "重新生成修复草稿", "必要时人工修正 YAML 后再重跑"],
                skippedJobs=skipped[:10],
                failedExecutionItems=failed_items[:10],
            ))
        else:
            wait_timeout = job_service.runner_job_wait_timeout_seconds(len(retried))
            wait_result = job_service.wait_jobs_finished(
                retried,
                run,
                timeout=wait_timeout,
                interval=5,
                phase="安全重跑",
            )
            completed = wait_result.get("completed") or []
            failed = wait_result.get("failed") or []
            timeout_jobs = wait_result.get("timeout") or []
            artifacts["rerunResult"] = {
                "createdCount": len(retried),
                "completedCount": len(completed),
                "failedCount": len(failed),
                "timeoutCount": len(timeout_jobs),
                "completed": completed,
                "failed": failed,
                "timeout": timeout_jobs,
                "waitTimeoutSeconds": wait_timeout,
            }
            progress = dict(artifacts.get("jobProgress") or {})
            progress.update({
                "scope": "failed_tasks",
                "source": "repair_draft" if uses_repair_draft else "original_yaml",
                "usesRepairDraft": uses_repair_draft,
                "repairDraftCount": repair_plan.get("draftCount", 0) if uses_repair_draft else 0,
                "appliedRepairDraftCount": len(repair_plan.get("targets") or []) if uses_repair_draft else 0,
                "notRerunOriginalYaml": uses_repair_draft,
                "sourceFailedCount": len(failed_items),
                "targetCount": len(job_ids),
                "createdCount": len(retried),
                "skippedCount": len(skipped),
                "createdJobIds": retried,
                "sources": retry_sources,
                "skipped": skipped,
            })
            artifacts["rerunProgress"] = progress
            summary = f"重跑执行完成：失败任务 {len(failed_items)} 个，创建 {len(retried)} 个，成功 {len(completed)} 个，失败 {len(failed)} 个，超时 {len(timeout_jobs)} 个"
            if uses_repair_draft:
                summary += f"；使用修复草稿 {len(repair_plan.get('targets') or [])}/{repair_plan.get('draftCount', 0)} 条，未覆盖失败任务 {len(skipped)} 个"
            call["outputSummary"] = summary
            call["rerunResult"] = artifacts["rerunResult"]
            call["rerunProgress"] = artifacts["rerunProgress"]
            if failed or timeout_jobs:
                call["status"] = "PARTIAL_FAILED" if completed else "FAILED"
                call["error"] = "重跑后仍有失败或超时任务"
                attach_diagnosis(call, make_diagnosis(
                    "重跑后仍有任务失败或超时",
                    "这次重跑已经实际下发 Runner，但存在失败结果。",
                    ["查看重跑 job 报告", "根据失败日志判断脚本/产品/环境问题", "必要时生成修复草稿后再重跑"],
                    failedJobs=(failed + timeout_jobs)[:10],
                ))
            elif uses_repair_draft and skipped:
                call["status"] = "PARTIAL_FAILED"
                call["error"] = "仅重跑了可用修复草稿，仍有失败任务未覆盖"
                attach_diagnosis(call, make_diagnosis(
                    "修复重跑覆盖不完整",
                    "系统只重跑了有可执行修复 YAML 的失败任务，未静默回退到旧 YAML。",
                    ["查看跳过的任务", "重新生成缺失任务的修复草稿", "确认是否需要人工处理剩余失败"],
                    skippedJobs=skipped[:10],
                ))
            else:
                call["status"] = "SUCCESS"
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def _tool_generate_summary(run):
    """生成总结报告。"""
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "generate_summary",
        "category": "AI",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {},
    }
    try:
        artifacts = run.setdefault("artifacts", {})
        steps = run.get("steps", [])
        completed = sum(1 for s in steps if s.get("status") == "SUCCESS")
        failed = sum(1 for s in steps if str(s.get("status")).upper() in ("FAILED", "PARTIAL_FAILED"))
        skipped = sum(1 for s in steps if s.get("status") == "SKIPPED")
        report = artifacts.get("report") or {}
        failure = artifacts.get("failureAnalysis") or {}
        matched_count = _safe_int_local(artifacts.get("matchedCount"), len(artifacts.get("matchedCases") or []))
        report_count = len(report.get("executionReports") or report.get("reports") or [])
        failed_jobs = report.get("failedJobs") or []
        timeout_jobs = report.get("timeoutJobs") or []
        running_jobs = report.get("runningJobs") or []
        failed_execution_items = artifacts.get("failedExecutionItems") or _agent_failed_execution_items(run)
        conclusion = "通过"
        if failed or failed_jobs or timeout_jobs or failed_execution_items:
            conclusion = "未通过"
        elif running_jobs:
            conclusion = "执行中"
        elif report.get("status") == "missing":
            conclusion = "报告缺失"
        next_actions = []
        if failed_execution_items or failed_jobs or timeout_jobs:
            next_actions.extend(["打开失败任务报告或 Runner 日志", "确认是脚本问题后生成修复草稿", "修复后重跑失败用例"])
        elif running_jobs:
            next_actions.extend(["等待 Runner 回传执行结果", "刷新 Agent 运行状态"])
        elif report.get("status") == "missing":
            next_actions.extend(["检查 Runner 报告上传", "查看执行中心 job 详情", "必要时重跑任务"])
        else:
            next_actions.extend(["保留本次结果作为回归记录", "如需复盘可查看执行报告链接"])
        summary = {
            "title": f"{run.get('target', 'Agent 任务')} - 执行总结",
            "target": run.get("target", ""),
            "conclusion": conclusion,
            "totalSteps": len(steps),
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
            "matchedCount": matched_count,
            "reportCount": report_count,
            "failedJobCount": len(failed_execution_items) or len(failed_jobs),
            "timeoutJobCount": len(timeout_jobs),
            "runningJobCount": len(running_jobs),
            "failedTasks": [
                {
                    "jobId": item.get("jobId"),
                    "taskName": item.get("taskName"),
                    "file": item.get("file"),
                    "reason": item.get("failureReason") or item.get("error"),
                }
                for item in failed_execution_items[:30]
            ],
            "failureType": failure.get("failureType") or "NONE",
            "nextActions": next_actions[:5],
            "reportStatus": report.get("status") or "",
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "mode": run.get("mode", ""),
            "riskLevel": run.get("riskLevel", ""),
            "message": f"Agent 执行完成：{completed}/{len(steps)} 步骤成功，{failed} 失败，{skipped} 跳过",
        }
        if _ai_gateway_available():
            try:
                resp = _ai_gateway_post("/ai/generate-case", {
                    "target": run.get("target", ""),
                    "scope": "summary",
                    "mode": run.get("mode", "AUTO_SAFE"),
                })
                if isinstance(resp, dict) and resp.get("summary"):
                    summary["aiSummary"] = resp["summary"]
                call["status"] = "SUCCESS"
                call["outputSummary"] = "总结报告生成完成"
            except Exception as e:
                call["status"] = "SKIPPED"
                call["outputSummary"] = f"AI Gateway 总结生成失败：{str(e)[:200]}"
        else:
            call["status"] = "SKIPPED"
            call["outputSummary"] = "AI Gateway 不可用，使用本地总结"
        artifacts["summary"] = summary
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def _tool_diagnose_failure(run):
    """把底层失败转成 Agent 可读诊断。"""
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "diagnose_failure",
        "category": "READ",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {},
    }
    try:
        artifacts = run.setdefault("artifacts", {})
        existing = artifacts.get("diagnosis")
        if isinstance(existing, dict) and existing.get("rootCause"):
            diagnosis = existing
        else:
            failed_steps = [s for s in run.get("steps", []) if str(s.get("status")).upper() in ("FAILED", "PARTIAL_FAILED")]
            sync_failed = ((artifacts.get("sonicSync") or {}).get("failed") or [])
            validation = _agent_yaml_validation_state(artifacts.get("yamlValidation"))
            if validation.get("issues"):
                diagnosis = make_diagnosis(
                    "YAML 强校验未通过",
                    "不能同步 Sonic 或执行测试。",
                    ["重新生成 YAML", "人工编辑 YAML 草稿", "确认 android/ios.tasks 非空"],
                    failedYaml=validation.get("issues", [])[:5],
                )
            elif sync_failed:
                err = str(sync_failed[0].get("error") or "")
                if "401" in err or "Unauthorized" in err:
                    diagnosis = make_diagnosis(
                        "Sonic 桥接脚本鉴权失败",
                        "Sonic 无法从 Task 平台拉取桥接脚本或回传执行状态。",
                        ["刷新 Sonic 桥接脚本", "检查 MIDSCENE_RUNNER_TOKEN", "确认 Sonic 用例里的脚本包含 x-token"],
                        failedYaml=sync_failed[0],
                    )
                else:
                    diagnosis = make_diagnosis("Sonic 同步失败", "无法执行 Sonic 回归。", ["查看同步失败详情", "检查项目/套件绑定", "重新同步 YAML"], failedYaml=sync_failed[0])
            elif failed_steps:
                first = failed_steps[0]
                diagnosis = make_diagnosis(
                    first.get("error") or first.get("summary") or "Agent 步骤失败",
                    "Agent 链路已停止或部分结果不可用。",
                    ["展开失败步骤查看诊断", "修复配置或 YAML 后重试", "必要时人工处理"],
                    failedStep=first.get("step"),
                )
            else:
                diagnosis = make_diagnosis("暂无失败需要诊断", "当前链路没有失败阻塞。", ["继续查看总结报告"])
            artifacts["diagnosis"] = diagnosis
        call["status"] = "SUCCESS" if diagnosis.get("rootCause") != "暂无失败需要诊断" else "SKIPPED"
        call["outputSummary"] = diagnosis.get("rootCause", "")
        call["diagnosis"] = diagnosis
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)[:500]
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def _tool_learn_from_result(run):
    """沉淀 Agent 结果到历史学习库。"""
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "learn_from_result",
        "category": "KNOWLEDGE",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {"runId": run.get("runId", "")},
    }
    try:
        artifacts = run.get("artifacts") or {}
        record = {
            "runId": run.get("runId"),
            "target": run.get("target"),
            "status": run.get("status"),
            "currentStep": run.get("currentStep"),
            "createdAt": run.get("createdAt"),
            "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "caseRetrieval": artifacts.get("caseRetrieval"),
            "matchedCases": artifacts.get("matchedCases") or [],
            "yamlRefs": artifacts.get("yamlRefs") or [],
            "diagnosis": artifacts.get("diagnosis"),
            "jobResult": artifacts.get("jobResult"),
            "sonicSync": artifacts.get("sonicSync"),
        }
        record["summary"] = {
            "matchedCases": len(record.get("matchedCases") or []),
            "yamlRefs": len(record.get("yamlRefs") or []),
            "hasDiagnosis": bool(record.get("diagnosis")),
            "hasJobResult": bool(record.get("jobResult")),
            "hasSonicSync": bool(record.get("sonicSync")),
        }
        with AGENT_LEARNING_LOCK:
            data = read_json_file(AGENT_LEARNING_FILE, default={"records": []})
            records = data.get("records") if isinstance(data, dict) else []
            records = [item for item in (records or []) if item.get("runId") != run.get("runId")]
            records.insert(0, record)
            write_json_file(AGENT_LEARNING_FILE, {"records": records[:500]})
        run.setdefault("artifacts", {})["learningSummary"] = record["summary"]
        call["status"] = "SUCCESS"
        call["learningSummary"] = record["summary"]
        call["outputSummary"] = (
            "已写入 Agent 历史学习库："
            f"匹配用例 {record['summary']['matchedCases']} 个，"
            f"YAML 引用 {record['summary']['yamlRefs']} 个，"
            f"{'包含诊断' if record['summary']['hasDiagnosis'] else '无诊断'}，"
            f"{'包含执行结果' if record['summary']['hasJobResult'] else '无执行结果'}"
        )
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)[:500]
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


# ---------------------------------------------------------------------------
# Agent Step Execution (consistent with midscene-upload.py)
# ---------------------------------------------------------------------------

_STEP_TOOL_MAP = {
    "PLAN": _tool_agent_plan,
    "PREPARE_SOURCE": _tool_prepare_source,
    "IMPACT_ANALYSIS": _tool_impact_analysis,
    "CASE_RETRIEVAL": _tool_case_retrieval,
    "MATCH_CASES": _tool_match_cases,
    "GENERATE_YAML": _tool_generate_yaml,
    "VALIDATE_YAML": _tool_validate_yaml,
    "RISK_REVIEW": _tool_risk_review,
    "EXECUTION_PRECHECK": _tool_execution_precheck,
    "SYNC_SONIC": _tool_sync_sonic,
    "RUN_SONIC": _tool_run_sonic,
    "COLLECT_REPORT": _tool_collect_report,
    "ANALYZE_FAILURE": _tool_analyze_failure,
    "DIAGNOSE_FAILURE": _tool_diagnose_failure,
    "GENERATE_REPAIR": _tool_generate_repair,
    "GENERATE_BUG_DRAFT": _tool_generate_bug_draft,
    "RERUN": _tool_rerun,
    "LEARN_FROM_RESULT": _tool_learn_from_result,
    "GENERATE_SUMMARY": _tool_generate_summary,
}

_STEP_ORDER = [
    "PLAN", "PREPARE_SOURCE", "IMPACT_ANALYSIS", "CASE_RETRIEVAL", "MATCH_CASES",
    "GENERATE_YAML", "VALIDATE_YAML", "RISK_REVIEW", "EXECUTION_PRECHECK",
    "SYNC_SONIC", "RUN_SONIC", "COLLECT_REPORT", "ANALYZE_FAILURE",
    "DIAGNOSE_FAILURE", "GENERATE_REPAIR", "GENERATE_BUG_DRAFT",
    "RERUN", "LEARN_FROM_RESULT", "GENERATE_SUMMARY",
]


def _refresh_agent_run_progress(run: Dict[str, Any]) -> int:
    steps = run.get("steps") if isinstance(run, dict) else []
    if not isinstance(steps, list) or not steps:
        return _safe_int_local((run or {}).get("progress"), 0)
    status = str(run.get("status") or "").upper()
    if status in ("DONE", "FINISH"):
        run["progress"] = 100
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
        progress = max(progress, _safe_int_local(run.get("progress"), 0), 1)
        run["progress"] = min(progress, 99)
    else:
        run["progress"] = max(_safe_int_local(run.get("progress"), 0), min(progress, 99))
    return _safe_int_local(run.get("progress"), 0)


def _execute_agent_step(run, step_name):
    """Execute a single agent step by calling the real _tool_xxx service.

    Returns (result, error) where result is the tool call record and error
    is None on success / a string on failure.
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    step = next((s for s in run["steps"] if s["step"] == step_name), None)
    if not step or step["status"] != "PENDING":
        return None, None
    step["status"] = "RUNNING"
    step["startedAt"] = now
    step["toolCalls"] = []
    step["liveTrace"] = [{
        "time": _trace_time_text(),
        "message": f"开始执行 {step_name}",
        "status": "RUNNING",
    }]
    business_constraint = _ensure_business_flow_constraint(run)
    flow_brief = " → ".join((business_constraint.get("businessFlow") or [])[:4])
    if flow_brief:
        step["liveTrace"].append({
            "time": _trace_time_text(),
            "message": f"业务主链约束：{flow_brief}",
            "status": "RUNNING",
        })
    run["currentStep"] = step_name
    run["updatedAt"] = now
    _refresh_agent_run_progress(run)
    _checkpoint_agent_state(run, "step_started", step_name, "RUNNING")
    with AGENT_RUN_LOCK:
        runs = load_agent_runs()
        for i, r in enumerate(runs):
            if r.get("runId") == run["runId"]:
                runs[i] = run
                break
        save_agent_runs(runs)
    result = None
    error = None
    try:
        tool_fn = _STEP_TOOL_MAP.get(step_name)
        tool_name = getattr(tool_fn, "__name__", step_name) if tool_fn else step_name
        if tool_fn:
            _append_step_trace(run, step, f"准备调用工具：{tool_name}", status="RUNNING", tool=step_name)
        if _agent_run_cancel_requested(run):
            _apply_agent_cancel_state(run, "用户取消")
            return {"status": "CANCELLED", "summary": "用户取消"}, None
        if tool_fn:
            _append_step_trace(run, step, f"调用工具：{tool_name}", tool=step_name)
            result = tool_fn(run)
            if _agent_run_cancel_requested(run):
                _apply_agent_cancel_state(run, "用户取消")
                return {"status": "CANCELLED", "summary": "用户取消"}, None
        elif step_name == "APPLY_SAFE_REPAIR":
            # APPLY_SAFE_REPAIR 仅在确认后执行
            step["status"] = "SKIPPED"
            step["summary"] = "需要人工确认后执行"
            run["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            return result, None
        else:
            step["status"] = "SKIPPED"
            step["summary"] = f"未知步骤：{step_name}"
            run["updatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            return result, None
        # Collect tool call into step
        if result and isinstance(result, dict):
            if step_name == "PREPARE_SOURCE" and result.get("status") in ("SUCCESS", "PARTIAL_FAILED"):
                _compact_agent_run_input_blobs(run)
            result.setdefault("businessFlowConstraint", _compact_business_flow_constraint(business_constraint))
            result.setdefault("toolEligibility", {
                "allowed": True,
                "reason": "状态机步骤已绑定业务主链约束",
                "businessFlowSource": business_constraint.get("source", "default"),
                "businessFlowKeywords": _business_flow_keywords(business_constraint),
            })
            step["toolCalls"].append(result)
            _append_step_trace(
                run,
                step,
                result.get("outputSummary") or result.get("error") or f"{step_name} 工具返回",
                status=result.get("status") or "",
            )
            if result.get("diagnosis"):
                step["diagnosis"] = result.get("diagnosis")
            if result.get("status") == "FAILED":
                error = result.get("error", "工具调用失败")
            elif result.get("status") == "WAIT_CONFIRM":
                step["status"] = "WAIT_CONFIRM"
    except Exception as e:
        error = str(e)
        result = {"status": "FAILED", "error": error}
        _append_step_trace(run, step, f"执行异常：{error[:200]}", status="FAILED")
    # Update step status
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    if _agent_run_cancel_requested(run):
        _apply_agent_cancel_state(run, "用户取消")
        _persist_agent_run_snapshot(run)
        return {"status": "CANCELLED", "summary": "用户取消"}, None
    step["endedAt"] = now
    step["durationMs"] = _compute_duration(step)
    if error:
        step["status"] = "FAILED"
        step["summary"] = f"{step_name} 失败：{str(error)[:200]}"
        step["error"] = str(error)[:500]
        _append_step_trace(run, step, f"步骤失败：{str(error)[:200]}", status="FAILED")
        if isinstance(result, dict) and result.get("diagnosis"):
            step["diagnosis"] = result.get("diagnosis")
    else:
        if step["status"] == "RUNNING":
            # Inherit status from tool call result
            if result and isinstance(result, dict) and result.get("status") in ("SKIPPED", "PARTIAL_FAILED", "WAIT_CONFIRM"):
                step["status"] = "SKIPPED"
                if result.get("status") == "PARTIAL_FAILED":
                    step["status"] = "PARTIAL_FAILED"
                if result.get("status") == "WAIT_CONFIRM":
                    step["status"] = "WAIT_CONFIRM"
            else:
                step["status"] = "SUCCESS"
        msg = ""
        if isinstance(result, dict):
            msg = result.get("outputSummary") or result.get("summary") or result.get("message") or ""
        step["summary"] = msg or f"{step_name} 完成"
        _append_step_trace(run, step, step["summary"], status=step.get("status", "SUCCESS"))
    run["updatedAt"] = now
    _refresh_agent_run_progress(run)
    _checkpoint_agent_state(run, "step_finished", step_name, step.get("status", ""))
    # 每步完成后立即持久化，避免异常时 in-memory 状态丢失
    with AGENT_RUN_LOCK:
        persisted_runs = load_agent_runs()
        for i, r in enumerate(persisted_runs):
            if r.get("runId") == run["runId"]:
                persisted_runs[i] = run
                break
        save_agent_runs(persisted_runs)
    return result, error


def _execute_agent_steps(run_id):
    """Background thread to execute agent steps sequentially.

    Each step calls a real _tool_xxx function.  Conditional branching:
      - RISK_REVIEW: HIGH risk -> WAIT_CONFIRM (any mode)
      - GENERATE_REPAIR: only SCRIPT_ISSUE
      - GENERATE_BUG_DRAFT: only PRODUCT_BUG
      - UNKNOWN failure type -> WAIT_CONFIRM
    """
    time.sleep(0.5)
    with AGENT_RUN_LOCK:
        runs = load_agent_runs()
        run = next((r for r in runs if r.get("runId") == run_id), None)
        if not run:
            return
    # Pre-load failure analysis if this is a rerun from a failed job
    failed_job_id = run.get("failedJobId")
    if failed_job_id:
        try:
            from task_server.services import job_service
            jobs = job_service.load_jobs()
            failed_job = next((j for j in jobs if j.get("job_id") == failed_job_id), None)
            if failed_job:
                run["artifacts"]["failureAnalysis"] = {
                    "jobId": failed_job_id,
                    "status": failed_job.get("status", "failed"),
                    "file": failed_job.get("file", ""),
                    "module": failed_job.get("module", ""),
                    "failureType": failed_job.get("failure_type", "SCRIPT_ISSUE"),
                    "summary": (failed_job.get("error") or failed_job.get("stderr_tail", ""))[:500],
                }
        except Exception:
            pass
    # Step execution order (matching AGENT_RUN_STEPS, excluding meta states)
    step_order = _STEP_ORDER
    try:
        for step_name in step_order:
            if run.get("status") in ("CANCELLED", "WAIT_CONFIRM"):
                break
            step = next((s for s in run["steps"] if s["step"] == step_name), None)
            if not step:
                continue
            execution_mode = str(run.get("executionMode") or run.get("execution_mode") or "RUNNER_JOB").strip().upper()
            if execution_mode not in ("RUNNER_JOB", "SONIC_SUITE"):
                execution_mode = "RUNNER_JOB"
            if step_name == "SYNC_SONIC" and execution_mode != "SONIC_SUITE":
                now = time.strftime("%Y-%m-%dT%H:%M:%S")
                step["status"] = "SKIPPED"
                step["startedAt"] = now
                step["endedAt"] = now
                step["summary"] = "Runner 单条/多条调试模式不需要同步 Sonic，已跳过"
                step["liveTrace"] = [{
                    "time": _trace_time_text(),
                    "message": "当前执行模式为 RUNNER_JOB，直接交给 Windows/Mac Runner 执行已匹配 YAML，不调用 Sonic 测试套同步。",
                    "status": "SKIPPED",
                }]
                run["updatedAt"] = now
                _refresh_agent_run_progress(run)
                _persist_agent_run_snapshot(run)
                continue
            # Conditional: GENERATE_REPAIR only for SCRIPT_ISSUE
            if step_name == "GENERATE_REPAIR":
                fa = (run.get("artifacts") or {}).get("failureAnalysis")
                ft = str(fa.get("failureType", "UNKNOWN")).upper() if fa else "NONE"
                if ft not in ("SCRIPT_ISSUE",):
                    step["status"] = "SKIPPED"
                    step["summary"] = f"非 SCRIPT_ISSUE（{ft}），跳过修复"
                    step["startedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    step["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    continue
            # Conditional: GENERATE_BUG_DRAFT only for PRODUCT_BUG
            if step_name == "GENERATE_BUG_DRAFT":
                fa = (run.get("artifacts") or {}).get("failureAnalysis")
                ft = str(fa.get("failureType", "UNKNOWN")).upper() if fa else "NONE"
                if ft != "PRODUCT_BUG":
                    step["status"] = "SKIPPED"
                    step["summary"] = f"非 PRODUCT_BUG（{ft}），跳过缺陷草稿"
                    step["startedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    step["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    continue
            # Conditional: RERUN only if there are failed jobs
            if step_name == "RERUN":
                fa = (run.get("artifacts") or {}).get("failureAnalysis")
                if not fa:
                    step["status"] = "SKIPPED"
                    step["summary"] = "无失败任务，跳过重跑"
                    step["startedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    step["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    continue
            result, error = _execute_agent_step(run, step_name)
            if run.get("status") == "CANCELLED":
                break
            if run.get("status") == "WAIT_CONFIRM" or (isinstance(result, dict) and result.get("status") == "WAIT_CONFIRM"):
                run["status"] = "WAIT_CONFIRM"
                run["currentStep"] = "WAIT_CONFIRM"
                break
            if error:
                NON_CRITICAL_STEPS = (
                    "ANALYZE_FAILURE", "GENERATE_REPAIR", "GENERATE_BUG_DRAFT",
                    "COLLECT_REPORT", "GENERATE_SUMMARY", "RERUN", "DIAGNOSE_FAILURE", "LEARN_FROM_RESULT",
                )
                if step_name in NON_CRITICAL_STEPS:
                    pass  # Non-critical, continue
                else:
                    run["status"] = "FAILED"
                    run["error"] = str(error)[:500]
                    if step_name in ("VALIDATE_YAML", "EXECUTION_PRECHECK", "SYNC_SONIC"):
                        for subsequent in run.get("steps", []):
                            if subsequent.get("step") in ("RUN_SONIC", "COLLECT_REPORT", "ANALYZE_FAILURE", "GENERATE_REPAIR") and subsequent.get("status") == "PENDING":
                                subsequent["status"] = "SKIPPED"
                                subsequent["summary"] = f"前置步骤 {step_name} 失败，跳过"
                                subsequent["startedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                                subsequent["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    break
            # SYNC_SONIC 全部失败时，跳过依赖步骤
            if step_name == "SYNC_SONIC":
                sync_artifacts = (run.get("artifacts") or {}).get("sonicSync", {})
                if not sync_artifacts.get("synced"):
                    for subsequent in run.get("steps", []):
                        if subsequent.get("step") in ("RUN_SONIC", "COLLECT_REPORT") and subsequent.get("status") == "PENDING":
                            subsequent["status"] = "SKIPPED"
                            subsequent["summary"] = "前置步骤 SYNC_SONIC 失败，跳过"
                            subsequent["startedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                            subsequent["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            # Post-step: RISK_REVIEW -> check if HIGH risk
            if step_name == "RISK_REVIEW" and str(run.get("riskLevel") or "").upper() == "HIGH" and not run.get("riskConfirmed"):
                risk_detail = run.get("riskDetail") if isinstance(run.get("riskDetail"), dict) else {}
                risk_keyword = risk_detail.get("keyword") or "、".join(run.get("riskHits", []))
                if _runner_precheck_should_warn_risk(run, risk_keyword):
                    continue
                run["status"] = "WAIT_CONFIRM"
                run["currentStep"] = "WAIT_CONFIRM"
                now = time.strftime("%Y-%m-%dT%H:%M:%S")
                message = _risk_detail_summary(risk_detail, risk_keyword)
                run.setdefault("pendingConfirmations", []).append({
                    "id": f"confirm-{int(time.time())}",
                    "type": "high_risk_action",
                    "message": f"{message}，请确认是否继续",
                    "riskKeyword": risk_keyword,
                    "riskSource": risk_detail.get("source") or "",
                    "riskSnippet": risk_detail.get("snippet") or "",
                    "riskDetail": risk_detail,
                    "createdAt": now,
                    "decision": None,
                })
                break
            # Post-step: ANALYZE_FAILURE -> UNKNOWN type triggers WAIT_CONFIRM
            if step_name == "ANALYZE_FAILURE":
                fa = (run.get("artifacts") or {}).get("failureAnalysis") or {}
                ft = str(fa.get("failureType", "")).upper()
                if ft == "UNKNOWN":
                    run["status"] = "WAIT_CONFIRM"
                    run["currentStep"] = "WAIT_CONFIRM"
                    now = time.strftime("%Y-%m-%dT%H:%M:%S")
                    run.setdefault("pendingConfirmations", []).append({
                        "id": f"confirm-{int(time.time())}",
                        "type": "unknown_failure",
                        "message": "未知失败类型，需要人工复核",
                        "createdAt": now,
                        "decision": None,
                    })
                    break
                elif ft == "ENV_ISSUE":
                    # ENV_ISSUE: don't repair YAML, just note
                    pass
        # Mark remaining pending steps as SKIPPED only after terminal states; WAIT_CONFIRM keeps the queue visible.
        if run.get("status") != "WAIT_CONFIRM":
            for step in run.get("steps", []):
                if step.get("status") == "PENDING":
                    step["status"] = "SKIPPED"
                    step["summary"] = "前序步骤结束，自动跳过"
        # Ensure final status is honest: pipeline failures must not be shown as DONE.
        failed_steps = [
            s for s in run.get("steps", [])
            if str(s.get("status")).upper() in ("FAILED", "PARTIAL_FAILED")
        ]
        if failed_steps and run.get("status") not in ("CANCELLED", "WAIT_CONFIRM"):
            run["status"] = "FAILED"
            run["currentStep"] = failed_steps[-1].get("step") or "FAILED"
            run["progress"] = min(_safe_int_local(run.get("progress"), 0), 99)
            run["error"] = run.get("error") or failed_steps[-1].get("error") or failed_steps[-1].get("summary") or "Agent 步骤失败"
        elif run.get("status") not in ("CANCELLED", "WAIT_CONFIRM", "FAILED"):
            run["status"] = "DONE"
            run["currentStep"] = "DONE"
            run["progress"] = 100
            if not (run.get("artifacts") or {}).get("summary"):
                completed = sum(1 for s in run["steps"] if s.get("status") == "SUCCESS")
                failed = sum(1 for s in run["steps"] if s.get("status") == "FAILED")
                skipped = sum(1 for s in run["steps"] if s.get("status") == "SKIPPED")
                run["artifacts"]["summary"] = {
                    "totalSteps": len(run["steps"]),
                    "completed": completed,
                    "failed": failed,
                    "skipped": skipped,
                    "message": f"Agent 执行完成：{completed}/{len(run['steps'])} 成功，{failed} 失败，{skipped} 跳过",
                }
        try:
            if run.get("status") in ("FAILED", "DONE", "CANCELLED") or run.get("currentStep") == "WAIT_CONFIRM":
                if run.get("status") == "FAILED" and not (run.get("artifacts") or {}).get("diagnosis"):
                    _tool_diagnose_failure(run)
                _tool_learn_from_result(run)
        except Exception:
            pass
        # Persist final state
        with AGENT_RUN_LOCK:
            runs = load_agent_runs()
            for i, r in enumerate(runs):
                if r.get("runId") == run_id:
                    runs[i] = run
                    break
            save_agent_runs(runs)
    except Exception as e:
        with AGENT_RUN_LOCK:
            runs = load_agent_runs()
            run = next((r for r in runs if r.get("runId") == run_id), None)
            if run:
                run["status"] = "FAILED"
                run["error"] = str(e)[:500]
                # 把所有 RUNNING 状态的步骤标记为 FAILED
                for step in run.get("steps", []):
                    if step.get("status") == "RUNNING":
                        step["status"] = "FAILED"
                        step["error"] = str(e)[:200]
                        step["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                save_agent_runs(runs)


# ---------------------------------------------------------------------------
# 路由层快捷调用
# ---------------------------------------------------------------------------

def list_agent_tools() -> List[Dict[str, Any]]:
    """返回工具白名单（分类列表）。"""
    return TOOL_REGISTRY.to_category_list()


def list_agent_runs(limit: int = 20) -> List[Dict[str, Any]]:
    """带分页的 Agent Run 列表。"""
    limit = max(1, min(200, int(limit or 20)))
    runs = recover_stale_agent_runs(limit=max(limit, 20))
    # 移除 steps 详细信息，仅保留摘要字段
    summaries: List[Dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        steps = [s for s in (run.get("steps") or []) if isinstance(s, dict)]
        last_step = next((s for s in reversed(steps) if s.get("summary") or s.get("error")), {})
        summaries.append({
            "runId": run.get("runId", ""),
            "mode": run.get("mode", ""),
            "target": run.get("target", ""),
            "appName": run.get("appName", ""),
            "appPackage": run.get("appPackage") or run.get("app_package") or "",
            "platform": run.get("platform", ""),
            "scope": run.get("scope", ""),
            "executionMode": run.get("executionMode", ""),
            "runnerId": run.get("runnerId", ""),
            "deviceId": run.get("deviceId", ""),
            "deviceStrategy": run.get("deviceStrategy", ""),
            "sourceType": run.get("sourceType", ""),
            "status": run.get("status", ""),
            "currentStep": run.get("currentStep", ""),
            "progress": run.get("progress", 0),
            "riskLevel": run.get("riskLevel", "low"),
            "createdAt": run.get("createdAt", ""),
            "updatedAt": run.get("updatedAt", ""),
            "error": run.get("error"),
            "summary": run.get("summary") or last_step.get("summary") or last_step.get("error"),
            "inputSummary": _agent_input_summary(run, detailed=False),
            "pendingConfirmations": run.get("pendingConfirmations") or [],
        })
    return summaries[:limit]
