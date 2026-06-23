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
call_dashscope_cases = _lazy("call_dashscope_cases", "task_server.services.ai_skill_service")
call_dashscope_refine_cases = _lazy("call_dashscope_refine_cases", "task_server.services.ai_skill_service")
improve_case_coverage = _lazy("improve_case_coverage", "task_server.services.ai_skill_service")
normalize_requirement_analysis_result = _lazy("normalize_requirement_analysis_result", "task_server.services.ai_skill_service")
generation_volume_targets = _lazy("generation_volume_targets", "task_server.services.ai_skill_service")
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
MINDMAP_JOB_TIMEOUT_SECONDS = max(300, env_int("MIDSCENE_MINDMAP_JOB_TIMEOUT_SECONDS", min(JOB_TIMEOUT_SECONDS, GENERATE_JOB_TIMEOUT_SECONDS)))
FIGMA_PARSE_JOB_TIMEOUT_SECONDS = max(120, env_int("MIDSCENE_FIGMA_PARSE_JOB_TIMEOUT_SECONDS", min(900, GENERATE_JOB_TIMEOUT_SECONDS)))


def generate_job_timeout_seconds(job):
    job_type = str((job or {}).get("type") or "").strip().lower()
    if job_type == "mindmap_only":
        return MINDMAP_JOB_TIMEOUT_SECONDS
    if job_type in ("figma_parse", "figma"):
        return FIGMA_PARSE_JOB_TIMEOUT_SECONDS
    return GENERATE_JOB_TIMEOUT_SECONDS


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
    type_label = "脑图生成" if job_type == "mindmap_only" else ("Figma 解析" if job_type in ("figma_parse", "figma") else "AI 生成")
    message = (
        f"{type_label}超过 {timeout_seconds} 秒仍未完成，已自动标记为超时；"
        f"最后阶段：{stage}。这表示后台任务没有正常落到完成态；"
        "常见原因包括网络请求超时、服务重启后线程丢失，或外部服务长时间未返回。"
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
    methods = normalize_text_list((scenario or {}).get("method") or (scenario or {}).get("methods"))
    return " / ".join(methods[:2])


def case_mm_title(case):
    case = case or {}
    prefix = str(case.get("case_id") or "").strip()
    title = str(case.get("title") or case.get("case_name") or case.get("name") or "未命名用例").strip()
    priority = str(case.get("priority") or "").strip().upper()
    suffix = f" [{priority}]" if priority else ""
    return f"{prefix} {title}{suffix}".strip()


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
    """判断是否为冒烟用例。"""
    explicit = case_value(case, "smoke", "is_smoke", "isSmoke", "smoke_test", "smokeTest", "flag")
    tags = case_tags(case)
    flags = normalize_text_list(case.get("flag") or case.get("flags") or [])
    if explicit not in ("", None):
        return truthy_text(explicit) or "冒烟" in str(explicit) or "smoke" in str(explicit).lower()
    if any("冒烟" in tag or "smoke" in tag.lower() for tag in tags):
        return True
    if any("冒烟" in flag or "smoke" in flag.lower() for flag in flags):
        return True
    priority = case_priority(case)
    clue = " ".join([
        str(case.get("title") or ""),
        str(case.get("scenario") or ""),
        str(case.get("coverage") or ""),
        str(case.get("risk") or ""),
        str(case.get("automation_reason") or case.get("automationReason") or "")
    ])
    return priority in ("P0", "P1") and any(word in clue for word in ("主流程", "核心", "入口", "关键", "冒烟"))


# ---------------------------------------------------------------------------
# 动作/输入解析工具
# ---------------------------------------------------------------------------

def action_type(text):
    """根据文本判断动作类型。"""
    text = str(text or "")
    if any(key in text for key in ("点击", "按钮", "勾选", "长按", "选择")):
        return "aiTap"
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
    return RUNTIME_GUARD_MODE if RUNTIME_GUARD_MODE in ("minimal", "balanced", "strict") else "balanced"


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
        "ok": not missing_cases and not generic_assertions,
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
        steps = normalize_text_list(case.get("steps") or [])
        if not steps:
            manual.append({
                "title": case.get("title") or case.get("name") or "未命名用例",
                "reason": "缺少可执行 UI 步骤，暂不生成自动化 YAML",
                "suggested_setup": "补充业务路径和页面入口后再转自动化",
            })
            continue
        if not _case_has_meaningful_assertion(case):
            manual.append({
                "title": case.get("title") or case.get("name") or "未命名用例",
                "reason": "缺少明确业务断言，避免生成只点击不验证的自动化用例",
                "suggested_setup": "补充页面标题、目标列表/空态、弹窗文案、按钮状态等 UI 可见断言",
            })
            continue
        ready.append(case)
    normalized["cases"] = ready
    normalized["manual_cases"] = manual
    normalized["_automation_ready"] = True
    if not ready:
        raise ValueError("没有可转换为自动化 YAML 的用例：请补充可执行步骤和明确 UI 断言")
    return normalized


# ---------------------------------------------------------------------------
# 守卫流程生成
# ---------------------------------------------------------------------------

def external_activity_cleanup_flow(indent):
    """返回清理外部 Activity 的流程行。"""
    return [
        indent + "- runAdbShell: " + yaml_text("input keyevent 3"),
        indent + "- sleep: 500",
        indent + "- runAdbShell: " + yaml_text("input keyevent 187"),
        indent + "- sleep: 1000",
        indent + "- runAdbShell: " + yaml_text("input swipe 540 1900 540 350 300"),
        indent + "- sleep: 500",
        indent + "- runAdbShell: " + yaml_text("input swipe 540 1900 540 350 300"),
        indent + "- sleep: 500",
        indent + "- runAdbShell: " + yaml_text("input swipe 540 1900 540 350 300"),
        indent + "- sleep: 500",
        indent + "- runAdbShell: " + yaml_text("input keyevent 3"),
        indent + "- sleep: 500",
        indent + "- runAdbShell: " + yaml_text("am kill-all"),
        indent + "- sleep: 500",
    ]


def launch_guard_flow(indent, app_package=None, evidence_text=""):
    """生成启动守卫流程。"""
    app_package = (app_package or "").strip()
    if not app_package:
        return []
    mode = runtime_guard_mode()
    flows = external_activity_cleanup_flow(indent) + [
        indent + "- runAdbShell: " + yaml_text("am force-stop " + app_package),
        indent + "- sleep: 1500",
        indent + "- launch: " + app_package,
        indent + "- sleep: 3000",
    ]
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

def flow_lines_for_step(indent, text):
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
            indent + "- sleep: 300",
            indent + "- aiScroll: " + yaml_text(target + "，只滚动该横向列表，不要滚动整个页面"),
            indent + "  scrollType: " + yaml_text("singleAction"),
            indent + "  direction: " + yaml_text("right"),
            indent + "  distance: 400",
            indent + "- sleep: 800",
            indent + "- runAdbShell: " + yaml_text("input swipe 950 1080 150 1080 500"),
            indent + "- sleep: 800",
        ]
    return [f"{indent}- {action_type(text)}: {yaml_text(text)}"]


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


def resolve_app_package(module="", file="", yaml_text="", explicit="", allow_default=False):
    """解析 app package。"""
    resolved = (
        (explicit or "").strip()
        or extract_app_package_from_yaml(yaml_text)
    ).strip()
    if resolved:
        return resolved
    return (os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE).strip() if allow_default else "")


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
    assertions = [normalize_assertion_for_yaml(item, case) for item in normalize_text_list(assertions)]
    assertions = [item for item in assertions if item]

    normalized_steps = []
    for step in steps:
        if isinstance(step, dict):
            action = step.get("action") or step.get("step") or step.get("description") or step.get("name")
            expected = step.get("expected") or step.get("assertion") or step.get("expect")
            if action:
                normalized_steps.append(str(action))
            if expected:
                assertions.append(str(expected))
        else:
            normalized_steps.append(str(step))

    meta = build_baseline_meta(case, normalized_steps, assertions)

    flows = []
    if app_package:
        flows.extend(launch_guard_flow(flow_indent + "  ", app_package))

    for item in preconditions[:8]:
        text = str(item).strip()
        if text:
            flows.append(flow_indent + "  - ai: " + yaml_text(f"确认前置条件：{text}"))

    for item in normalized_steps[:40]:
        text = str(item).strip()
        if text:
            flows.extend(flow_lines_for_step(flow_indent + "  ", text))

    for item in assertions[:20]:
        text = str(item).strip()
        if text:
            if ENABLE_ASSERT_WAITFOR:
                flows.append(flow_indent + "  - aiWaitFor: " + yaml_text(text))
                flows.append(flow_indent + "    timeout: " + str(DEFAULT_WAITFOR_TIMEOUT_MS))
            flows.append(flow_indent + "  - aiAssert: " + yaml_text(text))

    if app_package:
        flows.extend(cleanup_guard_flow(flow_indent + "  ", app_package))

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
    """将横向 icon 区域自然语言滑动改为官方 aiScroll。"""
    if not block:
        return block, []
    lines = block.splitlines()
    result = []
    changes = []
    idx = 0
    evidence = str(evidence_text or "")
    android_context = "android:" in evidence.lower() or "adb" in evidence.lower() or "runadbshell" in (block or "").lower()

    def append_android_horizontal_fallback(out, indent, window_text=""):
        if not android_context:
            return False
        if "input swipe 950 1080 150 1080 500" in window_text:
            return False
        out.append(indent + "- sleep: 500")
        out.append(indent + "- runAdbShell: " + yaml_text("input swipe 950 1080 150 1080 500"))
        out.append(indent + "- sleep: 800")
        return True

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
                result.append(indent + "- sleep: 300")
                result.append(indent + "- aiScroll: " + yaml_text(target + "，只滚动该横向列表，不要滚动整个页面"))
                result.append(indent + "  scrollType: " + yaml_text("singleAction"))
                result.append(indent + "  direction: " + yaml_text("right"))
                result.append(indent + "  distance: 400")
                result.append(indent + "- sleep: 800")
                changes.append("将横向 icon 区域自然语言滑动改为两次官方 aiScroll singleAction + direction:right + distance:400")
                if append_android_horizontal_fallback(result, indent, "\n".join(lines[idx + 1:idx + 6])):
                    changes.append("横向 icon 区域增加 Android ADB 横滑兜底，避免 aiScroll 未触发真实滑动")
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
            result.append(indent + "- aiScroll: " + yaml_text(target or "当前页面中的横向功能 icon 列表区域"))
            result.append(indent + "  scrollType: " + yaml_text("singleAction"))
            result.append(indent + "  direction: " + yaml_text("right"))
            result.append(indent + "  distance: 400")
            result.append(indent + "- sleep: 300")
            result.append(indent + "- aiScroll: " + yaml_text(target or "当前页面中的横向功能 icon 列表区域"))
            result.append(indent + "  scrollType: " + yaml_text("singleAction"))
            result.append(indent + "  direction: " + yaml_text("right"))
            result.append(indent + "  distance: 400")
            result.append(indent + "- sleep: 800")
            changes.append("将横向 icon/功能区滑动强制规范为两次 singleAction + direction:right + distance:400")
            if append_android_horizontal_fallback(result, indent, "\n".join(lines[idx:min(len(lines), j + 6)])):
                changes.append("横向 icon 区域增加 Android ADB 横滑兜底，避免 aiScroll 未触发真实滑动")
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
    """修正 aiWaitFor timeout 过短/过长。"""
    if not block:
        return block, []
    lines = block.splitlines()
    raised = 0
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
        elif value < 15000:
            lines[idx] = f"{m.group(1)}30000{m.group(3)}"
            raised += 1
    changes = []
    if raised:
        changes.append(f"将过短 aiWaitFor timeout 提升到 30000ms {raised} 处")
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
        return 30000
    if any(word in text for word in ("进度", "100%", "100.0%", "模型处理", "切片", "生成", "上传", "导入", "加载到")):
        return 240000
    if any(word in text for word in ("下一步", "去打印", "确认打印", "检查无误", "可点击", "按钮变为可点击")):
        return 60000
    if any(word in text for word in ("列表", "结果", "空态", "详情页", "页面标题")):
        return 60000
    return 30000


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
        while idx < len(lines):
            child = lines[idx]
            if re.match(r"^\s*-\s+[A-Za-z][\w]*\s*:", child):
                break
            tm = re.match(r"^(\s*timeout\s*:\s*)(\d+)(\s*(?:#.*)?)$", child)
            if tm:
                timeout_seen = True
                old_timeout = safe_int(tm.group(2), 0)
                result.append(f"{tm.group(1)}{min(max(old_timeout, 30000), 60000)}{tm.group(3)}")
            else:
                result.append(child)
            idx += 1
        if not timeout_seen:
            result.append(indent + "  timeout: 60000")
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
        insert = external_activity_cleanup_flow(indent)
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
    if not task_block_ends_with_key(text, "terminate") and not task_block_ends_with_force_stop(text):
        text = text + "\n" + "\n".join(cleanup_guard_flow(indent, app_package, evidence_text))
        changes.append("补充后置 force-stop App 和退出弹窗兜底")
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
    text = normalize_full_yaml_structure(yaml_text or "")
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
        "",
        "tasks:",
    ]
    for index, case in enumerate(normalized["cases"], start=1):
        if isinstance(case, dict):
            if app_package and not case.get("app_package") and not case.get("appPackage"):
                case = dict(case)
                case["app_package"] = app_package
            chunks.append(case_to_task_yaml(case, case_index=index))
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
            "",
            "tasks:",
            case_to_task_yaml(case, case_index=index),
        ]
        rendered = "\n".join(chunks) + "\n"
        rendered, _ = normalize_yaml_runtime_guards(rendered, app_package=app_package)
        files.append({
            "index": index,
            "file": unique_name,
            "title": case_title,
            "case_id": first_non_empty(case.get("case_id"), case.get("id"), f"TC-{index:03d}"),
            "content": rendered,
        })

    if not files:
        raise ValueError("没有可转换为自动化 YAML 的用例：请补充可执行步骤和明确 UI 断言")
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
                if action in ("ai", "aiAct", "aiAction", "aiTap", "aiAssert", "aiWaitFor", "aiInput") and _yaml_action_value_blank(item.get(action)):
                    issues.append(f"tasks[{idx}].flow[{fidx}] {action} 内容不能为空")
    return {
        "ok": not issues,
        "platform": platform,
        "taskCount": len(tasks),
        "issues": issues,
        "riskHits": risk_hits,
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
    """Raise generation targets for rich requirement+Figma inputs.

    A large Figma scope plus a requirement document should not be treated as a
    tiny one-path request just because the first analysis pass extracted too few
    requirement points.
    """
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
    min_points = 8 if figma_page_count >= 20 or requirement_size >= 2500 else 6
    for term in _generation_scope_terms_from_rich_context(title, module, requirement_text_assets, used_figma_pages):
        point = term if re.search(r"验收|覆盖|验证|测试", term) else f"{term}验收"
        if any(point in old or old in point for old in points):
            continue
        points.append(point)
        if len(points) >= min_points:
            break
    if len(points) < min_points:
        for idx in range(len(points) + 1, min_points + 1):
            points.append(f"需求文档核心场景{idx}覆盖")
    analysis["requirement_points"] = points
    review = payload.setdefault("review", {})
    if isinstance(review, dict):
        review["rich_generation_scope"] = {
            "enabled": True,
            "requirement_size": requirement_size,
            "figma_page_count": figma_page_count,
            "figma_image_count": figma_image_count,
            "requirement_point_count": len(points),
            "reason": "需求文档或 Figma 范围较大，已提高用例生成目标，避免只生成兜底级少量用例。",
        }
    return payload


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
    for item in raw.get("imageAssets") or raw.get("image_assets") or []:
        if not isinstance(item, dict):
            continue
        image_b64 = item.get("base64") or item.get("contentBase64")
        if not image_b64:
            continue
        name = clean_asset_filename(item.get("name") or "figma-design.png")
        image_assets.append({
            **item,
            "name": name,
            "mime": item.get("mime") or guess_mime(name),
            "base64": image_b64,
        })
    used_pages = [item for item in (raw.get("usedPages") or raw.get("used_pages") or []) if isinstance(item, dict)]
    ignored_pages = [item for item in (raw.get("ignoredPages") or raw.get("ignored_pages") or []) if isinstance(item, dict)]
    saved_designs = [item for item in (raw.get("savedDesigns") or raw.get("saved_designs") or []) if isinstance(item, dict)]
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


def generate_ui_yaml_from_request(d, job_id=None):
    title = d.get("title") or "UI自动化用例"
    module = d.get("module") or "AI测试"
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
                    message=f"已复用准备阶段解析结果：页面 {len(used_figma_pages)} 个，截图 {len(figma_images)} 张",
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
    # 脑图/YAML 视觉校准只使用当前 Figma 和人工上传截图。
    # 页面知识库截图容易包含历史无关页面，保留为文本上下文即可，避免误导模型选图。
    visual_image_assets = figma_images + uploaded_image_assets

    if job_id:
        update_generate_job(job_id, progress=45, step="理解需求", message="正在先根据需求文档拆解测试点和测试用例")
    stage1_text_assets = requirement_text_assets or visual_text_assets or [
        "未提供独立需求文档，请根据标题、模块、Figma/截图和页面知识先归纳业务范围，再生成测试用例。"
    ]
    skill_pipeline_error = ""
    if USE_AI_SKILL_PIPELINE:
        try:
            if job_id:
                update_generate_job(job_id, progress=45, step="需求解析", message="正在按 requirement_analyzer skill 做需求体检和测试点拆解")
            payload = build_cases_payload_from_skills(title, module, stage1_text_assets)
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

    if visual_text_assets or visual_image_assets:
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
            payload = call_dashscope_refine_cases(title, module, payload, visual_text_assets, visual_image_assets)
        except Exception as e:
            review = payload.setdefault("review", {})
            review["visual_refine_error"] = str(e)
            review["visual_refine_skipped"] = "视觉校准超时或失败，已保留需求解析主结果继续生成 YAML"
            review["remaining_risks"] = normalize_text_list(review.get("remaining_risks") or []) + [
                "视觉校准未完成，入口文案和 UI 断言可能需要人工在生成分析中补充截图后重新生成"
            ]
            if job_id:
                update_generate_job(job_id, progress=67, step="视觉校准跳过", message=f"视觉校准失败但不阻塞生成：{str(e)[:100]}")

    review = payload.setdefault("review", {})
    review["generation_targets"] = generation_volume_targets(payload.get("analysis") or {})
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

    if job_id:
        update_generate_job(job_id, progress=72, step="覆盖率审查", message="正在用 coverage_auditor 反查需求点、场景和用例覆盖，补齐遗漏场景")
    try:
        payload = _ensure_rich_generation_scope(payload, title, module, stage1_text_assets, used_figma_pages, figma_images)
        rich_scope = ((payload.get("review") or {}).get("rich_generation_scope") or {}) if isinstance(payload, dict) else {}
        coverage_rounds = 2 if rich_scope.get("enabled") else 1
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
    payload["id"] = case_set_id
    payload["module"] = module

    if job_id:
        update_generate_job(job_id, progress=75, step="保存用例 JSON", message="正在保存模型生成的用例 JSON")
    write_json_file(cases_path(case_set_id), payload)

    if job_id:
        update_generate_job(job_id, progress=85, step="转换 YAML", message="正在按用例拆分生成 Midscene YAML")
    converted_payload = split_automation_ready_cases(payload)
    _, yaml_items = cases_to_separate_midscene_yamls(converted_payload, app_package=app_package, base_file=yaml_file)
    yaml_file = yaml_items[0]["file"]
    yaml = yaml_items[0]["content"]
    yaml_files = [item["file"] for item in yaml_items]
    yaml_checks = []
    yaml_executability_checks = []
    module_dir = safe_join(TASK_DIR, module)
    os.makedirs(module_dir, exist_ok=True)
    for item in yaml_items:
        write_text_file(safe_join(module_dir, item["file"]), item["content"])
        yaml_checks.append({"file": item["file"], **validate_midscene_yaml(item["content"])})
        yaml_executability_checks.append({"file": item["file"], **validate_midscene_yaml_executability(item["content"])})
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
    for item in yaml_items:
        update_task_meta(module, item["file"], {
            "last_case_set_id": case_set_id,
            "last_case_set_title": title,
            "last_generated_at": summary.get("generated_at"),
            "last_case_count": 1,
            "last_manual_case_count": len(converted_payload.get("manual_cases", [])),
        })
    jobs = []
    if create_job:
        for item in yaml_items:
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
        "summary": summary,
        "summaryFiles": summary_files,
        "job": job,
        "jobs": jobs
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
    records.sort(key=lambda item: item.get("mindmap_updated_at") or item.get("generated_at") or "", reverse=True)
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
    has_figma = bool((d.get("figma_url") or d.get("figmaUrl") or "").strip())

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
            update_generate_job(job_id, progress=12, step="刷新 Figma UI 稿", message=f"已清理 {removed} 份旧的自动 Figma UI 稿，将重新按需求筛选")

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
        figma_future = executor.submit(load_figma_generation_context, d, app_package, job_id, query_text, case_set_id, title, module)
        if knowledge_future:
            try:
                knowledge_texts, knowledge_images, used_knowledge_pages = knowledge_future.result()
            except Exception:
                knowledge_texts, knowledge_images, used_knowledge_pages = [], [], []
        else:
            knowledge_texts, knowledge_images, used_knowledge_pages = [], [], []
        try:
            figma_texts, figma_images, used_figma_pages, ignored_figma_pages, saved_figma_designs = figma_future.result()
        except Exception:
            figma_texts, figma_images, used_figma_pages, ignored_figma_pages, saved_figma_designs = [], [], [], [], []

    visual_text_assets = figma_texts + knowledge_texts
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
    stage1_text_assets = (requirement_text_assets + visual_text_assets) or [
        "未提供独立需求文档，请根据标题、模块、当前 Figma/截图先归纳业务范围，再生成测试场景和用例脑图。"
    ]
    if job_id:
        update_generate_job(job_id, progress=50, step="生成用例结构", message="正在生成场景、用例、边界和人工待准备事项")
    if USE_AI_SKILL_PIPELINE:
        try:
            payload = build_cases_payload_from_skills(title, module, stage1_text_assets)
        except Exception as e:
            payload = call_dashscope_cases(title, module, stage1_text_assets, [])
            payload.setdefault("review", {})["skill_pipeline_error"] = str(e)
    else:
        payload = call_dashscope_cases(title, module, stage1_text_assets, [])

    review = payload.setdefault("review", {})
    review["mindmap_only"] = True
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
    if visual_text_assets or mindmap_visual_image_assets:
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
        visual_images_done = 0
        visual_errors = []
        for batch_index, image_batch in enumerate(visual_batches, start=1):
            if job_id and generate_job_should_stop(job_id):
                break
            remaining_budget = int(MINDMAP_VISUAL_TOTAL_BUDGET_SECONDS - (time.time() - visual_start))
            if remaining_budget <= 0:
                visual_errors.append("视觉校准总耗时预算已用完")
                break
            timeout_seconds = max(30, min(int(MINDMAP_VISUAL_TIMEOUT_SECONDS), remaining_budget))
            if job_id:
                progress = min(74, 65 + int((batch_index - 1) / max(1, len(visual_batches)) * 9))
                update_generate_job(
                    job_id,
                    progress=progress,
                    step="视觉校准",
                    message=f"正在分批校准 Figma/截图，第 {batch_index}/{len(visual_batches)} 批，图片 {len(image_batch)} 张"
                )
            try:
                payload = call_dashscope_refine_cases(
                    title,
                    module,
                    payload,
                    visual_text_assets,
                    image_batch,
                    timeout_seconds=timeout_seconds,
                    legacy_fallback=False,
                )
                visual_batches_done += 1
                visual_images_done += len(image_batch)
                payload.setdefault("review", {})["mindmap_visual_grounded"] = True
            except Exception as e:
                visual_errors.append(str(e))
                if job_id:
                    update_generate_job(job_id, progress=67, step="视觉校准跳过", message=f"第 {batch_index} 批视觉校准超时/失败，已降级继续：{str(e)[:100]}")
                break
        review = payload.setdefault("review", {})
        review["mindmap_visual_batches"] = f"{visual_batches_done}/{len(visual_batches)}"
        review["mindmap_visual_images_grounded"] = visual_images_done
        if visual_errors:
            review["visual_refine_error"] = "；".join(visual_errors)[-1000:]
            review["visual_refine_fallback"] = (
                "视觉校准部分批次超时或失败，已保留需求、PDF 文本和 Figma 页面文本继续生成脑图；"
                "未完成的图片批次不会阻塞脑图产出。"
            )

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
            for case in scenario_cases[:6]:
                case_children = []
                if case.get("expected_result"):
                    case_children.append(mm_node(f"检查：{case.get('expected_result')}", indent=4))
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
            orphan_children = [mm_node(case_mm_title(case), indent=3) for case in orphan_cases[:10]]
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
            manual_children.append(mm_node(title_text, [
                mm_node(f"原因：{case.get('reason') or '需要人工确认或准备数据'}", indent=3),
                mm_node(f"准备建议：{case.get('suggested_setup') or case.get('setup') or '按实际环境准备'}", indent=3),
            ], indent=2))
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
    try:
        if exists:
            stat = os.stat(mm_path)
            size = stat.st_size
            updated_at = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime))
    except Exception:
        pass
    return {
        "case_set_id": case_set_id,
        "title": summary.get("title") or case_set_id,
        "module": summary.get("module") or "",
        "yaml_file": summary.get("yaml_file") or "",
        "generated_at": summary.get("generated_at") or "",
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
