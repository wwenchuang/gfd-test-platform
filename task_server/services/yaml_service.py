"""YAML 业务服务层。

从 ``midscene-upload.py`` 精确迁移 YAML 解析、规范化、用例转 YAML、
覆盖度审计、运行时守卫等逻辑。仅依赖标准库（PyYAML 可选 try-import），可被
任何 task_server 路由/服务模块复用，且不会触发对 midscene-upload.py
的循环导入。

迁移源行号（仅供参考）：
- yaml_text                       midscene-upload.py:292-294
- yaml_task_names                 midscene-upload.py:297-304
- yaml_priority_stats             midscene-upload.py:307-334
- slug_for_file                   midscene-upload.py:337-342
- normalize_cases_payload         midscene-upload.py:807-829
- case_has_meaningful_assertion   midscene-upload.py:890-897
- requirement_points_from_payload midscene-upload.py:900-919
- coverage_blob_for_item          midscene-upload.py:922-938
- coverage_tokens                 midscene-upload.py:941-958
- point_covered                   midscene-upload.py:961-971
- audit_case_coverage             midscene-upload.py:974-996
- split_automation_ready_cases    midscene-upload.py:999-1029
- cases_to_midscene_yaml          midscene-upload.py:1252-1268
- _clean_yaml_name                midscene-upload.py:11268-11272
- find_yaml_task_block            midscene-upload.py:13048-13083
- replace_yaml_task_block         midscene-upload.py:13086-13092
- normalize_task_block_indent     midscene-upload.py:13095-13129
- list_yaml_task_blocks           midscene-upload.py:13146-13168
- yaml_with_single_task           midscene-upload.py:13132-13143
- normalize_yaml_runtime_guards   midscene-upload.py:12868-12882
- normalize_task_block_runtime_guards midscene-upload.py:12774-12865
- normalize_input_actions_in_task_block midscene-upload.py:11957-12001
- normalize_search_input_submit_in_task_block midscene-upload.py:12004-12102
- normalize_horizontal_icon_scrolls_in_task_block midscene-upload.py:12105-12191
- normalize_flowitem_syntax_in_task_block midscene-upload.py:11854-11954
- normalize_terminate_to_force_stop midscene-upload.py:12194-12218
- normalize_redundant_short_sleeps_in_task_block midscene-upload.py:12273-12304
- normalize_long_sleep_waits_in_task_block midscene-upload.py:12320-12354
- normalize_waitfor_timeouts_in_task_block midscene-upload.py:12357-12384
- normalize_inappropriate_model_processing_waits_in_task_block midscene-upload.py:12425-12471
- normalize_business_loading_waits_in_task_block midscene-upload.py:12474-12545
- normalize_combined_wait_click_actions_in_task_block midscene-upload.py:12548-12568
- insert_baseline_comments_into_task_block midscene-upload.py:12759-12771
- case_to_task_yaml               midscene-upload.py:1187-1252
- normalize_full_yaml_structure   midscene-upload.py:12925-13000
- normalize_yaml_from_model       midscene-upload.py:13003-13019
- normalize_yaml_task_block_from_model midscene-upload.py:13022-13038
- normalize_model_json            midscene-upload.py:15546-15555
- strip_yaml_quotes               midscene-upload.py:12705-12709
- normalize_yaml_scalar_value     midscene-upload.py:11805-11815
- save_file_version               midscene-upload.py:8100-8129
"""

from __future__ import annotations

import difflib
import base64
import copy
import hashlib
import json
import os
import re
import subprocess
import time
import traceback
import zipfile
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable, List, Tuple

try:
    import yaml as _pyyaml  # type: ignore
except Exception:  # pragma: no cover - PyYAML optional
    _pyyaml = None  # type: ignore

import threading

from task_server.services.yaml_executable_scorer import (
    assertion_tap_to_wait_prompt,
    conditional_action_to_wait_prompt,
    prompt_is_conditional_action,
    score_midscene_yaml_executable,
    tap_prompt_looks_assertion,
)

from ..config import (
    ASSET_DIR,
    CASE_DIR,
    DEFAULT_APP_PACKAGE,
    DEFAULT_WAITFOR_TIMEOUT_MS,
    ENABLE_ASSERT_WAITFOR,
    FIGMA_PARSE_LIMIT,
    GENERATE_JOB_DIR,
    GENERATE_LOCK,
    AI_COVERAGE_TOTAL_BUDGET_SECONDS,
    JOB_TIMEOUT_SECONDS,
    LEARNING_DIR,
    LONG_SLEEP_TO_WAITFOR_MS,
    MINDMAP_VISUAL_BATCH_SIZE,
    MINDMAP_VISUAL_MAX_IMAGES,
    MINDMAP_VISUAL_TIMEOUT_SECONDS,
    MINDMAP_VISUAL_TOTAL_BUDGET_SECONDS,
    MAX_LAUNCH_SLEEP_MS,
    MAX_STEP_SLEEP_MS,
    MAX_TERMINATE_SLEEP_MS,
    MAX_WAITFOR_TIMEOUT_MS,
    RUNTIME_GUARD_MODE,
    TASK_DIR,
    USE_AI_SKILL_PIPELINE,
    VERSION_DIR,
    YAML_VISUAL_BATCH_SIZE,
    YAML_VISUAL_TIMEOUT_SECONDS,
    YAML_VISUAL_TOTAL_BUDGET_SECONDS,
    env_int,
    safe_bool,
    safe_int,
)
from ..schemas import HIGH_RISK_KEYWORDS, MIDSCENE_FLOW_ACTIONS, TASK_LEVEL_ALLOWED_KEYS
from ..storage import (
    clean_filename,
    clean_asset_filename,
    clean_id,
    invalidate_json_cache,
    is_visible_yaml_filename,
    read_json_file,
    read_text_file,
    safe_join,
    unique_millis_id,
    write_bytes_file,
    write_json_file,
    write_text_file,
)
from .yaml_pattern_service import (
    build_yaml_pattern_contract_text,
    extract_yaml_patterns_from_examples,
    summarize_yaml_patterns,
)
from .yaml_baseline_cache import (
    get_yaml_baseline_cache,
    get_yaml_baseline_cache_status,
    search_diverse_baseline_examples,
    search_baseline_examples,
)
from .yaml_static_validator import (
    load_yaml_action_contract,
    validate_midscene_action_parameters,
    validate_yaml_static_executable,
)
from .yaml_template_matcher import (
    build_yaml_template_matcher_text,
    evaluate_baseline_template_matching,
    select_best_baseline_template,
)

__all__ = [
    "yaml_text",
    "yaml_task_names",
    "yaml_priority_stats",
    "slug_for_file",
    "normalize_cases_payload",
    "validate_yaml",
    "validate_midscene_flow",
    "split_automation_ready_cases",
    "audit_case_coverage",
    "cases_to_midscene_yaml",
    "cases_to_separate_midscene_yamls",
    "diff_yaml",
    "read_yaml",
    "save_yaml",
    "list_modules",
    "invalidate_modules_cache",
    "find_yaml_task_block",
    "replace_yaml_task_block",
    "normalize_task_block_indent",
    "list_yaml_task_blocks",
    "yaml_with_single_task",
    "normalize_yaml_runtime_guards",
    "normalize_task_block_runtime_guards",
    "normalize_input_actions_in_task_block",
    "normalize_search_input_submit_in_task_block",
    "normalize_horizontal_icon_scrolls_in_task_block",
    "normalize_flowitem_syntax_in_task_block",
    "normalize_terminate_to_force_stop",
    "normalize_redundant_short_sleeps_in_task_block",
    "normalize_long_sleep_waits_in_task_block",
    "normalize_waitfor_timeouts_in_task_block",
    "normalize_inappropriate_model_processing_waits_in_task_block",
    "normalize_business_loading_waits_in_task_block",
    "normalize_combined_wait_click_actions_in_task_block",
    "insert_baseline_comments_into_task_block",
    "normalize_full_yaml_structure",
    "normalize_yaml_from_model",
    "normalize_yaml_task_block_from_model",
    "normalize_model_json",
    "normalize_yaml_scalar_value",
    "strip_yaml_quotes",
    "collect_yaml_reference_examples",
    "collect_yaml_baseline_library_examples",
    "build_yaml_reference_examples_text",
    "record_yaml_reference_examples",
    "extract_yaml_patterns_from_examples",
    "validate_yaml_static_executable",
    "dry_run_midscene_yaml",
    "repair_generated_yaml_static_errors",
    "should_ai_rewrite_for_executable_gate",
    "ai_rewrite_yaml_for_executable_gate",
    "case_to_task_yaml",
    "extract_midscene_tasks",
    "validate_midscene_yaml_executability",
    "save_file_version",
    "version_dir_for",
    "extract_baseline_meta_from_block",
    "stable_case_id",
]


def _lazy(name, module):
    def _wrapper(*args, **kwargs):
        mod = __import__(module, fromlist=[name])
        return getattr(mod, name)(*args, **kwargs)
    return _wrapper


automatic_baseline_repair_enabled = _lazy("automatic_baseline_repair_enabled", "task_server.services.case_service")
build_cases_payload_from_skills = _lazy("build_cases_payload_from_skills", "task_server.services.ai_skill_service")
should_fast_path_baidu_entry_visibility = _lazy("should_fast_path_baidu_entry_visibility", "task_server.services.ai_skill_service")
call_dashscope_cases = _lazy("call_dashscope_cases", "task_server.services.ai_skill_service")
call_dashscope_refine_cases = _lazy("call_dashscope_refine_cases", "task_server.services.ai_skill_service")
dashscope_chat_content = _lazy("dashscope_chat_content", "task_server.services.ai_skill_service")
improve_case_coverage = _lazy("improve_case_coverage", "task_server.services.ai_skill_service")
normalize_requirement_analysis_result = _lazy("normalize_requirement_analysis_result", "task_server.services.ai_skill_service")
generation_volume_targets = _lazy("generation_volume_targets", "task_server.services.ai_skill_service")
generation_targets_for_scope = _lazy("generation_targets_for_scope", "task_server.services.ai_skill_service")
select_smoke_cases_for_payload = _lazy("select_smoke_cases_for_payload", "task_server.services.ai_skill_service")
call_skill_baseline_reranker = _lazy("call_skill_baseline_reranker", "task_server.services.ai_skill_service")
call_skill_execution_scope_planner = _lazy("call_skill_execution_scope_planner", "task_server.services.ai_skill_service")
call_skill_executable_yaml_planner = _lazy("call_skill_executable_yaml_planner", "task_server.services.ai_skill_service")
apply_executable_yaml_plan_to_payload = _lazy("apply_executable_yaml_plan_to_payload", "task_server.services.ai_skill_service")
executable_yaml_portfolio_audit = _lazy("executable_yaml_portfolio_audit", "task_server.services.ai_skill_service")
load_knowledge_context = _lazy("load_knowledge_context", "task_server.services.knowledge_service")
load_figma_generation_context = _lazy("load_figma_generation_context", "task_server.services.knowledge_service")
parse_figma_design = _lazy("parse_figma_design", "task_server.services.knowledge_service")
score_figma_draft_for_requirement = _lazy("score_figma_draft_for_requirement", "task_server.services.knowledge_service")
save_asset_files = _lazy("save_asset_files", "task_server.services.knowledge_service")
append_asset_files = _lazy("append_asset_files", "task_server.services.knowledge_service")
load_asset_contents = _lazy("load_asset_contents", "task_server.services.knowledge_service")
asset_meta_path = _lazy("asset_meta_path", "task_server.services.knowledge_service")
update_asset_request_context = _lazy("update_asset_request_context", "task_server.services.knowledge_service")
build_report_checkpoints = _lazy("build_report_checkpoints", "task_server.services.report_service")
update_task_meta = _lazy("update_task_meta", "task_server.services.job_service")
create_pending_job = _lazy("create_pending_job", "task_server.services.job_service")
normalize_device_strategy = _lazy("normalize_device_strategy", "task_server.services.job_service")


def case_ui_design_meta_path(case_set_id):
    return safe_join(case_ui_design_dir(case_set_id), "meta.json")


def parse_time(value):
    if not value:
        return 0
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return time.mktime(time.strptime(str(value)[:19], fmt))
        except Exception:
            continue
    return 0


GENERATE_JOB_ACTIVE_STATUSES = {"pending", "running"}
GENERATE_JOB_TERMINAL_STATUSES = {"success", "failed", "cancelled", "timeout"}
GENERATE_JOB_TIMEOUT_SECONDS = max(300, env_int("MIDSCENE_GENERATE_JOB_TIMEOUT_SECONDS", JOB_TIMEOUT_SECONDS))
AGENT_GENERATE_YAML_TIMEOUT_SECONDS = max(300, env_int("MIDSCENE_AGENT_GENERATE_YAML_TIMEOUT_SECONDS", 900))
MINDMAP_JOB_TIMEOUT_SECONDS = max(300, env_int("MIDSCENE_MINDMAP_JOB_TIMEOUT_SECONDS", min(JOB_TIMEOUT_SECONDS, GENERATE_JOB_TIMEOUT_SECONDS)))
FIGMA_PARSE_JOB_TIMEOUT_SECONDS = max(120, env_int("MIDSCENE_FIGMA_PARSE_JOB_TIMEOUT_SECONDS", min(900, GENERATE_JOB_TIMEOUT_SECONDS)))


def generate_job_timeout_seconds(job):
    job_type = str((job or {}).get("type") or "").strip().lower()
    explicit_timeout = safe_int((job or {}).get("timeout_seconds") or (job or {}).get("timeoutSeconds"), 0)
    if job_type == "agent_generate_yaml":
        return max(AGENT_GENERATE_YAML_TIMEOUT_SECONDS, explicit_timeout)
    if job_type == "mindmap_only":
        return max(MINDMAP_JOB_TIMEOUT_SECONDS, explicit_timeout)
    if job_type in ("figma_parse", "figma"):
        return max(FIGMA_PARSE_JOB_TIMEOUT_SECONDS, explicit_timeout)
    return max(GENERATE_JOB_TIMEOUT_SECONDS, explicit_timeout)


def generate_job_elapsed_seconds(job, now_ts=None):
    if not isinstance(job, dict):
        return 0
    now_ts = now_ts or time.time()
    started_ts = (
        parse_time(job.get("started_at"))
        or parse_time(job.get("created_at"))
        or parse_time(job.get("updated_at"))
    )
    if not started_ts:
        return 0
    return max(0, int(now_ts - started_ts))


def expire_generate_job_if_stale(job, persist=True):
    """把长期停留在 pending/running 的生成任务收敛为 timeout，避免 UI 假运行。"""
    if not isinstance(job, dict):
        return job
    status = str(job.get("status") or "").strip().lower()
    if status not in GENERATE_JOB_ACTIVE_STATUSES:
        return job
    timeout_seconds = generate_job_timeout_seconds(job)
    elapsed_seconds = generate_job_elapsed_seconds(job)
    if not elapsed_seconds or elapsed_seconds < timeout_seconds:
        return job

    stage = job.get("step") or "生成任务"
    job_type = str(job.get("type") or "")
    if job_type == "agent_generate_yaml":
        type_label = "Agent YAML 生成"
    elif job_type == "mindmap_only":
        type_label = "脑图生成"
    elif job_type in ("figma_parse", "figma"):
        type_label = "Figma 解析"
    else:
        type_label = "AI 生成"
    message = (
        f"{type_label}超过 {timeout_seconds} 秒仍未完成，已自动标记为超时；"
        f"最后阶段：{stage}。这表示后台任务没有正常落到完成态；"
        "常见原因包括网络请求超时、生成进程/后台线程中断，或外部模型服务长时间未返回。"
    )
    detail = generation_failure_detail(TimeoutError(message), {**job, "step": stage})
    changes = {
        "ok": False,
        "status": "timeout",
        "progress": max(5, min(99, safe_int(job.get("progress"), 90) or 90)),
        "step": f"{stage}超时",
        "message": message,
        "error": message,
        "error_detail": detail,
        "failure_detail": detail,
        "elapsed_seconds": elapsed_seconds,
        "timeout_seconds": timeout_seconds,
        "expired_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    job_id = job.get("job_id")
    if persist and job_id:
        return update_generate_job(job_id, **changes)
    expired = dict(job)
    expired.update(changes)
    return expired


def analysis_list(analysis, *keys):
    if not isinstance(analysis, dict):
        return []
    for key in keys:
        items = normalize_text_list(analysis.get(key))
        if items:
            return items
    return []


def mm_node(text, children=None, indent=0):
    children = children or []
    pad = "  " * indent
    label = str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    if children:
        return f'{pad}<node TEXT="{label}">\n' + "\n".join(children) + f"\n{pad}</node>"
    return f'{pad}<node TEXT="{label}" />'


def scenario_key(value):
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def scenario_method_text(scenario):
    methods = normalize_text_list(
        (scenario or {}).get("design_method")
        or (scenario or {}).get("designMethod")
        or (scenario or {}).get("method")
        or (scenario or {}).get("methods")
    )
    return " / ".join(methods[:2])


def case_mm_title(case):
    case = case or {}
    prefix = str(case.get("case_id") or "").strip()
    title = str(case.get("title") or case.get("case_name") or case.get("name") or "未命名用例").strip()
    priority = str(case.get("priority") or "").strip().upper()
    suffix = f" [{priority}]" if priority else ""
    if is_smoke_case(case):
        suffix = f"{suffix} flag=冒烟"
    return f"{prefix} {title}{suffix}".strip()


def _mindmap_step_text(step):
    if isinstance(step, dict):
        action = first_non_empty(
            step.get("step"),
            step.get("action"),
            step.get("desc"),
            step.get("description"),
            step.get("name"),
            step.get("prompt"),
        )
        value = first_non_empty(step.get("value"), step.get("input"), step.get("text"))
        if value and value not in str(action):
            return f"{action}：{value}" if action else str(value)
        return str(action or "").strip()
    return str(step or "").strip()


def _mindmap_step_expected(step):
    if not isinstance(step, dict):
        return []
    return normalize_text_list(
        step.get("expect")
        or step.get("expected")
        or step.get("assertion")
        or step.get("assertions")
        or step.get("result")
    )


def case_mindmap_detail_nodes(case, indent=4):
    case = case if isinstance(case, dict) else {}
    raw_steps = case.get("steps") or case.get("flow") or []
    if isinstance(raw_steps, str):
        raw_steps = [raw_steps]
    elif not isinstance(raw_steps, list):
        raw_steps = normalize_text_list(raw_steps)

    steps = []
    step_expected = []
    for step in raw_steps:
        text = _mindmap_step_text(step)
        if text:
            steps.append(text)
        step_expected.extend(_mindmap_step_expected(step))

    if not steps:
        path = first_non_empty(
            case_value(case, "business_path", "businessPath", "path", "flow_path", "flowPath", "navigation_path", "navigationPath")
        )
        if path:
            steps = [part.strip() for part in re.split(r"\s*(?:->|→|>|/)\s*", str(path)) if part.strip()]

    expectations = normalize_text_list(
        case.get("assertions")
        or case.get("expects")
        or case.get("expected")
        or case.get("expect")
    )
    expected_result = first_non_empty(
        case_value(case, "expected_result", "expectedResult", "expectation")
    )
    if expected_result:
        expectations.insert(0, expected_result)
    expectations.extend(step_expected)

    deduped_expectations = []
    seen = set()
    for item in expectations:
        key = re.sub(r"\s+", "", str(item or ""))
        if key and key not in seen:
            seen.add(key)
            deduped_expectations.append(item)

    children = []
    if steps:
        children.append(mm_node(
            "测试步骤",
            [mm_node(f"{idx}. {item}", indent=indent + 1) for idx, item in enumerate(steps[:12], start=1)],
            indent=indent,
        ))
    else:
        children.append(mm_node("测试步骤", [mm_node("待补充明确操作步骤", indent=indent + 1)], indent=indent))

    if deduped_expectations:
        children.append(mm_node(
            "预期结果",
            [mm_node(f"{idx}. {item}", indent=indent + 1) for idx, item in enumerate(deduped_expectations[:10], start=1)],
            indent=indent,
        ))
    else:
        children.append(mm_node("预期结果", [mm_node("按需求验收点检查页面结果符合预期", indent=indent + 1)], indent=indent))

    data_requirements = first_non_empty(case_value(case, "data_requirements", "dataRequirements", "test_data", "testData"))
    if data_requirements:
        children.append(mm_node(f"测试数据/前置：{data_requirements}", indent=indent))
    return children


def scenario_as_mindmap_case(scenario):
    scenario = scenario if isinstance(scenario, dict) else {}
    name = first_non_empty(scenario.get("scenario"), scenario.get("name"), scenario.get("title"), "未命名场景")
    path = first_non_empty(
        scenario.get("business_path"),
        scenario.get("businessPath"),
        scenario.get("path"),
        scenario.get("flow_path"),
        scenario.get("flowPath"),
    )
    expected = first_non_empty(
        scenario.get("expected"),
        scenario.get("expected_result"),
        scenario.get("expectedResult"),
        scenario.get("visible_outcome"),
        scenario.get("visibleOutcome"),
    )
    reason = first_non_empty(scenario.get("reason"), scenario.get("automation_reason"), scenario.get("automationReason"))
    steps = []
    if path:
        steps = [part.strip() for part in re.split(r"\s*(?:->|→|>|/)\s*", str(path)) if part.strip()]
    if not steps:
        steps = [f"按场景“{name}”准备测试条件", "执行需求描述中的核心操作", "观察页面反馈、状态变化或提示文案"]
    assertions = [expected] if expected else [f"场景“{name}”的可见结果符合需求描述"]
    title_prefix = "人工/待准备" if scenario.get("automation_suitable") is False else "场景检查"
    row = {
        "title": f"{title_prefix}：{name}",
        "priority": first_non_empty(scenario.get("priority"), "P2"),
        "steps": steps,
        "assertions": assertions,
        "expected_result": expected,
        "data_requirements": reason if scenario.get("automation_suitable") is False else "",
    }
    return row


def is_image_file(filename):
    return str(filename or "").lower().endswith((".png", ".jpg", ".jpeg"))


def guess_mime(filename):
    lower = str(filename or "").lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith((".jpg", ".jpeg")):
        return "image/jpeg"
    return "application/octet-stream"


def extract_docx_text(path):
    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"<[^>]+>", "", xml)
    except Exception:
        return ""


def extract_pdf_text(path):
    try:
        result = subprocess.run(["pdftotext", path, "-"], capture_output=True, text=True, timeout=20)
        if result.returncode == 0:
            return result.stdout
    except Exception:
        pass
    try:
        import pypdf
        reader = pypdf.PdfReader(path)
        parts = []
        total = 0
        for page in reader.pages[:30]:
            text = page.extract_text() or ""
            if not text.strip():
                continue
            parts.append(text)
            total += len(text)
            if total >= 30000:
                break
        extracted = "\n".join(parts).strip()
        if extracted:
            return extracted[:30000]
    except Exception:
        pass
    try:
        with open(path, "rb") as f:
            return f.read(1024 * 1024).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def extract_doc_text(path):
    try:
        with open(path, "rb") as f:
            return f.read(1024 * 1024).decode("utf-8", errors="ignore")
    except Exception:
        return ""


def extract_mm_text(path):
    try:
        return read_text_file(path, default="")
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 常量 — 从 midscene-upload.py 迁移
# ---------------------------------------------------------------------------

SUPPORTED_FLOW_ITEMS = MIDSCENE_FLOW_ACTIONS

FLOW_ITEM_ALIASES = {
    "tap": "aiTap",
    "click": "aiTap",
    "aiClick": "aiTap",
    "action": "ai",
    "act": "ai",
    "assert": "aiAssert",
    "wait": "aiWaitFor",
    "waitFor": "aiWaitFor",
    "aiWait": "aiWaitFor",
    "adb": "runAdbShell",
}
FLOW_ITEM_ALIASES.update({item.lower(): item for item in SUPPORTED_FLOW_ITEMS})

PROMPT_STYLE_FLOW_ITEMS = {
    "ai", "aiAct", "aiAction", "aiTap", "aiHover", "aiAssert", "aiWaitFor",
    "aiQuery", "aiAsk", "aiBoolean", "aiNumber", "aiString", "aiInput",
    "aiKeyboardPress", "aiScroll",
}

FLOW_CHILD_KEYS = {
    "locate", "prompt", "value", "timeout", "errorMessage", "name", "keyName",
    "direction", "scrollType", "distance", "deepThink", "xpath", "cacheable",
    "autoDismissKeyboard", "mode", "method", "endpoint", "data", "content",
    "title", "duration", "target", "query", "schema",
}

FLOW_ACTION_PREFIX_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*:\s*(.*)$", re.S)


# ---------------------------------------------------------------------------
# 基础 YAML 字符串处理
# ---------------------------------------------------------------------------

def yaml_text(value: Any) -> str:
    """YAML 字符串转义（双引号包裹，反斜杠/双引号转义）。

    Source: midscene-upload.py 行 292-294。
    """
    value = str(value or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def read_yaml(module, file):
    """按模块名和文件名读取 YAML 内容。"""
    path = safe_join(TASK_DIR, module, file)
    return read_text_file(path, default="")


def save_yaml(module, file, content):
    """按模块名和文件名保存 YAML 内容。"""
    file = clean_filename(file)
    path = safe_join(TASK_DIR, module, file)
    write_text_file(path, content)
    return path


def _clean_yaml_name(value: Any) -> str:
    """清理 YAML name 字段两端引号与转义。"""
    value = (value or "").strip()
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        value = value[1:-1]
    return value.replace('\\"', '"').strip()


def yaml_task_names(yaml_text_value: str) -> List[str]:
    """从 YAML 内容提取所有 task name。"""
    names: List[str] = []
    name_re = re.compile(r"^\s*-\s+name:\s*(.+?)\s*$")
    for line in (yaml_text_value or "").splitlines():
        m = name_re.match(line)
        if m:
            names.append(_clean_yaml_name(m.group(1)))
    return names


def yaml_priority_stats(yaml_text_value: str) -> dict:
    """统计 YAML 优先级和冒烟测试分布。"""
    stats = {"total": 0, "p0": 0, "p1": 0, "p2": 0, "p3": 0, "smoke": 0, "loaded": True}
    lines = (yaml_text_value or "").splitlines()
    task_starts: List[int] = []
    name_re = re.compile(r"^\s*-\s+name:\s*(.+?)\s*$")
    for idx, line in enumerate(lines):
        if name_re.match(line):
            task_starts.append(idx)
    for pos, start in enumerate(task_starts):
        end = task_starts[pos + 1] if pos + 1 < len(task_starts) else min(len(lines), start + 40)
        block = "\n".join(lines[start:end])
        priority = "P2"
        pm = re.search(r"#\s*baseline\.priority\s*:\s*(P[0-3])", block, flags=re.I)
        if pm:
            priority = pm.group(1).upper()
        smoke = False
        sm = re.search(r"#\s*baseline\.smoke\s*:\s*(.+?)\s*(?:\n|$)", block, flags=re.I)
        tm = re.search(r"#\s*baseline\.tags\s*:\s*(.+?)\s*(?:\n|$)", block, flags=re.I)
        if sm and re.search(r"true|1|yes|是|冒烟|smoke", sm.group(1), flags=re.I):
            smoke = True
        if tm and re.search(r"冒烟|smoke", tm.group(1), flags=re.I):
            smoke = True
        stats["total"] += 1
        key = priority.lower()
        stats[key if key in stats else "p2"] += 1
        if smoke:
            stats["smoke"] += 1
    return stats


def slug_for_file(value: Any) -> str:
    """生成文件 slug。"""
    value = (value or "测试用例").strip()
    value = re.sub(r'[\\/:*?"<>|]+', "_", value)
    value = re.sub(r"\s+", "_", value)
    value = value.strip("._-")
    return value[:80] or "测试用例"


# ---------------------------------------------------------------------------
# YAML 引号/标点/结构工具
# ---------------------------------------------------------------------------

def strip_yaml_quotes(value):
    """去除 YAML 值两端引号。"""
    value = str(value or "").strip()
    if len(value) >= 2 and value[0] in ("'", '"') and value[-1] == value[0]:
        value = value[1:-1]
    return value.replace('\\"', '"').replace("\\'", "'").strip()


def normalize_yaml_scalar_value(value):
    """规范化 YAML 标量值。"""
    value = str(value or "").strip()
    if not value:
        return '""'
    if value.lower() in ("true", "false"):
        return value.lower()
    if value.lower() in ("null", "none"):
        return "null"
    if value[0] in ("'", '"', "{", "[", "|", ">") or re.match(r"^-?\d+(\.\d+)?$", value):
        return value
    return yaml_text(value)


def yaml_comment_text(value, limit=180):
    """YAML 注释文本清洗。"""
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text).replace("#", "＃")
    return text[:limit]


# ---------------------------------------------------------------------------
# 通用工具函数
# ---------------------------------------------------------------------------

def truthy_text(value):
    """判断文本是否为真值。"""
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return False
    return text in ("1", "true", "yes", "y", "on", "是", "冒烟", "smoke", "smoke_test")


def first_non_empty(*values):
    """返回第一个非空值。"""
    for value in values:
        if isinstance(value, list):
            value = "；".join(str(item).strip() for item in value if str(item).strip())
        elif isinstance(value, dict):
            value = "；".join(f"{k}：{v}" for k, v in value.items() if str(v).strip())
        else:
            value = str(value or "").strip()
        if value:
            return value
    return ""


def ai_model_config_from_request(data):
    """Extract the user-selected AI model config from request payload."""
    data = data if isinstance(data, dict) else {}
    config = {
        "providerId": first_non_empty(
            data.get("modelProviderId"),
            data.get("aiProviderId"),
            data.get("providerId"),
            data.get("provider"),
        ),
        "model": first_non_empty(
            data.get("aiModel"),
            data.get("model"),
            data.get("modelName"),
        ),
    }
    return {key: value for key, value in config.items() if value}


def case_value(case, *keys):
    """从用例字典中按优先键序列取值。"""
    for key in keys:
        if key in case and case.get(key) not in (None, ""):
            return case.get(key)
    return ""


def normalize_text_list(value):
    """规范化文本列表。"""
    if value is None:
        return []
    if isinstance(value, list):
        result: List[str] = []
        for item in value:
            if isinstance(item, dict):
                text = first_non_empty(
                    item.get("action"), item.get("step"), item.get("description"),
                    item.get("name"), item.get("expected"),
                )
                if not text:
                    text = "；".join(f"{key}：{val}" for key, val in item.items() if str(val).strip())
            else:
                text = str(item or "").strip()
            if text:
                result.append(text)
        return result
    if isinstance(value, dict):
        return [f"{key}：{val}" for key, val in value.items() if str(val).strip()]
    text = str(value or "").strip()
    return [text] if text else []


SCOPE_GUARD_UNREQUESTED_PATTERNS = (
    "历史", "打印记录", "记录干扰", "干扰", "慢加载", "超时", "防抖", "重复进入",
    "返回重进", "状态保持", "渲染残留", "清理缓存", "缓存", "空态", "无结果",
    "规格切换", "分页", "旧入口", "骨架屏", "弹窗遮挡", "连续", "多次刷新",
    "加载过程", "未完全加载", "宽屏", "返回状态一致性", "返回后",
)
SCOPE_GUARD_CHANGE_KEYWORDS = (
    "百度网盘", "网盘入口", "网盘导入", "百度导入", "新增入口", "导入入口",
    "文档打印", "照片打印", "扫描复印", "普通照片", "证件照", "照片拼版",
    "AI建模", "图片建模", "语音创作", "文字建模", "开始创作", "标牌", "印章",
    "涂鸦建模", "模型导入", "模型上新", "中台", "耗材颜色", "确认耗材",
)
SCOPE_GUARD_GENERIC_TOKENS = {
    "测试", "验证", "页面", "功能", "需求", "用例", "执行", "当前", "进行", "是否",
    "可以", "需要", "点击", "进入", "打开", "显示", "相关", "流程", "按钮", "模块",
    "状态", "结果", "完成", "成功", "失败", "检查", "确认", "用户", "操作", "场景",
    "自动化", "生成", "入口", "新增", "调整", "支持", "正常", "展示", "首页",
}
FIGMA_INTERNAL_CASE_NAME_RE = re.compile(r"(备份\s*\d*|frame\s*\d*|节点|画板|画布|设计稿)", re.I)
SCOPE_GUARD_ABSTRACT_UI_TARGET_PATTERNS = (
    "相关页面或入口区域",
    "相关页面或相关入口",
    "相关页面或入口",
    "目标入口区域",
)
SCOPE_GUARD_TEST_TAXONOMY_TERMS = (
    "入口一致性",
    "入口可达性",
    "跨设备适配",
    "权限与状态",
    "测试分组",
    "测试场景",
    "校验场景",
    "验证场景",
)


def _scope_guard_join_values(*values) -> str:
    parts: List[str] = []
    for value in values:
        if isinstance(value, dict):
            parts.extend(_scope_guard_join_values(key, val) for key, val in value.items())
        elif isinstance(value, list):
            parts.extend(_scope_guard_join_values(item) for item in value)
        elif value not in (None, ""):
            parts.append(str(value))
    return " ".join(part for part in parts if part).strip()


def _scope_guard_action_value_texts(value) -> List[str]:
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, list):
        result: List[str] = []
        for item in value:
            result.extend(_scope_guard_action_value_texts(item))
        return result
    if isinstance(value, dict):
        result: List[str] = []
        structural_keys = {
            "timeout", "deepthink", "cacheable", "sleep", "duration", "distance",
            "x", "y", "width", "height", "index", "repeat", "repeatcount",
        }
        for key, item in value.items():
            if str(key or "").strip().lower() in structural_keys:
                continue
            result.extend(_scope_guard_action_value_texts(item))
        return result
    return []


def _scope_guard_yaml_action_values(yaml_text: str) -> dict:
    actions: dict = {}

    def add(action, value):
        texts = _scope_guard_action_value_texts(value)
        if texts:
            actions.setdefault(action, []).extend(texts)

    def walk(value):
        if isinstance(value, list):
            for item in value:
                walk(item)
            return
        if not isinstance(value, dict):
            return
        for key, item in value.items():
            action = str(key or "").strip()
            if action == "name" and isinstance(item, str):
                add("name", item)
            elif action in MIDSCENE_FLOW_ACTIONS:
                add(action, item)
            else:
                walk(item)

    parsed = None
    if _pyyaml is not None and str(yaml_text or "").strip():
        try:
            parsed = _pyyaml.safe_load(yaml_text)
        except Exception:
            parsed = None
    if parsed is not None:
        walk(parsed)
        return actions

    for line in str(yaml_text or "").splitlines():
        match = re.match(r"^\s*-?\s*(name|[A-Za-z][A-Za-z0-9_]*)\s*:\s*(.+?)\s*$", line)
        if not match:
            continue
        action, value = match.groups()
        if action == "name" or action in MIDSCENE_FLOW_ACTIONS:
            add(action, strip_yaml_quotes(value))
    return actions


def _scope_guard_yaml_semantic_text(yaml_text: str) -> str:
    """Extract task/action language while excluding structural YAML keys."""
    actions = _scope_guard_yaml_action_values(yaml_text)
    return _scope_guard_join_values(*actions.values())


def _scope_guard_abstract_ui_targets(case: dict, yaml_text: str = "") -> List[str]:
    candidates = normalize_text_list((case or {}).get("steps") or [])
    candidates.extend(_scope_guard_yaml_action_values(yaml_text).get("aiTap") or [])
    invalid = []
    for candidate in candidates:
        text = str(candidate or "").strip()
        if not text:
            continue
        navigates = any(term in text for term in ("点击", "进入", "打开", "aiTap"))
        if any(term in text for term in SCOPE_GUARD_ABSTRACT_UI_TARGET_PATTERNS):
            invalid.append(text)
        elif navigates and any(term in text for term in SCOPE_GUARD_TEST_TAXONOMY_TERMS):
            invalid.append(text)
    return list(dict.fromkeys(invalid))


def _scope_guard_tokens(text: str) -> List[str]:
    normalized = re.sub(r"REQ[-_ ]?\d+\s*[:：.-]?", " ", str(text or ""), flags=re.I)
    normalized = normalized.lower()
    raw = re.findall(r"[a-z0-9_.-]{2,}|[\u4e00-\u9fff]{2,}", normalized)
    tokens: List[str] = []

    def add(token: str):
        token = str(token or "").strip().lower()
        if not token or token in SCOPE_GUARD_GENERIC_TOKENS:
            return
        if re.fullmatch(r"\d+", token):
            return
        if token not in tokens:
            tokens.append(token)

    for token in raw:
        add(token)
        if re.fullmatch(r"[\u4e00-\u9fff]{5,}", token):
            for size in (2, 3, 4):
                for idx in range(0, max(0, len(token) - size + 1)):
                    add(token[idx:idx + size])
    return tokens


def _scope_guard_topic_terms(requirement_blob: str) -> List[str]:
    compact = re.sub(r"\s+", "", str(requirement_blob or ""))
    terms = []
    aliases = [
        ("百度网盘", ("百度网盘", "网盘")),
        ("文档打印", ("文档打印", "三方文档打印")),
        ("照片打印", ("照片打印", "普通照片打印")),
        ("证件照", ("证件照", "普通证件照", "智能证件照", "一寸照", "1寸", "2寸")),
        ("照片拼版", ("照片拼版",)),
        ("扫描复印", ("扫描复印", "复印扫描")),
        ("AI建模", ("AI建模", "ai建模")),
        ("图片建模", ("图片建模",)),
        ("语音创作", ("语音创作", "语音输入")),
        ("文字建模", ("文字建模", "文本输入")),
        ("标牌", ("标牌",)),
        ("印章", ("印章", "趣味印章")),
        ("涂鸦建模", ("涂鸦建模", "涂鸦")),
        ("模型导入", ("模型导入", "导入模型")),
        ("模型上新", ("模型上新", "上新")),
        ("耗材颜色", ("耗材颜色", "确认耗材")),
        ("中台模型库", ("中台模型库", "中台")),
    ]
    for canonical, words in aliases:
        if any(word in compact for word in words):
            terms.append(canonical)
    return list(dict.fromkeys(terms))


def _scope_guard_requirement_ids(value) -> List[str]:
    ids = []
    for match in re.finditer(r"REQ[-_ ]?0*(\d+)", _scope_guard_join_values(value), flags=re.I):
        requirement_id = f"REQ-{int(match.group(1)):03d}"
        if requirement_id not in ids:
            ids.append(requirement_id)
    return ids


def _scope_guard_requirement_point_texts(analysis: dict) -> List[str]:
    points: List[str] = []
    for key in ("requirement_points", "requirementPoints", "test_points", "testPoints"):
        for item in normalize_text_list((analysis or {}).get(key)):
            text = str(item or "").strip()
            if text and text not in points:
                points.append(text)
    return points


def _scope_guard_mapped_requirement_points(analysis: dict, case_blob: str) -> tuple:
    """Map a generated case to its own requirement points before token review."""
    points = _scope_guard_requirement_point_texts(analysis)
    case_ids = _scope_guard_requirement_ids(case_blob)
    if case_ids:
        wanted = set(case_ids)
        matched = [point for point in points if wanted.intersection(_scope_guard_requirement_ids(point))]
        if matched:
            return matched, case_ids

    case_topics = set(_scope_guard_topic_terms(case_blob)) - {"百度网盘"}
    if case_topics:
        matched = [
            point for point in points
            if case_topics.intersection(set(_scope_guard_topic_terms(point)) - {"百度网盘"})
        ]
        if matched:
            return matched, case_ids
    return [], case_ids


def build_requirement_semantic_constraints_text(text_assets: List[str], title: str = "") -> str:
    """Build deterministic requirement constraints before asking AI to compose YAML.

    This is deliberately rule-based: the model can decide wording and steps, but
    it must not replace explicit requirement points with nearby Figma page names
    or generic robustness cases.
    """
    blob = "\n".join([str(title or "")] + [str(item or "") for item in (text_assets or [])])
    compact = re.sub(r"\s+", "", blob)
    constraints: List[str] = []

    if "百度网盘" in compact and any(word in compact for word in ("基础打印", "文档打印", "照片打印", "扫描复印", "复印扫描")):
        constraints.append("""【需求文档硬约束：基础打印模块新增百度网盘入口】
本需求的业务范围以需求文档为准，Figma 只作为 UI 参考，不允许把 Figma 内部页名当作用例主题。
必须优先覆盖这些业务功能点：
1. 三方文档打印：百度网盘入口移至第 2 个，位于本地文档之后。
2. 照片打印：普通照片打印、普通证件照、智能证件照、照片拼版导入时增加百度网盘导入选项。
3. 扫描复印：复印/扫描首页增加百度网盘导入入口。
4. 埋点/终态关注：百度网盘文档、百度网盘照片、百度网盘复印入口可见，并可点击或进入授权/导入流程。
生成约束：
- 用例标题必须使用业务名称，不得出现“首页备份2、备份、引导1、Frame、节点、画板、设计稿”等 Figma 内部名称。
- 首批冒烟只覆盖正常入口链路：文档打印入口展示与点击、照片打印导入入口、扫描复印入口。
- 弹窗遮挡、连续进出、加载过程中点击、宽屏适配、多次刷新、返回状态一致性等鲁棒性场景，如果需求文档未明确要求，只能放到需确认/人工扩展，不得进入自动冒烟。""")

    return "\n\n".join(item.strip() for item in constraints if item.strip())


def generated_case_requirement_scope_review(case: dict, analysis: dict, yaml_text: str = "") -> dict:
    """Detect generated cases that drift outside the current requirement scope.

    AI can legitimately propose richer coverage, but newly generated YAML must not
    auto-run scenarios that are not traceable to the current requirement document.
    This guard only downgrades execution eligibility; it does not delete cases.
    """
    case = case if isinstance(case, dict) else {}
    analysis = analysis if isinstance(analysis, dict) else {}
    requirement_blob = _scope_guard_join_values(
        analysis.get("requirement_points"),
        analysis.get("requirementPoints"),
        analysis.get("test_points"),
        analysis.get("testPoints"),
        analysis.get("business_goals"),
        analysis.get("businessGoals"),
        analysis.get("entry_points"),
        analysis.get("entryPoints"),
        analysis.get("visible_outcomes"),
        analysis.get("visibleOutcomes"),
        analysis.get("business_flow"),
        analysis.get("businessFlow"),
        analysis.get("risks"),
        analysis.get("assumptions"),
    )
    yaml_semantic_text = _scope_guard_yaml_semantic_text(yaml_text)
    case_blob = _scope_guard_join_values(
        case.get("title"),
        case.get("name"),
        case.get("requirement_point"),
        case.get("requirementPoint"),
        case.get("source_requirement_point"),
        case.get("sourceRequirementPoint"),
        case.get("requirementRefs"),
        case.get("requirement_refs"),
        case.get("scenario"),
        case.get("goal"),
        case.get("coverage"),
        case.get("risk"),
        case.get("business_path"),
        case.get("businessPath"),
        case.get("expected_result"),
        case.get("expectedResult"),
        case.get("preconditions"),
        case.get("steps"),
        case.get("assertions"),
        case.get("tags"),
        yaml_semantic_text,
    )
    compact_requirement = re.sub(r"\s+", "", requirement_blob)
    compact_case = re.sub(r"\s+", "", case_blob)
    compact_case_lower = compact_case.lower()
    mapped_requirement_points, mapped_requirement_ids = _scope_guard_mapped_requirement_points(analysis, case_blob)
    trace_requirement_blob = _scope_guard_join_values(mapped_requirement_points) or requirement_blob
    reasons: List[str] = []
    title_blob = _scope_guard_join_values(case.get("title"), case.get("name"))
    if FIGMA_INTERNAL_CASE_NAME_RE.search(title_blob):
        reasons.append("用例标题包含 Figma 内部页名/设计稿标识，应改为业务名称后再自动执行")
    abstract_targets = _scope_guard_abstract_ui_targets(case, yaml_text)
    if abstract_targets:
        reasons.append("aiTap/导航步骤使用测试分组或抽象模块名作为界面目标，缺少真实可见入口文案")

    topic_terms = _scope_guard_topic_terms(requirement_blob)
    if topic_terms:
        missing_topics = [
            term for term in topic_terms
            if term.lower() not in compact_case_lower
            and not any(alias.lower() in compact_case_lower for alias in _scope_guard_tokens(term))
        ]
        if len(missing_topics) == len(topic_terms):
            reasons.append("用例未命中当前需求核心对象：" + "、".join(topic_terms[:4]))

    if compact_requirement and any(term in compact_requirement for term in SCOPE_GUARD_CHANGE_KEYWORDS):
        if not any(term in compact_case for term in SCOPE_GUARD_CHANGE_KEYWORDS):
            reasons.append("未覆盖本需求新增/变更的入口、模块或导入选项")

    for word in SCOPE_GUARD_UNREQUESTED_PATTERNS:
        if word in compact_case and word not in compact_requirement:
            reasons.append(f"包含需求未说明的扩展场景：{word}")
            if len(reasons) >= 3:
                break

    requirement_tokens = _scope_guard_tokens(trace_requirement_blob)
    case_tokens = set(_scope_guard_tokens(case_blob))
    strong_tokens = [
        token for token in requirement_tokens
        if len(token) >= 3 and token not in SCOPE_GUARD_GENERIC_TOKENS
    ][:12]
    compact_trace_requirement = re.sub(r"\s+", "", trace_requirement_blob)
    mapped_display_adaptation = bool(
        mapped_requirement_points
        and any(term in compact_case for term in GENERATED_DISPLAY_ASSERTION_TERMS + GENERATED_DISPLAY_ADAPTATION_TERMS)
        and any(term in compact_trace_requirement for term in GENERATED_DISPLAY_ASSERTION_TERMS + GENERATED_DISPLAY_ADAPTATION_TERMS)
    )
    if strong_tokens and case_tokens:
        hit_count = sum(1 for token in strong_tokens if token in case_tokens or token in compact_case)
        if hit_count == 0 and not reasons and not mapped_display_adaptation:
            reasons.append("用例标题/步骤无法追溯到当前需求关键词：" + "、".join(strong_tokens[:5]))

    return {
        "ok": not reasons,
        "reasons": reasons,
        "matchedRequirementIds": mapped_requirement_ids,
        "matchedRequirementPointCount": len(mapped_requirement_points),
        "rule": "生成用例必须能追溯到当前需求点；需求未提到的历史记录、缓存、超时、干扰等扩展场景不自动执行。",
    }


def apply_generated_case_scope_gate(payload: Any) -> dict:
    """Move off-scope generated automation cases out of the YAML execution pool."""
    normalized = normalize_cases_payload(payload)
    analysis = normalized.get("analysis") if isinstance(normalized.get("analysis"), dict) else {}
    kept: List[dict] = []
    moved: List[dict] = []
    for case in normalized.get("cases") or []:
        if not isinstance(case, dict):
            continue
        review = generated_case_requirement_scope_review(case, analysis)
        if review.get("ok"):
            kept.append(case)
            continue
        item = dict(case)
        item["executionLevel"] = item.get("executionLevel") or "needs_review"
        item["scopeReview"] = review
        item["reason"] = "当前需求范围审查未通过：" + "；".join(review.get("reasons") or [])
        item["suggested_setup"] = item.get("suggested_setup") or item.get("setup") or "人工确认是否属于本次需求范围；确认后可手动编辑 YAML 再执行"
        moved.append(item)

    if moved:
        normalized["cases"] = kept
        normalized["manual_cases"] = list(normalized.get("manual_cases") or []) + moved
        review = normalized.setdefault("review", {})
        review["scope_gate"] = {
            "enabled": True,
            "moved_to_manual_count": len(moved),
            "kept_automation_count": len(kept),
            "examples": [
                {
                    "title": item.get("title") or item.get("name") or "未命名用例",
                    "reasons": (item.get("scopeReview") or {}).get("reasons") or [],
                }
                for item in moved[:8]
            ],
            "rule": "需求范围不匹配的生成用例不再转换为自动化 YAML，避免无关历史/相邻场景进入 Runner。",
        }
    return normalized


YAML_VISUAL_REVIEW_TRACE_KEYS = (
    "yaml_visual_grounded",
    "yaml_visual_completed_batches",
    "yaml_visual_batches",
    "visual_refine_error",
    "visual_refine_errors",
    "visual_refine_skipped",
    "visual_grounder_error",
    "visual_grounder_skill",
    "visual_reference_note",
)
GENERATED_EXECUTION_LEVEL_ORDER = {
    "manual": 0,
    "draft": 1,
    "needs_review": 2,
    "executable": 3,
}
LOCAL_FALLBACK_SOURCE = "local_fallback_after_ai_timeout"


def snapshot_yaml_visual_review(payload: Any) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
    return {
        key: copy.deepcopy(review.get(key))
        for key in YAML_VISUAL_REVIEW_TRACE_KEYS
        if key in review
    }


def restore_yaml_visual_review(payload: Any, snapshot: dict) -> dict:
    payload = payload if isinstance(payload, dict) else {}
    if not isinstance(snapshot, dict) or not snapshot:
        return payload
    review = payload.setdefault("review", {})
    if not isinstance(review, dict):
        review = {}
        payload["review"] = review
    for key, value in snapshot.items():
        if key in YAML_VISUAL_REVIEW_TRACE_KEYS:
            review[key] = copy.deepcopy(value)
    return payload


def _generated_case_uses_local_fallback(case: dict) -> bool:
    case = case if isinstance(case, dict) else {}
    source = str(case.get("source") or "").strip()
    reason = str(case.get("automation_reason") or case.get("automationReason") or "").strip()
    return source == LOCAL_FALLBACK_SOURCE or "AI skill 超时后" in reason


def generated_payload_uses_local_fallback(payload: Any) -> bool:
    payload = payload if isinstance(payload, dict) else {}
    review = payload.get("review") if isinstance(payload.get("review"), dict) else {}
    if str(review.get("automation_filter_skill") or "").strip() == LOCAL_FALLBACK_SOURCE:
        return True
    return any(_generated_case_uses_local_fallback(case) for case in (payload.get("cases") or []))


def _stricter_generated_execution_level(current: str, floor: str) -> str:
    current = str(current or "").strip().lower()
    floor = str(floor or "needs_review").strip().lower()
    if current not in GENERATED_EXECUTION_LEVEL_ORDER:
        return floor
    if floor not in GENERATED_EXECUTION_LEVEL_ORDER:
        return current
    return min((current, floor), key=lambda level: GENERATED_EXECUTION_LEVEL_ORDER[level])


GENERATED_REQUIREMENT_MAPPED_DISPLAY_REASON = "明确映射到当前需求点的低风险可见 UI 文案/展示校验，可进入 Runner 由视觉 AI 判断"
GENERATED_DISPLAY_ASSERTION_TERMS = (
    "文案", "文字", "标题", "展示", "显示", "可见", "入口", "按钮", "标签", "位置", "顺序",
    "同级", "一致", "一致性", "布局", "页面", "控件", "tab", "Tab", "导航",
)
GENERATED_DISPLAY_ADAPTATION_TERMS = (
    "多端", "多设备", "设备形态", "形态适配", "适配", "宽屏", "手机", "移动端",
    "尺寸", "横向", "滚动", "截断",
)
GENERATED_NON_DISPLAY_EXPANSION_TERMS = (
    "断网", "弱网", "网络异常", "服务端异常", "缓存", "历史记录", "连续进出", "加载中点击",
    "过程中点击", "弹窗遮挡", "权限拒绝", "授权失败", "超时", "重试", "多次刷新",
    "横竖屏切换", "后台恢复", "杀进程", "异常恢复", "错误提示",
)


def _generated_case_requirement_mapped_display_check(source_case: dict, scope_review: dict, score: dict) -> bool:
    """Correct scorer downgrades for explicit requirement-mapped visible UI checks."""
    source_case = source_case if isinstance(source_case, dict) else {}
    scope_review = scope_review if isinstance(scope_review, dict) else {}
    score = score if isinstance(score, dict) else {}
    if not scope_review.get("ok", True):
        return False
    if _generated_case_uses_local_fallback(source_case):
        return False
    declared_level = str(source_case.get("executionLevel") or source_case.get("level") or "").strip().lower()
    if declared_level in {"needs_review", "draft", "manual"}:
        return False
    if not (
        safe_int(scope_review.get("matchedRequirementPointCount"), 0) > 0
        or scope_review.get("matchedRequirementIds")
        or re.search(r"\bREQ-\d+\b", _scope_guard_join_values(source_case.get("coverage"), source_case.get("requirement_point"), source_case.get("requirementPoint")), re.I)
    ):
        return False
    reasons = [str(item or "") for item in (score.get("reasons") or [])]
    if not any("缺少成功基线依据" in item and any(word in item for word in ("异常", "边界", "鲁棒")) for item in reasons):
        return False
    hard_block_reasons = (
        "静态校验", "解析失败", "固定坐标", "抽象模块名", "需求未说明", "高风险",
        "本地兜底", "禁止", "不存在的", "无法追溯",
    )
    if any(any(blocker in item for blocker in hard_block_reasons) for item in reasons):
        return False
    case_blob = _scope_guard_join_values(
        source_case.get("title"),
        source_case.get("name"),
        source_case.get("scenario"),
        source_case.get("goal"),
        source_case.get("coverage"),
        source_case.get("expected_result"),
        source_case.get("expectedResult"),
        source_case.get("steps"),
        source_case.get("assertions"),
        source_case.get("risk"),
        source_case.get("tags"),
    )
    compact_case = re.sub(r"\s+", "", case_blob)
    if any(term in compact_case for term in GENERATED_NON_DISPLAY_EXPANSION_TERMS):
        return False
    return any(term in compact_case for term in GENERATED_DISPLAY_ASSERTION_TERMS)


def enforce_generated_fallback_execution_floor(payload: Any, force: bool = False) -> dict:
    """Keep timeout fallbacks review-only even if later AI stages rewrite cases."""
    fallback_active = bool(force or generated_payload_uses_local_fallback(payload))
    if not fallback_active:
        return payload if isinstance(payload, dict) else normalize_cases_payload(payload)
    normalized = normalize_cases_payload(payload)
    for case in normalized.get("cases") or []:
        if not isinstance(case, dict):
            continue
        case["source"] = LOCAL_FALLBACK_SOURCE
        case["executionLevel"] = _stricter_generated_execution_level(
            case.get("executionLevel"),
            "needs_review",
        )
    review = normalized.setdefault("review", {})
    review["local_fallback_execution_floor"] = {
        "enabled": True,
        "executionLevel": "needs_review",
        "rule": "automation_filter 超时后的本地兜底只供评审，不自动下发 Runner。",
    }
    return normalized


def generated_yaml_effective_level(score_level: str, source_case: dict, scope_review: dict, score: dict = None) -> str:
    """Combine static score with stricter generation provenance and scope gates."""
    level = str(score_level or "draft").strip().lower()
    if level not in GENERATED_EXECUTION_LEVEL_ORDER:
        level = "draft"
    source_case = source_case if isinstance(source_case, dict) else {}
    if level == "needs_review" and _generated_case_requirement_mapped_display_check(source_case, scope_review, score or {}):
        level = "executable"
    declared_level = str(source_case.get("executionLevel") or source_case.get("level") or "").strip().lower()
    if _generated_case_uses_local_fallback(source_case):
        declared_level = _stricter_generated_execution_level(declared_level, "needs_review")
    if declared_level in GENERATED_EXECUTION_LEVEL_ORDER:
        level = _stricter_generated_execution_level(level, declared_level)
    if isinstance(scope_review, dict) and not scope_review.get("ok", True):
        level = _stricter_generated_execution_level(level, "needs_review")
    return level


YAML_REFERENCE_MAX_FILES = env_int("YAML_REFERENCE_MAX_FILES", 800)
YAML_REFERENCE_MAX_EXAMPLES = max(1, min(3, env_int("YAML_REFERENCE_MAX_EXAMPLES", 3)))
YAML_REFERENCE_MAX_SNIPPET_CHARS = max(800, min(1200, env_int("YAML_REFERENCE_MAX_SNIPPET_CHARS", 1200)))
YAML_GENERATED_ASSERTION_LIMIT = max(1, min(3, env_int("MIDSCENE_GENERATED_ASSERTION_LIMIT", 1)))
YAML_STATIC_REPAIR_ATTEMPTS = max(0, min(2, env_int("MIDSCENE_YAML_STATIC_REPAIR_ATTEMPTS", 1)))
YAML_STATIC_REPAIR_TIMEOUT_SECONDS = max(30, env_int("MIDSCENE_YAML_STATIC_REPAIR_TIMEOUT_SECONDS", 90))
YAML_REFERENCE_MEMORY_FILE = os.path.join(LEARNING_DIR, "yaml-reference-memory.json")
YAML_REFERENCE_STOPWORDS = {
    "测试", "验证", "页面", "功能", "需求", "用例", "执行", "当前", "进行", "是否", "可以", "需要",
    "点击", "进入", "打开", "显示", "相关", "流程", "按钮", "模块", "状态", "结果", "完成", "成功",
    "失败", "检查", "确认", "一个", "这个", "那个", "用户", "操作", "场景", "自动化", "生成",
}


def _yaml_reference_repo_root():
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _yaml_reference_roots():
    repo_root = _yaml_reference_repo_root()
    candidates = [
        TASK_DIR,
        os.path.join(repo_root, "server-tasks"),
        os.path.join(repo_root, "server-tasks-all"),
    ]
    roots = []
    seen = set()
    for root in candidates:
        path = os.path.abspath(str(root or ""))
        if not path or path in seen or not os.path.isdir(path):
            continue
        seen.add(path)
        roots.append(path)
    return roots


def _yaml_reference_terms(text, limit=120):
    text = str(text or "")
    terms = []
    seen = set()

    def add(term):
        term = str(term or "").strip().lower()
        if len(term) < 2 or term in YAML_REFERENCE_STOPWORDS or term in seen:
            return
        seen.add(term)
        terms.append(term)

    for match in re.finditer(r"[A-Za-z0-9_./-]+|[\u4e00-\u9fff]+", text):
        token = match.group(0).strip()
        if not token:
            continue
        if re.fullmatch(r"[A-Za-z0-9_./-]+", token):
            add(token)
            continue
        add(token)
        if len(token) >= 4:
            for size in (4, 3, 2):
                for idx in range(0, max(0, len(token) - size + 1)):
                    add(token[idx:idx + size])
                    if len(terms) >= limit:
                        return terms
    return terms[:limit]


def _iter_yaml_reference_files(max_files=None):
    max_files = safe_int(max_files, YAML_REFERENCE_MAX_FILES)
    count = 0
    for root in _yaml_reference_roots():
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                name for name in dirnames
                if not name.startswith(".") and name not in ("node_modules", "__pycache__")
            ]
            for filename in sorted(filenames):
                if not filename.lower().endswith((".yaml", ".yml")) or filename.startswith("."):
                    continue
                path = os.path.join(dirpath, filename)
                try:
                    rel = os.path.relpath(path, root).replace("\\", "/")
                except Exception:
                    rel = filename
                yield root, rel, path
                count += 1
                if count >= max_files:
                    return


def _yaml_reference_blocks(text):
    lines = (text or "").splitlines()
    starts = [idx for idx, line in enumerate(lines) if re.match(r"^\s*-\s+name:\s*.+", line)]
    if not starts:
        return []
    blocks = []
    for pos, start in enumerate(starts):
        end = starts[pos + 1] if pos + 1 < len(starts) else len(lines)
        block = "\n".join(lines[start:end]).strip()
        if block:
            blocks.append(block)
    return blocks


def _yaml_reference_title(block, fallback):
    match = re.search(r"^\s*-\s+name:\s*(.+?)\s*$", block or "", flags=re.M)
    return _clean_yaml_name(match.group(1)) if match else fallback


def _yaml_reference_flow_actions(block):
    actions = []
    for match in re.finditer(r"^\s*-\s*([A-Za-z][A-Za-z0-9_]*)\s*:", block or "", flags=re.M):
        action = match.group(1)
        if action not in MIDSCENE_FLOW_ACTIONS:
            continue
        if action not in actions:
            actions.append(action)
    return actions[:20]


def _yaml_reference_baseline_path(block):
    match = re.search(r"#\s*baseline\.path\s*:\s*(.+)", block or "")
    return match.group(1).strip() if match else ""


def _trim_yaml_reference_snippet(block, max_chars=None):
    max_chars = safe_int(max_chars, YAML_REFERENCE_MAX_SNIPPET_CHARS)
    kept = []
    for line in (block or "").splitlines():
        stripped = line.rstrip()
        if not stripped:
            continue
        if len(kept) > 48:
            break
        kept.append(stripped)
    text = "\n".join(kept).strip()
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n  # ... 已截断，仅保留关键步骤参考"
    return text


def _score_yaml_reference(query_terms, module, title, rel_path, block):
    haystack = "\n".join([rel_path, title, block[:5000]]).lower()
    title_l = str(title or "").lower()
    rel_l = str(rel_path or "").lower()
    score = 0
    matched = []
    for term in query_terms or []:
        if not term:
            continue
        hits = haystack.count(term)
        if hits:
            weight = 1 + min(5, len(term) // 2)
            if term in title_l:
                weight += 5
            if term in rel_l:
                weight += 3
            score += min(14, hits * weight)
            matched.append(term)
    module_text = str(module or "").strip().lower()
    if module_text and (module_text in rel_l or module_text in title_l):
        score += 10
        matched.append(module_text)
    actions = _yaml_reference_flow_actions(block)
    if actions:
        score += min(8, len(actions))
    if _yaml_reference_baseline_path(block):
        score += 4
    return score, matched[:12], actions


def collect_yaml_reference_examples(query_text, module="", limit=None):
    """Search cached YAML baseline tasks and return reusable step examples."""
    limit = safe_int(limit, YAML_REFERENCE_MAX_EXAMPLES)
    return search_baseline_examples(query_text, module=module, limit=max(1, limit))


def collect_yaml_baseline_library_examples(limit=None):
    """Collect a broad trusted baseline profile from the persisted cache."""
    limit = safe_int(limit, env_int("YAML_BASELINE_PROFILE_MAX_EXAMPLES", 120))
    cache = get_yaml_baseline_cache(force=False)
    rows = []
    for item in cache.get("items") or []:
        if not isinstance(item, dict):
            continue
        if item.get("baselineUsable") is not True or item.get("trusted") is not True:
            continue
        row = dict(item)
        row.setdefault("score", 0)
        row.setdefault("matched_terms", ["基线缓存"])
        rows.append(row)
        if len(rows) >= max(1, limit):
            break
    return rows


def build_yaml_reference_examples_text(examples):
    examples = [item for item in (examples or []) if isinstance(item, dict)]
    lines = [
        "【可信相似基线写法参考】",
        "你不是自由生成 YAML，而是按有明确来源的可信相似基线做仿写；优先采用真实执行成功样本，其次采用维护库样本。",
        "必须复用相似基线的动作顺序、等待方式、点击前置、点击后等待和断言方式；只能替换业务对象、按钮文案、输入内容和断言目标。",
        "禁止把检查/展示/存在/可见/状态类语义写成 aiTap；这类步骤必须写成 aiWaitFor、aiAssert 或基线中已有的轻量 ai。",
        "没有相似基线时，不要强行生成高风险长链路 YAML；应输出短链路可执行用例，外部授权/系统选择器/长流程放入需确认或人工用例。",
        "下面保留的是 YAML 原文片段，生成时按片段风格仿写，不要复制无关业务断言，不要把历史模块当成本次需求。",
        "",
    ]
    if not examples:
        lines.append("本次未命中可信相似基线；只允许生成短链路 YAML，复杂链路必须降级为需确认或人工用例。")
        return "\n".join(lines).strip()
    for idx, item in enumerate(examples[:3], start=1):
        matched = "、".join(item.get("matched_terms") or []) or "模块/步骤结构相近"
        actions = " -> ".join(item.get("actions") or []) or "-"
        lines.extend([
            f"### 参考样例 {idx}: {item.get('title') or item.get('file')}",
            f"- 来源: {item.get('provenancePath') or item.get('file')}",
            f"- 来源类型/验证: {item.get('sourceKind') or '-'} / {item.get('verificationStatus') or '-'}",
            f"- AI 对应业务分支: {item.get('ai_selected_branch_name') or '-'}",
            f"- AI 使用角色: {item.get('ai_selected_role') or '-'}",
            f"- 匹配: {matched}",
            f"- 动作类型: {actions}",
        ])
        if item.get("businessPath") or item.get("baseline_path"):
            lines.append(f"- 业务路径: {item.get('businessPath') or item.get('baseline_path')}")
        lines.extend(["```yaml", item.get("snippet") or "", "```", ""])
    return "\n".join(lines).strip()


def build_agent_business_plan_context_text(plan):
    """Render the upstream Agent business plan as guidance, not new requirements."""
    plan = plan if isinstance(plan, dict) else {}
    flows = [item for item in (plan.get("businessFlows") or []) if isinstance(item, dict)]
    if not flows:
        return ""
    lines = [
        "【Agent 上游业务计划】",
        "这是 AI 基于原始需求形成的业务分支与执行优先级，用于保持生成链路一致；原始需求仍是硬范围，Figma/截图仍是软参考。",
        "不得把计划中的假设或 unknowns 直接升级为硬断言；应由可信基线、设计资料或真机证据消解。",
    ]
    if plan.get("objective"):
        lines.append(f"- 验收目标: {plan.get('objective')}")
    for index, item in enumerate(flows[:8], start=1):
        lines.append(f"{index}. {item.get('name') or item.get('branch') or item.get('id') or '业务分支'}")
        steps = normalize_text_list(item.get("steps"))[:10]
        checks = normalize_text_list(item.get("checks"))[:8]
        if steps:
            lines.append("   - 页面路径: " + " -> ".join(steps))
        if checks:
            lines.append("   - 可见验收点: " + "；".join(checks))
    strategy = plan.get("executionStrategy") if isinstance(plan.get("executionStrategy"), dict) else {}
    if strategy:
        lines.append("- AI 冒烟/remaining 建议: " + json.dumps(strategy, ensure_ascii=False)[:1200])
    unknowns = normalize_text_list(plan.get("unknowns"))[:8]
    if unknowns:
        lines.append("- 待证据消解: " + "；".join(unknowns))
    return "\n".join(lines).strip()


def _baseline_branch_query_from_flow(item):
    """Render one AI-planned flow as a retrieval query without adding facts."""
    if not isinstance(item, dict):
        return ""
    parts = normalize_text_list([
        item.get("branch"),
        item.get("name"),
        item.get("steps"),
        item.get("checks"),
    ])
    return "\n".join(parts).strip()[:2000]


_BASELINE_BRANCH_HIERARCHY_RE = re.compile(r"\s*(?:->|=>|→|>|/|\\|\||｜|:|：|—|–|-)\s*")
_BASELINE_BRANCH_STRUCTURAL_SUFFIXES = (
    "入口可见性及布局校验",
    "入口可见性校验",
    "页面可见性校验",
    "可见性及布局校验",
    "可见性校验",
    "布局校验",
    "业务分支",
    "业务流程",
    "功能入口",
    "页面入口",
    "入口",
    "页面",
    "流程",
    "分支",
)


def _baseline_branch_leaf(value):
    raw = str(value or "").strip()
    parts = [item.strip() for item in _BASELINE_BRANCH_HIERARCHY_RE.split(raw) if item.strip()]
    leaf = parts[-1] if parts else raw
    changed = True
    while changed and leaf:
        changed = False
        for suffix in _BASELINE_BRANCH_STRUCTURAL_SUFFIXES:
            if leaf.endswith(suffix) and len(leaf) - len(suffix) >= 2:
                leaf = leaf[:-len(suffix)].strip()
                changed = True
                break
    return leaf


def baseline_branch_anchor_terms(branch_name, sibling_names=None):
    """Derive branch-specific evidence anchors from an AI-authored hierarchy label."""
    leaf = _baseline_branch_leaf(branch_name)
    normalized = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", leaf).lower()
    if len(normalized) < 2:
        return []
    sibling_values = {
        re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "", _baseline_branch_leaf(item)).lower()
        for item in (sibling_names or [])
        if str(item or "").strip() and str(item or "").strip() != str(branch_name or "").strip()
    }
    anchors = [normalized]
    tokens = re.findall(r"[a-zA-Z0-9]+|[\u4e00-\u9fff]+", leaf)
    if tokens:
        first = tokens[0].lower()
        if re.fullmatch(r"[\u4e00-\u9fff]+", first):
            first = first[:2] if len(first) > 2 else first
        if len(first) >= 2 and not any(first in sibling for sibling in sibling_values):
            anchors.append(first)
    return list(dict.fromkeys(item for item in anchors if len(item) >= 2))[:4]


def baseline_branch_queries_from_agent_plan(plan, limit=8):
    """Build narrow retrieval queries from AI-planned branches without adding facts."""
    plan = plan if isinstance(plan, dict) else {}
    queries = []
    seen = set()
    for item in plan.get("businessFlows") or []:
        text = _baseline_branch_query_from_flow(item)
        key = re.sub(r"\s+", " ", text).lower()
        if len(key) < 2 or key in seen:
            continue
        seen.add(key)
        queries.append(text[:2000])
        if len(queries) >= max(1, safe_int(limit, 8)):
            break
    return queries


def baseline_required_branches_from_agent_plan(plan, limit=3):
    """Use the AI-selected smoke flows as the branch coverage target for Top3 reranking."""
    plan = plan if isinstance(plan, dict) else {}
    flows = [item for item in (plan.get("businessFlows") or []) if isinstance(item, dict)]
    flow_by_id = {
        str(item.get("id") or "").strip(): item
        for item in flows
        if str(item.get("id") or "").strip()
    }
    strategy = plan.get("executionStrategy") if isinstance(plan.get("executionStrategy"), dict) else {}
    smoke_ids = normalize_text_list(strategy.get("smokeFlowIds") or strategy.get("smoke_flow_ids"))
    ordered = [flow_by_id[item] for item in smoke_ids if item in flow_by_id]
    ordered.extend(item for item in flows if item not in ordered)
    sibling_names = [
        str(item.get("branch") or item.get("name") or "").strip()
        for item in ordered
        if str(item.get("branch") or item.get("name") or "").strip()
    ]
    required = []
    seen_branches = set()
    for index, item in enumerate(ordered):
        query = _baseline_branch_query_from_flow(item)
        branch_name = str(item.get("branch") or item.get("name") or "").strip()
        branch_key = re.sub(r"\s+", " ", branch_name).lower()
        if not query or len(branch_key) < 2 or branch_key in seen_branches:
            continue
        seen_branches.add(branch_key)
        required.append({
            "id": str(item.get("id") or f"FLOW-{index + 1:03d}").strip(),
            "name": branch_name,
            "query": query,
            "anchors": baseline_branch_anchor_terms(branch_name, sibling_names),
            "source": "agent_smoke_flow" if str(item.get("id") or "").strip() in smoke_ids else "agent_business_flow",
        })
        if len(required) >= max(1, min(3, safe_int(limit, 3))):
            break
    return required


def build_ai_generation_decision_context_text(selected_baselines, scope_plan, case_plan=None):
    """Render AI decision outputs back into the generation prompt."""
    selected_baselines = [item for item in (selected_baselines or []) if isinstance(item, dict)]
    scope_plan = scope_plan if isinstance(scope_plan, dict) else {}
    case_plan = case_plan if isinstance(case_plan, dict) else {}
    lines = [
        "【AI 生成决策计划】",
        "下面是 AI 在生成前做出的基线选择、生成范围和执行计划。生成 YAML 时必须优先遵守该计划；不要绕开计划自由扩展长链路。",
    ]
    if scope_plan:
        lines.extend([
            f"- 需求规模: {scope_plan.get('size') or '-'}",
            f"- 自动化目标数: {scope_plan.get('targetCaseCount') or scope_plan.get('target_case_count') or '-'}",
            f"- 首批冒烟数: {scope_plan.get('smokeCount') or scope_plan.get('smoke_count') or '-'}",
            f"- 继续执行阈值: {scope_plan.get('continueThreshold') or 0.5}",
            f"- 规划原因: {scope_plan.get('reason') or '-'}",
        ])
        flow = scope_plan.get("businessFlow") or scope_plan.get("business_flow") or []
        if flow:
            lines.append("- 业务主链: " + " -> ".join(str(item) for item in flow if str(item or "").strip()))
    if selected_baselines:
        lines.append("【AI 选中的相似基线】")
        for index, item in enumerate(selected_baselines[:3], start=1):
            lines.append(
                f"{index}. {item.get('title') or item.get('file') or '-'}"
                f"；分支: {item.get('ai_selected_branch_name') or '-'}"
                f"；角色: {item.get('ai_selected_role') or '-'}"
                f"；来源: {item.get('provenancePath') or item.get('file') or '-'}"
                f"；动作: {' -> '.join(item.get('actions') or []) or '-'}"
                f"；原因: {item.get('ai_selected_reason') or item.get('selection_reason') or item.get('matched_terms') or '-'}"
            )
    plan_cases = [item for item in (case_plan.get("cases") or []) if isinstance(item, dict)]
    if plan_cases:
        lines.append("【AI 用例执行计划】")
        for index, item in enumerate(plan_cases[:8], start=1):
            lines.extend([
                f"{index}. {item.get('title') or item.get('case_id') or '-'}",
                f"   - 批次: {item.get('batch') or '-'}；优先级: {item.get('priority') or '-'}；基线: {item.get('baselineId') or '-'}",
                f"   - 前置: {item.get('precondition') or '-'}",
                f"   - 断言目标: {item.get('assertionTarget') or '-'}",
                f"   - 可执行理由: {item.get('executableReason') or '-'}",
            ])
    lines.append("硬约束：没有相似基线、没有明确前置页、没有可见断言目标的用例，不要进入首批冒烟；复杂外部授权/文件选择/长等待只进入扩展或需确认。")
    return "\n".join(lines).strip()


def build_executable_smoke_yaml_policy_text():
    """Explicit generation policy for Runner-first executable YAML."""
    return "\n".join([
        "【Runner YAML 可执行优先规则】",
        "1. 自动化 YAML 的目标是生成一组可分批全量执行的稳定用例，不是只生成冒烟；冒烟只是首批准入，用来证明 YAML 能下发、能运行、能产生日志。",
        "2. 完整覆盖要拆成多个短 YAML 文件：每个文件只覆盖一个清晰业务检查点，并自己包含启动、到达入口、核心动作、终态等待/判断和清理。",
        "2.1 首批冒烟 YAML 必须短链路：单条建议不超过 10~12 个动作、5 个等待、2~3 次页面交互；如果没有成功基线依据，不要把外部授权、文件选择、长列表滑动和多页面回跳串到同一条。",
        "2.2 新增入口类需求中，普通业务入口可以拆成“目标页面入口可见”和“点击入口后页面有反馈”两个短用例；第三方授权页、文件选择页、未安装 App、权限拒绝、历史记录干扰等放入扩展或需确认，不进入首批冒烟。",
        "2.3 百度网盘/微信/相册/相机等第三方入口类需求必须拆开：首批冒烟只验证入口在正确业务页面可见，例如“文档打印首页百度网盘入口可见”；点击第三方入口后的授权、登录、文件选择、WebView 或外部 SDK 流程必须单独生成扩展用例，不要命名为“展示与点击验证”。",
        "2.4 如果需求是“新增百度网盘入口”，不要从历史记录、备份页、失败页或无关模块扩展用例；只围绕需求文档和 Figma 给出的页面生成入口可见、入口点击反馈和异常授权类用例。",
        "3. Figma 只作为 UI 参考，实际 App 可能有文案、顺序和样式差异；不要照抄 Figma 上的长文案、尺寸、坐标或全部元素。",
        "4. 只有真实点击目标才能用 aiTap；检查/验证/是否展示/是否存在/页面可见/状态一致这类语义必须用 aiWaitFor 或按基线习惯保留为轻量 ai，不要为了补断言强行生成 aiAssert。",
        "4.1 用例标题里的“展示/检查/验证/可见性”不能直接变成 aiTap；如果只是检查入口是否存在，写 aiWaitFor；只有明确按钮/入口本身才写 aiTap。",
        "4.2 点击百度网盘、微信、相册、相机等第三方/系统入口后，后续等待和断言必须面向跳转后的授权页、文件选择页、空状态页或提示页，不能继续等待原业务页的入口展示。",
        "4.3 禁止把“如果当前不在首页则点击首页”“找到并点击”“向右翻看并查找”写成一个 aiTap/ai 动作；必须拆成稳定等待、必要滑动/点击、跳转后等待。",
        "5. 遇到相册、拍照、微信、外部跳转、搜索、列表、弹窗、登录、上传、模型生成等动作时，必须优先学习【现有 YAML 步骤经验库】里的稳定写法。",
        "6. 不确定页面入口时，先写稳定到达路径和等待条件，不要生成必须精确命中特定视觉细节才可通过的脚本。",
        "7. 智小白 3D AI建模链路必须按当前真机入口写：底部中间 Tab/首页卡片进入 AI建模；不要在首页三维创作区查找旧的“文字输入”；标牌/印章入口需要先横向滑动功能入口区域。",
        "8. 动态推荐、骨架屏、缩放控件、固定推荐标题、旧入口缺失验证不能作为自动化必过断言；没有稳定真机信号时转入 manual_cases/summary。",
        "9. 不要生成固定坐标的最近任务清理，例如 input swipe 540 1900 540 350；如需脱离外部页面，必须使用平台启动守卫注入的 wm size 动态坐标方案，且 shell 内不要使用 ${var%...}/${var#...} 这类会被 Midscene 误当环境变量插值的写法。",
    ]).strip()


def review_generated_yaml_smoke_stability(yaml_text):
    """Review generated YAML for smoke-execution stability without blocking valid YAML."""
    check = validate_midscene_yaml_executability(yaml_text)
    review = {
        "ok": bool(check.get("ok")),
        "platform": check.get("platform") or "",
        "taskCount": int(check.get("taskCount") or 0),
        "issues": list(check.get("issues") or []),
        "warnings": [],
        "assertCount": 0,
        "waitForCount": 0,
        "launchGuard": False,
        "cleanupGuard": False,
        "taskReviews": [],
        "rule": "Runner 冒烟可执行优先：单文件单业务检查点，少断言，复用现有 YAML 稳定步骤。",
    }
    if _pyyaml is None or not str(yaml_text or "").strip():
        if _pyyaml is None:
            review["warnings"].append("服务端未安装 PyYAML，无法做稳定性细查")
        return review
    try:
        parsed = _pyyaml.safe_load(str(yaml_text or ""))
    except Exception as exc:
        review["warnings"].append(f"YAML 稳定性审查解析失败：{exc}")
        return review
    _platform, tasks = extract_midscene_tasks(parsed)
    for index, task in enumerate(tasks or [], start=1):
        if not isinstance(task, dict):
            continue
        flow = task.get("flow") if isinstance(task.get("flow"), list) else []
        task_asserts = 0
        task_waits = 0
        long_asserts = []
        has_launch = False
        has_cleanup = False
        action_count = 0
        for item in flow:
            if not isinstance(item, dict):
                continue
            action_count += len([key for key in item if key in MIDSCENE_FLOW_ACTIONS])
            if "aiAssert" in item:
                task_asserts += 1
                text = str(item.get("aiAssert") or "")
                if len(text) > 180:
                    long_asserts.append(text[:80])
            if "aiWaitFor" in item:
                task_waits += 1
            if "launch" in item:
                has_launch = True
            shell = str(item.get("runAdbShell") or "")
            if "force-stop" in shell:
                has_cleanup = True
        review["assertCount"] += task_asserts
        review["waitForCount"] += task_waits
        review["launchGuard"] = review["launchGuard"] or has_launch
        review["cleanupGuard"] = review["cleanupGuard"] or has_cleanup
        item_review = {
            "name": task.get("name") or f"tasks[{index}]",
            "assertCount": task_asserts,
            "waitForCount": task_waits,
            "actionCount": action_count,
            "launchGuard": has_launch,
            "cleanupGuard": has_cleanup,
            "warnings": [],
        }
        if task_asserts > YAML_GENERATED_ASSERTION_LIMIT:
            item_review["warnings"].append(f"aiAssert 数量 {task_asserts} 超过平台默认 {YAML_GENERATED_ASSERTION_LIMIT} 个，建议保留最终业务断言")
        if long_asserts:
            item_review["warnings"].append("存在过长 aiAssert，可能把需求/Figma 文案直接塞进断言")
        if not has_launch:
            item_review["warnings"].append("缺少 launch 启动保护，单独执行时可能依赖上一个页面状态")
        if task_waits == 0:
            item_review["warnings"].append("缺少 aiWaitFor 等待，页面加载慢时容易失败")
        review["warnings"].extend(item_review["warnings"])
        review["taskReviews"].append(item_review)
    review["stable"] = bool(review["ok"] and not review["warnings"])
    return review


def record_yaml_reference_examples(case_set_id, title, module, examples):
    examples = [item for item in (examples or []) if isinstance(item, dict)]
    if not examples:
        return ""
    records = read_json_file(YAML_REFERENCE_MEMORY_FILE, default=[])
    if not isinstance(records, list):
        records = []
    records.append({
        "case_set_id": case_set_id,
        "title": title,
        "module": module,
        "used_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "examples": [
            {
                "id": item.get("id") or item.get("case_id") or "",
                "title": item.get("title"),
                "module": item.get("module"),
                "file": item.get("file"),
                "score": item.get("score"),
                "matched_terms": item.get("matched_terms") or [],
                "actions": item.get("actions") or [],
                "hash": item.get("hash"),
                "provenancePath": item.get("provenancePath") or item.get("file") or "",
                "sourceKind": item.get("sourceKind") or "",
                "verificationStatus": item.get("verificationStatus") or "",
                "businessPath": item.get("businessPath") or item.get("baseline_path") or "",
                "aiSelectedRole": item.get("ai_selected_role") or "",
            }
            for item in examples[:YAML_REFERENCE_MAX_EXAMPLES]
        ],
    })
    os.makedirs(os.path.dirname(YAML_REFERENCE_MEMORY_FILE), exist_ok=True)
    write_json_file(YAML_REFERENCE_MEMORY_FILE, records[-200:])
    return YAML_REFERENCE_MEMORY_FILE


def case_priority(case):
    """提取用例优先级。"""
    raw = str(case_value(case, "priority", "level", "severity") or "P2").strip().upper()
    aliases = {
        "0": "P0", "S0": "P0", "CRITICAL": "P0", "BLOCKER": "P0", "最高": "P0", "阻断": "P0",
        "1": "P1", "S1": "P1", "HIGH": "P1", "IMPORTANT": "P1", "高": "P1", "重要": "P1",
        "2": "P2", "S2": "P2", "MEDIUM": "P2", "NORMAL": "P2", "中": "P2", "普通": "P2",
        "3": "P3", "S3": "P3", "LOW": "P3", "低": "P3",
    }
    return raw if raw in ("P0", "P1", "P2", "P3") else aliases.get(raw, "P2")


def case_tags(case):
    """提取用例标签。"""
    return normalize_text_list(case.get("tags") or case.get("labels") or [])


def is_smoke_case(case):
    """判断是否为冒烟用例。

    新生成用例的首批自动执行必须来自 AI 明确标记或用户显式标记。
    不再根据 P0/P1、标题关键词、入口/主流程等规则自动推断，避免把
    历史记录、干扰、旧入口、弱路径误当成冒烟。
    """
    explicit = case_value(case, "smoke", "is_smoke", "isSmoke", "smoke_test", "smokeTest", "flag")
    tags = case_tags(case)
    flags = normalize_text_list(case.get("flag") or case.get("flags") or [])
    if explicit not in ("", None):
        return truthy_text(explicit) or "冒烟" in str(explicit) or "smoke" in str(explicit).lower()
    if any("冒烟" in tag or "smoke" in tag.lower() for tag in tags):
        return True
    if any("冒烟" in flag or "smoke" in flag.lower() for flag in flags):
        return True
    return False


# ---------------------------------------------------------------------------
# 动作/输入解析工具
# ---------------------------------------------------------------------------

PASSIVE_CHECK_PREFIXES = ("检查", "验证", "确认", "查看", "观察", "等待", "直到出现", "直到看到", "直到", "等到")
PASSIVE_CHECK_WORDS = (
    "是否", "可见", "存在", "展示", "显示", "出现", "加载完成", "加载完",
    "文案", "布局", "位置", "同级", "顺序", "状态", "入口可见", "按钮可见",
)
EXPLICIT_TAP_WORDS = (
    "点击", "点按", "轻触", "长按", "勾选", "取消勾选", "选择", "打开",
    "进入", "切换", "返回", "关闭", "提交", "确认打印", "下一步", "上一步",
    "开始", "重试", "刷新", "搜索", "保存", "下载",
)
TRANSITION_TAP_WORDS = (
    "进入", "打开", "跳转", "返回", "提交", "确认", "下一步", "上一步",
    "开始", "重试", "刷新", "搜索", "保存", "下载",
)


def step_looks_passive_check(text):
    """判断自然语言步骤是否是页面状态检查，而不是点击目标。"""
    raw = str(text or "").strip()
    compact = re.sub(r"\s+", "", raw)
    if not compact:
        return False
    explicit_tap = any(word in compact for word in EXPLICIT_TAP_WORDS)
    passive_signal = any(word in compact for word in PASSIVE_CHECK_WORDS)
    if compact.startswith(PASSIVE_CHECK_PREFIXES):
        return passive_signal or not explicit_tap or compact.startswith(("等待", "直到", "等到"))
    if passive_signal:
        return not explicit_tap
    return False


def action_type(text):
    """根据文本判断动作类型。"""
    text = str(text or "")
    stripped = text.strip()
    if stripped.startswith(("等待", "直到出现", "直到看到", "直到", "等到")):
        return "aiWaitFor"
    if step_looks_passive_check(text):
        return "aiWaitFor"
    if any(key in text for key in EXPLICIT_TAP_WORDS):
        return "aiTap"
    if any(key in text for key in ("等待", "直到出现", "直到看到", "加载完成", "加载完", "出现", "展示")):
        return "aiWaitFor"
    return "ai"


def parse_input_value(text):
    """从自然语言步骤中提取输入值。"""
    text = str(text or "").strip()
    if is_input_visibility_or_scroll_instruction(text):
        return ""
    patterns = [
        r".{0,40}?输入框\s*输入[^：:]*[：:]\s*(.+)$",
        r"(?:输入|搜索)\s*[\"\"']([^\"\"']+)[\"\"']",
        r"^输入\s*[：:]\s*(.+)$",
        r"^搜索\s*[：:]\s*(.+)$",
        r"^输入\s+(.+)$",
        r"^搜索\s+(.+)$",
        r"在.+?输入\s*[：:]?\s*(.+)$",
        r"在.+?搜索\s*[：:]?\s*(.+)$",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            value = m.group(1).strip()
            value = value.strip(" \t\r\n\"'""''")
            if value:
                return value
    return ""


def is_input_visibility_or_scroll_instruction(text):
    """判断是否为可见性/滚动指令而非真正输入。"""
    text = str(text or "")
    if not any(word in text for word in ("上滑", "下滑", "滑动", "滚动", "直到出现", "直到看到", "能看到", "看到")):
        return False
    if "输入框" not in text:
        return False
    explicit_input_markers = ("输入：", "输入:", "输入姓名：", "输入姓名:", "输入内容：", "输入内容:")
    return not any(marker in text for marker in explicit_input_markers)


def input_target_from_text(text):
    """从自然语言步骤推断输入框定位目标。"""
    text = str(text or "")
    if "姓名输入框" in text or "姓名" in text and "输入框" in text:
        return "姓名输入框"
    if "搜索" in text:
        return "当前页面的搜索输入框或文本输入框"
    return "当前页面的文本输入框"


def input_action_requires_search_entry(text):
    """判断输入动作是否需要先点击搜索入口。"""
    text = str(text or "")
    if not parse_input_value(text):
        return False
    search_entry_words = (
        "放大镜", "搜索图标", "搜索入口", "搜索按钮", "点击搜索", "右上角搜索", "顶部搜索"
    )
    return any(word in text for word in search_entry_words)


def search_entry_target_from_text(text):
    """推断搜索入口定位目标。"""
    text = str(text or "")
    if "右上角" in text:
        return "右上角放大镜搜索图标或搜索入口"
    if "顶部" in text:
        return "顶部搜索图标、搜索框或搜索入口"
    return "搜索图标、搜索框或搜索入口"


def adb_input_text(value):
    """转义 ADB input text 值。"""
    value = str(value or "")
    value = value.replace("\\", "\\\\").replace(" ", "%s")
    value = value.replace('"', '\\"').replace("'", "\\'")
    return value


def is_safe_adb_text(value):
    """判断值是否为 ADB 安全文本。"""
    return bool(re.match(r"^[A-Za-z0-9._@%+-]+$", str(value or "")))


def is_external_file_picker_context(text):
    """判断上下文是否为外部文件选择器。"""
    text = str(text or "")
    keywords = (
        "本地导入", "文件导入", "文件选择", "选择文件", "文件管理", "文件管理器",
        "系统文件", "文档", "相册", "图片选择", "本地文件", "导入文件",
        "file picker", "documentsui", "media provider"
    )
    return any(keyword.lower() in text.lower() for keyword in keywords)


def normalize_input_locate_for_context(locate, context_text=""):
    """根据上下文规范化输入框定位。"""
    locate = strip_yaml_quotes(locate or "")
    generic_locates = {
        "当前页面的搜索输入框或文本输入框",
        "当前页面的文本输入框",
        "当前页面的输入框",
        "搜索输入框",
        "文本输入框",
        "输入框",
    }
    if is_external_file_picker_context(context_text) and (not locate or locate in generic_locates):
        return "文件选择器顶部搜索输入框"
    return locate


def evidence_needs_adb_input_fallback(evidence_text=""):
    """判断日志是否需要 ADB 输入兜底。"""
    text = str(evidence_text or "").lower()
    keywords = (
        "未输入", "没输入", "没有输入", "输入失败", "输入框为空", "搜索框为空",
        "输入动作失败", "无法输入", "不会输入", "没看到输入", "光标已经定位",
        "text not entered", "input is empty", "empty input", "failed to input",
    )
    return any(keyword.lower() in text for keyword in keywords)


def should_add_adb_input_fallback(value, context_text, evidence_text=""):
    """判断是否需要添加 ADB 输入兜底。"""
    return (
        is_safe_adb_text(value)
        and is_external_file_picker_context(context_text)
        and evidence_needs_adb_input_fallback(evidence_text)
    )


def runtime_guard_mode():
    """返回运行时守卫模式。"""
    return RUNTIME_GUARD_MODE if RUNTIME_GUARD_MODE in ("minimal", "balanced", "strict") else "minimal"


def evidence_needs_popup_guard(evidence_text=""):
    """判断日志是否需要弹窗兜底。"""
    text = evidence_text or ""
    keywords = (
        "弹窗", "浮层", "遮挡", "权限", "升级", "广告", "活动", "引导",
        "modal", "dialog", "popup", "permission", "overlay"
    )
    return any(word.lower() in text.lower() for word in keywords)


def extract_app_package_from_yaml(yaml_text):
    """从 YAML 文本中提取 app package。"""
    packages = []
    for line in (yaml_text or "").splitlines():
        m = re.match(r"^\s*-\s+(?:launch|terminate)\s*:\s*[\"']?([^\"'\s#]+)", line)
        if m:
            pkg = m.group(1).strip()
            if pkg and pkg not in packages:
                packages.append(pkg)
    for pkg in packages:
        if "." in pkg:
            return pkg
    return packages[0] if packages else ""


# ---------------------------------------------------------------------------
# 用例 payload 规范化
# ---------------------------------------------------------------------------

def normalize_cases_payload(value: Any) -> dict:
    """规范化用例 JSON 负载。"""
    if isinstance(value, str):
        value = json.loads(value)
    if isinstance(value, list):
        return {"title": "测试用例", "cases": value}
    if not isinstance(value, dict):
        raise ValueError("JSON 根节点必须是对象或数组")

    cases = value.get("cases") or value.get("testCases") or value.get("items")
    if cases is None and all(key in value for key in ("title", "steps")):
        cases = [value]
    if not isinstance(cases, list) or not cases:
        raise ValueError("JSON 中必须包含非空 cases 数组")
    scenarios = value.get("scenarios") or []
    if not isinstance(scenarios, list):
        scenarios = []
    manual_cases = value.get("manual_cases") or value.get("manualCases") or []
    if not isinstance(manual_cases, list):
        manual_cases = []
    analysis = value.get("analysis") or {}
    if not isinstance(analysis, dict):
        analysis = {}
    review = value.get("review") or {}
    if not isinstance(review, dict):
        review = {
            "normalization_warning": "模型返回的 review 不是对象，已自动重置为空对象",
            "raw_review_type": type(value.get("review")).__name__,
        }

    return {
        "title": value.get("title") or value.get("name") or "测试用例",
        "module": value.get("module") or "AI测试",
        "analysis": analysis,
        "scenarios": scenarios,
        "cases": cases,
        "manual_cases": manual_cases,
        "review": review,
    }



# ---------------------------------------------------------------------------
# 覆盖度审计支撑函数（迁移自 midscene-upload.py）
# ---------------------------------------------------------------------------

def _normalize_text_list(value: Any) -> List[str]:
    """内部规范化文本列表（兼容旧调用）。"""
    return normalize_text_list(value)


def _case_has_meaningful_assertion(case: dict) -> bool:
    assertions = normalize_text_list(
        case.get("assertions") or case.get("expects") or case.get("expected")
    )
    expected = first_non_empty(
        case_value(case, "expected_result", "expectedResult", "expected", "expectation")
    )
    texts = assertions + ([expected] if expected else [])
    if not texts:
        return False
    vague = (
        "页面正常展示", "结果符合预期", "操作成功", "功能正常",
        "页面无异常", "进入相关页面",
    )
    return any(text and not any(item in text for item in vague) for text in texts)


def _case_execution_text(case: dict) -> str:
    if not isinstance(case, dict):
        return str(case or "")
    values = [
        case.get("title"),
        case.get("name"),
        case.get("scenario"),
        case.get("goal"),
        case.get("start_page") or case.get("startPage"),
        case.get("business_path") or case.get("businessPath") or case.get("path"),
        case.get("expected_result") or case.get("expectedResult") or case.get("expected"),
        case.get("data_requirements") or case.get("dataRequirements") or case.get("test_data") or case.get("testData"),
        case.get("automation_reason") or case.get("automationReason"),
        " ".join(normalize_text_list(case.get("preconditions") or case.get("precondition"))),
        " ".join(normalize_text_list(case.get("steps") or [])),
        " ".join(normalize_text_list(case.get("assertions") or case.get("expects") or case.get("expected"))),
        " ".join(normalize_text_list(case.get("tags") or [])),
    ]
    return " ".join(str(value or "") for value in values)


_EXTERNAL_DEEP_ACTION_TERMS = (
    "输入账号", "输入密码", "输入验证码", "确认授权", "同意授权", "点击允许",
    "选择文件", "选择照片", "下载文件", "上传文件", "删除文件",
)


def _case_has_deep_external_action(case: dict) -> bool:
    context = _case_execution_text(case).lower()
    if not any(term in context for term in ("授权", "第三方", "网盘", "外部", "h5")):
        return False
    for step in normalize_text_list((case or {}).get("steps")):
        text = str(step or "").strip()
        if text.startswith(("等待", "观察", "检查", "验证")):
            continue
        if any(term in text for term in _EXTERNAL_DEEP_ACTION_TERMS):
            return True
    return False


def _case_is_bounded_external_landing_check(case: dict) -> bool:
    """Allow a grounded click that stops at the first observable external state."""
    case = case if isinstance(case, dict) else {}
    case_plan = case.get("ai_case_plan") if isinstance(case.get("ai_case_plan"), dict) else {}
    if not (case_plan.get("baselineGrounded") and case_plan.get("pathPlanApplied")):
        return False
    if not normalize_text_list(case.get("requirementRefs") or case.get("requirement_refs")):
        return False
    steps = normalize_text_list(case.get("steps"))
    if len(steps) < 2:
        return False
    click_indexes = [
        index for index, step in enumerate(steps)
        if "点击" in step and any(term in step for term in ("入口", "按钮", "网盘", "第三方"))
    ]
    if not click_indexes:
        return False
    tail = steps[click_indexes[-1] + 1:]
    if not tail or any(not str(step).strip().startswith(("等待", "观察", "检查", "验证")) for step in tail):
        return False
    if _case_has_deep_external_action(case):
        return False
    outcome_text = " ".join(normalize_text_list(
        case.get("assertions") or case.get("expected_result") or case.get("expected")
    ))
    if not (
        any(term in outcome_text for term in ("任一", "之一", "任意", "或"))
        or re.search(r"[/／、]", outcome_text)
    ):
        return False
    observable_state_groups = (
        ("授权",),
        ("登录",),
        ("H5", "h5", "WebView", "webview", "网页", "Web页", "浏览器"),
        ("文件选择", "文件页", "文件列表", "内容列表", "选择页", "网盘文件"),
        ("空态", "提示页"),
        ("系统弹窗", "弹窗", "弹出层"),
    )
    if sum(1 for terms in observable_state_groups if any(term in outcome_text for term in terms)) < 2:
        return False
    return any(term in outcome_text for term in (
        "无白屏", "不白屏", "未白屏", "无长时间白屏", "没有长时间白屏",
        "无崩溃", "不崩溃", "未崩溃", "无Crash", "未Crash", "不闪退",
    ))


def _case_manual_block_reason(case: dict) -> str:
    """判断用例是否不适合直接转成 Runner YAML。

    这里是 AI 筛选后的确定性二次闸门：完整测试设计仍保留在
    manual_cases / 脑图里，但需要 Mock、造数、系统状态或纯设计稿对比的
    场景不能直接下发 Runner，避免空闲设备执行一批必失败脚本。
    """
    text = _case_execution_text(case)
    compact = re.sub(r"\s+", "", text).lower()
    bounded_external_landing = _case_is_bounded_external_landing_check(case)
    def has_any(*terms: str) -> bool:
        return any(str(term).lower() in compact for term in terms)

    if has_any("我的作品") and has_any("历史", "空态", "分页", "滑动加载", "没有更多", "暂无作品"):
        return "依赖账号作品数据、空态或分页状态，当前 Runner 不能保证测试数据一致"
    if has_any("无结果", "空结果", "未找到相关模型", "兜底提示", "降级走搜索引擎", "冷门测试词", "无意义字符"):
        return "依赖搜索/推荐算法返回特定空结果或兜底状态，需要造数或 Mock 后再自动化"
    if _case_has_deep_external_action(case):
        return "包含第三方深层授权、凭据或文件操作，只允许人工准备和验证"
    if has_any("权限弹窗", "权限申请", "授权弹窗", "麦克风权限", "相册权限") and not bounded_external_landing:
        return "依赖系统权限弹窗是否首次出现，Runner 当前环境无法保证可复现"
    if has_any("四维评估", "匹配度", "评估结果", "评估明细", "确认设计图页"):
        return "依赖模型评估结果或生成链路中间态，耗时和结果不稳定，建议作为人工/待准备用例"
    if has_any("生成中", "加载动画", "进度提示", "中途返回", "中断处理", "防重复点击", "重复点击"):
        return "依赖异步生成过程或瞬时 UI 状态，直接 Runner 执行容易超时或误判"
    if has_any("旧版") and has_any("清理", "移除", "删除", "不出现", "不存在"):
        return "属于改版后旧入口缺失校验，需先确认 App 版本和页面状态，再转自动化"
    if has_any("排序", "排列顺序") and has_any("首页", "模块", "入口", "卡片"):
        return "属于 UI 顺序/视觉验收，真机数据与 Figma 容易不一致，默认转人工复核"
    is_xiaobai_ai_model = has_any("ai建模", "图片建模", "语音创作", "文字建模", "标牌", "趣味印章", "印章", "com.kfb.model", "智小白3d")
    if is_xiaobai_ai_model:
        if has_any("三种入口", "多入口") and has_any("开始创作", "跳转", "验证"):
            return "当前 App 入口已改版，不能把历史多入口作为自动化必经路径；请按当前底部 AI建模 Tab/首页卡片重写"
        if has_any("文字输入") and has_any("首页", "三维创作", "开始创作", "入口"):
            return "当前首页三维创作区没有旧版文字输入入口，直接执行会定位失败"
        if (
            has_any("大家都在做", "骨架屏", "缩放控件", "固定推荐", "推荐标题", "素材标题")
            and not has_any("或", "任一", "任意", "之一", "任意一个")
        ):
            return "依赖动态推荐、加载瞬态或历史设计稿控件，当前真机不保证出现，不能直接下发 Runner"
        if has_any("欢迎态") and has_any("我是小魔法师"):
            return "欢迎态文案已随版本变化，不能用历史固定文案作为自动化断言"
        if has_any("没有喜欢的", "四维补充", "不补充直接生成"):
            return "AI建模推荐/补充分支是条件路径，不保证每次出现；应写条件处理或转待准备，不作为固定 Runner 用例"
        if has_any("键盘弹出", "输入框聚焦", "软键盘"):
            return "软键盘显隐受输入法和系统状态影响，Runner 视觉断言不稳定"
        if has_any("语音创作") and has_any("长按输入", "录音", "麦克风", "权限", "直接说", "点击语音"):
            return "语音链路依赖麦克风权限、长按录音和系统状态，默认转待准备用例"
        if has_any("文件选择器", "相册", "上传图片") and not has_any("已准备测试图片", "固定测试图片", "测试图片已准备"):
            return "图片/相册/文件选择依赖本机文件和系统选择器，未声明测试图片时不直接自动化"
        if has_any("模型打印编辑页") and has_any("一键应用", "尺寸参数", "返回操作"):
            return "模型生成后的编辑页依赖上游生成结果和动态控件，未命中稳定基线前不直接执行"
    rules = [
        (("mock", "接口mock", "接口返回", "服务端返回", "后台造数", "数据库", "后台配置", "已配置匹配接口", "造数"), "依赖接口 Mock、后台造数或服务端状态，当前 Runner 不能直接准备"),
        (("断网", "弱网", "网络异常", "破坏网络", "服务器繁忙", "服务端异常", "5xx"), "依赖网络/服务端异常状态，需要测试环境或人工准备"),
        (("系统权限", "通知权限", "系统通知权限", "系统设置", "权限关闭", "权限预置", "首次权限"), "依赖系统权限或系统设置状态，需要人工准备"),
        (("未登录态", "切换登录态", "切账号", "退出登录", "首次登录", "新用户首次", "清除缓存", "清数据"), "依赖账号态或本地数据状态切换，不能默认直接执行"),
        (("排队中", "并发", "并发数", "当前无排队任务", "任务队列", "取消任务"), "依赖队列/并发/异步任务状态，需要后台或人工准备"),
        (("真实支付", "真实删除", "真实打印完成", "真实外设", "外部app"), "依赖高风险或外部资源，不适合直接自动化"),
        (("设计稿一致", "与设计稿一致", "视觉还原", "完全一致", "模块排列顺序与设计稿", "figma关键区域", "figma设计稿", "figma一致", "画布尺寸", "node-id", "节点:", "节点："), "属于设计稿对比或视觉验收，Runner 无设计稿上下文时容易误判"),
    ]
    for terms, reason in rules:
        if any(str(term).lower() in compact for term in terms):
            return reason
    return ""


def _normalize_runner_case_for_current_app(case: dict) -> dict:
    """Make still-eligible cases follow current app navigation before YAML conversion."""
    if not isinstance(case, dict):
        return case
    text = _case_execution_text(case)
    compact = re.sub(r"\s+", "", text).lower()
    steps = normalize_text_list(case.get("steps") or [])
    if not steps:
        return case

    next_case = dict(case)
    step_blob = re.sub(r"\s+", "", " ".join(steps)).lower()
    if any(term in compact for term in ("ai建模", "图片建模", "文字建模", "语音创作")):
        if "底部中间tab" not in step_blob and "底部tab" not in step_blob and "首页可见ai建模" not in step_blob:
            steps = [
                "点击底部中间 Tab「AI建模」或首页可见的「AI建模」功能卡片，进入 AI建模功能页",
                "等待页面出现「AI建模」、开始创作、图片建模、语音创作或输入框中的任意一个稳定信号",
            ] + steps
            next_case["steps"] = steps
            hint = first_non_empty(next_case.get("repair_hints") or next_case.get("repairHints"))
            add_hint = "当前版本优先从底部中间 Tab「AI建模」进入，不要在首页三维创作区查找旧入口。"
            next_case["repair_hints"] = f"{hint}；{add_hint}" if hint else add_hint
    if any(term in compact for term in ("标牌", "趣味印章", "印章")):
        if "横向" not in step_blob and "滑动" not in step_blob:
            steps = [
                "在首页中部功能入口区域横向滑动，直到「标牌」或「趣味印章」入口可见",
            ] + steps
            next_case["steps"] = steps
    return next_case


def _requirement_points_from_payload(payload: dict) -> List[str]:
    analysis = payload.get("analysis") if isinstance(payload, dict) else {}
    points: List[str] = []
    if isinstance(analysis, dict):
        points.extend(normalize_text_list(
            analysis.get("requirement_points")
            or analysis.get("requirementPoints")
            or analysis.get("test_points")
            or analysis.get("testPoints")
            or []
        ))
        if not points:
            points.extend(normalize_text_list(
                analysis.get("business_goals") or analysis.get("businessGoals") or []
            ))
    if not points:
        for item in payload.get("scenarios") or []:
            if isinstance(item, dict):
                text = first_non_empty(
                    item.get("feature"), item.get("scenario"), item.get("expected")
                )
                if text:
                    points.append(text)
    return list(dict.fromkeys(point.strip() for point in points if point.strip()))


def _coverage_blob_for_item(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item or "")
    values = [
        item.get("title"),
        item.get("name"),
        item.get("scenario"),
        item.get("feature"),
        item.get("goal"),
        item.get("requirementRefs") or item.get("requirement_refs"),
        item.get("coverage"),
        item.get("expected_result") or item.get("expectedResult") or item.get("expected"),
        item.get("business_path") or item.get("businessPath") or item.get("path"),
        " ".join(normalize_text_list(item.get("steps") or [])),
        " ".join(normalize_text_list(item.get("assertions") or item.get("expected") or [])),
        " ".join(normalize_text_list(item.get("tags") or [])),
    ]
    return " ".join(str(value or "") for value in values)


_COVERAGE_STOPWORDS = {
    "页面", "功能", "用户", "展示", "进入", "点击", "验证", "正常", "流程", "场景",
    "可以", "进行", "是否", "相关", "测试", "按钮", "入口", "列表", "内容",
    "查看", "打开", "支持", "完成", "实现", "需要", "能够", "应该", "对应",
}


def _coverage_tokens(text: str) -> List[str]:
    normalized = str(text or "").lower()
    for word in _COVERAGE_STOPWORDS:
        normalized = normalized.replace(word, " ")
    raw = re.findall(r"[\w\u4e00-\u9fff]{2,}", normalized)
    tokens: List[str] = []
    for token in raw:
        if token in _COVERAGE_STOPWORDS:
            continue
        tokens.append(token)
        if re.fullmatch(r"[\u4e00-\u9fff]{5,}", token):
            tokens.extend(token[i:i + 4] for i in range(0, max(0, len(token) - 3)))
    return list(dict.fromkeys(tokens))


def _point_covered(point: str, blobs: Iterable[str]) -> bool:
    tokens = _coverage_tokens(point)
    if not tokens:
        return False
    strong = tokens[:8]
    for blob in blobs:
        lower = blob.lower()
        hit = sum(1 for token in strong if token in lower)
        if hit >= max(1, min(3, len(strong))):
            return True
    return False


def audit_case_coverage(payload: Any) -> Tuple[dict, dict]:
    """审计用例覆盖度。返回 ``(normalized_payload, coverage_audit_dict)``。"""
    normalized = normalize_cases_payload(payload)
    points = _requirement_points_from_payload(normalized)
    case_blobs = [_coverage_blob_for_item(item) for item in normalized.get("cases") or []]
    scenario_blobs = [_coverage_blob_for_item(item) for item in normalized.get("scenarios") or []]
    manual_blobs = [_coverage_blob_for_item(item) for item in normalized.get("manual_cases") or []]
    missing_cases = [point for point in points if not _point_covered(point, case_blobs + manual_blobs)]
    missing_scenarios = [point for point in points if not _point_covered(point, scenario_blobs)]
    generic_assertions: List[str] = []
    for case in normalized.get("cases") or []:
        if isinstance(case, dict) and not _case_has_meaningful_assertion(case):
            generic_assertions.append(case.get("title") or case.get("name") or "未命名用例")
    review = normalized.setdefault("review", {})
    review["coverage_audit"] = {
        "requirement_point_count": len(points),
        "case_count": len(normalized.get("cases") or []),
        "manual_case_count": len(normalized.get("manual_cases") or []),
        "missing_case_points": missing_cases,
        "missing_scenario_points": missing_scenarios,
        "generic_assertion_cases": generic_assertions,
        "ok": not missing_cases,
    }
    return normalized, review["coverage_audit"]


def split_automation_ready_cases(payload: Any) -> dict:
    """分离可自动化用例。"""
    normalized = normalize_cases_payload(payload)
    if normalized.get("_automation_ready"):
        return normalized
    ready: List[Any] = []
    manual: List[Any] = list(normalized.get("manual_cases") or [])
    for case in normalized["cases"]:
        if not isinstance(case, dict):
            continue
        manual_reason = _case_manual_block_reason(case)
        if manual_reason:
            item = dict(case)
            item["reason"] = manual_reason
            item["suggested_setup"] = item.get("suggested_setup") or item.get("setup") or "保留到完整测试用例/脑图，准备好数据、Mock 或环境后再人工转自动化"
            manual.append(item)
            continue
        steps = normalize_text_list(case.get("steps") or [])
        if not steps:
            manual.append({
                "title": case.get("title") or case.get("name") or "未命名用例",
                "reason": "缺少可执行 UI 步骤，暂不生成自动化 YAML",
                "suggested_setup": "补充业务路径和页面入口后再转自动化",
            })
            continue
        ready.append(_normalize_runner_case_for_current_app(case))
    normalized["cases"] = ready
    normalized["manual_cases"] = manual
    normalized["_automation_ready"] = True
    if not ready:
        raise ValueError("没有可转换为自动化 YAML 的用例：请补充可执行 UI 步骤")
    return normalized


# ---------------------------------------------------------------------------
# 守卫流程生成
# ---------------------------------------------------------------------------

def external_activity_cleanup_flow(indent):
    """返回清理外部 Activity 的流程行。"""
    return [
        indent + "- runAdbShell: " + yaml_text("input keyevent 3"),
        indent + "- sleep: 500",
    ]


def dynamic_recent_tasks_cleanup_flow(indent):
    """Return a screen-size aware recent-task cleanup guard.

    固定坐标的最近任务清理在不同手机分辨率上容易误滑。这里在设备端
    读取 ``wm size``，按宽高比例计算滑动坐标，只用于新生成 YAML 的
    启动前兜底；已有基线 YAML 不做迁移。
    """
    script = (
        "input keyevent 3; "
        "sleep 1; "
        "size=$(wm size | grep -oE '[0-9]+x[0-9]+' | tail -1); "
        "if [ -n \"$size\" ]; then "
        "w=$(echo \"$size\" | cut -d x -f 1); h=$(echo \"$size\" | cut -d x -f 2); "
        "x=$((w/2)); y1=$((h*82/100)); y2=$((h*18/100)); "
        "input keyevent 187; sleep 1; "
        "input swipe $x $y1 $x $y2 300; "
        "input swipe $x $y1 $x $y2 300; "
        "input swipe $x $y1 $x $y2 300; "
        "input keyevent 3; "
        "else input keyevent 3; fi"
    )
    return [
        indent + "- runAdbShell: " + yaml_text(script),
        indent + "- sleep: 800",
    ]


def launch_guard_flow(indent, app_package=None, evidence_text=""):
    """生成启动守卫流程。"""
    app_package = (app_package or "").strip()
    if not app_package:
        return []
    mode = runtime_guard_mode()
    flows = []
    if mode == "strict":
        flows.extend(dynamic_recent_tasks_cleanup_flow(indent))
    elif mode == "balanced":
        flows.append(indent + "- runAdbShell: " + yaml_text("input keyevent 3"))
    flows.extend([
        indent + "- runAdbShell: " + yaml_text("am force-stop " + app_package),
        indent + "- launch: " + app_package,
    ])
    if mode == "strict" or evidence_needs_popup_guard(evidence_text):
        flows.extend([
            indent + "- ai: " + yaml_text("如果出现权限弹窗、升级弹窗、广告弹窗、活动弹窗或引导浮层，优先点击允许、知道了、稍后、跳过、关闭或右上角关闭按钮；没有弹窗就继续"),
            indent + "- sleep: 1000",
        ])
    if mode == "strict":
        flows.extend([
            indent + "- ai: " + yaml_text("确保当前停留在被测 App 内；如果不在首页，尝试返回到首页或点击底部首页 Tab"),
            indent + "- sleep: 1000",
        ])
    return flows


def cleanup_guard_flow(indent, app_package=None, evidence_text=""):
    """生成清理守卫流程。"""
    app_package = (app_package or "").strip()
    if not app_package:
        return []
    flows = []
    if runtime_guard_mode() == "strict" or evidence_needs_popup_guard(evidence_text):
        flows.extend([
            indent + "- ai: " + yaml_text("如果页面出现未保存提示、确认弹窗或遮挡弹窗，点击取消、关闭或返回到稳定状态；没有弹窗就继续"),
            indent + "- sleep: 500",
        ])
    flows.extend([
        indent + "- runAdbShell: " + yaml_text("am force-stop " + app_package),
        indent + "- sleep: 1000",
    ])
    return flows


# ---------------------------------------------------------------------------
# flow_lines_for_step — 自然语言步骤转 Midscene YAML 行
# ---------------------------------------------------------------------------

def flow_lines_for_step(indent, text, add_transition_wait=True):
    """将自然语言步骤转为 Midscene YAML 流程行。"""
    text = str(text or "").strip()
    if not text:
        return []
    input_value = parse_input_value(text)
    if input_value:
        input_target = input_target_from_text(text)
        if input_action_requires_search_entry(text):
            return [
                indent + "- aiTap: " + yaml_text(search_entry_target_from_text(text)),
                indent + "- sleep: 300",
                indent + "- aiInput: " + yaml_text("当前页面的搜索输入框或文本输入框"),
                indent + "  value: " + yaml_text(input_value),
                indent + "- sleep: 200",
                indent + "- aiKeyboardPress: " + yaml_text("当前页面的搜索输入框或文本输入框"),
                indent + "  keyName: " + yaml_text("Enter"),
                indent + "- sleep: 300",
            ]
        lines = [
            indent + "- aiTap: " + yaml_text(input_target),
            indent + "- sleep: 200",
            indent + "- aiInput: " + yaml_text(input_target),
            indent + "  value: " + yaml_text(input_value),
            indent + "- sleep: 200",
        ]
        return lines
    if any(word in text for word in ("横向", "水平", "左划", "右划", "向左滑", "向右滑", "滑动")) and any(word in text for word in ("icon", "图标", "入口", "我的学习", "功能")):
        target = "我的学习下方的横向功能 icon 列表区域"
        if "我的学习" not in text:
            target = "当前页面中的横向功能 icon 列表区域"
        return [
            indent + "- aiScroll: " + yaml_text(target + "，只滚动该横向列表，不要滚动整个页面"),
            indent + "  scrollType: " + yaml_text("singleAction"),
            indent + "  direction: " + yaml_text("right"),
            indent + "  distance: 400",
            indent + "- sleep: 500",
        ]
    action = action_type(text)
    if action == "aiTap":
        lines = [f"{indent}- aiTap: {yaml_text(text)}", indent + "- sleep: 300"]
        if not add_transition_wait:
            return lines
        compact = re.sub(r"\s+", "", text)
        if "百度网盘" in compact and any(word in compact for word in ("点击", "点按", "进入", "打开", "选择")):
            lines.extend([
                indent + "- aiWaitFor: " + yaml_text(BAIDU_NETDISK_POST_CLICK_WAIT),
                indent + "  timeout: " + str(BAIDU_NETDISK_POST_CLICK_TIMEOUT_MS),
            ])
        elif any(word in compact for word in TRANSITION_TAP_WORDS):
            lines.extend([
                indent + "- aiWaitFor: " + yaml_text("点击后的目标页面或提示已稳定显示"),
                indent + "  timeout: " + str(min(loading_wait_timeout_for_context(text), MAX_WAITFOR_TIMEOUT_MS)),
            ])
        return lines
    return [f"{indent}- {action}: {yaml_text(text)}"]


# ---------------------------------------------------------------------------
# 基线元数据 & case_to_task_yaml
# ---------------------------------------------------------------------------

def ensure_case_trace(case, index):
    """确保用例有 case_id、priority、smoke、tags。"""
    row = dict(case)
    row.setdefault("case_id", f"TC-{index:03d}")
    row.setdefault("priority", case_priority(row))
    row.setdefault("smoke", is_smoke_case(row))
    tags = case_tags(row)
    if row.get("smoke") and not any("冒烟" in tag for tag in tags):
        tags.append("冒烟")
    if tags:
        row["tags"] = tags
    return row


def build_baseline_meta(case, normalized_steps, assertions):
    """构建基线描述元数据。"""
    title = str(case.get("title") or case.get("name") or "未命名用例").strip()
    preconditions = normalize_text_list(case.get("preconditions") or case.get("precondition"))
    goal = first_non_empty(
        case_value(case, "goal", "business_goal", "objective", "description", "desc"),
        f"验证{title}"
    )
    start_page = first_non_empty(
        case_value(case, "start_page", "startPage", "start", "entry_page", "entryPage"),
        "App 首页"
    )
    path = first_non_empty(
        case_value(case, "business_path", "businessPath", "path", "flow_path", "flowPath", "navigation_path", "navigationPath"),
        " -> ".join(normalized_steps[:8])
    )
    expected = first_non_empty(
        case_value(case, "expected_result", "expectedResult", "expected", "expectation"),
        assertions[:6]
    )
    repair_hints = first_non_empty(
        case_value(case, "repair_hints", "repairHints", "repair_hint", "repairHint", "hints"),
        "优先参考页面知识库和辅助截图中的真实入口文案、页面标题、Tab 名称和常用断言；不要使用坐标。"
    )
    risk = first_non_empty(case_value(case, "risk", "risks", "business_risk", "businessRisk"))
    coverage = first_non_empty(case_value(case, "coverage", "coverage_point", "coveragePoint", "test_point", "testPoint"))
    data_requirements = first_non_empty(case_value(case, "data_requirements", "dataRequirements", "test_data", "testData"))
    automation_reason = first_non_empty(case_value(case, "automation_reason", "automationReason", "why_automated", "whyAutomated"))
    scenario = first_non_empty(case_value(case, "scenario", "scene", "test_scenario", "testScenario"))
    case_id = first_non_empty(case_value(case, "case_id", "caseId", "id"))
    priority = case_priority(case)
    tags = "、".join(case_tags(case)[:8])
    smoke = "true" if is_smoke_case(case) else "false"
    if preconditions:
        goal = f"{goal}；前置：{'；'.join(preconditions[:4])}"
    return {
        "case_id": case_id,
        "priority": priority,
        "smoke": smoke,
        "tags": tags,
        "goal": goal,
        "scenario": scenario,
        "start_page": start_page,
        "path": path,
        "expected": expected,
        "repair_hint": repair_hints,
        "risk": risk,
        "coverage": coverage,
        "data": data_requirements,
        "automation": automation_reason
    }


def baseline_comment_lines(indent, meta):
    """生成基线描述注释行。"""
    labels = [
        ("baseline.case_id", meta.get("case_id")),
        ("baseline.priority", meta.get("priority")),
        ("baseline.smoke", meta.get("smoke")),
        ("baseline.tags", meta.get("tags")),
        ("baseline.goal", meta.get("goal")),
        ("baseline.scenario", meta.get("scenario")),
        ("baseline.start_page", meta.get("start_page")),
        ("baseline.path", meta.get("path")),
        ("baseline.expected", meta.get("expected")),
        ("baseline.repair_hint", meta.get("repair_hint")),
        ("baseline.risk", meta.get("risk")),
        ("baseline.coverage", meta.get("coverage")),
        ("baseline.data", meta.get("data")),
        ("baseline.automation", meta.get("automation")),
    ]
    return [
        f"{indent}  # {key}: {yaml_comment_text(value)}"
        for key, value in labels
        if yaml_comment_text(value)
    ]


def normalize_assertion_for_yaml(assertion, case):
    """规范化断言文本。"""
    text = str(assertion or "").strip()
    if not text:
        return ""
    generic_signals = (
        "页面正常展示", "列表展示正常", "结果符合预期", "操作成功",
        "功能正常", "页面无异常", "跳转成功", "进入相关页面", "展示正常",
    )
    if not any(signal in text for signal in generic_signals):
        return text
    expected = first_non_empty(
        case_value(case, "expected_result", "expectedResult", "expected", "expectation"),
        case_value(case, "goal", "business_goal", "objective"),
        case_value(case, "coverage", "coverage_point", "test_point"),
        case.get("title") if isinstance(case, dict) else "",
    )
    if expected:
        return f"{expected}；页面展示对应标题、核心区域、列表内容或空态提示之一"
    return "页面展示当前业务目标对应的标题、核心区域、列表内容或空态提示之一"


def _assertion_rank_for_yaml(text, case):
    """给候选断言排序，优先选择最像最终业务结果的可见断言。"""
    raw = str(text or "").strip()
    if not raw:
        return -100
    lower = raw.lower()
    score = 0
    vague = (
        "页面正常", "正常展示", "结果符合预期", "操作成功", "功能正常",
        "跳转成功", "无异常", "符合预期", "当前页面内容符合预期",
    )
    if any(item in raw for item in vague):
        score -= 10
    expected = first_non_empty(
        case_value(case, "expected_result", "expectedResult", "expected", "expectation"),
        case_value(case, "goal", "business_goal", "objective"),
        case_value(case, "coverage", "coverage_point", "test_point"),
    )
    if expected and raw == expected:
        score += 12
    elif expected and raw in expected:
        score += 6
    title = str(case.get("title") or case.get("name") or "").strip() if isinstance(case, dict) else ""
    if title and title in raw:
        score += 4
    visible_words = (
        "页面", "标题", "入口", "按钮", "tab", "Tab", "列表", "空态", "弹窗",
        "提示", "状态", "进度", "结果", "详情", "卡片", "模块", "区域",
    )
    if any(word.lower() in lower or word in raw for word in visible_words):
        score += 5
    final_words = ("最终", "完成", "结果", "生成", "详情", "列表", "预览", "成功", "可见")
    if any(word in raw for word in final_words):
        score += 3
    if len(raw) > 140:
        score -= 4
    if len(raw) < 8:
        score -= 4
    return score


def select_yaml_assertions_for_case(case, assertions, step_expected=None):
    """按平台风格选择 Runner YAML 断言，避免把所有验收点塞进 aiAssert。"""
    case = case if isinstance(case, dict) else {}
    candidates = []
    expected = first_non_empty(
        case_value(case, "expected_result", "expectedResult", "expectation"),
        case_value(case, "expected", "expect"),
    )
    if expected:
        candidates.append(expected)
    candidates.extend(normalize_text_list(assertions))
    candidates.extend(normalize_text_list(step_expected or []))

    normalized = []
    seen = set()
    for item in candidates:
        text = normalize_assertion_for_yaml(item, case)
        if not text:
            continue
        key = re.sub(r"\s+", "", text)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(text)

    if not normalized:
        return []

    ranked = sorted(
        enumerate(normalized),
        key=lambda item: (_assertion_rank_for_yaml(item[1], case), -item[0]),
        reverse=True,
    )
    selected_indexes = sorted(idx for idx, _ in ranked[:YAML_GENERATED_ASSERTION_LIMIT])
    return [normalized[idx] for idx in selected_indexes]


def resolve_app_package(module="", file="", yaml_text="", explicit="", allow_default=False):
    """解析 app package。"""
    resolved = (
        (explicit or "").strip()
        or extract_app_package_from_yaml(yaml_text)
    ).strip()
    if resolved:
        return resolved
    return (os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE).strip() if allow_default else "")


def generated_step_handled_by_launch_guard(text):
    compact = re.sub(r"\s+", "", str(text or ""))
    if re.match(r"^(启动|打开).*(App|APP|应用).*(首页|加载)", compact):
        return "launch"
    if re.match(r"^(如|如果|若).*不在首页.*(返回|首页)", compact):
        return "home_recovery"
    return ""


def generated_launch_ready_prompt(text):
    match = re.search(r"等待(.{2,40}?首页)(?:加载完成|稳定显示)", str(text or ""))
    page = str(match.group(1) if match else "被测 App 首页").strip(" ，,。")
    return f"{page}已加载完成，首页核心功能入口可见"


def generated_step_explicitly_waits(text):
    compact = re.sub(r"\s+", "", str(text or ""))
    return bool(compact) and (
        compact.startswith(("等待", "确认", "检查", "验证"))
        or any(word in compact for word in ("可见后", "显示后", "加载完成后", "稳定显示后"))
    )


def generated_steps_cover_assertion_wait(steps, assertion):
    quoted_targets = [
        str(item).strip()
        for item in re.findall(r"[「『\"']([^」』\"']{2,40})[」』\"']", str(assertion or ""))
        if str(item).strip()
    ]
    if not quoted_targets:
        return False
    for step in steps or []:
        text = str(step or "").strip()
        if not generated_step_explicitly_waits(text):
            continue
        if any(target in text for target in quoted_targets):
            return True
    return False


def case_to_task_yaml(case, indent="  ", case_index=1):
    """将单条用例转为 Midscene YAML task 块。"""
    case = ensure_case_trace(case, case_index) if isinstance(case, dict) else case
    title = case.get("title") or case.get("name") or "未命名用例"
    preconditions = case.get("preconditions") or case.get("precondition") or []
    steps = case.get("steps") or []
    assertions = case.get("assertions") or case.get("expects") or case.get("expected") or []
    app_package = resolve_app_package(explicit=case.get("app_package") or case.get("appPackage") or "")
    flow_indent = indent + "  "

    if isinstance(preconditions, str):
        preconditions = [preconditions]
    elif not isinstance(preconditions, list):
        preconditions = normalize_text_list(preconditions)
    preconditions = normalize_text_list(preconditions)
    if isinstance(steps, str):
        steps = [steps]
    elif not isinstance(steps, list):
        steps = normalize_text_list(steps)
    if isinstance(assertions, str):
        assertions = [assertions]
    elif not isinstance(assertions, list):
        assertions = normalize_text_list(assertions)
    raw_assertions = normalize_text_list(assertions)

    normalized_steps = []
    step_expected = []
    for step in steps:
        if isinstance(step, dict):
            action = step.get("action") or step.get("step") or step.get("description") or step.get("name")
            expected = step.get("expected") or step.get("assertion") or step.get("expect")
            if action:
                normalized_steps.append(str(action))
            if expected:
                step_expected.append(str(expected))
        else:
            normalized_steps.append(str(step))

    assertions = select_yaml_assertions_for_case(case, raw_assertions, step_expected)
    meta = build_baseline_meta(case, normalized_steps, assertions)

    flows = []
    if app_package:
        flows.extend(launch_guard_flow(flow_indent + "  ", app_package))

    launch_ready_added = False
    limited_steps = normalized_steps[:12]
    for index, item in enumerate(limited_steps):
        text = str(item).strip()
        if not text or text.startswith("确认前置条件"):
            continue
        guard_handled = generated_step_handled_by_launch_guard(text) if app_package else ""
        if guard_handled == "launch":
            if not launch_ready_added:
                flows.append(flow_indent + "  - aiWaitFor: " + yaml_text(generated_launch_ready_prompt(text)))
                flows.append(flow_indent + "    timeout: " + str(min(DEFAULT_WAITFOR_TIMEOUT_MS, 12000)))
                launch_ready_added = True
            continue
        if guard_handled == "home_recovery":
            continue
        next_text = str(limited_steps[index + 1]).strip() if index + 1 < len(limited_steps) else ""
        flows.extend(flow_lines_for_step(
            flow_indent + "  ",
            text,
            add_transition_wait=not generated_step_explicitly_waits(next_text),
        ))

    for item in assertions[:4]:
        text = str(item).strip()
        if text:
            if ENABLE_ASSERT_WAITFOR and not generated_steps_cover_assertion_wait(limited_steps, text):
                flows.append(flow_indent + "  - aiWaitFor: " + yaml_text(text))
                flows.append(flow_indent + "    timeout: " + str(DEFAULT_WAITFOR_TIMEOUT_MS))
            flows.append(flow_indent + "  - aiAssert: " + yaml_text(text))

    comment_block = "\n".join(baseline_comment_lines(indent, meta))
    return indent + "- name: " + yaml_text(title) + "\n" + comment_block + "\n" + indent + "  flow:\n" + "\n".join(flows)


# ---------------------------------------------------------------------------
# YAML 结构解析与操作
# ---------------------------------------------------------------------------

def has_unclosed_yaml_quote(value):
    """检测 YAML 值是否有未闭合引号。"""
    value = str(value or "").rstrip()
    if not value:
        return False
    first = value[0]
    if first not in ("'", '"'):
        return False
    escaped = False
    count = 0
    for ch in value:
        if escaped:
            escaped = False
            continue
        if ch == "\\":
            escaped = True
            continue
        if ch == first:
            count += 1
    return count % 2 == 1


def normalize_yaml_key_punctuation(text):
    """将 YAML key 的中文冒号转为英文冒号。"""
    lines = []
    for line in str(text or "").splitlines():
        new_line = re.sub(r"^(\s*-\s*[A-Za-z][\w]*)(\s*)：", r"\1:", line)
        new_line = re.sub(r"^(\s*[A-Za-z][\w]*)(\s*)：", r"\1:", new_line)
        lines.append(new_line)
    normalized = "\n".join(lines)
    if str(text or "").endswith("\n"):
        normalized += "\n"
    return normalized


def normalize_unclosed_yaml_quotes(text):
    """修复 YAML 中未闭合的引号。"""
    lines = []
    changed = False
    source_lines = str(text or "").splitlines()
    for idx, line in enumerate(source_lines):
        new_line = line.rstrip()
        stripped = new_line.strip()
        next_stripped = source_lines[idx + 1].strip() if idx + 1 < len(source_lines) else ""
        m = re.match(r"^(\s*(?:-\s*)?[A-Za-z][\w]*\s*:\s*)(['\"])(.*)$", new_line)
        if m and has_unclosed_yaml_quote(m.group(2) + m.group(3)):
            if (
                not next_stripped
                or next_stripped.startswith("#")
                or re.match(r"^-?\s*[A-Za-z][\w]*\s*:", next_stripped)
                or re.match(r"^-\s+[A-Za-z][\w]*\s*:", next_stripped)
            ):
                quote = m.group(2)
                new_line = new_line + quote
                changed = True
        lines.append(new_line)
    normalized = "\n".join(lines)
    if str(text or "").endswith("\n"):
        normalized += "\n"
    return normalized if changed else str(text or "")


def detect_yaml_platform(text):
    """检测 YAML 目标平台。"""
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = re.match(r"^(android|ios|web|computer)\s*:", stripped)
        if m:
            return m.group(1)
        if re.match(r"^tasks\s*:", stripped):
            break
    return "android"


def normalize_full_yaml_structure(text):
    """规范化 YAML 整体结构（缩进、tasks 包裹）。"""
    text = normalize_unclosed_yaml_quotes(normalize_yaml_key_punctuation(str(text or ""))).strip()
    if not text:
        return text
    if re.match(r"^\s*-\s+name\s*:", text):
        text = "android:\n\ntasks:\n" + "\n".join("  " + line if line.strip() else line for line in text.splitlines())
    lines = text.splitlines()
    result = []
    in_tasks = False
    in_task_flow = False
    changed = False
    idx = 0
    while idx < len(lines):
        line = lines[idx].rstrip()
        stripped = line.strip()
        if re.match(r"^tasks\s*:\s*$", line):
            result.append("tasks:")
            in_tasks = True
            idx += 1
            continue
        if in_tasks:
            if not stripped:
                result.append("")
                idx += 1
                continue
            if re.match(r"^[A-Za-z_][\w-]*\s*:\s*$", line) and not re.match(r"^\s", line) and not re.match(r"^flow\s*:\s*$", line):
                in_tasks = False
                in_task_flow = False
                result.append(line)
                idx += 1
                continue
            if re.match(r"^\s*-\s+name\s*:", line):
                result.append("  " + stripped)
                in_task_flow = False
                changed = True
                idx += 1
                continue
            if re.match(r"^\s*flow\s*:\s*$", line):
                result.append("    flow:")
                in_task_flow = True
                changed = True
                idx += 1
                continue
            if stripped.startswith("#"):
                result.append("    " + stripped)
                changed = changed or not line.startswith("    ") or line.startswith("      ")
                idx += 1
                continue
            if re.match(r"^\s*-\s+[A-Za-z][\w]*\s*:", line):
                result.append("      " + stripped)
                in_task_flow = True
                changed = changed or not line.startswith("      ") or line.startswith("        ")
                idx += 1
                continue
            if stripped and re.match(r"^[A-Za-z][\w]*\s*:", stripped):
                if re.match(r"^(name|flow)\s*:", stripped):
                    result.append("    " + stripped)
                    in_task_flow = stripped.startswith("flow")
                    changed = changed or not line.startswith("    ") or line.startswith("      ")
                else:
                    wanted = "        " if in_task_flow else "    "
                    result.append(wanted + stripped)
                    changed = changed or not line.startswith(wanted) or line.startswith(wanted + "  ")
                idx += 1
                continue
            if stripped and not line.startswith(" "):
                result.append("    " + stripped)
                changed = True
                idx += 1
                continue
        result.append(line)
        idx += 1
    normalized = "\n".join(result).rstrip() + "\n"
    if changed:
        return normalized
    return text.rstrip() + "\n"


def remove_empty_midscene_platform_roots(text):
    """Remove top-level ``android: null`` / ``ios: null`` stubs before dispatch."""
    if _pyyaml is None:
        return str(text or "")
    raw = str(text or "")
    if not raw.strip():
        return raw
    try:
        parsed = _pyyaml.safe_load(raw)
    except Exception:
        return raw
    if not isinstance(parsed, dict):
        return raw
    changed = False
    for platform in ("android", "ios"):
        if platform in parsed and parsed.get(platform) is None:
            parsed.pop(platform, None)
            changed = True
    if not changed:
        return raw
    return _pyyaml.safe_dump(parsed, allow_unicode=True, sort_keys=False, width=100000)


def ensure_midscene_platform_root(text, platform="android"):
    """Wrap root-level ``tasks`` into the platform root required by Runner."""
    if _pyyaml is None:
        return str(text or "")
    raw = str(text or "")
    if not raw.strip():
        return raw
    try:
        parsed = _pyyaml.safe_load(raw)
    except Exception:
        return raw
    if not isinstance(parsed, dict) or not isinstance(parsed.get("tasks"), list):
        return raw
    if any(isinstance(parsed.get(name), dict) and isinstance(parsed[name].get("tasks"), list) for name in ("android", "ios")):
        return raw
    target_platform = str(platform or "android").strip().lower()
    if target_platform not in ("android", "ios"):
        target_platform = "android"
    tasks = parsed.get("tasks") or []
    wrapped = {target_platform: {"tasks": tasks}}
    return _pyyaml.safe_dump(wrapped, allow_unicode=True, sort_keys=False, width=100000)


def midscene_cli_dispatch_yaml_text(text, platform="android", device_id=""):
    """Build the temporary YAML layout expected by Midscene CLI: interface config + root tasks."""
    if _pyyaml is None:
        return str(text or "")
    raw = str(text or "").replace("\ufeff", "")
    if not raw.strip():
        return raw
    try:
        parsed = _pyyaml.safe_load(raw)
    except Exception:
        return raw
    if not isinstance(parsed, dict):
        return raw
    tasks = parsed.get("tasks")
    interface_config = {}
    interface_name = ""
    for name in ("android", "ios", "web", "computer", "interface"):
        node = parsed.get(name)
        if isinstance(node, dict):
            if isinstance(node.get("tasks"), list) and tasks is None:
                tasks = node.get("tasks")
            interface_name = name
            interface_config = {k: v for k, v in node.items() if k != "tasks"}
            break
        if node is None and name in parsed and not interface_name:
            interface_name = name
    if not isinstance(tasks, list):
        return raw
    if not interface_name:
        interface_name = str(platform or "android").strip().lower()
    if interface_name not in ("android", "ios", "web", "computer", "interface"):
        interface_name = "android"
    if interface_name == "android" and str(device_id or "").strip():
        interface_config["deviceId"] = str(device_id or "").strip()
    agent_config = dict(parsed.get("agent") or {}) if isinstance(parsed.get("agent"), dict) else {}
    if interface_name == "android" and str(device_id or "").strip():
        agent_config.setdefault("screenshotShrinkFactor", 2)
    cli = {interface_name: interface_config or {}}
    if agent_config:
        cli["agent"] = agent_config
    cli["tasks"] = tasks
    return _pyyaml.safe_dump(cli, allow_unicode=True, sort_keys=False, width=100000)


def normalize_model_json(text):
    """规范化模型 JSON 输出。"""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.I).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def normalize_yaml_from_model(text):
    """从模型输出解析修复后 YAML。"""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:ya?ml)?", "", text, flags=re.I).strip()
        text = re.sub(r"```$", "", text).strip()
    parsed = normalize_model_json(text)
    content = parsed.get("content") or parsed.get("yaml") or parsed.get("yaml_content")
    if not content:
        raise ValueError("模型未返回修复后的 YAML content")
    if not isinstance(content, str):
        raise ValueError("模型返回的 YAML content 必须是字符串，不能是 JSON 对象或数组")
    content = normalize_full_yaml_structure(content)
    return {
        "analysis": parsed.get("analysis") or parsed.get("reason") or "",
        "changes": parsed.get("changes") or [],
        "content": content.strip() + "\n"
    }


def normalize_yaml_task_block_from_model(text):
    """从模型输出解析修复后单条 task。"""
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:ya?ml)?", "", text, flags=re.I).strip()
        text = re.sub(r"```$", "", text).strip()
    parsed = normalize_model_json(text)
    content = parsed.get("task") or parsed.get("content") or parsed.get("yaml") or parsed.get("yaml_content")
    if not content:
        raise ValueError("模型未返回修复后的单条 task 内容")
    if not isinstance(content, str):
        raise ValueError("模型返回的 task 必须是 YAML 字符串，不能是 JSON 对象或数组")
    content = str(content).strip("\n")
    return {
        "analysis": parsed.get("analysis") or parsed.get("reason") or "",
        "changes": parsed.get("changes") or [],
        "content": content
    }


# ---------------------------------------------------------------------------
# YAML task block 查找与替换
# ---------------------------------------------------------------------------

def find_yaml_task_block(yaml_text, task_name):
    """查找 YAML 中指定 task 的文本块。

    返回 dict: {name, start, end, indent, block}。
    """
    lines = yaml_text.splitlines()
    target = (task_name or "").strip()
    name_re = re.compile(r"^(\s*)-\s+name:\s*(.+?)\s*$")
    start = None
    indent = ""
    actual_name = ""

    for idx, line in enumerate(lines):
        m = name_re.match(line)
        if not m:
            continue
        current_name = _clean_yaml_name(m.group(2))
        if current_name == target:
            start = idx
            indent = m.group(1)
            actual_name = current_name
            break

    if start is None:
        raise ValueError(f"未找到用例：{task_name}")

    end = len(lines)
    for idx in range(start + 1, len(lines)):
        m = name_re.match(lines[idx])
        if m and m.group(1) == indent:
            end = idx
            break

    return {
        "name": actual_name,
        "start": start,
        "end": end,
        "indent": indent,
        "block": "\n".join(lines[start:end])
    }


def replace_yaml_task_block(yaml_text, task_info, new_block):
    """替换 YAML 中指定 task 的文本块。"""
    lines = yaml_text.splitlines()
    block_lines = normalize_task_block_indent(new_block, task_info["indent"]).splitlines()
    if not block_lines or not re.match(r"^\s*-\s+name:\s*", block_lines[0]):
        raise ValueError("修复后的内容必须是一条以 - name: 开头的 YAML task")
    new_text = "\n".join(lines[:task_info["start"]] + block_lines + lines[task_info["end"]:])
    return new_text.rstrip() + "\n"


def normalize_task_block_indent(block, target_indent):
    """规范化 task 块缩进。"""
    raw_lines = normalize_unclosed_yaml_quotes(str(block)).strip("\n").splitlines()
    if not raw_lines:
        raise ValueError("修复后的单条 task 为空")
    first = raw_lines[0]
    m = re.match(r"^(\s*)-\s+name:\s*", first)
    if not m:
        raise ValueError("修复后的内容必须是一条以 - name: 开头的 YAML task")
    source_indent = m.group(1)
    normalized = []
    in_flow = False
    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            normalized.append("")
            continue
        if source_indent and line.startswith(source_indent):
            line = line[len(source_indent):]
            stripped = line.strip()
        if re.match(r"^-\s+name\s*:", stripped):
            normalized.append(target_indent + stripped)
            in_flow = False
        elif stripped.startswith("#"):
            normalized.append(target_indent + "  " + stripped)
        elif re.match(r"^flow\s*:\s*$", stripped):
            normalized.append(target_indent + "  flow:")
            in_flow = True
        elif re.match(r"^-\s+[A-Za-z][\w]*\s*:", stripped):
            normalized.append(target_indent + "    " + stripped)
            in_flow = True
        elif re.match(r"^[A-Za-z][\w]*\s*:", stripped):
            normalized.append(target_indent + ("      " if in_flow else "  ") + stripped)
        else:
            normalized.append(target_indent + "  " + stripped)
    return "\n".join(normalized).rstrip() + "\n"


def yaml_with_single_task(yaml_text, task_name, app_package=None):
    """从 YAML 中提取指定 task 生成单条 task YAML。"""
    task_info = find_yaml_task_block(yaml_text, task_name)
    lines = yaml_text.splitlines()
    tasks_line = None
    for idx, line in enumerate(lines):
        if line.strip() == "tasks:":
            tasks_line = idx
            break
    if tasks_line is None:
        raise ValueError("YAML 缺少 tasks 节点")
    header = "\n".join(lines[:tasks_line + 1]).rstrip()
    return f"{header}\n{task_info['block']}\n"


def list_yaml_task_blocks(yaml_text):
    """列出 YAML 中所有 task 块。"""
    lines = (yaml_text or "").splitlines()
    name_re = re.compile(r"^(\s*)-\s+name:\s*(.+?)\s*$")
    starts = []
    for idx, line in enumerate(lines):
        m = name_re.match(line)
        if m:
            starts.append((idx, m.group(1), _clean_yaml_name(m.group(2))))
    tasks = []
    for pos, (start, indent, name) in enumerate(starts):
        end = len(lines)
        for next_start, next_indent, _ in starts[pos + 1:]:
            if next_indent == indent:
                end = next_start
                break
        tasks.append({
            "name": name,
            "start": start,
            "end": end,
            "indent": indent,
            "block": "\n".join(lines[start:end])
        })
    return tasks


# ---------------------------------------------------------------------------
# task block 辅助函数
# ---------------------------------------------------------------------------

def task_block_has_key(block, key):
    """判断 task 块中是否包含指定 flow key。"""
    return re.search(r"^\s*-\s+" + re.escape(key) + r"\s*:", block or "", flags=re.M) is not None


def task_block_has_popup_guard(block):
    """判断 task 块中是否已有弹窗兜底。"""
    return any(word in (block or "") for word in ("弹窗", "浮层", "关闭", "跳过", "允许"))


def task_block_ends_with_key(block, key):
    """判断 task 块最后一个 flow 项是否为指定 key。"""
    flow_keys = []
    for line in (block or "").splitlines():
        m = re.match(r"^\s*-\s+([A-Za-z][\w]*)\s*:", line)
        if m:
            item_key = m.group(1)
            if item_key != "sleep":
                flow_keys.append(item_key)
    return bool(flow_keys) and flow_keys[-1] == key


def task_block_ends_with_force_stop(block):
    """判断 task 块是否以 force-stop 结尾。"""
    last_item = ""
    for line in (block or "").splitlines():
        m = re.match(r"^\s*-\s+([A-Za-z][\w]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        key, value = m.groups()
        if key == "sleep":
            continue
        last_item = f"{key}: {strip_yaml_quotes(value)}"
    return last_item.startswith("runAdbShell: am force-stop ")


def previous_flow_key(lines, idx):
    """向前查找最近的 flow key。"""
    for prev in range(idx - 1, -1, -1):
        m = re.match(r"^\s*-\s+([A-Za-z][\w]*)\s*:", lines[prev])
        if m:
            return m.group(1)
    return ""


def next_flow_item(lines, idx):
    """向后查找最近的 flow 项。"""
    for nxt in range(idx + 1, len(lines)):
        m = re.match(r"^\s*-\s+([A-Za-z][\w]*)\s*:\s*(.*)$", lines[nxt])
        if m:
            return m.group(1), strip_yaml_quotes(m.group(2))
    return "", ""


def previous_flow_item(lines, idx):
    """向前查找最近的 flow 项。"""
    for prev in range(idx - 1, -1, -1):
        m = re.match(r"^\s*-\s+([A-Za-z][\w]*)\s*:\s*(.*)$", lines[prev])
        if m:
            return m.group(1), strip_yaml_quotes(m.group(2))
    return "", ""


def flow_texts_from_task_block(block, keys=None):
    """提取 task 块中指定 key 的 flow 文本。"""
    keys = set(keys or [])
    result = []
    for line in (block or "").splitlines():
        m = re.match(r"^\s*-\s+([A-Za-z][\w]*)\s*:\s*(.*)$", line)
        if not m:
            continue
        key = m.group(1)
        if keys and key not in keys:
            continue
        value = strip_yaml_quotes(m.group(2))
        if value:
            result.append((key, value))
    return result


def task_name_from_block(block):
    """从 task 块提取 name。"""
    m = re.search(r"^\s*-\s+name:\s*(.+?)\s*$", block or "", flags=re.M)
    return strip_yaml_quotes(m.group(1)) if m else "未命名用例"


def task_block_has_baseline_meta(block):
    """判断 task 块是否已有基线描述注释。"""
    return bool(re.search(r"^\s*#\s*baseline\.(goal|start_page|path)\s*:", block or "", flags=re.M))


def derive_task_baseline_meta(block):
    """从 task 块推导基线描述元数据。"""
    name = task_name_from_block(block)
    ai_steps = [text for key, text in flow_texts_from_task_block(block, {"aiTap", "aiAction"})]
    taps = [text for key, text in flow_texts_from_task_block(block, {"aiTap"})]
    assertions = []
    preconditions = []
    for text in ai_steps:
        if text.startswith("验证："):
            assertions.append(text.replace("验证：", "", 1).strip())
        elif text.startswith("确认前置条件："):
            preconditions.append(text.replace("确认前置条件：", "", 1).strip())
    path = " -> ".join(taps[:8]) or " -> ".join([text for text in ai_steps if not text.startswith(("验证：", "确认前置条件："))][:8])
    expected = "；".join(assertions[:6]) or f"{name}相关页面结果符合预期"
    start_page = "App 首页" if task_block_has_key(block, "launch") else "当前页面"
    goal = f"验证{name}"
    if preconditions:
        goal += "；前置：" + "；".join(preconditions[:4])
    return {
        "goal": goal,
        "start_page": start_page,
        "path": path,
        "expected": expected,
        "repair_hint": "优先参考当前 App 的页面知识库和基线辅助截图；如果入口文案变化，使用真实可见文案修复步骤和断言。"
    }


def insert_baseline_comments_into_task_block(block):
    """在 task 块中补充基线描述注释。"""
    if not block or task_block_has_baseline_meta(block):
        return block, []
    lines = block.splitlines()
    name_idx = next((idx for idx, line in enumerate(lines) if re.match(r"^\s*-\s+name:\s*", line)), None)
    if name_idx is None:
        return block, []
    indent = re.match(r"^(\s*)", lines[name_idx]).group(1)
    comments = baseline_comment_lines(indent, derive_task_baseline_meta(block))
    if not comments:
        return block, []
    lines = lines[:name_idx + 1] + comments + lines[name_idx + 1:]
    return "\n".join(lines).rstrip(), ["补充基线描述注释"]


# ---------------------------------------------------------------------------
# 基线元数据提取
# ---------------------------------------------------------------------------

def extract_baseline_meta_from_block(block):
    """从 task 块注释中提取 baseline 元数据。"""
    meta = {}
    for line in (block or "").splitlines():
        m = re.match(r"^\s*#\s*baseline\.([A-Za-z_]+)\s*:\s*(.*)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip()
    return meta


# ---------------------------------------------------------------------------
# stable_case_id
# ---------------------------------------------------------------------------

def stable_case_id(app_package, module, file, task_name):
    """生成稳定的 case ID。"""
    source = "||".join([app_package or "", module or "", clean_filename(file or ""), task_name or ""])
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]
    prefix = clean_id(app_package or module or "midscene", "midscene").replace(".", "_").upper()[:18]
    return f"{prefix}_{digest}"


# ---------------------------------------------------------------------------
# 版本管理
# ---------------------------------------------------------------------------

def version_dir_for(module, file):
    """返回版本目录路径。"""
    return safe_join(VERSION_DIR, clean_id(module, "module"), clean_id(file, "file"))


def save_file_version(module, file, content=None, reason="manual"):
    """保存 YAML 文件版本备份。"""
    try:
        file = clean_filename(file)
        fpath = safe_join(TASK_DIR, module, file)
        if content is None:
            if not os.path.exists(fpath):
                return None
            with open(fpath, encoding="utf-8") as f:
                content = f.read()
        vdir = version_dir_for(module, file)
        os.makedirs(vdir, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        vid = f"{ts}_{clean_id(reason, 'version')}"
        yaml_name = f"{vid}.yaml"
        meta_name = f"{vid}.json"
        write_text_file(safe_join(vdir, yaml_name), content or "")
        meta = {
            "id": vid,
            "module": module,
            "file": file,
            "reason": reason,
            "yaml": yaml_name,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "size": len((content or "").encode("utf-8"))
        }
        write_json_file(safe_join(vdir, meta_name), meta)
        return meta
    except Exception as e:
        print(f"save_file_version failed: {module}/{file}: {e}")
        return None


# ---------------------------------------------------------------------------
# YAML 运行时守卫 — 内部 normalize 函数
# ---------------------------------------------------------------------------

def strip_record_to_report_items(block):
    """移除修复阶段不需要的 recordToReport。"""
    if not block:
        return block, []
    lines = block.splitlines()
    result = []
    changes = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        m = re.match(r"^(\s*)-\s+recordToReport\s*:", line)
        if not m:
            result.append(line)
            idx += 1
            continue
        base_indent = len(m.group(1))
        idx += 1
        removed_children = 0
        while idx < len(lines):
            child = lines[idx]
            if child.strip() and (len(child) - len(child.lstrip(" "))) <= base_indent and re.match(r"^\s*-\s+[A-Za-z][\w]*\s*:", child):
                break
            if re.match(r"^\s*(title|content)\s*:", child):
                removed_children += 1
                idx += 1
                continue
            if child.strip() and (len(child) - len(child.lstrip(" "))) > base_indent:
                removed_children += 1
                idx += 1
                continue
            break
        changes.append("移除修复阶段不需要的 recordToReport，避免 title/content 缩进导致 YAML 解析失败")
    if not changes:
        return block, []
    return "\n".join(result).rstrip(), list(dict.fromkeys(changes))


def normalize_flowitem_syntax_in_task_block(block):
    """规范化 flowItem 语法（别名映射、标量值、aiAction→ai、aiInput 规范等）。"""
    if not block:
        return block, []
    block = normalize_yaml_key_punctuation(block)
    block, record_changes = strip_record_to_report_items(block)
    lines = block.splitlines()
    changes = list(record_changes)
    normalized = []
    idx = 0
    prompt_style_items = PROMPT_STYLE_FLOW_ITEMS
    while idx < len(lines):
        line = lines[idx]
        m = re.match(r"^(\s*-\s+)([A-Za-z][\w]*)(\s*):(\s*)(.*)$", line)
        if not m:
            child = re.match(r"^(\s*)content\s*:(\s*)(.*)$", line)
            prev = normalized[-1] if normalized else ""
            prev_record = re.match(r"^(\s*)-\s+recordToReport\s*:", prev)
            if child and prev_record:
                child_indent, child_space, child_value = child.groups()
                target_indent = prev_record.group(1) + "  "
                value = normalize_yaml_scalar_value(child_value.strip())
                normalized.append(f"{target_indent}content: {value}")
                if child_indent != target_indent or not child_space:
                    changes.append("修复 recordToReport.content 缩进或冒号空格")
                idx += 1
                continue
            normalized.append(line)
            idx += 1
            continue
        prefix, raw_key, before_colon, after_colon, raw_value = m.groups()
        key = FLOW_ITEM_ALIASES.get(raw_key, raw_key)
        if key == "aiAction":
            key = "ai"
            changes.append("将旧式 aiAction 规范为 Midscene 1.7 推荐的 ai")
        if key not in SUPPORTED_FLOW_ITEMS:
            normalized.append(line)
            idx += 1
            continue
        value = raw_value.strip()
        child_lines = []
        child_idx = idx + 1
        base_indent = len(prefix) - 2
        while child_idx < len(lines):
            child = lines[child_idx]
            if child.strip() and (len(child) - len(child.lstrip(" "))) <= base_indent and re.match(r"^\s*-\s+[A-Za-z][\w]*\s*:", child):
                break
            cm = re.match(r"^\s*(prompt|locate|value|timeout|errorMessage|name|keyName|direction|scrollType|distance|deepThink|xpath|cacheable|autoDismissKeyboard|mode)\s*:\s*(.*)$", child)
            if cm:
                child_lines.append((cm.group(1), cm.group(2).strip()))
                child_idx += 1
                continue
            break
        child_map = {}
        for child_key, child_value in child_lines:
            child_map[child_key] = strip_yaml_quotes(child_value)
        if child_lines and key in prompt_style_items and strip_yaml_quotes(value) in ("", "null", "None"):
            prompt_value = child_map.get("prompt") or child_map.get("locate") or child_map.get("value") or child_map.get("name") or ""
            if prompt_value:
                value = yaml_text(prompt_value)
                keep_keys = {"timeout", "errorMessage", "name"} if key in ("aiAssert", "aiWaitFor", "aiQuery") else set()
                normalized.append(f"{prefix}{key}: {value}")
                for child_key, child_value in child_lines:
                    if child_key in keep_keys:
                        child_out = str(safe_int(child_value, 0)) if child_key == "timeout" else normalize_yaml_scalar_value(child_value)
                        normalized.append(f"{' ' * (base_indent + 2)}{child_key}: {child_out}")
                changes.append(f"将 {key} 的 prompt/locate 子字段扁平化为 Midscene YAML 标准写法")
                idx = child_idx
                continue
        if child_lines and key == "aiInput":
            locate_value = strip_yaml_quotes(value)
            if not locate_value or locate_value in ("null", "None"):
                locate_value = child_map.get("prompt") or child_map.get("locate") or "当前页面的输入框"
            input_value = child_map.get("value") or ""
            normalized.append(f"{prefix}{key}: {yaml_text(locate_value)}")
            if input_value:
                normalized.append(f"{' ' * (base_indent + 2)}value: {yaml_text(input_value)}")
            for child_key, child_value in child_lines:
                if child_key in ("autoDismissKeyboard", "mode", "deepThink", "xpath", "cacheable"):
                    normalized.append(f"{' ' * (base_indent + 2)}{child_key}: {normalize_yaml_scalar_value(child_value)}")
            changes.append("将 aiInput 的 prompt/locate/value 子字段规范为 Midscene YAML 标准写法")
            idx = child_idx
            continue
        if raw_key != key:
            changes.append(f"将不标准 flowItem「{raw_key}」规范为「{key}」")
        if not after_colon:
            changes.append(f"修复 {key} 冒号后缺少空格")
        if key in ("aiAction", "aiTap", "aiHover", "aiInput", "aiKeyboardPress", "aiScroll", "aiAssert", "aiWaitFor", "ai", "aiAct", "aiQuery", "aiAsk", "aiBoolean", "aiNumber", "aiString", "recordToReport", "runAdbShell", "runWdaRequest"):
            value = normalize_yaml_scalar_value(value)
        elif key == "sleep":
            value = str(safe_int(value.strip("\"'"), 1000))
        else:
            value = value.strip("\"'")
        normalized.append(f"{prefix}{key}: {value}")
        idx += 1
    if not changes:
        return block, []
    deduped = []
    for item in changes:
        if item not in deduped:
            deduped.append(item)
    return "\n".join(normalized).rstrip(), deduped


# ---------------------------------------------------------------------------
# normalize_input_actions_in_task_block
# ---------------------------------------------------------------------------

def normalize_input_actions_in_task_block(block):
    """将旧式 aiAction/ai 输入动作改为 Midscene 1.7 标准 aiInput + value。"""
    lines = (block or "").splitlines()
    if not lines:
        return block, []
    result = []
    changes = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        m_action = re.match(r"^(\s*)-\s+(?:aiAction|ai|aiAct)\s*:\s*(.+?)\s*$", line)
        if m_action:
            indent, raw_value = m_action.groups()
            value = strip_yaml_quotes(raw_value)
            input_value = parse_input_value(value)
            if input_value:
                result.extend(flow_lines_for_step(indent, value))
                if input_action_requires_search_entry(value):
                    changes.append(f"将搜索入口输入动作「{value}」拆为点击搜索入口、aiInput 输入并提交，保留入口步骤")
                else:
                    changes.append(f"将泛化输入动作「{value}」改为 Midscene 1.7 标准 aiInput + value")
                idx += 1
                continue

        m_input = re.match(r"^(\s*)-\s+aiInput\s*:\s*(.+?)\s*$", line)
        if m_input:
            indent, raw_value = m_input.groups()
            value = strip_yaml_quotes(raw_value)
            next_line = lines[idx + 1] if idx + 1 < len(lines) else ""
            has_value = re.match(r"^\s+value\s*:", next_line) is not None
            if not has_value and re.match(r"^[A-Za-z0-9._@%+\-\u4e00-\u9fff]+$", value):
                result.append(indent + "- aiInput: " + yaml_text("当前页面的搜索输入框或文本输入框"))
                result.append(indent + "  value: " + yaml_text(value))
                changes.append(f"将旧式 aiInput 标量「{value}」改为 Midscene 1.7 标准 aiInput + value")
                idx += 1
                continue

        result.append(line)
        idx += 1
    if not changes:
        return block, []
    deduped = []
    for item in changes:
        if item not in deduped:
            deduped.append(item)
    return "\n".join(result).rstrip(), deduped


# ---------------------------------------------------------------------------
# normalize_search_input_submit_in_task_block
# ---------------------------------------------------------------------------

def normalize_search_input_submit_in_task_block(block, evidence_text=""):
    """为搜索类 aiInput 补充 Enter 提交和 ADB 兜底。"""
    lines = (block or "").splitlines()
    if not lines:
        return block, []
    context_text = "\n".join(lines)
    result = []
    changes = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        m = re.match(r"^(\s*)-\s+aiInput\s*:\s*(.+?)\s*$", line)
        if not m:
            result.append(line)
            idx += 1
            continue
        indent, locate_raw = m.groups()
        child_indent = indent + "  "
        children = []
        j = idx + 1
        while j < len(lines):
            child = lines[j]
            if re.match(r"^\s*-\s+[A-Za-z][\w]*\s*:", child):
                break
            cm = re.match(r"^\s*([A-Za-z][\w]*)\s*:\s*(.*)$", child)
            if cm:
                children.append((cm.group(1), cm.group(2).strip(), child))
                j += 1
                continue
            break
        raw_child_map = {k: v for k, v, _ in children}
        child_map = {k: strip_yaml_quotes(v) for k, v, _ in children}
        value = child_map.get("value", "")
        external_context = is_external_file_picker_context(context_text)
        raw_locate = strip_yaml_quotes(locate_raw)
        locate = normalize_input_locate_for_context(raw_locate, context_text)
        if external_context and locate != raw_locate:
            changes.append("将文件选择器搜索输入框定位文案收敛为顶部搜索输入框，避免泛化定位误点")
        needs_external_fallback = should_add_adb_input_fallback(value, context_text, evidence_text)
        is_search_input = "搜索" in locate or external_context
        if not needs_external_fallback and not is_search_input:
            result.append(line)
            result.extend(child for _, _, child in children)
            idx = j
            continue
        result.append(indent + "- aiInput: " + yaml_text(locate or "当前页面的搜索输入框或文本输入框"))
        emitted = {"value"}
        result.append(child_indent + "value: " + yaml_text(value))
        if external_context and value:
            result.append(child_indent + "autoDismissKeyboard: false")
            if raw_child_map.get("autoDismissKeyboard", "").strip().lower() in ('"false"', "'false'"):
                changes.append("修正 autoDismissKeyboard 为布尔 false，不能写成字符串")
            elif child_map.get("autoDismissKeyboard", "").lower() != "false":
                changes.append("外部文件选择器输入强制设置 autoDismissKeyboard: false")
            elif "autoDismissKeyboard" not in child_map:
                changes.append("外部文件选择器输入增加 autoDismissKeyboard: false，避免输入后键盘收起导致搜索提交不稳定")
        elif "autoDismissKeyboard" in child_map:
            result.append(child_indent + "autoDismissKeyboard: " + normalize_yaml_scalar_value(child_map["autoDismissKeyboard"]))
        if external_context and value and "mode" not in child_map:
            result.append(child_indent + "mode: " + yaml_text("replace"))
        elif "mode" in child_map:
            result.append(child_indent + "mode: " + yaml_text(child_map["mode"]))
        if external_context and value and "mode" in child_map and child_map.get("mode") != "replace":
            changes.append("外部文件选择器输入强制设置 mode: replace")
        for key, raw_value, child in children:
            if key in emitted or key in ("autoDismissKeyboard", "mode"):
                continue
            result.append(child)
        lookahead = "\n".join(lines[j:j + 6])
        has_adb_input = ("input text " + adb_input_text(value)) in lookahead if value else False
        has_submit = "aiKeyboardPress:" in lookahead or "input keyevent 66" in lookahead or re.search(r"-\s+aiTap\s*:\s*[\"']?搜索", lookahead)
        if has_adb_input and value and not needs_external_fallback:
            skip_pattern = "input text " + adb_input_text(value)
            k = j
            while k < len(lines) and k < j + 4:
                if skip_pattern in lines[k]:
                    changes.append("移除默认 adb input text 兜底，避免 aiInput 成功后重复输入")
                    k += 1
                    if k < len(lines) and re.match(r"^\s*-\s+sleep\s*:\s*\d+\s*$", lines[k]):
                        k += 1
                    j = k
                    break
                if re.match(r"^\s*-\s+sleep\s*:\s*\d+\s*$", lines[k]):
                    k += 1
                    continue
                break
        if needs_external_fallback and value and not has_adb_input:
            result.append(indent + "- sleep: 300")
            result.append(indent + "- runAdbShell: " + yaml_text("input text " + adb_input_text(value)))
            result.append(indent + "- sleep: 300")
            changes.append("日志明确显示输入失败，才增加 adb input text 兜底")
        if is_search_input and not has_submit:
            result.append(indent + "- sleep: 300")
            result.append(indent + "- aiKeyboardPress: " + yaml_text(locate or "当前页面的搜索输入框或文本输入框"))
            result.append(child_indent + "keyName: " + yaml_text("Enter"))
            changes.append("搜索类输入后增加 Enter 提交，避免只输入不触发搜索")
        idx = j
    if not changes:
        return block, []
    return "\n".join(result).rstrip(), list(dict.fromkeys(changes))


# ---------------------------------------------------------------------------
# normalize_horizontal_icon_scrolls_in_task_block
# ---------------------------------------------------------------------------

def normalize_horizontal_icon_scrolls_in_task_block(block, evidence_text=""):
    """将横向 icon 区域自然语言滑动幂等规范为一次官方 aiScroll。"""
    if not block:
        return block, []
    lines = block.splitlines()
    result = []
    changes = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        m_ai = re.match(r"^(\s*)-\s+(ai|aiAction|aiAct)\s*:\s*(.+?)\s*$", line)
        if m_ai:
            indent, key, raw_text = m_ai.groups()
            text = strip_yaml_quotes(raw_text)
            horizontal_hint = any(word in text for word in ("横向", "水平", "左划", "右划", "向左滑", "向右滑", "滑动"))
            icon_hint = any(word in text for word in ("icon", "图标", "入口", "我的学习", "功能"))
            if horizontal_hint and icon_hint:
                target = "我的学习下方的横向功能 icon 列表区域"
                if "我的学习" not in text:
                    target = "当前页面中的横向功能 icon 列表区域"
                result.append(indent + "- aiScroll: " + yaml_text(target + "，只滚动该横向列表，不要滚动整个页面"))
                result.append(indent + "  scrollType: " + yaml_text("singleAction"))
                result.append(indent + "  direction: " + yaml_text("right"))
                result.append(indent + "  distance: 400")
                result.append(indent + "- sleep: 500")
                changes.append("将横向 icon 区域自然语言滑动改为一次官方 aiScroll singleAction + direction:right + distance:400")
                idx += 1
                continue
        m_scroll = re.match(r"^(\s*)-\s+aiScroll\s*:\s*(.+?)\s*$", line)
        if not m_scroll:
            result.append(line)
            idx += 1
            continue
        indent, raw_target = m_scroll.groups()
        target = strip_yaml_quotes(raw_target)
        children = []
        j = idx + 1
        while j < len(lines):
            child = lines[j]
            if re.match(r"^\s*-\s+[A-Za-z][\w]*\s*:", child):
                break
            children.append(child)
            j += 1
        target_hint = any(word in target for word in ("横向", "水平", "icon", "图标", "我的学习", "功能"))
        if target_hint:
            child_text = "\n".join(children)
            already_normalized = (
                re.search(r"\bscrollType\s*:\s*['\"]?singleAction", child_text)
                and re.search(r"\bdirection\s*:\s*['\"]?right", child_text)
                and re.search(r"\bdistance\s*:\s*400\b", child_text)
            )
            if already_normalized:
                result.append(line)
                result.extend(children)
                idx = j
                continue
            result.append(indent + "- aiScroll: " + yaml_text(target or "当前页面中的横向功能 icon 列表区域"))
            result.append(indent + "  scrollType: " + yaml_text("singleAction"))
            result.append(indent + "  direction: " + yaml_text("right"))
            result.append(indent + "  distance: 400")
            changes.append("将横向 icon/功能区滑动规范为一次 singleAction + direction:right + distance:400")
            idx = j
            continue
        result.append(line)
        result.extend(children)
        idx = j
    if not changes:
        return block, []
    return "\n".join(result).rstrip(), list(dict.fromkeys(changes))


# ---------------------------------------------------------------------------
# normalize_terminate_to_force_stop
# ---------------------------------------------------------------------------

def normalize_terminate_to_force_stop(block, app_package=None):
    """将 terminate 改为 adb force-stop。"""
    lines = (block or "").splitlines()
    if not lines:
        return block, []
    changes = []
    result = []
    for line in lines:
        m = re.match(r"^(\s*)-\s+terminate\s*:\s*[\"']?([^\"'\s#]+)", line)
        if not m:
            result.append(line)
            continue
        indent, package = m.groups()
        package = (package or app_package or "").strip()
        if not package:
            result.append(line)
            continue
        result.append(indent + "- runAdbShell: " + yaml_text("am force-stop " + package))
        changes.append(f"将 terminate 改为 adb force-stop：{package}")
    if not changes:
        return block, []
    deduped = []
    for item in changes:
        if item not in deduped:
            deduped.append(item)
    return "\n".join(result).rstrip(), deduped


# ---------------------------------------------------------------------------
# normalize_redundant_short_sleeps_in_task_block
# ---------------------------------------------------------------------------

def normalize_redundant_short_sleeps_in_task_block(block):
    """移除 aiWaitFor/aiAssert/前置条件后的冗余短等待。"""
    if not block:
        return block, []
    lines = block.splitlines()
    result = []
    removed = {"wait": 0, "assert": 0, "precondition": 0}
    for idx, line in enumerate(lines):
        m = re.match(r"^\s*-\s+sleep\s*:\s*[\"']?(\d+)[\"']?\s*(?:#.*)?$", line)
        if not m or safe_int(m.group(1), 0) > 1000:
            result.append(line)
            continue
        prev_key, prev_text = previous_flow_item(lines, idx)
        if prev_key == "aiWaitFor":
            removed["wait"] += 1
            continue
        if prev_key == "aiAssert":
            removed["assert"] += 1
            continue
        if prev_key in ("ai", "aiAction", "aiAct") and prev_text.startswith("确认前置条件："):
            removed["precondition"] += 1
            continue
        result.append(line)
    messages = []
    if removed["wait"]:
        messages.append(f"移除 aiWaitFor 已完成后的冗余短等待 {removed['wait']} 处")
    if removed["assert"]:
        messages.append(f"移除断言已完成后的冗余短等待 {removed['assert']} 处")
    if removed["precondition"]:
        messages.append(f"移除前置条件确认后的冗余短等待 {removed['precondition']} 处")
    if not messages:
        return block, []
    return "\n".join(result).rstrip(), messages


# ---------------------------------------------------------------------------
# normalize_long_sleep_waits_in_task_block
# ---------------------------------------------------------------------------

def wait_condition_from_context(prev_key, next_key, next_text):
    """根据上下文推导等待条件。"""
    next_text = (next_text or "").strip()
    if next_key in ("aiAction", "aiAssert") and next_text.startswith("验证："):
        return next_text.replace("验证：", "", 1).strip()
    if next_key == "aiTap" and next_text:
        return f"页面加载完成，并且可以继续执行下一步：{next_text}"
    if next_key in ("aiAction", "aiAssert") and next_text:
        return f"页面状态已满足下一步操作需要：{next_text}"
    if prev_key == "launch":
        return "App 已启动并且首页或当前目标页面加载完成"
    return "页面加载完成且没有明显加载中状态"


def normalize_long_sleep_waits_in_task_block(block):
    """将过长固定 sleep 转为条件等待 aiWaitFor。"""
    if not block:
        return block, []
    lines = block.splitlines()
    changed = 0
    new_lines = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        m = re.match(r"^(\s*-\s+sleep\s*:\s*)([\"']?)(\d+)([\"']?)(\s*(?:#.*)?)$", line)
        if not m:
            new_lines.append(line)
            idx += 1
            continue
        value = safe_int(m.group(3), 0)
        if value <= LONG_SLEEP_TO_WAITFOR_MS:
            new_lines.append(line)
            idx += 1
            continue
        prev_key = previous_flow_key(lines, idx)
        next_key, next_text = next_flow_item(lines, idx)
        if prev_key == "terminate":
            new_lines.append(f"{m.group(1)}{MAX_TERMINATE_SLEEP_MS}{m.group(5)}")
            idx += 1
            changed += 1
            continue
        indent = re.match(r"^(\s*)", line).group(1)
        condition = wait_condition_from_context(prev_key, next_key, next_text)
        new_lines.append(indent + "- aiWaitFor: " + yaml_text(condition))
        new_lines.append(indent + "  timeout: " + str(min(value, MAX_WAITFOR_TIMEOUT_MS)))
        changed += 1
        idx += 1
    if not changed:
        return block, []
    return "\n".join(new_lines).rstrip(), [f"将过长固定 sleep 转为条件等待 aiWaitFor {changed} 处"]


# ---------------------------------------------------------------------------
# normalize_waitfor_timeouts_in_task_block
# ---------------------------------------------------------------------------

def normalize_waitfor_timeouts_in_task_block(block):
    """修正 aiWaitFor timeout 过长。"""
    if not block:
        return block, []
    lines = block.splitlines()
    capped = 0
    for idx, line in enumerate(lines):
        m = re.match(r"^(\s*timeout\s*:\s*)(\d+)(\s*(?:#.*)?)$", line)
        if not m:
            continue
        value = safe_int(m.group(2), 0)
        prev = "\n".join(lines[max(0, idx - 2):idx]).lower()
        if "aiwaitfor" not in prev:
            continue
        if value > MAX_WAITFOR_TIMEOUT_MS:
            lines[idx] = f"{m.group(1)}{MAX_WAITFOR_TIMEOUT_MS}{m.group(3)}"
            capped += 1
    changes = []
    if capped:
        changes.append(f"将过长 aiWaitFor timeout 压到 {MAX_WAITFOR_TIMEOUT_MS}ms {capped} 处")
    if not changes:
        return block, []
    return "\n".join(lines).rstrip(), changes


# ---------------------------------------------------------------------------
# loading/model/combo wait normalize helpers
# ---------------------------------------------------------------------------

def loading_wait_timeout_for_context(text):
    """根据上下文推断加载等待超时。"""
    text = str(text or "")
    if any(word in text for word in ("保存成功", "已保存", "保存完成", "导出成功", "下载完成", "结果提示", "失败提示", "权限失败")):
        return 15000
    if "百度网盘" in text and any(word in text for word in ("入口", "文案", "展示", "可见", "可点击", "按钮", "同级", "排序", "布局")):
        return 15000
    if any(word in text for word in ("入口", "文案", "展示", "可见", "可点击", "按钮", "同级", "排序", "布局")) and not any(word in text for word in ("上传中", "导入中", "生成中", "处理中", "下载中")):
        return 15000
    if any(word in text for word in ("切片", "模型处理", "模型生成", "生成模型", "AI评估", "100%", "100.0%")):
        return 180000
    if "百度网盘" in text and any(word in text for word in ("文件", "授权", "登录", "第三方", "跳转", "返回")):
        return 60000
    if any(word in text for word in ("上传中", "正在上传", "上传完成", "导入中", "正在导入", "导入完成", "文件选择", "选择文件", "加载到")):
        return 120000
    if any(word in text for word in ("下一步", "去打印", "确认打印", "检查无误", "可点击", "按钮变为可点击")):
        return 15000
    if any(word in text for word in ("列表", "结果", "空态", "详情页", "页面标题")):
        return 12000
    return 12000


def model_processing_context_from_task_block(block):
    """判断 task 块是否为模型处理上下文。"""
    text_parts = []
    for line in (block or "").splitlines():
        stripped = line.strip()
        if re.match(r"^-\s+aiWaitFor\s*:", stripped) and "模型处理进度" in stripped:
            continue
        if stripped.startswith("timeout:"):
            continue
        text_parts.append(stripped)
    text = "\n".join(text_parts)
    model_terms = ("3D", "3d", "模型", "建模", "切片", "stl", ".stl", "obj", ".obj", "关节龙", "模型导入", "模型库")
    non_model_terms = ("错题", "错题本", "文档", "PDF", "Word", "照片", "相册", "扫描", "复印", "证件", "格式转换", "基础打印", "试卷", "题目", "数学", "学习", "2D", "2d")
    model_score = sum(1 for word in model_terms if word in text)
    non_model_score = sum(1 for word in non_model_terms if word in text)
    if any(word in text for word in ("模型处理", "切片完成", "模型加载", "3D打印", "模型导入", ".stl", ".obj", "stl", "obj")):
        model_score += 2
    return model_score > 0 and model_score >= non_model_score


def normalize_inappropriate_model_processing_waits_in_task_block(block):
    """将非模型上下文中的"模型处理进度"等待改为更精准的等待。"""
    if not block or model_processing_context_from_task_block(block):
        return block, []
    lines = block.splitlines()
    result = []
    changes = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        m = re.match(r"^(\s*)-\s+aiWaitFor\s*:\s*(.+?)\s*$", line)
        if not m:
            result.append(line)
            idx += 1
            continue
        indent, raw_condition = m.groups()
        condition = strip_yaml_quotes(raw_condition)
        if "模型处理进度" not in condition and "模型处理" not in condition:
            result.append(line)
            idx += 1
            continue
        if "立即打印" in condition:
            new_condition = "页面已完成打印前准备，并出现可点击的「立即打印」按钮"
        elif "确认打印" in condition:
            new_condition = "页面出现打印确认弹窗或可点击的「确认打印」按钮"
        else:
            new_condition = "页面已完成加载，并出现目标按钮或可继续操作"
        result.append(indent + "- aiWaitFor: " + yaml_text(new_condition))
        idx += 1
        timeout_seen = False
        desired_timeout = loading_wait_timeout_for_context(new_condition)
        while idx < len(lines):
            child = lines[idx]
            if re.match(r"^\s*-\s+[A-Za-z][\w]*\s*:", child):
                break
            tm = re.match(r"^(\s*timeout\s*:\s*)(\d+)(\s*(?:#.*)?)$", child)
            if tm:
                timeout_seen = True
                result.append(f"{tm.group(1)}{desired_timeout}{tm.group(3)}")
            else:
                result.append(child)
            idx += 1
        if not timeout_seen:
            result.append(indent + "  timeout: " + str(desired_timeout))
        changes.append('将非模型/2D打印链路中的“模型处理进度”等待改为按钮或确认弹窗等待')
    if not changes:
        return block, []
    return "\n".join(result).rstrip(), list(dict.fromkeys(changes))


def normalize_business_loading_waits_in_task_block(block):
    """为业务加载等待补充/调整 timeout。"""
    if not block:
        return block, []
    lines = block.splitlines()
    result = []
    changes = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        m = re.match(r"^(\s*)-\s+aiWaitFor\s*:\s*(.+?)\s*$", line)
        if not m:
            result.append(line)
            idx += 1
            continue
        indent, raw_condition = m.groups()
        condition = strip_yaml_quotes(raw_condition)
        children = []
        j = idx + 1
        while j < len(lines):
            child = lines[j]
            if re.match(r"^\s*-\s+[A-Za-z][\w]*\s*:", child):
                break
            children.append(child)
            j += 1
        lookahead = "\n".join(lines[j:j + 4])
        context = "\n".join([condition, lookahead])
        next_key, next_text = "", ""
        for look_line in lines[j:j + 4]:
            nm = re.match(r"^\s*-\s+([A-Za-z][\w]*)\s*:\s*(.+?)\s*$", look_line)
            if nm:
                next_key, next_text = nm.group(1), strip_yaml_quotes(nm.group(2))
                break
        prev_window = "\n".join(lines[max(0, idx - 5):idx])
        next_mentions_print_progress = any(word in context for word in ("进度", "100%", "100.0%", "确认打印", "取消打印"))
        if (
            model_processing_context_from_task_block(block)
            and next_mentions_print_progress
            and condition == "页面加载完成且没有明显加载中状态"
            and re.search(r"aiTap\s*:\s*[\"']?下一步[\"']?", prev_window)
            and "确认打印" in context
        ):
            condition = "模型处理进度已加载到 100%，并且页面出现可点击的「确认打印」按钮"
            changes.append("将打印/模型处理短等待改为等待进度 100% 和确认打印按钮")
        timeout_context = context
        if next_key in ("aiTap", "ai", "aiAction", "aiAct") and next_text:
            timeout_context = "\n".join([condition, next_text])
        desired_timeout = min(loading_wait_timeout_for_context(timeout_context), MAX_WAITFOR_TIMEOUT_MS)
        result.append(indent + "- aiWaitFor: " + yaml_text(condition))
        timeout_seen = False
        for child in children:
            tm = re.match(r"^(\s*timeout\s*:\s*)(\d+)(\s*(?:#.*)?)$", child)
            if tm:
                timeout_seen = True
                old_timeout = safe_int(tm.group(2), 0)
                if desired_timeout <= 15000:
                    normalized_timeout = min(old_timeout or desired_timeout, desired_timeout)
                else:
                    normalized_timeout = min(max(old_timeout, desired_timeout), MAX_WAITFOR_TIMEOUT_MS)
                if normalized_timeout != old_timeout:
                    result.append(f"{tm.group(1)}{normalized_timeout}{tm.group(3)}")
                    if old_timeout < desired_timeout:
                        changes.append(f"将业务加载等待 timeout 从 {old_timeout}ms 提升到 {normalized_timeout}ms")
                    else:
                        changes.append(f"将业务加载等待 timeout 从 {old_timeout}ms 压到 {normalized_timeout}ms")
                else:
                    result.append(child)
            else:
                result.append(child)
        if not timeout_seen and desired_timeout > 30000:
            result.append(indent + "  timeout: " + str(desired_timeout))
            changes.append(f"为业务加载等待补充 timeout: {desired_timeout}")
        idx = j
    if not changes:
        return block, []
    return "\n".join(result).rstrip(), list(dict.fromkeys(changes))


def normalize_combined_wait_click_actions_in_task_block(block):
    """将"等待进度并点击确认打印"混合动作拆分。"""
    if not block:
        return block, []
    lines = block.splitlines()
    result = []
    changes = []
    for line in lines:
        m = re.match(r"^(\s*)-\s+(ai|aiAction|aiAct)\s*:\s*(.+?)\s*$", line)
        if not m:
            result.append(line)
            continue
        indent, key, raw_text = m.groups()
        text = strip_yaml_quotes(raw_text)
        if "等待" in text and "进度" in text and "点击" in text and "确认打印" in text:
            result.append(indent + "- aiTap: " + yaml_text("确认打印"))
            changes.append('将"等待进度并点击确认打印"的混合 ai 动作拆成等待后的明确 aiTap')
            continue
        result.append(line)
    if not changes:
        return block, []
    return "\n".join(result).rstrip(), list(dict.fromkeys(changes))


# ---------------------------------------------------------------------------
# normalize_task_block_runtime_guards — 主守卫函数
# ---------------------------------------------------------------------------

def normalize_task_block_runtime_guards(block, app_package=None, evidence_text="", platform="android"):
    """对单条 task 块应用运行时守卫规范化。"""
    block = normalize_unclosed_yaml_quotes(block or "").strip("\n")
    if not block:
        return block, []
    block, syntax_changes = normalize_flowitem_syntax_in_task_block(block)
    block, input_changes = normalize_input_actions_in_task_block(block)
    block, search_submit_changes = normalize_search_input_submit_in_task_block(block, evidence_text=evidence_text)
    block, horizontal_scroll_changes = normalize_horizontal_icon_scrolls_in_task_block(block, evidence_text=evidence_text)
    terminate_changes = []
    if platform == "android":
        block, terminate_changes = normalize_terminate_to_force_stop(block, app_package=app_package)
    lines = block.splitlines()
    name_idx = next((idx for idx, line in enumerate(lines) if re.match(r"^\s*-\s+name:\s*", line)), None)
    flow_idx = next((idx for idx, line in enumerate(lines) if re.match(r"^\s*flow:\s*$", line)), None)
    if name_idx is None or flow_idx is None:
        return block, syntax_changes
    indent = re.match(r"^(\s*)", lines[flow_idx]).group(1) + "  "
    changes = list(syntax_changes) + input_changes + search_submit_changes + horizontal_scroll_changes + terminate_changes
    flow_item_indices = [
        idx for idx in range(flow_idx + 1, len(lines))
        if re.match(r"^\s*-\s+([A-Za-z][\w]*)\s*:", lines[idx])
    ]
    first_flow_idx = flow_item_indices[0] if flow_item_indices else None
    first_flow_key = ""
    if first_flow_idx is not None:
        first_flow_key = re.match(r"^\s*-\s+([A-Za-z][\w]*)\s*:", lines[first_flow_idx]).group(1)

    if platform != "android":
        text = "\n".join(lines).rstrip()
        changes = list(syntax_changes) + input_changes + search_submit_changes + terminate_changes
        text, sleep_changes = normalize_long_sleep_waits_in_task_block(text)
        changes.extend(sleep_changes)
        text, combined_action_changes = normalize_combined_wait_click_actions_in_task_block(text)
        changes.extend(combined_action_changes)
        text, inappropriate_model_wait_changes = normalize_inappropriate_model_processing_waits_in_task_block(text)
        changes.extend(inappropriate_model_wait_changes)
        text, business_wait_changes = normalize_business_loading_waits_in_task_block(text)
        changes.extend(business_wait_changes)
        text, timeout_changes = normalize_waitfor_timeouts_in_task_block(text)
        changes.extend(timeout_changes)
        text, redundant_sleep_changes = normalize_redundant_short_sleeps_in_task_block(text)
        changes.extend(redundant_sleep_changes)
        text, meta_changes = insert_baseline_comments_into_task_block(text)
        changes.extend(meta_changes)
        return text, changes

    if not task_block_has_key(block, "launch") or first_flow_key not in ("runAdbShell", "launch"):
        insert = launch_guard_flow(indent, app_package, evidence_text)
        lines = lines[:flow_idx + 1] + insert + lines[flow_idx + 1:]
        changes.append("补充前置启动 App，并先回到手机主页脱离系统文件页/外部页面")
        if runtime_guard_mode() == "strict" or evidence_needs_popup_guard(evidence_text):
            changes.append("补充弹窗/浮层兜底处理")
    elif first_flow_key == "launch":
        launch_package = (app_package or extract_app_package_from_yaml(block) or "").strip()
        insert = dynamic_recent_tasks_cleanup_flow(indent) if runtime_guard_mode() == "strict" else external_activity_cleanup_flow(indent)
        if launch_package:
            insert += [
                indent + "- runAdbShell: " + yaml_text("am force-stop " + launch_package),
                indent + "- sleep: 1500",
            ]
        lines = lines[:first_flow_idx] + insert + lines[first_flow_idx:]
        changes.append("补充启动前清理 App 状态，并回到手机主页脱离系统文件页/外部页面")
    elif (runtime_guard_mode() == "strict" or evidence_needs_popup_guard(evidence_text)) and not task_block_has_popup_guard(block):
        insert = [
            indent + "- ai: " + yaml_text("如果出现权限弹窗、升级弹窗、广告弹窗、活动弹窗或引导浮层，优先点击允许、知道了、稍后、跳过、关闭或右上角关闭按钮；没有弹窗就继续"),
            indent + "- sleep: 1000",
        ]
        lines = lines[:flow_idx + 1] + insert + lines[flow_idx + 1:]
        changes.append("补充弹窗/浮层兜底处理")
    text = "\n".join(lines).rstrip()
    if "aiTap:" not in text and "aiAction:" not in text and "ai:" not in text and "aiAct:" not in text and "aiAssert:" not in text and "aiWaitFor:" not in text:
        text = text + "\n" + indent + "- aiAssert: " + yaml_text("当前 App 页面已正常展示")
        text = text + "\n" + indent + "- sleep: 500"
        changes.append("补充空 flow 的基础页面验证步骤")
    if (
        (runtime_guard_mode() == "strict" or evidence_needs_popup_guard(evidence_text))
        and not task_block_ends_with_key(text, "terminate")
        and not task_block_ends_with_force_stop(text)
    ):
        text = text + "\n" + "\n".join(cleanup_guard_flow(indent, app_package, evidence_text))
        changes.append("严格/弹窗场景补充后置 force-stop App 和退出弹窗兜底")
    text, sleep_changes = normalize_long_sleep_waits_in_task_block(text)
    changes.extend(sleep_changes)
    text, combined_action_changes = normalize_combined_wait_click_actions_in_task_block(text)
    changes.extend(combined_action_changes)
    text, inappropriate_model_wait_changes = normalize_inappropriate_model_processing_waits_in_task_block(text)
    changes.extend(inappropriate_model_wait_changes)
    text, business_wait_changes = normalize_business_loading_waits_in_task_block(text)
    changes.extend(business_wait_changes)
    text, timeout_changes = normalize_waitfor_timeouts_in_task_block(text)
    changes.extend(timeout_changes)
    text, redundant_sleep_changes = normalize_redundant_short_sleeps_in_task_block(text)
    changes.extend(redundant_sleep_changes)
    text, meta_changes = insert_baseline_comments_into_task_block(text)
    changes.extend(meta_changes)
    return text, changes


# ---------------------------------------------------------------------------
# normalize_yaml_runtime_guards — 整体 YAML 守卫入口
# ---------------------------------------------------------------------------

def normalize_yaml_runtime_guards(yaml_text, app_package=None, evidence_text=""):
    """对整个 YAML 文本应用运行时守卫规范化。"""
    text = ensure_midscene_platform_root(
        remove_empty_midscene_platform_roots(normalize_full_yaml_structure(yaml_text or "")),
        platform="android",
    )
    platform = detect_yaml_platform(text)
    names = yaml_task_names(text)
    changes = []
    for name in names:
        try:
            info = find_yaml_task_block(text, name)
            new_block, block_changes = normalize_task_block_runtime_guards(info["block"], app_package, evidence_text, platform=platform)
            if block_changes and new_block.strip() != info["block"].strip():
                text = replace_yaml_task_block(text, info, new_block)
                changes.extend([f"{name}：{item}" for item in block_changes])
        except Exception:
            continue
    return text.rstrip() + "\n", changes


# ---------------------------------------------------------------------------
# cases_to_midscene_yaml
# ---------------------------------------------------------------------------

def cases_to_midscene_yaml(payload: Any, app_package: str = "") -> Tuple[str, str]:
    """转换用例为 Midscene YAML。

    Source: midscene-upload.py 行 1252-1268。
    """
    normalized = (
        payload
        if isinstance(payload, dict) and payload.get("_automation_ready")
        else split_automation_ready_cases(payload)
    )
    chunks = [
        "# generated by MidScene Task Manager",
        "android:",
        "  tasks:",
    ]
    for index, case in enumerate(normalized["cases"], start=1):
        if isinstance(case, dict):
            if app_package and not case.get("app_package") and not case.get("appPackage"):
                case = dict(case)
                case["app_package"] = app_package
            chunks.append(case_to_task_yaml(case, indent="    ", case_index=index))
    rendered = "\n".join(chunks) + "\n"
    rendered, _ = normalize_yaml_runtime_guards(rendered, app_package=app_package)
    return normalized["title"], rendered


def cases_to_separate_midscene_yamls(payload: Any, app_package: str = "", base_file: str = "") -> Tuple[str, List[dict]]:
    """转换用例为多个 Midscene YAML。

    新需求生成默认按自动化用例拆分文件，避免一个 YAML 中塞入过长
    ``android.tasks`` 列表。每个返回项只包含一个 task，便于评审、执行和后续维护。
    """
    normalized = (
        payload
        if isinstance(payload, dict) and payload.get("_automation_ready")
        else split_automation_ready_cases(payload)
    )
    title = normalized.get("title") or "测试用例"
    used_names = set()
    files: List[dict] = []
    base_slug = slug_for_file(os.path.splitext(str(base_file or ""))[0] or title or "case")

    for index, raw_case in enumerate(normalized.get("cases") or [], start=1):
        if not isinstance(raw_case, dict):
            continue
        case = dict(raw_case)
        if app_package and not case.get("app_package") and not case.get("appPackage"):
            case["app_package"] = app_package
        case_title = str(case.get("title") or case.get("name") or f"用例{index}").strip()
        case_slug = slug_for_file(case_title) or f"case-{index:02d}"
        file_name = clean_filename(f"{index:02d}-{case_slug}.yaml")
        if file_name in used_names:
            file_name = clean_filename(f"{index:02d}-{base_slug}-{case_slug}.yaml")
        suffix = 2
        unique_name = file_name
        while unique_name in used_names:
            unique_name = clean_filename(f"{index:02d}-{case_slug}-{suffix}.yaml")
            suffix += 1
        used_names.add(unique_name)

        chunks = [
            "# generated by MidScene Task Manager",
            "android:",
            "  tasks:",
            case_to_task_yaml(case, indent="    ", case_index=index),
        ]
        rendered = "\n".join(chunks) + "\n"
        rendered, _ = normalize_yaml_runtime_guards(rendered, app_package=app_package)
        files.append({
            "index": index,
            "file": unique_name,
            "title": case_title,
            "case_id": first_non_empty(case.get("case_id"), case.get("id"), f"TC-{index:03d}"),
            "priority": case_priority(case),
            "smoke": is_smoke_case(case),
            "case": case,
            "content": rendered,
        })

    if not files:
        raise ValueError("没有可转换为自动化 YAML 的用例：请补充可执行 UI 步骤")
    return title, files


# ---------------------------------------------------------------------------
# Midscene flow 校验
# ---------------------------------------------------------------------------

def _strip_yaml_comments(text: str) -> str:
    """去掉行尾注释，保留字符串中的 ``#``。"""
    cleaned: List[str] = []
    for line in (text or "").splitlines():
        in_single = False
        in_double = False
        cut = len(line)
        for idx, ch in enumerate(line):
            if ch == "'" and not in_double:
                in_single = not in_single
            elif ch == '"' and not in_single:
                in_double = not in_double
            elif ch == "#" and not in_single and not in_double:
                if idx == 0 or line[idx - 1].isspace():
                    cut = idx
                    break
        cleaned.append(line[:cut].rstrip())
    return "\n".join(cleaned)


def validate_yaml(content: str) -> List[str]:
    """通用 YAML 校验：格式合法性 + Midscene flow 规则。"""
    warnings: List[str] = []
    text = (content or "").strip()
    if not text:
        warnings.append("YAML 内容为空")
        return warnings
    if _pyyaml is not None:
        try:
            _pyyaml.safe_load(text)
        except Exception as e:
            warnings.append(f"YAML 格式错误: {e}")
            return warnings
    warnings.extend(validate_midscene_flow(content))
    return warnings


def extract_midscene_tasks(parsed):
    """Return ``(platform, tasks)`` from supported Midscene YAML layouts."""
    if not isinstance(parsed, dict):
        return "", []
    if isinstance(parsed.get("tasks"), list):
        return "root", parsed.get("tasks") or []
    for platform in ("android", "ios"):
        node = parsed.get(platform)
        if isinstance(node, dict):
            tasks = node.get("tasks")
            if isinstance(tasks, list):
                return platform, tasks
    return "", []


def _yaml_action_value_blank(value):
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    return False


def _yaml_ai_tap_has_ambiguous_target(value):
    """Return true when one tap prompt asks Midscene to choose among targets."""
    text = str(value or "").strip()
    if not text:
        return False
    compact = re.sub(r"\s+", "", text)
    choice_markers = ("任一", "任意", "任选", "其中一个", "其中任意一个")
    if any(marker in compact for marker in choice_markers):
        return True
    quoted_targets = {
        item.strip()
        for item in re.findall(r"[「“]([^」”]{1,80})[」”]", text)
        if item.strip()
    }
    return len(quoted_targets) >= 2 and ("或" in compact or "/" in compact)


def validate_midscene_yaml_executability(text):
    """Strong executable YAML validation shared by Agent, repair and Sonic."""
    yaml_text_value = str(text or "")
    if not yaml_text_value.strip():
        return {"ok": False, "platform": "", "taskCount": 0, "issues": ["YAML 内容为空"], "riskHits": []}
    risk_hits = [kw for kw in HIGH_RISK_KEYWORDS if kw and kw in yaml_text_value]
    if _pyyaml is None:
        return {
            "ok": False,
            "platform": "",
            "taskCount": 0,
            "issues": ["服务端未安装 PyYAML，不能执行 YAML 强校验"],
            "riskHits": risk_hits,
        }
    try:
        parsed = _pyyaml.safe_load(yaml_text_value)
    except Exception as exc:
        return {"ok": False, "platform": "", "taskCount": 0, "issues": [f"YAML 解析失败：{exc}"], "riskHits": risk_hits}
    if not isinstance(parsed, dict):
        return {"ok": False, "platform": "", "taskCount": 0, "issues": ["YAML 根节点必须是对象"], "riskHits": risk_hits}

    platform, tasks = extract_midscene_tasks(parsed)
    empty_platforms = [name for name in ("android", "ios") if name in parsed and parsed.get(name) is None]
    if empty_platforms:
        return {
            "ok": False,
            "platform": platform,
            "taskCount": len(tasks) if isinstance(tasks, list) else 0,
            "issues": [f"顶层 {name}: null 会导致 Runner 注入设备后出现重复平台声明，请先移除" for name in empty_platforms],
            "riskHits": risk_hits,
        }
    if not platform:
        return {"ok": False, "platform": "", "taskCount": 0, "issues": ["必须包含 root.tasks、android.tasks 或 ios.tasks"], "riskHits": risk_hits}
    if not isinstance(tasks, list) or not tasks:
        return {"ok": False, "platform": platform, "taskCount": 0, "issues": [f"{platform}.tasks 不能为空"], "riskHits": risk_hits}

    issues = []
    for idx, task in enumerate(tasks, 1):
        if not isinstance(task, dict):
            issues.append(f"tasks[{idx}] 必须是对象")
            continue
        if not str(task.get("name") or "").strip():
            issues.append(f"tasks[{idx}] 缺少 name")
        flow = task.get("flow")
        if not isinstance(flow, list) or not flow:
            issues.append(f"tasks[{idx}].flow 不能为空")
            continue
        for fidx, item in enumerate(flow, 1):
            if not isinstance(item, dict) or not item:
                issues.append(f"tasks[{idx}].flow[{fidx}] 必须是非空对象")
                continue
            action_keys = [key for key in item.keys() if key in MIDSCENE_FLOW_ACTIONS]
            if not action_keys:
                issues.append(f"tasks[{idx}].flow[{fidx}] 存在不支持动作：{sorted(item.keys())}")
                continue
            if len(action_keys) > 1:
                issues.append(f"tasks[{idx}].flow[{fidx}] 同时声明多个动作：{action_keys}")
            for action in action_keys:
                action_value = item.get(action)
                issues.extend(validate_midscene_action_parameters(
                    action,
                    item,
                    f"tasks[{idx}].flow[{fidx}]",
                ))
                if action == "runAdbShell" and isinstance(action_value, str) and re.search(r"\$\{[^}]+\}", action_value):
                    issues.append(
                        f"tasks[{idx}].flow[{fidx}] runAdbShell 包含 `${{...}}` shell 参数展开，"
                        "Midscene 会先按环境变量插值解析；请改用 cut/awk 等不含 `${}` 的写法"
                    )
                if isinstance(action_value, str):
                    prefix_match = FLOW_ACTION_PREFIX_RE.match(action_value)
                    if prefix_match and prefix_match.group(1) in MIDSCENE_FLOW_ACTIONS:
                        prefix = prefix_match.group(1)
                        if prefix == action:
                            issues.append(f"tasks[{idx}].flow[{fidx}] {action} 内容重复包含动作前缀 `{prefix}:`")
                        else:
                            issues.append(
                                f"tasks[{idx}].flow[{fidx}] 声明为 {action}，但内容前缀是 `{prefix}:`，"
                                "会导致动作语义错误"
                            )
                if action in ("ai", "aiAct", "aiAction", "aiTap", "aiAssert", "aiWaitFor", "aiInput") and _yaml_action_value_blank(item.get(action)):
                    issues.append(f"tasks[{idx}].flow[{fidx}] {action} 内容不能为空")
                if action == "aiTap" and _yaml_ai_tap_has_ambiguous_target(action_value):
                    issues.append(
                        f"tasks[{idx}].flow[{fidx}] aiTap 包含多个备选目标；"
                        "请根据当前页真实可见文字拆成单一点击，多合法终态只能用于 aiWaitFor/aiAssert"
                    )
                if action == "aiInput" and _yaml_action_value_blank(item.get("value")):
                    issues.append(f"tasks[{idx}].flow[{fidx}] aiInput 必须包含 value")
    return {
        "ok": not issues,
        "platform": platform,
        "taskCount": len(tasks),
        "issues": issues,
        "riskHits": risk_hits,
    }


BAIDU_NETDISK_POST_CLICK_WAIT = (
    "百度网盘授权页、登录页、文件选择页、空状态页或提示页已打开，"
    "页面出现返回、搜索、确定、暂无数据、文件列表任一稳定信号"
)
BAIDU_NETDISK_POST_CLICK_ASSERT = (
    "点击百度网盘入口后进入百度网盘相关页面或出现可识别提示，"
    "未白屏、未闪退、未停留在原入口页"
)
BAIDU_NETDISK_POST_CLICK_TIMEOUT_MS = 60000


def _is_heavy_recent_cleanup_shell(text: str) -> bool:
    shell = str(text or "")
    compact = re.sub(r"\s+", "", shell).lower()
    if "inputkeyevent187" in compact:
        return True
    if "wmsize" in compact and "inputswipe" in compact:
        return True
    if shell.count("input swipe") >= 2:
        return True
    return any(word in shell for word in ("input keyevent 187", "wm size", "input swipe")) and len(shell) > 180


def _compact_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _is_baidu_netdisk_tap_prompt(text: str) -> bool:
    compact = _compact_text(text)
    if "百度网盘" not in compact:
        return False
    return any(word in compact for word in ("点击", "点按", "轻触", "进入", "打开", "选择", "入口", "导入"))


def _is_baidu_post_click_target_prompt(text: str) -> bool:
    compact = _compact_text(text)
    if "百度网盘" not in compact:
        return False
    return any(word in compact for word in ("授权", "登录", "文件选择", "文件列表", "暂无数据", "空状态", "确定", "搜索", "返回", "提示页", "相关页面"))


def _is_baidu_original_entry_prompt(text: str) -> bool:
    compact = _compact_text(text)
    if "百度网盘" not in compact:
        return False
    original_state_words = (
        "入口展示", "展示入口", "入口可见", "页面展示", "页面显示", "稳定显示",
        "可见性", "入口按钮可见", "导入方式", "首页展示", "首页的百度网盘入口",
        "文档打印首页的百度网盘入口", "普通照片打印页面展示", "普通证件照页面展示",
    )
    original_state = any(word in compact for word in original_state_words)
    mixed_post_click = original_state and any(word in compact for word in ("点击后进入", "授权", "登录", "文件选择", "流程"))
    return original_state or mixed_post_click


APP_BRAND_CONFLICT_RULES = {
    "com.xbxxhz.box": {
        "expected": ("小白学习打印", "小白学习"),
        "blocked": ("小白扫描王", "智小白3D", "3D打印", "3D 打印"),
        "label": "小白学习打印",
    },
    "com.kfb.model": {
        "expected": ("智小白3D", "3D打印", "3D 打印"),
        "blocked": ("小白学习打印", "小白扫描王"),
        "label": "智小白3D/3D 打印",
    },
}


def _app_brand_conflict_issues(app_package: str, compact_text: str, prefix: str) -> List[str]:
    package = str(app_package or "").strip()
    rule = APP_BRAND_CONFLICT_RULES.get(package)
    if not rule:
        return []
    issues: List[str] = []
    for blocked in rule.get("blocked") or ():
        blocked_compact = _compact_text(blocked)
        if blocked_compact and blocked_compact in compact_text:
            expected = rule.get("label") or "当前 App"
            issues.append(prefix + f"当前包名对应{expected}，首页等待/断言不能写成“{blocked}”")
    return issues


def _yaml_current_app_semantic_issues(yaml_text: str, *, app_package: str = "", module: str = "", file: str = "") -> List[str]:
    """Block YAML that is structurally valid but known to fail on the current App UI."""
    if _pyyaml is None:
        return []
    raw = str(yaml_text or "")
    scope_text = f"{app_package} {module} {file} {raw}"
    scope_compact = re.sub(r"\s+", "", scope_text).lower()
    effective_app_package = str(app_package or "").strip()
    if effective_app_package not in APP_BRAND_CONFLICT_RULES:
        if "com.xbxxhz.box" in scope_compact:
            effective_app_package = "com.xbxxhz.box"
        elif "com.kfb.model" in scope_compact:
            effective_app_package = "com.kfb.model"
    is_xiaobai_ai_model = (
        "com.kfb.model" in scope_compact
        or any(term in scope_compact for term in ("智小白3d", "ai建模", "图片建模", "文字建模", "语音创作", "标牌", "趣味印章", "涂鸦建模"))
    )
    is_xiaobai_scan = (
        "com.xbxxhz.box" in scope_compact
        or any(term in scope_compact for term in ("小白学习打印", "小白学习", "文档打印", "证件照", "照片打印", "扫描复印", "百度网盘"))
    )
    if not (is_xiaobai_ai_model or is_xiaobai_scan):
        return []
    try:
        parsed = _pyyaml.safe_load(raw)
    except Exception:
        return []
    _, tasks = extract_midscene_tasks(parsed)
    if not tasks:
        return []

    issues: List[str] = []

    def text_has(compact: str, *terms: str) -> bool:
        return any(str(term).lower() in compact for term in terms)

    for idx, task in enumerate(tasks, 1):
        if not isinstance(task, dict):
            continue
        name = str(task.get("name") or f"tasks[{idx}]").strip()
        flow = task.get("flow") if isinstance(task.get("flow"), list) else []
        fragments = [name]
        action_rows: List[Tuple[str, str]] = []
        for item in flow:
            if not isinstance(item, dict):
                continue
            for key, value in item.items():
                if key in MIDSCENE_FLOW_ACTIONS:
                    val = str(value or "")
                    action_rows.append((key, val))
                    fragments.append(val)
                elif key in ("value", "prompt", "description"):
                    fragments.append(str(value or ""))
        task_text = "\n".join(fragments)
        compact = re.sub(r"\s+", "", task_text).lower()
        prefix = f"{name}: "
        issues.extend(_app_brand_conflict_issues(effective_app_package, compact, prefix))

        if text_has(compact, "三种入口", "多入口") and text_has(compact, "开始创作", "跳转", "验证"):
            issues.append(prefix + "当前 App 入口已改版，旧版多入口验证不能直接下发 Runner")
        if text_has(compact, "文字输入") and text_has(compact, "首页", "三维创作", "开始创作", "入口"):
            issues.append(prefix + "当前首页没有旧版“文字输入”入口，需改为底部 AI建模 Tab/当前真机入口")
        if (
            text_has(compact, "大家都在做", "骨架屏", "缩放控件", "固定推荐", "推荐标题", "素材标题")
            and not text_has(compact, "或", "任一", "任意", "之一", "任意一个")
        ):
            issues.append(prefix + "包含动态推荐/加载瞬态/历史稿控件，不能作为自动化必过断言")
        if text_has(compact, "我是小魔法师"):
            issues.append(prefix + "欢迎态文案随版本变化，不能作为固定 wait/assert 信号")
        if text_has(compact, "标牌", "趣味印章", "涂鸦建模") and not text_has(compact, "横向", "滑动", "aiScroll"):
            issues.append(prefix + "横向功能入口缺少滑动步骤，真机上目标入口可能不可见")
        for action, value in action_rows:
            action_text = re.sub(r"\s+", "", value).lower()
            if action in ("aiTap", "aiWaitFor", "aiAssert") and text_has(action_text, "没有喜欢的", "不补充直接生成"):
                issues.append(prefix + "条件分支被写成固定动作；应改为 ai 条件处理或转待准备")
            if (
                action in ("aiTap", "aiWaitFor", "aiAssert")
                and text_has(action_text, "语音创作")
                and text_has(action_text, "长按输入", "录音", "麦克风", "权限", "直接说", "点击语音")
            ):
                issues.append(prefix + "语音录音链路依赖权限/麦克风/长按状态，默认不直接 Runner 执行")
        unstable_upload = (
            text_has(compact, "上传图片", "选择图片", "图片上传", "图片选择", "照片选择", "从相册选择", "图库选择", "系统相册选择")
            or (text_has(compact, "相机拍照") and text_has(compact, "拍照上传", "点击拍照", "确认使用照片", "使用照片"))
            or (
                text_has(compact, "相册")
                and text_has(compact, "上传图片", "选择图片", "图片上传", "图片选择", "照片选择", "从相册选择")
            )
        )
        if unstable_upload and not text_has(compact, "已准备测试图片", "固定测试图片", "测试图片已准备"):
            issues.append(prefix + "图片上传链路未声明固定测试图片，系统选择器/相册数据不稳定")
        if is_xiaobai_scan:
            after_baidu_tap = False
            for action, value in action_rows:
                if action in ("launch", "runAdbShell"):
                    after_baidu_tap = False
                if action == "aiTap" and _is_baidu_netdisk_tap_prompt(value):
                    after_baidu_tap = True
                    continue
                if after_baidu_tap and action in ("aiWaitFor", "aiAssert") and _is_baidu_original_entry_prompt(value):
                    issues.append(prefix + "点击百度网盘后仍在等待原业务页入口展示，应改为等待百度网盘授权/文件选择/空状态等跳转后信号")

    return list(dict.fromkeys(issues))


def dry_run_midscene_yaml(yaml_text: str = "", *, module: str = "", file: str = "", app_package: str = "") -> dict:
    """Mock Runner dry-run for YAML load/action/field validation.

    This does not touch any device. It verifies the same YAML text through the
    generic parser, Runner-oriented executable validator and the stricter action
    whitelist validator, then returns one consolidated result for Agent/UI use.
    """
    source = "inline"
    if not str(yaml_text or "").strip() and module and file:
        yaml_text = read_text_file(safe_join(TASK_DIR, module, clean_filename(file)), default="")
        source = "file"
    original = str(yaml_text or "")
    normalized, guard_changes = normalize_yaml_runtime_guards(original, app_package=app_package)
    yaml_check = validate_midscene_yaml(normalized)
    yaml_executability = validate_midscene_yaml_executability(normalized)
    yaml_static_validation = validate_yaml_static_executable(normalized)
    semantic_issues = _yaml_current_app_semantic_issues(normalized, app_package=app_package, module=module, file=file)
    ok = bool(
        yaml_check.get("ok")
        and yaml_executability.get("ok")
        and yaml_static_validation.get("ok")
        and not semantic_issues
    )
    errors = []
    errors.extend(str(item) for item in yaml_check.get("issues") or [] if str(item).strip())
    errors.extend(str(item) for item in yaml_executability.get("issues") or [] if str(item).strip())
    errors.extend(str(item) for item in yaml_static_validation.get("errors") or [] if str(item).strip())
    errors.extend(str(item) for item in semantic_issues if str(item).strip())
    warnings = []
    warnings.extend(str(item) for item in yaml_check.get("warnings") or [] if str(item).strip())
    warnings.extend(str(item) for item in yaml_static_validation.get("warnings") or [] if str(item).strip())
    return {
        "ok": ok,
        "mode": "mock_dry_run",
        "source": source,
        "runnerTouched": False,
        "deviceTouched": False,
        "module": module,
        "file": file,
        "executionLevel": yaml_static_validation.get("executionLevel") or ("executable" if ok else "draft"),
        "taskCount": yaml_executability.get("taskCount") or yaml_static_validation.get("taskCount") or 0,
        "errors": list(dict.fromkeys(errors)),
        "warnings": list(dict.fromkeys(warnings)),
        "normalizedChanged": normalized.strip() != original.strip(),
        "guardChanges": guard_changes,
        "yamlCheck": yaml_check,
        "yamlExecutability": yaml_executability,
        "yamlStaticValidation": yaml_static_validation,
        "semanticIssues": semantic_issues,
        "message": "dry-run 通过：YAML 可被平台规则加载" if ok else "dry-run 未通过：请先修复 YAML 结构或动作字段",
    }


def _yaml_static_repair_prompt(yaml_text, dry_run, *, title="", module="", file="", app_package=""):
    errors = dry_run.get("errors") or []
    warnings = dry_run.get("warnings") or []
    return f"""你是 Midscene YAML 静态可执行修复助手。只修复 YAML 结构和动作字段，不新增测试用例、不扩写步骤、不补业务断言。

要求：
1. 保持原有 task 数量、task 名称、业务路径和页面语义。
2. 只修复会导致解析/Runner 加载失败的问题，例如 YAML 顶层结构、android.tasks/ios.tasks、flow 数组、动作字段为空、不支持动作、同一步多个动作、明显缺失包名启动保护。
3. 不要引入现有 YAML 中没有的复杂动作；优先保持 aiWaitFor、aiTap、aiInput、aiAssert、runAdbShell、sleep、launch 等常见写法。
3.1 只有真实点击目标才能用 aiTap；检查/验证/是否展示/是否存在/页面可见/状态一致这类语义必须改成 aiWaitFor，不要用 aiTap。
4. 输出必须是 JSON 对象，字段为 analysis、changes、content；content 必须是完整 YAML 字符串。

上下文：
- 标题：{title or ""}
- 模块：{module or ""}
- 文件：{file or ""}
- App 包名：{app_package or ""}
- dry-run 错误：{json.dumps(errors[:12], ensure_ascii=False)}
- dry-run 警告：{json.dumps(warnings[:8], ensure_ascii=False)}

当前 YAML：
```yaml
{str(yaml_text or "")[:24000]}
```
"""


def _replace_step_action(step: dict, old_key: str, new_key: str, value: str, *, timeout: int = 0) -> None:
    original_value = step.pop(old_key, None)
    next_step = {new_key: value}
    if timeout:
        next_step["timeout"] = timeout
    for key, existing in list(step.items()):
        next_step[key] = existing
    step.clear()
    step.update(next_step)
    if original_value is not None and old_key == new_key:
        step[new_key] = value


def _repair_generated_broad_ai_step(step: dict) -> dict:
    """Normalize broad generated ai steps into one executable intention."""
    if not isinstance(step, dict):
        return {}
    action_key = next((key for key in ("ai", "aiAction", "aiAct") if key in step), "")
    if not action_key:
        return {}
    prompt = str(step.get(action_key) or "").strip()
    compact = _compact_text(prompt)
    if not compact:
        return {}

    replacement_key = ""
    replacement_prompt = ""
    timeout = DEFAULT_WAITFOR_TIMEOUT_MS
    if "文档导入入口区域" in compact and any(word in compact for word in ("找到", "查找", "这一行")):
        replacement_key = "aiWaitFor"
        replacement_prompt = "首页文档导入入口区域已稳定显示，本地导入、相册导入、微信导入或百度网盘入口可见"
    elif "向右翻看" in compact and "百度网盘" in compact:
        replacement_key = "aiWaitFor"
        replacement_prompt = "首页文档导入入口区域已显示「百度网盘」入口；如果入口在横向列表右侧，当前页面允许继续查找该入口"
    elif "进入" in compact and ("证件照" in compact or "一寸照" in compact):
        replacement_key = "aiTap"
        replacement_prompt = "一寸照入口"
        timeout = 0

    if not replacement_key:
        return {}
    _replace_step_action(step, action_key, replacement_key, replacement_prompt, timeout=timeout)
    return {
        "changed": f"{action_key} -> {replacement_key}",
        "prompt": prompt[:180],
        "replacement": replacement_prompt[:180],
    }


def _repair_generated_home_ai_step(step: dict, next_step: dict = None) -> dict:
    """Convert generated home-recovery ai planning into an explicit wait state."""
    if not isinstance(step, dict):
        return {}
    action_key = next((key for key in ("ai", "aiAction", "aiAct") if key in step), "")
    if not action_key:
        return {}
    prompt = str(step.get(action_key) or "").strip()
    compact = _compact_text(prompt)
    if not compact or "首页" not in compact:
        return {}
    if not any(word in compact for word in ("回到", "返回", "确保", "停留", "首页加载", "进入首页")):
        return {}
    target_hint = ""
    if isinstance(next_step, dict) and "aiTap" in next_step:
        tap_prompt = str(next_step.get("aiTap") or "").strip()
        quoted = re.search(r"[「“\"]([^」”\"]+)[」”\"]", tap_prompt)
        target = quoted.group(1).strip() if quoted else re.sub(r"^(点击|点按|轻触|选择|进入|打开)", "", tap_prompt).strip(" 「」")
        target = re.sub(r"(入口|按钮|控件)$", "", target).strip()
        if target:
            target_hint = f"，{target}可见"
    replacement_prompt = f"App 首页加载完成，主要入口或底部导航可见{target_hint}"
    _replace_step_action(step, action_key, "aiWaitFor", replacement_prompt, timeout=DEFAULT_WAITFOR_TIMEOUT_MS)
    return {
        "changed": f"{action_key} -> aiWaitFor",
        "prompt": prompt[:180],
        "replacement": replacement_prompt[:180],
    }


def _repair_generated_post_launch_restart_ai_step(step: dict, next_step: dict = None) -> dict:
    """Remove redundant AI app restarts after deterministic launch guards."""
    if not isinstance(step, dict):
        return {}
    action_key = next((key for key in ("ai", "aiAction", "aiAct") if key in step), "")
    if not action_key:
        return {}
    prompt = str(step.get(action_key) or "").strip()
    compact = _compact_text(prompt).lower()
    restart_terms = (
        "终止并重启app", "终止并重启应用", "关闭并重启app", "关闭并重启应用",
        "重启app", "重启应用", "重新启动app", "重新启动应用", "杀掉并重启",
    )
    if not compact or not any(term in compact for term in restart_terms):
        return {}
    target_hint = ""
    if isinstance(next_step, dict) and "aiWaitFor" in next_step:
        next_prompt = str(next_step.get("aiWaitFor") or "").strip()
        if next_prompt:
            target_hint = f"；随后应满足：{next_prompt[:100]}"
    replacement_prompt = f"被测 App 已按前置 launch 启动并进入稳定可见首屏{target_hint}"
    _replace_step_action(step, action_key, "aiWaitFor", replacement_prompt, timeout=DEFAULT_WAITFOR_TIMEOUT_MS)
    return {
        "changed": f"redundant post-launch {action_key} -> aiWaitFor",
        "prompt": prompt[:180],
        "replacement": replacement_prompt[:180],
    }


def repair_generated_yaml_executable_gate_issues(yaml_text: str) -> dict:
    """Repair local executable-gate issues before generated YAML is persisted.

    This is deliberately narrow. It does not add cases, assertions, or business
    coverage. It only fixes action semantics that are known to make generated
    YAML fail before Runner execution, such as using aiTap for a page-state check.
    """
    original = str(yaml_text or "")
    if _pyyaml is None or not original.strip():
        return {"changed": False, "content": original, "changes": []}
    try:
        parsed = _pyyaml.safe_load(original)
    except Exception:
        return {"changed": False, "content": original, "changes": []}
    _, tasks = extract_midscene_tasks(parsed)
    if not tasks:
        return {"changed": False, "content": original, "changes": []}

    changes = []
    for task_index, task in enumerate(tasks, start=1):
        if not isinstance(task, dict):
            continue
        flow = task.get("flow")
        if not isinstance(flow, list):
            continue
        after_baidu_tap = False
        deterministic_launch_seen = False
        converted_wait_prompts = []
        for step_index, step in enumerate(flow, start=1):
            if not isinstance(step, dict):
                continue
            if any(key in step for key in ("launch", "runAdbShell")):
                after_baidu_tap = False
            if "launch" in step:
                deterministic_launch_seen = True
            if isinstance(step.get("runAdbShell"), str) and _is_heavy_recent_cleanup_shell(step.get("runAdbShell")):
                prompt = str(step.get("runAdbShell") or "")
                step["runAdbShell"] = "input keyevent 3"
                changes.append({
                    "task": task.get("name") or f"tasks[{task_index}]",
                    "flowIndex": step_index,
                    "changed": "heavy recent-task cleanup -> home key",
                    "prompt": prompt[:180],
                    "replacement": "input keyevent 3",
                })

            next_step = flow[step_index] if step_index < len(flow) and isinstance(flow[step_index], dict) else None
            restart_repair = _repair_generated_post_launch_restart_ai_step(step, next_step) if deterministic_launch_seen else {}
            if restart_repair:
                changes.append({
                    "task": task.get("name") or f"tasks[{task_index}]",
                    "flowIndex": step_index,
                    **restart_repair,
                })
            home_repair = _repair_generated_home_ai_step(step, next_step)
            if home_repair:
                changes.append({
                    "task": task.get("name") or f"tasks[{task_index}]",
                    "flowIndex": step_index,
                    **home_repair,
                })

            broad_repair = _repair_generated_broad_ai_step(step)
            if broad_repair:
                changes.append({
                    "task": task.get("name") or f"tasks[{task_index}]",
                    "flowIndex": step_index,
                    **broad_repair,
                })

            if after_baidu_tap:
                if "aiWaitFor" in step and _is_baidu_original_entry_prompt(step.get("aiWaitFor")):
                    prompt = str(step.get("aiWaitFor") or "")
                    step["aiWaitFor"] = BAIDU_NETDISK_POST_CLICK_WAIT
                    step["timeout"] = max(int(step.get("timeout") or 0), BAIDU_NETDISK_POST_CLICK_TIMEOUT_MS)
                    changes.append({
                        "task": task.get("name") or f"tasks[{task_index}]",
                        "flowIndex": step_index,
                        "changed": "baidu post-click aiWaitFor",
                        "prompt": prompt[:180],
                        "waitFor": BAIDU_NETDISK_POST_CLICK_WAIT,
                    })
                if "aiAssert" in step and _is_baidu_original_entry_prompt(step.get("aiAssert")):
                    prompt = str(step.get("aiAssert") or "")
                    step["aiAssert"] = BAIDU_NETDISK_POST_CLICK_ASSERT
                    changes.append({
                        "task": task.get("name") or f"tasks[{task_index}]",
                        "flowIndex": step_index,
                        "changed": "baidu post-click aiAssert",
                        "prompt": prompt[:180],
                        "assert": BAIDU_NETDISK_POST_CLICK_ASSERT,
                    })

            if "aiTap" not in step:
                continue
            prompt = str(step.get("aiTap") or "").strip()
            if prompt and prompt_is_conditional_action(prompt):
                wait_prompt = conditional_action_to_wait_prompt(prompt)
                step.pop("aiTap", None)
                step["aiWaitFor"] = wait_prompt
                step.setdefault("timeout", DEFAULT_WAITFOR_TIMEOUT_MS)
                changes.append({
                    "task": task.get("name") or f"tasks[{task_index}]",
                    "flowIndex": step_index,
                    "changed": "conditional aiTap -> aiWaitFor",
                    "prompt": prompt[:180],
                    "waitFor": wait_prompt[:180],
                })
                continue
            if not prompt or not tap_prompt_looks_assertion(prompt):
                if _is_baidu_netdisk_tap_prompt(prompt):
                    after_baidu_tap = True
                continue
            wait_prompt = assertion_tap_to_wait_prompt(prompt)
            step.pop("aiTap", None)
            step["aiWaitFor"] = wait_prompt
            step.setdefault("timeout", DEFAULT_WAITFOR_TIMEOUT_MS)
            converted_wait_prompts.append(wait_prompt)
            changes.append({
                "task": task.get("name") or f"tasks[{task_index}]",
                "flowIndex": step_index,
                "changed": "aiTap -> aiWaitFor",
                "prompt": prompt[:180],
                "waitFor": wait_prompt[:180],
            })

        task_name_compact = _compact_text(task.get("name") or "")
        flow_text_compact = _compact_text(" ".join(
            " ".join(str(value or "") for key, value in step.items() if key in MIDSCENE_FLOW_ACTIONS)
            for step in flow
            if isinstance(step, dict)
        ))
        if "百度网盘" in task_name_compact and "文档打印" in task_name_compact and "文档打印" not in flow_text_compact:
            insert_at = next(
                (
                    idx for idx, step in enumerate(flow)
                    if isinstance(step, dict)
                    and any("百度网盘" in str(value or "") for key, value in step.items() if key in MIDSCENE_FLOW_ACTIONS)
                ),
                len(flow),
            )
            flow[insert_at:insert_at] = [
                {"aiTap": "文档打印入口"},
                {"sleep": 300},
                {
                    "aiWaitFor": "文档打印页或文档导入入口区域已稳定显示，本地导入、相册导入、微信导入或百度网盘入口可见",
                    "timeout": 15000,
                },
            ]
            changes.append({
                "task": task.get("name") or f"tasks[{task_index}]",
                "flowIndex": insert_at + 1,
                "changed": "insert document print path before baidu visibility check",
                "replacement": "文档打印入口 -> 文档打印页或文档导入入口区域已稳定显示",
            })

        if converted_wait_prompts and not any(isinstance(step, dict) and "aiAssert" in step for step in flow):
            wait_prompt = converted_wait_prompts[-1]
            insert_at = next(
                (
                    idx + 1 for idx, step in enumerate(flow)
                    if isinstance(step, dict) and str(step.get("aiWaitFor") or "") == wait_prompt
                ),
                len(flow),
            )
            flow.insert(insert_at, {"aiAssert": wait_prompt})
            changes.append({
                "task": task.get("name") or f"tasks[{task_index}]",
                "flowIndex": insert_at + 1,
                "changed": "add aiAssert for repaired visibility wait",
                "assert": wait_prompt[:180],
            })

    if not changes:
        return {"changed": False, "content": original, "changes": []}
    try:
        content = _pyyaml.safe_dump(parsed, allow_unicode=True, sort_keys=False, width=100000)
    except Exception:
        return {"changed": False, "content": original, "changes": []}
    return {"changed": True, "content": content, "changes": changes}


EXECUTABLE_GATE_AI_REWRITE_REASON_KEYWORDS = (
    "复合 ai 动作",
    "生成用例动作",
    "等待链路",
    "交互和等待组合偏长",
    "重规划",
    "replanning",
    "Replanned",
    "exceeding the limit",
    "单条用例步骤过长",
    "缺少稳定起点",
    "交互动作后缺少等待",
    "终态判断",
    "waitFor timeout",
    "Timeout after",
    "入口展示/位置类用例点击了百度网盘",
    "入口展示类用例",
    "当前业务页入口",
    "百度网盘点击后仍检查原业务页",
    "缺少进入",
    "缺少页面路径",
    "缺少先进入",
    "不能从首页直接点击",
    "最近任务清理",
    "多次滑动",
    "ADB 超时",
    "ADB timeout",
    "runAdbShell",
)


def _executable_gate_reason_lines(reasons) -> List[str]:
    if reasons is None:
        return []
    if isinstance(reasons, str):
        raw_items = re.split(r"[\n；;]+", reasons)
    elif isinstance(reasons, (list, tuple, set)):
        raw_items = []
        for item in reasons:
            if isinstance(item, dict):
                raw_items.append(json.dumps(item, ensure_ascii=False))
            else:
                raw_items.extend(re.split(r"[\n；;]+", str(item or "")))
    else:
        raw_items = [str(reasons or "")]
    result = []
    for item in raw_items:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if text and text not in result:
            result.append(text)
    return result


def _truncate_prompt_text(text, limit=6000):
    text = str(text or "")
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n...（已截断）"


def _build_executable_gate_baseline_text(*, title="", module="", file="", reasons=None):
    query = " ".join([
        str(title or ""),
        str(module or ""),
        str(file or ""),
        " ".join(_executable_gate_reason_lines(reasons)[:4]),
    ]).strip()
    if not query:
        return ""
    try:
        examples = search_baseline_examples(query, module=module or "", limit=3, allow_fallback=False)
    except Exception:
        examples = []
    blocks = []
    for index, example in enumerate(examples or [], start=1):
        if not isinstance(example, dict):
            continue
        snippet = str(example.get("snippet") or "").strip()
        if not snippet:
            continue
        blocks.append("\n".join([
            f"### 基线 {index}: {example.get('title') or example.get('file') or '未命名'}",
            f"- 模块：{example.get('module') or '-'}",
            f"- 文件：{example.get('file') or example.get('path') or '-'}",
            "```yaml",
            _truncate_prompt_text(snippet, 1200),
            "```",
        ]))
    return "\n\n".join(blocks)


def should_ai_rewrite_for_executable_gate(reasons) -> bool:
    """Return whether generated YAML needs a constrained AI rewrite.

    Deterministic repair handles small syntax/action mistakes. AI rewrite is
    reserved for semantic executability problems: overly long flows, compound
    actions, re-planning loops and weak terminal checks.
    """
    text = "；".join(_executable_gate_reason_lines(reasons))
    if not text:
        return False
    return any(keyword.lower() in text.lower() for keyword in EXECUTABLE_GATE_AI_REWRITE_REASON_KEYWORDS)


def _ai_executable_gate_rewrite_prompt(yaml_text, *, title="", module="", file="", reasons=None, baseline_text=""):
    reason_text = "\n".join(f"- {item}" for item in _executable_gate_reason_lines(reasons)[:12]) or "- 未提供具体原因"
    baseline_block = _truncate_prompt_text(baseline_text, 3500).strip()
    if not baseline_block:
        baseline_block = "无可信相似基线。只能保守缩短当前 YAML，不要扩展新场景。"
    return f"""
你是 Midscene YAML 可执行性修复器。当前 YAML 已经生成，但执行前 dry-run / 可执行性准入失败。

【任务】
只修复可执行性，不新增需求范围，不扩展新场景，不改成伪动作。

【失败原因】
{reason_text}

【用例信息】
- 标题：{title or "-"}
- 模块：{module or "-"}
- 文件：{file or "-"}

【可信相似基线写法参考】
{baseline_block}

【必须遵守】
1. 你不是自由生成 YAML，而是把当前 YAML 改写成短链路、可执行、可 dry-run 的 Midscene YAML。
2. 复用参考基线的动作顺序、等待方式和断言/终态判断方式；只能替换业务对象、按钮文案、输入内容和检查目标。
3. 如果当前任务过长，拆成 1～3 个短 task；每个 task 只验证一个检查点。
4. 每个 task 建议 4～8 个 flow 步骤，最多 10 个动作；禁止 15+ 步长链路。
5. 禁止把“查找/进入/判断/如果/直到/完成”揉在一个 ai 动作里；拆成 aiWaitFor、aiTap、aiWaitFor/aiAssert。
6. aiTap 只能写真实可点击目标；检查/展示/可见/布局/入口存在必须用 aiWaitFor 或 aiAssert。
7. 页面跳转后必须有 aiWaitFor；关键点击前必须有稳定页面或入口等待。
8. 入口可见性、布局、同级排列这类用例不要点击进入第三方授权、文件选择器、外部 App 或 WebView。
9. 必须先到达目标业务页，再检查该页的百度网盘入口：
   - 文档打印：从首页等待/点击「文档打印」，等待文档打印页或导入入口区域，再检查百度网盘。
   - 扫描复印：从首页等待/点击「扫描复印/复印扫描」，等待扫描复印页，再检查百度网盘。
   - 照片打印：从首页等待/点击「照片打印/图片打印」，等待照片导入页，再检查百度网盘。
   - 证件照/一寸照：先进入照片打印或证件照页，再点击一寸照/证件照入口；不能从首页直接点一寸照。
10. 入口展示/同级/位置校验只检查当前业务页；除非用例标题明确要求“点击百度网盘/授权/登录/文件列表”，否则不要 aiTap 百度网盘。
11. 启动守卫只允许简单稳定动作：am force-stop、launch、必要 sleep。禁止最近任务列表、多次 swipe、wm size 计算。
12. 埋点、曝光、eleTitle、后台统计类不要改写成 Runner 自动化；应保留为人工/待准备项。
13. 必须至少保留一个终态判断；不要堆大量 aiAssert，不要照抄 Figma 长文案。
14. 只能使用 Midscene 官方动作：launch、runAdbShell、sleep、ai、aiTap、aiInput、aiWaitFor、aiAssert、aiScroll、aiKeyboardPress。
15. 保留 YAML 顶层结构和 app 包名；不要输出 Markdown。

【当前 YAML】
```yaml
{_truncate_prompt_text(yaml_text, 9000)}
```

只返回 JSON：
{{
  "analysis": "为什么这样修",
  "changes": ["改动1", "改动2"],
  "content": "完整 YAML 字符串"
}}
""".strip()


def ai_rewrite_yaml_for_executable_gate(
    yaml_text,
    *,
    title="",
    module="",
    file="",
    reasons=None,
    baseline_text="",
    max_attempts=1,
):
    """Use AI once to rewrite generated YAML that deterministic repair cannot fix."""
    original = str(yaml_text or "")
    if not original.strip() or not should_ai_rewrite_for_executable_gate(reasons):
        return {"changed": False, "content": original, "changes": [], "skipped": True}
    attempts = []
    current = original
    if not str(baseline_text or "").strip():
        baseline_text = _build_executable_gate_baseline_text(
            title=title,
            module=module,
            file=file,
            reasons=reasons,
        )
    limit = max(1, min(2, safe_int(max_attempts, 1)))
    for attempt in range(1, limit + 1):
        try:
            prompt = _ai_executable_gate_rewrite_prompt(
                current,
                title=title,
                module=module,
                file=file,
                reasons=reasons,
                baseline_text=baseline_text,
            )
            raw = dashscope_chat_content(
                prompt,
                image_assets=[],
                temperature=0.05,
                timeout=YAML_STATIC_REPAIR_TIMEOUT_SECONDS,
                json_response=True,
                respect_global_timeout=False,
                retry_count=0,
            )
            repaired = normalize_yaml_from_model(raw)
            candidate = repaired.get("content") or ""
            local = repair_generated_yaml_executable_gate_issues(candidate)
            if local.get("changed"):
                candidate = local.get("content") or candidate
            dry = dry_run_midscene_yaml(candidate, module=module, file=file)
            score = score_midscene_yaml_executable(candidate, generated=True)
            ok = bool(dry.get("ok")) and score.get("executionLevel") == "executable"
            attempt_row = {
                "attempt": attempt,
                "ok": ok,
                "analysis": str(repaired.get("analysis") or "")[:500],
                "changes": list(repaired.get("changes") or [])[:12],
                "localChanges": list(local.get("changes") or [])[:8],
                "dryRunOk": bool(dry.get("ok")),
                "executionLevel": score.get("executionLevel"),
                "reasons": list(score.get("reasons") or [])[:8],
                "errors": list(dry.get("errors") or [])[:8],
            }
            attempts.append(attempt_row)
            current = candidate
            if ok:
                break
        except Exception as exc:
            attempts.append({
                "attempt": attempt,
                "ok": False,
                "error": str(exc)[:500],
            })
            break

    changed = current.strip() != original.strip()
    return {
        "changed": changed,
        "content": current if changed else original,
        "changes": [
            change
            for attempt in attempts
            for change in (attempt.get("changes") or [])
        ][:12],
        "attempts": attempts,
        "ok": bool(attempts and attempts[-1].get("ok")),
    }


def repair_generated_yaml_static_errors(yaml_text, *, title="", module="", file="", app_package="", max_attempts=None):
    """Repair generated YAML only enough to pass static/dry-run checks.

    This is intentionally narrow: it is not a coverage optimizer and must not add
    new scenarios. The goal is to keep generated YAML from entering Runner when it
    cannot even be parsed or loaded.
    """
    original = str(yaml_text or "")
    current, guard_changes = normalize_yaml_runtime_guards(original, app_package=app_package)
    attempts = []
    if guard_changes:
        attempts.append({
            "attempt": 0,
            "type": "runtime_guard_normalize",
            "ok": True,
            "changes": guard_changes[:12],
        })
    executable_gate_repair = repair_generated_yaml_executable_gate_issues(current)
    if executable_gate_repair.get("changed"):
        current = executable_gate_repair.get("content") or current
        attempts.append({
            "attempt": 0,
            "type": "local_executable_gate_repair",
            "ok": True,
            "changes": list(executable_gate_repair.get("changes") or [])[:12],
        })
    dry = dry_run_midscene_yaml(current, module=module, file=file, app_package=app_package)
    if dry.get("ok"):
        return {
            "ok": True,
            "content": current,
            "changed": current.strip() != original.strip(),
            "attempts": attempts,
            "dryRun": dry,
        }

    attempts.append({
        "attempt": 0,
        "type": "dry_run",
        "ok": False,
        "errors": list(dry.get("errors") or [])[:12],
        "warnings": list(dry.get("warnings") or [])[:8],
    })
    limit = YAML_STATIC_REPAIR_ATTEMPTS if max_attempts is None else max(0, safe_int(max_attempts, YAML_STATIC_REPAIR_ATTEMPTS))
    if limit <= 0 or not (dry.get("errors") or []):
        return {
            "ok": False,
            "content": current,
            "changed": current.strip() != original.strip(),
            "attempts": attempts,
            "dryRun": dry,
        }

    for attempt in range(1, limit + 1):
        try:
            prompt = _yaml_static_repair_prompt(
                current,
                dry,
                title=title,
                module=module,
                file=file,
                app_package=app_package,
            )
            raw = dashscope_chat_content(
                prompt,
                image_assets=[],
                temperature=0.05,
                timeout=YAML_STATIC_REPAIR_TIMEOUT_SECONDS,
                json_response=True,
                respect_global_timeout=False,
                retry_count=0,
            )
            repaired = normalize_yaml_from_model(raw)
            candidate, candidate_guard_changes = normalize_yaml_runtime_guards(repaired.get("content") or "", app_package=app_package)
            candidate_dry = dry_run_midscene_yaml(candidate, module=module, file=file, app_package=app_package)
            attempts.append({
                "attempt": attempt,
                "type": "ai_static_repair",
                "ok": bool(candidate_dry.get("ok")),
                "analysis": str(repaired.get("analysis") or "")[:500],
                "changes": list(repaired.get("changes") or [])[:12],
                "guardChanges": candidate_guard_changes[:12],
                "errors": list(candidate_dry.get("errors") or [])[:12],
            })
            current = candidate
            dry = candidate_dry
            if dry.get("ok"):
                break
        except Exception as exc:
            attempts.append({
                "attempt": attempt,
                "type": "ai_static_repair",
                "ok": False,
                "error": str(exc)[:500],
            })
            break

    return {
        "ok": bool(dry.get("ok")),
        "content": current,
        "changed": current.strip() != original.strip(),
        "attempts": attempts,
        "dryRun": dry,
    }


def validate_midscene_flow(content: str) -> List[str]:
    """校验 Midscene flow 动作是否合法。"""
    warnings: List[str] = []
    text = content or ""
    if not text.strip():
        warnings.append("YAML 内容为空")
        return warnings

    if _pyyaml is not None:
        try:
            parsed = _pyyaml.safe_load(text)
        except Exception as exc:
            warnings.append(f"YAML 解析失败：{exc}")
            return warnings
        if not isinstance(parsed, dict):
            warnings.append("YAML 顶层必须是对象")
            return warnings
        platform, tasks = extract_midscene_tasks(parsed)
        if not isinstance(tasks, list) or not tasks:
            warnings.append("YAML 必须包含非空 root.tasks、android.tasks 或 ios.tasks 数组")
            return warnings
        for idx, task in enumerate(tasks, 1):
            if not isinstance(task, dict):
                warnings.append(f"第 {idx} 条 task 必须是对象")
                continue
            misplaced = [
                key for key in task.keys()
                if key in MIDSCENE_FLOW_ACTIONS or key not in TASK_LEVEL_ALLOWED_KEYS
            ]
            misplaced = [key for key in misplaced if key not in ("name", "flow")]
            if misplaced:
                warnings.append(f"第 {idx} 条 task 出现了不允许出现在 task 顶层的字段：{misplaced}")
            flow = task.get("flow")
            if not isinstance(flow, list) or not flow:
                warnings.append(f"第 {idx} 条 task 必须包含非空 flow 数组")
                continue
            for fidx, item in enumerate(flow, 1):
                if not isinstance(item, dict) or not item:
                    warnings.append(f"第 {idx} 条 task flow[{fidx}] 必须是非空对象")
                    continue
                action_keys = [key for key in item.keys() if key in MIDSCENE_FLOW_ACTIONS]
                if not action_keys:
                    unknown = sorted(item.keys())
                    warnings.append(f"第 {idx} 条 task flow[{fidx}] 未识别的动作：{unknown}")
                    continue
                if len(action_keys) > 1:
                    warnings.append(f"第 {idx} 条 task flow[{fidx}] 同时声明多个动作：{action_keys}")
        return warnings

    stripped = _strip_yaml_comments(text)
    if not re.search(r"^\s*tasks\s*:", stripped, flags=re.M):
        warnings.append("YAML 必须包含非空 tasks 数组")
        return warnings
    flow_action_re = re.compile(r"^\s*-\s*([A-Za-z][\w]*)\s*[:：]")
    found_actions = False
    for line in stripped.splitlines():
        m = flow_action_re.match(line)
        if not m:
            continue
        key = m.group(1)
        if key in TASK_LEVEL_ALLOWED_KEYS and key not in MIDSCENE_FLOW_ACTIONS:
            continue
        found_actions = True
        if key not in MIDSCENE_FLOW_ACTIONS:
            warnings.append(f"未识别的 flow 动作：{key}")
    if not found_actions:
        warnings.append("YAML flow 中未发现可识别的动作")
    return warnings


# ---------------------------------------------------------------------------
# YAML 差异
# ---------------------------------------------------------------------------

def diff_yaml(old_content: str, new_content: str) -> str:
    """计算 YAML 前后差异，返回标准 unified diff 文本。"""
    old_lines = (old_content or "").splitlines(keepends=True)
    new_lines = (new_content or "").splitlines(keepends=True)
    diff_iter = difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile="old.yaml",
        tofile="new.yaml",
        lineterm="",
    )
    return "".join(diff_iter)


# ---------------------------------------------------------------------------
# 模块列表（带 TTL 缓存）
# ---------------------------------------------------------------------------

_MODULES_CACHE_LOCK = threading.Lock()
_MODULES_CACHE: tuple = ()  # (timestamp, data)


def list_modules(force: bool = False) -> List[dict]:
    """扫描 TASK_DIR 返回模块列表，支持 3 秒 TTL 缓存。"""
    global _MODULES_CACHE
    now = time.time()
    if not force:
        with _MODULES_CACHE_LOCK:
            if _MODULES_CACHE and (now - _MODULES_CACHE[0]) < 3:
                return _MODULES_CACHE[1]

    modules: List[dict] = []
    try:
        entries = sorted(os.listdir(TASK_DIR))
    except Exception:
        entries = []
    for entry in entries:
        entry_path = os.path.join(TASK_DIR, entry)
        if not os.path.isdir(entry_path):
            continue
        if entry.startswith(".") or entry.startswith("_"):
            continue
        try:
            yaml_files = sorted(
                f for f in os.listdir(entry_path)
                if is_visible_yaml_filename(f)
            )
        except Exception:
            yaml_files = []
        modules.append({
            "name": entry,
            "files": yaml_files,
            "fileCount": len(yaml_files),
        })

    with _MODULES_CACHE_LOCK:
        _MODULES_CACHE = (now, modules)
    return modules


def invalidate_modules_cache() -> None:
    """清除模块缓存，下次调用 list_modules 时重新扫描。"""
    global _MODULES_CACHE
    with _MODULES_CACHE_LOCK:
        _MODULES_CACHE = ()


# ---------------------------------------------------------------------------
# 兼容性别名 — 供其他模块 import
# ---------------------------------------------------------------------------

# validate_midscene_yaml — sonic_service 等模块依赖
def validate_midscene_yaml(yaml_text):
    """结构化 Midscene YAML 校验结果。

    ``validate_yaml`` 是旧工具函数，返回 warning 列表；生成、修复和同步
    链路已经按 dict 读取 ``ok`` / ``warnings``。这里统一转换，避免用
    ``**validate_midscene_yaml(...)`` 合并时把 list 当 mapping。
    """
    warnings = validate_yaml(yaml_text)
    return {
        "ok": not bool(warnings),
        "warnings": warnings,
        "issues": warnings,
    }

# list_task_case_assets — sonic_service 等模块依赖
# 实际实现在 case_service.py，此处提供重导出
def list_task_case_assets(module_filter="", file_filter=""):
    """兼容性别名，委托到 case_service.list_task_case_assets。"""
    from .case_service import list_task_case_assets as _impl
    return _impl(module_filter, file_filter)




# ---------------------------------------------------------------------------
# Migrated from midscene-upload.py
# ---------------------------------------------------------------------------

def run_generate_job(job_id, request_data):
    try:
        if generate_job_should_stop(job_id):
            return
        update_generate_job(job_id, status="running", progress=5, step="开始生成", message="生成任务已启动")
        result = generate_ui_yaml_from_request(request_data, job_id=job_id)
        if generate_job_should_stop(job_id):
            return
        summary = {
            "module": result["module"],
            "file": result["file"],
            "files": result.get("files") or result.get("yamlFiles") or [],
            "yamlFiles": result.get("yamlFiles") or result.get("files") or [],
            "yamlFileCount": result.get("yamlFileCount") or len(result.get("yamlFiles") or result.get("files") or []),
            "caseCount": result["caseCount"],
            "manualCaseCount": result["manualCaseCount"],
            "scenarioCount": result.get("scenarioCount", 0),
            "case_set_id": result["case_set_id"],
            "analysis": result.get("analysis", {}),
            "scenarios": result.get("scenarios", []),
            "review": result.get("review", {}),
            "coverageAudit": result.get("coverageAudit", {}),
            "knowledgePages": result.get("knowledgePages", []),
            "yamlCheck": result.get("yamlCheck", {}),
            "summary": result.get("summary", {}),
            "summaryFiles": result.get("summaryFiles", {}),
            "job": result.get("job")
        }
        update_generate_job(
            job_id,
            status="success",
            progress=100,
            step="完成",
            message="YAML 生成完成",
            result=summary
        )
    except Exception as e:
        if generate_job_should_stop(job_id):
            return
        current = load_generate_job(job_id) or {}
        progress = safe_int(current.get("progress"), 90)
        detail = generation_failure_detail(e, current)
        update_generate_job(
            job_id,
            status="failed",
            progress=max(5, min(99, progress or 90)),
            step=f"{detail.get('stage') or current.get('step') or '生成'}失败",
            message=detail.get("message") or str(e),
            error=detail.get("error") or str(e),
            error_detail=detail,
            error_trace=traceback.format_exc()[-3000:]
        )



def run_mindmap_only_job(job_id, request_data):
    try:
        if generate_job_should_stop(job_id):
            return
        update_generate_job(job_id, status="running", progress=5, step="开始生成脑图", message="只生成脑图任务已启动")
        result = generate_mindmap_from_request(request_data, job_id=job_id)
        if generate_job_should_stop(job_id):
            return
        summary = {
            "module": result.get("module", ""),
            "file": "",
            "caseCount": result.get("caseCount", 0),
            "manualCaseCount": result.get("manualCaseCount", 0),
            "scenarioCount": result.get("scenarioCount", 0),
            "case_set_id": result["case_set_id"],
            "coverageAudit": result.get("coverageAudit", {}),
            "summary": result.get("summary", {}),
            "summaryFiles": result.get("summaryFiles", {}),
        }
        update_generate_job(
            job_id,
            status="success",
            progress=100,
            step="完成",
            message="脑图生成完成，未生成 YAML",
            result=summary,
            case_set_id=result["case_set_id"]
        )
    except Exception as e:
        if generate_job_should_stop(job_id):
            return
        current = load_generate_job(job_id) or {}
        progress = safe_int(current.get("progress"), 90)
        detail = generation_failure_detail(e, current)
        update_generate_job(
            job_id,
            status="failed",
            progress=max(5, min(99, progress or 90)),
            step=f"{detail.get('stage') or current.get('step') or '脑图生成'}失败",
            message=detail.get("message") or str(e),
            error=detail.get("error") or str(e),
            error_detail=detail,
            error_trace=traceback.format_exc()[-3000:]
        )



def _generation_scope_terms_from_rich_context(title, module, requirement_text_assets, used_figma_pages):
    text = "\n".join([str(title or ""), str(module or "")] + [str(item or "") for item in (requirement_text_assets or [])])
    compact = re.sub(r"\s+", "", text)
    candidates: List[str] = []

    def add(term):
        term = str(term or "").strip(" -:：/、，,")
        if len(term) < 2:
            return
        if term in candidates:
            return
        candidates.append(term)

    priority_terms = (
        "AI建模入口", "开始创作", "图片建模", "上传图片", "语音创作", "长按输入",
        "生成模型", "模型生成中", "模型生成结果", "我的作品", "大家都在做",
        "引导弹窗", "搜索图片", "重新生成", "失败提示", "空态展示",
    )
    for term in priority_terms:
        if term in text or term in compact:
            add(term)
    for page in used_figma_pages or []:
        if not isinstance(page, dict):
            continue
        name = first_non_empty(page.get("page_name"), page.get("pageName"), page.get("route"))
        name = re.sub(r"^(figma-|页面|Frame\\s*)", "", str(name or ""), flags=re.I).strip()
        if not name or name.lower() in {"frame", "root"}:
            continue
        if re.search(r"缺省|历史|反馈|成长报告", name) and "AI" not in name and "建模" not in name:
            continue
        add(name)
    return candidates[:16]


def _ensure_rich_generation_scope(payload, title, module, requirement_text_assets, used_figma_pages, figma_images):
    """Record rich input metadata without turning Figma pages into requirements."""
    if not isinstance(payload, dict):
        return payload
    requirement_size = sum(len(str(item or "")) for item in (requirement_text_assets or []))
    figma_page_count = len(used_figma_pages or [])
    figma_image_count = len(figma_images or [])
    rich = requirement_size >= 800 or figma_page_count >= 8 or figma_image_count >= 8
    if not rich:
        return payload
    analysis = payload.setdefault("analysis", {})
    if not isinstance(analysis, dict):
        analysis = {}
        payload["analysis"] = analysis
    points = normalize_text_list(
        analysis.get("requirement_points")
        or analysis.get("requirementPoints")
        or analysis.get("test_points")
        or analysis.get("testPoints")
    )
    review = payload.setdefault("review", {})
    if isinstance(review, dict):
        review["rich_generation_scope"] = {
            "enabled": True,
            "requirement_size": requirement_size,
            "figma_page_count": figma_page_count,
            "figma_image_count": figma_image_count,
            "requirement_point_count": len(points),
            "synthetic_requirement_points_added": 0,
            "extra_coverage_round": False,
            "reason": "需求范围只采用需求分析明确提取的验收点；Figma 页面名和资料长度仅作参考，不再伪造验收点或抬高用例数量。",
        }
    return payload


def _prepared_figma_page_key(item):
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


def _dedupe_prepared_figma_pages(items):
    result = []
    seen = set()
    for item in items or []:
        if not isinstance(item, dict):
            continue
        key = _prepared_figma_page_key(item)
        if key and key in seen:
            continue
        if key:
            seen.add(key)
        result.append(item)
    return result


def _prepared_figma_image_key(item):
    if not isinstance(item, dict):
        return ""
    for key in ("asset_id", "assetId", "name", "image_name", "screenshot"):
        value = str(item.get(key) or "").strip()
        if value:
            return value
    return str(item.get("base64") or item.get("contentBase64") or "").strip()[:96]


def _prepared_figma_context_from_request(d):
    raw = d.get("prepared_figma_context") or d.get("preparedFigmaContext") or {}
    if not isinstance(raw, dict):
        return {}
    text_assets = [
        str(item)
        for item in (raw.get("textAssets") or raw.get("text_assets") or [])
        if str(item or "").strip()
    ]
    image_assets = []
    seen_images = set()
    for item in raw.get("imageAssets") or raw.get("image_assets") or []:
        if not isinstance(item, dict):
            continue
        image_b64 = item.get("base64") or item.get("contentBase64")
        if not image_b64:
            continue
        name = clean_asset_filename(item.get("name") or "figma-design.png")
        image_key = _prepared_figma_image_key({**item, "name": name})
        if image_key and image_key in seen_images:
            continue
        if image_key:
            seen_images.add(image_key)
        image_assets.append({
            **item,
            "name": name,
            "mime": item.get("mime") or guess_mime(name),
            "base64": image_b64,
        })
    used_pages = _dedupe_prepared_figma_pages(raw.get("usedPages") or raw.get("used_pages") or [])
    if used_pages and len(image_assets) > len(used_pages):
        page_image_names = {
            str(page.get(key) or "").strip()
            for page in used_pages
            for key in ("screenshot", "image_name", "name")
            if str(page.get(key) or "").strip()
        }
        matched_images = [item for item in image_assets if str(item.get("name") or "").strip() in page_image_names]
        image_assets = matched_images or image_assets[:len(used_pages)]
    ignored_pages = _dedupe_prepared_figma_pages(raw.get("ignoredPages") or raw.get("ignored_pages") or [])
    saved_designs = _dedupe_prepared_figma_pages(raw.get("savedDesigns") or raw.get("saved_designs") or [])
    if not (text_assets or image_assets or used_pages):
        return {}
    return {
        "source": raw.get("source") or "prepared_figma",
        "figmaUrl": raw.get("figmaUrl") or raw.get("figma_url") or "",
        "textAssets": text_assets,
        "imageAssets": image_assets,
        "usedPages": used_pages,
        "ignoredPages": ignored_pages,
        "savedDesigns": saved_designs,
    }


def _save_prepared_figma_design_assets(case_set_id, prepared_figma_context, title="", module=""):
    if not case_set_id or not prepared_figma_context:
        return []
    image_assets = prepared_figma_context.get("imageAssets") or []
    used_pages = prepared_figma_context.get("usedPages") or []
    if not image_assets:
        return []
    pages_by_image = {}
    for page in used_pages:
        if not isinstance(page, dict):
            continue
        for key in (page.get("screenshot"), page.get("image_name"), page.get("name")):
            if key:
                pages_by_image[str(key)] = page
    files = []
    for index, item in enumerate(image_assets, start=1):
        if not isinstance(item, dict):
            continue
        image_b64 = item.get("base64") or item.get("contentBase64")
        if not image_b64:
            continue
        # save_case_ui_design_files has a 5MB per-image guard; skip obviously large cached renders.
        if len(str(image_b64)) > 7 * 1024 * 1024:
            continue
        name = clean_asset_filename(item.get("name") or f"figma-design-{index}.png")
        page = pages_by_image.get(name)
        if page is None and index - 1 < len(used_pages):
            page = used_pages[index - 1] if isinstance(used_pages[index - 1], dict) else {}
        if not isinstance(page, dict):
            page = {}
        figma = page.get("figma") if isinstance(page.get("figma"), dict) else {}
        node_id = figma.get("node_id") or figma.get("nodeId") or page.get("page_id") or name
        files.append({
            "asset_id": f"figma-{node_id}",
            "name": name,
            "contentBase64": image_b64,
            "page_name": page.get("page_name") or page.get("pageName") or item.get("page_name") or "",
            "route": page.get("route") or item.get("route") or "",
            "description": page.get("description") or item.get("description") or "",
            "figma": {
                **figma,
                "reused_from_prepare_source": True,
                "source": prepared_figma_context.get("source") or "prepared_figma",
            },
        })
    if not files:
        return []
    try:
        saved, _meta = save_case_ui_design_files(case_set_id, files, source="figma", title=title, module=module)
        return saved
    except Exception:
        return []


def entry_visibility_fast_path_enabled(request, title, module, text_assets):
    request = request if isinstance(request, dict) else {}
    disabled = safe_bool(
        request.get("disableEntryVisibilityFastPath")
        or request.get("disable_entry_visibility_fast_path"),
        False,
    )
    if disabled:
        return False
    return safe_bool(
        request.get("forceEntryVisibilityFastPath")
        or request.get("force_entry_visibility_fast_path")
        or request.get("entryVisibilityFastPath"),
        False,
    ) or should_fast_path_baidu_entry_visibility(title, module, text_assets)


FIGMA_SOFT_EVIDENCE_POLICY = (
    "需求文本定义要验证什么；Figma 仅补充某一设计帧中真实同屏的页面层级、状态/变体和可见文案。",
    "页面/Frame 名称可能是内部旧命名，不能覆盖状态/变体和可见文字，也不能单独推导业务入口。",
    "某个能力只在一帧出现时，不得推广到相邻业务页；缺少到达路径时应进入复核，不能臆造导航。",
    "画布或设备形态用于适配检查，不代表需要选择或并发执行另一台真实设备。",
)


def build_figma_soft_evidence_context_text(figma_texts, limit=16000):
    """Wrap existing Figma parser output with a conservative evidence contract."""
    blocks = [str(item or "").strip() for item in (figma_texts or []) if str(item or "").strip()]
    if not blocks:
        return ""
    header = "【Figma 同帧软证据规则】\n- " + "\n- ".join(FIGMA_SOFT_EVIDENCE_POLICY)
    body = "\n\n".join(blocks)
    return f"{header}\n\n【Figma 解析结果】\n{body}"[:max(2000, safe_int(limit, 16000))]


def generate_ui_yaml_from_request(d, job_id=None):
    title = d.get("title") or d.get("target") or d.get("goal") or "UI自动化用例"
    module = d.get("module") or "AI测试"
    model_config = ai_model_config_from_request(d)
    raw_execution_context = d.get("executionContext") or d.get("execution_context") or {}
    execution_context = {
        key: raw_execution_context.get(key)
        for key in ("executionMode", "runnerId", "deviceId", "deviceStrategy", "singleDeviceOnly")
        if isinstance(raw_execution_context, dict) and raw_execution_context.get(key) not in (None, "")
    }
    yaml_file = clean_filename(d.get("file") or f"task-{slug_for_file(title)}.yaml")
    case_set_id = d.get("case_set_id") or new_case_set_id()
    create_job = safe_bool(d.get("createJob", d.get("create_job")))
    auto_optimize = automatic_baseline_repair_enabled(d.get("autoOptimize", d.get("auto_optimize")))
    run_mode = d.get("run_mode") or d.get("runMode") or ("baseline" if auto_optimize else "test")
    device_id = d.get("device_id") or d.get("deviceId") or ""
    runner_id = d.get("runner_id") or d.get("runnerId") or ""
    device_strategy = normalize_device_strategy(
        d.get("device_strategy") or d.get("deviceStrategy"),
        device_id=device_id,
        runner_id=runner_id,
    )
    if create_job and device_strategy != "auto" and not device_id and not runner_id:
        raise ValueError("生成后创建执行任务需要先选择执行设备；如确实需要平台分配，请明确选择“自动选择在线设备”。")
    files = d.get("files") or []
    reuse_assets = safe_bool(d.get("reuse_assets") or d.get("reuseAssets") or d.get("regenerate"))
    prepared_figma_context = _prepared_figma_context_from_request(d)
    prepared_cases_payload = d.get("preparedCasesPayload") or d.get("prepared_cases_payload") or {}
    if not isinstance(prepared_cases_payload, dict):
        prepared_cases_payload = {}
    requirement_contract = d.get("requirementCoverageContract") or d.get("requirement_coverage_contract") or {}
    if not isinstance(requirement_contract, dict):
        requirement_contract = {}

    if job_id:
        update_generate_job(job_id, progress=10, step="保存上传资产", message="正在保存上传文件")
    if files:
        meta = save_asset_files(case_set_id, title, module, files)
        meta = update_asset_request_context(case_set_id, d)
    elif reuse_assets:
        meta = read_json_file(asset_meta_path(case_set_id), default=None)
        if not meta or not meta.get("files"):
            raise ValueError("这个生成批次没有可复用的需求资料，请重新上传需求后生成")
        meta["title"] = title or meta.get("title")
        meta["module"] = module or meta.get("module")
        recovered_figma_url = (d.get("figma_url") or d.get("figmaUrl") or "").strip() or find_figma_url_for_case_set(case_set_id, meta=meta)
        if recovered_figma_url:
            d["figma_url"] = recovered_figma_url
            meta["figma_url"] = recovered_figma_url
        meta = update_asset_request_context(case_set_id, {**meta, **d})
    else:
        meta = {
            "case_set_id": case_set_id,
            "title": title,
            "module": module,
            "files": [],
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        os.makedirs(safe_join(ASSET_DIR, case_set_id), exist_ok=True)
        write_json_file(asset_meta_path(case_set_id), meta)
        meta = update_asset_request_context(case_set_id, d)

    has_prepared_figma = bool(prepared_figma_context)
    has_figma = bool((d.get("figma_url") or d.get("figmaUrl") or meta.get("figma_url") or "").strip() or has_prepared_figma)
    if case_set_id and (has_figma or reuse_assets):
        removed = clear_auto_figma_ui_design_assets(case_set_id)
        if job_id and removed:
            suffix = "，将复用 Agent 准备阶段的 Figma 解析结果" if has_prepared_figma else ("，将重新按需求筛选" if has_figma else "，本次没有可用 Figma 链接，不再沿用旧误选页面")
            update_generate_job(job_id, progress=12, step="刷新 Figma UI 稿", message=f"已清理 {removed} 份旧的自动 Figma UI 稿{suffix}")

    if job_id:
        update_generate_job(job_id, progress=25, step="解析资产", message="正在解析需求文档、脑图和设计稿")
    requirement_text_assets, uploaded_image_assets = load_asset_contents(case_set_id, meta)
    if not requirement_text_assets and not uploaded_image_assets and not has_figma:
        raise ValueError("没有可用于生成的文本、图片资产或 Figma 链接")

    if job_id:
        update_generate_job(job_id, progress=35, step="读取页面知识", message="正在匹配 APP 页面知识库")
    query_text = "\n".join([title, module] + requirement_text_assets)
    app_package = d.get("app_package") or d.get("appPackage") or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    selected_page_ids = d.get("knowledge_page_ids") or d.get("knowledgePageIds") or []
    knowledge_tier = d.get("knowledge_tier") or d.get("knowledgeTier") or "all"
    with ThreadPoolExecutor(max_workers=2) as executor:
        knowledge_future = executor.submit(
            load_knowledge_context,
            app_package,
            query_text,
            6,
            selected_page_ids,
            knowledge_tier
        )
        figma_future = None
        if not prepared_figma_context:
            figma_future = executor.submit(load_figma_generation_context, d, app_package, job_id, query_text, case_set_id, title, module)
        try:
            knowledge_texts, knowledge_images, used_knowledge_pages = knowledge_future.result()
        except Exception as e:
            knowledge_texts, knowledge_images, used_knowledge_pages = [], [], []
            if job_id:
                update_generate_job(job_id, progress=36, step="读取页面知识", message=f"页面知识读取失败，已跳过：{str(e)[:80]}")
        if prepared_figma_context:
            figma_texts = prepared_figma_context.get("textAssets") or []
            figma_images = prepared_figma_context.get("imageAssets") or []
            used_figma_pages = prepared_figma_context.get("usedPages") or []
            ignored_figma_pages = prepared_figma_context.get("ignoredPages") or []
            saved_figma_designs = _save_prepared_figma_design_assets(case_set_id, prepared_figma_context, title=title, module=module)
            if job_id:
                update_generate_job(
                    job_id,
                    progress=38,
                    step="复用 Figma 解析",
                    message=f"已复用准备阶段解析结果：页面 {len(used_figma_pages)} 个，Figma UI 图 {len(figma_images)} 张",
                )
        else:
            try:
                figma_texts, figma_images, used_figma_pages, ignored_figma_pages, saved_figma_designs = figma_future.result()
            except Exception as e:
                figma_texts, figma_images, used_figma_pages, ignored_figma_pages, saved_figma_designs = [], [], [], [], []
                if job_id:
                    update_generate_job(job_id, progress=38, step="解析 Figma", message=f"Figma 解析失败，已跳过：{str(e)[:80]}")
    used_reference_pages = used_figma_pages + used_knowledge_pages
    visual_text_assets = figma_texts + knowledge_texts
    figma_soft_evidence_text = build_figma_soft_evidence_context_text(figma_texts)
    # 脑图/YAML 视觉校准只使用当前 Figma 和人工上传截图。
    # 页面知识库截图容易包含历史无关页面，保留为文本上下文即可，避免误导模型选图。
    visual_image_assets = figma_images + uploaded_image_assets

    if job_id:
        update_generate_job(job_id, progress=45, step="理解需求", message="正在先根据需求文档拆解测试点和测试用例")
    stage1_text_assets = list(requirement_text_assets or [])
    if figma_soft_evidence_text:
        stage1_text_assets.append(figma_soft_evidence_text)
    if not stage1_text_assets and knowledge_texts:
        stage1_text_assets.extend(knowledge_texts)
    if not stage1_text_assets:
        stage1_text_assets = [
            "未提供独立需求文档，请根据标题、模块、Figma/截图和页面知识先归纳业务范围，再生成测试用例。"
        ]
    semantic_constraints_text = build_requirement_semantic_constraints_text(requirement_text_assets or [title], title)
    if semantic_constraints_text:
        stage1_text_assets = list(stage1_text_assets) + [semantic_constraints_text]
        if job_id:
            update_generate_job(
                job_id,
                progress=42,
                step="识别需求主链",
                message="已识别需求文档硬约束，生成 YAML 时按业务功能点优先，不使用 Figma 内部页名做用例主题",
            )
    agent_business_plan = d.get("agent_business_plan") or d.get("agentBusinessPlan") or {}
    agent_business_plan_text = build_agent_business_plan_context_text(agent_business_plan)
    if agent_business_plan_text:
        stage1_text_assets = list(stage1_text_assets) + [agent_business_plan_text]
    deterministic_entry_visibility_source = entry_visibility_fast_path_enabled(
        d,
        title,
        module,
        stage1_text_assets,
    )
    use_global_baseline_profile = safe_bool(
        d.get("useGlobalBaselineProfile")
        if d.get("useGlobalBaselineProfile") is not None
        else d.get("use_global_baseline_profile"),
        False,
    )
    baseline_query_text = "\n".join([title, module, query_text] + stage1_text_assets + visual_text_assets)
    baseline_branch_queries = baseline_branch_queries_from_agent_plan(agent_business_plan)
    baseline_required_branches = baseline_required_branches_from_agent_plan(agent_business_plan)
    baseline_retrieval_branches = baseline_required_branches or baseline_branch_queries
    baseline_candidates = search_diverse_baseline_examples(
        baseline_query_text,
        branch_queries=baseline_retrieval_branches,
        module=module,
        limit=20,
    )
    yaml_baseline_cache_status = get_yaml_baseline_cache_status()
    ai_decision_trace = {
        "enabled": True,
        "baseline_candidate_count": len(baseline_candidates),
        "baseline_branch_query_count": len(baseline_retrieval_branches),
        "baseline_plan_branch_query_count": len(baseline_branch_queries),
        "baseline_required_branch_count": len(baseline_required_branches),
        "baseline_required_branches": [item.get("name") for item in baseline_required_branches],
    }
    if deterministic_entry_visibility_source:
        yaml_reference_examples = baseline_candidates[:3]
        ai_decision_trace["baseline_reranker"] = {
            "enabled": False,
            "skipped": True,
            "reason": "入口可见性快路径使用本地短链路生成，跳过 AI 基线重排",
            "selected_count": len(yaml_reference_examples),
        }
    else:
        try:
            baseline_rerank = call_skill_baseline_reranker(
                title,
                module,
                baseline_query_text,
                baseline_candidates,
                model_config=model_config,
                limit=3,
                required_branches=baseline_required_branches,
            )
            yaml_reference_examples = baseline_rerank.get("selected") or (
                [] if baseline_required_branches else baseline_candidates[:3]
            )
            ai_decision_trace["baseline_reranker"] = baseline_rerank.get("trace") or {}
            ai_decision_trace["baseline_reranker_review"] = baseline_rerank.get("review") or {}
        except Exception as rerank_error:
            yaml_reference_examples = [] if baseline_required_branches else baseline_candidates[:3]
            ai_decision_trace["baseline_reranker"] = {
                "enabled": True,
                "fallback": True,
                "error": str(rerank_error),
                "selected_count": len(yaml_reference_examples),
            }
    if deterministic_entry_visibility_source:
        local_targets = generation_volume_targets({"requirement_points": stage1_text_assets}, mode="full")
        target_count = 3
        execution_scope_plan = {
            "size": "small",
            "targetCaseCount": target_count,
            "smokeCount": 3,
            "continueThreshold": 0.5,
            "reason": "入口可见性快路径固定生成 3 条首批短链路冒烟",
            "businessFlow": ["进入首页", "进入文档打印", "校验百度网盘入口可见"],
            "trace": {"enabled": False, "skipped": True, "reason": "deterministic_entry_visibility_source"},
        }
    else:
        try:
            execution_scope_plan = call_skill_execution_scope_planner(
                title,
                module,
                stage1_text_assets,
                yaml_reference_examples,
                model_config=model_config,
            )
        except Exception as scope_error:
            local_targets = generation_volume_targets({"requirement_points": stage1_text_assets}, mode="full")
            target_count = safe_int(local_targets.get("target_automation_cases"), 3)
            target_count = 3 if target_count <= 3 else (5 if target_count <= 5 else 8)
            execution_scope_plan = {
                "size": "small" if target_count <= 3 else ("medium" if target_count <= 5 else "large"),
                "targetCaseCount": target_count,
                "smokeCount": min(3, target_count),
                "continueThreshold": 0.5,
                "reason": "AI 范围规划失败，回退平台 3/5/8 规则",
                "businessFlow": [],
                "trace": {"enabled": True, "fallback": True, "error": str(scope_error)},
            }
    ai_decision_trace["execution_scope_planner"] = execution_scope_plan.get("trace") or {}
    decision_context_text = "" if deterministic_entry_visibility_source else build_ai_generation_decision_context_text(
        yaml_reference_examples,
        execution_scope_plan,
        {},
    )
    if decision_context_text:
        stage1_text_assets = list(stage1_text_assets) + [decision_context_text]
        if job_id:
            update_generate_job(
                job_id,
                progress=43,
                step="AI 生成决策",
                message=(
                    f"AI 已规划生成范围：目标 {execution_scope_plan.get('targetCaseCount')} 条，"
                    f"首批冒烟 {execution_scope_plan.get('smokeCount')} 条；"
                    f"相似基线候选 {len(baseline_candidates)} 个，采用 {len(yaml_reference_examples)} 个"
                ),
            )
    yaml_action_contract = load_yaml_action_contract()
    yaml_baseline_library_examples = []
    yaml_library_patterns = []
    yaml_library_profile = {}
    if use_global_baseline_profile:
        yaml_baseline_library_examples = collect_yaml_baseline_library_examples()
        yaml_library_patterns = extract_yaml_patterns_from_examples(yaml_baseline_library_examples, limit=12)
        yaml_library_profile = summarize_yaml_patterns(yaml_library_patterns)
    # 全量基线缓存只用于状态、评估和检索来源；模型 prompt 只接收本次需求 Top3
    # 相似基线片段，避免全局模式把无关历史用例带进当前需求。
    if yaml_library_patterns:
        if job_id:
            update_generate_job(
                job_id,
                progress=43,
                step="读取基线缓存",
                message=(
                    f"基线缓存 cache_hit={str(yaml_baseline_cache_status.get('cacheHit')).lower()}，"
                    f"文件 {yaml_baseline_cache_status.get('fileCount', 0)} 个，"
                    f"样本 {yaml_baseline_cache_status.get('caseCount', 0)} 条；"
                    "本次只把 Top3 分支互补基线片段送入模型"
                ),
            )
    yaml_baseline_patterns = []
    yaml_reference_text = build_yaml_reference_examples_text(yaml_reference_examples)
    if yaml_reference_text:
        stage1_text_assets = list(stage1_text_assets) + [yaml_reference_text]
        if job_id:
            names = "、".join((item.get("title") or item.get("file") or "") for item in yaml_reference_examples[:3])
            message = (
                f"已从缓存检索 Top{len(yaml_reference_examples)} 可信相似基线（优先执行成功），生成时仿写：{names}"
                if yaml_reference_examples
                else "未命中可信相似基线，本次只允许生成短链路 YAML，复杂链路进入需确认/人工"
            )
            update_generate_job(
                job_id,
                progress=44,
                step="检索用例库",
                message=message,
            )
    yaml_template_candidates = select_best_baseline_template(
        "\n".join([title, module, query_text] + stage1_text_assets + visual_text_assets),
        yaml_reference_examples,
        limit=3,
    )
    yaml_template_matcher_text = build_yaml_template_matcher_text(yaml_template_candidates)
    if yaml_template_matcher_text:
        stage1_text_assets = list(stage1_text_assets) + [yaml_template_matcher_text]
        if job_id:
            template_names = "、".join((item.get("title") or item.get("file") or "") for item in yaml_template_candidates[:3])
            update_generate_job(
                job_id,
                progress=44,
                step="匹配 YAML 模板",
                message=f"已选择 {len(yaml_template_candidates)} 个相似基线模板，生成时按模板填槽：{template_names}",
            )
    yaml_pattern_contract_text = ""
    if use_global_baseline_profile:
        yaml_baseline_patterns = extract_yaml_patterns_from_examples(yaml_reference_examples, limit=3)
        yaml_pattern_contract_text = build_yaml_pattern_contract_text(yaml_baseline_patterns, yaml_action_contract)
    if yaml_pattern_contract_text:
        stage1_text_assets = list(stage1_text_assets) + [yaml_pattern_contract_text]
        if job_id:
            pattern_names = "、".join((item.get("title") or item.get("file") or "") for item in yaml_baseline_patterns[:3])
            update_generate_job(
                job_id,
                progress=44,
                step="抽取基线模式",
                message=f"已抽取 {len(yaml_baseline_patterns)} 个可执行基线模式，限制模型按白名单动作仿写：{pattern_names}",
            )
    smoke_policy_text = build_executable_smoke_yaml_policy_text()
    stage1_text_assets = list(stage1_text_assets) + [smoke_policy_text]
    skill_pipeline_error = ""
    if prepared_cases_payload:
        payload = normalize_cases_payload(copy.deepcopy(prepared_cases_payload))
        review = payload.setdefault("review", {})
        review["agent_mindmap_plan_reused"] = {
            "enabled": True,
            "source": d.get("preparedCasesSource") or d.get("prepared_cases_source") or "platform_mindmap_ai",
            "rule": "复用 PLAN 阶段 MM skills 的需求分析、场景和视觉校准结果；YAML 阶段继续执行覆盖、可执行性和安全门禁。",
        }
    elif USE_AI_SKILL_PIPELINE:
        try:
            if job_id:
                if deterministic_entry_visibility_source:
                    update_generate_job(job_id, progress=45, step="需求解析", message="入口可见性快路径：跳过重型 AI 需求解析，直接生成短链路冒烟用例")
                else:
                    update_generate_job(job_id, progress=45, step="需求解析", message="正在按 requirement_analyzer skill 做需求体检和测试点拆解")
            payload = build_cases_payload_from_skills(
                title,
                module,
                stage1_text_assets,
                model_config=model_config,
                app_package=app_package,
                allow_entry_visibility_fast_path=deterministic_entry_visibility_source,
                generation_scope_plan=execution_scope_plan,
                requirement_contract=requirement_contract,
            )
        except Exception as e:
            skill_pipeline_error = str(e)
            if job_id:
                update_generate_job(job_id, progress=48, step="兼容生成", message=f"Skills 链路暂不可用，正在使用兼容生成兜底：{skill_pipeline_error[:80]}")
            payload = call_dashscope_cases(title, module, stage1_text_assets, [])
            review = payload.setdefault("review", {})
            review["skill_pipeline_error"] = skill_pipeline_error
            review["skill_pipeline_fallback"] = "call_dashscope_cases"
    else:
        payload = call_dashscope_cases(title, module, stage1_text_assets, [])
        review = payload.setdefault("review", {})
        review["skill_pipeline_disabled"] = True

    planned_generation_targets = generation_targets_for_scope(
        payload.get("analysis") or {},
        mode="full",
        scope_plan=execution_scope_plan,
    )
    payload.setdefault("review", {})["generation_targets"] = planned_generation_targets
    local_fallback_execution_floor = generated_payload_uses_local_fallback(payload)
    review = payload.setdefault("review", {})
    deterministic_entry_visibility = str(review.get("skill_pipeline") or "").startswith("deterministic_baidu_entry_visibility")
    if prepared_cases_payload:
        review["visual_refine_reused"] = "已复用 PLAN 阶段 visual_grounder 结果，不重复发送同一批 Figma/截图"
        if job_id:
            update_generate_job(
                job_id,
                progress=67,
                step="复用 AI 业务计划",
                message="已复用 MM 需求分析、场景设计和视觉校准结果，继续覆盖与 YAML 可执行性规划",
            )
    elif deterministic_entry_visibility and (visual_text_assets or visual_image_assets):
        review["visual_refine_skipped"] = (
            "确定性入口可见性短链路已覆盖首批冒烟，Figma/截图仅作为参考记录，不再阻塞 YAML 生成"
        )
        review["visual_reference_note"] = visual_reference_message(
            "已记录视觉参考但跳过重型视觉校准",
            figma_texts,
            figma_images,
            ignored_figma_pages,
            knowledge_texts,
            [],
            uploaded_image_assets,
        )
        if job_id:
            update_generate_job(
                job_id,
                progress=67,
                step="视觉校准跳过",
                message="入口可见性短链路已稳定生成，Figma/截图作为参考记录，不阻塞首批 YAML",
            )
    elif visual_text_assets or visual_image_assets:
        if job_id:
            update_generate_job(
                job_id,
                progress=65,
                step="视觉校准",
                message=visual_reference_message(
                    "正在校准入口、步骤和断言，实际送入模型",
                    figma_texts,
                    figma_images,
                    ignored_figma_pages,
                    knowledge_texts,
                    [],
                    uploaded_image_assets
                )
            )
        try:
            payload = refine_cases_with_yaml_visual_batches(
                title,
                module,
                payload,
                visual_text_assets,
                visual_image_assets,
                job_id=job_id,
            )
        except Exception as e:
            review = payload.setdefault("review", {})
            review["visual_refine_error"] = str(e)
            review["visual_refine_skipped"] = "视觉校准超时或失败，已保留需求解析主结果继续生成 YAML"
            review["remaining_risks"] = normalize_text_list(review.get("remaining_risks") or []) + [
                "视觉校准未完成，入口文案和 UI 断言可能需要人工在生成分析中补充截图后重新生成"
            ]
            if job_id:
                update_generate_job(job_id, progress=67, step="视觉校准跳过", message=f"视觉校准失败但不阻塞生成：{str(e)[:100]}")

    yaml_visual_review_trace = snapshot_yaml_visual_review(payload)
    review = payload.setdefault("review", {})
    review["ai_decision_trace"] = ai_decision_trace
    review["execution_scope_plan"] = {
        "size": execution_scope_plan.get("size"),
        "targetCaseCount": execution_scope_plan.get("targetCaseCount"),
        "smokeCount": execution_scope_plan.get("smokeCount"),
        "continueThreshold": execution_scope_plan.get("continueThreshold", 0.5),
        "reason": execution_scope_plan.get("reason") or "",
        "businessFlow": execution_scope_plan.get("businessFlow") or [],
    }
    if agent_business_plan_text:
        review["agent_business_plan"] = {
            "used": True,
            "source": (d.get("agent_business_plan") or d.get("agentBusinessPlan") or {}).get("source") or "",
            "businessFlowCount": len((d.get("agent_business_plan") or d.get("agentBusinessPlan") or {}).get("businessFlows") or []),
            "rule": "上游 Agent 业务计划参与需求拆解和路径规划，但不能覆盖原始需求或把软参考升级为硬门禁。",
        }
    if yaml_reference_examples:
        memory_path = record_yaml_reference_examples(case_set_id, title, module, yaml_reference_examples)
        review["yaml_reference_examples"] = [
            {
                "id": item.get("id") or item.get("case_id") or "",
                "title": item.get("title"),
                "module": item.get("module"),
                "file": item.get("file"),
                "score": item.get("score"),
                "matched_terms": item.get("matched_terms") or [],
                "actions": item.get("actions") or [],
                "baseline_path": item.get("baseline_path") or "",
                "businessPath": item.get("businessPath") or "",
                "sourceKind": item.get("sourceKind") or "",
                "verificationStatus": item.get("verificationStatus") or "",
                "provenancePath": item.get("provenancePath") or item.get("file") or "",
                "sourceTrust": item.get("sourceTrust") or 0,
                "aiSelectedRole": item.get("ai_selected_role") or "",
                "aiSelectedReason": item.get("ai_selected_reason") or "",
                "aiSelectedBranchId": item.get("ai_selected_branch_id") or "",
                "aiSelectedBranchName": item.get("ai_selected_branch_name") or "",
            }
            for item in yaml_reference_examples
        ]
        review["yaml_step_library"] = {
            "enabled": True,
            "example_count": len(yaml_reference_examples),
            "memory_path": memory_path,
            "rule": "生成 YAML 前检索现有用例库，学习可执行步骤组织方式；只复用相关动作结构，不复制无关业务断言。",
        }
    review["yaml_baseline_cache"] = {
        "enabled": True,
        **yaml_baseline_cache_status,
        "baseline_cache_hit": bool(yaml_baseline_cache_status.get("cacheHit")),
        "baseline_cache_source": yaml_baseline_cache_status.get("cacheSource") or "",
        "baseline_matched_count": len(yaml_reference_examples),
        "use_global_baseline_profile": bool(use_global_baseline_profile),
        "rule": "基线库先构建缓存，生成 YAML 时只从缓存中取 TopN 相似基线片段给模型仿写，避免每次现场全量读取 YAML。",
    }
    review["baseline_cache_hit"] = bool(yaml_baseline_cache_status.get("cacheHit"))
    review["baseline_cache_source"] = yaml_baseline_cache_status.get("cacheSource") or ""
    review["baseline_matched_count"] = len(yaml_reference_examples)
    if yaml_template_candidates:
        template_quality = evaluate_baseline_template_matching(yaml_reference_examples, limit=3)
        review["yaml_template_matcher"] = {
            "enabled": True,
            "template_count": len(yaml_template_candidates),
            "templates": [
                {
                    "rank": item.get("template_rank"),
                    "title": item.get("title"),
                    "module": item.get("module"),
                    "file": item.get("file"),
                    "score": item.get("template_score") or item.get("score"),
                    "matched_terms": item.get("matched_terms") or [],
                    "actions": item.get("actions") or [],
                }
                for item in yaml_template_candidates[:3]
            ],
            "rule": "需求先匹配 Top3 相似基线模板，AI 只能按模板做业务变量替换和少量步骤微调。",
            "quality_eval": template_quality,
        }
    if yaml_baseline_patterns:
        review["yaml_pattern_contract"] = {
            "enabled": True,
            "pattern_count": len(yaml_baseline_patterns),
            "summary": summarize_yaml_patterns(yaml_baseline_patterns),
            "allowed_actions": yaml_action_contract.get("allowed_actions") or [],
            "patterns": [
                {
                    "title": item.get("title"),
                    "module": item.get("module"),
                    "file": item.get("file"),
                    "score": item.get("score"),
                    "matched_terms": item.get("matched_terms") or [],
                    "actions": item.get("actions") or [],
                    "sample_labels": item.get("sample_labels") or [],
                }
                for item in yaml_baseline_patterns[:3]
            ],
            "rule": "AI 只能按相似基线动作模式做业务变量替换和少量步骤微调，禁止自由创造 Runner 不支持的 action。",
        }
    if yaml_library_patterns:
        review["yaml_baseline_library_profile"] = {
            "enabled": True,
            "example_count": len(yaml_baseline_library_examples),
            "pattern_count": len(yaml_library_patterns),
            "summary": yaml_library_profile,
            "patterns": [
                {
                    "title": item.get("title"),
                    "module": item.get("module"),
                    "file": item.get("file"),
                    "actions": item.get("actions") or [],
                    "sample_labels": item.get("sample_labels") or [],
                }
                for item in yaml_library_patterns[:12]
            ],
            "rule": "从 YAML 基线缓存提炼平台通用写法；相似 Top3 只用于当前需求的局部仿写。",
        }
    review["generation_targets"] = planned_generation_targets
    if prepared_figma_context:
        review["prepared_figma_context_reused"] = {
            "enabled": True,
            "used_count": len(used_figma_pages),
            "image_count": len(figma_images),
            "saved_design_count": len(saved_figma_designs),
            "source": prepared_figma_context.get("source") or "prepared_figma",
        }
    if used_figma_pages or ignored_figma_pages:
        review["figma_requirement_filter"] = {
            "enabled": True,
            "used_count": len(used_figma_pages),
            "ignored_count": len(ignored_figma_pages),
            "saved_design_count": len(saved_figma_designs),
            "used_pages": [
                {
                    "page_name": page.get("page_name", ""),
                    "route": page.get("route", ""),
                    "score": page.get("relevance_score", 0),
                    "reason": page.get("relevance_reason", "")
                }
                for page in used_figma_pages[:8]
            ],
            "ignored_pages": [
                {
                    "page_name": page.get("page_name", ""),
                    "score": ((page.get("figma") or {}).get("relevance_score", 0)),
                    "reason": ((page.get("figma") or {}).get("relevance_reason", ""))
                }
                for page in ignored_figma_pages[:12]
            ],
            "rule": "Figma 只作为与需求匹配的 UI 参考；无关页面不会进入视觉校准"
        }

    if deterministic_entry_visibility:
        if job_id:
            update_generate_job(job_id, progress=72, step="覆盖率审查", message="入口可见性短链路使用本地覆盖审查，跳过额外 AI 补全")
        payload, coverage_audit = audit_case_coverage(payload)
        review = payload.setdefault("review", {})
        review["coverage_auditor_skipped"] = "确定性入口可见性短链路已按 3 条首批冒烟覆盖，跳过额外 AI 覆盖补全"
        review["coverage_audit"] = coverage_audit
    else:
        if job_id:
            update_generate_job(job_id, progress=72, step="覆盖率审查", message="正在用 coverage_auditor 反查需求点、场景和用例覆盖，补齐遗漏场景")
        try:
            payload = _ensure_rich_generation_scope(payload, title, module, stage1_text_assets, used_figma_pages, figma_images)
            rich_scope = ((payload.get("review") or {}).get("rich_generation_scope") or {}) if isinstance(payload, dict) else {}
            coverage_rounds = 2 if rich_scope.get("extra_coverage_round") else 1
            def coverage_progress(message, progress=None):
                if job_id:
                    update_generate_job(
                        job_id,
                        progress=safe_int(progress, 72),
                        step="覆盖率审查",
                        message=str(message or "正在检查需求点、场景和用例覆盖"),
                    )
            payload, coverage_audit = improve_case_coverage(
                title,
                module,
                payload,
                max_rounds=coverage_rounds,
                progress_callback=coverage_progress,
                time_budget_seconds=AI_COVERAGE_TOTAL_BUDGET_SECONDS,
                model_config=model_config,
                targets=planned_generation_targets,
            )
        except Exception as e:
            try:
                payload, coverage_audit = audit_case_coverage(payload)
            except Exception as audit_error:
                payload = normalize_cases_payload(payload)
                coverage_audit = {
                    "ok": False,
                    "coverage_auditor_skill": "fallback_normalized_payload",
                    "coverage_auditor_error": str(audit_error),
                    "case_count": len(payload.get("cases") or []),
                }
            review = payload.setdefault("review", {})
            review["coverage_repair_error"] = str(e)
            review["remaining_risks"] = normalize_text_list(review.get("remaining_risks") or []) + [
                "覆盖率补全模型调用失败，已保留当前用例并记录覆盖审查结果"
            ]
    payload = normalize_cases_payload(payload)
    payload.setdefault("review", {})["generation_targets"] = planned_generation_targets
    final_executable_portfolio = None
    if deterministic_entry_visibility:
        review = payload.setdefault("review", {})
        ai_decision_trace = review.get("ai_decision_trace") if isinstance(review.get("ai_decision_trace"), dict) else ai_decision_trace
        ai_decision_trace["executable_yaml_planner"] = {
            "enabled": False,
            "skipped": True,
            "reason": "确定性入口可见性短链路已通过本地覆盖审查，直接转换 YAML",
        }
        review["ai_decision_trace"] = ai_decision_trace
        review["executable_yaml_planner_skipped"] = ai_decision_trace["executable_yaml_planner"]["reason"]
    else:
        executable_source_evidence = {
            "mode": "soft_reference",
            "requirementText": "\n\n".join(requirement_text_assets)[:12000],
            "figmaSoftEvidence": figma_soft_evidence_text,
            "figmaPageCount": len(used_figma_pages),
            "figmaImageCount": len(figma_images),
            "executionContext": execution_context,
            "policy": list(FIGMA_SOFT_EVIDENCE_POLICY),
        }
        try:
            executable_plan = call_skill_executable_yaml_planner(
                title,
                module,
                payload,
                yaml_reference_examples,
                execution_scope_plan,
                model_config=model_config,
                source_evidence=executable_source_evidence,
            )
            executable_plan["scopePlan"] = execution_scope_plan
            payload = apply_executable_yaml_plan_to_payload(payload, executable_plan)
            review = payload.setdefault("review", {})
            ai_decision_trace = review.get("ai_decision_trace") if isinstance(review.get("ai_decision_trace"), dict) else ai_decision_trace
            ai_decision_trace["executable_yaml_planner"] = executable_plan.get("trace") or {}
            review["ai_decision_trace"] = ai_decision_trace
            review["executable_yaml_planner_review"] = executable_plan.get("review") or {}
            review["needs_review_cases"] = executable_plan.get("needs_review_cases") or []
            review["draft_cases"] = executable_plan.get("draft_cases") or []

            portfolio_before = executable_yaml_portfolio_audit(payload, planned_generation_targets)
            final_executable_portfolio = portfolio_before
            review["executable_yaml_portfolio_initial"] = portfolio_before
            if not portfolio_before.get("ok") and executable_plan.get("authoritative") is True:
                if job_id:
                    update_generate_job(
                        job_id,
                        progress=76,
                        step="最终覆盖收敛",
                        message=(
                            f"AI 正在收敛显式需求覆盖：缺口 {len(portfolio_before.get('missingRequirementPoints') or [])} 个，"
                            f"未决自动候选 {portfolio_before.get('unresolvedAutomaticCount') or 0} 条"
                        ),
                    )
                convergence_context = {
                    "pass": "coverage_convergence",
                    "portfolioAudit": portfolio_before,
                    "rules": {
                        "preserveCurrentExecutableCases": True,
                        "coverEveryExplicitRequirementWhenBoundedUiEvidenceExists": True,
                        "classifyEveryCandidateAsExecutableOrManual": True,
                        "noNeedsReviewOrDraftInFinalPass": True,
                        "doNotBypassStaticScorerOrRunnerGate": True,
                    },
                }
                convergence_evidence = dict(executable_source_evidence)
                convergence_evidence["planningPass"] = "coverage_convergence"
                convergence_evidence["portfolioAudit"] = portfolio_before
                convergence_plan = call_skill_executable_yaml_planner(
                    title,
                    module,
                    payload,
                    yaml_reference_examples,
                    execution_scope_plan,
                    model_config=model_config,
                    source_evidence=convergence_evidence,
                    planning_context=convergence_context,
                )
                convergence_plan["scopePlan"] = execution_scope_plan
                initial_plan_review = copy.deepcopy(review.get("executable_yaml_plan") or {})
                if (
                    convergence_plan.get("authoritative") is True
                    or convergence_plan.get("evidenceFallback") is True
                ):
                    payload = apply_executable_yaml_plan_to_payload(payload, convergence_plan)
                portfolio_after = executable_yaml_portfolio_audit(payload, planned_generation_targets)
                final_executable_portfolio = portfolio_after
                review = payload.setdefault("review", {})
                if convergence_plan.get("evidenceFallback") is True:
                    review["needs_review_cases"] = [
                        item for item in (payload.get("cases") or [])
                        if isinstance(item, dict)
                        and str(item.get("executionLevel") or "").strip().lower() == "needs_review"
                    ]
                    review["draft_cases"] = [
                        item for item in (payload.get("cases") or [])
                        if isinstance(item, dict)
                        and str(item.get("executionLevel") or "").strip().lower() == "draft"
                    ]
                else:
                    review["needs_review_cases"] = convergence_plan.get("needs_review_cases") or []
                    review["draft_cases"] = convergence_plan.get("draft_cases") or []
                review["executable_yaml_plan_initial"] = initial_plan_review
                review["executable_yaml_convergence"] = {
                    "attempted": True,
                    "authoritative": convergence_plan.get("authoritative") is True,
                    "evidenceFallback": convergence_plan.get("evidenceFallback") is True,
                    "evidenceFallbackCandidateIds": sorted(
                        (convergence_plan.get("candidateEligibilityById") or {}).keys()
                    ),
                    "before": portfolio_before,
                    "after": portfolio_after,
                    "trace": convergence_plan.get("trace") or {},
                    "review": convergence_plan.get("review") or {},
                    "rule": (
                        "最终收敛优先由 AI 选择可执行组合；AI 不可用时只复用已验证的上游 AI 有界证据。"
                        "平台仍验证显式需求覆盖、分类终态、可信基线路径和可见终态，"
                        "不降低 scorer 或 Runner 门禁。"
                    ),
                }
                ai_decision_trace = review.get("ai_decision_trace") if isinstance(review.get("ai_decision_trace"), dict) else ai_decision_trace
                ai_decision_trace["executable_yaml_convergence"] = convergence_plan.get("trace") or {}
                review["ai_decision_trace"] = ai_decision_trace
        except Exception as plan_error:
            review = payload.setdefault("review", {})
            ai_decision_trace = review.get("ai_decision_trace") if isinstance(review.get("ai_decision_trace"), dict) else ai_decision_trace
            ai_decision_trace["executable_yaml_planner"] = {
                "enabled": True,
                "fallback": True,
                "error": str(plan_error),
            }
            review["ai_decision_trace"] = ai_decision_trace
            review["executable_yaml_planner_error"] = str(plan_error)
    if not deterministic_entry_visibility:
        final_executable_portfolio = final_executable_portfolio or executable_yaml_portfolio_audit(
            payload,
            planned_generation_targets,
        )
        review = payload.setdefault("review", {})
        review["executable_yaml_portfolio_final"] = final_executable_portfolio
        review["executable_yaml_portfolio_gate"] = {
            "passed": bool(final_executable_portfolio.get("ok")),
            "rule": (
                "AI 负责选择和补齐可执行组合；平台只在最终转换前检查显式需求映射、"
                "至少一条 executable 和分类终态；3/5/8 仅作为规划目标，不为凑数放宽可执行性。"
            ),
            "reasons": final_executable_portfolio.get("reasons") or [],
            "advisories": final_executable_portfolio.get("advisories") or [],
            "targetExecutableCount": final_executable_portfolio.get("targetExecutableCount") or 0,
            "targetMet": final_executable_portfolio.get("targetMet") is True,
            "targetShortfall": final_executable_portfolio.get("targetShortfall") or 0,
            "missingRequirementPoints": final_executable_portfolio.get("missingRequirementPoints") or [],
        }
        if not final_executable_portfolio.get("ok"):
            payload["id"] = case_set_id
            payload["module"] = module
            write_json_file(cases_path(case_set_id), payload)
            missing = final_executable_portfolio.get("missingRequirementPoints") or []
            reason_text = "；".join(final_executable_portfolio.get("reasons") or []) or "最终可执行覆盖不完整"
            if missing:
                reason_text += "；缺失：" + "、".join(str(item) for item in missing[:5])
            if job_id:
                update_generate_job(
                    job_id,
                    progress=78,
                    step="最终覆盖门禁",
                    message=reason_text[:500],
                )
            raise ValueError(f"最终可执行 YAML 覆盖门禁未通过：{reason_text}")
    if deterministic_entry_visibility:
        review = payload.setdefault("review", {})
        review["smoke_selector_final_skipped"] = "确定性入口可见性短链路已完成本地首批冒烟选择"
    else:
        try:
            payload = select_smoke_cases_for_payload(
                title,
                module,
                payload,
                mode="full",
                yaml_reference_context=yaml_reference_text,
                model_config=model_config,
                targets=planned_generation_targets,
            )
        except Exception as smoke_error:
            review = payload.setdefault("review", {})
            review["smoke_selector_final_error"] = str(smoke_error)
            review["smoke_selector_final_policy"] = "最终冒烟筛选失败时不使用 P0/P1 或关键词兜底，保留现有显式 smoke 标记。"
    payload = restore_yaml_visual_review(payload, yaml_visual_review_trace)
    payload = enforce_generated_fallback_execution_floor(payload, force=local_fallback_execution_floor)
    payload = apply_generated_case_scope_gate(payload)
    payload["id"] = case_set_id
    payload["module"] = module

    if job_id:
        update_generate_job(job_id, progress=75, step="保存用例 JSON", message="正在保存模型生成的用例 JSON")
    write_json_file(cases_path(case_set_id), payload)

    if job_id:
        update_generate_job(job_id, progress=85, step="转换 YAML", message="正在按用例拆分生成 Midscene YAML")
    converted_payload = split_automation_ready_cases(payload)
    _, yaml_items = cases_to_separate_midscene_yamls(converted_payload, app_package=app_package, base_file=yaml_file)

    if job_id:
        update_generate_job(job_id, progress=86, step="修复 YAML", message="正在对生成 YAML 做静态 dry-run 和必要修复")
    yaml_static_repair_results = []
    repaired_yaml_items = []
    for item in yaml_items:
        repair = repair_generated_yaml_static_errors(
            item.get("content") or "",
            title=title,
            module=module,
            file=item.get("file") or "",
            app_package=app_package,
        )
        next_item = dict(item)
        if repair.get("content"):
            next_item["content"] = repair.get("content")
        dry = repair.get("dryRun") or {}
        yaml_static_repair_results.append({
            "file": item.get("file") or "",
            "ok": bool(repair.get("ok")),
            "changed": bool(repair.get("changed")),
            "attempts": repair.get("attempts") or [],
            "errors": list(dry.get("errors") or [])[:12],
            "warnings": list(dry.get("warnings") or [])[:8],
            "executionLevel": dry.get("executionLevel") or "draft",
            "taskCount": dry.get("taskCount") or 0,
        })
        repaired_yaml_items.append(next_item)
    yaml_items = repaired_yaml_items
    yaml_file = yaml_items[0]["file"]
    yaml = yaml_items[0]["content"]
    yaml_files = [item["file"] for item in yaml_items]
    yaml_checks = []
    yaml_executability_checks = []
    yaml_smoke_stability_checks = []
    yaml_static_validation_checks = []
    module_dir = safe_join(TASK_DIR, module)
    os.makedirs(module_dir, exist_ok=True)
    for item in yaml_items:
        write_text_file(safe_join(module_dir, item["file"]), item["content"])
        yaml_checks.append({"file": item["file"], **validate_midscene_yaml(item["content"])})
        yaml_executability_checks.append({"file": item["file"], **validate_midscene_yaml_executability(item["content"])})
        yaml_smoke_stability_checks.append({"file": item["file"], **review_generated_yaml_smoke_stability(item["content"])})
        yaml_static_validation_checks.append({"file": item["file"], **validate_yaml_static_executable(item["content"])})
    yaml_check = {
        "ok": all(item.get("ok") for item in yaml_checks),
        "mode": "split_by_case",
        "file_count": len(yaml_items),
        "files": yaml_checks,
    }
    yaml_executability = {
        "ok": all(item.get("ok") for item in yaml_executability_checks),
        "mode": "split_by_case",
        "file_count": len(yaml_items),
        "files": yaml_executability_checks,
        "taskCount": sum(int(item.get("taskCount") or 0) for item in yaml_executability_checks),
    }
    yaml_smoke_stability = {
        "ok": all(item.get("ok") for item in yaml_smoke_stability_checks),
        "stable": all(item.get("stable") for item in yaml_smoke_stability_checks),
        "mode": "split_by_case",
        "file_count": len(yaml_items),
        "files": yaml_smoke_stability_checks,
        "warningCount": sum(len(item.get("warnings") or []) for item in yaml_smoke_stability_checks),
        "rule": "Runner 冒烟可执行优先：默认一条最终业务断言，过程使用等待和动作，参考现有 YAML 经验库。",
    }
    execution_level_counts = {}
    for item in yaml_static_validation_checks:
        level = item.get("executionLevel") or "draft"
        execution_level_counts[level] = execution_level_counts.get(level, 0) + 1
    yaml_static_validation = {
        "ok": all(item.get("ok") for item in yaml_static_validation_checks),
        "mode": "split_by_case",
        "file_count": len(yaml_items),
        "files": yaml_static_validation_checks,
        "errorCount": sum(len(item.get("errors") or []) for item in yaml_static_validation_checks),
        "warningCount": sum(len(item.get("warnings") or []) for item in yaml_static_validation_checks),
        "executionLevelCounts": execution_level_counts,
        "rule": "生成后做动作白名单和静态可执行校验；有静态错误的 YAML 标记为草稿，不进入 Runner 自动执行。",
    }
    review = converted_payload.setdefault("review", {})
    review["executable_smoke_policy"] = {
        "enabled": True,
        "assertion_limit": YAML_GENERATED_ASSERTION_LIMIT,
        "reference_example_count": len(yaml_reference_examples),
        "rule": "生成 YAML 时产出可分批全量执行的稳定用例；Runner 首批只跑冒烟准入，脚本/YAML/定位/超时类问题会阻断扩展，产品断言失败会记录为测试结果。",
    }
    review["yaml_smoke_stability"] = yaml_smoke_stability
    review["yaml_static_validation"] = yaml_static_validation
    review["yaml_static_repair"] = {
        "enabled": True,
        "attemptLimit": YAML_STATIC_REPAIR_ATTEMPTS,
        "file_count": len(yaml_static_repair_results),
        "repaired_count": sum(1 for item in yaml_static_repair_results if item.get("changed")),
        "passed_count": sum(1 for item in yaml_static_repair_results if item.get("ok")),
        "files": yaml_static_repair_results,
        "rule": "只修复 YAML 结构和动作字段，不新增用例、不补断言、不改业务覆盖。",
    }

    summary = build_generation_summary(
        case_set_id,
        title,
        module,
        yaml_file,
        converted_payload,
        used_knowledge_pages=used_reference_pages,
        yaml_check=yaml_check,
        yaml_executability=yaml_executability
    )
    summary["yaml_files"] = yaml_files
    summary["yaml_file_count"] = len(yaml_files)
    summary["yaml_smoke_stability"] = yaml_smoke_stability
    summary["yaml_static_validation"] = yaml_static_validation
    summary["yaml_static_repair"] = review.get("yaml_static_repair")
    if ignored_figma_pages:
        summary["ignored_figma_pages"] = ignored_figma_pages
    ui_design_meta = filtered_case_ui_design_assets_for_summary(case_set_id, summary)
    if ui_design_meta.get("designs"):
        summary["ui_design_assets"] = ui_design_meta.get("designs") or []
    if ui_design_meta.get("hidden_designs"):
        summary["hidden_ui_design_assets"] = ui_design_meta.get("hidden_designs") or []
    if ui_design_meta.get("excluded_figma_nodes"):
        summary["excluded_figma_nodes"] = ui_design_meta.get("excluded_figma_nodes") or []
    summary_files = write_generation_summary(case_set_id, summary)
    static_by_file = {item.get("file"): item for item in yaml_static_validation_checks}
    yaml_executable_scores = []
    generated_case_groups = {
        "executable_cases": [],
        "needs_review_cases": [],
        "draft_cases": [],
        "manual_cases": [],
    }
    generated_cases_by_id = {
        str(case.get("case_id") or case.get("caseId") or case.get("id") or "").strip(): case
        for case in (converted_payload.get("cases") or [])
        if isinstance(case, dict) and str(case.get("case_id") or case.get("caseId") or case.get("id") or "").strip()
    }
    generated_cases_by_title = {
        str(case.get("title") or case.get("name") or "").strip(): case
        for case in (converted_payload.get("cases") or [])
        if isinstance(case, dict) and str(case.get("title") or case.get("name") or "").strip()
    }
    requirement_analysis = converted_payload.get("analysis") if isinstance(converted_payload.get("analysis"), dict) else {}
    for item in yaml_items:
        score = score_midscene_yaml_executable(item.get("content") or "", generated=True)
        task_scores = [row for row in (score.get("taskScores") or []) if isinstance(row, dict)]
        first_task = task_scores[0] if task_scores else {}
        scored_level = str(score.get("level") or score.get("executionLevel") or "draft")
        source_case = (
            item.get("case")
            or generated_cases_by_id.get(str(item.get("case_id") or "").strip())
            or generated_cases_by_title.get(str(item.get("title") or "").strip())
            or {}
        )
        scope_review = generated_case_requirement_scope_review(source_case, requirement_analysis, item.get("content") or "")
        level = generated_yaml_effective_level(scored_level, source_case, scope_review, score)
        reasons = list(score.get("reasons") or [])[:8]
        scope_reasons = list(scope_review.get("reasons") or [])
        provenance_reasons = []
        if _generated_case_uses_local_fallback(source_case):
            provenance_reasons.append("automation_filter 超时后的本地兜底仅供评审，不自动下发 Runner")
        if level != scored_level or scope_reasons:
            score = dict(score)
            corrected_display_downgrade = (
                level == "executable"
                and str(scored_level or "").strip().lower() == "needs_review"
                and _generated_case_requirement_mapped_display_check(source_case, scope_review, score)
            )
            if corrected_display_downgrade:
                score["score"] = max(safe_int(score.get("score"), 0), 85)
            else:
                score["score"] = min(safe_int(score.get("score"), 0), 74)
            score["executionLevel"] = level
            score["level"] = level
            score["ok"] = level == "executable"
            score["scopeReview"] = scope_review
            correction_reasons = [GENERATED_REQUIREMENT_MAPPED_DISPLAY_REASON] if corrected_display_downgrade else []
            score["reasons"] = (scope_reasons + provenance_reasons + correction_reasons + reasons)[:8]
            reasons = list(score.get("reasons") or [])[:8]
        explicit_smoke = bool(item.get("smoke"))
        row = {
            "name": first_task.get("name") or item.get("title") or item.get("file"),
            "module": module,
            "file": item.get("file"),
            "case_id": item.get("case_id"),
            "score": score.get("score") or 0,
            "level": level,
            "executionLevel": level,
            "priority": first_task.get("priority") or item.get("priority") or "",
            "smoke": explicit_smoke,
            "smokeCandidate": explicit_smoke,
            "runnerCandidate": bool(score.get("smokeCandidate") or first_task.get("smokeCandidate")),
            "mainBusinessChain": bool(first_task.get("mainBusinessChain")),
            "baselineEvidence": bool(score.get("baselineEvidence") or first_task.get("baselineEvidence")),
            "scopeReview": scope_review,
            "reasons": reasons,
        }
        yaml_executable_scores.append({"file": item.get("file"), **score})
        bucket = {
            "executable": "executable_cases",
            "needs_review": "needs_review_cases",
            "manual": "manual_cases",
        }.get(level, "draft_cases")
        generated_case_groups[bucket].append(row)
    mapped_case_keys = set()
    for item in yaml_items:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("case_id") or "").strip()
        title_text = str(item.get("title") or "").strip()
        if case_id:
            mapped_case_keys.add(("id", case_id))
        if title_text:
            mapped_case_keys.add(("title", title_text))
    for index, case in enumerate(converted_payload.get("cases") or []):
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id") or case.get("caseId") or case.get("id") or "").strip()
        title_text = str(case.get("title") or case.get("name") or f"未命名用例-{index + 1}").strip()
        if (case_id and ("id", case_id) in mapped_case_keys) or (title_text and ("title", title_text) in mapped_case_keys):
            continue
        generated_case_groups["needs_review_cases"].append({
            "name": title_text,
            "module": module,
            "file": "",
            "case_id": case_id,
            "score": 0,
            "level": "needs_review",
            "executionLevel": "needs_review",
            "priority": case_priority(case),
            "smoke": bool(is_smoke_case(case)),
            "smokeCandidate": False,
            "runnerCandidate": False,
            "mainBusinessChain": False,
            "baselineEvidence": False,
            "scopeReview": {"ok": False, "reasons": ["该自动化用例未生成对应 YAML 文件"]},
            "reasons": ["生成结果缺少该用例的 YAML 文件，需补齐后才能自动下发 Runner"],
        })
    generated_case_groups["counts"] = {
        "executable": len(generated_case_groups["executable_cases"]),
        "needs_review": len(generated_case_groups["needs_review_cases"]),
        "draft": len(generated_case_groups["draft_cases"]),
        "manual": len(generated_case_groups["manual_cases"]),
    }
    generated_case_groups["rule"] = "只有 executable_cases 允许自动创建 Runner 任务；其他分组只展示或人工处理。"
    converted_payload["executable_cases"] = generated_case_groups["executable_cases"]
    converted_payload["needs_review_cases"] = generated_case_groups["needs_review_cases"]
    converted_payload["draft_cases"] = generated_case_groups["draft_cases"]
    converted_payload["manual_cases"] = list(converted_payload.get("manual_cases") or []) + generated_case_groups["manual_cases"]
    converted_payload["execution_level_counts"] = generated_case_groups["counts"]
    summary["generatedCaseGroups"] = generated_case_groups
    summary["executable_cases"] = generated_case_groups["executable_cases"]
    summary["needs_review_cases"] = generated_case_groups["needs_review_cases"]
    summary["draft_cases"] = generated_case_groups["draft_cases"]
    summary["manual_cases"] = converted_payload.get("manual_cases", [])
    summary["yamlExecutableScores"] = yaml_executable_scores
    summary["execution_level_counts"] = generated_case_groups["counts"]
    summary_files = write_generation_summary(case_set_id, summary)
    for item in yaml_items:
        static_check = static_by_file.get(item["file"], {})
        update_task_meta(module, item["file"], {
            "last_case_set_id": case_set_id,
            "last_case_set_title": title,
            "last_generated_at": summary.get("generated_at"),
            "last_case_count": 1,
            "last_manual_case_count": len(converted_payload.get("manual_cases", [])),
            "execution_level": static_check.get("executionLevel") or "draft",
            "yaml_static_ok": bool(static_check.get("ok")),
            "yaml_static_errors": list(static_check.get("errors") or [])[:8],
            "yaml_static_warnings": list(static_check.get("warnings") or [])[:8],
        })
    jobs = []
    job_skipped_yaml_files = []
    score_by_file = {item.get("file"): item for item in yaml_executable_scores}
    if create_job:
        for item in yaml_items:
            static_check = static_by_file.get(item["file"], {})
            if static_check and not static_check.get("ok"):
                job_skipped_yaml_files.append({
                    "file": item["file"],
                    "executionLevel": static_check.get("executionLevel") or "draft",
                    "errors": list(static_check.get("errors") or [])[:8],
                    "reason": "静态可执行校验未通过，已降级为草稿，未创建 Runner 任务。",
                })
                continue
            executable_score = score_by_file.get(item["file"], {})
            if executable_score and executable_score.get("executionLevel") != "executable":
                job_skipped_yaml_files.append({
                    "file": item["file"],
                    "executionLevel": executable_score.get("executionLevel") or executable_score.get("level") or "draft",
                    "score": executable_score.get("score") or 0,
                    "reasons": list(executable_score.get("reasons") or [])[:8],
                    "reason": "YAML 可执行性评分未达到 executable，未创建 Runner 任务。",
                })
                continue
            jobs.append(create_pending_job(
                module,
                item["file"],
                auto_optimize=auto_optimize,
                device_id=device_id,
                runner_id=runner_id,
                device_strategy=device_strategy,
                run_mode=run_mode
            ))
    job = jobs[0] if jobs else None
    return {
        "ok": True,
        "case_set_id": case_set_id,
        "asset": meta,
        "cases": converted_payload,
        "manual_cases": converted_payload.get("manual_cases", []),
        "module": module,
        "file": yaml_file,
        "files": yaml_files,
        "yamlFiles": yaml_files,
        "yamlFileCount": len(yaml_files),
        "content": yaml,
        "caseCount": len(converted_payload.get("cases", [])),
        "manualCaseCount": len(converted_payload.get("manual_cases", [])),
        "scenarioCount": len(converted_payload.get("scenarios", [])),
        "analysis": converted_payload.get("analysis", {}),
        "scenarios": converted_payload.get("scenarios", []),
        "review": converted_payload.get("review", {}),
        "coverageAudit": coverage_audit,
        "knowledgePages": used_reference_pages,
        "yamlCheck": yaml_check,
        "yamlExecutability": yaml_executability,
        "yamlExecutableScores": yaml_executable_scores,
        "generatedCaseGroups": generated_case_groups,
        "executable_cases": generated_case_groups["executable_cases"],
        "needs_review_cases": generated_case_groups["needs_review_cases"],
        "draft_cases": generated_case_groups["draft_cases"],
        "yamlSmokeStability": yaml_smoke_stability,
        "yamlStaticValidation": yaml_static_validation,
        "yamlStaticRepair": review.get("yaml_static_repair"),
        "summary": summary,
        "summaryFiles": summary_files,
        "job": job,
        "jobs": jobs,
        "jobSkippedYamlFiles": job_skipped_yaml_files,
    }



def list_generate_jobs(limit=80):
    if not os.path.exists(GENERATE_JOB_DIR):
        return []
    try:
        names = [name for name in os.listdir(GENERATE_JOB_DIR) if name.endswith(".json")]
    except Exception:
        return []
    rows = []
    for name in sorted(names, reverse=True)[:limit * 2]:
        try:
            job = read_json_file(safe_join(GENERATE_JOB_DIR, name), default=None)
        except Exception:
            job = None
        if isinstance(job, dict):
            job = expire_generate_job_if_stale(job, persist=True)
            rows.append(sanitize_generate_job_for_client(job))
    rows.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
    return rows[:limit]



def save_generate_job(job):
    os.makedirs(GENERATE_JOB_DIR, exist_ok=True)
    job.setdefault("job_id", generate_job_id())
    job.setdefault("ok", True)
    job.setdefault("created_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    job.setdefault("updated_at", job.get("created_at"))
    with GENERATE_LOCK:
        write_json_file(generate_job_path(job["job_id"]), job)



def load_generate_job(job_id):
    try:
        job = read_json_file(generate_job_path(job_id), default=None)
        if isinstance(job, dict):
            return expire_generate_job_if_stale(job, persist=True)
        return job
    except Exception:
        return None



def delete_generate_job(job_id):
    path = generate_job_path(job_id)
    with GENERATE_LOCK:
        job = read_json_file(path, default=None)
        if not isinstance(job, dict):
            return {"ok": False, "deleted": False, "error": "生成任务不存在"}
        job = expire_generate_job_if_stale(job, persist=False)
        status = str(job.get("status") or "").strip().lower()
        if status in {"pending", "running"}:
            return {"ok": False, "deleted": False, "error": "生成任务仍在执行，请先取消后再删除"}
        try:
            os.remove(path)
            return {"ok": True, "deleted": True, "job": sanitize_generate_job_for_client(job)}
        except FileNotFoundError:
            return {"ok": True, "deleted": False, "job": sanitize_generate_job_for_client(job)}



def generate_job_id():
    return unique_millis_id("gen")



def generate_retry_request_from_job(job):
    request = job.get("request_data") or job.get("requestData") or {}
    if isinstance(request, dict) and request:
        next_request = dict(request)
        next_request["retry_from_job_id"] = job.get("job_id", "")
        next_request["retry"] = True
        case_set_id = next_request.get("case_set_id") or next_request.get("caseSetId") or job.get("case_set_id")
        if case_set_id and not (next_request.get("figma_url") or next_request.get("figmaUrl")):
            summary = read_json_file(generation_summary_path(case_set_id), default={}) or {}
            meta = read_json_file(asset_meta_path(case_set_id), default={}) or {}
            figma_url = find_figma_url_for_case_set(case_set_id, summary=summary, meta=meta)
            if figma_url:
                next_request["figma_url"] = figma_url
                next_request.setdefault("figma_mode", meta.get("figma_mode") or meta.get("figmaMode") or "smart")
                next_request.setdefault("figma_limit", meta.get("figma_limit") or meta.get("figmaLimit") or FIGMA_PARSE_LIMIT)
        return next_request
    case_set_id = job.get("case_set_id") or (job.get("result") or {}).get("case_set_id")
    if case_set_id:
        summary = read_json_file(generation_summary_path(case_set_id), default={}) or {}
        meta = read_json_file(asset_meta_path(case_set_id), default={}) or {}
        figma_url = find_figma_url_for_case_set(case_set_id, summary=summary, meta=meta)
        if meta.get("files"):
            return {
                "case_set_id": case_set_id,
                "title": summary.get("title") or meta.get("title") or job.get("title") or "UI自动化用例",
                "module": summary.get("module") or meta.get("module") or job.get("module") or "AI测试",
                "file": summary.get("yaml_file") or job.get("file") or f"task-{slug_for_file(summary.get('title') or meta.get('title') or 'UI自动化用例')}.yaml",
                "figma_url": figma_url,
                "figma_mode": meta.get("figma_mode") or meta.get("figmaMode") or "smart",
                "figma_limit": meta.get("figma_limit") or meta.get("figmaLimit") or FIGMA_PARSE_LIMIT,
                "knowledge_page_ids": meta.get("knowledge_page_ids") or meta.get("knowledgePageIds") or [],
                "knowledge_tier": meta.get("knowledge_tier") or meta.get("knowledgeTier") or "all",
                "reuse_assets": True,
                "regenerate": True,
                "retry_from_job_id": job.get("job_id", ""),
                "retry": True,
            }
    return {}



def build_generation_summary(case_set_id, title, module, yaml_file, converted_payload, used_knowledge_pages=None, yaml_check=None, yaml_executability=None):
    cases = []
    priority_counts = {}
    smoke_count = 0
    for index, case in enumerate(converted_payload.get("cases", []), start=1):
        if not isinstance(case, dict):
            continue
        row = ensure_case_trace(case, index)
        priority = case_priority(row)
        smoke = is_smoke_case(row)
        priority_counts[priority] = priority_counts.get(priority, 0) + 1
        if smoke:
            smoke_count += 1
        cases.append({
            "case_id": row.get("case_id"),
            "title": row.get("title") or row.get("name") or "未命名用例",
            "priority": priority,
            "smoke": smoke,
            "feature": first_non_empty(case_value(row, "feature", "module", "business_feature")),
            "scenario": first_non_empty(case_value(row, "scenario", "scene")),
            "start_page": first_non_empty(case_value(row, "start_page", "startPage")),
            "business_path": first_non_empty(case_value(row, "business_path", "businessPath", "path")),
            "expected_result": first_non_empty(case_value(row, "expected_result", "expectedResult", "expected")),
            "coverage": first_non_empty(case_value(row, "coverage", "coverage_point", "test_point")),
            "risk": first_non_empty(case_value(row, "risk", "risks", "business_risk")),
            "tags": case_tags(row),
            "data_requirements": first_non_empty(case_value(row, "data_requirements", "dataRequirements", "test_data", "testData")),
            "automation_reason": first_non_empty(case_value(row, "automation_reason", "automationReason", "why_automated", "whyAutomated")),
            "preconditions": normalize_text_list(row.get("preconditions") or row.get("precondition")),
            "steps": normalize_text_list(row.get("steps") or row.get("flow")),
            "assertions": normalize_text_list(row.get("assertions") or row.get("expects") or row.get("expect"))
        })

    manual_cases = converted_payload.get("manual_cases", []) or []
    scenarios = converted_payload.get("scenarios", []) or []
    requirement_analysis = build_requirement_analysis_summary(converted_payload.get("analysis", {}))
    summary = {
        "case_set_id": case_set_id,
        "title": title,
        "module": module,
        "yaml_file": yaml_file,
        "yaml_files": [yaml_file] if yaml_file else [],
        "yaml_file_count": 1 if yaml_file else 0,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "counts": {
            "scenario_count": len(scenarios),
            "automation_case_count": len(cases),
            "manual_case_count": len(manual_cases),
            "smoke_count": smoke_count,
            "priority_counts": priority_counts
        },
        "analysis": converted_payload.get("analysis", {}),
        "requirement_analysis": requirement_analysis,
        "scenarios": scenarios,
        "cases": cases,
        "manual_cases": manual_cases,
        "review": converted_payload.get("review", {}),
        "knowledge_pages": used_knowledge_pages or [],
        "yaml_check": yaml_check or {},
        "yaml_executability": yaml_executability or {}
    }
    summary["report_checkpoints"] = build_report_checkpoints(summary)
    return summary



def generation_artifact_filename(summary, case_set_id, suffix):
    raw_suffix = str(suffix or "").strip().lstrip("_")
    suffix_ext = ""
    if "." in raw_suffix:
        suffix_ext = "." + raw_suffix.rsplit(".", 1)[-1]
        suffix_stem = raw_suffix[: -len(suffix_ext)].strip(" ._")
    else:
        suffix_stem = raw_suffix
    title = str((summary or {}).get("title") or case_set_id or "测试用例").strip()
    stem = clean_asset_filename(f"{title}_{suffix_stem}".strip("_"), default=f"{case_set_id or 'cases'}_{suffix_stem or 'artifact'}")
    if suffix_ext:
        stem = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", stem)
        return stem + suffix_ext
    return stem



def generation_mindmap_deleted_path(case_set_id):
    return safe_join(CASE_DIR, case_set_id, ".mindmap_deleted")


def generation_mindmap_record_deleted_path(case_set_id):
    return safe_join(CASE_DIR, case_set_id, ".mindmap_record_deleted")


def generation_mindmap_fallback_flag_path(case_set_id, flag_name):
    flag_dir = safe_join(LEARNING_DIR, "mindmap-delete-flags")
    return safe_join(flag_dir, f"{clean_id(case_set_id, 'case_set')}.{flag_name}")


def generation_mindmap_deleted_paths(case_set_id):
    return [
        generation_mindmap_deleted_path(case_set_id),
        generation_mindmap_fallback_flag_path(case_set_id, "mindmap_deleted"),
    ]


def generation_mindmap_record_deleted_paths(case_set_id):
    return [
        generation_mindmap_record_deleted_path(case_set_id),
        generation_mindmap_fallback_flag_path(case_set_id, "mindmap_record_deleted"),
    ]


def generation_mindmap_is_deleted(case_set_id):
    return any(os.path.exists(path) for path in generation_mindmap_deleted_paths(case_set_id))


def generation_mindmap_record_is_deleted(case_set_id):
    return any(os.path.exists(path) for path in generation_mindmap_record_deleted_paths(case_set_id))


def _write_first_available_flag(paths, text):
    last_error = None
    for path in paths:
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            write_text_file(path, text)
            return path
        except Exception as exc:
            last_error = exc
            continue
    if last_error:
        raise last_error
    raise OSError("没有可写的删除标记路径")


def mark_generation_mindmap_deleted(case_set_id, text=None):
    return _write_first_available_flag(
        generation_mindmap_deleted_paths(case_set_id),
        text or time.strftime("%Y-%m-%d %H:%M:%S"),
    )


def mark_generation_mindmap_record_deleted(case_set_id, text=None):
    return _write_first_available_flag(
        generation_mindmap_record_deleted_paths(case_set_id),
        text or time.strftime("%Y-%m-%d %H:%M:%S"),
    )


def clear_generation_mindmap_deleted(case_set_id, include_record=False):
    paths = list(generation_mindmap_deleted_paths(case_set_id))
    if include_record:
        paths.extend(generation_mindmap_record_deleted_paths(case_set_id))
    for path in paths:
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception:
            continue


def remove_generation_mindmap_file(case_set_id):
    mm_path = generation_mindmap_path(case_set_id)
    existed = os.path.exists(mm_path)
    removed = False
    error = ""
    if existed:
        try:
            os.remove(mm_path)
            removed = True
        except Exception as exc:
            error = str(exc)
    return {"path": mm_path, "existed": existed, "removed": removed, "error": error}



def generation_mindmap_path(case_set_id):
    return safe_join(CASE_DIR, case_set_id, "cases.mm")



def generation_summary_path(case_set_id):
    return safe_join(CASE_DIR, case_set_id, "summary.json")


def _mindmap_time_value(value):
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value or "").strip()
    if not text:
        return 0.0
    normalized = text.replace("T", " ").replace("Z", "").split(".")[0]
    for fmt, size in (("%Y-%m-%d %H:%M:%S", 19), ("%Y-%m-%d %H:%M", 16), ("%Y-%m-%d", 10)):
        try:
            return time.mktime(time.strptime(normalized[:size], fmt))
        except Exception:
            continue
    return 0.0



def list_generation_mindmaps(limit=100):
    if not os.path.isdir(CASE_DIR):
        return []
    records = []
    try:
        names = sorted(os.listdir(CASE_DIR), reverse=True)
    except Exception:
        return []
    for name in names:
        try:
            path = safe_join(CASE_DIR, name)
        except ValueError:
            continue
        if not os.path.isdir(path) or not os.path.exists(os.path.join(path, "summary.json")):
            continue
        if generation_mindmap_record_is_deleted(name):
            continue
        record = generation_mindmap_record(name)
        if record:
            records.append(record)
    records.sort(
        key=lambda item: max(
            _mindmap_time_value(item.get("mindmap_sort_ts")),
            _mindmap_time_value(item.get("mindmap_updated_ts")),
            _mindmap_time_value(item.get("mindmap_updated_at")),
            _mindmap_time_value(item.get("generated_ts")),
            _mindmap_time_value(item.get("generated_at")),
        ),
        reverse=True,
    )
    return records[:max(1, min(500, limit))]



def sanitize_generate_job_for_client(job):
    if not isinstance(job, dict):
        return job
    safe = dict(job)
    request = safe.pop("request_data", None) or safe.pop("requestData", None)
    safe["can_retry"] = job.get("type") in ("generate", "mindmap_only") and bool(generate_retry_request_from_job(job))
    if request:
        safe["request_summary"] = summarize_generate_request(request)
    if safe.get("error_trace"):
        safe["error_trace"] = str(safe.get("error_trace"))[-1200:]
    return safe



def update_generate_job(job_id, **changes):
    with GENERATE_LOCK:
        job = read_json_file(generate_job_path(job_id), default={}) or {}
        job.setdefault("job_id", job_id)
        job.setdefault("ok", True)
        job.setdefault("created_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        now_text = time.strftime("%Y-%m-%d %H:%M:%S")
        if changes.get("status") == "running" and not job.get("started_at"):
            changes.setdefault("started_at", now_text)
        if changes.get("status") in GENERATE_JOB_TERMINAL_STATUSES:
            changes.setdefault("finished_at", now_text)
        job.update(changes)
        if job.get("started_at") and job.get("finished_at"):
            started_ts = parse_time(job.get("started_at")) or parse_time(job.get("created_at"))
            finished_ts = parse_time(job.get("finished_at"))
            if started_ts and finished_ts and finished_ts >= started_ts:
                job["elapsed_seconds"] = int(finished_ts - started_ts)
        job["updated_at"] = now_text
        write_json_file(generate_job_path(job_id), job)
        return job



def write_generation_mindmap(case_set_id, summary):
    mm_path = generation_mindmap_path(case_set_id)
    os.makedirs(os.path.dirname(mm_path), exist_ok=True)
    clear_generation_mindmap_deleted(case_set_id)
    write_text_file(mm_path, build_generation_mindmap(summary))
    return mm_path



def write_generation_summary(case_set_id, summary):
    summary["report_checkpoints"] = build_report_checkpoints(summary)
    json_path = generation_summary_path(case_set_id)
    md_path = generation_summary_md_path(case_set_id)
    mm_path = generation_mindmap_path(case_set_id)
    write_json_file(json_path, summary)
    lines = [
        f"# {summary.get('title') or '测试用例'} 生成汇总",
        "",
        f"- 批次 ID：{summary.get('case_set_id')}",
        f"- 模块：{summary.get('module')}",
        f"- YAML：{summary.get('yaml_file')}",
        f"- YAML 文件数：{summary.get('yaml_file_count') or len(summary.get('yaml_files') or []) or 0}",
        f"- 生成时间：{summary.get('generated_at')}",
        "",
    ]
    yaml_files = [item for item in (summary.get("yaml_files") or []) if item]
    if len(yaml_files) > 1:
        lines.extend(["## YAML 文件", ""])
        lines.extend(f"- {item}" for item in yaml_files)
        lines.append("")
    counts = summary.get("counts") or {}
    priority_counts = counts.get("priority_counts") or {}
    report_checkpoints = normalize_text_list(summary.get("report_checkpoints"))[:5]
    lines.extend([
        "## 统计",
        "",
        f"- 测试场景：{counts.get('scenario_count', 0)}",
        f"- 自动化用例：{counts.get('automation_case_count', 0)}",
        f"- 冒烟用例：{counts.get('smoke_count', 0)}",
        f"- 转人工/待准备：{counts.get('manual_case_count', 0)}",
        f"- 优先级分布：{', '.join(f'{k}={v}' for k, v in sorted(priority_counts.items())) or '-'}",
        "",
        "## 需求分析",
        "",
    ])
    if report_checkpoints:
        lines.extend(["## 测试报告检查点", ""])
        for idx, item in enumerate(report_checkpoints, start=1):
            lines.append(f"{idx}. {markdown_cell(item)}")
        lines.append("")
    requirement_analysis = summary.get("requirement_analysis") or {}
    analysis_rows = [
        ("体检等级", [f"{requirement_analysis.get('readiness_level') or '-'} / {requirement_analysis.get('readiness_score', 0)}"]),
        ("置信度", [requirement_analysis.get("confidence") or "-"]),
        ("业务目标", requirement_analysis.get("business_goals")),
        ("用户角色", requirement_analysis.get("roles")),
        ("入口路径", requirement_analysis.get("entry_points")),
        ("状态前置", requirement_analysis.get("state_assumptions")),
        ("数据假设", requirement_analysis.get("data_assumptions")),
        ("可见结果", requirement_analysis.get("visible_outcomes")),
        ("核心风险", requirement_analysis.get("risks")),
        ("需求点", requirement_analysis.get("requirement_points")),
        ("缺失资料", requirement_analysis.get("missing_inputs")),
        ("待确认问题", requirement_analysis.get("questions")),
        ("阻断项", requirement_analysis.get("blockers")),
        ("当前假设", requirement_analysis.get("assumptions")),
    ]
    for label, values in analysis_rows:
        if values:
            lines.append(f"- {label}：{'；'.join(markdown_cell(item) for item in values)}")
    coverage_matrix = requirement_analysis.get("coverage_matrix") or []
    if coverage_matrix:
        lines.extend(["", "### 覆盖矩阵", "", "| 功能 | 需求点 | 自动化用例 | 转人工/待准备 | 未覆盖原因 |", "| --- | --- | --- | --- | --- |"])
        for item in coverage_matrix:
            lines.append("| {feature} | {point} | {auto} | {manual} | {reason} |".format(
                feature=markdown_cell(item.get("feature")),
                point=markdown_cell(item.get("requirement_point")),
                auto=markdown_cell("、".join(item.get("auto_cases") or [])),
                manual=markdown_cell("、".join(item.get("manual_cases") or [])),
                reason=markdown_cell(item.get("uncovered_reason")),
            ))
    figma_filter = ((summary.get("review") or {}).get("figma_requirement_filter") or {})
    if figma_filter:
        lines.extend(["", "### Figma 需求相关性筛选", ""])
        lines.append(f"- 已使用：{figma_filter.get('used_count', 0)} 个相关页面")
        lines.append(f"- 已忽略：{figma_filter.get('ignored_count', 0)} 个无关候选")
        if figma_filter.get("saved_design_count") is not None:
            lines.append(f"- 已保存 UI 稿：{figma_filter.get('saved_design_count', 0)} 份")
        used_pages = figma_filter.get("used_pages") or []
        if used_pages:
            lines.extend(["", "| 使用页面 | 分数 | 原因 |", "| --- | --- | --- |"])
            for page in used_pages:
                lines.append("| {name} | {score} | {reason} |".format(
                    name=markdown_cell(page.get("page_name")),
                    score=markdown_cell(page.get("score")),
                    reason=markdown_cell(page.get("reason")),
                ))
        ignored_pages = figma_filter.get("ignored_pages") or []
        if ignored_pages:
            lines.extend(["", "| 忽略页面 | 分数 | 原因 |", "| --- | --- | --- |"])
            for page in ignored_pages[:8]:
                lines.append("| {name} | {score} | {reason} |".format(
                    name=markdown_cell(page.get("page_name")),
                    score=markdown_cell(page.get("score")),
                    reason=markdown_cell(page.get("reason")),
                ))
    ui_design_assets = summary.get("ui_design_assets") or []
    if ui_design_assets:
        lines.extend(["", "### 当前批次 UI 设计稿", "", "| 来源 | 页面 | 文件 | 大小 | 说明 |", "| --- | --- | --- | --- | --- |"])
        for item in ui_design_assets:
            figma = item.get("figma") or {}
            reason = item.get("description") or figma.get("relevance_reason") or ""
            lines.append("| {source} | {page} | {filename} | {size} | {reason} |".format(
                source=markdown_cell(item.get("source")),
                page=markdown_cell(item.get("page_name")),
                filename=markdown_cell(item.get("filename") or item.get("name")),
                size=markdown_cell(item.get("size")),
                reason=markdown_cell(reason),
            ))
    yaml_reference_examples = ((summary.get("review") or {}).get("yaml_reference_examples") or [])
    if yaml_reference_examples:
        lines.extend(["", "### YAML 步骤经验参考", ""])
        lines.append("生成 YAML 前已从现有用例库检索可复用步骤写法；这里只展示参考来源，实际生成仍以本次需求和 Figma 为准。")
        lines.extend(["", "| 参考用例 | 来源 YAML | 匹配词 | 动作类型 |", "| --- | --- | --- | --- |"])
        for item in yaml_reference_examples[:8]:
            lines.append("| {title} | {file} | {matched} | {actions} |".format(
                title=markdown_cell(item.get("title")),
                file=markdown_cell(item.get("file")),
                matched=markdown_cell("、".join(item.get("matched_terms") or [])),
                actions=markdown_cell(" -> ".join(item.get("actions") or [])),
            ))
    lines.extend([
        "",
        "## 自动化用例",
        "",
        "| ID | 优先级 | 冒烟 | 用例 | 场景 | 覆盖点 |",
        "| --- | --- | --- | --- | --- | --- |",
    ])
    for case in summary.get("cases") or []:
        lines.append(
            "| {case_id} | {priority} | {smoke} | {title} | {scenario} | {coverage} |".format(
                case_id=markdown_cell(case.get("case_id")),
                priority=markdown_cell(case.get("priority")),
                smoke="是" if case.get("smoke") else "否",
                title=markdown_cell(case.get("title")),
                scenario=markdown_cell(case.get("scenario")),
                coverage=markdown_cell(case.get("coverage")),
            )
        )
    manual_cases = summary.get("manual_cases") or []
    if manual_cases:
        lines.extend(["", "## 转人工/待准备", "", "| 用例 | 原因 | 准备建议 |", "| --- | --- | --- |"])
        for case in manual_cases:
            if isinstance(case, dict):
                lines.append("| {title} | {reason} | {setup} |".format(
                    title=markdown_cell(case.get("title") or case.get("name")),
                    reason=markdown_cell(case.get("reason")),
                    setup=markdown_cell(case.get("suggested_setup") or case.get("setup")),
                ))
    review = summary.get("review") or {}
    review_text = first_non_empty(review.get("coverage_check"), review.get("automation_check"), review.get("assertion_check"))
    if review_text:
        lines.extend(["", "## 自评审", "", review_text])
    os.makedirs(os.path.dirname(md_path), exist_ok=True)
    write_text_file(md_path, "\n".join(lines) + "\n")
    write_generation_mindmap(case_set_id, summary)
    return {"json": json_path, "markdown": md_path, "mindmap": mm_path}



def new_case_set_id():
    return unique_millis_id("cs")



def cases_path(case_set_id):
    return safe_join(CASE_DIR, case_set_id, "cases.json")



def changed_line_count(old_text, new_text):
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    max_len = max(len(old_lines), len(new_lines))
    changed = 0
    for idx in range(max_len):
        old = old_lines[idx] if idx < len(old_lines) else ""
        new = new_lines[idx] if idx < len(new_lines) else ""
        if old != new:
            changed += 1
    return changed



def yaml_diff_summary(old_text, new_text, limit=160):
    old_lines = (old_text or "").splitlines()
    new_lines = (new_text or "").splitlines()
    diff_lines = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile="before.yaml",
        tofile="after.yaml",
        n=3,
        lineterm=""
    ))
    if len(diff_lines) > limit:
        hidden = len(diff_lines) - limit
        diff_lines = diff_lines[:limit] + [f"... diff 已截断，还有 {hidden} 行未展示"]
    return "\n".join(diff_lines)



def case_ui_design_dir(case_set_id):
    return safe_join(ASSET_DIR, case_set_id, "ui_designs")



def generation_summary_md_path(case_set_id):
    return safe_join(CASE_DIR, case_set_id, "summary.md")



def generate_job_path(job_id):
    return safe_join(GENERATE_JOB_DIR, f"{job_id}.json")



def generate_job_cancelled(job_id):
    job = load_generate_job(job_id) or {}
    return job.get("status") == "cancelled"


def generate_job_should_stop(job_id):
    job = load_generate_job(job_id) or {}
    return str(job.get("status") or "").strip().lower() in {"cancelled", "timeout", "failed"}



def iter_raw_generate_jobs(limit=300):
    if not os.path.exists(GENERATE_JOB_DIR):
        return []
    try:
        names = [name for name in os.listdir(GENERATE_JOB_DIR) if name.endswith(".json")]
    except Exception:
        return []
    rows = []
    for name in sorted(names, reverse=True)[:limit]:
        try:
            job = read_json_file(safe_join(GENERATE_JOB_DIR, name), default=None)
        except Exception:
            job = None
        if isinstance(job, dict):
            rows.append(job)
    rows.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
    return rows



def summarize_generate_request(request):
    if not isinstance(request, dict) or not request:
        return {}
    files = request.get("files") or []
    summary = {
        "title": request.get("title") or "",
        "module": request.get("module") or "",
        "file": request.get("file") or "",
        "case_set_id": request.get("case_set_id") or request.get("caseSetId") or "",
        "reuse_assets": safe_bool(request.get("reuse_assets") or request.get("reuseAssets") or request.get("regenerate")),
        "retry": safe_bool(request.get("retry")),
        "retry_from_job_id": request.get("retry_from_job_id") or "",
        "file_count": len(files) if isinstance(files, list) else 0,
        "has_files": bool(files),
        "has_figma": bool(request.get("figma_url") or request.get("figmaUrl")),
        "has_supplement": bool(request.get("supplement") or request.get("supplement_text") or request.get("confirmation")),
    }
    return {key: value for key, value in summary.items() if value not in ("", False, 0)}



def generation_failure_detail(error, job=None):
    job = job or {}
    raw = str(error or "").strip() or "生成失败"
    lower = raw.lower()
    stage = job.get("step") or "AI生成"
    progress = safe_int(job.get("progress"), 0)
    error_type = "generation_error"
    suggestion = "查看上传资料是否完整；如需求或 UI 信息不足，可在生成分析中补充确认项、截图或 UI 稿后重新生成。"
    message = raw
    if "capacity" in lower or "at capacity" in lower or "容量" in raw or "繁忙" in raw:
        error_type = "model_capacity"
        message = "模型当前容量不足或繁忙"
        suggestion = (
            "这不是需求文档错误。可以稍后重试，或在模型配置里临时切换到其他可用模型；"
            "如果是脑图-only任务，建议先保留关键需求和关键 Frame，减少一次性输入。"
        )
    elif "timeout" in lower or "timed out" in lower or "超时" in raw:
        error_type = "model_timeout"
        message = "千问模型响应超时"
        suggestion = (
            "优先减少单次上传的大图、长文档或重复截图，只保留关键需求和关键 UI；"
            "也可以在生成分析中先采纳/忽略待确认项后重新生成。服务端已启用更长超时和自动重试。"
        )
    elif "dashscope" in lower or "model" in lower or "qwen" in lower or "千问" in raw:
        error_type = "model_call_error"
        suggestion = "检查 DashScope Key、模型名和网络；确认后可直接点重新生成。"
    elif "json" in lower:
        error_type = "model_json_error"
        suggestion = "模型返回格式不完整。建议补充更明确的需求范围和 UI 截图，再重新生成。"
    elif "yaml" in lower:
        error_type = "yaml_convert_error"
        suggestion = "用例已生成但 YAML 转换失败。建议减少复杂条件描述，或把无法稳定执行的场景标记为人工待准备。"
    elif "figma" in lower:
        error_type = "figma_error"
        suggestion = "检查 Figma 链接、Token 和 Frame 范围；也可以先上传关键截图替代 Figma。"
    return {
        "stage": stage,
        "progress": progress,
        "type": error_type,
        "message": message,
        "error": raw,
        "suggestion": suggestion,
        "failed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }



def generate_mindmap_from_request(d, job_id=None):
    title = d.get("title") or "测试用例脑图"
    module = d.get("module") or "AI测试"
    case_set_id = d.get("case_set_id") or new_case_set_id()
    files = d.get("files") or []
    prepared_figma_context = _prepared_figma_context_from_request(d)
    has_prepared_figma = bool(prepared_figma_context)
    has_figma = bool((d.get("figma_url") or d.get("figmaUrl") or "").strip() or has_prepared_figma)
    mindmap_mode = str(d.get("mindmap_mode") or d.get("mindmapMode") or "full").strip().lower() or "full"
    require_ai_planning = safe_bool(d.get("requireAiPlanning") or d.get("require_ai_planning"), False)
    use_yaml_baseline_context = safe_bool(
        d.get("useYamlBaselineContext") or d.get("use_yaml_baseline_context"),
        False,
    )
    requirement_contract = d.get("requirementCoverageContract") or d.get("requirement_coverage_contract") or {}
    if not isinstance(requirement_contract, dict):
        requirement_contract = {}
    model_config = ai_model_config_from_request(d)

    if job_id:
        update_generate_job(job_id, progress=10, step="保存资料", message="正在保存脑图资料")
    meta = save_asset_files(case_set_id, title, module, files) if files else {
        "case_set_id": case_set_id,
        "title": title,
        "module": module,
        "files": [],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    if not files:
        os.makedirs(safe_join(ASSET_DIR, case_set_id), exist_ok=True)
        write_json_file(asset_meta_path(case_set_id), meta)
    meta = update_asset_request_context(case_set_id, d)
    if has_figma:
        removed = clear_auto_figma_ui_design_assets(case_set_id)
        if job_id and removed:
            suffix = "，将复用 Agent 准备阶段结果" if has_prepared_figma else "，将重新按需求筛选"
            update_generate_job(job_id, progress=12, step="刷新 Figma UI 稿", message=f"已清理 {removed} 份旧的自动 Figma UI 稿{suffix}")

    if job_id:
        update_generate_job(job_id, progress=25, step="解析资料", message="正在解析需求、截图和设计资料")
    requirement_text_assets, uploaded_image_assets = load_asset_contents(case_set_id, meta)
    if not requirement_text_assets and not uploaded_image_assets and not has_figma:
        raise ValueError("没有可用于生成脑图的文本、图片资产或 Figma 链接")

    app_package = d.get("app_package") or d.get("appPackage") or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    query_text = "\n".join([title, module] + requirement_text_assets)
    selected_page_ids = d.get("knowledge_page_ids") or d.get("knowledgePageIds") or []
    knowledge_tier = d.get("knowledge_tier") or d.get("knowledgeTier") or "all"
    use_knowledge_context = bool(d.get("use_knowledge_context") or d.get("useKnowledgeContext") or selected_page_ids)

    if job_id:
        context_msg = "正在匹配相关 Figma 页面"
        if use_knowledge_context:
            context_msg = "正在匹配已选择的页面知识和相关 Figma 页面"
        update_generate_job(job_id, progress=35, step="匹配上下文", message=context_msg)
    with ThreadPoolExecutor(max_workers=2) as executor:
        knowledge_future = (
            executor.submit(load_knowledge_context, app_package, query_text, 6, selected_page_ids, knowledge_tier)
            if use_knowledge_context else None
        )
        figma_future = None
        if not has_prepared_figma:
            figma_future = executor.submit(load_figma_generation_context, d, app_package, job_id, query_text, case_set_id, title, module)
        if knowledge_future:
            try:
                knowledge_texts, knowledge_images, used_knowledge_pages = knowledge_future.result()
            except Exception:
                knowledge_texts, knowledge_images, used_knowledge_pages = [], [], []
        else:
            knowledge_texts, knowledge_images, used_knowledge_pages = [], [], []
        if has_prepared_figma:
            figma_texts = prepared_figma_context.get("textAssets") or []
            figma_images = prepared_figma_context.get("imageAssets") or []
            used_figma_pages = prepared_figma_context.get("usedPages") or []
            ignored_figma_pages = prepared_figma_context.get("ignoredPages") or []
            saved_figma_designs = _save_prepared_figma_design_assets(
                case_set_id,
                prepared_figma_context,
                title=title,
                module=module,
            )
            if job_id:
                update_generate_job(
                    job_id,
                    progress=38,
                    step="复用 Figma 解析",
                    message=f"已复用准备阶段解析结果：页面 {len(used_figma_pages)} 个，Figma UI 图 {len(figma_images)} 张",
                )
        else:
            try:
                figma_texts, figma_images, used_figma_pages, ignored_figma_pages, saved_figma_designs = figma_future.result()
            except Exception:
                figma_texts, figma_images, used_figma_pages, ignored_figma_pages, saved_figma_designs = [], [], [], [], []

    visual_text_assets = figma_texts + knowledge_texts
    figma_soft_evidence_text = build_figma_soft_evidence_context_text(figma_texts)
    # 脑图只让当前 Figma 和人工上传截图进入视觉模型；
    # 页面知识库截图仅作为文本上下文，避免把历史无关 UI 图重新带回来。
    visual_image_assets = figma_images + uploaded_image_assets
    max_visual_images = int(MINDMAP_VISUAL_MAX_IMAGES)
    mindmap_visual_image_assets = visual_image_assets[:max_visual_images] if max_visual_images > 0 else []
    selected_figma_count = min(len(figma_images), len(mindmap_visual_image_assets))
    mindmap_figma_images = figma_images[:selected_figma_count]
    mindmap_uploaded_images = mindmap_visual_image_assets[selected_figma_count:]
    mindmap_visual_image_assets = mindmap_figma_images + mindmap_uploaded_images
    skipped_visual_image_count = max(0, len(visual_image_assets) - len(mindmap_visual_image_assets))
    stage1_text_assets = list(requirement_text_assets or [])
    if figma_soft_evidence_text:
        stage1_text_assets.append(figma_soft_evidence_text)
    if knowledge_texts:
        stage1_text_assets.extend(knowledge_texts)
    if not stage1_text_assets:
        stage1_text_assets = [
            "未提供独立需求文档，请根据标题、模块、当前 Figma/截图先归纳业务范围，再生成测试场景和用例脑图。"
        ]
    plan_validation_issues = normalize_text_list(
        d.get("planValidationIssues") or d.get("plan_validation_issues")
    )
    if require_ai_planning and plan_validation_issues:
        stage1_text_assets = list(stage1_text_assets) + [
            "【上一轮 AI 计划门禁反馈，不是新增需求】\n"
            "请修正场景分支、页面层级或可见检查点，不得删除原始需求：\n- "
            + "\n- ".join(plan_validation_issues[:8])
        ]
    yaml_reference_examples = []
    baseline_rerank_trace = {}
    if use_yaml_baseline_context:
        baseline_query_text = "\n".join([title, module, query_text] + stage1_text_assets)
        baseline_candidates = search_baseline_examples(
            baseline_query_text,
            module=module,
            limit=20,
            allow_fallback=False,
        )
        try:
            baseline_rerank = call_skill_baseline_reranker(
                title,
                module,
                baseline_query_text,
                baseline_candidates,
                model_config=model_config,
                limit=3,
            )
            yaml_reference_examples = baseline_rerank.get("selected") or baseline_candidates[:3]
            baseline_rerank_trace = baseline_rerank.get("trace") or {}
        except Exception as exc:
            yaml_reference_examples = baseline_candidates[:3]
            baseline_rerank_trace = {"enabled": True, "fallback": True, "error": str(exc)}
        yaml_reference_text = build_yaml_reference_examples_text(yaml_reference_examples)
        if yaml_reference_text:
            stage1_text_assets = list(stage1_text_assets) + [yaml_reference_text]
    if job_id:
        update_generate_job(job_id, progress=50, step="生成用例结构", message="正在生成场景、用例、边界和人工待准备事项")
    if USE_AI_SKILL_PIPELINE:
        try:
            payload = build_cases_payload_from_skills(
                title,
                module,
                stage1_text_assets,
                mode="mindmap",
                model_config=model_config,
                app_package=app_package,
                app_name=d.get("appName") or d.get("app_name") or "",
                allow_entry_visibility_fast_path=not require_ai_planning,
                require_ai_core=require_ai_planning,
                requirement_contract=requirement_contract,
            )
        except Exception as e:
            if require_ai_planning:
                payload = {
                    "title": title,
                    "module": module,
                    "analysis": {},
                    "scenarios": [],
                    "cases": [],
                    "manual_cases": [],
                    "review": {
                        "skill_pipeline": "requirement_analyzer.v1",
                        "skill_pipeline_error": str(e),
                        "core_ai_failure": {
                            "stage": "skill_pipeline",
                            "reason": str(e)[:500],
                        },
                        "downstream_skipped": [
                            "scenario_designer", "automation_filter", "smoke_selector", "visual_grounder"
                        ],
                    },
                }
            else:
                payload = call_dashscope_cases(title, module, stage1_text_assets, [])
                payload.setdefault("review", {})["skill_pipeline_error"] = str(e)
    else:
        payload = call_dashscope_cases(title, module, stage1_text_assets, [])

    review = payload.setdefault("review", {})
    review["mindmap_only"] = True
    review["mindmap_mode"] = mindmap_mode
    review["mindmap_quality_mode"] = "visual_grounded"
    review["mindmap_visual_image_policy"] = (
        f"需求文档、Figma 文本和页面名称全量参与脑图结构生成；图片只用于视觉校准。"
        f"图片按每批 {MINDMAP_VISUAL_BATCH_SIZE} 张分批送入模型，最多纳入 {max_visual_images} 张，"
        f"本次纳入 {len(mindmap_visual_image_assets)} 张，跳过 {skipped_visual_image_count} 张。"
    )
    review["mindmap_visual_timeout_seconds"] = MINDMAP_VISUAL_TIMEOUT_SECONDS
    review["mindmap_visual_total_budget_seconds"] = MINDMAP_VISUAL_TOTAL_BUDGET_SECONDS
    review["mindmap_knowledge_policy"] = (
        "已按用户选择引用页面知识" if use_knowledge_context
        else "只生成脑图默认不引用已有页面知识，避免把历史无关页面混入当前需求"
    )
    agent_plan_review = {"agent_ai_planning_required": require_ai_planning}
    if has_prepared_figma:
        agent_plan_review["prepared_figma_context_reused"] = {
            "enabled": True,
            "used_count": len(used_figma_pages),
            "image_count": len(figma_images),
            "saved_design_count": len(saved_figma_designs),
            "source": prepared_figma_context.get("source") or "prepared_figma",
        }
    if use_yaml_baseline_context:
        agent_plan_review["yaml_reference_examples"] = [
            {
                "id": item.get("id") or item.get("case_id") or "",
                "title": item.get("title"),
                "module": item.get("module"),
                "file": item.get("file"),
                "businessPath": item.get("businessPath") or "",
                "sourceKind": item.get("sourceKind") or "",
                "verificationStatus": item.get("verificationStatus") or "",
                "provenancePath": item.get("provenancePath") or item.get("file") or "",
                "sourceTrust": item.get("sourceTrust") or 0,
                "aiSelectedRole": item.get("ai_selected_role") or "",
                "aiSelectedReason": item.get("ai_selected_reason") or "",
                "aiSelectedBranchId": item.get("ai_selected_branch_id") or "",
                "aiSelectedBranchName": item.get("ai_selected_branch_name") or "",
            }
            for item in yaml_reference_examples
        ]
        agent_plan_review["baseline_reranker"] = baseline_rerank_trace
    review.update(copy.deepcopy(agent_plan_review))
    core_ai_failure = review.get("core_ai_failure") if isinstance(review.get("core_ai_failure"), dict) else {}
    if require_ai_planning and core_ai_failure:
        review["visual_refine_skipped"] = (
            f"核心 AI 阶段 {core_ai_failure.get('stage') or 'unknown'} 未成功，"
            "本次尝试立即结束并交给 Agent 有界重试，不再调用视觉模型。"
        )
    elif visual_text_assets or mindmap_visual_image_assets:
        if job_id:
            update_generate_job(
                job_id,
                progress=65,
                step="视觉校准",
                message=visual_reference_message(
                    "正在校准脑图场景，实际送入模型",
                    figma_texts,
                    mindmap_figma_images,
                    ignored_figma_pages,
                    knowledge_texts,
                    [],
                    mindmap_uploaded_images
                )
            )
        visual_batches = [
            mindmap_visual_image_assets[i:i + MINDMAP_VISUAL_BATCH_SIZE]
            for i in range(0, len(mindmap_visual_image_assets), MINDMAP_VISUAL_BATCH_SIZE)
        ] or [[]]
        visual_start = time.time()
        visual_batches_done = 0
        visual_batches_attempted = 0
        visual_images_done = 0
        visual_errors = []
        visual_batch_results = []
        for batch_index, image_batch in enumerate(visual_batches, start=1):
            if job_id and generate_job_should_stop(job_id):
                break
            remaining_budget = int(MINDMAP_VISUAL_TOTAL_BUDGET_SECONDS - (time.time() - visual_start))
            if remaining_budget <= 0:
                visual_errors.append("视觉校准总耗时预算已用完")
                break
            timeout_seconds = max(30, min(int(MINDMAP_VISUAL_TIMEOUT_SECONDS), remaining_budget))
            batch_offset = (batch_index - 1) * MINDMAP_VISUAL_BATCH_SIZE
            batch_text_assets = []
            for image_offset in range(len(image_batch)):
                source_index = batch_offset + image_offset
                if source_index < selected_figma_count and source_index < len(figma_texts):
                    batch_text_assets.append(figma_texts[source_index])
            if knowledge_texts:
                batch_text_assets.extend(knowledge_texts[:2])
            if not batch_text_assets:
                batch_text_assets = [
                    "当前批次为用户上传截图；请结合 base_payload 的需求点，只校准与截图同页的真实文案、入口和可见终态。"
                ]
            image_names = [
                str((item or {}).get("name") or (item or {}).get("fileName") or f"image-{batch_offset + idx + 1}")
                if isinstance(item, dict) else f"image-{batch_offset + idx + 1}"
                for idx, item in enumerate(image_batch)
            ]
            if job_id:
                progress = min(74, 65 + int((batch_index - 1) / max(1, len(visual_batches)) * 9))
                update_generate_job(
                    job_id,
                    progress=progress,
                    step="视觉校准",
                    message=f"正在分批校准 Figma/截图，第 {batch_index}/{len(visual_batches)} 批，图片 {len(image_batch)} 张"
                )
            visual_batches_attempted += 1
            batch_started = time.time()
            try:
                payload = call_dashscope_refine_cases(
                    title,
                    module,
                    payload,
                    batch_text_assets,
                    image_batch,
                    timeout_seconds=timeout_seconds,
                    legacy_fallback=False,
                    bounded_retry=True,
                )
                visual_batches_done += 1
                visual_images_done += len(image_batch)
                payload_review = payload.setdefault("review", {})
                batch_attempt_meta = payload_review.get("visual_grounder_attempts")
                batch_attempt_meta = batch_attempt_meta if isinstance(batch_attempt_meta, dict) else {}
                payload_review["mindmap_visual_grounded"] = True
                visual_batch_results.append({
                    "batch": batch_index,
                    "status": "completed",
                    "imageCount": len(image_batch),
                    "imageNames": image_names,
                    "durationSeconds": max(0, int(time.time() - batch_started)),
                    "attemptCount": safe_int(batch_attempt_meta.get("count"), 1),
                    "retryUsed": bool(batch_attempt_meta.get("retryUsed")),
                    "judgement": str(payload_review.get("visual_grounding_check") or "").strip()[:500],
                })
            except Exception as e:
                error_text = str(e)
                retry_used = "同一批次预算内两次失败" in error_text
                visual_errors.append(f"第 {batch_index} 批：{error_text}")
                visual_batch_results.append({
                    "batch": batch_index,
                    "status": "failed",
                    "imageCount": len(image_batch),
                    "imageNames": image_names,
                    "durationSeconds": max(0, int(time.time() - batch_started)),
                    "attemptCount": 2 if retry_used else 1,
                    "retryUsed": retry_used,
                    "error": error_text[:500],
                })
                if job_id:
                    update_generate_job(
                        job_id,
                        progress=67,
                        step="视觉校准",
                        message=f"第 {batch_index} 批视觉校准超时/失败，已记录并继续下一批：{error_text[:100]}",
                    )
        recorded_batches = {safe_int(item.get("batch"), 0) for item in visual_batch_results}
        for batch_index, image_batch in enumerate(visual_batches, start=1):
            if batch_index in recorded_batches:
                continue
            batch_offset = (batch_index - 1) * MINDMAP_VISUAL_BATCH_SIZE
            visual_batch_results.append({
                "batch": batch_index,
                "status": "not_attempted",
                "imageCount": len(image_batch),
                "imageNames": [
                    str((item or {}).get("name") or (item or {}).get("fileName") or f"image-{batch_offset + idx + 1}")
                    if isinstance(item, dict) else f"image-{batch_offset + idx + 1}"
                    for idx, item in enumerate(image_batch)
                ],
                "reason": "任务已取消或视觉总耗时预算已用完",
            })
        review = payload.setdefault("review", {})
        review["mindmap_visual_batches"] = f"{visual_batches_done}/{len(visual_batches)}"
        review["mindmap_visual_batches_attempted"] = visual_batches_attempted
        review["mindmap_visual_batch_results"] = sorted(visual_batch_results, key=lambda item: safe_int(item.get("batch"), 0))
        review["mindmap_visual_images_grounded"] = visual_images_done
        if visual_errors:
            review["visual_refine_error"] = "；".join(visual_errors)[-1000:]
            review["visual_refine_fallback"] = (
                "视觉校准部分批次超时或失败，已保留需求、PDF 文本和 Figma 页面文本继续生成脑图；"
                "未完成的图片批次不会阻塞脑图产出。"
            )

    # visual_grounder may return a fresh review object; restore orchestration
    # provenance so Agent PLAN can prove which prepared sources and baselines it used.
    review = payload.setdefault("review", {})
    review.update(copy.deepcopy(agent_plan_review))

    if require_ai_planning and core_ai_failure:
        coverage_audit = {
            "ok": False,
            "skipped": True,
            "reason": "core_ai_failure",
            "stage": core_ai_failure.get("stage") or "unknown",
        }
        return {
            "ok": False,
            "case_set_id": case_set_id,
            "asset": meta,
            "module": module,
            "file": "",
            "cases": payload,
            "caseCount": 0,
            "manualCaseCount": 0,
            "scenarioCount": len(payload.get("scenarios") or []),
            "summary": {
                "title": title,
                "module": module,
                "review": copy.deepcopy(review),
            },
            "summaryFiles": [],
            "coverageAudit": coverage_audit,
        }

    if job_id:
        update_generate_job(job_id, progress=78, step="本地覆盖检查", message="正在做本地覆盖检查并写入脑图")
    try:
        payload, coverage_audit = audit_case_coverage(payload)
        payload.setdefault("review", {})["coverage_auditor_skipped"] = "只生成脑图流程跳过 coverage_auditor 重模型审查，避免长时间卡在思考；需要补齐用例时可在生成分析里重新生成"
    except Exception as e:
        coverage_audit = {"ok": False, "error": str(e)}
        payload.setdefault("review", {})["coverage_repair_error"] = str(e)

    converted_payload = split_automation_ready_cases(payload)
    converted_payload["id"] = case_set_id
    converted_payload["module"] = module
    write_json_file(cases_path(case_set_id), converted_payload)
    summary = build_generation_summary(
        case_set_id,
        title,
        module,
        "",
        converted_payload,
        used_knowledge_pages=(used_figma_pages + used_knowledge_pages),
        yaml_check={"ok": True, "mode": "mindmap_only", "message": "只生成脑图任务未生成 YAML"}
    )
    review = summary.setdefault("review", {})
    review["mindmap_only"] = True
    review["mindmap_mode"] = mindmap_mode
    summary["mindmap_mode"] = mindmap_mode
    review["coverage_audit"] = coverage_audit
    if ignored_figma_pages:
        summary["ignored_figma_pages"] = ignored_figma_pages
    ui_design_meta = filtered_case_ui_design_assets_for_summary(case_set_id, summary)
    if ui_design_meta.get("designs"):
        summary["ui_design_assets"] = ui_design_meta.get("designs") or []
    if ui_design_meta.get("hidden_designs"):
        summary["hidden_ui_design_assets"] = ui_design_meta.get("hidden_designs") or []
    if ui_design_meta.get("excluded_figma_nodes"):
        summary["excluded_figma_nodes"] = ui_design_meta.get("excluded_figma_nodes") or []
    summary_files = write_generation_summary(case_set_id, summary)
    return {
        "ok": True,
        "case_set_id": case_set_id,
        "asset": meta,
        "module": module,
        "file": "",
        "cases": converted_payload,
        "caseCount": len(converted_payload.get("cases", [])),
        "manualCaseCount": len(converted_payload.get("manual_cases", [])),
        "scenarioCount": len(converted_payload.get("scenarios", [])),
        "summary": summary,
        "summaryFiles": summary_files,
        "coverageAudit": coverage_audit
    }



def build_generation_mindmap(summary):
    summary = summary if isinstance(summary, dict) else {}
    mode = str(summary.get("mindmap_mode") or summary.get("mindmapMode") or "").strip().lower()
    review = summary.get("review") if isinstance(summary.get("review"), dict) else {}
    mode = mode or str(review.get("mindmap_mode") or review.get("mindmapMode") or "full").strip().lower()
    if mode in {"compact", "mindmap", "compact_mindmap"}:
        return build_generation_mindmap_compact(summary)
    return build_generation_mindmap_full(summary)


def _compact_generation_flow(summary, analysis, scenarios):
    values = normalize_text_list(
        analysis.get("business_flow")
        or analysis.get("businessFlow")
        or analysis.get("business_paths")
        or analysis.get("businessPaths")
        or summary.get("business_flow")
        or summary.get("businessFlow")
    )
    if not values:
        values = normalize_text_list(analysis.get("requirement_points") or analysis.get("requirementPoints"))
    if not values:
        values = [
            first_non_empty(item.get("scenario"), item.get("name"), item.get("title"))
            for item in scenarios
            if isinstance(item, dict)
        ]
    return normalize_text_list(values)[:12]


def _compact_case_matches(label, case):
    text = " ".join(normalize_text_list([
        (case or {}).get("title"),
        (case or {}).get("scenario"),
        (case or {}).get("coverage"),
        (case or {}).get("business_path"),
        (case or {}).get("goal"),
        (case or {}).get("expected_result"),
    ]))
    label_key = scenario_key(label)
    text_key = scenario_key(text)
    return bool(label_key and text_key and (label_key in text_key or text_key in label_key))


def build_generation_mindmap_compact(summary):
    summary = summary if isinstance(summary, dict) else {}
    title = summary.get("title") or "自动化测试"
    analysis = summary.get("analysis") or summary.get("requirement_analysis") or {}
    if not isinstance(analysis, dict):
        analysis = {}
    scenarios = [item for item in (summary.get("scenarios") or []) if isinstance(item, dict)]
    cases = [item for item in (summary.get("cases") or []) if isinstance(item, dict)]
    manual_cases = [item for item in (summary.get("manual_cases") or []) if isinstance(item, dict)]
    counts = summary.get("counts") if isinstance(summary.get("counts"), dict) else {}
    flow_nodes = _compact_generation_flow(summary, analysis, scenarios)
    root_children = []

    if flow_nodes:
        flow_children = []
        for idx, flow in enumerate(flow_nodes, start=1):
            matched = [case for case in cases if _compact_case_matches(flow, case)][:5]
            children = [mm_node(case_mm_title(case), indent=3) for case in matched]
            if not children:
                related_scenarios = [
                    first_non_empty(item.get("scenario"), item.get("name"), item.get("title"))
                    for item in scenarios
                    if _compact_case_matches(flow, item)
                ][:5]
                children = [mm_node(item, indent=3) for item in related_scenarios]
            if not children:
                children = [mm_node("待在生成分析或人工用例中确认覆盖", indent=3)]
            flow_children.append(mm_node(f"{idx}. {flow}", children, indent=2))
        root_children.append(mm_node("业务主线与覆盖", flow_children, indent=1))

    priority_groups = {}
    for case in cases:
        priority = str(case.get("priority") or "未标级").upper()
        priority_groups.setdefault(priority, []).append(case)
    auto_children = []
    for priority in ["P0", "P1", "P2", "P3", "未标级"]:
        group = priority_groups.get(priority) or []
        if not group:
            continue
        auto_children.append(mm_node(
            f"{priority} 自动化用例（{len(group)} 条）",
            [mm_node(case_mm_title(case), indent=3) for case in group[:10]],
            indent=2,
        ))
    if auto_children:
        root_children.append(mm_node("可执行 YAML 用例", auto_children, indent=1))

    if manual_cases:
        root_children.append(mm_node(
            "人工验证 / 环境准备",
            [
                mm_node(
                    first_non_empty(case.get("title"), case.get("name"), case.get("reason"), "人工验证项"),
                    [mm_node(f"原因：{first_non_empty(case.get('reason'), case.get('data_requirements'), '需要人工准备或确认')}", indent=3)],
                    indent=2,
                )
                for case in manual_cases[:12]
            ],
            indent=1,
        ))

    risks = normalize_text_list(analysis.get("risks") or analysis.get("questions") or analysis.get("open_questions"))
    if risks:
        root_children.append(mm_node("风险与待确认", [mm_node(item, indent=2) for item in risks[:10]], indent=1))

    counts_children = [
        mm_node(f"场景：{len(scenarios) or safe_int(counts.get('scenario_count'), 0)}", indent=2),
        mm_node(f"自动化：{len(cases) or safe_int(counts.get('automation_case_count'), 0)}", indent=2),
        mm_node(f"人工：{len(manual_cases) or safe_int(counts.get('manual_case_count'), 0)}", indent=2),
    ]
    root_children.append(mm_node("生成产物", counts_children, indent=1))

    root = mm_node(f"{title}-测试用例", root_children, indent=0)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<map version="1.0.1">\n' + root + "\n</map>\n"


def build_generation_mindmap_full(summary):
    title = summary.get("title") or "自动化测试"
    root_children = []
    scenarios = [item for item in (summary.get("scenarios") or []) if isinstance(item, dict)]
    cases = [item for item in (summary.get("cases") or []) if isinstance(item, dict)]
    manual_cases = [item for item in (summary.get("manual_cases") or []) if isinstance(item, dict)]
    report_checkpoints = normalize_text_list(summary.get("report_checkpoints"))[:5]

    def limited_nodes(values, limit=8, indent=2):
        rows = []
        for value in normalize_text_list(values)[:limit]:
            rows.append(mm_node(value, indent=indent))
        return rows

    def add_section(name, values, limit=8):
        nodes = limited_nodes(values, limit=limit, indent=2)
        if nodes:
            root_children.append(mm_node(name, nodes, indent=1))

    if report_checkpoints:
        root_children.append(mm_node(
            "测试报告检查点",
            [mm_node(f"{idx}. {item}", indent=2) for idx, item in enumerate(report_checkpoints, start=1)],
            indent=1
        ))

    analysis = summary.get("analysis") or summary.get("requirement_analysis") or {}
    add_section("需求目标", (
        analysis.get("business_goals")
        or analysis.get("goals")
        or summary.get("business_goals")
        or summary.get("goals")
    ), limit=8)
    add_section("需求点", (
        analysis.get("requirement_points")
        or analysis.get("requirements")
        or summary.get("requirement_points")
    ), limit=12)
    add_section("风险与待确认", (
        analysis.get("risks")
        or analysis.get("questions")
        or analysis.get("open_questions")
        or summary.get("risks")
    ), limit=10)
    requirement_summary = build_requirement_analysis_summary(analysis)
    coverage_matrix = requirement_summary.get("coverage_matrix") or []
    if coverage_matrix:
        matrix_children = []
        for item in coverage_matrix:
            point = item.get("requirement_point") or item.get("feature") or "未命名需求点"
            point_children = []
            normal = limited_nodes(item.get("normal_scenarios"), limit=8, indent=3)
            negative = limited_nodes(item.get("negative_scenarios"), limit=8, indent=3)
            boundary = limited_nodes(item.get("boundary_scenarios"), limit=8, indent=3)
            auto = limited_nodes(item.get("auto_cases"), limit=12, indent=3)
            manual = limited_nodes(item.get("manual_cases"), limit=12, indent=3)
            if normal:
                point_children.append(mm_node("正常/主流程场景", normal, indent=2))
            if negative:
                point_children.append(mm_node("异常/错误提示场景", negative, indent=2))
            if boundary:
                point_children.append(mm_node("边界/状态组合场景", boundary, indent=2))
            if auto:
                point_children.append(mm_node("进入 YAML 的自动化用例", auto, indent=2))
            if manual:
                point_children.append(mm_node("人工验证 / 待准备", manual, indent=2))
            if item.get("uncovered_reason"):
                point_children.append(mm_node(f"未覆盖原因：{item.get('uncovered_reason')}", indent=2))
            matrix_children.append(mm_node(point, point_children, indent=1))
        root_children.append(mm_node("完整需求覆盖追踪矩阵", matrix_children, indent=1))

    scenario_feature_map = {}
    for scenario in scenarios:
        name = first_non_empty(scenario.get("scenario"), scenario.get("name"), scenario.get("title"))
        feature = first_non_empty(scenario.get("feature"), scenario.get("module"), summary.get("module"), "未分组功能")
        if name:
            scenario_feature_map[scenario_key(name)] = feature

    feature_names = []
    for scenario in scenarios:
        feature = first_non_empty(scenario.get("feature"), scenario.get("module"), summary.get("module"), "未分组功能")
        if feature not in feature_names:
            feature_names.append(feature)
    for case in cases:
        feature = first_non_empty(
            case.get("feature"),
            scenario_feature_map.get(scenario_key(case.get("scenario"))),
            summary.get("module"),
            "未分组功能"
        )
        if feature not in feature_names:
            feature_names.append(feature)

    for feature in feature_names or [summary.get("module") or "未分组功能"]:
        feature_children = []
        feature_scenarios = []
        for scenario in scenarios:
            scenario_feature = first_non_empty(scenario.get("feature"), scenario.get("module"), summary.get("module"), "未分组功能")
            if scenario_feature == feature:
                feature_scenarios.append(scenario)

        matched_case_ids = set()
        for scenario in feature_scenarios:
            scenario_name = first_non_empty(scenario.get("scenario"), scenario.get("name"), scenario.get("title"), "未命名场景")
            method = scenario_method_text(scenario)
            scenario_title = f"{scenario_name}（{method}）" if method else scenario_name
            scenario_children = []
            for key, label in (("expected", "预期"), ("reason", "适合性说明")):
                value = scenario.get(key) or scenario.get(key.replace("_", ""))
                if value:
                    scenario_children.append(mm_node(f"{label}：{value}", indent=3))
            scenario_cases = [case for case in cases if scenario_key(case.get("scenario")) == scenario_key(scenario_name)]
            for case in scenario_cases:
                matched_case_ids.add(id(case))
            if not scenario_cases:
                scenario_cases = [scenario_as_mindmap_case(scenario)]
            for case in scenario_cases[:6]:
                case_children = case_mindmap_detail_nodes(case, indent=4)
                if case.get("risk"):
                    case_children.append(mm_node(f"风险：{case.get('risk')}", indent=4))
                scenario_children.append(mm_node(case_mm_title(case), case_children, indent=3))
            if len(scenario_cases) > 6:
                scenario_children.append(mm_node(f"其余 {len(scenario_cases) - 6} 条用例见 YAML/生成分析", indent=3))
            feature_children.append(mm_node(scenario_title, scenario_children, indent=2))

        orphan_cases = []
        for case in cases:
            case_feature = first_non_empty(
                case.get("feature"),
                scenario_feature_map.get(scenario_key(case.get("scenario"))),
                summary.get("module"),
                "未分组功能"
            )
            if case_feature == feature and id(case) not in matched_case_ids:
                orphan_cases.append(case)
        if orphan_cases:
            orphan_children = [
                mm_node(case_mm_title(case), case_mindmap_detail_nodes(case, indent=4), indent=3)
                for case in orphan_cases[:10]
            ]
            if len(orphan_cases) > 10:
                orphan_children.append(mm_node(f"其余 {len(orphan_cases) - 10} 条用例见 YAML/生成分析", indent=3))
            feature_children.append(mm_node("未匹配场景的自动化用例（等价类）", orphan_children, indent=2))

        if feature_children:
            root_children.append(mm_node(f"覆盖场景：{feature}", feature_children, indent=1))

    priority_groups = {}
    for case in cases:
        priority = str(case.get("priority") or "未标级").upper()
        priority_groups.setdefault(priority, []).append(case)
    priority_order = ["P0", "P1", "P2", "P3", "未标级"]
    case_group_children = []
    for priority in priority_order + [key for key in priority_groups if key not in priority_order]:
        group_cases = priority_groups.get(priority) or []
        if not group_cases:
            continue
        rows = []
        for case in group_cases[:12]:
            suffix = " · 冒烟" if case.get("smoke") else ""
            rows.append(mm_node(f"{case.get('case_id') or ''} {case.get('title') or '未命名用例'}{suffix}".strip(), indent=3))
        if len(group_cases) > 12:
            rows.append(mm_node(f"其余 {len(group_cases) - 12} 条见 YAML/生成分析", indent=3))
        case_group_children.append(mm_node(f"{priority}（{len(group_cases)} 条）", rows, indent=2))
    if case_group_children:
        root_children.append(mm_node("自动化用例分级", case_group_children, indent=1))

    if manual_cases:
        manual_children = []
        for case in manual_cases:
            title_text = case.get("title") or case.get("name") or "人工用例"
            details = [
                mm_node(f"原因：{case.get('reason') or '需要人工确认或准备数据'}", indent=3),
                mm_node(f"准备建议：{case.get('suggested_setup') or case.get('setup') or '按实际环境准备'}", indent=3),
            ]
            extra = case_mindmap_detail_nodes(case, indent=3)
            if extra:
                details.extend(extra)
            manual_children.append(mm_node(title_text, details, indent=2))
        root_children.append(mm_node("人工用例 / 待准备", manual_children, indent=1))

    review = summary.get("review") or {}
    review_text = first_non_empty(review.get("coverage_check"), review.get("automation_check"), review.get("assertion_check"))
    if review_text:
        root_children.append(mm_node("自评审", [mm_node(review_text, indent=2)], indent=1))

    root = mm_node(f"{title}-测试用例", root_children, indent=0)
    return '<?xml version="1.0" encoding="UTF-8"?>\n<map version="1.0.1">\n' + root + "\n</map>\n"



def generation_mindmap_record(case_set_id):
    summary = read_json_file(generation_summary_path(case_set_id), default=None)
    if not isinstance(summary, dict):
        return None
    counts = summary.get("counts") or {}
    mm_path = generation_mindmap_path(case_set_id)
    exists = os.path.exists(mm_path)
    deleted = generation_mindmap_is_deleted(case_set_id)
    record_deleted = generation_mindmap_record_is_deleted(case_set_id)
    size = 0
    updated_at = ""
    updated_ts = 0.0
    try:
        if exists:
            stat = os.stat(mm_path)
            size = stat.st_size
            updated_ts = stat.st_mtime
            updated_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
    except Exception:
        pass
    generated_at = summary.get("generated_at") or ""
    generated_ts = _mindmap_time_value(generated_at)
    sort_ts = max(updated_ts, generated_ts)
    return {
        "case_set_id": case_set_id,
        "title": summary.get("title") or case_set_id,
        "module": summary.get("module") or "",
        "yaml_file": summary.get("yaml_file") or "",
        "generated_at": generated_at,
        "scenario_count": safe_int(counts.get("scenario_count"), 0),
        "automation_case_count": safe_int(counts.get("automation_case_count"), 0),
        "manual_case_count": safe_int(counts.get("manual_case_count"), 0),
        "smoke_count": safe_int(counts.get("smoke_count"), 0),
        "priority_counts": counts.get("priority_counts") or {},
        "mindmap_exists": exists,
        "mindmap_deleted": deleted,
        "mindmap_record_deleted": record_deleted,
        "mindmap_downloadable": exists and not deleted,
        "mindmap_size": size,
        "mindmap_updated_at": updated_at,
        "mindmap_updated_ts": updated_ts,
        "generated_ts": generated_ts,
        "mindmap_sort_ts": sort_ts,
    }



def build_requirement_analysis_summary(analysis):
    analysis = analysis if isinstance(analysis, dict) else {}
    try:
        analysis = normalize_requirement_analysis_result(dict(analysis))
    except Exception:
        pass
    matrix = analysis.get("coverage_matrix") or analysis.get("coverageMatrix") or []
    if not isinstance(matrix, list):
        matrix = []
    normalized_matrix = []
    for item in matrix:
        if not isinstance(item, dict):
            continue
        normalized_matrix.append({
            "feature": first_non_empty(item.get("feature"), item.get("module"), item.get("name")),
            "requirement_point": first_non_empty(item.get("requirement_point"), item.get("requirementPoint"), item.get("point")),
            "normal_scenarios": normalize_text_list(item.get("normal_scenarios") or item.get("normalScenarios")),
            "negative_scenarios": normalize_text_list(item.get("negative_scenarios") or item.get("negativeScenarios")),
            "boundary_scenarios": normalize_text_list(item.get("boundary_scenarios") or item.get("boundaryScenarios")),
            "auto_cases": normalize_text_list(item.get("auto_cases") or item.get("autoCases")),
            "manual_cases": normalize_text_list(item.get("manual_cases") or item.get("manualCases")),
            "uncovered_reason": first_non_empty(item.get("uncovered_reason"), item.get("uncoveredReason"), item.get("reason")),
        })
    return {
        "business_goals": analysis_list(analysis, "business_goals", "businessGoals", "goals"),
        "roles": analysis_list(analysis, "roles", "users", "user_roles", "userRoles"),
        "entry_points": analysis_list(analysis, "entry_points", "entryPoints", "entries"),
        "state_assumptions": analysis_list(analysis, "state_assumptions", "stateAssumptions", "preconditions"),
        "data_assumptions": analysis_list(analysis, "data_assumptions", "dataAssumptions", "data"),
        "visible_outcomes": analysis_list(analysis, "visible_outcomes", "visibleOutcomes", "ui_outcomes", "uiOutcomes"),
        "risks": analysis_list(analysis, "risks", "risk_points", "riskPoints"),
        "requirement_points": analysis_list(analysis, "requirement_points", "requirementPoints", "test_points", "testPoints"),
        "questions": analysis_list(analysis, "questions", "open_questions", "openQuestions"),
        "missing_inputs": analysis_list(analysis, "missing_inputs", "missingInputs", "gaps"),
        "blockers": analysis_list(analysis, "blockers", "blocking_points", "blockingPoints"),
        "assumptions": analysis_list(analysis, "assumptions", "inferred_assumptions", "inferredAssumptions"),
        "confidence": str(analysis.get("confidence") or "medium").strip().lower(),
        "readiness_score": safe_int(analysis.get("readiness_score") or analysis.get("readinessScore"), 0),
        "readiness_level": str(analysis.get("readiness_level") or analysis.get("readinessLevel") or "").strip().lower(),
        "source_quality": analysis.get("source_quality") or analysis.get("sourceQuality") or {},
        "coverage_matrix": normalized_matrix,
    }



def markdown_cell(value):
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text.replace("|", "｜") or "-"



def visual_reference_message(prefix, figma_texts, figma_images, ignored_figma_pages, knowledge_texts, knowledge_images, uploaded_image_assets):
    used_image_parts = []
    used_text_parts = []
    skipped_parts = []
    figma_image_count = len(figma_images or [])
    figma_page_count = len(figma_texts or [])
    knowledge_image_count = len(knowledge_images or [])
    knowledge_page_count = len(knowledge_texts or [])
    uploaded_image_count = len(uploaded_image_assets or [])

    if figma_image_count:
        used_image_parts.append(f"Figma {figma_image_count} 张")
    if knowledge_image_count:
        used_image_parts.append(f"页面知识 {knowledge_image_count} 张")
    if uploaded_image_count:
        used_image_parts.append(f"人工上传 {uploaded_image_count} 张")
    if figma_page_count and figma_page_count != figma_image_count:
        used_text_parts.append(f"Figma 文本 {figma_page_count} 页")
    if knowledge_page_count and knowledge_page_count != knowledge_image_count:
        used_text_parts.append(f"页面知识文本 {knowledge_page_count} 页")
    if ignored_figma_pages:
        skipped_parts.append(f"未使用低匹配 Figma {len(ignored_figma_pages)} 页")

    parts = []
    if used_image_parts:
        parts.append("本次用图：" + " + ".join(used_image_parts))
    elif used_text_parts:
        parts.append("本次无图片，仅用文本参考")
    else:
        parts.append("本次无视觉图片，仅按需求文本生成")
    if used_text_parts:
        parts.append("文本参考：" + " + ".join(used_text_parts))
    if skipped_parts:
        parts.append("未使用：" + " + ".join(skipped_parts))
    return prefix + "，" + "；".join(parts)


def _chunked_list(items, size):
    size = max(1, safe_int(size, 1))
    items = list(items or [])
    for idx in range(0, len(items), size):
        yield items[idx:idx + size]


def yaml_visual_total_budget_for_batches(total_batches, per_batch_timeout=None):
    """Use actual batch count to avoid cutting off long Figma visual grounding."""
    total_batches = max(1, safe_int(total_batches, 1))
    per_batch_timeout = max(60, safe_int(per_batch_timeout or YAML_VISUAL_TIMEOUT_SECONDS, YAML_VISUAL_TIMEOUT_SECONDS))
    return max(int(YAML_VISUAL_TOTAL_BUDGET_SECONDS), total_batches * per_batch_timeout)


def refine_cases_with_yaml_visual_batches(title, module, payload, visual_text_assets, visual_image_assets, job_id=None):
    """Run visual grounding in bounded batches for Figma-heavy YAML generation."""
    image_assets = list(visual_image_assets or [])
    text_assets = list(visual_text_assets or [])
    if not text_assets and not image_assets:
        return payload

    review = payload.setdefault("review", {}) if isinstance(payload, dict) else {}
    started = time.time()
    visual_errors = []
    completed_batches = 0

    if not image_assets:
        if job_id:
            update_generate_job(
                job_id,
                progress=65,
                step="视觉校准",
                message=f"正在用 Figma/页面文本校准入口、步骤和断言，最多等待 {YAML_VISUAL_TIMEOUT_SECONDS} 秒",
            )
        try:
            refined = call_dashscope_refine_cases(
                title,
                module,
                payload,
                text_assets,
                [],
                timeout_seconds=YAML_VISUAL_TIMEOUT_SECONDS,
                legacy_fallback=False,
            )
            refined.setdefault("review", {})["yaml_visual_grounded"] = True
            refined.setdefault("review", {})["yaml_visual_batches"] = {
                "enabled": True,
                "text_only": True,
                "completed_batches": 1,
                "timeout_seconds": YAML_VISUAL_TIMEOUT_SECONDS,
            }
            return refined
        except Exception as exc:
            review["visual_refine_error"] = str(exc)
            review["visual_refine_skipped"] = "视觉校准超时或失败，已保留需求解析主结果继续生成 YAML"
            return payload

    batches = list(_chunked_list(image_assets, YAML_VISUAL_BATCH_SIZE))
    total_batches = len(batches)
    total_budget_seconds = yaml_visual_total_budget_for_batches(total_batches, YAML_VISUAL_TIMEOUT_SECONDS)
    if job_id:
        update_generate_job(
            job_id,
            timeout_seconds=max(AGENT_GENERATE_YAML_TIMEOUT_SECONDS, total_budget_seconds),
            visual_total_budget_seconds=total_budget_seconds,
        )
    refined_payload = payload
    for index, batch in enumerate(batches, start=1):
        elapsed = int(time.time() - started)
        remaining_budget = max(0, total_budget_seconds - elapsed)
        if remaining_budget <= 0:
            visual_errors.append("视觉校准总耗时预算已用完")
            break
        if job_id and generate_job_should_stop(job_id):
            visual_errors.append("生成任务已取消或被标记为停止")
            break
        timeout_seconds = max(60, min(int(YAML_VISUAL_TIMEOUT_SECONDS), remaining_budget))
        progress = min(71, 64 + int((index / max(1, total_batches)) * 7))
        if job_id:
            update_generate_job(
                job_id,
                progress=progress,
                step="视觉校准",
                message=(
                    f"正在分批校准 Figma/UI 图，第 {index}/{total_batches} 批，"
                    f"本批 {len(batch)} 张，已用 {elapsed}s / 动态预算 {total_budget_seconds}s，"
                    f"本批最多等待 {timeout_seconds}s"
                ),
            )
        try:
            refined_payload = call_dashscope_refine_cases(
                title,
                module,
                refined_payload,
                text_assets,
                batch,
                timeout_seconds=timeout_seconds,
                legacy_fallback=False,
            )
            completed_batches += 1
            review = refined_payload.setdefault("review", {})
            review["yaml_visual_grounded"] = True
            review["yaml_visual_completed_batches"] = completed_batches
            if job_id:
                update_generate_job(
                    job_id,
                    progress=progress,
                    step="视觉校准",
                    message=f"第 {index}/{total_batches} 批视觉校准完成，已校准 {completed_batches} 批",
                )
        except Exception as exc:
            visual_errors.append(f"第 {index} 批视觉校准失败：{str(exc)[:180]}")
            if job_id:
                update_generate_job(
                    job_id,
                    progress=progress,
                    step="视觉校准",
                    message=f"第 {index}/{total_batches} 批视觉校准失败，已记录并继续后续生成：{str(exc)[:100]}",
                )

    review = refined_payload.setdefault("review", {})
    review["yaml_visual_batches"] = {
        "enabled": True,
        "image_count": len(image_assets),
        "batch_size": YAML_VISUAL_BATCH_SIZE,
        "total_batches": total_batches,
        "completed_batches": completed_batches,
        "timeout_seconds_per_batch": YAML_VISUAL_TIMEOUT_SECONDS,
        "total_budget_seconds": total_budget_seconds,
        "configured_min_total_budget_seconds": YAML_VISUAL_TOTAL_BUDGET_SECONDS,
        "errors": visual_errors[:8],
    }
    if visual_errors:
        review["visual_refine_errors"] = visual_errors[:8]
        review["remaining_risks"] = normalize_text_list(review.get("remaining_risks") or []) + [
            "部分视觉校准批次超时或失败，已保留需求解析和已成功校准的 UI 信息继续生成 YAML"
        ]
    return refined_payload



def clear_auto_figma_ui_design_assets(case_set_id):
    meta = load_case_ui_design_meta(case_set_id)
    kept = []
    removed = 0
    for item in meta.get("designs") or []:
        if not isinstance(item, dict):
            continue
        if item.get("source") == "figma":
            filename = item.get("filename") or ""
            if filename:
                try:
                    os.remove(safe_join(case_ui_design_dir(case_set_id), filename))
                except FileNotFoundError:
                    pass
                except Exception:
                    pass
            removed += 1
            continue
        kept.append(item)
    if removed:
        meta["designs"] = kept
        save_case_ui_design_meta(case_set_id, meta)
    return removed



def figma_url_from_design_asset(item):
    figma = (item or {}).get("figma") or {}
    raw = (figma.get("url") or figma.get("figma_url") or figma.get("figmaUrl") or "").strip()
    if raw:
        return raw
    file_key = figma.get("file_key") or figma.get("fileKey") or ""
    node_id = figma.get("node_id") or figma.get("nodeId") or ""
    if file_key:
        url = f"https://www.figma.com/design/{file_key}"
        if node_id:
            url += f"?node-id={str(node_id).replace(':', '-')}"
        return url
    return ""



def normalize_design_asset_id(value):
    text = clean_id(value or "")
    return text[:80] or unique_millis_id("ui")



def summary_requirement_query(summary):
    analysis = summary.get("requirement_analysis") or summary.get("analysis") or {}
    parts = [
        summary.get("title") or "",
        summary.get("module") or "",
        " ".join(normalize_text_list(analysis.get("business_goals"))),
        " ".join(normalize_text_list(analysis.get("requirement_points"))),
        " ".join(normalize_text_list(analysis.get("visible_outcomes"))),
        " ".join(normalize_text_list(analysis.get("risks"))),
    ]
    for item in summary.get("cases") or []:
        if not isinstance(item, dict):
            continue
        parts.extend([
            item.get("title") or "",
            item.get("coverage") or "",
            item.get("expected_result") or "",
            item.get("scenario") or "",
        ])
    return "\n".join(str(item) for item in parts if item)



def filtered_case_ui_design_assets_for_summary(case_set_id, summary):
    meta = list_case_ui_design_assets(case_set_id)
    designs = meta.get("designs") or []
    query = summary_requirement_query(summary or {})
    min_score = max(0, env_int("FIGMA_AUTO_SAVE_MIN_RELEVANCE", 5))
    excluded_node_ids = {
        str(item.get("node_id") or item.get("nodeId") or "").strip()
        for item in (meta.get("excluded_figma_nodes") or [])
        if isinstance(item, dict)
    }
    excluded_node_ids |= {
        str(item or "").strip()
        for item in (meta.get("excluded_figma_node_ids") or [])
        if str(item or "").strip()
    }
    filtered = []
    hidden = []
    for item in designs:
        if not isinstance(item, dict):
            continue
        if item.get("source") != "figma":
            filtered.append(item)
            continue
        figma = item.get("figma") or {}
        node_id = str(figma.get("node_id") or figma.get("nodeId") or "").strip()
        if node_id and node_id in excluded_node_ids:
            hidden.append({
                "page_name": item.get("page_name") or item.get("name") or "",
                "node_id": node_id,
                "score": 0,
                "reason": "该 Figma 页面已被手动删除并加入排除列表"
            })
            continue
        if figma.get("pinned"):
            filtered.append(item)
            continue
        draft = {
            "page_name": item.get("page_name") or item.get("name") or "",
            "description": " ".join([
                item.get("description") or "",
                item.get("route") or "",
                " ".join(normalize_text_list(figma.get("relevance_terms"))),
                figma.get("relevance_reason") or "",
            ]),
            "key_elements": [],
            "common_assertions": [],
            "figma": dict(figma),
        }
        score, matched = score_figma_draft_for_requirement(draft, query)
        if score >= min_score:
            next_item = dict(item)
            next_figma = dict(figma)
            next_figma["rechecked_relevance_score"] = score
            next_figma["rechecked_relevance_terms"] = matched
            next_item["figma"] = next_figma
            filtered.append(next_item)
        else:
            hidden.append({
                "page_name": item.get("page_name") or item.get("name") or "",
                "score": score,
                "reason": "按当前需求重新校验后匹配度低，已从参考 UI 稿中隐藏"
            })
    meta["designs"] = filtered
    if hidden:
        meta["hidden_designs"] = hidden
    return meta



def load_case_ui_design_meta(case_set_id):
    meta = read_json_file(case_ui_design_meta_path(case_set_id), default=None)
    if isinstance(meta, dict):
        meta.setdefault("case_set_id", case_set_id)
        meta.setdefault("designs", [])
        return meta
    return {
        "case_set_id": case_set_id,
        "designs": [],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }



def save_case_ui_design_meta(case_set_id, meta):
    meta["case_set_id"] = case_set_id
    meta["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_json_file(case_ui_design_meta_path(case_set_id), meta)
    return meta



def list_case_ui_design_assets(case_set_id):
    meta = load_case_ui_design_meta(case_set_id)
    rows = []
    for item in meta.get("designs") or []:
        if not isinstance(item, dict):
            continue
        row = dict(item)
        filename = row.get("filename") or row.get("name") or ""
        row["exists"] = bool(filename and os.path.exists(safe_join(case_ui_design_dir(case_set_id), filename)))
        rows.append(row)
    meta["designs"] = rows
    return meta



def restore_excluded_figma_node(case_set_id, node_id=""):
    node_id = str(node_id or "").strip()
    if not node_id:
        raise ValueError("node_id 不能为空")
    meta = load_case_ui_design_meta(case_set_id)
    old_nodes = [item for item in (meta.get("excluded_figma_nodes") or []) if isinstance(item, dict)]
    new_nodes = [
        item for item in old_nodes
        if str(item.get("node_id") or item.get("nodeId") or "").strip() != node_id
    ]
    old_ids = {
        str(item or "").strip()
        for item in (meta.get("excluded_figma_node_ids") or [])
        if str(item or "").strip()
    }
    old_ids.discard(node_id)
    meta["excluded_figma_nodes"] = new_nodes
    meta["excluded_figma_node_ids"] = sorted(old_ids)
    save_case_ui_design_meta(case_set_id, meta)
    return len(old_nodes) != len(new_nodes), list_case_ui_design_assets(case_set_id)



def save_case_ui_design_files(case_set_id, files, source="manual", title="", module="", extra=None):
    if not isinstance(files, list) or not files:
        raise ValueError("files 不能为空")
    root = case_ui_design_dir(case_set_id)
    os.makedirs(root, exist_ok=True)
    meta = load_case_ui_design_meta(case_set_id)
    designs = [item for item in (meta.get("designs") or []) if isinstance(item, dict)]
    by_id = {item.get("asset_id"): item for item in designs if item.get("asset_id")}
    extra = extra or {}
    saved = []
    for index, item in enumerate(files, start=1):
        name = clean_asset_filename(item.get("name") or f"ui-design-{index}.png")
        if not is_image_file(name):
            raise ValueError(f"UI 设计稿只支持 png / jpg / jpeg：{name}")
        content_base64 = item.get("contentBase64")
        if not content_base64:
            raise ValueError(f"UI 设计稿缺少图片内容：{name}")
        data = base64.b64decode(content_base64)
        if len(data) > 5 * 1024 * 1024:
            raise ValueError(f"UI 设计稿过大，请压缩后上传：{name}")
        asset_id = normalize_design_asset_id(item.get("asset_id") or item.get("assetId") or item.get("node_id") or item.get("page_name") or name)
        ext = os.path.splitext(name)[1].lower() or ".png"
        filename = clean_asset_filename(f"{asset_id}{ext}")
        path_to_save = safe_join(root, filename)
        write_bytes_file(path_to_save, data)
        record = {
            "asset_id": asset_id,
            "name": name,
            "filename": filename,
            "mime": guess_mime(filename),
            "size": len(data),
            "source": source or "manual",
            "title": title or extra.get("title") or "",
            "module": module or extra.get("module") or "",
            "page_name": item.get("page_name") or item.get("pageName") or extra.get("page_name") or "",
            "route": item.get("route") or extra.get("route") or "",
            "description": item.get("description") or extra.get("description") or "",
            "figma": item.get("figma") or extra.get("figma") or {},
            "created_at": by_id.get(asset_id, {}).get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        by_id[asset_id] = record
        saved.append(record)
    meta["title"] = title or meta.get("title") or ""
    meta["module"] = module or meta.get("module") or ""
    meta["designs"] = sorted(by_id.values(), key=lambda row: row.get("updated_at") or row.get("created_at") or "", reverse=True)
    save_case_ui_design_meta(case_set_id, meta)
    return saved, list_case_ui_design_assets(case_set_id)



def delete_case_ui_design_asset(case_set_id, asset_id="", filename=""):
    meta = load_case_ui_design_meta(case_set_id)
    kept = []
    deleted = None
    target_id = str(asset_id or "").strip()
    target_filename = clean_asset_filename(filename or "")
    for item in meta.get("designs") or []:
        if not isinstance(item, dict):
            continue
        match = (target_id and item.get("asset_id") == target_id) or (target_filename and item.get("filename") == target_filename)
        if match and deleted is None:
            deleted = item
            continue
        kept.append(item)
    if not deleted:
        return False, list_case_ui_design_assets(case_set_id)
    if deleted.get("source") == "figma":
        figma = deleted.get("figma") or {}
        node_id = str(figma.get("node_id") or figma.get("nodeId") or "").strip()
        if node_id:
            excluded = [item for item in (meta.get("excluded_figma_nodes") or []) if isinstance(item, dict)]
            if not any(str(item.get("node_id") or "") == node_id for item in excluded):
                excluded.append({
                    "node_id": node_id,
                    "page_name": deleted.get("page_name") or deleted.get("name") or "",
                    "asset_id": deleted.get("asset_id") or "",
                    "excluded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "reason": "用户从当前批次 UI 设计稿中删除"
                })
            meta["excluded_figma_nodes"] = excluded
            ids = {
                str(item or "").strip()
                for item in (meta.get("excluded_figma_node_ids") or [])
                if str(item or "").strip()
            }
            ids.add(node_id)
            meta["excluded_figma_node_ids"] = sorted(ids)
    filename_to_remove = deleted.get("filename") or ""
    if filename_to_remove:
        try:
            os.remove(safe_join(case_ui_design_dir(case_set_id), filename_to_remove))
        except FileNotFoundError:
            pass
    meta["designs"] = kept
    save_case_ui_design_meta(case_set_id, meta)
    return True, list_case_ui_design_assets(case_set_id)



def is_text_asset(filename):
    return filename.lower().endswith((".txt", ".md", ".json", ".pdf", ".doc", ".docx", ".mm"))



def extract_asset_text(path, name):
    lower = name.lower()
    if lower.endswith((".txt", ".md", ".json")):
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    if lower.endswith(".docx"):
        return extract_docx_text(path)
    if lower.endswith(".pdf"):
        return extract_pdf_text(path)
    if lower.endswith(".doc"):
        return extract_doc_text(path)
    if lower.endswith(".mm"):
        return extract_mm_text(path)
    return ""



def supported_asset_file(filename):
    return filename.lower().endswith((
        ".txt", ".md", ".json", ".pdf", ".doc", ".docx", ".mm",
        ".png", ".jpg", ".jpeg"
    ))



def run_figma_parse_job(job_id, request_data):
    try:
        update_generate_job(job_id, status="running", progress=10, step="读取 Figma", message="正在连接 Figma API")
        update_generate_job(job_id, progress=35, step="解析节点", message="正在按页面级 Frame 解析设计稿")
        result = parse_figma_design(request_data)
        update_generate_job(
            job_id,
            status="success",
            progress=100,
            step="完成",
            message=f"已解析 {len(result.get('drafts') or [])} 个候选页面",
            result=result
        )
    except Exception as e:
        update_generate_job(
            job_id,
            status="failed",
            progress=90,
            step="失败",
            message=str(e),
            error=str(e)
        )




def find_figma_url_for_case_set(case_set_id, summary=None, meta=None):
    summary = summary or {}
    meta = meta or {}
    for source in (meta, summary):
        url = (source.get("figma_url") or source.get("figmaUrl") or "").strip()
        if url:
            return url
    for job in iter_raw_generate_jobs():
        request = job.get("request_data") or job.get("requestData") or {}
        if not isinstance(request, dict):
            continue
        job_case_set = (
            request.get("case_set_id")
            or request.get("caseSetId")
            or job.get("case_set_id")
            or (job.get("result") or {}).get("case_set_id")
        )
        if job_case_set != case_set_id:
            continue
        url = (request.get("figma_url") or request.get("figmaUrl") or "").strip()
        if url:
            return url
    ui_meta = load_case_ui_design_meta(case_set_id)
    for item in ui_meta.get("designs") or []:
        if not isinstance(item, dict) or item.get("source") != "figma":
            continue
        url = figma_url_from_design_asset(item)
        if url:
            return url
    return ""
