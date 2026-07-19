"""Agent 运行框架服务。

从 midscene-upload.py 迁移 Agent 状态机、工具调用和运行管理逻辑。
与 midscene-upload.py 中的 _execute_agent_step / _execute_agent_steps 保持一致。
"""

import copy
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
    dashscope_vl_model,
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
from task_server.services.yaml_service import (
    ai_rewrite_yaml_for_executable_gate,
    baseline_branch_anchor_terms,
    ensure_midscene_platform_root,
    extract_midscene_tasks,
    loading_wait_timeout_for_context,
    remove_empty_midscene_platform_roots,
    repair_generated_yaml_executable_gate_issues,
    should_ai_rewrite_for_executable_gate,
    slug_for_file,
    strict_visible_value_contract,
    validate_midscene_yaml_executability,
)
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

AGENT_PLATFORM_LIFECYCLE_STEPS = [
    "整理输入来源与软参考",
    "检索并由 AI 重排可信基线（优先真实执行成功）",
    "生成用例、YAML 与覆盖审查",
    "执行平台静态校验和风险门禁",
    "在指定 Runner/设备上先冒烟后扩展",
    "收集报告、关键帧和 Runner 日志",
    "由 AI 诊断失败并生成受约束修复草稿",
    "校验后只重跑失败用例并生成总结",
]

AGENT_GENERATED_RUNNER_SMOKE_LIMIT = max(
    1,
    min(10, safe_int(os.getenv("MIDSCENE_AGENT_GENERATED_RUNNER_SMOKE_LIMIT"), 3)),
)
AGENT_GENERATED_RUNNER_FIRST_SMOKE_LIMIT = max(
    1,
    min(3, safe_int(os.getenv("MIDSCENE_AGENT_GENERATED_RUNNER_FIRST_SMOKE_LIMIT"), 3)),
)
AGENT_GENERATED_RUNNER_EXPAND_LIMIT = max(
    AGENT_GENERATED_RUNNER_SMOKE_LIMIT,
    min(100, safe_int(os.getenv("MIDSCENE_AGENT_GENERATED_RUNNER_EXPAND_LIMIT"), 5)),
)
AGENT_GENERATED_RUNNER_EXPAND_BATCH_LIMIT = max(
    1,
    min(
        AGENT_GENERATED_RUNNER_EXPAND_LIMIT,
        safe_int(
            os.getenv("MIDSCENE_AGENT_GENERATED_RUNNER_EXPAND_BATCH_LIMIT"),
            max(AGENT_GENERATED_RUNNER_SMOKE_LIMIT, min(5, AGENT_GENERATED_RUNNER_SMOKE_LIMIT * 2)),
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


def _agent_cancel_runner_jobs(run_id, reason="用户取消"):
    """Cancel every non-terminal Runner job created by this Agent run."""
    run_id = str(run_id or "").strip()
    if not run_id:
        return []
    try:
        from task_server.services import job_service

        terminal_statuses = {"success", "failed", "cancelled", "timeout"}
        cancelled = []
        for job in job_service.load_jobs(limit=None):
            parent_run_id = str(job.get("parent_run_id") or job.get("parentRunId") or "").strip()
            if parent_run_id != run_id:
                continue
            status = job_service.normalize_job_status(job.get("status"))
            if status in terminal_statuses:
                continue
            job_id = str(job.get("job_id") or job.get("jobId") or "").strip()
            if not job_id:
                continue
            updated = job_service.update_job(job_id, {
                "status": "cancelled",
                "cancel_reason": str(reason or "用户取消"),
                "cancelled_by": "agent_run",
            })
            if updated:
                cancelled.append(job_id)
        return cancelled
    except Exception:
        return []


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
        "currentStep": "PREPARE_SOURCE",
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
    _checkpoint_agent_state(run, "created", "PREPARE_SOURCE", "RUNNING")
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
        run["currentStep"] = "PREPARE_SOURCE"
        run["status"] = "RUNNING"
        run["progress"] = 0
        run["updatedAt"] = now
        save_agent_runs(runs)

    # Start background step execution
    _start_agent_worker(run_id)
    return run


def preview_agent_plan(payload):
    """Preview inputs and platform gates without fabricating an AI business plan."""
    goal = str(payload.get("target") or payload.get("goal") or "").strip()
    app_name = str(payload.get("appName") or "").strip() or "智小白3D APP"
    platform = str(payload.get("platform") or "android").strip()
    scope = str(payload.get("scope") or "smoke").strip()
    mode = str(payload.get("mode") or "AUTO_SAFE").upper()
    risk_hits = [kw for kw in AGENT_RISK_KEYWORDS if kw in goal]
    normalized_input = normalize_agent_input(payload)
    preview_run = {
        "target": goal,
        "scope": scope,
        "normalizedInput": normalized_input,
        "artifacts": {},
    }
    constraint = _ensure_business_flow_constraint(preview_run)
    requirement_candidates = _agent_plan_constraint_flows(constraint)
    return {
        "mode": mode,
        "appName": app_name,
        "platform": platform,
        "scope": scope,
        "riskHits": risk_hits,
        "version": "agent-business-plan-preview-v2",
        "source": "requirement_preview",
        "aiGenerated": False,
        "candidateOnly": True,
        "businessFlows": [],
        "requirementCandidates": [
            {
                "id": str(item.get("id") or f"CANDIDATE-{index:03d}"),
                "name": str(item.get("name") or item.get("branch") or f"需求候选 {index}"),
                "branch": str(item.get("branch") or ""),
            }
            for index, item in enumerate(requirement_candidates, start=1)
        ],
        "steps": [],
        "platformLifecycle": list(AGENT_PLATFORM_LIFECYCLE_STEPS),
        "note": "这里只展示输入中显式出现的覆盖候选，不代表业务分支、层级或路径；任务启动后先准备资料，再由平台 MM skills 和所选模型生成真实业务计划。",
    }


def cancel_agent_run(run_id, reason="用户取消"):
    """取消 Agent 运行。"""
    cancel_reason = reason or "用户取消"
    _mark_agent_run_cancel_requested(run_id, cancel_reason)
    with AGENT_RUN_LOCK:
        runs = load_agent_runs()
        run = next((r for r in runs if r.get("runId") == run_id), None)
        if not run:
            return None
        if run.get("status") in ("DONE", "FAILED"):
            return run
        if run.get("status") != "CANCELLED":
            _apply_agent_cancel_state(run, cancel_reason)
            save_agent_runs(runs)
    _agent_cancel_progress_job(run_id, cancel_reason)
    cancelled_job_ids = _agent_cancel_runner_jobs(run_id, cancel_reason)
    with AGENT_RUN_LOCK:
        runs = load_agent_runs()
        run = next((r for r in runs if r.get("runId") == run_id), run)
        artifacts = run.setdefault("artifacts", {})
        artifacts["runnerCancellation"] = {
            "requestedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "reason": str(cancel_reason),
            "cancelledCount": len(cancelled_job_ids),
            "jobIds": cancelled_job_ids,
        }
        save_agent_runs(runs)
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
    content = ensure_midscene_platform_root(content, platform=run.get("platform", "android"))
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
    content = ensure_midscene_platform_root(content, platform=run.get("platform", "android"))
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
        target_platform = platform if platform in ("android", "ios") else str(run.get("platform") or "android").strip().lower()
        if target_platform not in ("android", "ios"):
            target_platform = "android"
        payload = {target_platform: {"tasks": [task]}}
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
    non_executable = []
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
        normalized_content = ensure_midscene_platform_root(content, platform=run.get("platform", "android"))
        if normalized_content != content:
            write_text_file(path, normalized_content)
            content = normalized_content
        check = validate_agent_yaml_content(content)
        local_score = score_midscene_yaml_executable(content, generated=True)
        declared_raw = item.get("executionLevel") or item.get("level")
        declared_level = _agent_execution_level(declared_raw) if declared_raw else ""
        local_level = _agent_execution_level(local_score.get("executionLevel") or local_score.get("level"))
        level_rank = {"manual": 0, "draft": 1, "needs_review": 2, "executable": 3}
        effective_level = local_level
        if declared_level and level_rank.get(declared_level, 1) < level_rank.get(effective_level, 1):
            effective_level = declared_level
        scope_review = item.get("scopeReview") if isinstance(item.get("scopeReview"), dict) else {}
        if scope_review and scope_review.get("ok") is False and effective_level == "executable":
            effective_level = "needs_review"
        high_replan_without_baseline = (
            not bool(local_score.get("baselineEvidence"))
            and any(
                str(task_score.get("replanRisk") or "").strip().lower() == "high"
                for task_score in (local_score.get("taskScores") or [])
                if isinstance(task_score, dict)
            )
        )
        preserve_declared_executable = (
            high_replan_without_baseline
            and declared_level == "executable"
            and local_level == "executable"
            and safe_int(local_score.get("score"), 0) >= 80
            and safe_int(item.get("score"), 0) >= 80
            and bool(check.get("ok"))
            and not check.get("issues")
            and not (scope_review and scope_review.get("ok") is False)
        )
        if high_replan_without_baseline and effective_level == "executable" and not preserve_declared_executable:
            effective_level = "needs_review"
        effective_reasons = []
        for reason in list(local_score.get("reasons") or local_score.get("warnings") or []) + list(item.get("reasons") or []) + list(scope_review.get("reasons") or []):
            text = str(reason or "").strip()
            if text and text not in effective_reasons:
                effective_reasons.append(text)
        executable_score = {
            **local_score,
            "rawExecutionLevel": local_level,
            "declaredExecutionLevel": declared_level,
            "executionLevel": effective_level,
            "level": effective_level,
            "ok": bool(local_score.get("ok")) and effective_level == "executable",
            "scopeReview": scope_review,
            "reasons": effective_reasons,
        }
        if high_replan_without_baseline and not preserve_declared_executable and "高重规划风险且缺少成功基线，只保留复核，不自动下发 Runner" not in executable_score["reasons"]:
            executable_score["reasons"].append("高重规划风险且缺少成功基线，只保留复核，不自动下发 Runner")
        result = {
            "module": module,
            "file": file_name or os.path.basename(path),
            "path": path,
            **check,
            "executionLevel": executable_score.get("executionLevel"),
            "executableScore": executable_score,
            "scopeReview": scope_review,
        }
        results.append(result)
        if not check.get("ok"):
            issues.extend([f"{result['file']}：{issue}" for issue in (check.get("issues") or ["校验未通过"])])
            continue
        if executable_score.get("executionLevel") != "executable":
            reasons = executable_score.get("reasons") or executable_score.get("warnings") or []
            reason_text = "；".join(str(reason) for reason in list(reasons)[:3] if str(reason).strip())
            non_executable.append(f"{result['file']}：{executable_score.get('executionLevel') or 'draft'}；{reason_text}".rstrip("；"))
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
            "scopeReview": scope_review,
        })
    generated_executable_count = len(refs)
    scope = str(run.get("scope") or "").strip().lower()
    if scope in ("regression", "回归", "full", "完整") and generated_executable_count <= 0:
        detail = issues or non_executable or ["完整回归没有达到 executable 的正式需求 YAML"]
        return [], "完整回归生成结果未达到 Runner 自动执行门禁：" + "；".join(detail)
    refs, results = _ensure_agent_entry_visibility_smoke_ref(run, refs, results)
    if not refs:
        detail = issues or non_executable or ["没有达到 executable 的 YAML 文件"]
        return [], "YAML 文件未达到 Runner 自动执行门禁：" + "；".join(detail)
    artifacts["generatedYaml"] = ""
    artifacts["generatedYamlPath"] = refs[0]["path"]
    artifacts["generatedYamlPaths"] = [item["path"] for item in refs]
    artifacts["draftConfirmed"] = True
    artifacts["yamlRefs"] = refs
    artifacts["yamlValidation"] = {
        "ok": not issues,
        "results": results,
        "issues": issues,
        "nonExecutable": non_executable,
        "executionGate": {
            "executableCount": sum(1 for item in results if (item.get("executableScore") or {}).get("executionLevel") == "executable"),
            "needsReviewCount": sum(1 for item in results if (item.get("executableScore") or {}).get("executionLevel") == "needs_review"),
            "draftCount": sum(1 for item in results if (item.get("executableScore") or {}).get("executionLevel") == "draft"),
            "manualCount": sum(1 for item in results if (item.get("executableScore") or {}).get("executionLevel") == "manual"),
        },
    }
    generation_pipeline = artifacts.get("generationPipeline") if isinstance(artifacts.get("generationPipeline"), dict) else {}
    if generation_pipeline:
        pipeline_gate = generation_pipeline.get("yamlExecutability") if isinstance(generation_pipeline.get("yamlExecutability"), dict) else {}
        generation_pipeline["yamlExecutability"] = {
            **pipeline_gate,
            "ok": bool(refs),
            "executableFileCount": len(refs),
            "generatedExecutableFileCount": generated_executable_count,
            "taskCount": len(refs),
        }
    quality = artifacts.get("qualityReport") if isinstance(artifacts.get("qualityReport"), dict) else {}
    if quality:
        quality["executableTaskCount"] = len(refs)
        quality["generatedExecutableTaskCount"] = generated_executable_count
        blockers = [
            str(item) for item in (quality.get("blockers") or [])
            if "没有可执行 YAML 文件" not in str(item)
        ]
        quality["blockers"] = blockers
        quality["status"] = "blocked" if blockers else ("warn" if quality.get("warnings") else "pass")
        quality["statusText"] = {"blocked": "阻断", "warn": "需关注", "pass": "通过"}[quality["status"]]
        for layer in quality.get("layers") or []:
            if isinstance(layer, dict) and layer.get("name") == "可自动化 YAML":
                layer["count"] = len(refs)
                layer["ready"] = bool(refs)
    _sync_agent_generated_case_groups(artifacts, results)
    return refs, "" if not issues else "；".join(issues)


def _agent_entry_visibility_intent(run):
    if not isinstance(run, dict):
        return None
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    source_context = artifacts.get("sourceContext") if isinstance(artifacts.get("sourceContext"), dict) else {}
    text = "\n".join([
        str(run.get("target") or ""),
        str(run.get("requirementText") or run.get("requirement_text") or ""),
        str((run.get("normalizedInput") or {}).get("requirementText") if isinstance(run.get("normalizedInput"), dict) else ""),
        str(source_context.get("requirementText") or ""),
    ])
    compact = re.sub(r"\s+", "", text)
    if "入口" not in compact:
        return None
    external_flow_terms = (
        "点击后", "跳转", "授权", "登录", "文件选择", "导入文件", "进入第三方",
        "进入百度网盘", "可达", "落地页", "WebView", "SDK", "支付", "删除",
    )
    if any(term in compact for term in external_flow_terms):
        return None
    visibility_terms = (
        "新增", "增加", "添加", "展示", "显示", "可见", "校验", "检查", "位置",
        "同级", "并列", "排序", "布局", "入口在", "入口位于",
    )
    if not any(term in compact for term in visibility_terms):
        return None

    entry_label = ""
    if "百度网盘" in compact:
        entry_label = "百度网盘"
    else:
        matches = re.findall(r"([\u4e00-\u9fffA-Za-z0-9_-]{1,18})入口", compact)
        for match in reversed(matches):
            label = match
            for _ in range(4):
                label = re.sub(r"^(基础打印|文档打印页|文档打印|照片打印页|照片打印|扫描复印页|扫描复印|复印扫描页|复印扫描|个人中心页|个人中心|设置页|设置|我的页|我的|首页|目标页面|新增一个|增加一个|新增|增加|添加|展示|显示|校验|检查)", "", label)
            if "入口" in label:
                label = label.split("入口")[-1] or label.split("入口")[0]
            label = re.sub(r"(新增|增加|添加|展示|显示|校验|检查)$", "", label)
            if label and label not in ("目标", "业务", "页面", "入口", "功能"):
                entry_label = label[-12:]
                break
    if not entry_label:
        entry_label = "目标"

    page_priority = ("文档打印", "照片打印", "扫描复印", "复印扫描", "个人中心", "我的", "设置", "首页")
    target_page = next((label for label in page_priority if label in compact), "")
    if target_page == "复印扫描":
        target_page = "扫描复印"
    if not target_page:
        target_page = "目标页面"
    return {
        "entryLabel": entry_label,
        "targetPage": target_page,
        "isHomePage": target_page == "首页",
    }


def _agent_needs_entry_visibility_smoke(run):
    return bool(_agent_entry_visibility_intent(run))


def _agent_use_direct_entry_visibility_smoke(run):
    scope = str((run or {}).get("scope") or "smoke").strip().lower()
    return _agent_needs_entry_visibility_smoke(run) and scope in ("smoke", "冒烟", "single", "单条")


def _agent_needs_baidu_entry_smoke(run):
    return _agent_needs_entry_visibility_smoke(run)


def _agent_entry_visibility_smoke_yaml(run):
    intent = _agent_entry_visibility_intent(run) or {}
    app_package = _agent_app_package(run)
    entry_label = str(intent.get("entryLabel") or "目标").strip() or "目标"
    target_page = str(intent.get("targetPage") or "目标页面").strip() or "目标页面"
    task_name = f"{target_page}{entry_label}入口可见性短链路冒烟"
    flow = [
        f"        - terminate: {app_package}",
        f"        - launch: {app_package}",
        f"        - aiWaitFor: 应用首页或启动页已打开，可看到{target_page}入口或底部导航",
        "          timeout: 15000",
    ]
    if target_page != "首页":
        flow.extend([
            f"        - aiTap: 应用首页或底部导航中名称为{target_page}的入口；只点击与“{target_page}”文字对应的目标，不要先进入其他打印、资料库、题库、教辅、模型页或我的",
            f"        - aiWaitFor: {target_page}页面或{target_page}导入入口区域已加载，并展示{entry_label}入口",
            "          timeout: 20000",
            f"        - aiAssert: {target_page}页面展示{entry_label}入口",
        ])
    else:
        flow.extend([
            f"        - aiWaitFor: 首页展示{entry_label}入口",
            "          timeout: 15000",
            f"        - aiAssert: 首页{entry_label}入口可见",
        ])
    flow_text = "\n".join(flow)
    return f"""android:
  tasks:
    - name: {task_name}
      flow:
{flow_text}
"""


def _agent_entry_visibility_smoke_filename(run):
    intent = _agent_entry_visibility_intent(run) or {}
    entry_label = str(intent.get("entryLabel") or "目标").strip() or "目标"
    target_page = str(intent.get("targetPage") or "目标页面").strip() or "目标页面"
    return clean_filename(f"00-{target_page}{entry_label}入口可见性短链路冒烟.yaml")


def _ensure_agent_entry_visibility_smoke_ref(run, refs, results):
    refs = list(refs or [])
    results = list(results or [])
    has_stable_smoke_candidate = False
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        score = ref.get("executableScore") if isinstance(ref.get("executableScore"), dict) else {}
        if not bool(score.get("smokeCandidate") or ref.get("smokeCandidate") or ref.get("runnerCandidate")):
            continue
        task_scores = [item for item in (score.get("taskScores") or []) if isinstance(item, dict)]
        max_action_count = max([int(item.get("actionCount") or 0) for item in task_scores] or [0])
        max_wait_count = max([int(item.get("waitCount") or 0) for item in task_scores] or [0])
        max_transition_count = max([int(item.get("transitionCount") or 0) for item in task_scores] or [0])
        min_assert_count = min([int(item.get("assertCount") or 0) for item in task_scores] or [0])
        high_replan_risk = any(str(item.get("replanRisk") or "") == "high" for item in task_scores)
        if (
            task_scores
            and max_action_count <= 12
            and max_wait_count <= 6
            and max_transition_count <= 2
            and min_assert_count >= 1
            and not high_replan_risk
        ):
            has_stable_smoke_candidate = True
            break
    if has_stable_smoke_candidate or not _agent_needs_entry_visibility_smoke(run):
        return refs, results
    module = clean_agent_module_name(run)
    file_name = _agent_entry_visibility_smoke_filename(run)
    path = safe_join(TASK_DIR, module, file_name)
    content = _agent_entry_visibility_smoke_yaml(run)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_text_file(path, content)
    check = validate_agent_yaml_content(content)
    executable_score = score_midscene_yaml_executable(content, generated=True)
    if not check.get("ok") or executable_score.get("executionLevel") != "executable" or not executable_score.get("smokeCandidate"):
        results.append({
            "module": module,
            "file": file_name,
            "path": path,
            **check,
            "executionLevel": executable_score.get("executionLevel"),
            "executableScore": executable_score,
            "autoGeneratedSmoke": True,
        })
        return refs, results
    ref = {
        "type": "file",
        "module": module,
        "file": file_name,
        "path": path,
        "content": "",
        "confirmed": True,
        "executionLevel": executable_score.get("executionLevel"),
        "executableScore": executable_score,
        "smokeCandidate": True,
        "runnerCandidate": True,
        "autoGeneratedSmoke": True,
        "reason": "生成结果缺少稳定首批冒烟候选，按需求硬约束补充入口可见性短链路",
    }
    result = {
        "module": module,
        "file": file_name,
        "path": path,
        **check,
        "executionLevel": executable_score.get("executionLevel"),
        "executableScore": executable_score,
        "smokeCandidate": True,
        "runnerCandidate": True,
        "autoGeneratedSmoke": True,
    }
    return [ref] + refs, [result] + results


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
        with AGENT_RUN_LOCK:
            snapshot = json.loads(json.dumps(run, ensure_ascii=False))
            runs = load_agent_runs()
            for i, item in enumerate(runs):
                if item.get("runId") == snapshot.get("runId"):
                    runs[i] = snapshot
                    break
            else:
                runs.insert(0, snapshot)
            save_agent_runs(runs[:200])
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


def _agent_high_confidence_failure_review(review, threshold=0.8):
    if not isinstance(review, dict):
        return False
    try:
        return float(review.get("confidence") or 0) >= float(threshold)
    except Exception:
        return False


def _agent_text_has_concrete_environment_evidence(value):
    """Recognize infrastructure evidence without treating a wall-clock timeout as proof."""
    text = str(value or "").lower()
    concrete_terms = (
        "neither android_home nor android_sdk_root", "android_sdk_root environment variable",
        "unable to get connected android device list", "no devices/emulators found", "device unauthorized",
        "device offline", "device disconnected", "adb offline", "adb disconnected",
        "runner offline", "runner disconnected", "model service", "gateway unavailable", "gateway timeout",
        "model request was aborted", "model request aborted", "connection refused", "network unreachable",
        "dns resolution", "econnreset", "socket hang up", "http 502", "http 503", "http 504",
        "安装失败", "设备离线", "设备断开", "设备未授权", "runner 离线", "runner 断开",
        "模型服务", "模型请求中止", "网关不可用", "网关超时", "网络不可达", "连接拒绝",
    )
    return any(term in text for term in concrete_terms)


def _agent_failure_review_has_concrete_environment_evidence(review):
    """A bare task timeout is not enough to lock the failure as infrastructure."""
    if not isinstance(review, dict) or not _agent_high_confidence_failure_review(review):
        return False
    if _agent_failure_type_from_review(review) != "ENV_ISSUE":
        return False
    text = " ".join(str(review.get(key) or "") for key in (
        "reason", "evidence", "summary", "recommendation", "category", "subCategory", "sub_category",
    ))
    return _agent_text_has_concrete_environment_evidence(text)


def _agent_failed_item_has_concrete_environment_evidence(item):
    if not isinstance(item, dict):
        return False
    if _agent_failure_review_has_concrete_environment_evidence(item.get("failureReview") or item.get("failure_review") or {}):
        return True
    raw = "\n".join(str(item.get(key) or "") for key in (
        "error", "failureReason", "stdoutTail", "stdout_tail", "stderrTail", "stderr_tail", "summaryText", "summary_text",
    ))
    return _agent_text_has_concrete_environment_evidence(raw)


def _agent_job_failure_reason(job):
    failure_review = job.get("failure_review") or job.get("failureReview") or {}
    if isinstance(failure_review, dict):
        review_reason = str(failure_review.get("reason") or "").strip()
        review_type = _agent_failure_type_from_review(failure_review)
        if review_reason and review_type and _agent_high_confidence_failure_review(failure_review):
            prefix = f"{review_type}：" if review_type else "Runner 复核："
            return f"{prefix}{_agent_job_log_tail(review_reason)}"
    summary_excerpt = _agent_summary_error_excerpt(
        _agent_job_field(job, "summary_text", "summaryText") or job.get("summary")
    )
    if summary_excerpt:
        failure_type = _agent_job_failure_type(summary_excerpt)
        prefix = f"{failure_type}：" if failure_type else ""
        return f"{prefix}Midscene 摘要: {summary_excerpt}"
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


def _agent_summary_error_excerpt(summary):
    if not summary:
        return ""
    data = summary
    if isinstance(summary, str):
        try:
            data = json.loads(summary)
        except Exception:
            data = summary
    if isinstance(data, dict):
        results = data.get("results") or []
        if isinstance(results, list):
            for result in results:
                if not isinstance(result, dict):
                    continue
                error = str(result.get("error") or "").strip()
                if error:
                    return _agent_job_error_excerpt(error)
        error = str(data.get("error") or data.get("message") or "").strip()
        if error:
            return _agent_job_error_excerpt(error)
    return _agent_job_error_excerpt(str(summary or ""))


def _agent_job_failure_type(text):
    blob = str(text or "")
    lowered = blob.lower()
    if (
        "neither android_home nor android_sdk_root" in lowered
        or "unable to get connected android device list" in lowered
        or "android_sdk_root environment variable" in lowered
        or "android_home" in lowered and "environment variable" in lowered
    ):
        return "ENV_ISSUE"
    if any(term in lowered for term in (
        "model request was aborted", "model request aborted", "模型服务", "model service",
        "service unavailable", "gateway timeout", "econnreset", "etimedout",
    )):
        return "ENV_ISSUE"
    if (
        any(term in blob for term in ("实际文案", "实际文本", "实际显示", "实际展示"))
        and any(term in lowered for term in (
            "不严格等于", "不等于", "不一致", "不相符", "does not equal", "not equal", "mismatch",
        ))
    ):
        return "PRODUCT_BUG"
    if "replanned 5 times" in lowered or "replanningcyclelimit" in lowered:
        return "Midscene 重规划超限"
    if any(term in lowered for term in (
        "invalid_enum_value",
        "invalid_type",
        "invalid parameters",
        "invalid parameter",
        "expected 'down' | 'up' | 'right' | 'left'",
        "expected number, received string",
        "expected number, received",
    )):
        return "YAML 动作参数不兼容"
    if "timeout after 300s" in lowered:
        return "Runner 单任务超时"
    if "failed to locate element" in lowered:
        return "元素定位失败"
    if "waitfor timeout" in lowered or "assertion failed" in lowered:
        if any(term in blob for term in ("并未出现", "未出现", "无法确认", "陈述为假", "StatementIsTruthy", "当前页面", "截图内容")):
            return "断言/页面状态不匹配"
        return "等待目标超时"
    if "adb" in lowered and ("device" in lowered or "offline" in lowered):
        return "ENV_ISSUE"
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
        failure_review = job.get("failure_review") or job.get("failureReview") or {}
        review_failure_type = _agent_failure_type_from_review(failure_review)
        inferred_failure_type = _agent_job_failure_type(raw_text)
        trusted_review_type = review_failure_type if _agent_high_confidence_failure_review(failure_review) else ""
        failure_type = trusted_review_type or inferred_failure_type
        reasons.append({
            "jobId": _agent_job_field(job, "job_id", "jobId"),
            "target": _agent_job_failure_target(job),
            "reason": reason,
            "failureType": failure_type,
            "failureReview": failure_review if isinstance(failure_review, dict) else {},
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


def _ai_gateway_post(path, payload, timeout=30, include_error=False):
    """对 AI Gateway 发起 POST 请求，可为恢复链路保留可审计错误。"""
    url = AI_GATEWAY_URL.rstrip("/") + path
    try:
        request_payload = dict(payload) if isinstance(payload, dict) else {}
        request_payload.setdefault("timeoutMs", max(5000, (safe_int(timeout, 30) - 2) * 1000))
        resp = http_client.post_json(url, request_payload, timeout=timeout)
        parsed = resp.json(default={})
        if resp.ok:
            return parsed
        if include_error:
            parsed = parsed if isinstance(parsed, dict) else {}
            detail = str(
                parsed.get("error") or parsed.get("message") or resp.body or f"HTTP {resp.status}"
            ).strip()
            return {
                **parsed,
                "error": detail[:1000] or f"AI Gateway HTTP {resp.status}",
                "errorType": "http_error",
                "httpStatus": int(resp.status or 0),
            }
        return None
    except Exception as exc:
        if include_error:
            return {
                "error": str(exc)[:1000] or exc.__class__.__name__,
                "errorType": "request_error",
                "exceptionType": exc.__class__.__name__,
            }
        return None


def _agent_model_config(run):
    """Return the model selected when this Agent run was created."""
    run = run if isinstance(run, dict) else {}
    provider_id = str(run.get("modelProviderId") or run.get("aiProviderId") or "").strip()
    model = str(run.get("aiModel") or run.get("model") or "").strip()
    return {
        key: value
        for key, value in {"providerId": provider_id, "model": model}.items()
        if value
    }


def _agent_ai_route_payload(run, has_images=False):
    model_config = _agent_model_config(run)
    payload = {
        "modelConfig": model_config,
        "providerId": model_config.get("providerId") or "",
        "model": model_config.get("model") or "",
    }
    if has_images:
        payload["fallbackModelConfig"] = {
            "providerId": str(
                os.getenv("MIDSCENE_AI_GATEWAY_VISION_FALLBACK_PROVIDER_ID", "qwen_plus")
            ).strip() or "qwen_plus",
            "model": dashscope_vl_model(),
        }
    return payload


def _agent_ai_response_model_trace(run, response):
    response = response if isinstance(response, dict) else {}
    selected = _agent_model_config(run)
    return {
        "selectedProviderId": selected.get("providerId") or "",
        "selectedModel": selected.get("model") or "",
        "providerId": response.get("providerId") or selected.get("providerId") or "",
        "model": response.get("model") or selected.get("model") or "",
        "fallbackUsed": bool(response.get("fallbackUsed")),
        "fallbackIndex": safe_int(response.get("fallbackIndex"), 0),
        "fallbackReason": str(response.get("fallbackReason") or "")[:500],
        "source": "ai_gateway",
    }


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
        business_flows = [item for item in (payload.get("businessFlows") or []) if isinstance(item, dict)]
        candidate_constraint = artifacts.get("requirementCoverageCandidates")
        if not isinstance(candidate_constraint, dict):
            candidate_constraint = constraint
        required_flows = _agent_plan_constraint_flows(candidate_constraint)
        plan_text = _normalize_business_flow_text(json.dumps(business_flows, ensure_ascii=False))
        missing_branches = []
        for item in required_flows:
            branch = str(item.get("branch") or item.get("name") or "").strip()
            if branch and not _agent_plan_branch_present(branch, plan_text):
                missing_branches.append(branch)
        generic_only = bool(business_flows) and all(
            set(_agent_plan_text_list(item.get("steps"))).issubset(set(AGENT_DEFAULT_BUSINESS_FLOW))
            for item in business_flows
        )
        ai_generated = bool(payload.get("aiGenerated"))
        trusted_source = str(payload.get("source") or "") == "platform_mindmap_ai"
        passed = bool(steps) and bool(business_flows) and not missing_branches and not generic_only and ai_generated and trusted_source
        if not ai_generated or not trusted_source:
            reason = "PLAN 必须来自平台 MM skills 的真实 AI 结果，规则兜底不能冒充成功"
        elif missing_branches:
            reason = "AI 计划缺少需求业务分支：" + "、".join(missing_branches)
        elif generic_only:
            reason = "计划只有平台通用生命周期，未展开业务步骤"
        else:
            reason = "AI 计划已展开独立业务分支并覆盖原始需求候选" if passed else "计划缺少业务分支或业务步骤"
        return _record_agent_quality_gate(
            run,
            "plan_grounding",
            passed,
            reason,
            stepCount=len(steps),
            businessFlowCount=len(business_flows),
            missingBranches=missing_branches,
            aiGenerated=ai_generated,
            fallbackUsed=bool(payload.get("fallbackUsed")),
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
            reasons.append("复用依据未命中 AI 业务计划关键词")
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
业务上下文（PLAN 前是未验证候选，PLAN 后是 AI 业务分支）：{business_constraint.get("businessFlowText", "")}

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
                "model": run.get("aiModel") or run.get("model") or "",
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
                    normalized["modelTrace"] = _agent_ai_response_model_trace(run, gw_result)
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
        explicit_model = bool(_agent_model_config(run))
        api_key = "" if explicit_model else dashscope_api_key(required=False)
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
            reason = (
                "显式选定模型已由 Gateway 完成允许的降级，禁止再静默直连其他模型"
                if explicit_model else "未配置 DASHSCOPE_API_KEY"
            )
            _record_agent_ai_decision(run, "analyze_goal", "dashscope", False, reason)
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
            "parent_run_id": run.get("runId", ""),
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
            "parent_run_id": run.get("runId", ""),
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
            parent_run_id=run.get("runId", ""),
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

def _agent_plan_text_list(value, limit=12):
    values = value if isinstance(value, list) else ([value] if value not in (None, "") else [])
    result = []
    for item in values:
        text = re.sub(r"^\s*\d+[.、)）]\s*", "", str(item or "")).strip()
        if not text or text in result:
            continue
        result.append(text[:180])
        if len(result) >= limit:
            break
    return result


def _agent_plan_requirement_text(run):
    run = run if isinstance(run, dict) else {}
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    source = artifacts.get("sourceContext") if isinstance(artifacts.get("sourceContext"), dict) else {}
    normalized = run.get("normalizedInput") if isinstance(run.get("normalizedInput"), dict) else {}
    source_inputs = normalized.get("sourceInputs") if isinstance(normalized.get("sourceInputs"), dict) else {}
    return str(
        source.get("requirementText")
        or normalized.get("requirementText")
        or source_inputs.get("requirementText")
        or normalized.get("text")
        or run.get("target")
        or ""
    ).strip()


def _agent_plan_constraint_flows(constraint):
    constraint = constraint if isinstance(constraint, dict) else {}
    flows = [item for item in (constraint.get("businessFlows") or []) if isinstance(item, dict)]
    if flows:
        return flows[:8]
    flat = _agent_plan_text_list(constraint.get("businessFlow"), limit=12)
    if not flat:
        return []
    return [{"id": "FLOW-001", "name": "业务流程候选", "branch": "", "steps": flat}]


def _agent_plan_branch_present(branch, text):
    branch = str(branch or "").strip()
    text = str(text or "")
    aliases = {
        "扫描复印": ("扫描复印", "复印扫描"),
        "复印扫描": ("扫描复印", "复印扫描"),
        "个人中心": ("个人中心", "我的"),
        "我的": ("我的", "个人中心"),
    }
    return bool(branch and any(alias in text for alias in aliases.get(branch, (branch,))))


def _agent_plan_constraint_branch_match(flow, constraint_flows):
    """Recover one source-defined branch when an AI scenario uses a generic feature label."""
    flow = flow if isinstance(flow, dict) else {}
    candidates = []
    for item in constraint_flows or []:
        if not isinstance(item, dict):
            continue
        branch = str(item.get("branch") or item.get("name") or "").strip()
        if branch and branch not in candidates:
            candidates.append(branch)
    if not candidates:
        return ""
    evidence_groups = (
        flow.get("name"),
        flow.get("requirementRefs") or flow.get("requirement_refs"),
        flow.get("steps"),
        flow.get("checks") or flow.get("assertions"),
    )
    for evidence in evidence_groups:
        text = _normalize_business_flow_text(json.dumps(evidence, ensure_ascii=False))
        matches = [branch for branch in candidates if _agent_plan_branch_present(branch, text)]
        if len(matches) == 1:
            return matches[0]
    return ""


def _normalize_agent_business_plan(value, run, constraint):
    if not isinstance(value, dict):
        return None, ["AI 计划不是 JSON 对象"]
    raw_flows = value.get("businessFlows") or value.get("business_flows") or value.get("flows") or []
    if not isinstance(raw_flows, list):
        return None, ["businessFlows 必须是数组"]
    flows = []
    issues = []
    required_flows = _agent_plan_constraint_flows(constraint)
    for index, item in enumerate(raw_flows[:8], start=1):
        if not isinstance(item, dict):
            continue
        steps = _agent_plan_text_list(item.get("steps") or item.get("flow"), limit=10)
        checks = _agent_plan_text_list(item.get("checks") or item.get("assertions"), limit=6)
        name = str(item.get("name") or item.get("branch") or f"业务分支 {index}").strip()
        if len(steps) < 2:
            issues.append(f"{name} 缺少完整业务步骤")
        if not checks:
            issues.append(f"{name} 缺少用户可见验收点")
        normalized_flow = {
            "id": str(item.get("id") or f"FLOW-{index:03d}")[:40],
            "name": name[:100],
            "branch": str(item.get("branch") or name)[:80],
            "preconditions": _agent_plan_text_list(item.get("preconditions"), limit=6),
            "steps": steps,
            "checks": checks,
            "requirementRefs": _agent_plan_text_list(item.get("requirementRefs") or item.get("requirement_refs"), limit=8),
            "evidence": _agent_plan_text_list(item.get("evidence"), limit=6),
        }
        matched_branch = _agent_plan_constraint_branch_match(normalized_flow, required_flows)
        if matched_branch:
            normalized_flow["branch"] = matched_branch[:80]
            normalized_flow["branchSource"] = "source_requirement_contract"
        flows.append(normalized_flow)
    if not flows:
        issues.append("AI 计划没有业务分支")

    combined = _normalize_business_flow_text(json.dumps(flows, ensure_ascii=False))
    missing_branches = []
    for item in required_flows:
        branch = str(item.get("branch") or item.get("name") or "").strip()
        if branch and not _agent_plan_branch_present(branch, combined):
            missing_branches.append(branch)
    if missing_branches:
        issues.append("缺少需求业务分支：" + "、".join(missing_branches))
    if flows and all(set(item.get("steps") or []).issubset(set(AGENT_DEFAULT_BUSINESS_FLOW)) for item in flows):
        issues.append("计划仍是平台通用生命周期，没有展开真实业务步骤")
    if issues:
        return None, issues

    plan = {
        "version": "agent-business-plan-v3",
        "source": "platform_mindmap_ai",
        "aiGenerated": True,
        "fallbackUsed": False,
        "objective": str(value.get("objective") or value.get("goal") or run.get("target") or "")[:300],
        "businessFlows": flows,
        "coverage": [item for item in (value.get("coverage") or []) if isinstance(item, dict)][:20],
        "assumptions": _agent_plan_text_list(value.get("assumptions"), limit=10),
        "unknowns": _agent_plan_text_list(value.get("unknowns"), limit=10),
        "executionStrategy": value.get("executionStrategy") if isinstance(value.get("executionStrategy"), dict) else {},
    }
    return plan, []


def _agent_plan_path_steps(value):
    if isinstance(value, list):
        values = value
    else:
        values = re.split(r"\s*(?:->|→|=>|＞|>)\s*", str(value or ""))
    return _agent_plan_text_list(values, limit=10)


def _agent_mm_plan_failure_reasons(payload):
    payload = payload if isinstance(payload, dict) else {}
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
    review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
    scenarios = [item for item in (payload.get("scenarios") or []) if isinstance(item, dict)]
    reasons = []
    core_ai_failure = review.get("core_ai_failure") if isinstance(review.get("core_ai_failure"), dict) else {}
    if core_ai_failure:
        stage = str(core_ai_failure.get("stage") or "core_ai").strip()
        reason = str(core_ai_failure.get("reason") or "未产出 AI 结果").strip()
        reasons.append(f"{stage} 核心 AI 节点失败：{reason[:180]}")
    if analysis.get("fallback_reason"):
        reasons.append("requirement_analyzer 未产出 AI 结果：" + str(analysis.get("fallback_reason"))[:180])
    if not scenarios:
        reasons.append("scenario_designer 未产出业务场景")
    elif any(str(item.get("source") or "").startswith("local_fallback") for item in scenarios):
        reason = next((str(item.get("fallback_reason") or "") for item in scenarios if item.get("fallback_reason")), "")
        reasons.append("scenario_designer 使用了本地规则兜底" + (f"：{reason[:180]}" if reason else ""))
    skill_pipeline = str(review.get("skill_pipeline") or "")
    if skill_pipeline.startswith("deterministic_"):
        reasons.append("MM 规划错误进入确定性快路径")
    elif not skill_pipeline.startswith("requirement_analyzer.v1 -> scenario_designer.v1"):
        reasons.append("MM 规划未经过 requirement_analyzer 与 scenario_designer")
    if review.get("skill_pipeline_error"):
        reasons.append("MM skills 链路失败：" + str(review.get("skill_pipeline_error"))[:180])
    return list(dict.fromkeys(item for item in reasons if item))


def _agent_mm_case_for_scenario(cases, scenario):
    feature = str(scenario.get("feature") or "").strip()
    requirement_point = str(
        scenario.get("requirement_point")
        or scenario.get("requirementPoint")
        or ""
    ).strip()
    for case in cases:
        blob = _normalize_business_flow_text(json.dumps(case, ensure_ascii=False))
        if requirement_point and requirement_point in blob:
            return case
        if feature and feature in blob:
            return case
    return {}


def _agent_mm_visual_status(review):
    review = review if isinstance(review, dict) else {}
    batch_text = str(review.get("mindmap_visual_batches") or "").strip()
    match = re.fullmatch(r"\s*(\d+)\s*/\s*(\d+)\s*", batch_text)
    batches_done = safe_int(match.group(1), 0) if match else 0
    batches_total = safe_int(match.group(2), 0) if match else 0
    error = str(review.get("visual_refine_error") or "").strip()
    batch_results = [
        item for item in (review.get("mindmap_visual_batch_results") or [])
        if isinstance(item, dict)
    ]
    batches_attempted = safe_int(
        review.get("mindmap_visual_batches_attempted"),
        len([item for item in batch_results if str(item.get("status") or "") != "not_attempted"]),
    )
    grounded = bool(review.get("mindmap_visual_grounded") or batches_done > 0)
    attempted = bool(batch_text or grounded or error or batches_attempted)
    if match and batches_total > 0:
        completed = batches_done >= batches_total and not error
    else:
        completed = grounded and not error
    if completed:
        status = "completed"
    elif grounded:
        status = "partial"
    elif attempted:
        status = "failed"
    else:
        status = "not_requested"
    return {
        "attempted": attempted,
        "grounded": grounded,
        "completed": completed,
        "status": status,
        "batchesDone": batches_done,
        "batchesTotal": batches_total,
        "batchesAttempted": batches_attempted,
        "batchResults": batch_results[:40],
        "error": error,
    }


def _agent_business_plan_from_mindmap(run, mindmap_result, requirement_candidates):
    result = mindmap_result if isinstance(mindmap_result, dict) else {}
    payload = result.get("cases") if isinstance(result.get("cases"), dict) else {}
    failure_reasons = _agent_mm_plan_failure_reasons(payload)
    if failure_reasons:
        return None, failure_reasons
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
    review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
    scenarios = [item for item in (payload.get("scenarios") or []) if isinstance(item, dict)]
    cases = [item for item in (payload.get("cases") or []) if isinstance(item, dict)]
    source_context = (run.get("artifacts") or {}).get("sourceContext") or {}
    visual_batches = str(review.get("mindmap_visual_batches") or "")
    visual_status = _agent_mm_visual_status(review)
    visual_attempted = visual_status["attempted"]
    visual_grounded = visual_status["grounded"]
    flows = []
    for index, scenario in enumerate(scenarios[:8], start=1):
        case = _agent_mm_case_for_scenario(cases, scenario)
        branch = str(
            scenario.get("feature")
            or scenario.get("branch")
            or scenario.get("requirement_point")
            or scenario.get("requirementPoint")
            or ""
        ).strip()
        steps = _agent_plan_text_list(scenario.get("steps"), limit=10)
        if len(steps) < 2:
            steps = _agent_plan_path_steps(
                scenario.get("business_path")
                or scenario.get("businessPath")
                or case.get("business_path")
                or case.get("businessPath")
            )
        if len(steps) < 2:
            steps = _agent_plan_text_list(case.get("steps"), limit=10)
        checks = _agent_plan_text_list(
            scenario.get("assertions")
            or scenario.get("expected")
            or scenario.get("expected_result")
            or case.get("assertions")
            or case.get("expected_result"),
            limit=8,
        )
        requirement_refs = _agent_plan_text_list(
            scenario.get("requirement_point")
            or scenario.get("requirementPoint")
            or case.get("requirement_point")
            or case.get("coverage"),
            limit=6,
        )
        evidence = ["requirement_analyzer", "scenario_designer"]
        if review.get("yaml_reference_examples"):
            evidence.append("trusted_baseline_reranker")
        if visual_grounded:
            evidence.append("figma_or_screenshot_soft_reference")
        flows.append({
            "id": str(scenario.get("id") or scenario.get("scenario_id") or f"FLOW-{index:03d}"),
            "name": str(scenario.get("scenario") or scenario.get("name") or branch or f"业务分支 {index}"),
            "branch": branch,
            "preconditions": _agent_plan_text_list(
                scenario.get("preconditions") or case.get("preconditions"),
                limit=6,
            ),
            "steps": steps,
            "checks": checks,
            "requirementRefs": requirement_refs,
            "evidence": evidence,
        })
    normalized_plan, issues = _normalize_agent_business_plan({
        "objective": str(analysis.get("summary") or run.get("target") or ""),
        "businessFlows": flows,
        "coverage": analysis.get("coverage_matrix") or [],
        "assumptions": analysis.get("assumptions") or [],
        "unknowns": (
            _agent_plan_text_list(analysis.get("questions"), limit=6)
            + _agent_plan_text_list(analysis.get("missing_inputs"), limit=6)
            + _agent_plan_text_list(analysis.get("blockers"), limit=6)
        ),
        "executionStrategy": {},
    }, run, requirement_candidates)
    if not normalized_plan:
        return None, issues
    smoke_flow_ids = []
    for flow in normalized_plan.get("businessFlows") or []:
        flow_blob = _normalize_business_flow_text(json.dumps(flow, ensure_ascii=False))
        if any(case.get("smoke") and any(
            token and token in flow_blob
            for token in (
                str(case.get("requirement_point") or "").strip(),
                str(case.get("coverage") or "").strip(),
                str(case.get("title") or "").strip(),
            )
        ) for case in cases):
            smoke_flow_ids.append(flow.get("id"))
    if not smoke_flow_ids:
        smoke_flow_ids = [item.get("id") for item in (normalized_plan.get("businessFlows") or [])[:3]]
    all_flow_ids = [item.get("id") for item in normalized_plan.get("businessFlows") or []]
    skill_model_traces = review.get("skill_model_traces") if isinstance(review.get("skill_model_traces"), dict) else {}
    plan_fallback_used = any(
        isinstance(trace, dict) and trace.get("fallbackUsed") is True
        for trace in skill_model_traces.values()
    )
    normalized_plan.update({
        "version": "agent-business-plan-v3",
        "source": "platform_mindmap_ai",
        "aiGenerated": True,
        "fallbackUsed": plan_fallback_used,
        "providerId": run.get("modelProviderId") or run.get("aiProviderId") or "",
        "model": run.get("aiModel") or run.get("model") or "",
        "executionStrategy": {
            "smokeFlowIds": smoke_flow_ids[:3],
            "remainingFlowIds": [item for item in all_flow_ids if item not in smoke_flow_ids[:3]],
            "reason": "沿用平台 MM skills 的显式 smoke 选择；平台只校验数量、覆盖和执行安全",
        },
        "mindmapTrace": {
            "caseSetId": result.get("case_set_id") or "",
            "skillPipeline": review.get("skill_pipeline") or "",
            "skillModelTraces": copy.deepcopy(skill_model_traces),
            "scenarioCount": len(scenarios),
            "caseCount": len(cases),
            "visualBatches": visual_batches,
            "visualBatchesDone": visual_status["batchesDone"],
            "visualBatchesTotal": visual_status["batchesTotal"],
            "visualBatchesAttempted": visual_status["batchesAttempted"],
            "visualBatchResults": visual_status["batchResults"],
            "visualImagesGrounded": review.get("mindmap_visual_images_grounded") or 0,
            "visualAttempted": visual_attempted,
            "visualCompleted": visual_status["completed"],
            "visualStatus": visual_status["status"],
            "visualSoftReference": True,
            "preparedFigmaReused": bool(review.get("prepared_figma_context_reused")),
            "trustedBaselines": review.get("yaml_reference_examples") or [],
        },
        "goalAnalysis": {
            "businessGoals": analysis.get("business_goals") or [],
            "entryPoints": analysis.get("entry_points") or [],
            "requirementPoints": analysis.get("requirement_points") or [],
            "confidence": analysis.get("confidence") or "",
            "readiness": analysis.get("readiness_level") or "",
            "aiSource": "requirement_analyzer.v1",
        },
        "visualReference": {
            "figmaPageCount": len(source_context.get("figmaUsedPages") or []),
            "figmaImageCount": int(source_context.get("figmaImageCount") or 0),
            "sentToAiForJudgement": visual_attempted,
            "aiJudgementCompleted": visual_status["completed"],
            "aiJudgementStatus": visual_status["status"],
            "hardGate": False,
            "error": visual_status["error"],
        },
    })
    return normalized_plan, []


def _agent_mindmap_plan_request(run, source_context, attempt=1, validation_issues=None):
    source_text = _agent_plan_requirement_text(run)
    files = _agent_source_files_for_generation(run)
    if source_text and not files:
        files = [{
            "name": "agent-requirement.md",
            "type": "text/markdown",
            "kind": "requirement_text",
            "content": source_text,
            "source": "agent-source-context",
        }]
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    requirement_contract = artifacts.get("requirementCoverageCandidates")
    if not isinstance(requirement_contract, dict):
        requirement_contract = {}
    return {
        "case_set_id": f"agent-plan-{_agent_safe_run_file_id(run)}-{attempt}",
        "title": str(run.get("target") or source_context.get("target") or "AI Agent 业务计划"),
        "module": clean_agent_module_name(run),
        "files": files,
        "figma_url": source_context.get("figmaUrl") or "",
        "figmaUrl": source_context.get("figmaUrl") or "",
        "prepared_figma_context": _agent_prepared_figma_context_from_source(source_context),
        "requirementCoverageContract": requirement_contract,
        "app_package": _agent_app_package(run),
        "appName": run.get("appName") or "",
        "use_knowledge_context": False,
        "mindmap_mode": "full",
        "requireAiPlanning": True,
        "useYamlBaselineContext": True,
        "planValidationIssues": _agent_plan_text_list(validation_issues, limit=8),
        "modelProviderId": run.get("modelProviderId") or run.get("aiProviderId") or "",
        "aiModel": run.get("aiModel") or run.get("model") or "",
        "source": "agent_plan",
    }


def _tool_agent_plan(run):
    """Build the Agent plan from the platform MM skills after source preparation."""
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "analyze_goal",
        "category": "AI",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {
            "target": run.get("target", ""),
            "requirement": _agent_plan_requirement_text(run),
            "scope": run.get("scope", "smoke"),
            "planner": "platform_mindmap_ai",
        },
    }
    artifacts = run.setdefault("artifacts", {})
    requirement_candidates = _ensure_business_flow_constraint(run)
    ai_health = _probe_agent_ai_health(run)
    source_context = artifacts.get("sourceContext") if isinstance(artifacts.get("sourceContext"), dict) else {}
    try:
        if not source_context:
            raise RuntimeError("PREPARE_SOURCE 未产出资料上下文，不能开始 AI 业务规划")
        if not ai_health.get("ready"):
            raise RuntimeError("AI 服务未就绪，不能用规则计划冒充 AI 计划")
        from task_server.services.yaml_service import generate_mindmap_from_request, update_generate_job

        plan = None
        plan_issues = []
        mindmap_result = {}
        plan_step = next((item for item in (run.get("steps") or []) if item.get("step") == "PLAN"), None)
        for attempt in range(1, 3):
            progress_job_id = f"agent-plan-{_agent_safe_run_file_id(run)}-{attempt}"
            update_generate_job(
                progress_job_id,
                status="running",
                type="agent_mindmap_plan",
                progress=5,
                step="AI 业务规划",
                message=f"正在复用平台 MM skills 生成业务计划（第 {attempt}/2 次）",
                run_id=run.get("runId", ""),
                timeout_seconds=900,
            )
            stop_event = threading.Event()
            watcher = None
            if plan_step:
                watcher = threading.Thread(
                    target=_watch_agent_generation_progress,
                    args=(run, plan_step, progress_job_id, stop_event),
                    daemon=True,
                )
                watcher.start()
            try:
                mindmap_result = generate_mindmap_from_request(
                    _agent_mindmap_plan_request(
                        run,
                        source_context,
                        attempt=attempt,
                        validation_issues=plan_issues if attempt > 1 else None,
                    ),
                    job_id=progress_job_id,
                )
            finally:
                stop_event.set()
                if watcher:
                    watcher.join(timeout=0.5)
            plan, plan_issues = _agent_business_plan_from_mindmap(
                run,
                mindmap_result,
                requirement_candidates,
            )
            update_generate_job(
                progress_job_id,
                status="success" if plan else "failed",
                ok=bool(plan),
                progress=100 if plan else 99,
                step="AI 业务规划完成" if plan else "AI 业务规划失败",
                message=(
                    f"MM skills 已生成 {len(plan.get('businessFlows') or [])} 条业务分支"
                    if plan else "；".join(plan_issues)[:300]
                ),
            )
            if plan or _agent_run_cancel_requested(run):
                break
        artifacts["mindmapPlan"] = {
            "source": "platform_mindmap_ai",
            "caseSetId": mindmap_result.get("case_set_id") or "",
            "cases": mindmap_result.get("cases") or {},
            "summary": mindmap_result.get("summary") or {},
            "coverageAudit": mindmap_result.get("coverageAudit") or {},
            "issues": plan_issues,
        }
        # PLAN owns the first visual-AI attempt. Persist its real batch outcome
        # immediately so a later generation failure cannot leave the report pending.
        artifacts["visualReferenceReport"] = _agent_visual_reference_report(run, mindmap_result)
        if not plan:
            failure = {
                "version": "agent-business-plan-v3",
                "source": "platform_mindmap_ai",
                "aiGenerated": False,
                "fallbackUsed": False,
                "status": "failed",
                "issues": plan_issues or ["平台 MM skills 未产出可验证的 AI 业务计划"],
                "aiHealth": ai_health,
                "requirementCandidates": _compact_business_flow_constraint(requirement_candidates),
            }
            artifacts["plan"] = failure
            _record_agent_ai_decision(run, "plan", "platform_mindmap_ai", False, "；".join(failure["issues"])[:500])
            raise RuntimeError("AI 业务规划失败：" + "；".join(failure["issues"])[:400])

        plan["steps"] = [
            f"{item.get('name') or item.get('branch') or item.get('id')}：{' -> '.join(item.get('steps') or [])}"
            for item in (plan.get("businessFlows") or [])
        ]
        plan["platformLifecycle"] = list(AGENT_PLATFORM_LIFECYCLE_STEPS)
        plan["mode"] = run.get("mode", "AUTO_SAFE")
        plan["target"] = run.get("target", "")
        plan["riskLevel"] = run.get("riskLevel", "low")
        plan["aiHealth"] = ai_health
        plan["requirementCandidates"] = _compact_business_flow_constraint(requirement_candidates)
        plan["dispatchPolicy"] = {
            "decisionOwner": "AI",
            "aiDecisions": [
                "MM requirement_analyzer 理解需求",
                "MM scenario_designer 决定业务分支、层级和场景",
                "可信相似基线由 AI 重排并作为路径经验",
                "Figma/截图由 visual_grounder 作为软参考校准",
                "冒烟优先级和剩余批次由 AI 推荐",
            ],
            "safetyGates": [
                "原始需求覆盖审计",
                "YAML 强校验",
                "固定 Runner/设备约束",
                "平台级高风险确认",
            ],
            "note": "规则只校验 AI 是否漏覆盖和是否越过安全边界，不预先编排业务路径。",
        }
        plan["qualityGate"] = _evaluate_agent_quality_gate(run, "plan", plan)
        if not plan["qualityGate"].get("passed"):
            artifacts["plan"] = plan
            raise RuntimeError("AI 业务计划未通过覆盖门禁：" + str(plan["qualityGate"].get("reason") or ""))
        artifacts["plan"] = plan
        strict_constraint = _ensure_business_flow_constraint(run)
        plan["businessFlowConstraint"] = _compact_business_flow_constraint(strict_constraint)
        call["status"] = "SUCCESS"
        call["outputSummary"] = (
            f"平台 MM skills 已生成 {len(plan.get('businessFlows') or [])} 条 AI 业务分支计划；"
            f"Figma {plan.get('visualReference', {}).get('figmaPageCount', 0)} 页/"
            f"{plan.get('visualReference', {}).get('figmaImageCount', 0)} 图为软参考"
        )
        _record_agent_ai_decision(
            run,
            "plan",
            "platform_mindmap_ai",
            True,
            call["outputSummary"],
            flowCount=len(plan.get("businessFlows") or []),
            model=plan.get("model") or "",
        )
    except Exception as exc:
        call["status"] = "FAILED"
        call["error"] = str(exc)[:1000]
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
                "model": model,
            }, timeout=20)
            if gw_result and isinstance(gw_result, dict):
                content = gw_result.get("content", "")
                if content:
                    parsed = _parse_ai_match_response(content, all_yamls)
                    if parsed:
                        parsed["ai_source"] = "ai_gateway"
                        parsed["modelTrace"] = _agent_ai_response_model_trace({
                            "modelProviderId": provider_id,
                            "aiModel": model,
                        }, gw_result)
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
    if provider_id or model:
        _ai_errors.append("显式选定模型已由 Gateway 完成允许的降级，禁止再静默直连其他模型")
        return {"_ai_errors": _ai_errors}
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
    prompt = f"""你是自动化测试平台的 Case Retrieval 语义判定器。规则召回已经给出候选 YAML，但规则分可能不准；请你根据测试目标、AI 业务计划、输入资料和 YAML 内容判断是否应该复用已有用例。

测试目标：{target}
应用：{app_name}
执行范围：{scope or "auto"}
AI 业务计划（必须优先覆盖；若仍是未验证候选，只能作为召回提示）：
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
6. 如果候选 YAML 没有覆盖 AI 业务计划中的核心分支，应返回 generate_draft 或 wait_confirm，不要强行复用。

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
                "model": model,
            }, timeout=25)
            content = gw_result.get("content", "") if isinstance(gw_result, dict) else ""
            parsed = _parse_ai_case_retrieval_response(content, candidates)
            if parsed:
                parsed["ai_source"] = "ai_gateway"
                parsed["modelTrace"] = _agent_ai_response_model_trace({
                    "modelProviderId": provider_id,
                    "aiModel": model,
                }, gw_result)
                return parsed
            errors.append(f"AI Gateway 返回无法解析: {str(content)[:200]}")
        else:
            errors.append("AI Gateway 不可用")
    except Exception as e:
        errors.append(f"AI Gateway 异常: {str(e)[:200]}")

    if provider_id or model:
        errors.append("显式选定模型已由 Gateway 完成允许的降级，禁止再静默直连其他模型")
        return {"_ai_errors": errors}
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
        step[action] = "input keyevent 3"
        return {"changed": "replace unsafe runAdbShell recent-task cleanup with home key"}
    if action == "runAdbShell" and isinstance(value, str):
        compact = re.sub(r"\s+", "", value).lower()
        if "inputkeyevent187" in compact or ("wmsize" in compact and "inputswipe" in compact) or value.count("input swipe") >= 2:
            step[action] = "input keyevent 3"
            return {"changed": "replace heavy runAdbShell recent-task cleanup with home key"}
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
        step.setdefault("timeout", loading_wait_timeout_for_context(payload))
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
    gate_repair = repair_generated_yaml_executable_gate_issues(str(yaml_text or ""))
    base_changes = []
    if gate_repair.get("changed"):
        yaml_text = gate_repair.get("content") or yaml_text
        base_changes.extend(list(gate_repair.get("changes") or []))
    try:
        parsed = pyyaml.safe_load(str(yaml_text or ""))
    except Exception:
        if base_changes:
            return {"changed": True, "content": yaml_text, "changes": base_changes}
        return {"changed": False, "content": yaml_text, "changes": []}
    platform, tasks = extract_midscene_tasks(parsed)
    if not tasks:
        if base_changes:
            return {"changed": True, "content": yaml_text, "changes": base_changes}
        return {"changed": False, "content": yaml_text, "changes": []}
    changes = list(base_changes)
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
                    step.setdefault("timeout", loading_wait_timeout_for_context(wait_prompt))
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
                wait_step = {"aiWaitFor": wait_text, "timeout": loading_wait_timeout_for_context(wait_text)}
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
        "条件式 aiTap",
        "复合 ai 动作",
        "百度网盘点击后仍",
        "生成用例动作",
        "等待链路",
        "交互和等待组合偏长",
        "重规划",
        "动作前缀",
        "${...}",
        "shell 参数展开",
    ))


def _agent_executable_gate_reason_lines(scored_ref, dry_compact=None, extra_reasons=None):
    score = scored_ref.get("executableScore") if isinstance(scored_ref.get("executableScore"), dict) else {}
    lines = []
    lines.extend(str(item) for item in (score.get("reasons") or []) if str(item or "").strip())
    if isinstance(dry_compact, dict):
        lines.extend(str(item) for item in (dry_compact.get("errors") or []) if str(item or "").strip())
        lines.extend(str(item) for item in (dry_compact.get("warnings") or []) if str(item or "").strip())
    lines.extend(str(item) for item in (extra_reasons or []) if str(item or "").strip())
    return lines[:24]


def _agent_ref_needs_ai_execution_rewrite(scored_ref, dry_compact=None, extra_reasons=None):
    score = scored_ref.get("executableScore") if isinstance(scored_ref.get("executableScore"), dict) else {}
    level = score.get("executionLevel") or scored_ref.get("executionLevel") or ""
    dry_ok = True if dry_compact is None else bool(dry_compact.get("ok"))
    if level == "executable" and dry_ok:
        return False
    return should_ai_rewrite_for_executable_gate(
        _agent_executable_gate_reason_lines(scored_ref, dry_compact, extra_reasons)
    )


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
    normalized_content = ensure_midscene_platform_root(content, platform=run.get("platform", "android"))
    platform_root_changed = normalized_content != content
    if platform_root_changed:
        repaired_ref = {**ref}
        _agent_write_repaired_yaml_ref(repaired_ref, normalized_content)
        ref = repaired_ref
        content = normalized_content
        scored_before = _score_agent_yaml_ref_for_execution(run, ref)
    before_score = scored_before.get("executableScore") if isinstance(scored_before.get("executableScore"), dict) else {}
    before_reasons = list(before_score.get("reasons") or [])[:12]
    if (
        not _agent_ref_needs_local_execution_repair(scored_before)
        and not _agent_ref_needs_ai_execution_rewrite(scored_before)
    ):
        if platform_root_changed:
            auto_repair = {
                "type": "local_yaml_execution_repair",
                "reason": reason,
                "changed": True,
                "changes": ["wrap root tasks into android.tasks for Runner dry-run"],
                "ok": before_score.get("executionLevel") == "executable",
                "before": {"executionLevel": "root_tasks", "reasons": ["Runner requires android or ios root"]},
                "after": {
                    "executionLevel": before_score.get("executionLevel"),
                    "dryRunOk": True,
                    "reasons": list(before_score.get("reasons") or [])[:6],
                },
            }
            scored_before["autoRepair"] = auto_repair
            _agent_update_yaml_ref_artifact(run, ref, scored_before)
            artifacts = (run or {}).setdefault("artifacts", {})
            repairs = artifacts.get("yamlExecutionRepairs") if isinstance(artifacts.get("yamlExecutionRepairs"), list) else []
            repairs.append({
                "module": scored_before.get("module") or "",
                "file": scored_before.get("file") or "",
                "path": scored_before.get("path") or "",
                **auto_repair,
            })
            artifacts["yamlExecutionRepairs"] = repairs[-50:]
            return scored_before, auto_repair
        return scored_before, None

    repaired = _agent_repair_missing_interaction_followups(content)
    repaired_ref = {**ref}
    current_content = content
    local_changed = bool(repaired.get("changed"))
    if local_changed:
        current_content = repaired.get("content") or content
        _agent_write_repaired_yaml_ref(repaired_ref, current_content)
    scored_after = _score_agent_yaml_ref_for_execution(run, repaired_ref)
    dry_after = _agent_yaml_dry_run_for_ref(run, repaired_ref)
    dry_compact = _compact_yaml_dry_run_result(dry_after)
    after_score = scored_after.get("executableScore") if isinstance(scored_after.get("executableScore"), dict) else {}
    after_reasons = list(after_score.get("reasons") or [])[:12]
    auto_repair = {
        "type": "local_yaml_execution_repair",
        "reason": reason,
        "changed": bool(local_changed or platform_root_changed),
        "changes": (
            (["wrap root tasks into android.tasks for Runner dry-run"] if platform_root_changed else [])
            + list(repaired.get("changes") or [])
        )[:12],
        "ok": bool(dry_compact.get("ok")) and after_score.get("executionLevel") == "executable",
        "before": {
            "executionLevel": before_score.get("executionLevel"),
            "reasons": before_reasons[:6],
        },
        "after": {
            "executionLevel": after_score.get("executionLevel"),
            "dryRunOk": bool(dry_compact.get("ok")),
            "reasons": list(after_score.get("reasons") or [])[:6],
        },
        "dryRun": dry_compact,
    }

    combined_reasons = []
    combined_reasons.extend(before_reasons)
    combined_reasons.extend(after_reasons)
    combined_reasons.extend(str(item) for item in (dry_compact.get("errors") or [])[:8])
    combined_reasons.extend(str(item) for item in (dry_compact.get("warnings") or [])[:6])
    should_ai_rewrite = (
        not auto_repair.get("ok")
        and _agent_ref_needs_ai_execution_rewrite(scored_after, dry_compact, combined_reasons)
    )
    if should_ai_rewrite:
        ai_repair = ai_rewrite_yaml_for_executable_gate(
            current_content,
            title=ref.get("file") or ref.get("type") or "",
            module=ref.get("module") or "",
            file=ref.get("file") or "",
            reasons=combined_reasons,
            baseline_text="",
            max_attempts=1,
            model_config=_agent_model_config(run),
        )
        auto_repair["aiRewrite"] = {
            "changed": bool(ai_repair.get("changed")),
            "ok": bool(ai_repair.get("ok")),
            "changes": list(ai_repair.get("changes") or [])[:12],
            "attempts": list(ai_repair.get("attempts") or [])[:2],
        }
        if ai_repair.get("changed") and ai_repair.get("ok"):
            current_content = ai_repair.get("content") or current_content
            _agent_write_repaired_yaml_ref(repaired_ref, current_content)
            scored_after = _score_agent_yaml_ref_for_execution(run, repaired_ref)
            dry_after = _agent_yaml_dry_run_for_ref(run, repaired_ref)
            dry_compact = _compact_yaml_dry_run_result(dry_after)
            after_score = scored_after.get("executableScore") if isinstance(scored_after.get("executableScore"), dict) else {}
            auto_repair = {
                **auto_repair,
                "type": "ai_yaml_executable_gate_rewrite",
                "changed": True,
                "ok": bool(dry_compact.get("ok")) and after_score.get("executionLevel") == "executable",
                "after": {
                    "executionLevel": after_score.get("executionLevel"),
                    "dryRunOk": bool(dry_compact.get("ok")),
                    "reasons": list(after_score.get("reasons") or [])[:6],
                },
                "dryRun": dry_compact,
            }
        elif ai_repair.get("changed"):
            auto_repair["aiRewrite"]["candidateKept"] = False
            auto_repair["aiRewrite"]["reason"] = "AI 返回了改写内容，但仍未达到 executable，未覆盖当前 YAML。"

    if not auto_repair.get("changed") and not auto_repair.get("aiRewrite"):
        return scored_before, None

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


def _agent_runner_gate_ref_is_deferred(item):
    """Keep every executable non-smoke case available for gated expansion."""
    reason = str((item or {}).get("gateReason") or "").strip()
    return bool(
        reason.startswith("超过自动冒烟首批上限")
        or reason.startswith("非首批冒烟候选")
        or "待首批完成执行准入后再扩展执行" in reason
    )


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
        if _agent_runner_gate_ref_is_deferred(item)
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
        "rule": "Agent 新生成 YAML 首批优先下发 executable 冒烟候选；没有稳定冒烟候选时不强行下发第三方授权、外部跳转或文件选择链路，需先生成或修正入口可见性短链路。首批冒烟用于验证 YAML 能下发、能运行、能产生日志；保留每条真实结果，通过率不低于 50% 且没有脚本/YAML/定位/超时阻断时才继续扩展。",
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
    selection_excluded = list(initial_blocked or [])
    dry_run_blocked = []
    runner_dry_run_jobs = []
    prepared = []
    serial_same_device = bool(
        str(selected_device_id or "").strip()
        or (
            str(selected_runner_id or "").strip()
            and str(selected_device_strategy or "").strip().lower() in ("fixed", "指定设备")
        )
    )

    # Phase 1: finish the entire preflight batch before any formal job can occupy
    # the fixed-device queue. This keeps later dry-runs from timing out behind an
    # earlier long-running UI task.
    for ref in refs or []:
        if _agent_run_cancel_requested(run):
            dry_run_blocked.append({
                "phase": phase,
                "reason": "Agent 已取消，停止创建后续 Runner 任务",
            })
            break
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
            prepared_item = {
                "ref": ref,
                "module": mod,
                "file": fn,
                "path": full_path,
                "dryRow": dry_row,
                "runnerDryRunJobId": "",
            }
            if runner_dry_run_enabled:
                if _agent_run_cancel_requested(run):
                    dry_run_blocked.append({
                        "module": mod,
                        "file": fn,
                        "path": full_path,
                        "phase": phase,
                        "reason": "Agent 已取消，未创建 Runner dry-run 任务",
                    })
                    break
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
                    prepared_item["runnerDryRunJobId"] = dry_job_id
                else:
                    dry_row["ok"] = False
                    message = "Runner dry-run 任务创建失败，未下发正式任务"
                    dry_row.setdefault("errors", []).append(message)
                    dry_run_blocked.append({
                        "module": mod,
                        "file": fn,
                        "path": full_path,
                        "phase": phase,
                        "reason": message,
                    })
                    continue
            prepared.append(prepared_item)
        except Exception as exc:
            dry_run_blocked.append({
                "module": ref.get("module") or "",
                "file": ref.get("file") or os.path.basename(str(ref.get("path") or "")),
                "path": str(ref.get("path") or ""),
                "phase": phase,
                "reason": f"创建 Runner 任务前异常：{str(exc)[:180]}",
            })

    dispatch_ready = []
    if runner_dry_run_jobs:
        wait_dry = job_service.wait_jobs_finished(
            runner_dry_run_jobs,
            run,
            timeout=dry_run_timeout,
            interval=3,
            phase=f"{phase}-dry-run",
        )
        completed_by_id = {
            str(item.get("job_id") or item.get("jobId") or ""): item
            for item in (wait_dry.get("completed") or [])
            if isinstance(item, dict)
        }
        failed_by_id = {
            str(item.get("job_id") or item.get("jobId") or ""): item
            for item in (wait_dry.get("failed") or [])
            if isinstance(item, dict)
        }
        timeout_by_id = {
            str(item.get("job_id") or item.get("jobId") or ""): item
            for item in (wait_dry.get("timeout") or [])
            if isinstance(item, dict)
        }
        for item in prepared:
            dry_job_id = item.get("runnerDryRunJobId") or ""
            dry_row = item["dryRow"]
            if not dry_job_id:
                dispatch_ready.append(item)
                continue
            dry_failed = failed_by_id.get(dry_job_id)
            dry_timed_out = timeout_by_id.get(dry_job_id)
            dry_completed = completed_by_id.get(dry_job_id)
            dry_row["runnerDryRun"] = {
                "completed": 1 if dry_completed else 0,
                "failed": 1 if dry_failed else 0,
                "timeout": 1 if dry_timed_out else 0,
                "waitTimedOut": bool(dry_timed_out),
                "inconclusive": bool(dry_timed_out and not dry_failed),
                "jobId": dry_job_id,
            }
            if dry_completed:
                dispatch_ready.append(item)
                continue
            if dry_failed:
                errors = [
                    str(dry_failed.get("error") or dry_failed.get("stderr_tail") or dry_failed.get("status") or "dry-run 失败")[:220]
                ]
                message = "Runner 真实 dry-run 未通过"
            else:
                errors = []
                message = "Runner 真实 dry-run 等待报告超时，结果不确定；未下发正式任务"
                dry_row["formalDispatchSkipped"] = True
                dry_row["skipReason"] = message
                dry_row["runnerDryRun"]["blockedFormalDispatch"] = True
            dry_row["ok"] = False
            dry_row.setdefault("errors", []).extend(errors or [message])
            dry_run_blocked.append({
                "module": item["module"],
                "file": item["file"],
                "path": item["path"],
                "phase": phase,
                "reason": message,
                "job_id": dry_job_id,
                "errors": errors,
            })
    else:
        dispatch_ready = list(prepared)

    # Phase 2: formal UI execution. A fixed device receives only one live job at
    # a time; each job must reach a terminal state before the next is created.
    formal_wait_results = []
    for ready_index, item in enumerate(dispatch_ready):
        if _agent_run_cancel_requested(run):
            dry_run_blocked.append({
                "module": item["module"],
                "file": item["file"],
                "path": item["path"],
                "phase": phase,
                "reason": "Agent 已取消，未创建正式 Runner 任务",
            })
            break
        try:
            task_names = _agent_yaml_task_names_for_runner(item["path"])
            target_task_name = task_names[0] if len(task_names) == 1 else ""
            job = job_service.create_job({
                "module": item["module"],
                "file": item["file"],
                "target_task_name": target_task_name,
                "task_names": task_names,
                "current_task_name": target_task_name or (task_names[0] if task_names else ""),
                "runner_id": selected_runner_id,
                "device_id": selected_device_id,
                "device_strategy": selected_device_strategy,
                "parent_run_id": run.get("runId", ""),
                "phase": phase,
            })
            job_id = job.get("job_id") if job else ""
            if not job_id:
                dry_run_blocked.append({
                    "module": item["module"],
                    "file": item["file"],
                    "path": item["path"],
                    "phase": phase,
                    "reason": "正式 Runner 任务创建失败",
                })
                continue
            job_ids.append(job_id)
            if serial_same_device:
                formal_wait = job_service.wait_jobs_finished(
                    [job_id],
                    run,
                    timeout=job_service.runner_job_wait_timeout_seconds(1),
                    interval=5,
                    phase=phase,
                )
                formal_wait_results.append(formal_wait)
                if formal_wait.get("timeout"):
                    for remaining in dispatch_ready[ready_index + 1:]:
                        dry_run_blocked.append({
                            "module": remaining["module"],
                            "file": remaining["file"],
                            "path": remaining["path"],
                            "phase": phase,
                            "reason": "上一条固定设备任务等待超时，为避免并发占用同一设备，未创建后续正式任务",
                        })
                    break
        except Exception as exc:
            dry_run_blocked.append({
                "module": item.get("module") or "",
                "file": item.get("file") or "",
                "path": item.get("path") or "",
                "phase": phase,
                "reason": f"创建正式 Runner 任务异常：{str(exc)[:180]}",
            })
    return {
        "jobIds": job_ids,
        "dryRunResults": dry_run_results,
        "dryRunBlocked": dry_run_blocked,
        "runnerDryRunJobs": runner_dry_run_jobs,
        "selectionExcluded": selection_excluded,
        "serialSameDevice": serial_same_device,
        "formalWaitResult": (
            _agent_merge_runner_wait_results(*formal_wait_results)
            if formal_wait_results else None
        ),
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
        scope_review = scored_ref.get("scopeReview") if isinstance(scored_ref.get("scopeReview"), dict) else {}
        scope_issues = list(scope_review.get("reasons") or []) if scope_review.get("ok") is False else []
        if generated_ref:
            ref_issues = scope_issues or dry_compact.get("errors") or strong_check.get("issues") or []
        else:
            ref_issues = scope_issues or dry_compact.get("errors") or strong_check.get("issues") or []
        if generated_ref and not ref_issues and executable_score.get("executionLevel") != "executable":
            ref_issues = list(executable_score.get("reasons") or [])[:5] or [f"执行等级为 {executable_score.get('executionLevel') or 'unknown'}"]
        if generated_ref and auto_repair and isinstance(auto_repair.get("aiRewrite"), dict) and not auto_repair.get("ok"):
            ai_rewrite = auto_repair.get("aiRewrite") or {}
            attempts = ai_rewrite.get("attempts") if isinstance(ai_rewrite.get("attempts"), list) else []
            last_attempt = attempts[-1] if attempts and isinstance(attempts[-1], dict) else {}
            ai_error = last_attempt.get("error") or "AI 重写后仍未达到可执行"
            ref_issues = [f"AI 修复已尝试但未通过：{str(ai_error)[:180]}"] + list(ref_issues or [])
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


def _agent_visual_reference_report(run, generation_result=None):
    """Explain how uploaded screenshots/Figma images were used as soft references."""
    run = run if isinstance(run, dict) else {}
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    source_context = artifacts.get("sourceContext") if isinstance(artifacts.get("sourceContext"), dict) else {}
    result = generation_result if isinstance(generation_result, dict) else {}
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    cases_payload = result.get("cases") if isinstance(result.get("cases"), dict) else {}
    review = result.get("review") if isinstance(result.get("review"), dict) else {}
    case_review = cases_payload.get("review") if isinstance(cases_payload.get("review"), dict) else {}
    mindmap_visual = _agent_mm_visual_status(case_review)
    uploaded_images = _agent_public_file_list(source_context.get("uploadedImages") or [], 40)
    figma_pages = source_context.get("figmaUsedPages") or source_context.get("uiDesigns") or []
    summary_figma_assets = summary.get("ui_design_assets") or []
    source_figma_assets = source_context.get("uiDesignAssets") or []
    figma_assets = summary_figma_assets or source_figma_assets or []
    figma_image_count = max(
        _agent_list_length(summary_figma_assets),
        _agent_list_length(source_figma_assets),
        _safe_int_local(source_context.get("figmaImageCount"), 0),
    )
    ignored_figma = source_context.get("figmaIgnoredPages") or summary.get("ignored_figma_pages") or []
    reference_sources = []
    if uploaded_images:
        reference_sources.append("uploaded_screenshots")
    if figma_pages or figma_assets or figma_image_count:
        reference_sources.append("figma")
    if source_context.get("requirementText"):
        reference_sources.append("requirement_text")
    notes = []
    if uploaded_images:
        notes.append(f"已接收 {len(uploaded_images)} 张上传截图，要求进入 AI 视觉判断，用于辅助识别页面文案、入口位置和同级关系。")
    if figma_pages or figma_assets or figma_image_count:
        notes.append(f"已解析 Figma 页面 {len(figma_pages)} 个、UI 图 {figma_image_count} 张。")
    if uploaded_images and (figma_pages or figma_assets):
        notes.append("上传截图与 Figma 同时存在时，生成会综合参考；截图不会替代需求、Figma 和成功基线，也不会单独作为执行门禁。")
    elif uploaded_images:
        notes.append("未提供或未解析 Figma 图时，上传截图仍只作为辅助参考，不会强制阻断生成或执行。")
    else:
        notes.append("本次没有上传截图，视觉参考主要来自 Figma 或文本资料。")
    conflict_notes = []
    if uploaded_images and ignored_figma:
        conflict_notes.append("部分 Figma 页面未进入本次参考；如截图与 Figma 页面不一致，请以质量检查中的页面证据为准人工复核。")
    ai_visual_completed = bool(
        review.get("yaml_visual_grounded")
        or case_review.get("yaml_visual_grounded")
        or review.get("visual_grounded")
        or case_review.get("visual_grounded")
        or mindmap_visual.get("completed")
    )
    visual_batches = (
        review.get("yaml_visual_batches")
        or case_review.get("yaml_visual_batches")
        or {}
    )
    visual_batches = visual_batches if isinstance(visual_batches, dict) else {}
    if not visual_batches and mindmap_visual.get("attempted"):
        visual_batches = {
            "enabled": True,
            "completed_batches": mindmap_visual.get("batchesDone") or 0,
            "total_batches": mindmap_visual.get("batchesTotal") or 0,
            "attempted_batches": mindmap_visual.get("batchesAttempted") or 0,
            "batch_results": mindmap_visual.get("batchResults") or [],
            "errors": [mindmap_visual.get("error")] if mindmap_visual.get("error") else [],
        }
    visual_skipped = (
        review.get("visual_refine_skipped")
        or case_review.get("visual_refine_skipped")
    )
    visual_errors = []
    for value in (
        review.get("visual_refine_error"),
        case_review.get("visual_refine_error"),
        review.get("visual_refine_errors"),
        case_review.get("visual_refine_errors"),
        review.get("visual_grounder_error"),
        case_review.get("visual_grounder_error"),
        visual_batches.get("errors"),
    ):
        if isinstance(value, list):
            visual_errors.extend(str(item).strip() for item in value if str(item or "").strip())
        elif str(value or "").strip():
            visual_errors.append(str(value).strip())
    visual_errors = list(dict.fromkeys(visual_errors))
    visual_inputs_present = bool(uploaded_images or figma_pages or figma_assets or figma_image_count)
    ai_visual_attempted = bool(
        ai_visual_completed
        or mindmap_visual.get("attempted")
        or visual_batches.get("enabled")
        or visual_errors
        or review.get("visual_grounder_skill")
        or case_review.get("visual_grounder_skill")
    )
    ai_visual_failed = bool(ai_visual_attempted and not ai_visual_completed and visual_errors)
    ai_visual_partial = bool(mindmap_visual.get("status") == "partial")
    if ai_visual_completed:
        ai_visual_status = "completed"
    elif ai_visual_partial:
        ai_visual_status = "partial"
    elif ai_visual_failed:
        ai_visual_status = "failed"
    elif ai_visual_attempted:
        ai_visual_status = "pending"
    elif visual_inputs_present and generation_result is None:
        ai_visual_status = "pending"
    elif visual_inputs_present:
        ai_visual_status = "skipped"
    else:
        ai_visual_status = "not_required"
    if ai_visual_partial:
        conflict_notes.append("视觉资料已送入 AI 判断，部分批次完成、部分批次失败；已保留批次实绩，生成继续按软参考处理。")
    elif ai_visual_failed:
        conflict_notes.append("视觉资料已送入 AI 判断，但视觉校准失败；失败原因已保留，生成结果需要重新校准或人工复核。")
    elif visual_inputs_present and generation_result is not None and not ai_visual_completed:
        conflict_notes.append("视觉资料已进入本次输入，但未看到 AI 视觉校准完成标记；本次仍会保留生成结果并提示人工复核图片参考是否充分。")
    return {
        "mode": "soft_reference",
        "hardGate": False,
        "aiJudgementRequired": visual_inputs_present,
        "sentToAiForJudgement": ai_visual_attempted,
        "aiJudgementCompleted": ai_visual_completed,
        "aiJudgementStatus": ai_visual_status,
        "visualRefineSkipped": "；".join(visual_errors[:3]) or str(visual_skipped or "").strip(),
        "rule": "上传截图和 Figma 是视觉辅助证据：帮助补充页面文案、入口位置、同级关系和设备形态；不因未完全引用视觉资料而阻断生成或 Runner 执行。",
        "referenceSources": reference_sources,
        "uploadedImageCount": len(uploaded_images),
        "uploadedImages": uploaded_images[:12],
        "figmaPageCount": _agent_list_length(figma_pages),
        "figmaImageCount": figma_image_count,
        "ignoredFigmaCount": _agent_list_length(ignored_figma),
        "visualBatchesDone": _safe_int_local(visual_batches.get("completed_batches"), 0),
        "visualBatchesTotal": _safe_int_local(visual_batches.get("total_batches"), 0),
        "visualBatchesAttempted": _safe_int_local(
            visual_batches.get("attempted_batches"),
            len(visual_batches.get("batch_results") or []),
        ),
        "visualBatchResults": (visual_batches.get("batch_results") or [])[:40],
        "usageNotes": notes,
        "conflictPolicy": "如果截图、Figma、需求文档或历史基线存在冲突，只做显式提醒和人工复核提示，不静默把截图升级为硬门禁。",
        "conflictNotes": conflict_notes,
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
    source_type = str(source_type or run.get("sourceType") or "").strip().lower()
    refs = run.get("sourceRefs") if isinstance(run.get("sourceRefs"), dict) else {}
    if source_type == "failed_job" or _source_ref_value(refs, "failedJobId", "failed_job_id"):
        return True
    scope = str(run.get("scope") or "").strip().lower()
    if scope in ("failed_rerun", "失败重跑"):
        return True
    target = str(run.get("target") or "").strip()
    return bool(re.search(r"(回归|基线|复用|已有用例|旧用例|失败任务|failed[_ -]?job|\bregression\b|\breuse\b|\bbaseline\b)", target, re.I))


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


def _watch_agent_generation_progress(run, step, job_id, stop_event):
    """Mirror shared MM/YAML generation progress into the Agent timeline."""
    if not isinstance(step, dict):
        return
    try:
        from task_server.services.yaml_service import expire_generate_job_if_stale, generate_job_path
    except Exception:
        return
    last_key = None
    while not stop_event.wait(2.0):
        job = read_json_file(generate_job_path(job_id), default={}) or {}
        try:
            job = expire_generate_job_if_stale(job, persist=True) or job
        except Exception:
            pass
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


def _requirement_new_entry_label(value):
    text = _normalize_business_flow_text(value)
    patterns = (
        r"([\u4e00-\u9fffA-Za-z0-9_-]{1,18})入口(?:是|为)(?:本次)?(?:新增|增加|添加)",
        r"(?:新增|增加|添加)(?:一个|的)?([\u4e00-\u9fffA-Za-z0-9_-]{1,18})入口",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        label = str(match.group(1) or "").strip()
        label = re.sub(r"^(?:本次|需要|要求|能力|功能)", "", label)
        if label:
            return label[:18]
    return "目标"


def _requirement_entry_branches(value, target_label=""):
    """Extract sibling business branches from explicit requirement lists."""
    text = _normalize_business_flow_text(value)
    branches = []
    for match in re.finditer(r"(?:业务)?入口[^：:。；\n]{0,24}[：:]\s*([^。；\n]{2,160})", text):
        section = str(match.group(1) or "")
        for raw in re.split(r"\s*(?:、|，|,|；|;|以及|和|及|与)\s*", section):
            candidate = str(raw or "").strip(" -:：()（）[]【】")
            candidate = re.sub(r"^(?:包括|包含|分别为|分别是|有|为|是|以下)", "", candidate)
            candidate = re.sub(r"(?:等|这些业务|以上业务)$", "", candidate).strip()
            if not candidate or candidate == target_label or len(candidate) > 20:
                continue
            if any(term in candidate for term in ("展示", "同级", "文案", "要求", "新增", "能力", "结合", "完整覆盖")):
                continue
            if candidate not in branches:
                branches.append(candidate)
        if branches:
            break
    return branches[:8]


def _fallback_business_flows_from_text(value):
    compact = re.sub(r"\s+", "", _normalize_business_flow_text(value))
    if "入口" in compact and any(term in compact for term in ("新增", "增加", "添加", "展示", "显示", "可见", "校验", "检查")):
        entry_label = _requirement_new_entry_label(value)
        branches = _requirement_entry_branches(value, entry_label)
        if not branches:
            branches = ["目标业务页"]
        checks = [f"校验{entry_label}入口可见"]
        if any(term in compact for term in ("同级", "并列", "层级", "关系", "位置")):
            checks.append(f"校验{entry_label}入口与当前页面同级入口的层级和位置关系")
        if any(term in compact for term in ("文案", "文字", "标题", "命名")):
            checks.append(f"校验{entry_label}入口使用需求约定的可见文案")
        if any(term in compact for term in ("可达", "跳转", "点击后", "授权页", "文件选择页")):
            checks.append(f"点击{entry_label}入口并校验目标页面稳定可达")
        flows = []
        for index, branch in enumerate(branches, start=1):
            steps = []
            if "首页" in compact:
                steps.append("进入首页")
            steps.append(f"进入{branch}")
            flows.append({
                "id": f"FLOW-{index:03d}",
                "name": f"{branch}-{entry_label}入口验收",
                "branch": branch,
                "steps": steps,
                "checks": list(checks),
            })
        return flows
    if any(term in compact for term in ("AI建模", "ai建模", "开始创作", "图片建模", "语音创作", "语音输入")):
        flow = ["进入 AI建模页"]
        if "开始创作" in compact:
            flow.append("点击开始创作")
        if "图片建模" in compact or "上传" in compact:
            flow.append("选择图片建模并上传图片")
        if "语音创作" in compact or "语音输入" in compact or "长按" in compact:
            flow.append("选择语音创作并长按输入")
        flow.append("生成模型并查看结果")
        return [{"id": "FLOW-001", "name": "AI建模业务验收", "branch": "AI建模", "steps": flow}]
    return []


def _fallback_business_flow_from_text(value):
    """Backward-compatible flat view of the branch-aware requirement flow."""
    flattened = []
    for item in _fallback_business_flows_from_text(value):
        for step in item.get("steps") or []:
            if step and step not in flattened:
                flattened.append(step)
    return flattened


def _compact_business_flow_constraint(constraint):
    constraint = constraint if isinstance(constraint, dict) else {}
    flow = constraint.get("businessFlow") if isinstance(constraint.get("businessFlow"), list) else []
    flows = [item for item in (constraint.get("businessFlows") or []) if isinstance(item, dict)]
    return {
        "required": bool(constraint.get("required", False)),
        "strict": bool(constraint.get("strict", False)),
        "candidateOnly": bool(constraint.get("candidateOnly", False)),
        "source": str(constraint.get("source") or "default"),
        "relationship": str(constraint.get("relationship") or "unknown"),
        "businessFlow": [str(item) for item in flow[:8] if str(item or "").strip()],
        "businessFlows": [
            {
                "id": str(item.get("id") or f"FLOW-{index:03d}"),
                "name": str(item.get("name") or item.get("branch") or f"业务分支 {index}"),
                "branch": str(item.get("branch") or ""),
                "steps": _agent_plan_text_list(item.get("steps"), limit=10),
                "checks": _agent_plan_text_list(item.get("checks"), limit=8),
            }
            for index, item in enumerate(flows[:8], start=1)
        ],
    }


def _ensure_business_flow_constraint(run):
    """Persist requirement candidates before PLAN and the validated AI plan after it."""
    if not isinstance(run, dict):
        return {
            "required": False,
            "strict": False,
            "candidateOnly": True,
            "source": "default",
            "relationship": "unknown",
            "businessFlow": [],
            "businessFlows": [],
            "businessFlowText": "待 AI 在资料准备后规划",
        }
    artifacts = run.setdefault("artifacts", {})
    source_context = artifacts.get("sourceContext") if isinstance(artifacts.get("sourceContext"), dict) else {}
    normalized_input = run.get("normalizedInput") if isinstance(run.get("normalizedInput"), dict) else {}
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
    plan = artifacts.get("plan") if isinstance(artifacts.get("plan"), dict) else {}
    ai_plan_flows = [
        item for item in (plan.get("businessFlows") or [])
        if isinstance(item, dict)
    ] if plan.get("aiGenerated") else []
    fallback_source_text = "\n".join([
        str(run.get("target") or ""),
        str(requirement_text or ""),
    ])
    fallback_flows = _fallback_business_flows_from_text(fallback_source_text)
    if ai_plan_flows:
        business_flows = ai_plan_flows[:8]
        business_flow = _agent_plan_text_list(business_flows[0].get("steps"), limit=12) if len(business_flows) == 1 else []
        business_flow_text = "\n".join(
            f"{index}. {item.get('name') or item.get('branch') or item.get('id')}："
            f"{' -> '.join(_agent_plan_text_list(item.get('steps'), limit=10))}"
            for index, item in enumerate(business_flows, start=1)
        )
        constraint = {
            "required": True,
            "strict": True,
            "candidateOnly": False,
            "source": str(plan.get("source") or "ai_plan"),
            "relationship": "single" if len(business_flows) == 1 else "separate_branches",
            "businessFlow": business_flow,
            "businessFlows": business_flows,
            "businessFlowText": business_flow_text,
            "guardrails": [
                "后续工具必须引用 AI 计划中的业务分支或明确说明新增依据",
                "平台覆盖审计不得把同级分支扁平为顺序路径",
                "异常和修复只能挂载到相关业务分支",
            ],
        }
    else:
        business_flows = fallback_flows[:8]
        business_flow_text = (
            "需求候选分支（待 AI 判断关系与路径）：" +
            "、".join(str(item.get("branch") or item.get("name") or "") for item in business_flows)
            if business_flows else "待 AI 在资料准备后规划"
        )
        constraint = {
            "required": False,
            "strict": False,
            "candidateOnly": True,
            "source": "requirement_candidates" if business_flows else "unverified_input",
            "relationship": "unknown",
            "businessFlow": [],
            "businessFlows": business_flows,
            "businessFlowText": business_flow_text,
            "guardrails": [
                "候选分支只用于 AI 输出后的覆盖审计，不能作为预先确定的业务路径",
                "AI 可以重组层级和顺序，但必须解释原始需求中显式入口的去留",
            ],
        }
        artifacts["requirementCoverageCandidates"] = _compact_business_flow_constraint(constraint)
    artifacts["businessFlowConstraint"] = constraint
    if prompt_ctx.get("promptCenter"):
        artifacts["promptCenter"] = prompt_ctx.get("promptCenter")
    if business_ctx:
        business_ctx["business_flow"] = list(constraint.get("businessFlow") or [])[:12]
        business_ctx["business_flow_text"] = constraint.get("businessFlowText") or ""
        business_ctx["business_flow_source"] = constraint.get("source") or ""
        run["businessContext"] = business_ctx
    return constraint


def _business_flow_keywords(constraint, limit=10):
    constraint = constraint if isinstance(constraint, dict) else {}
    if str(constraint.get("source") or "") in ("default", "unverified_input"):
        return []
    flow = constraint.get("businessFlow") if isinstance(constraint.get("businessFlow"), list) else []
    values = list(flow)
    for item in constraint.get("businessFlows") or []:
        if not isinstance(item, dict):
            continue
        values.extend([item.get("branch"), item.get("name")])
        values.extend(_agent_plan_text_list(item.get("steps"), limit=8))
    return _dedupe_business_terms(values, limit=limit)


def _record_tool_eligibility(run, tool_def):
    """Record the business-flow filter decision for tool selection observability."""
    constraint = _ensure_business_flow_constraint(run)
    tool_def = tool_def if isinstance(tool_def, dict) else {}
    category = tool_def.get("category", "UNKNOWN")
    tool_name = tool_def.get("name", "")
    flow_keywords = _business_flow_keywords(constraint)
    allowed = bool(constraint.get("businessFlow") or constraint.get("businessFlows"))
    reason = "AI 业务计划已建立，允许工具围绕业务分支执行" if constraint.get("strict") else "需求候选已记录，允许读取资料供 AI 规划"
    if category in ("READ", "KNOWLEDGE"):
        reason = "读取/知识工具允许用于补齐 AI 业务计划上下文"
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
        artifacts = run.setdefault("artifacts", {})
        artifacts["sourceContext"] = context
        artifacts["visualReferenceReport"] = _agent_visual_reference_report(run)
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
            f"AI 业务计划（PLAN 前仅为未验证候选）：\n{business_constraint.get('businessFlowText', '')}\n\n"
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
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
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
    agent_plan = artifacts.get("plan") if isinstance(artifacts.get("plan"), dict) else {}
    mindmap_plan = artifacts.get("mindmapPlan") if isinstance(artifacts.get("mindmapPlan"), dict) else {}
    prepared_cases_payload = mindmap_plan.get("cases") if isinstance(mindmap_plan.get("cases"), dict) else {}
    requirement_contract = artifacts.get("requirementCoverageCandidates")
    if not isinstance(requirement_contract, dict):
        requirement_contract = {}
    direct_entry_visibility = _agent_use_direct_entry_visibility_smoke(run)
    has_entry_visibility_intent = _agent_needs_entry_visibility_smoke(run)
    request_data = {
        "case_set_id": case_set_id,
        "title": title,
        "target": title,
        "module": module,
        "modelProviderId": run.get("modelProviderId") or run.get("aiProviderId") or "",
        "aiProviderId": run.get("aiProviderId") or run.get("modelProviderId") or "",
        "aiModel": run.get("aiModel") or run.get("model") or "",
        "model": run.get("aiModel") or run.get("model") or "",
        "files": files,
        "figma_url": source_context.get("figmaUrl") or "",
        "figmaUrl": source_context.get("figmaUrl") or "",
        "prepared_figma_context": prepared_figma_context,
        "app_package": _agent_app_package(run),
        "use_knowledge_context": False,
        "source": "agent",
        "scope": str(run.get("scope") or "smoke").strip(),
        "forceEntryVisibilityFastPath": direct_entry_visibility,
        "disableEntryVisibilityFastPath": has_entry_visibility_intent and not direct_entry_visibility,
        "agent_business_plan": {
            key: agent_plan.get(key)
            for key in ("version", "source", "objective", "businessFlows", "coverage", "assumptions", "unknowns", "executionStrategy")
            if agent_plan.get(key) not in (None, "", [], {})
        },
        "executionContext": {
            "executionMode": str(run.get("executionMode") or "RUNNER_JOB").strip().upper(),
            "runnerId": str(run.get("runnerId") or "").strip(),
            "deviceId": str(run.get("deviceId") or "").strip(),
            "deviceStrategy": str(run.get("deviceStrategy") or "auto").strip().lower(),
            "singleDeviceOnly": bool(
                str(run.get("deviceId") or "").strip()
                and str(run.get("deviceStrategy") or "auto").strip().lower() != "auto"
            ),
        },
        "preparedCasesPayload": prepared_cases_payload,
        "preparedCasesSource": "platform_mindmap_ai" if prepared_cases_payload else "",
        "requirementCoverageContract": requirement_contract,
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
        timeout_seconds=900,
    )
    entry_visibility_intent = _agent_entry_visibility_intent(run)
    direct_entry_visibility = bool(entry_visibility_intent) and direct_entry_visibility
    if direct_entry_visibility:
        file_name = _agent_entry_visibility_smoke_filename(run)
        path = safe_join(TASK_DIR, module, file_name)
        content = _agent_entry_visibility_smoke_yaml(run)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        write_text_file(path, content)
        executable_score = score_midscene_yaml_executable(content, generated=True)
        entry_label = str((entry_visibility_intent or {}).get("entryLabel") or "目标").strip() or "目标"
        target_page = str((entry_visibility_intent or {}).get("targetPage") or "目标页面").strip() or "目标页面"
        case_title = f"{target_page}{entry_label}入口可见性短链路冒烟"
        steps = ["启动 App", "等待应用首页加载"]
        if target_page != "首页":
            steps.append(f"进入{target_page}")
        steps.append(f"等待{entry_label}入口可见")
        result = {
            "case_set_id": case_set_id,
            "cases": {
                "title": title,
                "module": module,
                "analysis": {
                    "business_goals": [title],
                    "requirement_points": [f"{target_page}{entry_label}入口可见"],
                    "visible_outcomes": [f"{entry_label}入口可见"],
                },
                "cases": [{
                    "case_id": "TC-ENTRY-001",
                    "title": case_title,
                    "smoke": True,
                    "priority": "P0",
                    "steps": steps,
                    "assertions": [f"{target_page}{entry_label}入口可见"],
                }],
                "manual_cases": [],
                "review": {
                    "skill_pipeline": "agent_direct_entry_visibility_smoke.v1",
                    "fast_path_reason": "Agent 已识别入口可见性需求，直接生成首批短链路冒烟 YAML",
                },
            },
            "yamlFiles": [file_name],
            "files": [file_name],
            "caseCount": 1,
            "manualCaseCount": 0,
            "scenarioCount": 1,
            "yamlFileCount": 1,
            "summaryFiles": [],
            "yamlExecutableScores": [executable_score],
            "generatedCaseGroups": {
                "executable_cases": [{
                    "file": file_name,
                    "case_id": "TC-ENTRY-001",
                    "title": case_title,
                    "executionLevel": executable_score.get("executionLevel") or "executable",
                    "level": executable_score.get("executionLevel") or "executable",
                    "score": executable_score.get("score") or 0,
                    "smoke": True,
                    "runnerCandidate": True,
                    "reasons": executable_score.get("reasons") or [],
                }],
                "needs_review_cases": [],
                "draft_cases": [],
                "manual_cases": [],
            },
            "review": {
                "source": "agent_direct_entry_visibility_smoke",
                "reason": "跳过通用需求/Figma 生成器，避免入口展示需求被重型 AI 阶段阻塞",
            },
            "coverageAudit": {"ok": True, "case_count": 1, "requirement_point_count": 1},
            "summary": {
                "counts": {"cases": 1, "manual_cases": 0, "yaml_files": 1},
                "ui_design_assets": [],
                "ignored_figma_pages": [],
            },
        }
        update_generate_job(
            progress_job_id,
            status="success",
            progress=100,
            step="生成完成",
            message="已直接生成入口可见性短链路冒烟 YAML",
        )
    elif step:
        watcher = threading.Thread(
            target=_watch_agent_generation_progress,
            args=(run, step, progress_job_id, stop_event),
            daemon=True,
        )
        watcher.start()
    if not direct_entry_visibility:
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
    executable_file_items = [
        item for item in yaml_file_items
        if str(item.get("executionLevel") or item.get("level") or "").strip().lower() == "executable"
    ]
    yaml_executability = {
        "ok": bool(executable_file_items) and all(item.get("ok") for item in yaml_validation_results),
        "mode": "split_by_case",
        "fileCount": len(yaml_file_items),
        "executableFileCount": len(executable_file_items),
        "taskCount": len(executable_file_items),
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
    artifacts["visualReferenceReport"] = _agent_visual_reference_report(run, result)
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


def _agent_requirement_ids(value):
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    ids = []
    for match in re.finditer(r"\bREQ[-_ ]?0*(\d+)\b", str(text or ""), flags=re.I):
        requirement_id = f"REQ-{int(match.group(1)):03d}"
        if requirement_id not in ids:
            ids.append(requirement_id)
    return ids


def _agent_mapped_requirement_ids(refs=None, groups=None):
    mapped = []
    candidates = list(refs or [])
    groups = groups if isinstance(groups, dict) else {}
    for key in ("executable_cases", "needs_review_cases", "draft_cases", "manual_cases"):
        candidates.extend(item for item in _as_list(groups.get(key)) if isinstance(item, dict))
    for item in candidates:
        if not isinstance(item, dict):
            continue
        score = item.get("executableScore") if isinstance(item.get("executableScore"), dict) else {}
        scope_review = item.get("scopeReview") if isinstance(item.get("scopeReview"), dict) else {}
        if not scope_review and isinstance(score.get("scopeReview"), dict):
            scope_review = score.get("scopeReview") or {}
        values = [
            scope_review.get("matchedRequirementIds"),
            item.get("requirementRefs"),
            item.get("requirement_ids"),
            item.get("requirementIds"),
        ]
        for requirement_id in _agent_requirement_ids(values):
            if requirement_id not in mapped:
                mapped.append(requirement_id)
    return mapped


def _agent_unresolved_coverage_points(coverage, refs=None, groups=None):
    coverage = coverage if isinstance(coverage, dict) else {}
    missing = [str(item) for item in _as_list(coverage.get("missing_case_points")) if str(item).strip()]
    mapped = set(_agent_mapped_requirement_ids(refs, groups))
    unresolved = []
    for item in missing:
        item_ids = set(_agent_requirement_ids(item))
        if item_ids and item_ids.issubset(mapped):
            continue
        unresolved.append(item)
    return unresolved, sorted(mapped)


def _agent_final_executable_yaml_refs(refs):
    result = []
    for item in _as_list(refs):
        if not isinstance(item, dict):
            continue
        if item.get("confirmed") is False:
            continue
        if str(item.get("type") or "").strip().lower() == "draft":
            continue
        level = str(item.get("executionLevel") or item.get("level") or "").strip().lower()
        if level and level != "executable":
            continue
        result.append(item)
    return result


def _agent_yaml_flow_evidence(yaml_text):
    evidence = []
    if pyyaml is None:
        return evidence
    try:
        _platform, tasks = extract_midscene_tasks(pyyaml.safe_load(str(yaml_text or "")))
    except Exception:
        return evidence
    for task in tasks or []:
        if not isinstance(task, dict):
            continue
        task_name = str(task.get("name") or "").strip()
        if task_name:
            evidence.append(task_name)
        for step in task.get("flow") or []:
            if not isinstance(step, dict):
                continue
            for key, value in step.items():
                if value in (None, "", [], {}):
                    continue
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False)
                evidence.append(f"{key}: {value}")
    return evidence


def _agent_final_yaml_acceptance_gaps(generated, refs):
    generated = generated if isinstance(generated, dict) else {}
    analysis = generated.get("analysis") if isinstance(generated.get("analysis"), dict) else {}
    checks = [
        item for item in (analysis.get("requirement_acceptance_checks") or [])
        if isinstance(item, dict) and str(item.get("text") or "").strip()
    ]
    if not checks:
        return []
    from task_server.services.ai_skill_service import (
        case_covers_requirement_acceptance,
        requirement_acceptance_descriptor,
    )

    yaml_cases = []
    for ref in _agent_final_executable_yaml_refs(refs):
        requirement_ids = _agent_mapped_requirement_ids([ref], groups={})
        yaml_cases.append({
            "title": ref.get("file") or ref.get("path") or "",
            "requirementRefs": requirement_ids,
            "steps": _agent_yaml_flow_evidence(_yaml_ref_content(ref)),
        })
    return [
        requirement_acceptance_descriptor(check)
        for check in checks
        if not any(case_covers_requirement_acceptance(case, check) for case in yaml_cases)
    ]


def _agent_final_yaml_coverage_points(generated, coverage, refs=None):
    """Compare the full requirement list with confirmed executable YAML refs.

    The design-time coverage audit may consider manual and review cases covered.
    That is useful for the test plan, but those cases cannot satisfy the final
    Runner gate. At dispatch time, confirmed YAML refs are the source of truth.
    """
    generated = generated if isinstance(generated, dict) else {}
    coverage = coverage if isinstance(coverage, dict) else {}
    requirement_points = _quality_points_from_payload(generated)
    mapped = set(_agent_mapped_requirement_ids(_agent_final_executable_yaml_refs(refs), groups={}))
    required_ids = []
    unresolved = []
    for point in requirement_points:
        point_ids = _agent_requirement_ids(point)
        if not point_ids:
            continue
        for requirement_id in point_ids:
            if requirement_id not in required_ids:
                required_ids.append(requirement_id)
        if not set(point_ids).issubset(mapped):
            unresolved.append(point)
    unresolved.extend(
        point for point in _agent_final_yaml_acceptance_gaps(generated, refs)
        if point not in unresolved
    )
    if required_ids:
        return unresolved, sorted(mapped), required_ids
    fallback_unresolved, fallback_mapped = _agent_unresolved_coverage_points(
        coverage,
        refs,
        groups={},
    )
    return fallback_unresolved, fallback_mapped, []


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
    visual_reference = artifacts.get("visualReferenceReport") if isinstance(artifacts.get("visualReferenceReport"), dict) else _agent_visual_reference_report(run, result)
    figma_image_count = max(len(ui_assets), _safe_int_local(visual_reference.get("figmaImageCount"), 0))
    ignored_figma_count = max(len(ignored_figma), _safe_int_local(visual_reference.get("ignoredFigmaCount"), 0))
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
    if auto_case_count > yaml_file_count:
        blockers.append(f"自动化用例 {auto_case_count} 条，但只生成 {yaml_file_count} 个 YAML 文件，完整回归覆盖不完整。")
    groups = (artifacts.get("generationPipeline") or {}).get("generatedCaseGroups") if isinstance(artifacts.get("generationPipeline"), dict) else {}
    reported_missing_case_points = [str(item) for item in _as_list(coverage.get("missing_case_points")) if str(item).strip()]
    missing_case_points, mapped_requirement_ids = _agent_unresolved_coverage_points(coverage, yaml_items, groups)
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
    if (summary.get("figma_url") or result.get("figma_url") or (artifacts.get("sourceContext") or {}).get("figmaUrl")) and figma_image_count <= 0:
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
        "figmaImageCount": figma_image_count,
        "uploadedImageCount": int(visual_reference.get("uploadedImageCount") or 0),
        "visualReferenceReport": visual_reference,
        "ignoredFigmaCount": ignored_figma_count,
        "coverageOk": bool(coverage.get("ok")) if coverage else not (missing_case_points or generic_assertions),
        "coverage": {
            "missingCasePoints": missing_case_points[:20],
            "reportedMissingCasePoints": reported_missing_case_points[:20],
            "mappedRequirementIds": mapped_requirement_ids[:20],
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
            {"name": "Figma 解析图片", "count": figma_image_count, "ready": figma_image_count > 0},
            {"name": "上传截图参考", "count": int(visual_reference.get("uploadedImageCount") or 0), "ready": int(visual_reference.get("uploadedImageCount") or 0) > 0},
        ],
    }
    return report


def _agent_generated_yaml_coverage_gap(run, refs=None):
    artifacts = (run or {}).get("artifacts") if isinstance(run, dict) else {}
    if not isinstance(artifacts, dict):
        return {}
    pipeline = artifacts.get("generationPipeline") if isinstance(artifacts.get("generationPipeline"), dict) else {}
    generated = artifacts.get("generatedCases") if isinstance(artifacts.get("generatedCases"), dict) else {}
    coverage = pipeline.get("coverageAudit") if isinstance(pipeline.get("coverageAudit"), dict) else {}
    case_count = _safe_int_local(pipeline.get("caseCount"), len(_as_list(generated.get("cases"))))
    requirement_count = _safe_int_local(coverage.get("requirement_point_count"), 0)
    groups = pipeline.get("generatedCaseGroups") if isinstance(pipeline.get("generatedCaseGroups"), dict) else {}
    effective_refs = refs if refs is not None else _as_list(artifacts.get("yamlRefs"))
    executable_refs = _agent_final_executable_yaml_refs(effective_refs)
    yaml_count = (
        len(executable_refs)
        if refs is not None or effective_refs
        else _safe_int_local(pipeline.get("yamlFileCount"), 0)
    )
    unresolved_case_points, mapped_requirement_ids, required_requirement_ids = _agent_final_yaml_coverage_points(
        generated,
        coverage,
        executable_refs,
    )
    requirement_count = max(requirement_count, len(required_requirement_ids))
    counts = groups.get("counts") if isinstance(groups.get("counts"), dict) else {}
    needs_review_count = _safe_int_local(counts.get("needs_review"), 0)
    missing_titles = []
    for item in _as_list(groups.get("needs_review_cases")):
        if not isinstance(item, dict):
            continue
        reasons = "；".join(str(reason) for reason in _as_list(item.get("reasons")))
        scope_reasons = "；".join(str(reason) for reason in _as_list((item.get("scopeReview") or {}).get("reasons") if isinstance(item.get("scopeReview"), dict) else []))
        if "未生成对应 YAML" in reasons or "缺少该用例的 YAML" in reasons or "未生成对应 YAML" in scope_reasons:
            title = str(item.get("name") or item.get("title") or item.get("case_id") or "").strip()
            if title:
                missing_titles.append(title)
    full_scope = str((run or {}).get("scope") or "").strip().lower() in ("regression", "full", "all", "complete", "完整", "回归")
    if not full_scope and case_count <= 1:
        return {}
    reasons = []
    if case_count > 0 and yaml_count < case_count:
        reasons.append(f"生成自动化用例 {case_count} 条，但只生成/确认 YAML {yaml_count} 个")
    if requirement_count >= 3 and yaml_count < min(case_count or requirement_count, requirement_count):
        reasons.append(f"需求点 {requirement_count} 个，YAML 覆盖不足 {yaml_count} 个")
    if missing_titles:
        reasons.append("缺少 YAML 的用例：" + "、".join(missing_titles[:5]))
    if unresolved_case_points:
        reasons.append("仍未覆盖的需求点：" + "、".join(unresolved_case_points[:5]))
    if not reasons and needs_review_count:
        reasons.append(f"仍有 {needs_review_count} 条生成用例停留在需复核，不能视为完整回归可执行")
    if not reasons:
        return {}
    return {
        "ok": False,
        "caseCount": case_count,
        "yamlCount": yaml_count,
        "requirementPointCount": requirement_count,
        "needsReviewCount": needs_review_count,
        "missingYamlCases": missing_titles[:20],
        "missingRequirementPoints": unresolved_case_points[:20],
        "mappedRequirementIds": mapped_requirement_ids[:20],
        "requiredRequirementIds": required_requirement_ids[:20],
        "reasons": reasons,
    }


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
    visual_reference = artifacts.get("visualReferenceReport") if isinstance(artifacts.get("visualReferenceReport"), dict) else _agent_visual_reference_report(run)
    source_context = artifacts.get("sourceContext") if isinstance(artifacts.get("sourceContext"), dict) else {}
    figma_image_count = max(
        len(source_context.get("uiDesignAssets") or []),
        _safe_int_local(visual_reference.get("figmaImageCount"), 0),
    )
    ignored_figma_count = max(
        len(source_context.get("figmaIgnoredPages") or []),
        _safe_int_local(visual_reference.get("ignoredFigmaCount"), 0),
    )
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
        "figmaImageCount": figma_image_count,
        "ignoredFigmaCount": ignored_figma_count,
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
            {"name": "Figma 解析图片", "count": figma_image_count, "ready": figma_image_count > 0},
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
                    coverage_gap = _agent_generated_yaml_coverage_gap(run, refs)
                    if coverage_gap:
                        artifacts.setdefault("generationPipeline", {})["coverageGap"] = coverage_gap
                        quality = artifacts.setdefault("qualityReport", {})
                        blockers = [str(item).strip() for item in _as_list(quality.get("blockers")) if str(item).strip()]
                        quality["status"] = "blocked"
                        quality["statusText"] = "阻断"
                        quality["blockers"] = (blockers + coverage_gap.get("reasons", []))[:20]
                        raise ValueError("完整回归生成结果覆盖不完整：" + "；".join(coverage_gap.get("reasons") or []))
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
            blocked_reasons = []
            for item in (execution_gate.get("blocking") or execution_gate.get("blocked") or []):
                if not isinstance(item, dict):
                    continue
                reason = str(item.get("gateReason") or item.get("reason") or "").strip()
                if reason and reason not in blocked_reasons:
                    blocked_reasons.append(reason)
                if len(blocked_reasons) >= 3:
                    break
            gate_detail = (
                f"首批可执行 {execution_gate.get('selectedCount', 0)}/{execution_gate.get('totalCount', 0)}；"
                f"executable {execution_gate.get('executableCount', 0)}，"
                f"需复核 {execution_gate.get('needsReviewCount', 0)}，草稿 {execution_gate.get('draftCount', 0)}，"
                f"人工 {execution_gate.get('manualCount', 0)}，"
                f"延后 {execution_gate.get('deferredCount', 0)}"
            )
            if blocked_reasons:
                gate_detail += "；拦截原因：" + "；".join(blocked_reasons)
            add(
                "generated_yaml_executable_gate",
                bool(selected_refs),
                gate_detail,
                severity,
            )
        coverage_gap = _agent_generated_yaml_coverage_gap(run, refs)
        if coverage_gap:
            artifacts.setdefault("generationPipeline", {})["coverageGap"] = coverage_gap
            add(
                "generated_yaml_coverage_gate",
                False,
                "；".join(coverage_gap.get("reasons") or [])[:500],
                "blocker",
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
                selected_device_meta = next(
                    (
                        d for d in (runner.get("devices") or [])
                        if str(d.get("device_id") or d.get("deviceId") or "") == selected_device
                    ),
                    {},
                )
                selected_device_label = " ".join(
                    str(selected_device_meta.get(key) or "").strip()
                    for key in ("label", "display_name", "brand", "model")
                    if str(selected_device_meta.get(key) or "").strip()
                )
                detail = f"指定 Runner：{selected_runner}"
                if selected_device:
                    detail += f"，设备：{selected_device}"
                    if selected_device_label:
                        detail += f"（{selected_device_label}）"
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
            selection_excluded = created.get("selectionExcluded") or []
            job_ids = created["jobIds"]

            dry_run_passed = sum(1 for item in dry_run_results if item.get("ok"))
            dry_run_inconclusive = [
                item for item in dry_run_results
                if isinstance(item, dict)
                and ((item.get("runnerDryRun") or {}).get("inconclusive") or item.get("formalDispatchSkipped"))
            ]
            run_artifacts["runnerDryRun"] = {
                "ok": not dry_run_blocked,
                "checked": len(dry_run_results),
                "blockedCount": len(dry_run_blocked),
                "inconclusiveCount": len(dry_run_inconclusive),
                "createdCount": len(job_ids),
                "mode": "runner_yaml_dry_run" if runner_dry_run_enabled else "mock_dry_run",
                "reason": runner_dry_run_reason,
                "runnerJobIds": runner_dry_run_jobs,
                "results": dry_run_results,
                "blocked": dry_run_blocked,
                "selectionExcluded": selection_excluded,
                "selectionExcludedCount": len(selection_excluded),
                "inconclusive": dry_run_inconclusive,
                "deferred": gate_deferred,
                "deferredCount": len(gate_deferred),
                "executionGate": execution_gate,
            }
            summary_parts = [
                f"Runner 调试模式：{run_artifacts['runnerDryRun']['mode']} 通过 {dry_run_passed} 个，拦截 {len(dry_run_blocked)} 个，不确定 {len(dry_run_inconclusive)} 个，创建 {len(job_ids)} 个本地任务"
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
                    "smokeExecutable": bool(smoke_blocker.get("executable", not smoke_blocker.get("block"))),
                    "smokePassThresholdMet": bool(smoke_blocker.get("thresholdPassed", not smoke_blocker.get("block"))),
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
                        "smokeExecutable": bool(smoke_blocker.get("executable", not smoke_blocker.get("block"))),
                        "smokePassThresholdMet": bool(smoke_blocker.get("thresholdPassed", not smoke_blocker.get("block"))),
                        "rule": smoke_blocker.get("rule") or "首批冒烟不可执行时停止扩展，先修复 YAML 或 Runner 环境。",
                    }
                    gate.update(stop_info)
                    run_artifacts["runnerExecutionGate"] = gate
                    run_artifacts["runnerSmokeGate"] = stop_info
                    summary_parts.append(f"首批冒烟未达到后续扩展门槛（通过 {smoke_total - smoke_failed}/{smoke_total}），已停止后续批量执行：{stop_reason}")
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


def _agent_failure_review(item, fallback=None):
    for source in (item, fallback):
        if not isinstance(source, dict):
            continue
        review = source.get("failureReview") or source.get("failure_review")
        if isinstance(review, dict):
            return review
    return {}


def _agent_failure_type_from_review(review):
    if not isinstance(review, dict):
        return ""
    category = str(review.get("category") or review.get("failureType") or review.get("failure_type") or "").strip().lower()
    category = category.replace("-", "_").split("/", 1)[0]
    return {
        "env_issue": "ENV_ISSUE",
        "environment_issue": "ENV_ISSUE",
        "model_service": "ENV_ISSUE",
        "script_issue": "SCRIPT_ISSUE",
        "yaml_issue": "SCRIPT_ISSUE",
        "product_bug": "PRODUCT_BUG",
        "unknown": "UNKNOWN",
    }.get(category, "")


def _agent_canonical_failure_type(value):
    raw = str(value or "").strip()
    normalized = raw.upper().replace("-", "_")
    if normalized in ("ENV_ISSUE", "SCRIPT_ISSUE", "PRODUCT_BUG", "UNKNOWN", "NONE"):
        return normalized
    if raw in (
        "Midscene 重规划超限",
        "YAML 动作参数不兼容",
        "Runner 单任务超时",
        "元素定位失败",
        "等待目标超时",
        "断言/页面状态不匹配",
    ):
        return "SCRIPT_ISSUE"
    return ""


def _agent_explicit_auto_repair_decision(item):
    """Return an explicit per-failure repair decision without inventing a default."""
    item = item if isinstance(item, dict) else {}
    review = _agent_failure_review(item)
    for source in (item, review):
        for key in ("canAutoRepair", "can_auto_repair"):
            if key not in source:
                continue
            value = source.get(key)
            if isinstance(value, bool):
                return value
            normalized = str(value or "").strip().lower()
            if normalized in ("true", "1", "yes"):
                return True
            if normalized in ("false", "0", "no"):
                return False
    return None


def _agent_repair_eligibility(item, fallback_failure_type="", fallback_can_auto_repair=None):
    """Keep repair eligibility bound to one failed Runner task."""
    item = item if isinstance(item, dict) else {}
    failure_type = (
        _agent_canonical_failure_type(item.get("failureType") or item.get("failure_type"))
        or _agent_failure_type_from_review(_agent_failure_review(item))
        or _agent_canonical_failure_type(fallback_failure_type)
        or "UNKNOWN"
    )
    can_auto_repair = _agent_explicit_auto_repair_decision(item)
    if can_auto_repair is None and fallback_can_auto_repair is not None:
        can_auto_repair = bool(fallback_can_auto_repair)
    if failure_type != "SCRIPT_ISSUE":
        return {
            "eligible": False,
            "failureType": failure_type,
            "canAutoRepair": can_auto_repair,
            "reason": f"{failure_type} 只保留诊断证据，不自动修改 YAML",
            "code": "failure_type_not_repairable",
        }
    if can_auto_repair is False:
        return {
            "eligible": False,
            "failureType": failure_type,
            "canAutoRepair": False,
            "reason": "该失败任务的证据明确不支持安全自动修复",
            "code": "source_auto_repair_denied",
        }
    return {
        "eligible": True,
        "failureType": failure_type,
        "canAutoRepair": can_auto_repair,
        "reason": "失败分类允许生成有界 YAML 修复候选",
        "code": "",
    }


def _agent_failure_type_counts(items):
    """Count terminal failure types without collapsing mixed Runner outcomes."""
    counts = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        failure_type = (
            _agent_canonical_failure_type(item.get("failureType") or item.get("failure_type"))
            or _agent_failure_type_from_review(_agent_failure_review(item))
            or "UNKNOWN"
        )
        counts[failure_type] = counts.get(failure_type, 0) + 1
    return counts


def _agent_has_repairable_failure(run):
    """Let one repairable task proceed even when another task is product or environment failure."""
    items = _agent_failed_execution_items(run)
    analysis = ((run or {}).get("artifacts") or {}).get("failureAnalysis") or {}
    return any(
        _agent_repair_eligibility(
            item,
            fallback_failure_type=analysis.get("failureType") if len(items) == 1 else None,
            fallback_can_auto_repair=(
                analysis.get("canAutoRepair")
                if len(items) == 1 and "canAutoRepair" in analysis
                else None
            ),
        ).get("eligible") is True
        for item in items
    )


def _agent_original_rerun_eligible(item):
    """Allow one unchanged retry only for concrete environment failures or legacy unclassified scripts."""
    item = item if isinstance(item, dict) else {}
    failure_type = (
        _agent_canonical_failure_type(item.get("failureType") or item.get("failure_type"))
        or _agent_failure_type_from_review(_agent_failure_review(item))
        or "UNKNOWN"
    )
    if failure_type == "ENV_ISSUE":
        return _agent_failed_item_has_concrete_environment_evidence(item)
    return failure_type == "SCRIPT_ISSUE" and _agent_explicit_auto_repair_decision(item) is None


def _agent_should_confirm_unknown_failure(run, failure_type):
    return (
        _agent_canonical_failure_type(failure_type) == "UNKNOWN"
        and not bool((run or {}).get("unknownFailureConfirmed"))
    )


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
    summary = item.get("summary") if isinstance(item.get("summary"), dict) else fallback.get("summary") if isinstance(fallback.get("summary"), dict) else {}
    summary_text = str(item.get("summaryText") or item.get("summary_text") or fallback.get("summaryText") or fallback.get("summary_text") or "").strip()
    failure_review = _agent_failure_review(item, fallback)
    reason = str(item.get("failureReason") or item.get("failure_reason") or "").strip()
    if not reason:
        reason = str(failure_review.get("reason") or "").strip()
    if not reason:
        reason = _agent_job_failure_reason({
            **item,
            "error": error,
            "stdout_tail": stdout_tail,
            "stderr_tail": stderr_tail,
            "summary": summary,
            "summaryText": summary_text,
        })
    raw_failure_type = str(item.get("failureType") or item.get("failure_type") or "").strip()
    failure_kind = raw_failure_type
    failure_type = _agent_canonical_failure_type(raw_failure_type)
    review_failure_type = _agent_failure_type_from_review(failure_review)
    trusted_review_type = review_failure_type if _agent_high_confidence_failure_review(failure_review) else ""
    if trusted_review_type == "PRODUCT_BUG":
        failure_type = review_failure_type
    elif trusted_review_type == "ENV_ISSUE" and _agent_failure_review_has_concrete_environment_evidence(failure_review):
        failure_type = review_failure_type
    elif failure_type in ("", "UNKNOWN") and trusted_review_type and trusted_review_type != "ENV_ISSUE":
        failure_type = trusted_review_type
    if failure_type in ("", "UNKNOWN"):
        inferred_kind = _agent_job_failure_type("\n".join([error, stdout_tail, stderr_tail, summary_text]))
        inferred_type = _agent_canonical_failure_type(inferred_kind)
        if inferred_type:
            failure_type = inferred_type
            if not failure_kind or _agent_canonical_failure_type(failure_kind) == "UNKNOWN":
                failure_kind = inferred_kind
    failure_type = failure_type or "UNKNOWN"
    normalized = {
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
        "summary": summary,
        "summaryText": summary_text[:4000],
        "failureReason": reason,
        "failureType": failure_type,
        "failureKind": failure_kind,
        "failureReview": failure_review,
    }
    explicit_auto_repair = _agent_explicit_auto_repair_decision({**item, "failureReview": failure_review})
    if explicit_auto_repair is not None:
        normalized["canAutoRepair"] = explicit_auto_repair
    return normalized


def _agent_runner_job_material(job_id):
    job_id = str(job_id or "").strip()
    if not job_id:
        return {}
    run_dir = safe_join(LEARNING_DIR, "runs", job_id)
    if not os.path.isdir(run_dir):
        return {}
    stdout = read_text_file(safe_join(run_dir, "stdout.log"), "")
    stderr = read_text_file(safe_join(run_dir, "stderr.log"), "")
    summary = read_json_file(safe_join(run_dir, "summary.json"), default=None)
    attempts = read_json_file(safe_join(run_dir, "attempts.json"), default=None)
    material = {
        "runDir": run_dir,
        "stdoutTail": stdout[-2000:] if stdout else "",
        "stderrTail": stderr[-2000:] if stderr else "",
    }
    if isinstance(summary, dict):
        material["summary"] = summary
        material["summaryText"] = json.dumps(summary, ensure_ascii=False)[:4000]
    if isinstance(attempts, list):
        material["attempts"] = attempts[-3:]
    return {key: value for key, value in material.items() if value not in ("", None, [], {})}


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


def _agent_rerun_source_links(artifacts):
    """Return every persisted source -> repair job link across bounded rerun rounds."""
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    sources = []
    for progress in list(artifacts.get("rerunProgressHistory") or []) + [artifacts.get("rerunProgress")]:
        if isinstance(progress, dict):
            sources.extend(progress.get("sources") or [])
    sources.extend(artifacts.get("rerunSources") or [])
    links = []
    seen = set()
    for item in sources:
        if not isinstance(item, dict):
            continue
        source_job_id = str(item.get("sourceJobId") or item.get("source_job_id") or "").strip()
        new_job_id = str(item.get("newJobId") or item.get("new_job_id") or "").strip()
        key = (source_job_id, new_job_id)
        if not source_job_id or not new_job_id or key in seen:
            continue
        seen.add(key)
        links.append({**copy.deepcopy(item), "sourceJobId": source_job_id, "newJobId": new_job_id})
    return links


def _agent_attempt_job_ids(artifacts):
    """Build the immutable attempt ledger used by report collection and summaries."""
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    values = list(artifacts.get("jobIds") or []) + list(artifacts.get("retriedJobs") or [])
    for attempt in artifacts.get("rerunAttempts") or []:
        if isinstance(attempt, dict):
            values.extend(attempt.get("createdJobIds") or [])
    values.extend(item.get("newJobId") for item in _agent_rerun_source_links(artifacts))
    result = []
    for value in values:
        job_id = str(value or "").strip()
        if job_id and job_id not in result:
            result.append(job_id)
    return result


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
        job_ids = _agent_attempt_job_ids(artifacts)
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
                        "failureReview": job.get("failure_review") or job.get("failureReview") or {},
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
                    report_url = job.get("report_url") or job.get("reportUrl", "")
                    local_path = job.get("local_report_path") or job.get("localReportPath", "")
                    yaml_execution_refs.append({
                        "jobId": jid,
                        "module": job.get("module", ""),
                        "file": job.get("file", ""),
                        "taskName": job_entry.get("taskName", ""),
                        "status": status,
                    })
                    if report_url or str(local_path).lower().endswith((".html", ".htm")):
                        execution_reports.append({
                            "jobId": jid,
                            "module": job.get("module", ""),
                            "file": job.get("file", ""),
                            "taskName": job_entry.get("taskName", ""),
                            "reportUrl": report_url,
                            "localPath": local_path,
                            "status": status,
                        })
                    material = _agent_runner_job_material(jid)
                    fail_entry = {
                        **job_entry,
                        **material,
                        "localPath": local_path,
                        "error": job.get("error") or job.get("fail_reason", ""),
                        "stderrTail": (material.get("stderrTail") or job.get("stderr") or job.get("stderr_tail") or "")[-1600:],
                        "stdoutTail": (material.get("stdoutTail") or job.get("stdout") or job.get("stdout_tail") or "")[-1200:],
                    }
                    fail_entry = _normalize_failed_execution_item(fail_entry, job) or fail_entry
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
                    raw_fail_entry = {
                        **fj,
                        "jobId": fj_id or "",
                        "status": fj.get("status", "failed"),
                        "module": fj.get("module", ""),
                        "file": fj.get("file", ""),
                        "taskName": fj.get("taskName") or fj.get("task_name") or fj.get("target_task_name") or fj.get("current_task_name") or "",
                        "reportUrl": fj.get("report_url") or fj.get("reportUrl", ""),
                        "stdoutTail": fj.get("stdout_tail") or "",
                        "stderrTail": fj.get("stderr_tail") or "",
                        "error": fj.get("error", ""),
                    }
                    failed_jobs.append(_normalize_failed_execution_item(raw_fail_entry, fj) or raw_fail_entry)
                    if fj.get("error"):
                        errors.append(fj.get("error"))
            for tj in (job_result.get("timeout") or []):
                tj_id = tj.get("job_id") or tj.get("jobId")
                if tj_id in success_job_ids:
                    continue
                if not any(f.get("jobId") == tj_id for f in failed_jobs):
                    raw_timeout_entry = {
                        **tj,
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
                    timeout_entry = _normalize_failed_execution_item(raw_timeout_entry, tj) or raw_timeout_entry
                    failed_jobs.append(timeout_entry)
                    timeout_jobs.append(timeout_entry)
                    errors.append(timeout_entry["error"])
        report_ref_ids = {str(item.get("jobId") or "") for item in execution_reports}
        yaml_ref_ids = {str(item.get("jobId") or "") for item in yaml_execution_refs}
        for item in failed_jobs:
            jid = str(item.get("jobId") or "").strip()
            if not jid:
                continue
            status = str(item.get("status") or "failed").lower()
            if jid not in yaml_ref_ids:
                yaml_execution_refs.append({
                    "jobId": jid,
                    "module": item.get("module", ""),
                    "file": item.get("file", ""),
                    "taskName": item.get("taskName", ""),
                    "status": status,
                })
                yaml_ref_ids.add(jid)
            report_url = item.get("reportUrl") or item.get("report_url") or ""
            local_path = item.get("localPath") or item.get("local_report_path") or ""
            if jid not in report_ref_ids and (
                report_url or str(local_path).lower().endswith((".html", ".htm"))
            ):
                execution_reports.append({
                    "jobId": jid,
                    "module": item.get("module", ""),
                    "file": item.get("file", ""),
                    "taskName": item.get("taskName", ""),
                    "reportUrl": report_url,
                    "localPath": local_path,
                    "status": status,
                })
                report_ref_ids.add(jid)
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


def _agent_failure_ai_payload(run, failure_type, failure_context, failed_jobs):
    primary_failure = next((item for item in failed_jobs if isinstance(item, dict)), {})
    primary_yaml = ""
    module = str(primary_failure.get("module") or "").strip()
    file_name = clean_filename(primary_failure.get("file") or "")
    if module and file_name:
        try:
            primary_yaml = read_text_file(safe_join(TASK_DIR, module, file_name), default="")
        except Exception:
            primary_yaml = ""
    primary_log = "\n".join(filter(None, [
        str(failure_context or "").strip(),
        str(primary_failure.get("failureReason") or "").strip(),
        str(primary_failure.get("error") or "").strip(),
        str(primary_failure.get("stdoutTail") or "").strip(),
        str(primary_failure.get("stderrTail") or "").strip(),
        str(primary_failure.get("summaryText") or "").strip(),
    ]))
    failed_job_payloads = [
        {
            "jobId": fj.get("jobId", ""),
            "taskName": fj.get("taskName", ""),
            "file": fj.get("file", ""),
            "error": fj.get("error", ""),
            "failureReason": fj.get("failureReason", ""),
            "stdoutTail": fj.get("stdoutTail", ""),
            "stderrTail": fj.get("stderrTail", ""),
            "summary": fj.get("summary") if isinstance(fj.get("summary"), dict) else {},
            "summaryText": fj.get("summaryText", ""),
            "failureReview": fj.get("failureReview") if isinstance(fj.get("failureReview"), dict) else {},
        }
        for fj in failed_jobs[:12]
        if isinstance(fj, dict)
    ]
    image_assets = []
    frame_names = []
    seen_frames = set()
    for failed_job in failed_jobs[:3]:
        for frame in _agent_failure_report_keyframes(failed_job, limit=3):
            frame_key = str(frame.get("base64") or "")[:80]
            if not frame_key or frame_key in seen_frames:
                continue
            seen_frames.add(frame_key)
            image_assets.append(frame)
            frame_names.append(frame.get("name") or "report-keyframe")
            if len(image_assets) >= 6:
                break
        if len(image_assets) >= 6:
            break
    baseline_examples = _agent_repair_baseline_examples(run, primary_failure, primary_yaml, limit=6)
    return {
        "target": run.get("target", ""),
        "requirement": _agent_plan_requirement_text(run),
        "taskName": primary_failure.get("taskName") or run.get("target", ""),
        "yaml": primary_yaml[:20000],
        "log": primary_log[:12000],
        "screenshotDesc": str(primary_failure.get("failureReason") or primary_failure.get("error") or failure_context or "")[:4000],
        "failureType": failure_type,
        "context": str(failure_context or "")[:2000],
        "failedJobs": failed_job_payloads,
        "imageAssets": image_assets,
        "reportKeyframes": frame_names,
        "baselineExamples": baseline_examples,
        "sourceEvidence": _agent_source_evidence(run),
        "evidenceSources": [
            "原始需求", "Figma 同帧软证据", "原始 YAML", "Runner 日志",
            "Runner failureReview", "Midscene 报告视觉时间线关键帧", "可信分支基线",
        ],
        "executionConstraint": {
            "runnerId": run.get("runnerId") or "",
            "deviceId": run.get("deviceId") or "",
            "deviceStrategy": run.get("deviceStrategy") or "",
            "allowOtherDevices": not bool(run.get("deviceId") and str(run.get("deviceStrategy") or "").lower() == "fixed"),
        },
        **_agent_ai_route_payload(run, has_images=bool(image_assets)),
    }


def _agent_source_evidence(run):
    """Return bounded source facts for planning and failure-repair AI calls."""
    artifacts = (run or {}).get("artifacts") if isinstance((run or {}).get("artifacts"), dict) else {}
    source = artifacts.get("sourceContext") if isinstance(artifacts.get("sourceContext"), dict) else {}
    used_pages = source.get("figmaUsedPages") or source.get("uiDesigns") or []
    visual_report = (
        artifacts.get("visualReferenceReport")
        if isinstance(artifacts.get("visualReferenceReport"), dict)
        else {}
    )
    if not visual_report:
        quality_report = artifacts.get("qualityReport") if isinstance(artifacts.get("qualityReport"), dict) else {}
        visual_report = (
            quality_report.get("visualReferenceReport")
            if isinstance(quality_report.get("visualReferenceReport"), dict)
            else {}
        )
    visual_current_page_evidence = []
    seen_visual_evidence = set()
    for batch in visual_report.get("visualBatchResults") or []:
        if not isinstance(batch, dict) or str(batch.get("status") or "").lower() != "completed":
            continue
        for item in batch.get("currentPageEvidence") or []:
            if not isinstance(item, dict):
                continue
            row = {
                key: copy.deepcopy(item.get(key))
                for key in (
                    "caseId", "requirementId", "branch", "pageTitle", "parentPath",
                    "navigationLeaf", "targetText", "sameBranch", "confidence", "source",
                    "leafDerivedFromPageTitle", "originalNavigationLeaf",
                )
                if item.get(key) not in (None, "", [])
            }
            key = json.dumps(row, ensure_ascii=False, sort_keys=True)
            if not row or key in seen_visual_evidence:
                continue
            seen_visual_evidence.add(key)
            visual_current_page_evidence.append(row)
            if len(visual_current_page_evidence) >= 16:
                break
        if len(visual_current_page_evidence) >= 16:
            break
    return {
        "mode": "soft_reference",
        "target": str((run or {}).get("target") or source.get("target") or "")[:1000],
        "requirementText": str(source.get("requirementText") or _agent_plan_requirement_text(run) or "")[:12000],
        "sourceSummary": str(source.get("sourceSummary") or "")[:2000],
        "figmaText": str(source.get("figmaText") or "")[:12000],
        "figmaPages": [
            _agent_figma_page_brief(item)
            for item in used_pages[:20]
            if isinstance(item, dict)
        ],
        "figmaPageCount": len(used_pages),
        "figmaImageCount": _safe_int_local(source.get("figmaImageCount"), 0),
        "visualCurrentPageEvidence": visual_current_page_evidence,
        "policy": [
            "需求文本定义验证目标；Figma 只补充单个设计帧中的页面状态、层级和可见文字。",
            "Frame 名可能是内部旧命名，状态/变体和可见文字优先；一帧能力不能推广到相邻页面。",
            "成功基线只提供父页面路径结构；当前视觉证据已采用的尺寸、模式或产品叶子不能被基线样例值替换。",
            "失败关键帧证明实际到达状态；若只到父页面，应先修正导航，不能直接判产品缺陷。",
            "画布设备形态不是第二台真实设备要求；执行设备仍由 executionConstraint 决定。",
        ],
    }


def _agent_failure_report_keyframes(failed_job, limit=4):
    """Extract bounded Midscene report frames for multimodal failure review."""
    if not isinstance(failed_job, dict):
        return []
    try:
        from task_server.services.report_service import report_image_context
    except Exception:
        return []
    job_id = _failed_job_id(failed_job)
    material = _agent_runner_job_material(job_id) if job_id else {}
    report_job = {
        "job_id": job_id,
        "report_url": failed_job.get("reportUrl") or failed_job.get("report_url") or "",
        "local_report_path": failed_job.get("localPath") or failed_job.get("local_report_path") or "",
        "run_dir": material.get("runDir") or failed_job.get("runDir") or failed_job.get("run_dir") or "",
    }
    try:
        return [
            item for item in report_image_context(report_job, limit=max(1, min(6, safe_int(limit, 4))))
            if isinstance(item, dict) and item.get("base64") and item.get("mime")
        ]
    except Exception:
        return []


def _agent_repair_baseline_examples(run, target_job, original_yaml, limit=3):
    """Retrieve trustworthy, requirement-related examples for one failed path."""
    try:
        from task_server.services.yaml_baseline_cache import search_diverse_baseline_examples
    except Exception:
        return []
    target_text = "\n".join(filter(None, [
        str(target_job.get("taskName") or target_job.get("file") or ""),
        str(target_job.get("failureReason") or target_job.get("error") or ""),
        str(original_yaml or "")[:6000],
    ]))
    target_compact = re.sub(r"\s+", "", target_text).lower()
    identity_text = "\n".join(filter(None, [
        str(target_job.get("taskName") or ""),
        str(target_job.get("file") or ""),
        str(target_job.get("failureReason") or target_job.get("error") or ""),
    ]))
    identity_compact = re.sub(r"\s+", "", identity_text).lower()
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), dict) else {}
    constraint = artifacts.get("businessFlowConstraint") if isinstance(artifacts.get("businessFlowConstraint"), dict) else {}
    flows = [flow for flow in (constraint.get("businessFlows") or []) if isinstance(flow, dict)]

    def flow_matches(flow, compact_target):
        labels = _agent_plan_text_list([flow.get("branch"), flow.get("name")])
        label_compacts = [
            re.sub(r"\s+", "", str(item)).lower()
            for item in labels
            if len(str(item).strip()) >= 2
        ]
        return any(label in compact_target for label in label_compacts)

    matched_flows = [flow for flow in flows if flow_matches(flow, identity_compact)]
    if not matched_flows:
        matched_flows = [flow for flow in flows if flow_matches(flow, target_compact)]

    sibling_names = list(dict.fromkeys(
        str(flow.get("branch") or flow.get("name") or "").strip()
        for flow in flows
        if str(flow.get("branch") or flow.get("name") or "").strip()
    ))
    branch_queries = []
    for flow in matched_flows:
        if not isinstance(flow, dict):
            continue
        branch_parts = []
        for value in (flow.get("branch"), flow.get("name"), flow.get("steps"), flow.get("checks")):
            branch_parts.extend(_agent_plan_text_list(value))
        branch_query = "\n".join(dict.fromkeys(branch_parts)).strip()
        if branch_query:
            branch_name = str(flow.get("branch") or flow.get("name") or "").strip()
            branch_queries.append({
                "id": str(flow.get("id") or clean_id(branch_name, f"repair_branch_{len(branch_queries) + 1}")),
                "name": branch_name,
                "query": branch_query[:2000],
                "anchors": baseline_branch_anchor_terms(branch_name, sibling_names),
            })
    try:
        rows = search_diverse_baseline_examples(
            target_text,
            branch_queries=branch_queries,
            module=str(target_job.get("module") or ""),
            limit=limit,
            per_branch=max(3, min(6, safe_int(limit, 3))),
        )
    except Exception:
        rows = []
    return [
        {
            "id": item.get("id") or "",
            "title": item.get("title") or item.get("file") or "",
            "file": item.get("file") or "",
            "provenancePath": item.get("provenancePath") or item.get("file") or "",
            "sourceKind": item.get("sourceKind") or "",
            "verificationStatus": item.get("verificationStatus") or "",
            "sourceTrust": item.get("sourceTrust") or 0,
            "businessPath": item.get("businessPath") or item.get("baseline_path") or "",
            "retrievalRoles": item.get("retrievalRoles") or [],
            "retrievalQueries": item.get("retrievalQueries") or [],
            "retrievalBranchIds": item.get("retrievalBranchIds") or [],
            "retrievalAnchors": item.get("retrievalAnchors") or [],
            "actions": item.get("actions") or [],
            "snippet": str(item.get("snippet") or "")[:2400],
        }
        for item in rows[:limit]
    ]


def _agent_repair_semantic_document(yaml_text):
    if pyyaml is None or not str(yaml_text or "").strip():
        return None
    try:
        parsed = pyyaml.safe_load(yaml_text)
    except Exception:
        return None
    if not isinstance(parsed, dict):
        return None
    for platform_key in ("android", "web", "ios", "computer"):
        platform_block = parsed.get(platform_key)
        if not isinstance(platform_block, dict) or not isinstance(platform_block.get("tasks"), list):
            continue
        parsed = {key: value for key, value in parsed.items() if key != platform_key}
        parsed["tasks"] = platform_block.get("tasks")
        interface_config = {key: value for key, value in platform_block.items() if key != "tasks"}
        if interface_config:
            parsed["interfaceConfig"] = {platform_key: interface_config}
        break

    def normalize(value, parent_key=""):
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                if parent_key == "tasks" and key in ("name", "description", "tags"):
                    continue
                if key == "flow" and isinstance(child, list):
                    result[key] = [
                        normalize(step, "flow")
                        for step in child
                        if not (
                            isinstance(step, dict)
                            and "sleep" in step
                            and set(step).issubset({"sleep", "timeout"})
                        )
                    ]
                else:
                    result[key] = normalize(child, str(key))
            return result
        if isinstance(value, list):
            return [normalize(item, parent_key) for item in value]
        return value

    return normalize(parsed)


def _agent_repair_has_semantic_change(original_yaml, fixed_yaml):
    original = _agent_repair_semantic_document(original_yaml)
    fixed = _agent_repair_semantic_document(fixed_yaml)
    return original is not None and fixed is not None and original != fixed


def _agent_repair_navigation_signature(yaml_text):
    """Extract navigation-like actions so evidence gates do not depend on AI prose."""
    if pyyaml is None:
        return None
    try:
        _platform, tasks = extract_midscene_tasks(pyyaml.safe_load(str(yaml_text or "")))
    except Exception:
        return None
    signature = []
    navigation_actions = {"aiTap", "tap", "ai", "aiAction", "aiAct"}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        for step in task.get("flow") or []:
            if not isinstance(step, dict):
                continue
            for action, value in step.items():
                if action not in navigation_actions:
                    continue
                try:
                    normalized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                except Exception:
                    normalized = str(value or "")
                signature.append((action, normalized))
    return signature


def _agent_repair_navigation_missing_ready_wait(yaml_text):
    """Detect a first AI navigation action that runs immediately after app launch."""
    if pyyaml is None:
        return False
    try:
        _platform, tasks = extract_midscene_tasks(pyyaml.safe_load(str(yaml_text or "")))
    except Exception:
        return False
    navigation_actions = {"aiTap", "tap", "ai", "aiAction", "aiAct"}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        flow = [step for step in (task.get("flow") or []) if isinstance(step, dict)]
        launch_index = -1
        for index, step in enumerate(flow):
            if "launch" in step:
                launch_index = index
                continue
            if not navigation_actions.intersection(step):
                continue
            guard_start = launch_index + 1 if launch_index >= 0 else 0
            if not any("aiWaitFor" in previous for previous in flow[guard_start:index]):
                return True
            break
    return False


def _agent_repair_flow_records(yaml_text):
    """Return ordered Midscene flow actions for source-backed repair checks."""
    if pyyaml is None:
        return []
    try:
        _platform, tasks = extract_midscene_tasks(pyyaml.safe_load(str(yaml_text or "")))
    except Exception:
        return []
    result = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        records = []
        for index, step in enumerate(task.get("flow") or []):
            if not isinstance(step, dict):
                continue
            action = next((key for key in MIDSCENE_FLOW_ACTIONS if key in step), "")
            if not action:
                continue
            text = str(step.get(action) or "").strip()
            records.append({
                "index": index,
                "action": action,
                "text": text,
                "compact": re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", text).lower(),
            })
        result.append(records)
    return result


def _agent_repair_source_navigation_issues(original_yaml, fixed_yaml, source_evidence):
    """Prevent a repair baseline from replacing an already adopted source leaf."""
    source_evidence = source_evidence if isinstance(source_evidence, dict) else {}
    visual_items = []
    for item in source_evidence.get("visualCurrentPageEvidence") or []:
        if not isinstance(item, dict) or item.get("sameBranch") is not True:
            continue
        try:
            confidence = float(item.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = {"high": 0.9, "medium": 0.6, "low": 0.3}.get(
                str(item.get("confidence") or "").strip().lower(),
                0.0,
            )
        if confidence >= 0.75:
            visual_items.append(item)
    if not visual_items:
        return []
    original_records = _agent_repair_flow_records(original_yaml)
    fixed_records = _agent_repair_flow_records(fixed_yaml)
    if not original_records or not fixed_records:
        return []
    original_compact = re.sub(
        r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(original_yaml or "")
    ).lower()
    navigation_actions = {"aiTap", "tap", "ai", "aiAction", "aiAct"}
    observation_actions = {"aiWaitFor", "aiAssert"}
    issues = []
    seen = set()
    for item in visual_items:
        leaf = str(item.get("navigationLeaf") or "").strip()
        target = str(item.get("targetText") or "").strip()
        leaf_key = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", leaf).lower()
        target_key = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", target).lower()
        case_key = re.sub(
            r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", str(item.get("caseId") or "")
        ).lower()
        if not leaf_key or not target_key or (case_key and case_key not in original_compact):
            continue
        for task_index, original_task in enumerate(original_records):
            original_leaf = [
                row for row in original_task
                if row.get("action") in navigation_actions and leaf_key in row.get("compact", "")
            ]
            if not original_leaf or not any(target_key in row.get("compact", "") for row in original_task):
                continue
            fixed_task = fixed_records[task_index] if task_index < len(fixed_records) else []
            fixed_leaf = [
                row for row in fixed_task
                if row.get("action") in navigation_actions and leaf_key in row.get("compact", "")
            ]
            if not fixed_leaf:
                code = "source_backed_navigation_target_removed"
                if code not in seen:
                    seen.add(code)
                    issues.append({
                        "code": code,
                        "message": (
                            f"当前 YAML 已采用视觉 AI 证据支持的导航叶子“{leaf}”，修复候选不得用历史基线的"
                            "同类尺寸、模式或产品值替换它"
                        ),
                    })
                continue
            first_leaf_index = min(row.get("index", 0) for row in fixed_leaf)
            target_checks = [
                row for row in fixed_task
                if row.get("action") in observation_actions and target_key in row.get("compact", "")
            ]
            if target_checks and first_leaf_index > min(row.get("index", 0) for row in target_checks):
                code = "source_backed_leaf_after_target_check"
                if code not in seen:
                    seen.add(code)
                    issues.append({
                        "code": code,
                        "message": (
                            f"视觉 AI 已确认目标“{target}”位于“{leaf}”页面；必须先通过可见文字进入该叶子，"
                            "再执行目标等待或断言"
                        ),
                    })
    return issues


_AGENT_REPAIR_NAVIGATION_SUBJECT_PATTERN = (
    r"(?:navigation(?:\s+path)?|route|parent\s+page|intermediate\s+page|"
    r"导航|父页面|中间页面|页面层级|页面路径|业务路径|父子页面|aitap|点击)"
)
_AGENT_REPAIR_NAVIGATION_MUTATION_PATTERN = (
    r"(?:add(?:ed|ing)?|change(?:d|ing)?|modify|modified|adjust(?:ed|ing)?|"
    r"rewrite|rewrote|replace(?:d)?|fix(?:ed)?|complete(?:d)?|"
    r"新增|增加|补充|补齐|补全|修正|修改|调整|改写|替换|重建|优化)"
)
_AGENT_REPAIR_NAVIGATION_UNCHANGED_PATTERNS = (
    r"(?:保持|保留|沿用)\s*(?:原有|现有|已有|当前)?\s*"
    + _AGENT_REPAIR_NAVIGATION_SUBJECT_PATTERN
    + r"[^，。；;\n]{0,30}?(?:不变|未变|不修改|未修改|保持不变)",
    r"(?:未|没有|并未|无需)\s*(?:修改|调整|改写|替换|变更|新增|增加|补充)\s*"
    + _AGENT_REPAIR_NAVIGATION_SUBJECT_PATTERN,
    _AGENT_REPAIR_NAVIGATION_SUBJECT_PATTERN + r"[^，。；;\n]{0,20}?(?:不变|未变|未修改|未调整)",
    r"(?:preserve|keep|retain)\s+(?:the\s+)?(?:existing|original|current)?\s*"
    + _AGENT_REPAIR_NAVIGATION_SUBJECT_PATTERN
    + r"(?:\s+unchanged)?",
    _AGENT_REPAIR_NAVIGATION_SUBJECT_PATTERN + r"\s+(?:is\s+)?(?:unchanged|unmodified|preserved)",
    r"(?:did\s+not|do\s+not|without)\s+"
    + _AGENT_REPAIR_NAVIGATION_MUTATION_PATTERN
    + r"[^.;\n]{0,16}?"
    + _AGENT_REPAIR_NAVIGATION_SUBJECT_PATTERN,
)


def _agent_repair_claims_navigation_change(change_text):
    """Recognize a positive navigation mutation claim, not a mention of navigation."""
    normalized = str(change_text or "").strip().lower()
    if not normalized:
        return False
    for pattern in _AGENT_REPAIR_NAVIGATION_UNCHANGED_PATTERNS:
        normalized = re.sub(pattern, " ", normalized, flags=re.IGNORECASE)
    nearby = r"[^，。；;\n]{0,24}?"
    return bool(
        re.search(
            _AGENT_REPAIR_NAVIGATION_MUTATION_PATTERN
            + nearby
            + _AGENT_REPAIR_NAVIGATION_SUBJECT_PATTERN,
            normalized,
            flags=re.IGNORECASE,
        )
        or re.search(
            _AGENT_REPAIR_NAVIGATION_SUBJECT_PATTERN
            + nearby
            + _AGENT_REPAIR_NAVIGATION_MUTATION_PATTERN,
            normalized,
            flags=re.IGNORECASE,
        )
    )


def _agent_repair_correction_feedback(candidate_gate, response, yaml_limit=24000):
    """Build one bounded correction prompt from exact rejected-candidate evidence."""
    candidate_gate = candidate_gate if isinstance(candidate_gate, dict) else {}
    response = response if isinstance(response, dict) else {}
    details = []

    def append_detail(prefix, value):
        text = str(value or "").strip()
        if not text:
            return
        detail = f"{prefix}{text}"
        if detail not in details:
            details.append(detail[:4000])

    for issue in candidate_gate.get("issues") or []:
        if not isinstance(issue, dict):
            append_detail("平台门禁：", issue)
            continue
        code = str(issue.get("code") or "candidate_gate").strip()
        append_detail(f"平台门禁 [{code}]：", issue.get("message") or code)
    append_detail("模型调用错误：", response.get("error"))
    for label, validation in (
        ("AI Gateway 校验", candidate_gate.get("aiGatewayValidation")),
        ("Task Server 校验", candidate_gate.get("yamlValidation")),
    ):
        if not isinstance(validation, dict):
            continue
        for field in ("errors", "issues"):
            values = validation.get(field) or []
            if not isinstance(values, (list, tuple)):
                values = [values]
            for issue in values:
                append_detail(f"{label}：", issue)

    previous_yaml = str(
        candidate_gate.get("rawFixedYaml")
        or candidate_gate.get("fixedYaml")
        or ""
    ).strip()
    previous_analysis = str(response.get("analysis") or "").strip()
    previous_changes = response.get("changes") or []
    if not isinstance(previous_changes, list):
        previous_changes = [previous_changes]
    correction_text = (
        "\n\n上一次修复候选被平台拒绝。这是本任务唯一一次有界纠错，请根据以下原始证据"
        "修正候选，不要重新设计业务路径：\n- "
        + "\n- ".join(details or ["模型未返回可校验的完整 YAML"])
    )
    if previous_analysis:
        correction_text += "\n上一次 analysis：" + previous_analysis[:3000]
    if previous_changes:
        correction_text += "\n上一次 changes：" + json.dumps(
            previous_changes[:20], ensure_ascii=False, separators=(",", ":")
        )[:3000]
    if previous_yaml:
        excerpt = previous_yaml[:max(1000, safe_int(yaml_limit, 24000))]
        suffix = "\n[候选过长，已截断]" if len(previous_yaml) > len(excerpt) else ""
        correction_text += (
            "\n上一次被拒 YAML（只用于纠错，必须返回修正后的完整 YAML）：\n"
            "--- rejected yaml begin ---\n"
            + excerpt
            + suffix
            + "\n--- rejected yaml end ---"
        )
    correction_text += (
        "\n请逐条消除精确校验错误，并返回完整、可解析、符合 Midscene 契约的 YAML。"
        "analysis/changes 必须与真实 YAML diff 一致；若没有修改导航，应明确写导航保持不变，"
        "不要声称新增点击或补齐路径。YAML 标量内引用界面文案时，使用中文引号“”或合法的"
        "单引号/转义，禁止在双引号标量中嵌入未转义的 ASCII 双引号。"
        "若使用 aiScroll，aiScroll 本身必须是描述可见滚动区域的非空字符串，"
        "direction/distance/scrollType 是同一 flow item 的同级字段。"
    )
    return details, correction_text


def _agent_repair_candidate_gate(
    original_yaml,
    response,
    baseline_examples,
    platform="android",
    source_evidence=None,
):
    """Audit AI repair prose, baseline evidence and executable YAML as one candidate."""
    response = response if isinstance(response, dict) else {}
    baseline_examples = [item for item in (baseline_examples or []) if isinstance(item, dict)]
    raw_fixed_yaml = str(
        response.get("fixedYaml")
        or response.get("fixed_yaml")
        or response.get("optimizedYaml")
        or response.get("yaml")
        or ""
    ).strip()
    fixed_yaml = ""
    if raw_fixed_yaml:
        fixed_yaml = ensure_midscene_platform_root(
            remove_empty_midscene_platform_roots(raw_fixed_yaml),
            platform=platform,
        ).strip()
    used_baseline_ids = list(dict.fromkeys(
        str(item).strip()
        for item in (response.get("usedBaselineIds") or [])
        if str(item or "").strip()
    ))
    known_baseline_ids = {
        str(item.get("id") or "").strip()
        for item in baseline_examples
        if str(item.get("id") or "").strip()
    }
    branch_baseline_ids = {
        str(item.get("id") or "").strip()
        for item in baseline_examples
        if str(item.get("id") or "").strip()
        and "business_branch" in (item.get("retrievalRoles") or [])
        and (item.get("retrievalBranchIds") or [])
    }
    invalid_baseline_ids = [item for item in used_baseline_ids if item not in known_baseline_ids]
    change_text = " ".join(
        _agent_plan_text_list(response.get("analysis"), limit=20)
        + _agent_plan_text_list(response.get("changes"), limit=20)
    ).lower()
    navigation_claimed = _agent_repair_claims_navigation_change(change_text)
    original_navigation = _agent_repair_navigation_signature(original_yaml)
    fixed_navigation = _agent_repair_navigation_signature(fixed_yaml)
    original_assertion_contract = strict_visible_value_contract(original_yaml)
    fixed_assertion_contract = strict_visible_value_contract(fixed_yaml)
    fixed_assertion_values = {
        str(item.get("value") or "").strip().casefold()
        for item in fixed_assertion_contract
        if str(item.get("value") or "").strip()
    }
    missing_assertion_contract = [
        item for item in original_assertion_contract
        if str(item.get("value") or "").strip().casefold() not in fixed_assertion_values
    ]
    navigation_changed = bool(
        original_navigation is not None
        and fixed_navigation is not None
        and original_navigation != fixed_navigation
    )
    ai_validation = response.get("validation") if isinstance(response.get("validation"), dict) else {}
    validation = {}
    gateway_validation_failed = False
    if fixed_yaml:
        validation = validate_midscene_yaml_executability(fixed_yaml)
        validation["validatedBy"] = "task_server"
        gateway_validation_failed = bool(
            ai_validation
            and (
                ai_validation.get("valid") is False
                or ("valid" not in ai_validation and ai_validation.get("success") is False)
            )
        )
        if gateway_validation_failed:
            gateway_issues = [
                str(item).strip()
                for item in (ai_validation.get("errors") or ai_validation.get("issues") or [])
                if str(item or "").strip()
            ]
            validation["ok"] = False
            validation["issues"] = list(dict.fromkeys(
                gateway_issues + list(validation.get("issues") or [])
            ))
            validation["gatewayRejected"] = True

    issues = []

    def add_issue(code, message):
        if code not in {item.get("code") for item in issues}:
            issues.append({"code": code, "message": message})

    if not fixed_yaml:
        add_issue("ai_no_yaml", "AI 未返回完整修复 YAML")
    if fixed_yaml and missing_assertion_contract:
        missing_values = list(dict.fromkeys(
            str(item.get("value") or "").strip()
            for item in missing_assertion_contract
            if str(item.get("value") or "").strip()
        ))
        add_issue(
            "assertion_contract_drift",
            "修复候选删除、弱化或改写了原始精确可见文案断言："
            + "、".join(f"“{value}”" for value in missing_values[:5])
            + "；Runner 当前展示值只能作为产品差异证据，不能替换需求期望值",
        )
    if invalid_baseline_ids:
        add_issue("unknown_baseline_citation", "引用了本次候选之外的基线：" + "、".join(invalid_baseline_ids[:3]))
    if fixed_yaml and navigation_claimed and not navigation_changed:
        add_issue(
            "navigation_claim_without_yaml_change",
            "changes/analysis 声称修正导航，但 YAML 的 aiTap/ai/aiAction/aiAct 路径没有变化",
        )
    if fixed_yaml and navigation_changed and baseline_examples and not used_baseline_ids:
        add_issue("navigation_change_without_baseline_citation", "导航发生变化，但没有引用本次可信路径基线")
    if (
        fixed_yaml
        and navigation_changed
        and branch_baseline_ids
        and not branch_baseline_ids.intersection(used_baseline_ids)
    ):
        add_issue("navigation_change_without_branch_baseline", "导航修改没有引用当前业务分支召回的路径基线")
    if fixed_yaml:
        for source_issue in _agent_repair_source_navigation_issues(
            original_yaml,
            fixed_yaml,
            source_evidence,
        ):
            add_issue(source_issue.get("code"), source_issue.get("message"))
    if fixed_yaml and gateway_validation_failed:
        add_issue("ai_gateway_validation_failed", "AI Gateway 判定修复 YAML 不符合 Midscene 契约")
    elif fixed_yaml and not validation.get("ok"):
        add_issue("yaml_validation_failed", "修复 YAML 未通过 Task Server 可执行校验")
    elif fixed_yaml and navigation_changed and _agent_repair_navigation_missing_ready_wait(fixed_yaml):
        add_issue(
            "navigation_missing_ready_wait",
            "新增或改写首个导航动作前缺少 aiWaitFor 起始页稳定态，应用仍在启动加载时会立即定位失败",
        )
    elif fixed_yaml and not _agent_repair_has_semantic_change(original_yaml, fixed_yaml):
        add_issue("sleep_only_or_noop", "候选只增加 sleep、修改说明或与原 YAML 执行语义等价")

    return {
        "rawFixedYaml": raw_fixed_yaml,
        "fixedYaml": fixed_yaml,
        "usedBaselineIds": used_baseline_ids,
        "invalidBaselineIds": invalid_baseline_ids,
        "branchBaselineIds": sorted(branch_baseline_ids),
        "navigationClaimed": navigation_claimed,
        "navigationChanged": navigation_changed,
        "originalAssertionContract": original_assertion_contract,
        "fixedAssertionContract": fixed_assertion_contract,
        "missingAssertionContract": missing_assertion_contract,
        "assertionContractPreserved": not missing_assertion_contract,
        "aiGatewayValidation": ai_validation,
        "yamlValidation": validation,
        "issues": issues,
        "ok": not issues,
    }


def _normalize_agent_failed_items(items):
    normalized = []
    seen = set()
    for item in items or []:
        row = _normalize_failed_execution_item(item)
        if not row:
            continue
        key = row.get("jobId") or f"{row.get('module')}::{row.get('file')}::{row.get('taskName')}"
        if not key or key in seen:
            continue
        seen.add(key)
        normalized.append(row)
    return normalized


def _tool_analyze_failure(run, failed_jobs_override=None):
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
        if failed_jobs_override is None:
            failed_jobs = _agent_persist_failed_execution_items(run)
        else:
            failed_jobs = _normalize_agent_failed_items(failed_jobs_override)
            artifacts["failedExecutionItems"] = failed_jobs
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
        failure_type_counts = _agent_failure_type_counts(failed_jobs)

        if has_job_failures:
            # Runner 最新终态优先于准备阶段的旧体检诊断。
            job_failure_types = {
                _agent_canonical_failure_type(item.get("failureType")) or "UNKNOWN"
                for item in failed_jobs
                if isinstance(item, dict)
            }
            if "ENV_ISSUE" in job_failure_types:
                failure_type = "ENV_ISSUE"
            elif "PRODUCT_BUG" in job_failure_types:
                failure_type = "PRODUCT_BUG"
            elif "SCRIPT_ISSUE" in job_failure_types:
                failure_type = "SCRIPT_ISSUE"
            else:
                failure_type = "UNKNOWN"
            failure_context = f"执行失败 {len(failed_jobs)} 个任务:\n"
            for fj in failed_jobs[:12]:
                failure_context += (
                    f"- {fj.get('module', '')}/{fj.get('file', '')} ({fj.get('status', '')})"
                    f"[{fj.get('failureType') or ''}]：{fj.get('error', '')}\n"
                )
                stderr = fj.get("stderrTail") or fj.get("stderr_tail") or ""
                if stderr:
                    failure_context += f"  stderr: {stderr[:200]}\n"
                summary_text = fj.get("summaryText") or ""
                if summary_text:
                    failure_context += f"  summary: {summary_text[:600]}\n"
        elif has_sonic_failures:
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

        # 构建本地分析结果
        analysis = {
            "failureType": failure_type,
            "failureTypeCounts": failure_type_counts,
            "mixedFailureTypes": len(failure_type_counts) > 1,
            "summary": failure_context[:500],
            "conclusion": "",
            "recommendation": "",
        }

        # 尝试调用 AI Gateway 分析
        if _ai_gateway_available():
            try:
                failure_payload = _agent_failure_ai_payload(run, failure_type, failure_context, failed_jobs)
                result = _ai_gateway_post(
                    "/ai/analyze-failure",
                    failure_payload,
                    timeout=max(30, safe_int(os.getenv("MIDSCENE_AGENT_FAILURE_ANALYSIS_TIMEOUT_SECONDS"), 90)),
                )
                analysis["modelTrace"] = _agent_ai_response_model_trace(run, result)
                analysis["evidence"] = {
                    "reportKeyframes": failure_payload.get("reportKeyframes") or [],
                    "reportKeyframeCount": len(failure_payload.get("reportKeyframes") or []),
                    "baselineExamples": [
                        {
                            "id": item.get("id"),
                            "provenancePath": item.get("provenancePath"),
                            "businessPath": item.get("businessPath"),
                        }
                        for item in (failure_payload.get("baselineExamples") or [])[:6]
                    ],
                    "sources": failure_payload.get("evidenceSources") or [],
                }
                if isinstance(result, dict):
                    analysis["conclusion"] = result.get("conclusion") or result.get("analysis", "")
                    analysis["recommendation"] = result.get("recommendation") or result.get("suggestion", "")
                    analysis["aiEvidence"] = result.get("evidence") if isinstance(result.get("evidence"), list) else []
                    if "canAutoRepair" in result:
                        analysis["canAutoRepair"] = bool(result.get("canAutoRepair"))
                    # AI 可能返回更准确的失败类型
                    ai_failure_type = _agent_canonical_failure_type(result.get("failureType"))
                    environment_locked = any(
                        _agent_failed_item_has_concrete_environment_evidence(item)
                        for item in failed_jobs
                    )
                    product_locked = any(
                        _agent_canonical_failure_type(item.get("failureType") or item.get("failure_type")) == "PRODUCT_BUG"
                        or (
                            _agent_failure_type_from_review(_agent_failure_review(item)) == "PRODUCT_BUG"
                            and _agent_high_confidence_failure_review(_agent_failure_review(item))
                        )
                        for item in failed_jobs
                    )
                    if (
                        ai_failure_type
                        and ai_failure_type != "UNKNOWN"
                        and not environment_locked
                        and not product_locked
                    ):
                        analysis["failureType"] = ai_failure_type
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


def _tool_generate_repair(run, failed_jobs_override=None):
    """只对 SCRIPT_ISSUE 类型生成可追溯的 YAML 修复草稿。"""
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "generate_repair_draft",
        "category": "AI",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {},
    }
    try:
        artifacts = run.setdefault("artifacts", {})
        fa = artifacts.get("failureAnalysis") if isinstance(artifacts.get("failureAnalysis"), dict) else {}
        ft = _agent_canonical_failure_type(fa.get("failureType")) or "UNKNOWN"
        if failed_jobs_override is None:
            failed_jobs = _agent_persist_failed_execution_items(run)
        else:
            failed_jobs = _normalize_agent_failed_items(failed_jobs_override)
            artifacts["failedExecutionItems"] = failed_jobs
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
        ai_timeout = max(90, safe_int(os.getenv("MIDSCENE_AGENT_REPAIR_TIMEOUT_SECONDS"), 120))
        source_evidence = _agent_source_evidence(run)
        saved_drafts = []
        summary_items = []
        ai_attempted_count = 0
        ai_request_count = 0
        ai_correction_attempted_count = 0
        ai_used_count = 0
        validation_passed_count = 0
        blocked_count = 0

        try:
            from task_server.services import repair_service
        except Exception:
            repair_service = None

        for index, target_job in enumerate(repair_targets, start=1):
            item_eligibility = _agent_repair_eligibility(
                target_job,
                fallback_failure_type=ft,
                fallback_can_auto_repair=(
                    fa.get("canAutoRepair")
                    if len(failed_jobs) == 1 and "canAutoRepair" in fa
                    else None
                ),
            )
            item_failure_type = item_eligibility.get("failureType") or "UNKNOWN"
            module = str(target_job.get("module") or fa.get("module") or "").strip()
            file_name = clean_filename(target_job.get("file") or fa.get("file") or "")
            task_name = str(target_job.get("taskName") or fa.get("taskName") or fa.get("task_name") or "").strip()
            original_yaml = ""
            if module and file_name:
                try:
                    original_yaml = read_text_file(safe_join(TASK_DIR, module, file_name), default="")
                except Exception:
                    original_yaml = ""
            assertion_contract = strict_visible_value_contract(original_yaml)
            report_keyframes = (
                _agent_failure_report_keyframes(target_job, limit=3)
                if item_eligibility.get("eligible") else []
            )
            report_keyframe_names = [item.get("name") or "report-keyframe" for item in report_keyframes]
            baseline_examples = (
                _agent_repair_baseline_examples(run, target_job, original_yaml, limit=3)
                if item_eligibility.get("eligible") else []
            )
            assertion_contract_text = "、".join(
                f"“{item.get('value')}”"
                for item in assertion_contract
                if str(item.get("value") or "").strip()
            )
            evidence_parts = [
                f"失败类型：{item_failure_type}",
                f"失败序号：{index}/{len(failed_jobs)}",
                f"Agent 目标：{run.get('target', '')}",
                f"失败用例：{task_name or file_name}",
                f"失败原因：{target_job.get('failureReason') or fa.get('summary') or target_job.get('error') or ''}",
                f"不可变精确文案断言：{assertion_contract_text}" if assertion_contract_text else "",
                f"Runner 错误：{target_job.get('error') or ''}",
                f"stderr：{target_job.get('stderrTail') or target_job.get('stderr_tail') or ''}",
                f"stdout：{target_job.get('stdoutTail') or target_job.get('stdout_tail') or ''}",
                f"summary：{target_job.get('summaryText') or ''}",
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
                "type": item_failure_type,
                "failureType": item_failure_type,
                "sourceCanAutoRepair": item_eligibility.get("canAutoRepair"),
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
                "reportKeyframes": report_keyframe_names,
                "baselineExamples": [
                    {key: item.get(key) for key in (
                        "id", "title", "file", "provenancePath", "sourceKind", "verificationStatus",
                        "businessPath", "retrievalRoles", "retrievalBranchIds", "retrievalAnchors",
                    )}
                    for item in baseline_examples
                ],
            }
            item_summary = {
                "draftId": draft_id,
                "targetJobId": draft.get("jobId", ""),
                "targetTaskName": task_name,
                "module": module,
                "file": file_name,
                "failureType": item_failure_type,
                "canAutoRepair": item_eligibility.get("canAutoRepair"),
                "failureReason": target_job.get("failureReason") or target_job.get("error") or "",
                "aiAttempted": False,
                "aiUsed": False,
                "yamlValidation": {},
                "changes": [],
                "repairSource": "not_started",
                "reportKeyframes": report_keyframe_names,
                "reportKeyframeCount": len(report_keyframe_names),
                "selectedBaselines": [item.get("provenancePath") or item.get("file") for item in baseline_examples],
            }

            if not item_eligibility.get("eligible"):
                blocked_count += 1
                item_summary["blockedReason"] = item_eligibility.get("code") or "failure_type_not_repairable"
                item_summary["repairEligibilityReason"] = item_eligibility.get("reason") or ""
                draft["blockedReason"] = item_summary["blockedReason"]
                draft["analysis"] = (
                    f"{draft.get('analysis') or ''}\n{item_eligibility.get('reason') or ''}"
                ).strip()
                draft["repairSource"] = "diagnosis_only"
                draft["status"] = "REJECTED"
            elif not original_yaml.strip():
                blocked_count += 1
                item_summary["blockedReason"] = "missing_original_yaml"
                draft["analysis"] = "未找到原始 YAML；仅保留失败证据，无法生成可应用修复。"
                draft["repairSource"] = "diagnosis_only"
                draft["status"] = "REJECTED"
            elif ai_available:
                ai_attempted_count += 1
                item_summary["aiAttempted"] = True
                resp = None
                request_payload = {
                    "yaml": original_yaml,
                    "target": run.get("target", ""),
                    "requirement": _agent_plan_requirement_text(run),
                    "taskName": task_name,
                    "failureAnalysis": evidence,
                    "issues": evidence,
                    "allFailedJobs": failed_jobs[:30],
                    "imageAssets": report_keyframes,
                    "reportKeyframes": report_keyframe_names,
                    "baselineExamples": baseline_examples,
                    "sourceEvidence": source_evidence,
                    "repairPolicy": {
                        "alignReportFramesWithBaselinePath": True,
                        "requireBaselineCitationForNavigationChange": True,
                        "requireProseYamlDiffConsistency": True,
                        "requireReadyWaitBeforeNewNavigation": True,
                        "visibleTextOnly": True,
                        "preserveOriginalBusinessGoal": True,
                        "preserveExactVisibleValueAssertions": True,
                        "exactVisibleValueAssertions": assertion_contract,
                        "preserveSourceBackedNavigationTargets": True,
                        "baselineConcreteValuesAreExamplesOnly": True,
                        "preferEarlierRuntimeTargetRegion": True,
                    },
                    "evidenceSources": [
                        "原始需求", "Figma 同帧软证据", "原始 YAML", "Runner 日志",
                        "failureReview", "Midscene 报告关键帧", "可信分支基线",
                    ],
                    "executionConstraint": {
                        "runnerId": run.get("runnerId") or "",
                        "deviceId": run.get("deviceId") or "",
                        "deviceStrategy": run.get("deviceStrategy") or "",
                        "allowOtherDevices": not bool(run.get("deviceId") and str(run.get("deviceStrategy") or "").lower() == "fixed"),
                    },
                    **_agent_ai_route_payload(run, has_images=bool(report_keyframes)),
                }
                candidate_gate = None
                ai_attempt_errors = []
                for repair_attempt in range(2):
                    try:
                        ai_request_count += 1
                        resp = _ai_gateway_post(
                            "/ai/optimize-yaml",
                            request_payload,
                            timeout=ai_timeout if repair_attempt == 0 else min(ai_timeout, 75),
                            include_error=True,
                        )
                    except Exception as e:
                        resp = {"error": str(e)[:300]}
                    if isinstance(resp, dict) and resp.get("error"):
                        ai_attempt_errors.append({
                            "attempt": repair_attempt + 1,
                            "error": str(resp.get("error"))[:500],
                            "errorType": str(resp.get("errorType") or ""),
                            "httpStatus": safe_int(resp.get("httpStatus"), 0),
                        })
                    candidate_gate = _agent_repair_candidate_gate(
                        original_yaml,
                        resp,
                        baseline_examples,
                        platform=run.get("platform", "android"),
                        source_evidence=source_evidence,
                    )
                    if candidate_gate.get("ok") or repair_attempt > 0:
                        break
                    correction_issues, correction_text = _agent_repair_correction_feedback(
                        candidate_gate,
                        resp,
                    )
                    item_summary["aiCorrectionAttempted"] = True
                    item_summary["aiCorrectionIssues"] = correction_issues
                    ai_correction_attempted_count += 1
                    request_payload = dict(request_payload)
                    request_payload["failureAnalysis"] = evidence + correction_text
                    request_payload["issues"] = evidence + correction_text
                    request_payload["candidateValidationIssues"] = candidate_gate.get("issues") or []
                    request_payload["repairPolicy"] = {
                        **request_payload["repairPolicy"],
                        "boundedCorrectionAttempt": 1,
                        "requireCompleteYamlResponse": True,
                    }
                    request_payload["imageAssets"] = report_keyframes[-2:]
                    request_payload["reportKeyframes"] = report_keyframe_names[-2:]
                    request_payload["baselineExamples"] = baseline_examples[:3]
                    request_payload["allFailedJobs"] = [{
                        key: target_job.get(key)
                        for key in (
                            "jobId", "taskName", "module", "file", "failureType",
                            "failureReason", "error", "summaryText", "stderrTail", "stdoutTail",
                        )
                        if target_job.get(key) not in (None, "", [], {})
                    }]

                item_summary["aiRequestCount"] = 2 if item_summary.get("aiCorrectionAttempted") else 1
                candidate_gate = candidate_gate or _agent_repair_candidate_gate(
                    original_yaml,
                    resp,
                    baseline_examples,
                    platform=run.get("platform", "android"),
                    source_evidence=source_evidence,
                )
                fixed_yaml = candidate_gate.get("fixedYaml") or ""
                model_trace = _agent_ai_response_model_trace(run, resp)
                item_summary["modelTrace"] = model_trace
                draft["modelTrace"] = model_trace
                if ai_attempt_errors:
                    item_summary["aiAttemptErrors"] = ai_attempt_errors
                    draft["aiAttemptErrors"] = ai_attempt_errors
                if isinstance(resp, dict):
                    if resp.get("changes"):
                        changes = resp.get("changes")
                        item_summary["changes"] = changes if isinstance(changes, list) else [str(changes)]
                    if resp.get("analysis"):
                        draft["aiAnalysis"] = str(resp.get("analysis"))[:4000]
                    if resp.get("diff") or resp.get("diff_summary"):
                        draft["diff"] = resp.get("diff") or resp.get("diff_summary")
                    if resp.get("error"):
                        item_summary["aiError"] = str(resp.get("error"))[:500]
                        draft["aiError"] = str(resp.get("error"))[:500]
                item_summary["usedBaselineIds"] = candidate_gate.get("usedBaselineIds") or []
                item_summary["branchBaselineIds"] = candidate_gate.get("branchBaselineIds") or []
                item_summary["navigationChanged"] = bool(candidate_gate.get("navigationChanged"))
                item_summary["navigationClaimed"] = bool(candidate_gate.get("navigationClaimed"))
                item_summary["assertionContractPreserved"] = bool(candidate_gate.get("assertionContractPreserved"))
                if candidate_gate.get("missingAssertionContract"):
                    item_summary["missingAssertionContract"] = candidate_gate.get("missingAssertionContract")
                if candidate_gate.get("invalidBaselineIds"):
                    item_summary["invalidBaselineIds"] = candidate_gate.get("invalidBaselineIds")
                ai_validation = candidate_gate.get("aiGatewayValidation") or {}
                validation = candidate_gate.get("yamlValidation") or {}
                if ai_validation:
                    draft["aiGatewayValidation"] = ai_validation
                    item_summary["aiGatewayValidation"] = ai_validation
                if validation:
                    draft["validation"] = validation
                    item_summary["yamlValidation"] = validation
                    item_summary["taskCount"] = validation.get("taskCount")
                if candidate_gate.get("ok"):
                    draft["fixedYaml"] = fixed_yaml[:200000]
                    draft["fixed_yaml"] = draft["fixedYaml"]
                    draft["draftYaml"] = draft["fixedYaml"][:5000]
                    draft["repairSource"] = "ai_gateway"
                    draft["status"] = "WAIT_CONFIRM"
                    item_summary["aiUsed"] = True
                    ai_used_count += 1
                    validation_passed_count += 1
                else:
                    blocked_count += 1
                    candidate_issues = candidate_gate.get("issues") or []
                    blocked_reason = str((candidate_issues[0] if candidate_issues else {}).get("code") or "ai_no_yaml")
                    item_summary["blockedReason"] = blocked_reason
                    item_summary["candidateValidationIssues"] = candidate_issues
                    draft["blockedReason"] = blocked_reason
                    if fixed_yaml:
                        draft["rejectedYaml"] = fixed_yaml[:200000]
                    draft["repairSource"] = "diagnosis_only"
                    draft["status"] = "REJECTED"
                    draft["analysis"] = (
                        f"{draft.get('analysis') or ''}\n"
                        "AI 修复候选未通过平台语义/证据/可执行门禁，已保留诊断证据但禁止下发 Runner。"
                    ).strip()
            else:
                blocked_count += 1
                item_summary["blockedReason"] = "ai_gateway_unavailable"
                draft["repairSource"] = "diagnosis_only"
                draft["status"] = "REJECTED"

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
            "aiRequestCount": ai_request_count,
            "aiCorrectionAttemptedCount": ai_correction_attempted_count,
            "aiUsed": ai_used_count > 0,
            "aiUsedCount": ai_used_count,
            "validationPassedCount": validation_passed_count,
            "failureType": ft,
            "evidenceSources": [
                "失败类型", "Agent 目标", "原始需求", "Figma 同帧软证据", "Runner 错误",
                "stdout/stderr 尾部", "原始 YAML", "Midscene 报告关键帧", "可信分支基线", "整批失败摘要",
            ],
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
        call["aiRequestCount"] = ai_request_count
        call["aiCorrectionAttemptedCount"] = ai_correction_attempted_count
        call["aiUsedCount"] = ai_used_count
        call["yamlValidation"] = repair_summary.get("yamlValidation")
        call["targetTaskName"] = "、".join([str(x) for x in repair_summary["targetTasks"][:3] if x])
        call["artifactRefs"] = ["repair"]
        if ai_used_count:
            call["status"] = "SUCCESS" if validation_passed_count == ai_used_count and blocked_count == 0 else "PARTIAL_FAILED"
            call["outputSummary"] = (
                f"AI 已生成 {ai_used_count}/{len(repair_targets)} 条可应用修复草稿；"
                f"本轮分析 {len(repair_targets)}/{len(failed_jobs)} 条失败任务，门禁拒绝 {blocked_count} 条"
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
        product_failures = [
            item for item in _agent_failed_execution_items(run)
            if _agent_repair_eligibility(item).get("failureType") == "PRODUCT_BUG"
        ]
        if not product_failures:
            ft = str(fa.get("failureType", "UNKNOWN")).upper()
            call["status"] = "SKIPPED"
            call["outputSummary"] = f"没有 PRODUCT_BUG 任务（汇总类型 {ft}），跳过缺陷草稿"
            return call
        product_summary = "\n".join(
            f"- {item.get('taskName') or item.get('file') or item.get('jobId')}："
            f"{item.get('failureReason') or item.get('error') or ''}"
            for item in product_failures[:10]
        )
        draft = {
            "type": "PRODUCT_BUG",
            "title": f"[{run.get('appName', '')}] {run.get('target', '')[:50]}",
            "description": f"失败分析：{product_summary[:1200]}",
            "status": "DRAFT",
            "failedJobs": [item.get("jobId") for item in product_failures if item.get("jobId")],
        }
        if _ai_gateway_available():
            try:
                resp = _ai_gateway_post("/ai/generate-bug", {
                    "failureType": "PRODUCT_BUG",
                    "summary": product_summary,
                    "jobId": product_failures[0].get("jobId", ""),
                    "failedJobs": product_failures[:10],
                    **_agent_ai_route_payload(run),
                })
                if isinstance(resp, dict):
                    draft["title"] = resp.get("title", draft["title"])
                    draft["description"] = resp.get("description", draft["description"])
                    draft["severity"] = resp.get("severity", "medium")
                draft["modelTrace"] = _agent_ai_response_model_trace(run, resp)
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
    """Return only the latest repair cycle's de-duplicated drafts."""
    artifacts = artifacts if isinstance(artifacts, dict) else {}
    repair_summary = artifacts.get("repairSummary") if isinstance(artifacts.get("repairSummary"), dict) else {}
    current_draft_ids = {
        str(item or "").strip()
        for item in (repair_summary.get("draftIds") or [])
        if str(item or "").strip()
    }
    candidates = []
    if isinstance(artifacts.get("repairDrafts"), list):
        candidates.extend(item for item in artifacts.get("repairDrafts") if isinstance(item, dict))
    if isinstance(artifacts.get("repairDraft"), dict):
        candidates.append(artifacts.get("repairDraft"))
    result = []
    seen = set()
    for item in candidates:
        item_draft_id = str(item.get("draftId") or item.get("draft_id") or "").strip()
        if current_draft_ids and item_draft_id not in current_draft_ids:
            continue
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
            return ensure_midscene_platform_root(remove_empty_midscene_platform_roots(value), platform="android").strip()
    return ""


def _agent_rerun_requires_serial_device(run):
    """Avoid concurrent rerun jobs fighting over one fixed Android device."""
    if not isinstance(run, dict):
        return False
    device_id = str(run.get("deviceId") or run.get("device_id") or "").strip()
    runner_id = str(run.get("runnerId") or run.get("runner_id") or "").strip()
    strategy = str(run.get("deviceStrategy") or run.get("device_strategy") or "").strip().lower()
    return bool(device_id or (runner_id and strategy in ("fixed", "指定设备")))


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
        draft_status = str(draft.get("status") or "").strip().upper()
        draft_failure_type = _agent_canonical_failure_type(
            draft.get("failureType") or draft.get("failure_type") or draft.get("type")
        )
        source_failure_type = (
            _agent_canonical_failure_type(source_item.get("failureType") or source_item.get("failure_type"))
            or _agent_failure_type_from_review(_agent_failure_review(source_item))
        )
        source_auto_repair = _agent_explicit_auto_repair_decision(source_item)
        draft_auto_repair = _agent_explicit_auto_repair_decision({
            "canAutoRepair": draft.get("sourceCanAutoRepair")
        }) if "sourceCanAutoRepair" in draft else None
        has_repair_classification = bool(
            draft_failure_type or source_failure_type
            or source_auto_repair is not None or draft_auto_repair is not None
        )
        rerun_eligibility = _agent_repair_eligibility(
            source_item,
            fallback_failure_type=draft_failure_type,
            fallback_can_auto_repair=(
                source_auto_repair if source_auto_repair is not None else draft_auto_repair
            ),
        )
        if draft_status in ("REJECTED", "BLOCKED") or (
            has_repair_classification and not rerun_eligibility.get("eligible")
        ):
            skipped.append({
                "draftId": draft_id,
                "jobId": source_job_id,
                "taskName": draft.get("taskName") or source_item.get("taskName") or draft.get("file") or "",
                "status": "repair_not_eligible",
                "failureType": rerun_eligibility.get("failureType") or draft_failure_type or source_failure_type or "UNKNOWN",
                "reason": (
                    "修复草稿已被平台拒绝，禁止下发 Runner"
                    if draft_status in ("REJECTED", "BLOCKED")
                    else rerun_eligibility.get("reason")
                ),
            })
            continue
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
        ai_gateway_validation = (
            draft.get("aiGatewayValidation")
            if isinstance(draft.get("aiGatewayValidation"), dict)
            else {}
        )
        if ai_gateway_validation.get("valid") is False:
            skipped.append({
                "draftId": draft_id,
                "jobId": source_job_id,
                "taskName": draft.get("taskName") or source_item.get("taskName") or draft.get("file") or "",
                "status": "ai_gateway_invalid",
                "reason": "AI Gateway 已判定修复 YAML 无效，禁止下发 Runner",
                "issues": list(ai_gateway_validation.get("errors") or ai_gateway_validation.get("issues") or [])[:12],
            })
            continue
        validation = validate_midscene_yaml_executability(fixed_yaml)
        validation["validatedBy"] = "task_server"
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


def _agent_post_rerun_autonomy(run, latest_failed, repair_depth=0):
    """Use the latest rerun evidence for one bounded AI repair cycle."""
    artifacts = run.setdefault("artifacts", {})
    latest_failed = _normalize_agent_failed_items(latest_failed)
    result = {
        "enabled": True,
        "maxRepairCycles": 1,
        "repairDepth": repair_depth,
        "latestFailureCount": len(latest_failed),
        "analyzed": False,
        "repairGenerated": False,
        "followupExecuted": False,
        "reason": "",
    }
    if repair_depth >= 1 or not latest_failed:
        result["reason"] = "已达到重跑后 AI 修复上限" if repair_depth >= 1 else "没有最新失败证据"
        artifacts["postRerunAutonomy"] = result
        return result

    analysis_call = _tool_analyze_failure(run, failed_jobs_override=latest_failed)
    failure_analysis = artifacts.get("failureAnalysis") if isinstance(artifacts.get("failureAnalysis"), dict) else {}
    failure_type = _agent_canonical_failure_type(failure_analysis.get("failureType")) or "UNKNOWN"
    result.update({
        "analyzed": True,
        "analysisStatus": analysis_call.get("status") if isinstance(analysis_call, dict) else "",
        "failureType": failure_type,
        "failureTypeCounts": _agent_failure_type_counts(latest_failed),
        "latestJobIds": [_failed_job_id(item) for item in latest_failed if _failed_job_id(item)],
        "reportKeyframes": ((failure_analysis.get("evidence") or {}).get("reportKeyframes") or [])[:12]
        if isinstance(failure_analysis.get("evidence"), dict) else [],
    })
    repairable_latest = []
    for item in latest_failed:
        eligibility = _agent_repair_eligibility(
            item,
            fallback_failure_type=failure_type if len(latest_failed) == 1 else None,
            fallback_can_auto_repair=(
                failure_analysis.get("canAutoRepair")
                if len(latest_failed) == 1 and "canAutoRepair" in failure_analysis
                else None
            ),
        )
        if eligibility.get("eligible") is True:
            repairable_latest.append(item)
    if not repairable_latest:
        result["reason"] = (
            f"最新失败中没有可安全自动修复的脚本任务："
            f"{result.get('failureTypeCounts') or {failure_type: len(latest_failed)}}"
        )
        artifacts["postRerunAutonomy"] = result
        return result

    repair_call = _tool_generate_repair(run, failed_jobs_override=latest_failed)
    result.update({
        "repairStatus": repair_call.get("status") if isinstance(repair_call, dict) else "",
        "repairGenerated": bool(isinstance(repair_call, dict) and repair_call.get("aiUsed")),
        "repairDraftIds": list((repair_call or {}).get("repairDraftIds") or [])[:20] if isinstance(repair_call, dict) else [],
    })
    if not result["repairGenerated"]:
        result["reason"] = (repair_call or {}).get("outputSummary") if isinstance(repair_call, dict) else "AI 未生成可用修复草稿"
    else:
        result["reason"] = "已基于最新报告关键帧和可信基线生成修复草稿，准备在原设备验证"
    artifacts["postRerunAutonomy"] = result
    return result


def _agent_failed_sources_recovered(failed_items, retry_sources, completed, failed, timeout_jobs, skipped):
    """Require a passed repair descendant for every failed source in this rerun round."""
    source_ids = {_failed_job_id(item) for item in failed_items if _failed_job_id(item)}
    if not source_ids or failed or timeout_jobs or skipped:
        return False
    completed_ids = {
        str(item.get("job_id") or item.get("jobId") or "").strip()
        for item in completed or []
        if isinstance(item, dict)
    }
    recovered_source_ids = {
        str(item.get("sourceJobId") or "").strip()
        for item in retry_sources or []
        if isinstance(item, dict)
        and str(item.get("newJobId") or "").strip() in completed_ids
    }
    return source_ids.issubset(recovered_source_ids)


def _agent_resume_deferred_after_recovery(run):
    """Resume only the executable refs paused by the smoke gate, on the selected device."""
    from task_server.services import job_service

    artifacts = run.setdefault("artifacts", {})
    gate = artifacts.get("runnerExecutionGate") if isinstance(artifacts.get("runnerExecutionGate"), dict) else {}
    if not gate.get("enabled"):
        return {"status": "SUCCESS", "resumed": False, "reason": "无 generated YAML 执行门禁"}
    if "remainingDeferred" in gate:
        deferred = [item for item in (gate.get("remainingDeferred") or []) if isinstance(item, dict)]
    else:
        deferred = [item for item in (gate.get("deferred") or []) if isinstance(item, dict)]
    if not deferred:
        gate.update({
            "smokeRecovered": True,
            "remainingDeferredCount": 0,
            "remainingDeferred": [],
            "stopFurtherExecution": False,
        })
        artifacts["runnerExecutionGate"] = gate
        artifacts["runnerSmokeGate"] = gate
        return {"status": "SUCCESS", "resumed": False, "reason": "没有延后的 executable YAML"}

    selected_runner_id = str(run.get("runnerId") or run.get("runner_id") or "").strip()
    selected_device_id = str(run.get("deviceId") or run.get("device_id") or "").strip()
    selected_device_strategy = job_service.normalize_device_strategy(
        run.get("deviceStrategy") or run.get("device_strategy") or "auto",
        device_id=selected_device_id,
        runner_id=selected_runner_id,
    )
    runner_dry_run_enabled, runner_dry_run_reason = _runner_supports_yaml_dry_run(selected_runner_id)
    expand_limit = max(1, AGENT_GENERATED_RUNNER_EXPAND_LIMIT)
    batch_limit = max(1, min(AGENT_GENERATED_RUNNER_EXPAND_BATCH_LIMIT, expand_limit))
    pending = list(deferred[:expand_limit])
    overflow = list(deferred[expand_limit:])
    aggregate_wait = {"completed": [], "failed": [], "timeout": []}
    created_job_ids = []
    dry_run_results = []
    dry_run_blocked = []
    runner_dry_run_jobs = []
    batches = []
    stop_reason = ""
    batch_index = 0

    while pending and not _agent_run_cancel_requested(run):
        batch_index += 1
        batch_refs = pending[:batch_limit]
        pending = pending[batch_limit:]
        phase = f"recovered-expanded-{batch_index}"
        created = _agent_create_runner_jobs_for_refs(
            run,
            batch_refs,
            selected_runner_id,
            selected_device_id,
            selected_device_strategy,
            runner_dry_run_enabled=runner_dry_run_enabled,
            phase=phase,
        )
        batch_job_ids = list(created.get("jobIds") or [])
        batch_blocked = list(created.get("dryRunBlocked") or [])
        created_job_ids.extend(batch_job_ids)
        dry_run_results.extend(created.get("dryRunResults") or [])
        dry_run_blocked.extend(batch_blocked)
        runner_dry_run_jobs.extend(created.get("runnerDryRunJobs") or [])
        wait_result = created.get("formalWaitResult") if isinstance(created.get("formalWaitResult"), dict) else None
        if batch_job_ids and wait_result is None:
            wait_result = job_service.wait_jobs_finished(
                batch_job_ids,
                run,
                timeout=job_service.runner_job_wait_timeout_seconds(len(batch_job_ids)),
                interval=5,
                phase=f"修复通过后扩展第{batch_index}批",
            )
        wait_result = wait_result or {"completed": [], "failed": [], "timeout": []}
        aggregate_wait = _agent_merge_runner_wait_results(aggregate_wait, wait_result)
        batch_total = sum(len(wait_result.get(key) or []) for key in ("completed", "failed", "timeout"))
        batch_failed = len(wait_result.get("failed") or []) + len(wait_result.get("timeout") or [])
        batch_row = {
            "batch": batch_index,
            "phase": phase,
            "plannedCount": len(batch_refs),
            "createdCount": len(batch_job_ids),
            "blockedCount": len(batch_blocked),
            "jobIds": batch_job_ids,
            "completedCount": len(wait_result.get("completed") or []),
            "failedCount": len(wait_result.get("failed") or []),
            "timeoutCount": len(wait_result.get("timeout") or []),
        }
        batches.append(batch_row)

        if batch_blocked:
            blocked_keys = {
                (
                    str(item.get("module") or "").strip(),
                    str(item.get("file") or "").strip(),
                    str(item.get("path") or "").strip(),
                )
                for item in batch_blocked if isinstance(item, dict)
            }
            blocked_refs = [
                ref for ref in batch_refs
                if (
                    str(ref.get("module") or "").strip(),
                    str(ref.get("file") or "").strip(),
                    str(ref.get("path") or "").strip(),
                ) in blocked_keys
            ]
            pending = blocked_refs + pending
            stop_reason = f"修复通过后扩展 dry-run 拦截 {len(batch_blocked)} 个 executable YAML"
        elif batch_total and batch_failed / batch_total > 0.5:
            stop_reason = f"修复通过后第 {batch_index} 批扩展失败率超过 50%，暂停后续扩展"
        elif not batch_job_ids:
            pending = batch_refs + pending
            stop_reason = "修复通过后扩展未创建 Runner 任务"
        if stop_reason:
            break

    if _agent_run_cancel_requested(run):
        stop_reason = stop_reason or "Agent 已取消，停止修复后的扩展执行"
    remaining_deferred = pending + overflow
    existing_job_ids = [str(item or "").strip() for item in (artifacts.get("jobIds") or []) if str(item or "").strip()]
    artifacts["jobIds"] = list(dict.fromkeys(existing_job_ids + created_job_ids))
    existing_result = artifacts.get("jobResult") if isinstance(artifacts.get("jobResult"), dict) else {}
    merged_result = _agent_merge_runner_wait_results(existing_result, aggregate_wait)
    phases = copy.deepcopy(existing_result.get("phases") or {})
    for row in batches:
        phases[row["phase"]] = {
            key: copy.deepcopy(row[key]) for key in (
                "jobIds", "completedCount", "failedCount", "timeoutCount"
            )
        }
    artifacts["jobResult"] = {
        **copy.deepcopy(existing_result),
        "completedCount": len(merged_result["completed"]),
        "failedCount": len(merged_result["failed"]),
        "timeoutCount": len(merged_result["timeout"]),
        "completed": merged_result["completed"],
        "failed": merged_result["failed"],
        "timeout": merged_result["timeout"],
        "phases": phases,
    }
    runner_dry_run = artifacts.get("runnerDryRun") if isinstance(artifacts.get("runnerDryRun"), dict) else {}
    runner_dry_run.setdefault("results", []).extend(dry_run_results)
    runner_dry_run.setdefault("blocked", []).extend(dry_run_blocked)
    runner_dry_run.setdefault("runnerJobIds", []).extend(runner_dry_run_jobs)
    runner_dry_run.update({
        "mode": "runner_yaml_dry_run" if runner_dry_run_enabled else "mock_dry_run",
        "reason": runner_dry_run_reason,
        "checked": len(runner_dry_run.get("results") or []),
        "blockedCount": len(runner_dry_run.get("blocked") or []),
        "createdCount": len(artifacts.get("jobIds") or []),
        "ok": not runner_dry_run.get("blocked"),
    })
    artifacts["runnerDryRun"] = runner_dry_run
    existing_expanded_job_ids = list(gate.get("expandedJobIds") or [])
    existing_expanded_batches = list(gate.get("expandedBatches") or [])
    gate.update({
        "smokeRecovered": True,
        "smokeRecoverySource": "successful_ai_repair_rerun",
        "smokeRecoveredCount": len((artifacts.get("recovery") or {}).get("sourceJobIds") or []),
        "smokePassThresholdMetAfterRecovery": True,
        "recoveredExpandedExecution": True,
        "recoveredExpandedBatches": batches,
        "recoveredExpandedJobIds": created_job_ids,
        "recoveredExpandedCompletedCount": len(aggregate_wait["completed"]),
        "recoveredExpandedFailedCount": len(aggregate_wait["failed"]),
        "recoveredExpandedTimeoutCount": len(aggregate_wait["timeout"]),
        "recoveredExpandedBlockedCount": len(dry_run_blocked),
        "expandedExecution": True,
        "expandedBatches": existing_expanded_batches + batches,
        "expandedBatchCount": len(existing_expanded_batches) + len(batches),
        "expandedJobIds": list(dict.fromkeys(existing_expanded_job_ids + created_job_ids)),
        "expandedCreatedCount": _safe_int_local(gate.get("expandedCreatedCount"), 0) + len(created_job_ids),
        "expandedCompletedCount": _safe_int_local(gate.get("expandedCompletedCount"), 0) + len(aggregate_wait["completed"]),
        "expandedFailedCount": _safe_int_local(gate.get("expandedFailedCount"), 0) + len(aggregate_wait["failed"]),
        "expandedTimeoutCount": _safe_int_local(gate.get("expandedTimeoutCount"), 0) + len(aggregate_wait["timeout"]),
        "expandedBlockedCount": _safe_int_local(gate.get("expandedBlockedCount"), 0) + len(dry_run_blocked),
        "remainingDeferredCount": len(remaining_deferred),
        "remainingDeferred": remaining_deferred[:30],
        "stopFurtherExecution": bool(stop_reason or remaining_deferred),
        "expandedStopReason": stop_reason,
    })
    execution_plan = dict(gate.get("executionPlan") or artifacts.get("generatedYamlExecutionPlan") or {})
    readiness = dict(execution_plan.get("readiness") or {})
    readiness.update({
        "smokeRecovered": True,
        "smokePassThresholdMetAfterRecovery": True,
        "expandedExecution": bool(created_job_ids or dry_run_blocked),
        "stopFurtherExecution": bool(stop_reason or remaining_deferred),
        "remainingDeferredCount": len(remaining_deferred),
    })
    execution_plan["readiness"] = readiness
    execution_plan["recoveredExpandedResult"] = {
        "created": len(created_job_ids),
        "passed": len(aggregate_wait["completed"]),
        "failed": len(aggregate_wait["failed"]),
        "timeout": len(aggregate_wait["timeout"]),
        "blocked": len(dry_run_blocked),
        "remainingDeferred": len(remaining_deferred),
        "stopReason": stop_reason,
    }
    gate["executionPlan"] = execution_plan
    gate["executionReadiness"] = readiness
    artifacts["runnerExecutionGate"] = gate
    artifacts["runnerSmokeGate"] = gate
    artifacts["generatedYamlExecutionPlan"] = execution_plan
    _persist_agent_run_snapshot(run)

    status = "SUCCESS"
    if aggregate_wait["failed"] or aggregate_wait["timeout"]:
        status = "PARTIAL_FAILED" if aggregate_wait["completed"] else "FAILED"
    elif dry_run_blocked or remaining_deferred:
        status = "PARTIAL_FAILED"
    return {
        "status": status,
        "resumed": True,
        "runnerId": selected_runner_id,
        "deviceId": selected_device_id,
        "deviceStrategy": selected_device_strategy,
        "createdJobIds": created_job_ids,
        "completed": aggregate_wait["completed"],
        "failed": aggregate_wait["failed"],
        "timeout": aggregate_wait["timeout"],
        "blocked": dry_run_blocked,
        "remainingDeferred": remaining_deferred,
        "batches": batches,
        "stopReason": stop_reason,
    }


def _agent_mark_recovered_execution_steps(run, execution, report_refresh=None):
    """Resolve orchestration steps while preserving their original failed attempts."""
    artifacts = run.setdefault("artifacts", {})
    recovered_job_ids = list(execution.get("recoveredJobIds") or [])
    recovery_job_ids = [
        item.get("newJobId") for item in _agent_rerun_source_links(artifacts)
        if item.get("sourceJobId") in recovered_job_ids
    ]
    recovery = artifacts.get("recovery") if isinstance(artifacts.get("recovery"), dict) else {}
    recovery.update({
        "status": "RECOVERED",
        "recovered": True,
        "recoveredJobIds": recovered_job_ids,
        "recoveryJobIds": [item for item in recovery_job_ids if item],
        "logicalPassedCount": execution.get("logicalPassedCount", 0),
        "rawPassedAttemptCount": execution.get("passedCount", 0),
        "rawFailedAttemptCount": execution.get("failedCount", 0),
        "remainingDeferredCount": execution.get("remainingDeferredCount", 0),
        "reportRefreshStatus": (report_refresh or {}).get("status") if isinstance(report_refresh, dict) else "",
        "resolvedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "rule": "原始失败尝试保留；仅关联修复任务通过且延后 executable 已完成时，逻辑执行链标记为 recovered。",
    })
    artifacts["recovery"] = recovery
    artifacts["resolvedFailedExecutionItems"] = copy.deepcopy(artifacts.get("failedExecutionItems") or [])
    failure_analysis = artifacts.get("failureAnalysis") if isinstance(artifacts.get("failureAnalysis"), dict) else {}
    if failure_analysis:
        failure_analysis["resolved"] = True
        failure_analysis["resolution"] = copy.deepcopy(recovery)
        artifacts["failureAnalysis"] = failure_analysis
    report = artifacts.get("report") if isinstance(artifacts.get("report"), dict) else {}
    if report:
        report["logicalStatus"] = "recovered"
        report["recoveredJobIds"] = recovered_job_ids
        artifacts["report"] = report
    for step in run.get("steps") or []:
        if step.get("step") not in ("RUN_SONIC", "COLLECT_REPORT"):
            continue
        old_status = str(step.get("status") or "").upper()
        if old_status not in ("FAILED", "PARTIAL_FAILED"):
            continue
        history = step.setdefault("attemptHistory", [])
        history.append({
            "status": old_status,
            "summary": step.get("summary") or "",
            "error": step.get("error") or "",
            "endedAt": step.get("endedAt") or "",
        })
        del history[:-5]
        step["initialStatus"] = step.get("initialStatus") or old_status
        step["recovered"] = True
        step["recovery"] = copy.deepcopy(recovery)
        step["status"] = "SUCCESS"
        if step.get("step") == "COLLECT_REPORT" and isinstance(report_refresh, dict):
            tool_calls = step.setdefault("toolCalls", [])
            refresh_call_id = report_refresh.get("callId")
            if not refresh_call_id or not any(item.get("callId") == refresh_call_id for item in tool_calls if isinstance(item, dict)):
                tool_calls.append(copy.deepcopy(report_refresh))
        recovery_method = (
            "AI 修复与环境重试"
            if recovery.get("rerunSource") == "mixed"
            else "AI 修复" if recovery.get("usesRepairDraft")
            else "安全重跑"
        )
        step["summary"] = (
            f"首次 Runner 尝试失败已保留；{recovery_method}在原设备验证通过，"
            "延后 executable 已执行到终态，逻辑执行链恢复。"
        )
        step.pop("error", None)
    run.pop("error", None)


def _tool_rerun(run, failed_items_override=None, repair_depth=0):
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
        previous_progress = artifacts.get("rerunProgress") if isinstance(artifacts.get("rerunProgress"), dict) else {}
        if repair_depth > 0 and previous_progress:
            history = artifacts.get("rerunProgressHistory") if isinstance(artifacts.get("rerunProgressHistory"), list) else []
            history.append(json.loads(json.dumps(previous_progress, ensure_ascii=False)))
            artifacts["rerunProgressHistory"] = history[-3:]
        if failed_items_override is None:
            failed_items = _agent_persist_failed_execution_items(run)
        else:
            failed_items = _normalize_agent_failed_items(failed_items_override)
            artifacts["failedExecutionItems"] = failed_items
        failed_ids = [_failed_job_id(item) for item in failed_items if _failed_job_id(item)]
        source_by_id = {item.get("jobId"): item for item in failed_items if item.get("jobId")}
        retried = []
        retry_sources = []
        skipped = []
        jobs = job_service.load_jobs()
        repair_plan = _agent_prepare_repair_rerun_targets(run, failed_items, jobs)
        repair_targets = [item for item in (repair_plan.get("targets") or []) if isinstance(item, dict)]
        repair_source_ids = [
            str(item.get("sourceJobId") or "").strip()
            for item in repair_targets
            if str(item.get("sourceJobId") or "").strip()
        ]
        candidate_original_ids = failed_ids or [
            str(jid) for jid in (artifacts.get("jobIds") or []) if str(jid or "").strip()
        ]
        original_retry_ids = []
        for jid in candidate_original_ids:
            if jid in repair_source_ids:
                continue
            source_item = source_by_id.get(jid)
            if source_item is None:
                original_retry_ids.append(jid)
                continue
            failure_type = _agent_repair_eligibility(source_item).get("failureType")
            if failure_type == "ENV_ISSUE" and _agent_original_rerun_eligible(source_item):
                original_retry_ids.append(jid)
            elif (
                failure_type == "SCRIPT_ISSUE"
                and not repair_plan.get("hasRepairDrafts")
                and _agent_original_rerun_eligible(source_item)
            ):
                original_retry_ids.append(jid)
        job_ids = list(dict.fromkeys(repair_source_ids + original_retry_ids))
        had_repair_drafts = bool(repair_plan.get("hasRepairDrafts"))
        uses_repair_draft = bool(repair_targets)
        rerun_source = (
            "mixed" if uses_repair_draft and original_retry_ids
            else "repair_draft" if uses_repair_draft
            else "original_yaml" if original_retry_ids
            else "diagnosis_only"
        )
        serial_same_device = _agent_rerun_requires_serial_device(run)
        serial_wait_results = []
        repair_summary = artifacts.get("repairSummary") if isinstance(artifacts.get("repairSummary"), dict) else {}
        repair_summary_by_draft = {
            str(item.get("draftId") or ""): item
            for item in (repair_summary.get("items") or [])
            if isinstance(item, dict) and str(item.get("draftId") or "").strip()
        }
        progress_items = []
        progress_item_by_key = {}

        def add_progress_item(item):
            key = str(item.get("draftId") or item.get("sourceJobId") or item.get("newJobId") or "").strip()
            if key and key in progress_item_by_key:
                progress_item_by_key[key].update(item)
                return progress_item_by_key[key]
            progress_items.append(item)
            if key:
                progress_item_by_key[key] = item
            return item

        if uses_repair_draft:
            for target in repair_targets:
                repair_meta = repair_summary_by_draft.get(str(target.get("draftId") or ""), {})
                source_job = target.get("sourceJob") if isinstance(target.get("sourceJob"), dict) else {}
                add_progress_item({
                    "draftId": target.get("draftId") or "",
                    "sourceJobId": target.get("sourceJobId") or "",
                    "sourceModule": target.get("sourceModule") or "",
                    "sourceFile": target.get("sourceFile") or "",
                    "targetTaskName": target.get("sourceTaskName") or (target.get("taskNames") or [""])[0],
                    "repairModule": target.get("module") or "",
                    "repairFile": target.get("file") or "",
                    "failureReason": target.get("failureReason") or "",
                    "repairChanges": repair_meta.get("changes") or [],
                    "repairSource": repair_meta.get("repairSource") or "ai_gateway",
                    "selectedBaselines": repair_meta.get("selectedBaselines") or [],
                    "runnerId": source_job.get("target_runner_id") or source_job.get("runner_id") or run.get("runnerId") or "",
                    "deviceId": source_job.get("device_id") or run.get("deviceId") or "",
                    "status": "pending",
                })
        if original_retry_ids:
            for jid in original_retry_ids:
                source = source_by_id.get(jid) or {}
                source_job = next((job for job in jobs if job.get("job_id") == jid or job.get("jobId") == jid), {})
                add_progress_item({
                    "sourceJobId": jid,
                    "sourceModule": source_job.get("module") or source.get("module") or "",
                    "sourceFile": source_job.get("file") or source.get("file") or "",
                    "targetTaskName": _failed_job_task_name(source) or source_job.get("target_task_name") or "",
                    "failureReason": source.get("failureReason") or source.get("error") or "",
                    "repairChanges": [],
                    "repairSource": "original_yaml",
                    "runnerId": source_job.get("target_runner_id") or source_job.get("runner_id") or run.get("runnerId") or "",
                    "deviceId": source_job.get("device_id") or run.get("deviceId") or "",
                    "status": "pending",
                })

        rerun_progress = {
            "scope": "failed_tasks",
            "source": rerun_source,
            "usesRepairDraft": uses_repair_draft,
            "repairDraftCount": repair_plan.get("draftCount", 0) if had_repair_drafts else 0,
            "appliedRepairDraftCount": len(repair_targets) if uses_repair_draft else 0,
            "originalRetryCount": len(original_retry_ids),
            "notRerunOriginalYaml": not bool(original_retry_ids),
            "sourceFailedCount": len(failed_items),
            "targetCount": len(job_ids),
            "serialSameDevice": serial_same_device,
            "runnerId": run.get("runnerId") or "",
            "deviceId": run.get("deviceId") or "",
            "items": progress_items,
            "skipped": [],
            "status": "RUNNING",
        }
        artifacts["rerunProgress"] = rerun_progress

        def persist_rerun_progress(status=None):
            item_statuses = [str(item.get("status") or "pending").lower() for item in progress_items]
            success_count = sum(1 for value in item_statuses if value == "success")
            failed_count = sum(1 for value in item_statuses if value in ("failed", "error", "cancelled"))
            timeout_count = sum(1 for value in item_statuses if value == "timeout")
            skipped_count = sum(1 for value in item_statuses if value == "skipped")
            running_count = sum(1 for value in item_statuses if value in ("running", "assigned"))
            pending_count = sum(1 for value in item_statuses if value in ("pending", "created", "queued", "waiting", "creating"))
            terminal_count = success_count + failed_count + timeout_count + skipped_count
            total_count = max(len(progress_items), len(failed_items))
            rerun_progress.update({
                "total": total_count,
                "completedCount": terminal_count,
                "successCount": success_count,
                "failedCount": failed_count,
                "timeoutCount": timeout_count,
                "runningCount": running_count,
                "pendingCount": max(pending_count, total_count - terminal_count - running_count),
                "createdCount": sum(1 for item in progress_items if item.get("newJobId")),
                "skippedCount": skipped_count,
                "createdJobIds": [item.get("newJobId") for item in progress_items if item.get("newJobId")],
                "sources": retry_sources,
                "skipped": skipped,
                "status": status or rerun_progress.get("status") or "RUNNING",
                "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            })
            artifacts["rerunProgress"] = rerun_progress
            _persist_agent_run_snapshot(run)

        def apply_wait_result(wait_result):
            groups = (
                ("success", wait_result.get("completed") or []),
                ("failed", wait_result.get("failed") or []),
                ("timeout", wait_result.get("timeout") or []),
            )
            for status_value, entries in groups:
                for entry in entries:
                    job_id = str(entry.get("job_id") or entry.get("jobId") or "").strip()
                    item = next((row for row in progress_items if str(row.get("newJobId") or "") == job_id), None)
                    if item is None:
                        continue
                    item.update({
                        "status": status_value,
                        "runnerId": entry.get("runner_id") or entry.get("runnerId") or item.get("runnerId") or "",
                        "deviceId": entry.get("device_id") or entry.get("deviceId") or item.get("deviceId") or "",
                        "reportUrl": entry.get("report_url") or entry.get("reportUrl") or "",
                        "resultReason": entry.get("error") or entry.get("progress_message") or "",
                        "finishedAt": entry.get("updated_at") or entry.get("updatedAt") or time.strftime("%Y-%m-%dT%H:%M:%S"),
                    })

        persist_rerun_progress("RUNNING")
        if uses_repair_draft:
            for target in repair_targets:
                if _agent_run_cancel_requested(run):
                    break
                j = target.get("sourceJob") if isinstance(target.get("sourceJob"), dict) else {}
                source = target.get("sourceItem") if isinstance(target.get("sourceItem"), dict) else {}
                source_job_id = target.get("sourceJobId") or source.get("jobId") or j.get("job_id") or ""
                progress_item = progress_item_by_key.get(str(target.get("draftId") or "")) or progress_item_by_key.get(str(source_job_id or ""))
                if progress_item is not None:
                    progress_item["status"] = "creating"
                    persist_rerun_progress("RUNNING")
                new_job = job_service.create_pending_job(
                    target.get("module", ""),
                    target.get("file", ""),
                    auto_optimize=False,
                    max_attempt=max(safe_int(j.get("max_attempt"), 2), safe_int(j.get("attempt"), 1) + 1),
                    attempt=safe_int(j.get("attempt"), 1) + 1,
                    parent_job_id=source_job_id,
                    device_id=j.get("device_id") or run.get("deviceId") or run.get("device_id") or "",
                    runner_id=j.get("target_runner_id") or j.get("runner_id", ""),
                    device_strategy=j.get("device_strategy") or j.get("deviceStrategy") or "",
                    run_mode=j.get("run_mode", "test"),
                    target_task_name="",
                    parent_run_id=run.get("runId", ""),
                )
                if new_job and new_job.get("job_id"):
                    retried.append(new_job["job_id"])
                    if progress_item is not None:
                        progress_item.update({
                            "newJobId": new_job["job_id"],
                            "status": "running",
                            "createdAt": new_job.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%S"),
                        })
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
                    persist_rerun_progress("RUNNING")
                    if serial_same_device:
                        serial_result = job_service.wait_jobs_finished(
                            [new_job["job_id"]],
                            run,
                            timeout=job_service.runner_job_wait_timeout_seconds(1),
                            interval=5,
                            phase="安全重跑-同设备串行",
                        )
                        serial_wait_results.append(serial_result)
                        apply_wait_result(serial_result)
                        persist_rerun_progress("RUNNING")
                elif progress_item is not None:
                    progress_item.update({"status": "failed", "resultReason": "创建 Runner 重跑任务失败"})
                    persist_rerun_progress("RUNNING")
            original_retry_id_set = set(original_retry_ids)
            skipped.extend(
                item for item in (repair_plan.get("skipped") or [])
                if str(item.get("jobId") or item.get("sourceJobId") or "").strip() not in original_retry_id_set
                and not any(
                    _agent_repair_draft_matches_failed_item(item, source_by_id.get(jid) or {})
                    for jid in original_retry_ids
                )
            )
        if original_retry_ids:
            for jid in original_retry_ids:
                if _agent_run_cancel_requested(run):
                    break
                j = next((job for job in jobs if job.get("job_id") == jid or job.get("jobId") == jid), None)
                source = source_by_id.get(jid) or {}
                progress_item = progress_item_by_key.get(str(jid))
                if j and str(j.get("status", "")).lower() in ("failed", "error", "timeout"):
                    target_task_name = j.get("target_task_name") or j.get("taskName") or j.get("current_task_name") or ""
                    if progress_item is not None:
                        progress_item["status"] = "creating"
                        persist_rerun_progress("RUNNING")
                    new_job = job_service.create_pending_job(
                        j.get("module", ""),
                        j.get("file", ""),
                        auto_optimize=False,
                        max_attempt=max(safe_int(j.get("max_attempt"), 2), safe_int(j.get("attempt"), 1) + 1),
                        attempt=safe_int(j.get("attempt"), 1) + 1,
                        parent_job_id=jid,
                        device_id=j.get("device_id", ""),
                        runner_id=j.get("target_runner_id") or j.get("runner_id", ""),
                        device_strategy=j.get("device_strategy") or j.get("deviceStrategy") or "",
                        run_mode=j.get("run_mode", "test"),
                        target_task_name=target_task_name,
                        parent_run_id=run.get("runId", ""),
                    )
                    if new_job and new_job.get("job_id"):
                        retried.append(new_job["job_id"])
                        if progress_item is not None:
                            progress_item.update({
                                "newJobId": new_job["job_id"],
                                "status": "running",
                                "createdAt": new_job.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%S"),
                            })
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
                        persist_rerun_progress("RUNNING")
                        if serial_same_device:
                            serial_result = job_service.wait_jobs_finished(
                                [new_job["job_id"]],
                                run,
                                timeout=job_service.runner_job_wait_timeout_seconds(1),
                                interval=5,
                                phase="安全重跑-同设备串行",
                            )
                            serial_wait_results.append(serial_result)
                            apply_wait_result(serial_result)
                            persist_rerun_progress("RUNNING")
                    elif progress_item is not None:
                        progress_item.update({"status": "failed", "resultReason": "创建 Runner 重跑任务失败"})
                        persist_rerun_progress("RUNNING")
                elif j:
                    skipped.append({
                        "jobId": jid,
                        "status": j.get("status", ""),
                        "taskName": source.get("taskName") or j.get("target_task_name") or j.get("current_task_name") or "",
                        "reason": "不是失败/超时终态，不创建重跑任务",
                    })
                    if progress_item is not None:
                        progress_item.update({"status": "skipped", "resultReason": "不是失败/超时终态，不创建重跑任务"})
                else:
                    skipped.append({
                        "jobId": jid,
                        "status": "not_found",
                        "taskName": source.get("taskName") or "",
                        "reason": "原始 job 已不存在",
                    })
                    if progress_item is not None:
                        progress_item.update({"status": "skipped", "resultReason": "原始 job 已不存在"})
            persist_rerun_progress("RUNNING")

        covered_source_ids = {
            str(item.get("sourceJobId") or "").strip()
            for item in retry_sources
            if str(item.get("sourceJobId") or "").strip()
        }
        for item in failed_items:
            item_id = _failed_job_id(item)
            if item_id in covered_source_ids:
                continue
            progress_item = progress_item_by_key.get(str(item_id or ""))
            if progress_item is not None and str(progress_item.get("status") or "").lower() in (
                "failed", "error", "timeout", "cancelled"
            ):
                continue
            existing_skipped = next((
                skipped_item for skipped_item in skipped
                if isinstance(skipped_item, dict)
                and _agent_repair_draft_matches_failed_item(
                    {
                        "jobId": skipped_item.get("jobId") or skipped_item.get("sourceJobId"),
                        "file": skipped_item.get("file"),
                        "taskName": skipped_item.get("taskName"),
                    },
                    item,
                )
            ), None)
            eligibility = _agent_repair_eligibility(item)
            failure_type = eligibility.get("failureType") or "UNKNOWN"
            if existing_skipped:
                reason = existing_skipped.get("reason") or "该失败任务只保留诊断证据"
            elif failure_type == "PRODUCT_BUG":
                reason = "产品失败只保留缺陷证据，不自动重跑"
            elif failure_type == "SCRIPT_ISSUE":
                reason = "AI 未生成通过门禁的修复 YAML，禁止原样重跑"
            elif failure_type == "ENV_ISSUE":
                reason = "环境失败缺少可验证的临时性证据，禁止盲目重跑"
            else:
                reason = f"{failure_type} 证据不足，未自动重跑"
            if not existing_skipped:
                skipped.append({
                    "jobId": item_id,
                    "taskName": _failed_job_task_name(item) or item.get("file") or "",
                    "status": "diagnosis_only",
                    "failureType": failure_type,
                    "reason": reason,
                })
            if progress_item is None:
                progress_item = add_progress_item({
                    "sourceJobId": item_id,
                    "sourceModule": item.get("module") or "",
                    "sourceFile": item.get("file") or "",
                    "targetTaskName": _failed_job_task_name(item),
                    "failureReason": item.get("failureReason") or item.get("error") or "",
                    "repairChanges": [],
                    "repairSource": "diagnosis_only",
                    "runnerId": run.get("runnerId") or "",
                    "deviceId": run.get("deviceId") or "",
                })
            progress_item.update({"status": "skipped", "resultReason": reason})
        persist_rerun_progress("RUNNING")

        artifacts["retriedJobs"] = retried
        artifacts["rerunSources"] = retry_sources
        artifacts["rerunSkippedJobs"] = skipped
        persist_rerun_progress("CREATED" if retried else "SKIPPED")
        call["createdJobIds"] = retried
        call["sourceFailedCount"] = len(failed_items)
        call["targetCount"] = len(job_ids)
        call["skippedCount"] = len(skipped)
        call["usesRepairDraft"] = uses_repair_draft
        call["rerunSource"] = rerun_source
        rerun_strategy_text = {
            "mixed": "按任务混合恢复",
            "repair_draft": "使用修复草稿",
            "original_yaml": "使用原始 YAML",
            "diagnosis_only": "仅保留诊断",
        }.get(rerun_source, "按失败证据")
        call["outputSummary"] = (
            f"基于 {len(failed_items)} 个失败任务，"
            f"{rerun_strategy_text}，创建 {len(retried)} 个重跑任务"
        )
        if not retried:
            persist_rerun_progress("SKIPPED")
            creation_failed = any(
                str(item.get("status") or "").lower() in ("failed", "error")
                for item in progress_items
            )
            call["status"] = "FAILED" if creation_failed else "SKIPPED"
            call["outputSummary"] = (
                "已有修复草稿但没有可执行 YAML，已阻止重跑原脚本"
                if had_repair_drafts else "没有符合自动重跑条件的失败任务"
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
            if serial_same_device and serial_wait_results:
                wait_result = _agent_merge_runner_wait_results(*serial_wait_results)
            else:
                wait_result = job_service.wait_jobs_finished(
                    retried,
                    run,
                    timeout=wait_timeout,
                    interval=5,
                    phase="安全重跑",
                )
                apply_wait_result(wait_result)
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
                "serialSameDevice": serial_same_device,
            }
            artifacts.setdefault("rerunAttempts", []).append({
                "repairDepth": repair_depth,
                "source": rerun_source,
                "createdJobIds": list(retried),
                "completedCount": len(completed),
                "failedCount": len(failed),
                "timeoutCount": len(timeout_jobs),
                "serialSameDevice": serial_same_device,
            })
            del artifacts["rerunAttempts"][:-3]
            final_progress_status = "FAILED" if failed or timeout_jobs else ("PARTIAL_FAILED" if skipped else "SUCCESS")
            persist_rerun_progress(final_progress_status)
            summary = f"重跑执行完成：失败任务 {len(failed_items)} 个，创建 {len(retried)} 个，成功 {len(completed)} 个，失败 {len(failed)} 个，超时 {len(timeout_jobs)} 个"
            if rerun_source == "mixed":
                summary += (
                    f"；AI 修复 {len(repair_targets)} 个，环境原脚本重试 {len(original_retry_ids)} 个，"
                    f"诊断跳过 {len(skipped)} 个"
                )
            elif uses_repair_draft:
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
            elif skipped:
                call["status"] = "PARTIAL_FAILED"
                call["error"] = "只执行了有证据支持的恢复动作，仍有失败任务保留为诊断项"
                attach_diagnosis(call, make_diagnosis(
                    "自动恢复覆盖不完整",
                    "系统只执行通过门禁的 AI 修复或有具体证据的环境重试，未对其余失败盲目重跑旧 YAML。",
                    ["查看诊断跳过项", "检查是否已有足够报告证据", "产品失败转缺陷处理"],
                    skippedJobs=skipped[:10],
                ))
            else:
                call["status"] = "SUCCESS"
            report_refresh = _tool_collect_report(run)
            artifacts["rerunReportRefresh"] = {
                "status": report_refresh.get("status") if isinstance(report_refresh, dict) else "",
                "summary": report_refresh.get("outputSummary") if isinstance(report_refresh, dict) else "",
                "refreshedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            }
            call["reportRefresh"] = copy.deepcopy(artifacts["rerunReportRefresh"])
            sources_recovered = _agent_failed_sources_recovered(
                failed_items,
                retry_sources,
                completed,
                failed,
                timeout_jobs,
                skipped,
            )
            if sources_recovered:
                recovery = artifacts.get("recovery") if isinstance(artifacts.get("recovery"), dict) else {}
                recovery.update({
                    "status": "REPAIR_VALIDATED",
                    "usesRepairDraft": uses_repair_draft,
                    "rerunSource": rerun_source,
                    "sourceJobIds": [_failed_job_id(item) for item in failed_items if _failed_job_id(item)],
                    "recoveryJobIds": list(retried),
                    "runnerId": run.get("runnerId") or "",
                    "deviceId": run.get("deviceId") or "",
                    "deviceStrategy": run.get("deviceStrategy") or run.get("device_strategy") or "",
                    "validatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                })
                artifacts["recovery"] = recovery
                expansion_result = _agent_resume_deferred_after_recovery(run)
                artifacts["recoveryExpansion"] = copy.deepcopy(expansion_result)
                call["recoveryExpansion"] = copy.deepcopy(expansion_result)
                report_refresh = _tool_collect_report(run)
                artifacts["recoveryReportRefresh"] = {
                    "status": report_refresh.get("status") if isinstance(report_refresh, dict) else "",
                    "summary": report_refresh.get("outputSummary") if isinstance(report_refresh, dict) else "",
                }
                if expansion_result.get("status") == "SUCCESS":
                    execution = _agent_runner_execution_summary(run)
                    if (
                        execution.get("outcome") == "passed"
                        and execution.get("recoveredCount", 0) > 0
                        and execution.get("remainingDeferredCount", 0) == 0
                    ):
                        _agent_mark_recovered_execution_steps(run, execution, report_refresh)
                        call["status"] = "SUCCESS"
                        call.pop("error", None)
                        resumed_count = len(expansion_result.get("createdJobIds") or [])
                        call["outputSummary"] = (
                            f"{summary}；失败源已由关联重跑任务验证恢复，"
                            f"并在原 Runner/设备完成 {resumed_count} 个延后 executable 任务"
                        )
                        call["recovery"] = copy.deepcopy(artifacts.get("recovery") or {})
                    else:
                        call["status"] = "PARTIAL_FAILED"
                        call["error"] = "修复任务已通过，但逻辑执行链仍有未完成或未解析结果"
                else:
                    failed = list(expansion_result.get("failed") or [])
                    timeout_jobs = list(expansion_result.get("timeout") or [])
                    call["status"] = expansion_result.get("status") or "PARTIAL_FAILED"
                    call["error"] = expansion_result.get("stopReason") or "修复通过后的扩展任务未全部通过"
                    call["outputSummary"] = (
                        f"{summary}；修复任务已通过，但后续扩展成功 "
                        f"{len(expansion_result.get('completed') or [])}、失败 {len(failed)}、"
                        f"超时 {len(timeout_jobs)}、拦截 {len(expansion_result.get('blocked') or [])}"
                    )
            if (failed or timeout_jobs) and repair_depth < 1:
                latest_failed = _normalize_agent_failed_items(list(failed) + list(timeout_jobs))
                followup = _agent_post_rerun_autonomy(run, latest_failed, repair_depth=repair_depth)
                call["postRerunAutonomy"] = followup
                if followup.get("repairGenerated"):
                    followup["followupExecuted"] = True
                    followup_call = _tool_rerun(
                        run,
                        failed_items_override=latest_failed,
                        repair_depth=repair_depth + 1,
                    )
                    followup["followupStatus"] = followup_call.get("status") if isinstance(followup_call, dict) else ""
                    followup["followupSummary"] = followup_call.get("outputSummary") if isinstance(followup_call, dict) else ""
                    artifacts["postRerunAutonomy"] = followup
                    call["rerunResult"] = artifacts.get("rerunResult") or call.get("rerunResult")
                    call["rerunProgress"] = artifacts.get("rerunProgress") or call.get("rerunProgress")
                    if followup.get("followupStatus") == "SUCCESS":
                        call["status"] = "SUCCESS"
                        call.pop("error", None)
                        call["outputSummary"] = f"{summary}；AI 根据最新失败证据修复后在原设备验证成功"
                    else:
                        call["outputSummary"] = f"{summary}；AI 已进行一次受限修复重跑，结果仍未通过"
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def _agent_runner_execution_summary(run):
    """Summarize real Runner outcomes without folding them into Agent orchestration."""
    artifacts = (run or {}).get("artifacts") or {}
    report = artifacts.get("report") if isinstance(artifacts.get("report"), dict) else {}
    job_result = artifacts.get("jobResult") if isinstance(artifacts.get("jobResult"), dict) else {}
    progress_by_phase = (
        artifacts.get("jobProgressByPhase")
        if isinstance(artifacts.get("jobProgressByPhase"), dict)
        else {}
    )
    records = {}
    phase_fallback = []

    def unique_job_ids(values):
        result = []
        for value in values or []:
            job_id = str(value or "").strip()
            if job_id and job_id not in result:
                result.append(job_id)
        return result

    def normalized_status(item, fallback=""):
        item = item if isinstance(item, dict) else {}
        status = str(item.get("status") or fallback or "").strip().lower()
        if item.get("agent_wait_timeout") or status in ("timeout", "timed_out"):
            return "timeout"
        if status in ("success", "passed", "pass", "completed", "complete"):
            return "passed"
        if status in ("failed", "fail", "error", "not_found"):
            return "failed"
        if status in ("cancelled", "canceled"):
            return "cancelled"
        if status in ("running", "pending", "queued", "created", "waiting", "assigned"):
            return "running"
        return "unknown"

    def add_items(items, fallback_status, source, priority, phase=""):
        for index, item in enumerate(items or []):
            if not isinstance(item, dict):
                continue
            job_id = str(
                item.get("jobId")
                or item.get("job_id")
                or item.get("newJobId")
                or ""
            ).strip()
            identity = job_id or "::".join(filter(None, [
                str(item.get("module") or "").strip(),
                str(item.get("file") or "").strip(),
                str(item.get("taskName") or item.get("task_name") or "").strip(),
            ]))
            key = identity or f"{source}:{phase}:{index}"
            current = records.get(key)
            if current and current.get("priority", 0) > priority:
                continue
            failure_review = item.get("failureReview") or item.get("failure_review") or {}
            failure_type = _agent_canonical_failure_type(
                item.get("failureType") or item.get("failure_type")
            )
            if (not failure_type or failure_type == "UNKNOWN") and isinstance(failure_review, dict):
                failure_type = _agent_failure_type_from_review(failure_review) or failure_type
            if not failure_type or failure_type == "UNKNOWN":
                failure_text = "\n".join(str(item.get(key) or "") for key in (
                    "error", "stderrTail", "stderr_tail", "stdoutTail", "stdout_tail", "summaryText", "summary_text",
                ))
                inferred_failure_type = _agent_canonical_failure_type(
                    _agent_job_failure_type(failure_text)
                )
                if inferred_failure_type:
                    failure_type = inferred_failure_type
            current_failure_type = (current or {}).get("failureType") or ""
            if failure_type in ("", "UNKNOWN") and current_failure_type not in ("", "UNKNOWN"):
                failure_type = current_failure_type
            records[key] = {
                "jobId": job_id,
                "status": normalized_status(item, fallback_status),
                "phase": phase or str(item.get("phase") or "").strip() or (current or {}).get("phase", ""),
                "failureType": failure_type or (current or {}).get("failureType") or "UNKNOWN",
                "source": source,
                "priority": priority,
            }

    original_job_ids = unique_job_ids(artifacts.get("jobIds") or [])
    rerun_job_ids = []
    for attempt in artifacts.get("rerunAttempts") or []:
        if not isinstance(attempt, dict):
            continue
        rerun_job_ids.extend(attempt.get("createdJobIds") or [])
    rerun_job_ids.extend(artifacts.get("retriedJobs") or [])
    rerun_job_ids.extend(item.get("newJobId") for item in _agent_rerun_source_links(artifacts))
    rerun_progress_rows = []
    for progress in list(artifacts.get("rerunProgressHistory") or []) + [artifacts.get("rerunProgress")]:
        if not isinstance(progress, dict):
            continue
        for item in progress.get("items") or []:
            if not isinstance(item, dict):
                continue
            new_job_id = str(item.get("newJobId") or item.get("new_job_id") or "").strip()
            if not new_job_id:
                continue
            rerun_job_ids.append(new_job_id)
            rerun_progress_rows.append({**item, "jobId": new_job_id})
    rerun_job_ids = unique_job_ids(rerun_job_ids)

    # Formal job ids are the attempt ledger. Register them before richer report
    # sources so an unavailable/pruned job remains visible as an unknown attempt.
    add_items(
        [{"jobId": job_id, "status": "unknown"} for job_id in original_job_ids],
        "unknown",
        "formal_job_ledger",
        5,
        "Runner",
    )
    add_items(
        [{"jobId": job_id, "status": "unknown"} for job_id in rerun_job_ids],
        "unknown",
        "rerun_job_ledger",
        5,
        "安全重跑",
    )
    add_items(rerun_progress_rows, "", "rerun_progress", 25, "安全重跑")

    for phase_name, progress in progress_by_phase.items():
        if not isinstance(progress, dict):
            continue
        phase_text = str(progress.get("phase") or phase_name or "runner").strip()
        if "dry-run" in phase_text.lower() or "dry run" in phase_text.lower():
            continue
        jobs = [item for item in (progress.get("jobs") or []) if isinstance(item, dict)]
        if jobs:
            add_items(jobs, "", "phase_progress", 10, phase_text)
            continue
        timeout_count = _safe_int_local(progress.get("timeoutCount"), 0)
        if not timeout_count and progress.get("agentWaitTimeout"):
            timeout_count = _safe_int_local(progress.get("timeout"), 0)
        phase_fallback.append({
            "phase": phase_text,
            "passed": _safe_int_local(progress.get("completed"), 0),
            "failed": _safe_int_local(progress.get("failed"), 0),
            "timeout": timeout_count,
            "running": _safe_int_local(progress.get("running"), 0),
        })

    add_items(job_result.get("completed"), "success", "job_result", 20)
    add_items(job_result.get("failed"), "failed", "job_result", 20)
    add_items(job_result.get("timeout"), "timeout", "job_result", 20)
    add_items(report.get("jobStatuses"), "", "report", 30)
    add_items(report.get("successJobs"), "success", "report", 31)
    add_items(report.get("failedJobs"), "failed", "report", 31)
    add_items(report.get("timeoutJobs"), "timeout", "report", 32)
    add_items(report.get("runningJobs"), "running", "report", 31)

    # Report collection can precede the bounded repair reruns. Refresh every
    # known formal attempt from the persisted Runner job store so those retries
    # cannot disappear from the final totals.
    attempt_job_ids = unique_job_ids(original_job_ids + rerun_job_ids)
    if attempt_job_ids:
        try:
            from task_server.services import job_service

            jobs_by_id = {
                str(item.get("job_id") or item.get("jobId") or "").strip(): item
                for item in (job_service.load_jobs() or [])
                if isinstance(item, dict)
            }
            persisted_attempts = []
            for job_id in attempt_job_ids:
                job = jobs_by_id.get(job_id)
                if not job:
                    continue
                persisted_attempts.append({
                    **job,
                    "jobId": job_id,
                    "failureReview": job.get("failureReview") or job.get("failure_review") or {},
                    "failureType": job.get("failureType") or job.get("failure_type") or "",
                })
            add_items(persisted_attempts, "", "runner_job_store", 40)
        except Exception:
            pass

    counts = {key: 0 for key in ("passed", "failed", "timeout", "running", "cancelled", "unknown")}
    failure_counts = {key: 0 for key in ("product", "broken", "unknown")}
    phase_counts = {}
    for item in records.values():
        status = item.get("status") or "unknown"
        counts[status] = counts.get(status, 0) + 1
        phase = item.get("phase") or "Runner"
        phase_row = phase_counts.setdefault(
            phase,
            {"phase": phase, "passed": 0, "failed": 0, "timeout": 0, "running": 0, "cancelled": 0, "unknown": 0},
        )
        phase_row[status] = phase_row.get(status, 0) + 1
        if status == "failed":
            failure_type = item.get("failureType") or "UNKNOWN"
            if failure_type == "PRODUCT_BUG":
                failure_counts["product"] += 1
            elif failure_type in ("SCRIPT_ISSUE", "ENV_ISSUE"):
                failure_counts["broken"] += 1
            else:
                failure_counts["unknown"] += 1

    for row in ([] if records else phase_fallback):
        for status in ("passed", "failed", "timeout", "running"):
            value = _safe_int_local(row.get(status), 0)
            counts[status] += value
        failure_counts["unknown"] += _safe_int_local(row.get("failed"), 0)
        phase_counts[row["phase"]] = {
            "phase": row["phase"],
            "passed": row["passed"],
            "failed": row["failed"],
            "timeout": row["timeout"],
            "running": row["running"],
            "cancelled": 0,
            "unknown": 0,
        }

    # Older runs may only have smoke/expanded gate totals. They are still real
    # Runner outcomes and must remain visible in the final report.
    if not records and not phase_fallback:
        gate = artifacts.get("runnerExecutionGate") if isinstance(artifacts.get("runnerExecutionGate"), dict) else {}
        counts["passed"] = (
            _safe_int_local(gate.get("smokePassedCount"), 0)
            + _safe_int_local(gate.get("expandedCompletedCount"), 0)
        )
        counts["failed"] = (
            _safe_int_local(gate.get("smokeFailedCount"), 0)
            + _safe_int_local(gate.get("expandedFailedCount"), 0)
        )
        counts["timeout"] = _safe_int_local(gate.get("expandedTimeoutCount"), 0)
        failure_counts["unknown"] = counts["failed"]

    attempted_count = sum(counts.values())
    terminal_count = counts["passed"] + counts["failed"] + counts["timeout"] + counts["cancelled"]
    adverse_count = counts["failed"] + counts["timeout"] + counts["cancelled"]
    status_by_job_id = {
        str(item.get("jobId") or "").strip(): str(item.get("status") or "unknown")
        for item in records.values()
        if str(item.get("jobId") or "").strip()
    }
    children_by_source = {}
    for link in _agent_rerun_source_links(artifacts):
        children_by_source.setdefault(link["sourceJobId"], []).append(link["newJobId"])

    def has_passed_descendant(job_id, visited=None):
        visited = set(visited or set())
        if not job_id or job_id in visited:
            return False
        visited.add(job_id)
        for child_id in children_by_source.get(job_id, []):
            if status_by_job_id.get(child_id) == "passed":
                return True
            if has_passed_descendant(child_id, visited):
                return True
        return False

    logical_counts = {key: 0 for key in ("passed", "failed", "timeout", "running", "cancelled", "unknown")}
    recovered_job_ids = []
    unresolved_failed_job_ids = []
    if original_job_ids:
        for job_id in original_job_ids:
            status = status_by_job_id.get(job_id, "unknown")
            if status in ("failed", "timeout", "cancelled", "unknown") and has_passed_descendant(job_id):
                logical_counts["passed"] += 1
                recovered_job_ids.append(job_id)
                continue
            logical_counts[status if status in logical_counts else "unknown"] += 1
            if status in ("failed", "timeout", "cancelled"):
                unresolved_failed_job_ids.append(job_id)
    else:
        logical_counts = dict(counts)
        unresolved_failed_job_ids = [
            job_id for job_id, status in status_by_job_id.items()
            if status in ("failed", "timeout", "cancelled")
        ]
    unresolved_failed_job_ids = list(dict.fromkeys(
        unresolved_failed_job_ids + [
            job_id for job_id, status in status_by_job_id.items()
            if status in ("failed", "timeout", "cancelled")
            and not has_passed_descendant(job_id)
        ]
    ))

    gate = artifacts.get("runnerExecutionGate") if isinstance(artifacts.get("runnerExecutionGate"), dict) else {}
    if "remainingDeferredCount" in gate:
        remaining_deferred_count = _safe_int_local(gate.get("remainingDeferredCount"), 0)
    elif gate.get("stopFurtherExecution"):
        remaining_deferred_count = len(gate.get("remainingDeferred") or gate.get("deferred") or [])
    else:
        remaining_deferred_count = 0
    logical_adverse_count = (
        logical_counts["failed"] + logical_counts["timeout"] + logical_counts["cancelled"]
    )
    if logical_counts["running"]:
        outcome, label = "running", "执行中"
    elif logical_counts["passed"] and (logical_adverse_count or remaining_deferred_count):
        outcome, label = "partial", "部分通过"
    elif logical_adverse_count:
        outcome, label = "failed", "未通过"
    elif logical_counts["passed"]:
        outcome, label = "passed", "修复后通过" if recovered_job_ids else "通过"
    else:
        outcome, label = "not_executed", "未执行"
    return {
        "outcome": outcome,
        "label": label,
        "hasExecution": attempted_count > 0,
        "attemptedCount": attempted_count,
        "originalAttemptCount": len(original_job_ids),
        "rerunAttemptCount": len(rerun_job_ids),
        "untrackedAttemptCount": max(0, attempted_count - len(set(original_job_ids + rerun_job_ids))),
        "attemptJobIds": [item.get("jobId") for item in records.values() if item.get("jobId")][:100],
        "terminalCount": terminal_count,
        "passedCount": counts["passed"],
        "failedCount": counts["failed"],
        "logicalAttemptCount": len(original_job_ids) if original_job_ids else attempted_count,
        "logicalPassedCount": logical_counts["passed"],
        "logicalFailedCount": logical_counts["failed"],
        "logicalTimeoutCount": logical_counts["timeout"],
        "logicalRunningCount": logical_counts["running"],
        "recoveredCount": len(recovered_job_ids),
        "recoveredJobIds": recovered_job_ids[:100],
        "unresolvedFailedJobIds": unresolved_failed_job_ids[:100],
        "remainingDeferredCount": remaining_deferred_count,
        "productFailedCount": failure_counts["product"],
        "brokenCount": failure_counts["broken"],
        "unknownFailedCount": failure_counts["unknown"],
        "timeoutCount": counts["timeout"],
        "runningCount": counts["running"],
        "cancelledCount": counts["cancelled"],
        "unknownCount": counts["unknown"],
        "phases": list(phase_counts.values()),
        "rule": (
            "Runner 真实结果与 Agent 编排终态分别汇总；原始正式任务和每次修复重跑均按 job ID 计入尝试，"
            "编排失败不会抹掉已通过的冒烟或扩展任务；失败源只有在关联的同设备修复 job 真正通过后才记为 recovered。"
        ),
    }


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
        execution = _agent_runner_execution_summary(run)
        unresolved_failed_job_ids = set(execution.get("unresolvedFailedJobIds") or [])
        active_failed_execution_items = [
            item for item in failed_execution_items
            if not item.get("jobId")
            or item.get("jobId") in unresolved_failed_job_ids
        ]
        if execution.get("outcome") == "passed" and not unresolved_failed_job_ids:
            active_failed_execution_items = []
        observed_run_status = str(run.get("status") or "").strip().upper()
        if observed_run_status == "CANCELLED":
            run_status = "CANCELLED"
            orchestration_state, orchestration_label = "cancelled", "编排已取消"
        elif failed or observed_run_status == "FAILED":
            run_status = "FAILED"
            orchestration_state, orchestration_label = "blocked", "编排阻断"
        else:
            # GENERATE_SUMMARY runs before the worker writes its terminal state.
            # A summary with no failed steps will therefore finish as DONE even
            # when the observed in-memory status is still RUNNING.
            run_status = "DONE"
            orchestration_state, orchestration_label = "completed", "编排完成"
        orchestration = {
            "state": orchestration_state,
            "label": orchestration_label,
            "runStatus": run_status,
            "observedRunStatus": observed_run_status,
            "statusProjectedAtSummary": observed_run_status != run_status,
            "completedStepCount": completed,
            "failedStepCount": failed,
            "skippedStepCount": skipped,
            "failedSteps": [
                {
                    "step": step.get("step") or step.get("title") or "",
                    "status": step.get("status") or "",
                    "reason": step.get("error") or step.get("summary") or "",
                }
                for step in steps
                if str(step.get("status") or "").upper() in ("FAILED", "PARTIAL_FAILED")
            ][:8],
        }
        conclusion = execution.get("label") or "未执行"
        if execution.get("outcome") == "passed" and orchestration_state != "completed":
            conclusion = "部分通过"
        elif execution.get("outcome") == "not_executed" and report.get("status") == "missing":
            conclusion = "报告缺失"
        next_actions = []
        if active_failed_execution_items or execution.get("logicalFailedCount") or execution.get("logicalTimeoutCount"):
            next_actions.extend(["打开失败任务报告或 Runner 日志", "确认是脚本问题后生成修复草稿", "修复后重跑失败用例"])
        elif execution.get("runningCount") or running_jobs:
            next_actions.extend(["等待 Runner 回传执行结果", "刷新 Agent 运行状态"])
        elif report.get("status") == "missing":
            next_actions.extend(["检查 Runner 报告上传", "查看执行中心 job 详情", "必要时重跑任务"])
        elif orchestration_state == "blocked":
            next_actions.extend(["查看编排阻断步骤和覆盖门禁", "保留已通过 Runner 结果", "修复生成资产后从阻断点重新验证"])
        else:
            next_actions.extend(["保留本次结果作为回归记录", "如需复盘可查看执行报告链接"])
        summary = {
            "title": f"{run.get('target', 'Agent 任务')} - 执行总结",
            "target": run.get("target", ""),
            "conclusion": conclusion,
            "execution": execution,
            "orchestration": orchestration,
            "totalSteps": len(steps),
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
            "matchedCount": matched_count,
            "reportCount": report_count,
            "passedJobCount": execution.get("passedCount", 0),
            "failedJobCount": execution.get("failedCount", 0),
            "productFailedJobCount": execution.get("productFailedCount", 0),
            "brokenJobCount": execution.get("brokenCount", 0),
            "recoveredJobCount": execution.get("recoveredCount", 0),
            "logicalPassedJobCount": execution.get("logicalPassedCount", 0),
            "logicalFailedJobCount": execution.get("logicalFailedCount", 0),
            "unknownFailedJobCount": execution.get("unknownFailedCount", 0),
            "timeoutJobCount": execution.get("timeoutCount", 0),
            "runningJobCount": execution.get("runningCount", 0),
            "runnerAttemptCount": execution.get("attemptedCount", 0),
            "runnerOriginalAttemptCount": execution.get("originalAttemptCount", 0),
            "runnerRerunAttemptCount": execution.get("rerunAttemptCount", 0),
            "failedTasks": [
                {
                    "jobId": item.get("jobId"),
                    "taskName": item.get("taskName"),
                    "file": item.get("file"),
                    "reason": item.get("failureReason") or item.get("error"),
                }
                for item in active_failed_execution_items[:30]
            ],
            "failureType": failure.get("failureType") or "NONE",
            "nextActions": next_actions[:5],
            "reportStatus": report.get("status") or "",
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "mode": run.get("mode", ""),
            "riskLevel": run.get("riskLevel", ""),
            "message": (
                f"Runner：{execution.get('label')}，通过 {execution.get('passedCount', 0)}，"
                f"失败尝试 {execution.get('failedCount', 0)}，修复后通过 {execution.get('recoveredCount', 0)}，"
                f"超时 {execution.get('timeoutCount', 0)}；"
                f"Agent：{orchestration_label}，{completed}/{len(steps)} 步骤成功"
            ),
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
    "PREPARE_SOURCE", "PLAN", "IMPACT_ANALYSIS", "CASE_RETRIEVAL", "MATCH_CASES",
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
    branch_names = [
        str(item.get("branch") or item.get("name") or "").strip()
        for item in (business_constraint.get("businessFlows") or [])[:6]
        if isinstance(item, dict) and str(item.get("branch") or item.get("name") or "").strip()
    ]
    if business_constraint.get("strict") and branch_names:
        step["liveTrace"].append({
            "time": _trace_time_text(),
            "message": f"AI 业务计划分支：{'、'.join(branch_names)}",
            "status": "RUNNING",
        })
    elif business_constraint.get("candidateOnly") and branch_names:
        step["liveTrace"].append({
            "time": _trace_time_text(),
            "message": f"原始需求候选：{'、'.join(branch_names)}（仅供覆盖审计，待 AI 判断关系与路径）",
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
                "reason": (
                    "状态机步骤已绑定 AI 业务计划"
                    if business_constraint.get("strict")
                    else "当前仅绑定原始需求候选，不能视为已确认业务路径"
                ),
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
      - GENERATE_REPAIR: any per-task repairable SCRIPT_ISSUE
      - GENERATE_BUG_DRAFT: any per-task PRODUCT_BUG
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
            # Mixed Runner outcomes keep per-task actions instead of collapsing to one aggregate type.
            if step_name == "GENERATE_REPAIR":
                if not _agent_has_repairable_failure(run):
                    counts = _agent_failure_type_counts(_agent_failed_execution_items(run))
                    step["status"] = "SKIPPED"
                    step["summary"] = f"没有可安全自动修复的 SCRIPT_ISSUE（{counts or {'NONE': 0}}），跳过修复"
                    step["startedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    step["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
                    continue
            if step_name == "GENERATE_BUG_DRAFT":
                counts = _agent_failure_type_counts(_agent_failed_execution_items(run))
                if not counts.get("PRODUCT_BUG"):
                    step["status"] = "SKIPPED"
                    step["summary"] = f"没有 PRODUCT_BUG 任务（{counts or {'NONE': 0}}），跳过缺陷草稿"
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
                POST_FAILURE_ANALYSIS_STEPS = ("RUN_SONIC",)
                if step_name in NON_CRITICAL_STEPS or step_name in POST_FAILURE_ANALYSIS_STEPS:
                    pass  # Continue into report collection, failure analysis and repair planning.
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
                if _agent_should_confirm_unknown_failure(run, ft):
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
