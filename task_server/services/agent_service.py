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
)
from task_server.schemas import AGENT_STATE_STEPS, HIGH_RISK_KEYWORDS, MIDSCENE_FLOW_ACTIONS
from task_server.storage import (
    read_json_cached,
    read_json_file,
    read_text_file,
    safe_join,
    unique_millis_id,
    write_text_file,
    write_json_file,
)
from task_server.services.yaml_service import validate_midscene_yaml_executability
from task_server.prompts import get_prompt_center

# ---------------------------------------------------------------------------
# Agent Tool Registry & Constants (migrated from midscene-upload.py)
# ---------------------------------------------------------------------------

AGENT_TOOL_CALLS_FILE = os.path.join(LEARNING_DIR, "agent-tool-calls.json")
AGENT_TOOL_CALL_LOCK = threading.Lock()
AGENT_DRAFT_DIR = os.path.join(LEARNING_DIR, "agent-drafts")
AGENT_LEARNING_FILE = os.path.join(LEARNING_DIR, "agent-learning.json")
AGENT_LEARNING_LOCK = threading.Lock()

AGENT_RUN_STEPS = AGENT_STATE_STEPS

AGENT_RISK_KEYWORDS = HIGH_RISK_KEYWORDS

AUTO_AGENT_RISK_KEYWORDS = AGENT_RISK_KEYWORDS

AGENT_DEFAULT_BUSINESS_FLOW = ["进入稳定起点", "执行核心业务动作", "校验业务结果"]

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
    runs = load_agent_runs()
    return next((r for r in runs if r.get("runId") == run_id), None)


def create_agent_run(payload):
    """创建新 Agent 运行。

    payload 支持的字段:
        - target / goal: 测试目标描述
        - mode: AUTO_SAFE | FULL_AUTO | SEMI_AUTO
        - appName: 应用名称
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
    高风险目标会进入 WAIT_CONFIRM 状态等待人工确认。
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
    worker = threading.Thread(target=_execute_agent_steps, args=(run_id,), daemon=True)
    worker.start()
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
            "8. 高风险动作进入 WAIT_CONFIRM",
            "9. 生成总结报告",
        ],
    }


def cancel_agent_run(run_id):
    """取消 Agent 运行。"""
    with AGENT_RUN_LOCK:
        runs = load_agent_runs()
        run = next((r for r in runs if r.get("runId") == run_id), None)
        if not run:
            return None
        if run.get("status") in ("DONE", "FAILED", "CANCELLED"):
            return run
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        run["status"] = "CANCELLED"
        run["currentStep"] = "FAILED"
        run["updatedAt"] = now
        run["error"] = "用户取消"
        save_agent_runs(runs)
        return run


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
    }]
    artifacts["yamlValidation"] = {"ok": True, "results": [{**artifacts["yamlRefs"][0], **check}], "issues": []}
    return target_path, ""


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
        result = {"module": module, "file": file_name or os.path.basename(path), "path": path, **check}
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
        })
    if not refs:
        return [], "YAML 文件校验未通过：" + "；".join(issues or ["没有可确认的 YAML 文件"])
    artifacts["generatedYaml"] = ""
    artifacts["generatedYamlPath"] = refs[0]["path"]
    artifacts["generatedYamlPaths"] = [item["path"] for item in refs]
    artifacts["draftConfirmed"] = True
    artifacts["yamlRefs"] = refs
    artifacts["yamlValidation"] = {"ok": not issues, "results": results, "issues": issues}
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
            else:
                return {"error": "确认项不存在", "run": run}
        decision_key = str(decision or "").strip().lower()
        approve_keys = (
            "approve", "approved", "confirm", "confirmed", "yes", "true", "1",
            "continue", "confirm_case_reuse", "confirm_yaml_draft",
            "confirm_run", "confirm_bug", "confirm_bug_draft",
            "apply_baseline", "apply_repair_and_rerun",
        )
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
                        "time": now,
                        "message": summary,
                        "status": "SKIPPED",
                    }]

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
                _target_path, err = _confirm_agent_yaml_content(run, artifacts, content, draft_path=draft_path)
                if err:
                    return {"error": err, "run": run}
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
            run["pendingConfirmations"] = [
                c for c in run.get("pendingConfirmations", []) if c.get("id") != step_id
            ]
            run["status"] = "RUNNING"
            if run.get("currentStep") == "WAIT_CONFIRM":
                run["currentStep"] = "VALIDATE_YAML"
            run["updatedAt"] = now
        elif rejected:
            run["status"] = "CANCELLED"
            run["currentStep"] = "FAILED"
            run["updatedAt"] = now
            run["error"] = f"用户拒绝确认：{confirmation.get('type', '')}"

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
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
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
        "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "message": str(message or ""),
    }
    if extra:
        row.update({k: v for k, v in extra.items() if v not in (None, "")})
    step.setdefault("liveTrace", []).append(row)
    del step["liveTrace"][:-30]
    _agent_log(run, f"{step.get('step', '')}: {message}")
    _persist_agent_run_snapshot(run)


def _evaluate_risk(run):
    """评估风险等级。"""
    target = run.get("target", "")
    try:
        for ref in normalize_yaml_refs(run):
            target += "\n" + _yaml_ref_content(ref)[:5000]
    except Exception:
        pass
    for kw in AUTO_AGENT_RISK_KEYWORDS:
        if kw in target:
            return "HIGH", kw
    return "LOW", None


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
        new_job = job_service.create_job({
            "module": module,
            "file": file,
            "target_task_name": old_job.get("target_task_name", ""),
            "attempt": attempt,
            "parent_job_id": job_id,
        })
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
                        "7. 高风险动作进入 WAIT_CONFIRM",
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
                        "7. 高风险动作进入 WAIT_CONFIRM",
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
                    "7. 高风险动作进入 WAIT_CONFIRM",
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
                "高风险动作确认",
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


def _looks_like_yaml_text(value):
    text = str(value or "")
    return bool("\n" in text and re.search(r"^\s*(android|ios|tasks)\s*:", text, flags=re.M))


def normalize_yaml_refs(run):
    artifacts = run.setdefault("artifacts", {})
    refs = []
    for item in artifacts.get("yamlRefs") or []:
        if isinstance(item, dict):
            ref = {
                "type": item.get("type") or "file",
                "module": item.get("module") or "",
                "file": item.get("file") or "",
                "path": item.get("path") or "",
                "content": item.get("content") or "",
                "confirmed": bool(item.get("confirmed")),
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
        refs.append({"type": "file", "module": module, "file": file, "path": path, "content": "", "confirmed": True})
        known_paths.add(path)

    draft_path = artifacts.get("draftPath") or ""
    if draft_path and draft_path not in known_paths:
        module, file = _task_dir_for_path(draft_path)
        refs.append({"type": "draft", "module": module, "file": file, "path": draft_path, "content": "", "confirmed": False})
        known_paths.add(draft_path)

    generated_path = artifacts.get("generatedYamlPath") or ""
    if generated_path and generated_path not in known_paths and not _looks_like_yaml_text(generated_path):
        module, file = _task_dir_for_path(generated_path)
        refs.append({"type": "file", "module": module, "file": file, "path": generated_path, "content": "", "confirmed": True})
        known_paths.add(generated_path)

    generated = artifacts.get("generatedYaml")
    if isinstance(generated, str) and generated.strip() and _looks_like_yaml_text(generated):
        if not any(item.get("type") == "text" and item.get("content") == generated for item in refs):
            refs.append({"type": "text", "module": "", "file": "", "path": "", "content": generated, "confirmed": False})

    artifacts["yamlRefs"] = refs
    return refs


def _yaml_ref_content(ref):
    if ref.get("content"):
        return str(ref.get("content") or "")
    path = ref.get("path") or ""
    return read_text_file(path, "") if path else ""


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
    return (
        str(run.get("appPackage") or run.get("app_package") or refs.get("appPackage") or refs.get("app_package") or "").strip()
        or os.getenv("APP_PACKAGE", "com.kfb.model").strip()
        or "com.kfb.model"
    )


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
        query_text = "\n".join([
            str(run.get("target") or ""),
            str(context.get("requirementText") or ""),
            " ".join(str((item or {}).get("name") or "") for item in context.get("uploadedFiles") or [] if isinstance(item, dict)),
        ]).strip()
        request_data = {
            "figma_url": figma_url,
            "figma_mode": normalized.get("figmaMode") or refs.get("figmaMode") or "smart",
            "figma_limit": normalized.get("figmaLimit") or refs.get("figmaLimit") or 80,
            "figma_reference_limit": normalized.get("figmaReferenceLimit") or refs.get("figmaReferenceLimit") or 36,
            "figma_max_reference_limit": normalized.get("figmaMaxReferenceLimit") or refs.get("figmaMaxReferenceLimit") or 72,
        }
        text_assets, image_assets, used_pages, ignored_pages, _saved_designs = load_figma_generation_context(
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
        context["uiDesigns"] = used_pages[:20]
        context["figmaUsedPages"] = used_pages[:20]
        context["figmaIgnoredPages"] = ignored_pages[:20]
        context["figmaImageAssets"] = [
            {
                "name": item.get("name") or f"figma-{idx + 1}.png",
                "mime": item.get("mime") or "",
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
    try:
        prompt_ctx = get_prompt_center().enrich({
            **run,
            "sourceContext": source_context,
            "requirementText": (
                source_context.get("requirementText")
                or normalized_input.get("requirementText")
                or normalized_input.get("text")
                or run.get("target", "")
            ),
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
            part = part.strip(" -:：")
            if part and part not in expanded_flow:
                expanded_flow.append(part)
    business_flow = expanded_flow
    if not business_flow:
        business_flow = list(AGENT_DEFAULT_BUSINESS_FLOW)
    business_flow_text = business_ctx.get("business_flow_text") or "\n".join(
        f"{idx + 1}. {item}" for idx, item in enumerate(business_flow)
    )
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
            context["knowledgePages"] = _load_knowledge_pages_for_agent(run, f"{run.get('target','')} {context.get('requirementText','')}")
        else:
            context["requirementText"] = run.get("target", "")
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
            f"截图 {context.get('figmaImageCount') or 0} 张；"
            f"上传资料 {material.get('fileCount') or 0} 个，其中截图 {material.get('imageCount') or 0} 张、"
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
        if material.get("fileCount") and not context.get("knowledgePages"):
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


def _agent_generate_yaml_from_mindmap_pipeline(run, source_context, source_text):
    """Reuse the mature requirement/Figma -> cases/mindmap pipeline for Agent drafts."""
    from task_server.services.yaml_service import (
        generate_mindmap_from_request,
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
    request_data = {
        "case_set_id": case_set_id,
        "title": title,
        "module": module,
        "files": files,
        "figma_url": source_context.get("figmaUrl") or "",
        "figmaUrl": source_context.get("figmaUrl") or "",
        "app_package": _agent_app_package(run),
        "use_knowledge_context": False,
        "source": "agent",
    }
    result = generate_mindmap_from_request(request_data, job_id=None)
    cases_payload = result.get("cases") if isinstance(result, dict) else {}
    if not isinstance(cases_payload, dict):
        cases_payload = {}
    yaml_files = result.get("yamlFiles") or result.get("files") or []
    yaml_file_items = []
    for file_name in yaml_files:
        if isinstance(file_name, dict):
            name = str(file_name.get("file") or "").strip()
        else:
            name = str(file_name or "").strip()
        if not name:
            continue
        yaml_file_items.append({
            "module": module,
            "file": name,
            "path": safe_join(TASK_DIR, module, name),
        })
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
    artifacts["generationPipeline"] = {
        "source": "mindmap_pipeline",
        "caseSetId": result.get("case_set_id"),
        "caseCount": result.get("caseCount"),
        "manualCaseCount": result.get("manualCaseCount"),
        "scenarioCount": result.get("scenarioCount"),
        "yamlFiles": [item.get("file") for item in yaml_file_items],
        "yamlFileCount": len(yaml_file_items),
        "summaryFiles": result.get("summaryFiles"),
        "yamlCheck": result.get("yamlCheck") or {},
        "yamlExecutability": yaml_executability,
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
    return yaml_file_items, result


def _save_agent_yaml_draft(run, artifacts, yaml_text, draft_reason="generated"):
    os.makedirs(AGENT_DRAFT_DIR, exist_ok=True)
    draft_path = os.path.join(AGENT_DRAFT_DIR, f"{run.get('runId')}.yaml")
    write_text_file(draft_path, yaml_text)
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
    artifacts["requiresConfirm"] = True
    run["status"] = "WAIT_CONFIRM"
    run["currentStep"] = "WAIT_CONFIRM"
    if not any(item.get("type") == "generated_yaml_draft" for item in run.get("pendingConfirmations") or []):
        run.setdefault("pendingConfirmations", []).append({
            "id": f"confirm-{int(time.time())}",
            "type": "generated_yaml_draft",
            "title": "确认 YAML 草稿",
            "action": "confirm_yaml_draft",
            "message": "Agent 已生成 YAML 草稿。请确认、编辑或保存为正式 YAML 后，才能同步 Sonic 和执行。",
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
            try:
                yaml_file_items, pipeline_result = _agent_generate_yaml_from_mindmap_pipeline(run, source_context, source_text)
                refs, err = _confirm_agent_yaml_files(run, artifacts, yaml_file_items)
                if refs and not err:
                    check = artifacts.get("yamlValidation") or {"ok": True, "issues": [], "results": []}
                    if err:
                        raise ValueError(err)
                    artifacts["yamlValidation"] = {**check, "autoConfirmed": True}
                    call["status"] = "SUCCESS"
                    call["outputSummary"] = (
                        "已调用需求解析/脑图生成/Figma解析主链按用例拆分生成 YAML，"
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
                artifacts.setdefault("generationPipeline", {})["error"] = str(e)[:500]
                attach_diagnosis(call, make_diagnosis(
                    "需求解析/脑图生成主链失败",
                    "已准备回退到 Agent 多任务兜底草稿。",
                    ["检查 AI Skills / Figma Token", "查看 generationPipeline.error", "确认草稿后再同步 Sonic"],
                    error=str(e)[:300],
                ))
            fallback_yaml = _agent_fallback_yaml_draft(run, source_context, source_text)
            fallback_check = validate_agent_yaml_content(fallback_yaml)
            if fallback_check.get("ok"):
                _save_agent_yaml_draft(run, artifacts, fallback_yaml, draft_reason="fallback_after_mindmap_pipeline")
                artifacts["yamlValidation"] = {
                    "ok": False,
                    "issues": ["需求解析/脑图生成主链未产出可执行 YAML"],
                    "fallbackOk": True,
                    "results": [{"type": "fallback", **fallback_check}],
                }
                call["status"] = "WAIT_CONFIRM"
                call["outputSummary"] = f"需求解析/脑图主链未产出可执行 YAML，已生成多任务兜底草稿（{fallback_check.get('taskCount')} 条）"
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
                                ["检查兜底草稿步骤", "结合 Figma 调整入口和断言", "确认后再同步 Sonic"],
                                failedYaml=check.get("issues") or [],
                            ))
                            return _finish_agent_tool_call(call, run)
                        artifacts["yamlValidation"] = {"ok": False, "issues": check.get("issues") or [], "results": [{"type": "text", **check}]}
                        call["status"] = "FAILED"
                        call["error"] = "AI 生成 YAML 未通过强校验：" + "；".join(check.get("issues") or [])
                        attach_diagnosis(call, make_diagnosis(
                            "AI 生成 YAML 为空 tasks 或结构不可执行",
                            "兜底草稿也未通过强校验，不能同步 Sonic。",
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
                            ["检查兜底草稿步骤", "必要时人工补充关键路径", "确认后再同步 Sonic"],
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

        ok_count = 0
        results = []
        issues = []
        for ref in refs:
            label = ref.get("path") or ref.get("file") or ref.get("type")
            content = _yaml_ref_content(ref)
            check = validate_agent_yaml_content(content)
            row = {**ref, "ok": bool(check.get("ok")), "issues": check.get("issues") or [], "taskCount": check.get("taskCount", 0)}
            results.append(row)
            if check.get("ok"):
                ok_count += 1
            else:
                issues.append(f"{label}: {'; '.join(check.get('issues') or [])}")
        artifacts["yamlValidation"] = {"ok": not issues, "results": results, "issues": issues}
        if issues:
            call["status"] = "FAILED"
            call["error"] = issues[0][:300]
            attach_diagnosis(call, make_diagnosis(
                "YAML 强校验未通过",
                "YAML 无法安全同步 Sonic 或执行测试。",
                ["重新生成 YAML", "人工编辑 YAML 草稿", "确认 android/ios.tasks 为非空", "保存为正式 YAML 后再执行"],
                failedYaml=results[0].get("path") or results[0].get("type") if results else "",
            ))
        else:
            call["status"] = "SUCCESS"
        call["outputSummary"] = f"强校验 {len(refs)} 个 YAML，{ok_count} 个通过" + (f"；问题：{'; '.join(issues[:3])}" if issues else "")
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
        risk_level, hit_kw = _evaluate_risk(run)
        run["riskLevel"] = risk_level
        if risk_level == "HIGH":
            run["riskHits"] = [hit_kw] if hit_kw else run.get("riskHits", [])
            call["status"] = "SUCCESS"
            call["outputSummary"] = f"命中高风险关键词：{hit_kw}"
            call["riskLevel"] = "high"
        else:
            run["riskHits"] = []
            call["status"] = "SUCCESS"
            call["outputSummary"] = "风险检查通过，无高风险关键词"
            call["riskLevel"] = "low"
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
            artifacts["executionPrecheck"] = {"checks": checks, "blockers": blockers, "warnings": warnings, "diagnosis": None}
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

        validation = artifacts.get("yamlValidation") or {}
        if not validation or not validation.get("ok"):
            validation_issues = []
            for ref in refs:
                check = validate_agent_yaml_content(_yaml_ref_content(ref))
                if not check.get("ok"):
                    validation_issues.extend(check.get("issues") or [])
            ok = not validation_issues and bool(refs)
            artifacts["yamlValidation"] = {"ok": ok, "issues": validation_issues, "results": validation.get("results") or []}
            add("yaml_strong_validation", ok, "；".join(validation_issues[:3]) if validation_issues else "通过")
        else:
            add("yaml_strong_validation", True, "已通过强校验")

        try:
            from task_server.services import sonic_service
            sonic_service.sonic_request("GET", "/users/list", timeout=5)
            add("sonic_reachable", True, "Sonic API 可访问")
        except Exception as exc:
            add("sonic_reachable", False, f"Sonic API 不可访问：{str(exc)[:180]}", "warning")

        if file_refs:
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

        public_base = os.getenv("MIDSCENE_PUBLIC_BASE_URL") or os.getenv("TASK_PUBLIC_BASE_URL") or ""
        add("public_base_url", bool(public_base), public_base or "未配置 MIDSCENE_PUBLIC_BASE_URL/TASK_PUBLIC_BASE_URL", "warning")

        token_ok = bool(os.getenv("MIDSCENE_RUNNER_TOKEN", "").strip())
        callback_ok = bool(os.getenv("SONIC_CALLBACK_TOKEN", "").strip())
        add("bridge_token", token_ok, "MIDSCENE_RUNNER_TOKEN 已配置" if token_ok else "MIDSCENE_RUNNER_TOKEN 未配置")
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
            if not should_require_sonic:
                detail += "；Runner 调试模式不阻断"
            add("bridge_groovy_endpoint", False, detail, sonic_severity)

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

        risk_level, hit_kw = _evaluate_risk(run)
        high_risk = risk_level == "HIGH"
        if high_risk and not run.get("riskConfirmed"):
            add("high_risk_confirm", False, f"命中高风险动作：{hit_kw}", "blocker")
            run["status"] = "WAIT_CONFIRM"
            run["currentStep"] = "WAIT_CONFIRM"
            if not any(c.get("type") == "high_risk_action" for c in run.get("pendingConfirmations", [])):
                run.setdefault("pendingConfirmations", []).append({
                    "id": f"confirm-{int(time.time())}",
                    "type": "high_risk_action",
                    "title": "确认高风险动作",
                    "action": "confirm_high_risk_action",
                    "message": f"命中高风险关键词：{hit_kw}，请确认是否继续执行。",
                    "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "decision": None,
                })
        else:
            add("high_risk_confirm", True, "无高风险动作" if not high_risk else "已人工确认")

        artifacts["executionPrecheck"] = {"checks": checks, "blockers": blockers, "warnings": warnings, "diagnosis": None}
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
            next_actions = ["处理体检失败项", "确认 YAML 草稿/高风险动作", "确认 Runner 在线后重试"]
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
            job_ids = []
            selected_runner_id = str(run.get("runnerId") or run.get("runner_id") or "").strip()
            selected_device_id = str(run.get("deviceId") or run.get("device_id") or "").strip()
            selected_device_strategy = job_service.normalize_device_strategy(
                run.get("deviceStrategy") or run.get("device_strategy") or "auto",
                device_id=selected_device_id,
                runner_id=selected_runner_id,
            )
            for ref in file_refs:
                try:
                    yf = str(ref.get("path") or "")
                    full_path = yf
                    if not os.path.exists(full_path):
                        continue
                    mod = ref.get("module") or _task_dir_for_path(full_path)[0]
                    fn = ref.get("file") or os.path.basename(full_path)
                    job = job_service.create_job({
                        "module": mod,
                        "file": fn,
                        "target_task_name": fn.replace(".yaml", "").replace(".yml", ""),
                        "runner_id": selected_runner_id,
                        "device_id": selected_device_id,
                        "device_strategy": selected_device_strategy,
                    })
                    if job and job.get("job_id"):
                        job_ids.append(job["job_id"])
                except Exception:
                    pass

            summary_parts = [f"Runner 调试模式：创建 {len(job_ids)} 个本地任务"]
            run_artifacts["jobIds"] = job_ids
            if not job_ids:
                call["status"] = "FAILED"
                call["error"] = "Runner 任务创建失败"
                attach_diagnosis(call, make_diagnosis(
                    "Runner 任务创建失败",
                    "已确认 YAML 未能进入本地执行队列。",
                    ["检查 YAML 文件是否存在", "检查任务目录权限", "确认 Runner 在线后重试"],
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
            wait_result = job_service.wait_jobs_finished(job_ids, run, timeout=wait_timeout, interval=5)
            run_artifacts["jobResult"] = {
                "completedCount": len(wait_result["completed"]),
                "failedCount": len(wait_result["failed"]),
                "timeoutCount": len(wait_result["timeout"]),
                "completed": wait_result["completed"],
                "failed": wait_result["failed"],
                "timeout": wait_result["timeout"],
                "waitTimeoutSeconds": wait_timeout,
            }

            if wait_result["timeout"]:
                call["status"] = "PARTIAL_FAILED"
                summary_parts.append(f"{len(wait_result['timeout'])} 个超时（等待上限 {wait_timeout}s）")
            elif wait_result["failed"]:
                if wait_result["completed"]:
                    call["status"] = "PARTIAL_FAILED"
                else:
                    call["status"] = "FAILED"
                summary_parts.append(f"{len(wait_result['failed'])} 个失败")

            if wait_result["completed"]:
                summary_parts.append(f"{len(wait_result['completed'])} 个成功")

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
                        "status": "success",
                    })
                    report_entry = {
                        "jobId": jid,
                        "module": job.get("module", ""),
                        "file": job.get("file", ""),
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
        if job_result:
            for fj in (job_result.get("failed") or []):
                if not any(f.get("jobId") == fj.get("job_id") for f in failed_jobs):
                    failed_jobs.append({
                        "jobId": fj.get("job_id", ""),
                        "status": fj.get("status", "failed"),
                        "module": fj.get("module", ""),
                        "file": fj.get("file", ""),
                        "taskName": fj.get("task_name") or fj.get("target_task_name") or "",
                        "reportUrl": fj.get("report_url") or fj.get("reportUrl", ""),
                        "stdoutTail": fj.get("stdout_tail") or "",
                        "stderrTail": fj.get("stderr_tail") or "",
                        "error": fj.get("error", ""),
                    })
                    if fj.get("error"):
                        errors.append(fj.get("error"))
            for tj in (job_result.get("timeout") or []):
                tj_id = tj.get("job_id") or tj.get("jobId")
                if not any(f.get("jobId") == tj_id for f in failed_jobs):
                    timeout_entry = {
                        "jobId": tj_id or "",
                        "status": "timeout",
                        "module": tj.get("module", ""),
                        "file": tj.get("file", ""),
                        "taskName": tj.get("task_name") or tj.get("target_task_name") or "",
                        "reportUrl": tj.get("report_url") or tj.get("reportUrl", ""),
                        "stdoutTail": tj.get("stdout_tail") or "",
                        "stderrTail": tj.get("stderr_tail") or "",
                        "error": tj.get("error") or "Runner 执行等待超时，报告尚未回传",
                    }
                    failed_jobs.append(timeout_entry)
                    timeout_jobs.append(timeout_entry)
                    errors.append(timeout_entry["error"])
        for sf in ((artifacts.get("sonicSync") or {}).get("failed") or []):
            errors.append(sf.get("error") or "")

        # 终态优先：旧快照里可能同时存在 running + timeout/failed。
        # 这里按 jobId 归一，避免最终产物出现“失败、超时、仍在运行”的矛盾状态。
        terminal_by_id = {}
        for item in success_jobs:
            if item.get("jobId"):
                terminal_by_id[item.get("jobId")] = item
        for item in failed_jobs:
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
        report_data = artifacts.get("report") or {}
        failed_jobs = report_data.get("failedJobs") or []
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
            for fj in failed_jobs[:5]:
                failure_context += f"- {fj.get('module', '')}/{fj.get('file', '')} ({fj.get('status', '')})：{fj.get('error', '')}\n"
                stderr = fj.get("stderr_tail", "")
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
                    "failedJobs": [{"jobId": fj.get("jobId", ""), "file": fj.get("file", ""), "error": fj.get("error", "")} for fj in failed_jobs[:5]],
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
        run["artifacts"] = artifacts

        call["status"] = "SUCCESS"
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
    """只对 SCRIPT_ISSUE 类型调用 AI Gateway 生成修复。"""
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
        draft_id = unique_millis_id("repair")
        draft = {
            "draftId": draft_id,
            "type": ft,
            "suggestion": fa.get("suggestion", "建议修复定位器或等待条件"),
            "draftYaml": "# 修复草稿\n# 请根据实际失败原因调整",
        }
        if _ai_gateway_available():
            try:
                resp = _ai_gateway_post("/ai/optimize-yaml", {
                    "yaml": fa.get("file", ""),
                    "target": run.get("target", ""),
                    "issues": fa.get("summary", ""),
                })
                if isinstance(resp, dict) and resp.get("optimizedYaml"):
                    draft["draftYaml"] = resp["optimizedYaml"][:5000]
                    draft["suggestion"] = resp.get("changes", draft["suggestion"])
                call["status"] = "SUCCESS"
                call["outputSummary"] = "修复草稿生成完成"
            except Exception as e:
                call["status"] = "SKIPPED"
                call["outputSummary"] = f"AI Gateway 修复生成失败：{str(e)[:200]}"
        else:
            call["status"] = "SKIPPED"
            call["outputSummary"] = "AI Gateway 不可用，使用本地修复草稿"
        run.setdefault("artifacts", {})["repairDraft"] = draft
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


def _tool_rerun(run):
    """对失败任务重新创建 job。"""
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "retry_failed_job",
        "category": "TASK",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {},
    }
    try:
        from task_server.services import job_service
        job_ids = (run.get("artifacts") or {}).get("jobIds", [])
        retried = []
        for jid in job_ids:
            jobs = job_service.load_jobs()
            j = next((job for job in jobs if job.get("job_id") == jid or job.get("jobId") == jid), None)
            if j and str(j.get("status", "")).lower() in ("failed", "error", "timeout"):
                new_job = job_service.create_job({
                    "module": j.get("module", ""),
                    "file": j.get("file", ""),
                    "target_task_name": j.get("target_task_name", j.get("taskName", "")),
                    "parent_job_id": jid,
                })
                if new_job and new_job.get("job_id"):
                    retried.append(new_job["job_id"])
        run.setdefault("artifacts", {})["retriedJobs"] = retried
        call["status"] = "SUCCESS"
        call["outputSummary"] = f"重跑 {len(retried)} 个失败任务"
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
        conclusion = "通过"
        if failed or failed_jobs or timeout_jobs:
            conclusion = "未通过"
        elif running_jobs:
            conclusion = "执行中"
        elif report.get("status") == "missing":
            conclusion = "报告缺失"
        next_actions = []
        if failed_jobs or timeout_jobs:
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
            "failedJobCount": len(failed_jobs),
            "timeoutJobCount": len(timeout_jobs),
            "runningJobCount": len(running_jobs),
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
            validation = artifacts.get("yamlValidation") or {}
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
        with AGENT_LEARNING_LOCK:
            data = read_json_file(AGENT_LEARNING_FILE, default={"records": []})
            records = data.get("records") if isinstance(data, dict) else []
            records = [item for item in (records or []) if item.get("runId") != run.get("runId")]
            records.insert(0, record)
            write_json_file(AGENT_LEARNING_FILE, {"records": records[:500]})
        call["status"] = "SUCCESS"
        call["outputSummary"] = "已写入 Agent 历史学习库"
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
        "time": now,
        "message": f"开始执行 {step_name}",
        "status": "RUNNING",
    }]
    business_constraint = _ensure_business_flow_constraint(run)
    flow_brief = " → ".join((business_constraint.get("businessFlow") or [])[:4])
    if flow_brief:
        step["liveTrace"].append({
            "time": now,
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
        if tool_fn:
            _append_step_trace(run, step, f"调用工具：{getattr(tool_fn, '__name__', step_name)}", tool=step_name)
            result = tool_fn(run)
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
                    "time": now,
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
            if step_name == "RISK_REVIEW" and run.get("riskLevel") == "high":
                run["status"] = "WAIT_CONFIRM"
                run["currentStep"] = "WAIT_CONFIRM"
                now = time.strftime("%Y-%m-%dT%H:%M:%S")
                run.setdefault("pendingConfirmations", []).append({
                    "id": f"confirm-{int(time.time())}",
                    "type": "high_risk_action",
                    "message": f"命中高风险关键词：{'、'.join(run.get('riskHits', []))}，请确认是否继续",
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
    runs = load_agent_runs()
    # 移除 steps 详细信息，仅保留摘要字段
    summaries: List[Dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        summaries.append({
            "runId": run.get("runId", ""),
            "mode": run.get("mode", ""),
            "target": run.get("target", ""),
            "appName": run.get("appName", ""),
            "platform": run.get("platform", ""),
            "status": run.get("status", ""),
            "currentStep": run.get("currentStep", ""),
            "progress": run.get("progress", 0),
            "riskLevel": run.get("riskLevel", "low"),
            "createdAt": run.get("createdAt", ""),
            "updatedAt": run.get("updatedAt", ""),
            "error": run.get("error"),
        })
    return summaries[:limit]
