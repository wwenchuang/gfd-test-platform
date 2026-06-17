#!/usr/bin/env python3
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn
import base64
import difflib
import hashlib
import html as html_lib
import json
import os
import re
import shutil
import secrets
import subprocess
import tempfile
import threading
import time
import socket
import traceback
import urllib.parse
import urllib.request
import uuid
import zipfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

try:
    import yaml as pyyaml
except Exception:
    pyyaml = None


MIDSCENE_ENV_FILE = os.getenv("MIDSCENE_ENV_FILE", "/opt/midscene.env")
MIDSCENE_ENV_PREFIXES = (
    "DASHSCOPE_",
    "OPENAI_",
    "FEISHU_",
    "FIGMA_",
    "SONIC_",
    "MIDSCENE_",
    "TASK_",
)
MIDSCENE_ENV_EXACT_KEYS = {
    "AI_SKILLS_DIR",
    "PORT",
    "REPORT_DIR",
    "LEARNING_DIR",
    "ASSET_DIR",
    "CASE_DIR",
    "GENERATE_JOB_DIR",
    "KNOWLEDGE_DIR",
    "TZ",
}


def load_startup_env(path=None):
    """Load simple export assignments without executing a shell file."""
    path = path or MIDSCENE_ENV_FILE
    status = {
        "path": path,
        "loaded": False,
        "valid": False,
        "loaded_keys": [],
        "issues": [],
        "error": "",
    }
    if not path or not os.path.exists(path):
        return status
    try:
        if os.stat(path).st_mode & 0o077:
            status["error"] = "配置文件权限过宽，请执行 chmod 600"
            return status
        with open(path, encoding="utf-8") as env_file:
            for raw_line in env_file:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if not re.match(r"^[A-Z_][A-Z0-9_]*$", key):
                    continue
                if key != "APP_PACKAGE" and key not in MIDSCENE_ENV_EXACT_KEYS and not key.startswith(MIDSCENE_ENV_PREFIXES):
                    status["issues"].append({"key": key, "message": "忽略非平台配置变量"})
                    continue
                value = value.strip()
                if any(char in value for char in "“”‘’"):
                    status["issues"].append({"key": key, "message": "包含中文引号，请改用英文单引号"})
                    continue
                if value[:1] in ("'", '"') and value[-1:] != value[:1]:
                    status["issues"].append({"key": key, "message": "引号未闭合"})
                    continue
                if value[-1:] in ("'", '"') and value[:1] != value[-1:]:
                    status["issues"].append({"key": key, "message": "引号不匹配"})
                    continue
                if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
                    value = value[1:-1]
                if not value or "\n" in value or "\r" in value or "export " in value:
                    status["issues"].append({"key": key, "message": "值为空或包含误粘配置内容"})
                    continue
                if not os.environ.get(key):
                    os.environ[key] = value
                    status["loaded_keys"].append(key)
        status["loaded"] = True
        status["valid"] = not status["issues"]
    except Exception as exc:
        status["error"] = str(exc)
    return status


ENV_FILE_LOAD_STATUS = load_startup_env()


def env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def require_secret(name, value):
    weak_values = {"midscene2026", "admin", "password", "test123", "change-me", "change-this-long-random-secret"}
    if APP_ENV == "prod" and (not value or str(value).strip() in weak_values):
        raise RuntimeError(f"{name} 未配置或使用弱默认值；请在 /opt/midscene.env 中配置强随机值")


def validate_runtime_secrets():
    require_secret("MIDSCENE_RUNNER_TOKEN", TOKEN)
    require_secret("TASK_SESSION_SECRET", TASK_SESSION_SECRET)
    if APP_ENV == "prod":
        if not TASK_ADMIN_PASSWORD_HASH:
            raise RuntimeError("TASK_ADMIN_PASSWORD_HASH 未配置；生产环境不允许使用 TASK_ADMIN_PASSWORD 明文密码")
        if not SONIC_CALLBACK_TOKEN:
            raise RuntimeError("SONIC_CALLBACK_TOKEN 未配置；请为 Sonic 回调单独配置 token")
        if SONIC_CALLBACK_TOKEN == TOKEN:
            raise RuntimeError("SONIC_CALLBACK_TOKEN 不能等于 MIDSCENE_RUNNER_TOKEN")
    elif SONIC_CALLBACK_TOKEN and TOKEN and SONIC_CALLBACK_TOKEN == TOKEN:
        print("WARNING: SONIC_CALLBACK_TOKEN equals MIDSCENE_RUNNER_TOKEN; use a separate callback token before production", flush=True)


TASK_DIR = os.getenv("TASK_DIR", "/opt/midscene-tasks")
REPORT_DIR = os.getenv("REPORT_DIR", "/opt/midscene-reports")
LEARNING_DIR = os.getenv("LEARNING_DIR", "/opt/midscene-learning")
ASSET_DIR = os.getenv("ASSET_DIR", "/opt/midscene-assets")
CASE_DIR = os.getenv("CASE_DIR", "/opt/midscene-cases")
GENERATE_JOB_DIR = os.getenv("GENERATE_JOB_DIR", "/opt/midscene-generate-jobs")
KNOWLEDGE_DIR = os.getenv("KNOWLEDGE_DIR", "/opt/midscene-knowledge")
AI_SKILLS_DIR = os.getenv("AI_SKILLS_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_skills"))
APP_ENV = os.getenv("TASK_APP_ENV", "prod").strip().lower()
TOKEN = os.getenv("MIDSCENE_RUNNER_TOKEN", "").strip()
SONIC_CALLBACK_TOKEN = os.getenv("SONIC_CALLBACK_TOKEN", "").strip()
TASK_ADMIN_USER = os.getenv("TASK_ADMIN_USER", "admin")
TASK_ADMIN_PASSWORD_HASH = os.getenv("TASK_ADMIN_PASSWORD_HASH", "")
TASK_ADMIN_PASSWORD = os.getenv("TASK_ADMIN_PASSWORD", "")
TASK_SESSION_SECRET = os.getenv("TASK_SESSION_SECRET", "").strip()
TASK_SESSION_TTL_SECONDS = env_int("TASK_SESSION_TTL_SECONDS", 12 * 60 * 60)
ALLOW_QUERY_TOKEN = env_int("TASK_ALLOW_QUERY_TOKEN", 0) != 0
TASK_ALLOWED_ORIGINS = [
    item.strip()
    for item in os.getenv("TASK_ALLOWED_ORIGINS", "http://101.34.197.12:8088,http://localhost:8088,http://127.0.0.1:8088").split(",")
    if item.strip()
]
MAX_BODY_SIZE = env_int("TASK_MAX_BODY_SIZE", 20 * 1024 * 1024)
MAX_UPLOAD_BODY_SIZE = env_int("TASK_MAX_UPLOAD_BODY_SIZE", 120 * 1024 * 1024)
PORT = env_int("PORT", 8091)
JOB_TIMEOUT_SECONDS = int(os.getenv("MIDSCENE_JOB_TIMEOUT_SECONDS", "1800"))
DEFAULT_APP_PACKAGE = os.getenv("APP_PACKAGE", "com.kfb.model")
DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_TEXT_MODEL = os.getenv("DASHSCOPE_MODEL", "qwen3.6-plus")
DEFAULT_VL_MODEL = os.getenv("DASHSCOPE_VL_MODEL", "qwen3.6-plus")
DEFAULT_REPLANNING_CYCLE_LIMIT = str(env_int("MIDSCENE_REPLANNING_CYCLE_LIMIT", 5))
DEFAULT_FIGMA_API_BASE = "https://api.figma.com/v1"
FIGMA_TIMEOUT_SECONDS = env_int("FIGMA_TIMEOUT_SECONDS", 90)
FIGMA_RETRY_COUNT = env_int("FIGMA_RETRY_COUNT", 2)
FIGMA_IMAGE_EXPORT = env_int("FIGMA_IMAGE_EXPORT", 1) != 0
FIGMA_PARSE_LIMIT = max(1, env_int("FIGMA_PARSE_LIMIT", 80))
FIGMA_REFERENCE_LIMIT = max(1, env_int("FIGMA_REFERENCE_LIMIT", 36))
FIGMA_MAX_REFERENCE_LIMIT = max(FIGMA_REFERENCE_LIMIT, env_int("FIGMA_MAX_REFERENCE_LIMIT", 72))
FIGMA_VISUAL_IMAGE_LIMIT = max(0, env_int("FIGMA_VISUAL_IMAGE_LIMIT", 40))
FIGMA_PARENT_LOOKUP = env_int("FIGMA_PARENT_LOOKUP", 0) != 0
AI_VISION_IMAGE_LIMIT = max(1, env_int("MIDSCENE_AI_VISION_IMAGE_LIMIT", FIGMA_VISUAL_IMAGE_LIMIT or 40))
FALLBACK_DASHSCOPE_API_KEY = os.getenv("FALLBACK_DASHSCOPE_API_KEY", "")
RUNTIME_GUARD_MODE = os.getenv("MIDSCENE_RUNTIME_GUARD_MODE", "balanced").strip().lower()
MAX_STEP_SLEEP_MS = env_int("MIDSCENE_MAX_STEP_SLEEP_MS", 1500)
MAX_LAUNCH_SLEEP_MS = env_int("MIDSCENE_MAX_LAUNCH_SLEEP_MS", 3000)
MAX_TERMINATE_SLEEP_MS = env_int("MIDSCENE_MAX_TERMINATE_SLEEP_MS", 1500)
LONG_SLEEP_TO_WAITFOR_MS = env_int("MIDSCENE_LONG_SLEEP_TO_WAITFOR_MS", 3000)
ENABLE_ASSERT_WAITFOR = env_int("MIDSCENE_ENABLE_ASSERT_WAITFOR", 1) != 0
DEFAULT_WAITFOR_TIMEOUT_MS = env_int("MIDSCENE_WAITFOR_TIMEOUT_MS", 8000)
MAX_WAITFOR_TIMEOUT_MS = env_int("MIDSCENE_MAX_WAITFOR_TIMEOUT_MS", 300000)
USE_AI_SKILL_PIPELINE = env_int("MIDSCENE_USE_AI_SKILL_PIPELINE", 1) != 0
AI_CHAT_TIMEOUT_SECONDS = max(180, env_int("MIDSCENE_AI_CHAT_TIMEOUT_SECONDS", 480))
AI_CHAT_RETRY_COUNT = max(0, env_int("MIDSCENE_AI_CHAT_RETRY_COUNT", 1))
AI_COVERAGE_MODEL_WHEN_LOCAL_OK = env_int("MIDSCENE_COVERAGE_MODEL_WHEN_LOCAL_OK", 0) != 0
REPORT_RETENTION_DAYS = max(1, env_int("MIDSCENE_REPORT_RETENTION_DAYS", 14))
REPORT_RETENTION_MIN_KEEP = max(0, env_int("MIDSCENE_REPORT_RETENTION_MIN_KEEP", 200))
REPORT_CLEANUP_INTERVAL_SECONDS = max(3600, env_int("MIDSCENE_REPORT_CLEANUP_INTERVAL_SECONDS", 24 * 3600))
REPORT_CLEANUP_ON_STARTUP = env_int("MIDSCENE_REPORT_CLEANUP_ON_STARTUP", 1) != 0
# Stable baseline runs must surface failures without silently changing verified YAML.
# Explicit repair endpoints remain available for maintenance work.
ENABLE_AUTOMATIC_BASELINE_REPAIR = env_int("MIDSCENE_ENABLE_AUTO_BASELINE_REPAIR", 0) != 0
# Sonic emits one authoritative completion event for a suite. Case result posts
# enrich Task details, but must not produce partial Feishu summaries first.
SONIC_NOTIFY_ON_SUITE_COMPLETION_ONLY = env_int("SONIC_NOTIFY_ON_SUITE_COMPLETION_ONLY", 1) != 0
# Sonic 2.7.2 stores the authoritative suite lifecycle in /results/list.
# Case callbacks only enrich details; final Feishu summaries are keyed by
# resultId and sent once after Sonic marks the result finished.

JOBS_FILE = os.path.join(LEARNING_DIR, "jobs.json")
REPAIR_DRAFTS_FILE = os.path.join(LEARNING_DIR, "repair-drafts.json")
RUNNERS_FILE = os.path.join(LEARNING_DIR, "runners.json")
TASK_APPS_FILE = os.path.join(LEARNING_DIR, "task-apps.json")
TASK_META_FILE = os.path.join(LEARNING_DIR, "task-meta.json")
BASELINE_REFS_FILE = os.path.join(LEARNING_DIR, "baseline-page-refs.json")
SONIC_SYNC_FILE = os.path.join(LEARNING_DIR, "sonic-sync.json")
SONIC_NOTIFY_LOG_FILE = os.path.join(LEARNING_DIR, "sonic-notify.log")
SONIC_SUITE_RESULTS_FILE = os.path.join(LEARNING_DIR, "sonic-suite-results.json")
SONIC_TOKEN_CACHE_FILE = os.path.join(LEARNING_DIR, "sonic-token-cache.json")
AGENT_RUNS_FILE = os.path.join(LEARNING_DIR, "agent-runs.json")
SONIC_SUITE_COMPLETION_PATHS = {
    "/api/sonic/suite-complete",
    "/api/sonic/suite-report",
    "/api/sonic/custom-robot",
    "/api/sonic/bridge-groovy",
    "/api/sonic/case-yaml",
}
VERSION_DIR = os.path.join(LEARNING_DIR, "versions")
JOB_LOCK = threading.Lock()
GENERATE_LOCK = threading.Lock()
RUNNER_LOCK = threading.Lock()
SONIC_LOCK = threading.Lock()
AGENT_RUN_LOCK = threading.Lock()
SONIC_SUITE_LOCK = threading.Lock()
SONIC_SUITE_TIMERS = {}
ID_LOCK = threading.Lock()
ID_COUNTER = 0


def safe_join(root, *parts):
    root_abs = os.path.abspath(root)
    path = os.path.abspath(os.path.join(root_abs, *parts))
    if path != root_abs and not path.startswith(root_abs + os.sep):
        raise ValueError("非法路径")
    return path


def unique_millis_id(prefix):
    global ID_COUNTER
    with ID_LOCK:
        ID_COUNTER = (ID_COUNTER + 1) % 100000
        counter = ID_COUNTER
    return f"{prefix}_{int(time.time() * 1000)}_{counter:05d}"


def clean_filename(name, default="task.yaml"):
    name = str(name or "").strip()
    name = name.replace("/", "_").replace("\\", "_")
    name = re.sub(r'[\\:*?"<>|]+', "_", name).strip()
    base = re.sub(r"\.(yaml|yml)$", "", name, flags=re.I).strip(" ._\t\r\n")
    if not base:
        name = default
    elif name.startswith("."):
        name = base
    if not name.endswith((".yaml", ".yml")):
        name += ".yaml"
    return name


def is_visible_yaml_filename(name):
    name = str(name or "").strip()
    if not name or name.startswith(".") or name.startswith("._"):
        return False
    if not name.endswith((".yaml", ".yml")):
        return False
    base = re.sub(r"\.(yaml|yml)$", "", name, flags=re.I).strip(" ._\t\r\n")
    return bool(base)


def clean_asset_filename(name, default="asset.txt"):
    name = (name or default).strip()
    name = name.replace("/", "_").replace("\\", "_")
    name = re.sub(r'[\\:*?"<>|]+', "_", name)
    return name or default


def clean_id(value, default="page"):
    value = (value or default).strip()
    value = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value)
    return value.strip("._")[:80] or default


def yaml_text(value):
    value = str(value or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def yaml_task_names(yaml_text_value):
    names = []
    name_re = re.compile(r"^\s*-\s+name:\s*(.+?)\s*$")
    for line in (yaml_text_value or "").splitlines():
        m = name_re.match(line)
        if m:
            names.append(_clean_yaml_name(m.group(1)))
    return names


def yaml_priority_stats(yaml_text_value):
    stats = {"total": 0, "p0": 0, "p1": 0, "p2": 0, "p3": 0, "smoke": 0, "loaded": True}
    lines = (yaml_text_value or "").splitlines()
    task_starts = []
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


def slug_for_file(value):
    value = (value or "测试用例").strip()
    value = re.sub(r'[\\/:*?"<>|]+', "_", value)
    value = re.sub(r"\s+", "_", value)
    value = value.strip("._-")
    return value[:80] or "测试用例"


def action_type(text):
    text = str(text or "")
    if any(key in text for key in ("点击", "按钮", "勾选", "长按", "选择")):
        return "aiTap"
    return "ai"


def parse_input_value(text):
    text = str(text or "").strip()
    if is_input_visibility_or_scroll_instruction(text):
        return ""
    patterns = [
        r".{0,40}?输入框\s*输入[^：:]*[：:]\s*(.+)$",
        r"(?:输入|搜索)\s*[“\"']([^”\"']+)[”\"']",
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
            value = value.strip(" \t\r\n\"'“”‘’")
            if value:
                return value
    return ""


def is_input_visibility_or_scroll_instruction(text):
    text = str(text or "")
    if not any(word in text for word in ("上滑", "下滑", "滑动", "滚动", "直到出现", "直到看到", "能看到", "看到")):
        return False
    if "输入框" not in text:
        return False
    explicit_input_markers = ("输入：", "输入:", "输入姓名：", "输入姓名:", "输入内容：", "输入内容:")
    return not any(marker in text for marker in explicit_input_markers)


def input_target_from_text(text):
    text = str(text or "")
    if "姓名输入框" in text or "姓名" in text and "输入框" in text:
        return "姓名输入框"
    if "搜索" in text:
        return "当前页面的搜索输入框或文本输入框"
    return "当前页面的文本输入框"


def input_action_requires_search_entry(text):
    text = str(text or "")
    if not parse_input_value(text):
        return False
    search_entry_words = (
        "放大镜", "搜索图标", "搜索入口", "搜索按钮", "点击搜索", "右上角搜索", "顶部搜索"
    )
    return any(word in text for word in search_entry_words)


def search_entry_target_from_text(text):
    text = str(text or "")
    if "右上角" in text:
        return "右上角放大镜搜索图标或搜索入口"
    if "顶部" in text:
        return "顶部搜索图标、搜索框或搜索入口"
    return "搜索图标、搜索框或搜索入口"


def adb_input_text(value):
    value = str(value or "")
    value = value.replace("\\", "\\\\").replace(" ", "%s")
    value = value.replace('"', '\\"').replace("'", "\\'")
    return value


def is_safe_adb_text(value):
    return bool(re.match(r"^[A-Za-z0-9._@%+-]+$", str(value or "")))


def is_external_file_picker_context(text):
    text = str(text or "")
    keywords = (
        "本地导入", "文件导入", "文件选择", "选择文件", "文件管理", "文件管理器",
        "系统文件", "文档", "相册", "图片选择", "本地文件", "导入文件",
        "file picker", "documentsui", "media provider"
    )
    return any(keyword.lower() in text.lower() for keyword in keywords)


def normalize_input_locate_for_context(locate, context_text=""):
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
    text = str(evidence_text or "").lower()
    keywords = (
        "未输入", "没输入", "没有输入", "输入失败", "输入框为空", "搜索框为空",
        "输入动作失败", "无法输入", "不会输入", "没看到输入", "光标已经定位",
        "text not entered", "input is empty", "empty input", "failed to input",
    )
    return any(keyword.lower() in text for keyword in keywords)


def evidence_is_toast_assertion_issue(text=""):
    text = str(text or "")
    toast_words = ("toast", "提示", "成功", "已保存", "完成", "没看到", "没有看到", "未找到", "未出现", "无法找到")
    action_words = ("保存", "下载", "导出", "转换", "生成", "写入", "相册", "完成", "成功")
    return any(word.lower() in text.lower() for word in toast_words) and any(word in text for word in action_words)


def runtime_toast_error_from_text(text=""):
    raw = str(text or "")
    lower = raw.lower()
    patterns = (
        ("mapper function returned a null value", "The mapper function returned a null value."),
        ("returned a null value", "returned a null value"),
        ("null value", "null value"),
        ("nullpointerexception", "NullPointerException"),
        ("空指针", "空指针"),
        ("系统异常", "系统异常"),
        ("服务异常", "服务异常"),
        ("发生错误", "发生错误"),
        ("操作失败", "操作失败"),
    )
    for needle, label in patterns:
        if needle in lower or needle in raw:
            return label
    return ""


def should_add_adb_input_fallback(value, context_text, evidence_text=""):
    return (
        is_safe_adb_text(value)
        and is_external_file_picker_context(context_text)
        and evidence_needs_adb_input_fallback(evidence_text)
    )


def detect_wait_strategy_issue(yaml_text, log_text):
    log_lower = str(log_text or "").lower()
    hard_non_script_signals = (
        "http error", "request entity too large", "502", "503", "504", "model configuration",
        "adb: device", "device offline", "no devices", "应用崩溃", "闪退", "exception",
        "服务器异常", "网络异常", "接口异常", "系统错误", "产品缺陷"
    )
    if any(signal.lower() in log_lower for signal in hard_non_script_signals):
        return None
    loading_failure_signals = (
        "timeout", "timed out", "超时", "卡在", "加载中", "按钮持续不可点击",
        "不可点击", "未出现", "没有出现", "failed to locate", "task failed"
    )
    slow_business_signals = (
        "进度", "35%", "100%", "100.0%", "确认打印", "取消打印", "下一步",
        "模型处理", "切片", "上传", "导入", "生成"
    )
    if not any(signal.lower() in log_lower for signal in loading_failure_signals):
        return None
    if not any(signal.lower() in log_lower for signal in slow_business_signals):
        return None
    short_waits = []
    lines = (yaml_text or "").splitlines()
    for idx, line in enumerate(lines):
        m = re.match(r"^\s*-\s+aiWaitFor\s*:\s*(.+?)\s*$", line)
        if not m:
            continue
        condition = strip_yaml_quotes(m.group(1))
        timeout = 0
        j = idx + 1
        while j < len(lines):
            child = lines[j]
            if re.match(r"^\s*-\s+[A-Za-z][\w]*\s*:", child):
                break
            tm = re.match(r"^\s*timeout\s*:\s*(\d+)\s*$", child)
            if tm:
                timeout = safe_int(tm.group(1), 0)
                break
            j += 1
        next_key, next_text = "", ""
        for look_line in lines[j:j + 4]:
            nm = re.match(r"^\s*-\s+([A-Za-z][\w]*)\s*:\s*(.+?)\s*$", look_line)
            if nm:
                next_key, next_text = nm.group(1), strip_yaml_quotes(nm.group(2))
                break
        context = "\n".join(lines[max(0, idx - 1):min(len(lines), idx + 5)])
        timeout_context = context
        if next_key in ("aiTap", "ai", "aiAction", "aiAct") and next_text:
            timeout_context = "\n".join([condition, next_text])
        desired = loading_wait_timeout_for_context(timeout_context)
        if desired >= 60000 and (not timeout or timeout < desired):
            short_waits.append(f"{condition} timeout={timeout or '未设置'}，建议 {desired}ms")
    if short_waits:
        source_text = "\n".join([str(yaml_text or ""), str(log_text or "")])
        wait_targets = []
        for word in ("进度条", "目标按钮", "下一步", "去打印", "确认打印", "取消打印", "返回"):
            if word == "目标按钮" or word in source_text:
                wait_targets.append(word)
        wait_target_text = " / ".join(wait_targets[:4]) or "目标 UI"
        return {
            "category": "script_issue",
            "confidence": 0.82,
            "reason": "失败更像业务加载等待策略过短，可先做一次脚本等待修复；若重跑仍在长等待后失败，应保留为产品/环境问题复核",
            "evidence": short_waits[:5],
            "suggested_action": f"将本次脚本真实涉及的慢加载节点（{wait_target_text}）改为 aiWaitFor + 合理 timeout，只重跑验证一次；仍失败则不要继续放宽脚本",
            "can_auto_repair": True
        }
    return None


def review_ui_terms(text):
    raw = str(text or "")
    terms = []
    for item in re.findall(r"[「“\"']([^」”\"']{1,40})[」”\"']", raw):
        item = item.strip()
        if item and item not in terms:
            terms.append(item)
    ui_words = (
        "确认打印", "继续打印", "去编辑", "去打印", "下一步", "返回", "取消打印",
        "立即打印", "查看全部", "搜索", "保存成功", "保存到相册", "导出", "完成",
        "迷你保龄球套装", "保龄球", "试卷夹"
    )
    for word in ui_words:
        if word in raw and word not in terms:
            terms.append(word)
    return terms[:12]


def sanitize_failure_review_against_sources(review, yaml_text="", stdout="", stderr="", summary=None, ctx=None):
    if not isinstance(review, dict):
        return review
    ctx = ctx or {}
    source_text = "\n".join([
        str(yaml_text or ""),
        str(stdout or ""),
        str(stderr or ""),
        json.dumps(summary, ensure_ascii=False) if summary is not None else "",
        str(ctx.get("report_text") or ""),
    ])
    review_text = "\n".join([
        str(review.get("reason") or ""),
        str(review.get("suggested_action") or ""),
        "\n".join([str(item) for item in (review.get("evidence") or [])]),
    ])
    unseen_terms = []
    for term in review_ui_terms(review_text):
        if term and term not in source_text and term not in unseen_terms:
            unseen_terms.append(term)
    if not unseen_terms:
        return review
    sanitized = dict(review)
    sanitized["category"] = "unknown"
    sanitized["failure_type"] = "review_source_mismatch"
    sanitized["confidence"] = min(float(sanitized.get("confidence") or 0), 0.45)
    sanitized["reason"] = (
        "失败复检引用了当前 YAML、执行日志或报告文本中不存在的控件/步骤："
        + "、".join(unseen_terms[:5])
        + "。已降级为不确定，避免把旧脚本、串用报告或模型臆测当成真实失败原因。"
    )
    sanitized["evidence"] = [
        "当前 YAML/本次日志未出现：" + "、".join(unseen_terms[:5]),
        "请优先确认 Sonic 是否执行了最新同步的 YAML，以及 Midscene 原始报告中的真实失败步骤。"
    ]
    sanitized["suggested_action"] = "不要自动修改业务链路；先核对 Sonic 用例模板和当前 YAML 是否一致，再按原始报告失败步骤处理。"
    sanitized["can_auto_repair"] = False
    return sanitized


def detect_horizontal_scroll_script_issue(yaml_text, log_text):
    text = str(yaml_text or "")
    log = str(log_text or "")
    combined = "\n".join([text, log])
    has_horizontal_scroll = (
        "aiScroll" in text
        and any(word in text for word in ("横向", "icon", "图标", "我的学习", "功能", "列表"))
    )
    missing_target = any(word in log for word in ("未出现", "没有出现", "找不到", "未找到", "failed to locate", "not found", "看不到"))
    target_is_icon = any(word in combined for word in ("试卷夹", "入口", "icon", "图标"))
    if has_horizontal_scroll and missing_target and target_is_icon:
        return {
            "category": "script_issue",
            "confidence": 0.94,
            "reason": "失败点前存在横向 icon 列表 aiScroll，但目标入口仍未出现，结合当前截图更像横向滑动未真正执行或滑动距离/方式不正确，不应判为产品缺陷",
            "evidence": [
                "YAML 中存在横向 icon 列表 aiScroll",
                "执行日志显示目标入口未出现或定位失败",
                "当前页面仅显示横向列表前几个入口，符合未滑到目标入口的脚本问题"
            ],
            "suggested_action": "将横向列表滑动修复为两次 aiScroll singleAction direction:right distance:400，并追加 Android ADB 横滑兜底后重跑",
            "can_auto_repair": True
        }
    return None


def flow_lines_for_step(indent, text):
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


def runtime_guard_mode():
    return RUNTIME_GUARD_MODE if RUNTIME_GUARD_MODE in ("minimal", "balanced", "strict") else "balanced"


def evidence_needs_popup_guard(evidence_text=""):
    text = evidence_text or ""
    keywords = (
        "弹窗", "浮层", "遮挡", "权限", "升级", "广告", "活动", "引导",
        "modal", "dialog", "popup", "permission", "overlay"
    )
    return any(word.lower() in text.lower() for word in keywords)


def external_activity_cleanup_flow(indent):
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


def extract_app_package_from_yaml(yaml_text):
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


def app_package_for_module(module):
    try:
        apps = sonic_notify_known_apps()
        for app in apps:
            if module in (app.get("modules") or []):
                package = (app.get("package") or "").strip()
                if package:
                    return package
    except Exception:
        pass
    return ""


def resolve_app_package(module="", file="", yaml_text="", explicit="", allow_default=False):
    resolved = (
        (explicit or "").strip()
        or app_package_for_module(module)
        or extract_app_package_from_yaml(yaml_text)
    ).strip()
    if resolved:
        return resolved
    return (os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE).strip() if allow_default else "")


def normalize_cases_payload(value):
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

    return {
        "title": value.get("title") or value.get("name") or "测试用例",
        "module": value.get("module") or "AI测试",
        "analysis": value.get("analysis") or {},
        "scenarios": value.get("scenarios") or [],
        "cases": cases,
        "manual_cases": value.get("manual_cases") or value.get("manualCases") or [],
        "review": value.get("review") or {}
    }


def truthy_text(value):
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    if not text:
        return False
    return text in ("1", "true", "yes", "y", "on", "是", "冒烟", "smoke", "smoke_test")


def case_priority(case):
    raw = str(case_value(case, "priority", "level", "severity") or "P2").strip().upper()
    aliases = {
        "0": "P0", "S0": "P0", "CRITICAL": "P0", "BLOCKER": "P0", "最高": "P0", "阻断": "P0",
        "1": "P1", "S1": "P1", "HIGH": "P1", "IMPORTANT": "P1", "高": "P1", "重要": "P1",
        "2": "P2", "S2": "P2", "MEDIUM": "P2", "NORMAL": "P2", "中": "P2", "普通": "P2",
        "3": "P3", "S3": "P3", "LOW": "P3", "低": "P3",
    }
    return raw if raw in ("P0", "P1", "P2", "P3") else aliases.get(raw, "P2")


def case_tags(case):
    return normalize_text_list(case.get("tags") or case.get("labels") or [])


def is_smoke_case(case):
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


def ensure_case_trace(case, index):
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


def case_has_meaningful_assertion(case):
    assertions = normalize_text_list(case.get("assertions") or case.get("expects") or case.get("expected"))
    expected = first_non_empty(case_value(case, "expected_result", "expectedResult", "expected", "expectation"))
    texts = assertions + ([expected] if expected else [])
    if not texts:
        return False
    vague = ("页面正常展示", "结果符合预期", "操作成功", "功能正常", "页面无异常", "进入相关页面")
    return any(text and not any(item in text for item in vague) for text in texts)


def requirement_points_from_payload(payload):
    analysis = payload.get("analysis") if isinstance(payload, dict) else {}
    points = []
    if isinstance(analysis, dict):
        points.extend(normalize_text_list(
            analysis.get("requirement_points")
            or analysis.get("requirementPoints")
            or analysis.get("test_points")
            or analysis.get("testPoints")
            or []
        ))
        if not points:
            points.extend(normalize_text_list(analysis.get("business_goals") or analysis.get("businessGoals") or []))
    if not points:
        for item in payload.get("scenarios") or []:
            if isinstance(item, dict):
                text = first_non_empty(item.get("feature"), item.get("scenario"), item.get("expected"))
                if text:
                    points.append(text)
    return list(dict.fromkeys(point.strip() for point in points if point.strip()))


def coverage_blob_for_item(item):
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


def coverage_tokens(text):
    normalized = str(text or "").lower()
    stop = {
        "页面", "功能", "用户", "展示", "进入", "点击", "验证", "正常", "流程", "场景",
        "可以", "进行", "是否", "相关", "测试", "按钮", "入口", "列表", "内容",
        "查看", "打开", "支持", "完成", "实现", "需要", "能够", "应该", "对应"
    }
    for word in stop:
        normalized = normalized.replace(word, " ")
    raw = re.findall(r"[\w\u4e00-\u9fff]{2,}", normalized)
    tokens = []
    for token in raw:
        if token in stop:
            continue
        tokens.append(token)
        if re.fullmatch(r"[\u4e00-\u9fff]{5,}", token):
            tokens.extend(token[i:i + 4] for i in range(0, max(0, len(token) - 3)))
    return list(dict.fromkeys(tokens))


def point_covered(point, blobs):
    tokens = coverage_tokens(point)
    if not tokens:
        return False
    strong = tokens[:8]
    for blob in blobs:
        lower = blob.lower()
        hit = sum(1 for token in strong if token in lower)
        if hit >= max(1, min(3, len(strong))):
            return True
    return False


def audit_case_coverage(payload):
    normalized = normalize_cases_payload(payload)
    points = requirement_points_from_payload(normalized)
    case_blobs = [coverage_blob_for_item(item) for item in normalized.get("cases") or []]
    scenario_blobs = [coverage_blob_for_item(item) for item in normalized.get("scenarios") or []]
    manual_blobs = [coverage_blob_for_item(item) for item in normalized.get("manual_cases") or []]
    missing_cases = [point for point in points if not point_covered(point, case_blobs + manual_blobs)]
    missing_scenarios = [point for point in points if not point_covered(point, scenario_blobs)]
    generic_assertions = []
    for case in normalized.get("cases") or []:
        if isinstance(case, dict) and not case_has_meaningful_assertion(case):
            generic_assertions.append(case.get("title") or case.get("name") or "未命名用例")
    review = normalized.setdefault("review", {})
    review["coverage_audit"] = {
        "requirement_point_count": len(points),
        "case_count": len(normalized.get("cases") or []),
        "manual_case_count": len(normalized.get("manual_cases") or []),
        "missing_case_points": missing_cases,
        "missing_scenario_points": missing_scenarios,
        "generic_assertion_cases": generic_assertions,
        "ok": not missing_cases and not generic_assertions
    }
    return normalized, review["coverage_audit"]


def split_automation_ready_cases(payload):
    normalized = normalize_cases_payload(payload)
    if normalized.get("_automation_ready"):
        return normalized
    ready = []
    manual = list(normalized.get("manual_cases") or [])
    for case in normalized["cases"]:
        if not isinstance(case, dict):
            continue
        steps = normalize_text_list(case.get("steps") or [])
        if not steps:
            manual.append({
                "title": case.get("title") or case.get("name") or "未命名用例",
                "reason": "缺少可执行 UI 步骤，暂不生成自动化 YAML",
                "suggested_setup": "补充业务路径和页面入口后再转自动化"
            })
            continue
        if not case_has_meaningful_assertion(case):
            manual.append({
                "title": case.get("title") or case.get("name") or "未命名用例",
                "reason": "缺少明确业务断言，避免生成只点击不验证的自动化用例",
                "suggested_setup": "补充页面标题、目标列表/空态、弹窗文案、按钮状态等 UI 可见断言"
            })
            continue
        ready.append(case)
    normalized["cases"] = ready
    normalized["manual_cases"] = manual
    normalized["_automation_ready"] = True
    if not ready:
        raise ValueError("没有可转换为自动化 YAML 的用例：请补充可执行步骤和明确 UI 断言")
    return normalized


def first_non_empty(*values):
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


def yaml_comment_text(value, limit=180):
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text).replace("#", "＃")
    return text[:limit]


def case_value(case, *keys):
    for key in keys:
        if key in case and case.get(key) not in (None, ""):
            return case.get(key)
    return ""


def normalize_text_list(value):
    if value is None:
        return []
    if isinstance(value, list):
        result = []
        for item in value:
            if isinstance(item, dict):
                text = first_non_empty(item.get("action"), item.get("step"), item.get("description"), item.get("name"), item.get("expected"))
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


def build_baseline_meta(case, normalized_steps, assertions):
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
    text = str(assertion or "").strip()
    if not text:
        return ""
    generic_signals = (
        "页面正常展示",
        "列表展示正常",
        "结果符合预期",
        "操作成功",
        "功能正常",
        "页面无异常",
        "跳转成功",
        "进入相关页面",
        "展示正常",
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


def case_to_task_yaml(case, indent="  ", case_index=1):
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


def cases_to_midscene_yaml(payload, app_package=""):
    normalized = payload if isinstance(payload, dict) and payload.get("_automation_ready") else split_automation_ready_cases(payload)
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
    yaml_text = "\n".join(chunks) + "\n"
    yaml_text, _ = normalize_yaml_runtime_guards(yaml_text, app_package=app_package)
    return normalized["title"], yaml_text


def load_jobs():
    data = read_json_file(JOBS_FILE, default=[])
    return data if isinstance(data, list) else []


def save_jobs(jobs):
    write_json_file(JOBS_FILE, jobs)


# ===== Agent Run Storage =====

AGENT_RUN_STEPS = [
    "IDLE", "PLAN", "MATCH_CASES", "GENERATE_YAML", "VALIDATE_YAML",
    "SYNC_SONIC", "RUN_SONIC", "COLLECT_REPORT", "ANALYZE_FAILURE",
    "GENERATE_REPAIR", "RISK_REVIEW", "APPLY_SAFE_REPAIR", "RERUN",
    "GENERATE_SUMMARY", "GENERATE_BUG_DRAFT", "DONE", "FAILED", "WAIT_CONFIRM"
]

AGENT_RISK_KEYWORDS = [
    "确认打印", "开始打印", "支付", "删除", "覆盖基线",
    "格式化", "清空", "解绑", "重置", "批量同步", "批量执行"
]

AUTO_AGENT_RISK_KEYWORDS = AGENT_RISK_KEYWORDS

# ===== Agent Tool Registry =====
AGENT_TOOL_CALLS_FILE = os.path.join(LEARNING_DIR, "agent-tool-calls.json")
AGENT_TOOL_CALL_LOCK = threading.Lock()

AGENT_TOOLS = {
    # READ_TOOLS
    "list_cases": {"name":"list_cases","title":"读取用例列表","category":"READ","riskLevel":"low","write":False,"requiresConfirm":False},
    "read_yaml": {"name":"read_yaml","title":"读取 YAML 文件","category":"READ","riskLevel":"low","write":False,"requiresConfirm":False},
    "list_jobs": {"name":"list_jobs","title":"读取执行记录","category":"READ","riskLevel":"low","write":False,"requiresConfirm":False},
    "read_report": {"name":"read_report","title":"读取执行报告","category":"READ","riskLevel":"low","write":False,"requiresConfirm":False},
    "read_model_strategy": {"name":"read_model_strategy","title":"读取模型策略","category":"READ","riskLevel":"low","write":False,"requiresConfirm":False},
    "list_runners": {"name":"list_runners","title":"读取 Runner 列表","category":"READ","riskLevel":"low","write":False,"requiresConfirm":False},
    # AI_TOOLS
    "analyze_goal": {"name":"analyze_goal","title":"分析测试目标","category":"AI","riskLevel":"low","write":False,"requiresConfirm":False},
    "generate_cases": {"name":"generate_cases","title":"生成测试用例","category":"AI","riskLevel":"low","write":False,"requiresConfirm":False},
    "generate_yaml": {"name":"generate_yaml","title":"生成 YAML","category":"AI","riskLevel":"low","write":False,"requiresConfirm":False},
    "analyze_failure": {"name":"analyze_failure","title":"分析失败原因","category":"AI","riskLevel":"low","write":False,"requiresConfirm":False},
    "generate_repair_draft": {"name":"generate_repair_draft","title":"生成修复草稿","category":"AI","riskLevel":"low","write":False,"requiresConfirm":False},
    "generate_bug_draft": {"name":"generate_bug_draft","title":"生成缺陷草稿","category":"AI","riskLevel":"low","write":False,"requiresConfirm":False},
    "generate_summary": {"name":"generate_summary","title":"生成总结报告","category":"AI","riskLevel":"low","write":False,"requiresConfirm":False},
    # SONIC_TOOLS
    "sonic_list_projects": {"name":"sonic_list_projects","title":"查询 Sonic 项目","category":"SONIC","riskLevel":"low","write":False,"requiresConfirm":False},
    "sonic_list_suites": {"name":"sonic_list_suites","title":"查询 Sonic 测试套","category":"SONIC","riskLevel":"low","write":False,"requiresConfirm":False},
    "sonic_sync_case": {"name":"sonic_sync_case","title":"同步单条用例到 Sonic","category":"SONIC","riskLevel":"medium","write":True,"requiresConfirm":False},
    "sonic_sync_batch": {"name":"sonic_sync_batch","title":"批量同步 Sonic 用例","category":"SONIC","riskLevel":"high","write":True,"requiresConfirm":True},
    "sonic_run_suite": {"name":"sonic_run_suite","title":"执行 Sonic 测试套","category":"SONIC","riskLevel":"medium","write":True,"requiresConfirm":False},
    "sonic_read_result": {"name":"sonic_read_result","title":"读取 Sonic 执行结果","category":"SONIC","riskLevel":"low","write":False,"requiresConfirm":False},
    "sonic_read_report": {"name":"sonic_read_report","title":"读取 Sonic 报告","category":"SONIC","riskLevel":"low","write":False,"requiresConfirm":False},
    # TASK_TOOLS
    "create_runner_job": {"name":"create_runner_job","title":"创建 Runner 任务","category":"TASK","riskLevel":"medium","write":True,"requiresConfirm":False},
    "run_midscene_task": {"name":"run_midscene_task","title":"执行 Midscene 任务","category":"TASK","riskLevel":"medium","write":True,"requiresConfirm":False},
    "retry_failed_job": {"name":"retry_failed_job","title":"重跑失败任务","category":"TASK","riskLevel":"medium","write":True,"requiresConfirm":False},
    "save_repair_draft": {"name":"save_repair_draft","title":"保存修复草稿","category":"TASK","riskLevel":"low","write":True,"requiresConfirm":False},
    "apply_repair_after_confirm": {"name":"apply_repair_after_confirm","title":"应用修复（需确认）","category":"TASK","riskLevel":"high","write":True,"requiresConfirm":True},
    # CONFIRM_TOOLS
    "confirm_high_risk_action": {"name":"confirm_high_risk_action","title":"确认高风险动作","category":"CONFIRM","riskLevel":"high","write":True,"requiresConfirm":True},
    "confirm_apply_yaml": {"name":"confirm_apply_yaml","title":"确认应用 YAML","category":"CONFIRM","riskLevel":"high","write":True,"requiresConfirm":True},
    "confirm_rerun": {"name":"confirm_rerun","title":"确认重新执行","category":"CONFIRM","riskLevel":"medium","write":True,"requiresConfirm":True},
    "confirm_baseline_update": {"name":"confirm_baseline_update","title":"确认覆盖基线","category":"CONFIRM","riskLevel":"high","write":True,"requiresConfirm":True},
    "confirm_bug_submit": {"name":"confirm_bug_submit","title":"确认提交缺陷","category":"CONFIRM","riskLevel":"medium","write":True,"requiresConfirm":True},
}

AGENT_PERMISSION_LEVELS = {
    "READ_ONLY": {"allowed_categories": {"READ"}, "max_auto_risk": "low"},
    "AUTO_SAFE": {"allowed_categories": {"READ","AI","SONIC","TASK","CONFIRM"}, "max_auto_risk": "medium"},
    "FULL_AUTO": {"allowed_categories": {"READ","AI","SONIC","TASK","CONFIRM"}, "max_auto_risk": "medium"},
}

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


def load_agent_tool_calls():
    data = read_json_file(AGENT_TOOL_CALLS_FILE, default={"calls": []})
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("calls") or []
    return []


def save_agent_tool_calls(calls):
    write_json_file(AGENT_TOOL_CALLS_FILE, {"calls": calls if isinstance(calls, list) else []})


def create_tool_call(run_id, tool_name, input_data, risk_level=None):
    tool_def = AGENT_TOOLS.get(tool_name, {})
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
    call["status"] = status
    call["outputSummary"] = output_summary if isinstance(output_summary, str) else json.dumps(output_summary, ensure_ascii=False)[:500]
    call["error"] = error
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    return call


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


def _evaluate_risk(run):
    """评估风险等级。"""
    target = run.get("target", "")
    for kw in AUTO_AGENT_RISK_KEYWORDS:
        if kw in target:
            return "HIGH", kw
    return "LOW", None


def _ai_gateway_available():
    """检查 AI Gateway 是否可用。"""
    try:
        url = os.getenv("AI_GATEWAY_URL", "http://127.0.0.1:8090").rstrip("/") + "/health"
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status == 200
    except Exception:
        return False


def _ai_gateway_post(path, payload, timeout=30):
    """对 AI Gateway 发起 POST 请求（默认 30 秒超时）。"""
    url = os.getenv("AI_GATEWAY_URL", "http://127.0.0.1:8090").rstrip("/") + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {}
    except Exception as e:
        return None


def tool_requires_confirm(tool_def, run):
    if tool_def.get("requiresConfirm"):
        return True
    if run.get("riskHits") and tool_def.get("write"):
        return True
    risk = tool_def.get("riskLevel", "low")
    perm = AGENT_PERMISSION_LEVELS.get(run.get("permissionLevel", run.get("mode", "AUTO_SAFE")), AGENT_PERMISSION_LEVELS["AUTO_SAFE"])
    if RISK_ORDER.get(risk, 0) > RISK_ORDER.get(perm.get("max_auto_risk", "medium"), 1):
        return True
    return False


def can_execute_tool(tool_def, run):
    perm = AGENT_PERMISSION_LEVELS.get(run.get("permissionLevel", run.get("mode", "AUTO_SAFE")), AGENT_PERMISSION_LEVELS["AUTO_SAFE"])
    cat = tool_def.get("category", "UNKNOWN")
    if cat not in perm.get("allowed_categories", set()):
        return False
    return True


def execute_tool(run, tool_name, input_data):
    """Execute a whitelisted tool and return the call record."""
    tool_def = AGENT_TOOLS.get(tool_name)
    if not tool_def:
        raise ValueError(f"未知工具：{tool_name}")
    if not can_execute_tool(tool_def, run):
        raise ValueError(f"权限不足：{run.get('permissionLevel', run.get('mode'))} 不允许调用 {tool_name}")
    call = create_tool_call(run.get("runId", ""), tool_name, input_data)
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


# ===== Agent Tool Handlers =====

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
        txt = read_text(fpath, "")
        if not txt:
            return {"ok": False, "error": "文件不存在或为空"}
        return {"ok": True, "module": mod, "file": fn, "content": txt[:5000], "size": len(txt)}
    except ValueError:
        return {"ok": False, "error": "非法路径"}


def tool_list_jobs(run, inp):
    """Read execution job list."""
    jobs = load_jobs()
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
        router = read_json_file(os.path.join(AI_GATEWAY_DIR, "config", "model-router.json"), default={})
        providers = read_json_file(os.path.join(AI_GATEWAY_DIR, "config", "providers.json"), default={"providers": []})
        prov_list = providers.get("providers", []) if isinstance(providers, dict) else []
        safe_providers = [{"id": p.get("id",""), "name": p.get("name",""), "model": p.get("model","")} for p in prov_list if isinstance(p, dict)]
        return {"router": router, "providers": safe_providers}
    except Exception as e:
        return {"error": str(e)}


def tool_list_runners(run, inp):
    """Read runner list."""
    apps = load_task_apps() if 'load_task_apps' in dir() else {"apps": []}
    return {"apps": [{"package": a.get("package",""), "name": a.get("name","")} for a in (apps.get("apps",[]) or []) if isinstance(a, dict)]}


def tool_analyze_goal(run, inp):
    """Analyze test goal using AI (Qwen) with rule-based fallback."""
    target = inp.get("target", run.get("target", ""))
    scope = run.get("scope", "smoke")
    app_name = run.get("appName", "智小白3D APP")
    risk_hits = [kw for kw in AGENT_RISK_KEYWORDS if kw in target]

    # Rule-based fallback result
    rule_result = {
        "target": target,
        "module": "",
        "keywords": [],
        "matchAll": True,
        "scope": scope,
        "riskLevel": "high" if risk_hits else "low",
        "riskHits": risk_hits,
        "summary": f"执行目标：{target}",
        "suggestedSteps": [
            "匹配或生成用例",
            "生成 YAML",
            "校验 YAML",
            "风险检查" + ("（命中高风险）" if risk_hits else ""),
            "同步 Sonic" if scope in ("smoke", "regression") else "跳过 Sonic",
            "执行测试",
            "收集报告",
            "分析失败（如有）",
            "生成总结",
        ],
    }

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

    prompt = f"""你是测试任务意图解析器。用户输入了一个测试目标，请解析为结构化JSON。

用户输入：{target}
应用名称：{app_name}
执行范围：{scope}

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
- 如果用户说"回归基线"、"跑全部"、"执行所有"等，matchAll=true，keywords为空
- 如果用户指定了具体用例如"关节龙"、"姓名牌"，matchAll=false，keywords只包含业务名词
- 应用名（智小白3D、小白学习）不是业务关键词
- module 应该严格对应上面列出的可用模块目录名"""

    messages = [{"role": "user", "content": prompt}]

    # === Strategy 1: AI Gateway /ai/chat ===
    try:
        gw_result = _ai_gateway_post("/ai/chat", {
            "messages": messages,
            "temperature": 0.1,
        }, timeout=15)
        if gw_result and isinstance(gw_result, dict):
            content = gw_result.get("content", "")
            if content:
                # Strip markdown fence if present
                content = re.sub(r'^\s*```(?:json)?\s*', '', content)
                content = re.sub(r'\s*```\s*$', '', content)
                ai_result = json.loads(content)
                if isinstance(ai_result, dict) and "module" in ai_result:
                    ai_result.setdefault("matchAll", False)
                    ai_result.setdefault("keywords", [])
                    ai_result.setdefault("summary", target)
                    ai_result["riskLevel"] = "high" if risk_hits else ai_result.get("riskLevel", "low")
                    ai_result["riskHits"] = risk_hits
                    ai_result["target"] = target
                    ai_result.setdefault("suggestedSteps", rule_result["suggestedSteps"])
                    run.setdefault("artifacts", {})["goalAnalysis"] = ai_result
                    return ai_result
    except (json.JSONDecodeError, KeyError, TypeError, Exception):
        pass

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
            req = urllib.request.Request(
                f"{base_url}/chat/completions",
                data=req_body,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {api_key}",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                content = data["choices"][0]["message"]["content"]
                ai_result = json.loads(content)
                if isinstance(ai_result, dict) and "module" in ai_result:
                    ai_result.setdefault("matchAll", False)
                    ai_result.setdefault("keywords", [])
                    ai_result.setdefault("summary", target)
                    ai_result["riskLevel"] = "high" if risk_hits else ai_result.get("riskLevel", "low")
                    ai_result["riskHits"] = risk_hits
                    ai_result["target"] = target
                    ai_result.setdefault("suggestedSteps", rule_result["suggestedSteps"])
                    run.setdefault("artifacts", {})["goalAnalysis"] = ai_result
                    return ai_result
    except (json.JSONDecodeError, KeyError, TypeError, Exception):
        pass

    # === Strategy 3: Rule-based fallback ===
    run.setdefault("artifacts", {})["goalAnalysis"] = rule_result
    return rule_result


def tool_generate_cases(run, inp):
    """Generate test cases via AI Gateway."""
    target = inp.get("target") or inp.get("goal") or run.get("target", "")
    if not target:
        return {"ok": False, "error": "target 不能为空"}
    try:
        ai_gw = os.getenv("AI_GATEWAY_URL", "http://localhost:3200")
        req_body = json.dumps({"target": target, "type": "generate_cases"}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(f"{ai_gw}/api/ai/generate", data=req_body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return {"ok": True, "cases": result.get("cases", []), "casesGenerated": len(result.get("cases", []))}
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
        req_body = json.dumps({"prompt": target, "module": module, "type": "generate_yaml"}, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(f"{ai_gw}/api/ai/generate", data=req_body, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return {"ok": True, "yaml": result.get("yaml", ""), "yamlGenerated": bool(result.get("yaml"))}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300], "yamlGenerated": False}


def tool_analyze_failure(run, inp):
    """Analyze failure from job data."""
    job_id = inp.get("jobId") or run.get("failedJobId", "")
    if not job_id:
        return {"message": "无失败任务需要分析"}
    jobs = load_jobs()
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
        "title": f"[{run.get('appName','')}] {run.get('target','')[:50]}",
        "description": f"失败分析：{failure.get('summary','')[:300]}",
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
        resp = sonic_request("GET", "/projects", timeout=15)
        data = sonic_response_data(resp) or {}
        projects = data.get("data", []) if isinstance(data, dict) else []
        safe = [{"id": p.get("id"), "name": p.get("name","")} for p in projects if isinstance(p, dict)]
        return {"projects": safe, "total": len(safe)}
    except Exception as e:
        return {"projects": [], "total": 0, "error": str(e)}


def tool_sonic_list_suites(run, inp):
    """List Sonic test suites."""
    try:
        project_id = inp.get("projectId") or ""
        params = {"id": project_id} if project_id else {}
        resp = sonic_request("GET", "/testSuites", params=params, timeout=15)
        data = sonic_response_data(resp) or {}
        suites = data.get("data", []) if isinstance(data, dict) else []
        safe = [{"id": s.get("id"), "name": s.get("name",""), "caseCount": len(s.get("testCases",[]))} for s in suites if isinstance(s, dict)]
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
    result = sonic_publish_yaml({"module": mod, "file": fn, "taskName": task_name, "dryRun": inp.get("dryRun", False)})
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
        item = next((r for r in items if str(r.get("id","")) == str(report_id)), None)
        return item or {"error": "报告不存在"}
    return {"results": items[:10]}


def tool_create_runner_job(run, inp):
    """Create a runner job."""
    module = inp.get("module", "")
    file = inp.get("file", "")
    if not module or not file:
        return {"ok": False, "error": "module 和 file 不能为空"}
    try:
        job = create_pending_job(
            module=module,
            file=file,
            target_task_name=inp.get("taskName") or inp.get("task_name") or "",
        )
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
        job = create_pending_job(
            module=module,
            file=file,
            target_task_name=inp.get("taskName") or file.replace(".yaml", "").replace(".yml", ""),
        )
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
        with JOB_LOCK:
            jobs = load_jobs()
            old_job = next((j for j in jobs if j.get("job_id") == job_id), None)
        if not old_job:
            return {"ok": False, "error": f"任务 {job_id} 不存在"}
        module = old_job.get("module", "")
        file = old_job.get("file", "")
        if not module or not file:
            return {"ok": False, "error": "原任务缺少 module/file 信息"}
        attempt = (old_job.get("attempt") or 1) + 1
        new_job = create_pending_job(
            module=module,
            file=file,
            target_task_name=old_job.get("target_task_name", ""),
            attempt=attempt,
            parent_job_id=job_id,
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
    draft = dict(inp)
    if not draft.get("draftId"):
        draft["draftId"] = unique_millis_id("repair")
    draft.setdefault("status", "DRAFTED")
    drafts = load_repair_drafts()
    existing = next((d for d in drafts if d.get("draftId") == draft["draftId"]), None)
    if existing:
        existing.update(draft)
    else:
        drafts.append(normalize_repair_draft(draft))
    save_repair_drafts(drafts)
    return {"draftId": draft["draftId"], "status": "DRAFTED"}


def tool_apply_repair_after_confirm(run, inp):
    """Apply a repair draft - update draft status and write optimized YAML."""
    draft_id = inp.get("draftId") or inp.get("draft_id") or ""
    if not draft_id:
        return {"ok": False, "error": "draftId 不能为空"}
    try:
        drafts = load_repair_drafts()
        draft = next((d for d in drafts if d.get("draftId") == draft_id), None)
        if not draft:
            return {"ok": False, "error": f"修复草稿 {draft_id} 不存在"}
        # 更新状态
        draft["status"] = "APPLIED"
        draft["applied_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        save_repair_drafts(drafts)
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
        if _ai_gateway_available():
            try:
                resp = _ai_gateway_post("/ai/generate-case", {
                    "target": run.get("target", ""),
                    "scope": run.get("scope", "smoke"),
                    "mode": run.get("mode", "AUTO_SAFE"),
                    "appName": run.get("appName", ""),
                    "platform": run.get("platform", "android"),
                })
                plan_steps = resp.get("steps", []) if isinstance(resp, dict) else []
                if not plan_steps:
                    plan_steps = [
                        "1. 分析测试目标",
                        "2. 匹配已有用例或生成新用例",
                        "3. 生成并校验 Midscene YAML",
                        "4. 同步 Sonic 并执行测试",
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
                }
                call["status"] = "SUCCESS"
                call["outputSummary"] = "AI Gateway 生成计划完成"
            except Exception as e:
                call["status"] = "SKIPPED"
                call["outputSummary"] = f"AI Gateway 调用失败：{str(e)[:200]}"
                plan = {
                    "steps": [
                        "1. 分析测试目标",
                        "2. 匹配已有用例或生成新用例",
                        "3. 生成并校验 Midscene YAML",
                        "4. 同步 Sonic 并执行测试",
                        "5. 收集报告并分析失败",
                        "6. 生成修复草稿或缺陷草稿",
                        "7. 高风险动作进入 WAIT_CONFIRM",
                        "8. 生成总结报告",
                    ],
                    "mode": run.get("mode", "AUTO_SAFE"),
                    "target": run.get("target", ""),
                    "riskLevel": run.get("riskLevel", "low"),
                }
        else:
            call["status"] = "SKIPPED"
            call["outputSummary"] = "AI Gateway 不可用，使用本地默认计划"
            plan = {
                "steps": [
                    "1. 分析测试目标",
                    "2. 匹配已有用例或生成新用例",
                    "3. 生成并校验 Midscene YAML",
                    "4. 同步 Sonic 并执行测试",
                    "5. 收集报告并分析失败",
                    "6. 生成修复草稿或缺陷草稿",
                    "7. 高风险动作进入 WAIT_CONFIRM",
                    "8. 生成总结报告",
                ],
                "mode": run.get("mode", "AUTO_SAFE"),
                "target": run.get("target", ""),
                "riskLevel": run.get("riskLevel", "low"),
            }
        run.setdefault("artifacts", {})["plan"] = plan
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


# APP 名称 -> 目录关键词映射（用于按应用过滤用例目录）
APP_DIR_KEYWORDS = {
    '智小白3D': ['3D打印基线', '3D打印'],
    '小白学习': ['小白学习'],
}


def get_available_apps():
    """扫描 server-tasks 目录，返回可用应用及其模块列表。"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
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
    """根据应用名确定搜索目录，优先 server-tasks/ 再 server-tasks-all/。"""
    dir_keywords = None
    for app_key, keywords in APP_DIR_KEYWORDS.items():
        if app_key in app_name:
            dir_keywords = keywords
            break

    search_dirs = []

    # 多个候选base目录（支持不同部署方式）
    candidate_bases = [
        os.path.join(base_dir, 'server-tasks'),
        os.path.join(base_dir, 'server-tasks-all'),
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


def _tool_match_cases(run):
    """从 server-tasks 目录匹配用例 - 精确匹配优先。"""
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "list_cases",
        "category": "READ",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {"target": run.get("target", ""), "module": run.get("module", ""),
                  "appName": run.get("appName", "智小白3D APP")},
    }
    try:
        target = run.get("target", "")
        app_name = run.get("appName", "智小白3D APP")
        module = run.get("module", "")
        scope = run.get("scope", "")
        failed_job_id = run.get("failedJobId") or run.get("failed_job_id") or ""
        base_dir = os.path.dirname(os.path.abspath(__file__))

        # === 策略1: 如果有 failedJobId，只匹配对应YAML ===
        if failed_job_id:
            with JOB_LOCK:
                jobs = load_jobs()
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

        # === 策略3: 使用 AI 解析结果（优先）或 fallback 到关键词提取 ===
        goal = (run.get("artifacts") or {}).get("goalAnalysis") or {}
        ai_module = goal.get("module", "")
        ai_keywords = goal.get("keywords", [])
        ai_match_all = goal.get("matchAll", False)

        # AI 解析的 module 优先
        if ai_module and not module:
            module = ai_module
            # 重新过滤 all_yamls 只留该 module 的
            all_yamls = [y for y in all_yamls if y["dir_name"] == module or module in y["dir_name"]]

        match_reason = ""
        matched = []
        skipped = []

        if ai_match_all or (not ai_keywords and not module):
            # AI 判定全匹配
            matched = [y["abs_path"] for y in all_yamls]
            match_reason = f"匹配全部用例（{goal.get('summary', '目标为宽泛执行指令')}）"
        elif ai_keywords:
            # 用 AI 提取的关键词
            for y in all_yamls:
                if any(kw in y["file_name"] or kw in y["task_name"] or kw in y["dir_name"] for kw in ai_keywords):
                    matched.append(y["abs_path"])
                else:
                    skipped.append(y["rel_path"])
            match_reason = f"按AI提取关键词「{'、'.join(ai_keywords)}」匹配"
        elif module:
            matched = [y["abs_path"] for y in all_yamls]
            match_reason = f"匹配模块「{module}」全部用例"
        else:
            # 最终兜底：原有 SCOPE_WORDS 逻辑
            SCOPE_WORDS = ("回归", "冒烟", "基线", "全部", "执行", "测试", "跑一下", "用例", "一下", "3D", "打印")
            APP_NAME_WORDS = ("智小白", "小白学习", "APP", "app", "的")
            remaining = target
            for sw in SCOPE_WORDS:
                remaining = remaining.replace(sw, "")
            for aw in APP_NAME_WORDS:
                remaining = remaining.replace(aw, "")
            parts = [p.strip() for p in re.split(r'[\s,，、/]+', remaining) if p.strip() and len(p.strip()) >= 2]
            if parts:
                for y in all_yamls:
                    if any(kw in y["file_name"] or kw in y["task_name"] or kw in y["dir_name"] for kw in parts):
                        matched.append(y["abs_path"])
                    else:
                        skipped.append(y["rel_path"])
                match_reason = f"按关键词「{'、'.join(parts)}」精确匹配"
            else:
                matched = [y["abs_path"] for y in all_yamls]
                match_reason = "匹配全部用例（目标为宽泛执行指令）"

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
        matched = (run.get("artifacts") or {}).get("matchedCases", [])
        if matched:
            call["status"] = "SKIPPED"
            call["outputSummary"] = f"已有 {len(matched)} 个匹配用例，跳过生成"
            run.setdefault("artifacts", {})["generatedYaml"] = None
            call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            call["durationMs"] = _compute_duration(call)
            _log_tool_call(call, run.get("runId", ""))
            return call
        if _ai_gateway_available():
            try:
                resp = _ai_gateway_post("/ai/generate-yaml", {
                    "target": run.get("target", ""),
                    "appName": run.get("appName", ""),
                    "platform": run.get("platform", "android"),
                })
                yaml_text = resp.get("yaml", "") if isinstance(resp, dict) else ""
                run.setdefault("artifacts", {})["generatedYaml"] = yaml_text[:5000] if yaml_text else None
                call["status"] = "SUCCESS"
                call["outputSummary"] = "YAML 生成完成" if yaml_text else "YAML 生成返回空"
            except Exception as e:
                call["status"] = "SKIPPED"
                call["outputSummary"] = f"AI Gateway YAML 生成失败：{str(e)[:200]}"
                run.setdefault("artifacts", {})["generatedYaml"] = None
        else:
            call["status"] = "SKIPPED"
            call["outputSummary"] = "AI Gateway 不可用，跳过 YAML 生成"
            run.setdefault("artifacts", {})["generatedYaml"] = None
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)
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
        matched = (run.get("artifacts") or {}).get("matchedCases", [])
        generated = (run.get("artifacts") or {}).get("generatedYaml", "")

        # 确定要校验的 YAML 列表：优先使用匹配到的现有 YAML
        yaml_files_to_validate = matched if matched else ([generated] if generated and isinstance(generated, str) else [])

        if not yaml_files_to_validate:
            call["status"] = "SKIPPED"
            call["outputSummary"] = "无 YAML 需要校验"
            call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            call["durationMs"] = _compute_duration(call)
            _log_tool_call(call, run.get("runId", ""))
            return call

        ok_count = 0
        issues = []
        for yf in yaml_files_to_validate:
            try:
                # 如果是生成的 YAML 文本（非文件路径），直接校验内容
                if not os.path.isabs(yf) and not os.path.exists(yf) and ("\n" in yf or "target:" in yf):
                    if "target:" in yf or "flow:" in yf or "tasks:" in yf:
                        ok_count += 1
                    else:
                        issues.append("generated: 缺少关键字段")
                    continue
                txt = read_text_file(yf, "")
                if not txt:
                    issues.append(f"{os.path.basename(yf)}: 文件为空")
                    continue
                if "target:" not in txt and "flow:" not in txt and "tasks:" not in txt:
                    issues.append(f"{os.path.basename(yf)}: 缺少关键字段")
                    continue
                ok_count += 1
            except Exception as e:
                issues.append(f"{os.path.basename(yf)}: {str(e)[:100]}")
        call["status"] = "SUCCESS"
        call["outputSummary"] = f"校验 {len(yaml_files_to_validate)} 个 YAML，{ok_count} 个通过" + (f"；问题：{'; '.join(issues[:5])}" if issues else "")
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)
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


def _tool_sync_sonic(run):
    """同步用例到 Sonic - 真实发布，记录详细失败原因。"""
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "sonic_sync_case",
        "category": "SONIC",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {},
    }
    try:
        matched = (run.get("artifacts") or {}).get("matchedCases", [])
        generated = (run.get("artifacts") or {}).get("generatedYaml", "")

        yaml_to_sync = matched if matched else ([generated] if generated and isinstance(generated, str) else [])

        if not yaml_to_sync:
            call["status"] = "SKIPPED"
            call["outputSummary"] = "无匹配用例，跳过 Sonic 同步"
            call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            call["durationMs"] = _compute_duration(call)
            _log_tool_call(call, run.get("runId", ""))
            return call

        synced = []
        failed_items = []
        suite_ids = set()
        base_dir = os.path.dirname(os.path.abspath(__file__))
        tasks_dir = TASK_DIR if os.path.isdir(TASK_DIR) else os.path.join(base_dir, "server-tasks")

        for yf in yaml_to_sync:
            try:
                # 直接从绝对路径提取：倒数第2级=module，倒数第1级=file
                path_parts = os.path.normpath(yf).split(os.sep)
                if len(path_parts) < 2:
                    failed_items.append({
                        "module": "",
                        "file": os.path.basename(yf),
                        "taskName": "",
                        "error": f"路径格式异常: {yf}",
                        "projectId": None,
                        "suiteId": None,
                    })
                    continue

                fn = path_parts[-1]   # 文件名如 "关节龙打印.yaml"
                mod = path_parts[-2]  # 模块名如 "3D打印基线"
                task_name = fn.replace(".yaml", "").replace(".yml", "")

                # 真实发布到 Sonic（非 dry-run）
                result = sonic_publish_yaml({"module": mod, "file": fn, "dryRun": False})

                if isinstance(result, dict) and result.get("ok"):
                    synced.append({"module": mod, "file": fn, "taskName": task_name})
                    # 收集 suite_id
                    for r in (result.get("results") or []):
                        sid = r.get("sonic_suite_id") or (r.get("suite_sync") or {}).get("suite_id")
                        if sid:
                            suite_ids.add(int(sid))
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
                    })
            except Exception as e:
                failed_items.append({
                    "module": parts[0] if len(parts) >= 2 else "",
                    "file": os.path.basename(yf),
                    "taskName": os.path.basename(yf).replace(".yaml", "").replace(".yml", ""),
                    "error": str(e)[:500],
                    "projectId": None,
                    "suiteId": None,
                })

        # 记录到 artifacts
        artifacts = run.setdefault("artifacts", {})
        artifacts["sonicSync"] = {
            "synced": synced,
            "failed": failed_items,
            "total": len(yaml_to_sync),
            "syncedCount": len(synced),
            "failedCount": len(failed_items),
        }
        if suite_ids:
            artifacts["sonicSuiteId"] = list(suite_ids)[0]
            artifacts["sonicSuiteIds"] = list(suite_ids)

        # 根据结果设置状态
        if len(synced) == 0 and len(failed_items) > 0:
            call["status"] = "FAILED"
            call["outputSummary"] = f"Sonic 同步全部失败，0/{len(yaml_to_sync)} 成功，{len(failed_items)} 失败"
            call["error"] = failed_items[0].get("error", "")[:200] if failed_items else ""
        elif len(failed_items) > 0:
            call["status"] = "PARTIAL_FAILED"
            call["outputSummary"] = f"Sonic 同步部分成功，{len(synced)}/{len(yaml_to_sync)} 成功，{len(failed_items)} 失败"
        else:
            call["status"] = "SUCCESS"
            call["outputSummary"] = f"Sonic 同步完成，{len(synced)}/{len(yaml_to_sync)} 全部成功"

    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)[:500]
        call["outputSummary"] = f"Sonic 同步异常：{str(e)[:200]}"
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def sonic_force_run_suite(suite_id):
    """触发 Sonic 测试套强制执行，返回 resultId。"""
    if not suite_id:
        return {"ok": False, "error": "suiteId 为空"}
    try:
        resp = sonic_request("GET", "/testSuites/runSuite", params={"id": suite_id}, timeout=30)
        data = sonic_response_data(resp)
        if data and isinstance(data, dict):
            result_id = data.get("id") or data.get("resultId") or data.get("result_id")
            return {"ok": True, "resultId": result_id, "data": data}
        # Sonic可能直接返回 resultId 作为整数
        if isinstance(data, (int, str)) and data:
            return {"ok": True, "resultId": data}
        return {"ok": True, "resultId": None, "raw": resp}
    except Exception as e:
        return {"ok": False, "error": str(e)[:500]}


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

        with JOB_LOCK:
            jobs = load_jobs()

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
    with JOB_LOCK:
        jobs = load_jobs()

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
    """触发 Sonic 测试套执行 + 创建 Runner Job。"""
    call = {
        "callId": str(uuid.uuid4())[:8],
        "toolName": "create_runner_job",
        "category": "TASK",
        "startedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "input": {"target": run.get("target", "")},
    }
    try:
        artifacts = run.get("artifacts") or {}
        matched = artifacts.get("matchedCases", [])
        generated = artifacts.get("generatedYaml", "")
        suite_id = artifacts.get("sonicSuiteId")

        yaml_to_run = matched if matched else ([generated] if generated and isinstance(generated, str) else [])

        if not yaml_to_run:
            call["status"] = "SKIPPED"
            call["outputSummary"] = "无用例需要执行"
            call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            call["durationMs"] = _compute_duration(call)
            _log_tool_call(call, run.get("runId", ""))
            return call

        # 1. 触发 Sonic 测试套执行（如果有suite_id）
        sonic_result_id = None
        if suite_id:
            try:
                run_result = sonic_force_run_suite(suite_id)
                if run_result.get("ok"):
                    sonic_result_id = run_result.get("resultId")
            except Exception:
                pass  # Sonic触发失败不阻塞，仍创建本地job

        # 2. 创建本地 Runner Job（兼容Runner执行模式）
        job_ids = []
        base_dir = os.path.dirname(os.path.abspath(__file__))
        tasks_dir = TASK_DIR if os.path.isdir(TASK_DIR) else os.path.join(base_dir, "server-tasks")

        for yf in yaml_to_run:
            try:
                full_path = yf if os.path.isabs(yf) else os.path.join(base_dir, yf)
                if not os.path.exists(full_path):
                    continue
                # 直接从绝对路径提取模块名和文件名
                path_parts = os.path.normpath(full_path).split(os.sep)
                if len(path_parts) < 2:
                    continue
                fn = path_parts[-1]   # 文件名
                mod = path_parts[-2]  # 模块名
                job = create_pending_job(
                    module=mod,
                    file=fn,
                    target_task_name=fn.replace(".yaml", "").replace(".yml", ""),
                )
                if job and job.get("job_id"):
                    job_ids.append(job["job_id"])
            except Exception:
                pass

        summary_parts = [f"创建 {len(job_ids)} 个任务"]
        if sonic_result_id:
            summary_parts.append(f"Sonic 套件已触发 (resultId: {sonic_result_id})")
        elif suite_id:
            summary_parts.append("Sonic 套件触发失败，将通过 Runner 执行")

        # 更新 artifacts
        run_artifacts = run.setdefault("artifacts", {})
        run_artifacts["jobIds"] = job_ids
        if sonic_result_id:
            run_artifacts["sonicResultId"] = sonic_result_id

        # === 等待任务执行完成 ===
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

            wait_result = wait_jobs_finished(job_ids, run, timeout=600, interval=5)
            run_artifacts["jobResult"] = {
                "completedCount": len(wait_result["completed"]),
                "failedCount": len(wait_result["failed"]),
                "timeoutCount": len(wait_result["timeout"]),
                "completed": wait_result["completed"],
                "failed": wait_result["failed"],
                "timeout": wait_result["timeout"],
            }

            if wait_result["timeout"]:
                call["status"] = "PARTIAL_FAILED"
                summary_parts.append(f"{len(wait_result['timeout'])} 个超时")
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
        artifacts = run.get("artifacts") or {}
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

        reports = []
        job_statuses = []
        failed_jobs = []
        success_jobs = []

        # 1. 从本地 job 收集报告
        if job_ids:
            with JOB_LOCK:
                jobs = load_jobs()

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
                }
                job_statuses.append(job_entry)

                if status == "success":
                    # 收集报告URL和路径
                    report_url = job.get("report_url") or job.get("reportUrl", "")
                    local_path = job.get("local_report_path") or job.get("localReportPath", "")
                    report_entry = {
                        "jobId": jid,
                        "module": job.get("module", ""),
                        "file": job.get("file", ""),
                        "reportUrl": report_url,
                        "localPath": local_path,
                        "status": "success",
                    }
                    reports.append(report_entry)
                    success_jobs.append(job_entry)
                elif status in ("failed", "error", "timeout", "cancelled"):
                    # 收集失败信息
                    fail_entry = {
                        **job_entry,
                        "error": job.get("error") or job.get("fail_reason", ""),
                        "stderr_tail": (job.get("stderr") or "")[-500:],
                        "stdout_tail": (job.get("stdout") or "")[-300:],
                    }
                    failed_jobs.append(fail_entry)

        # 2. 从 Sonic 收集结果
        sonic_results = []
        if sonic_result_id:
            try:
                for _ in range(12):
                    resp = sonic_request("GET", "/resultDetail", params={"id": sonic_result_id}, timeout=15)
                    detail = sonic_response_data(resp)
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
        if job_result and not failed_jobs:
            for fj in (job_result.get("failed") or []):
                if not any(f.get("jobId") == fj.get("job_id") for f in failed_jobs):
                    failed_jobs.append({
                        "jobId": fj.get("job_id", ""),
                        "status": fj.get("status", "failed"),
                        "module": fj.get("module", ""),
                        "file": fj.get("file", ""),
                        "error": fj.get("error", ""),
                    })

        # 4. 生成摘要
        summary_parts = []
        if reports:
            summary_parts.append(f"{len(reports)} 个报告")
        if failed_jobs:
            summary_parts.append(f"{len(failed_jobs)} 个失败")
        if success_jobs:
            summary_parts.append(f"{len(success_jobs)} 个成功")
        if sonic_results:
            summary_parts.append(f"Sonic 结果 {len(sonic_results)} 条")
        summary = "，".join(summary_parts) if summary_parts else "无报告数据"

        # 5. 统一写入 artifacts.report
        artifacts["report"] = {
            "reports": reports,
            "jobStatuses": job_statuses,
            "failedJobs": failed_jobs,
            "successJobs": success_jobs,
            "sonicResults": sonic_results,
            "summary": summary,
        }
        run["artifacts"] = artifacts

        call["status"] = "SUCCESS"
        call["outputSummary"] = f"收集到 {len(reports)} 个报告，{len(job_statuses)} 个任务状态"
        if failed_jobs:
            call["outputSummary"] += f"（{len(failed_jobs)} 个失败）"

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

        # 判断是否需要分析
        has_job_failures = len(failed_jobs) > 0
        has_sonic_failures = len(sonic_failed) > 0

        if not has_job_failures and not has_sonic_failures:
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
                failure_context += f"- {sf.get('module','')}/{sf.get('file','')}：{sf.get('error','')}\n"
        elif has_job_failures:
            # 有执行失败
            failure_type = "SCRIPT_ISSUE"
            failure_context = f"执行失败 {len(failed_jobs)} 个任务:\n"
            for fj in failed_jobs[:5]:
                failure_context += f"- {fj.get('module','')}/{fj.get('file','')} ({fj.get('status','')})：{fj.get('error','')}\n"
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
                    "failedJobs": [{"jobId": fj.get("jobId",""), "file": fj.get("file",""), "error": fj.get("error","")} for fj in failed_jobs[:5]],
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
            "title": f"[{run.get('appName','')}] {run.get('target','')[:50]}",
            "description": f"失败分析：{fa.get('summary','')[:300]}",
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
        job_ids = (run.get("artifacts") or {}).get("jobIds", [])
        retried = []
        for jid in job_ids:
            jobs = load_jobs()
            j = next((job for job in jobs if job.get("job_id") == jid or job.get("jobId") == jid), None)
            if j and str(j.get("status", "")).lower() in ("failed", "error", "timeout"):
                new_job = create_pending_job(
                    module=j.get("module", ""),
                    file=j.get("file", ""),
                    target_task_name=j.get("target_task_name", j.get("taskName", "")),
                    parent_job_id=jid,
                )
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
        steps = run.get("steps", [])
        completed = sum(1 for s in steps if s.get("status") == "SUCCESS")
        failed = sum(1 for s in steps if s.get("status") == "FAILED")
        skipped = sum(1 for s in steps if s.get("status") == "SKIPPED")
        summary = {
            "totalSteps": len(steps),
            "completed": completed,
            "failed": failed,
            "skipped": skipped,
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
        run.setdefault("artifacts", {})["summary"] = summary
    except Exception as e:
        call["status"] = "FAILED"
        call["error"] = str(e)
    call["endedAt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    call["durationMs"] = _compute_duration(call)
    _log_tool_call(call, run.get("runId", ""))
    return call


def load_agent_runs():
    data = read_json_file(AGENT_RUNS_FILE, default={"runs": []})
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("runs") or []
    return []


def save_agent_runs(runs):
    write_json_file(AGENT_RUNS_FILE, {"runs": runs})


def create_agent_run(payload):
    run_id = f"agent-{int(time.time() * 1000)}-{secrets.token_hex(4)}"
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    mode = str(payload.get("mode") or "AUTO_SAFE").upper()
    if mode not in ("AUTO_SAFE", "FULL_AUTO", "SEMI_AUTO"):
        mode = "AUTO_SAFE"
    goal = str(payload.get("target") or payload.get("goal") or "").strip()
    risk_hits = [kw for kw in AGENT_RISK_KEYWORDS if kw in goal]
    risk_level = "high" if risk_hits else "low"
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
        "status": "RUNNING",
        "currentStep": "PLAN",
        "progress": 0,
        "createdAt": now,
        "updatedAt": now,
        "steps": steps,
        "artifacts": {
            "plan": None,
            "matchedCases": [],
            "generatedYaml": None,
            "sonicJob": None,
            "report": None,
            "failureAnalysis": None,
            "repairDraft": None,
            "summary": None,
            "bugDraft": None
        },
        "pendingConfirmations": [],
        "riskLevel": risk_level,
        "riskHits": risk_hits,
        "error": None
    }
    return run


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
    run["currentStep"] = step_name
    run["updatedAt"] = now
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
        if step_name == "PLAN":
            result = _tool_agent_plan(run)
        elif step_name == "MATCH_CASES":
            result = _tool_match_cases(run)
        elif step_name == "GENERATE_YAML":
            result = _tool_generate_yaml(run)
        elif step_name == "VALIDATE_YAML":
            result = _tool_validate_yaml(run)
        elif step_name == "RISK_REVIEW":
            result = _tool_risk_review(run)
        elif step_name == "SYNC_SONIC":
            result = _tool_sync_sonic(run)
        elif step_name == "RUN_SONIC":
            result = _tool_run_sonic(run)
        elif step_name == "COLLECT_REPORT":
            result = _tool_collect_report(run)
        elif step_name == "ANALYZE_FAILURE":
            result = _tool_analyze_failure(run)
        elif step_name == "GENERATE_REPAIR":
            result = _tool_generate_repair(run)
        elif step_name == "GENERATE_BUG_DRAFT":
            result = _tool_generate_bug_draft(run)
        elif step_name == "RERUN":
            result = _tool_rerun(run)
        elif step_name == "GENERATE_SUMMARY":
            result = _tool_generate_summary(run)
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
            step["toolCalls"].append(result)
            if result.get("status") == "FAILED":
                error = result.get("error", "工具调用失败")
    except Exception as e:
        error = str(e)
        result = {"status": "FAILED", "error": error}
    # Update step status
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    step["endedAt"] = now
    step["durationMs"] = _compute_duration(step)
    if error:
        step["status"] = "FAILED"
        step["summary"] = f"{step_name} 失败：{str(error)[:200]}"
        step["error"] = str(error)[:500]
    else:
        if step["status"] == "RUNNING":
            # Inherit status from tool call result
            if result and isinstance(result, dict) and result.get("status") in ("SKIPPED",):
                step["status"] = "SKIPPED"
            else:
                step["status"] = "SUCCESS"
        msg = ""
        if isinstance(result, dict):
            msg = result.get("outputSummary") or result.get("summary") or result.get("message") or ""
        step["summary"] = msg or f"{step_name} 完成"
    run["updatedAt"] = now
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
            jobs = load_jobs()
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
    step_order = [
        "PLAN", "MATCH_CASES", "GENERATE_YAML", "VALIDATE_YAML",
        "RISK_REVIEW", "SYNC_SONIC", "RUN_SONIC", "COLLECT_REPORT",
        "ANALYZE_FAILURE", "GENERATE_REPAIR", "GENERATE_BUG_DRAFT",
        "RERUN", "GENERATE_SUMMARY",
    ]
    try:
        for step_name in step_order:
            if run.get("status") in ("CANCELLED", "FAILED", "WAIT_CONFIRM"):
                break
            step = next((s for s in run["steps"] if s["step"] == step_name), None)
            if not step:
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
            if error:
                # 非关键步骤失败不阻塞整个流程
                NON_CRITICAL_STEPS = (
                    "ANALYZE_FAILURE", "GENERATE_REPAIR", "GENERATE_BUG_DRAFT",
                    "SYNC_SONIC", "COLLECT_REPORT", "GENERATE_SUMMARY", "RERUN",
                )
                if step_name in NON_CRITICAL_STEPS:
                    pass  # Non-critical, continue
                else:
                    run["status"] = "FAILED"
                    run["error"] = str(error)[:500]
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
        # Mark remaining pending steps as SKIPPED if run is done or paused
        if run.get("status") not in ("CANCELLED", "FAILED", "WAIT_CONFIRM"):
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


def advance_agent_run(run):
    """Advance agent run by starting background step execution.

    The plan artifact will be generated by the PLAN step (_tool_agent_plan),
    so here we only initialize state and start the worker thread.
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    run["currentStep"] = "PLAN"
    run["status"] = "RUNNING"
    run["progress"] = 0
    # Plan will be populated by _tool_agent_plan during step execution
    run["updatedAt"] = now
    worker = threading.Thread(target=_execute_agent_steps, args=(run["runId"],), daemon=True)
    worker.start()
    return run


REPAIR_DRAFT_STATUSES = {"DRAFTED", "WAIT_CONFIRM", "APPLIED", "REJECTED", "EXPIRED"}


def load_repair_drafts():
    data = read_json_file(REPAIR_DRAFTS_FILE, default={"drafts": []})
    if isinstance(data, list):
        drafts = data
    elif isinstance(data, dict):
        drafts = data.get("drafts") or []
    else:
        drafts = []
    return [normalize_repair_draft(item) for item in drafts if isinstance(item, dict)]


def save_repair_drafts(drafts):
    write_json_file(REPAIR_DRAFTS_FILE, {"drafts": drafts if isinstance(drafts, list) else []})


def normalize_repair_draft(draft):
    draft = dict(draft or {})
    draft_id = str(draft.get("draftId") or draft.get("draft_id") or "").strip()
    if not draft_id:
        draft_id = unique_millis_id("repair")
    job_id = str(draft.get("jobId") or draft.get("job_id") or "").strip()
    status = str(draft.get("status") or "DRAFTED").upper()
    if status not in REPAIR_DRAFT_STATUSES:
        status = "DRAFTED"
    risk_hits = draft.get("riskHits") or draft.get("risk_hits") or []
    if isinstance(risk_hits, str):
        risk_hits = [item.strip() for item in re.split(r"[,，、\s]+", risk_hits) if item.strip()]
    if not isinstance(risk_hits, list):
        risk_hits = []
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    fixed_yaml = draft.get("fixedYaml") or draft.get("fixed_yaml") or draft.get("yaml") or ""
    original_yaml = draft.get("originalYaml") or draft.get("original_yaml") or ""
    normalized = {
        **draft,
        "draftId": draft_id,
        "draft_id": draft_id,
        "jobId": job_id,
        "job_id": job_id,
        "module": str(draft.get("module") or "").strip(),
        "file": clean_filename(draft.get("file") or "task.yaml") if draft.get("file") else "",
        "taskName": str(draft.get("taskName") or draft.get("task_name") or "").strip(),
        "status": status,
        "failureType": str(draft.get("failureType") or draft.get("failure_type") or "SCRIPT_ISSUE").upper(),
        "riskLevel": str(draft.get("riskLevel") or draft.get("risk_level") or "medium").lower(),
        "riskHits": [str(item) for item in risk_hits if str(item).strip()],
        "risk_hits": [str(item) for item in risk_hits if str(item).strip()],
        "analysis": draft.get("analysis") or "",
        "originalYaml": original_yaml,
        "original_yaml": original_yaml,
        "fixedYaml": fixed_yaml,
        "fixed_yaml": fixed_yaml,
        "diff": draft.get("diff") or draft.get("diff_summary") or "",
        "validation": draft.get("validation") or {},
        "requireConfirm": True,
        "require_confirm": True,
        "createdAt": draft.get("createdAt") or draft.get("created_at") or now,
        "created_at": draft.get("created_at") or draft.get("createdAt") or now,
        "updatedAt": draft.get("updatedAt") or draft.get("updated_at") or now,
        "updated_at": draft.get("updated_at") or draft.get("updatedAt") or now,
    }
    return normalized


def upsert_repair_draft(draft):
    normalized = normalize_repair_draft(draft)
    normalized["updatedAt"] = normalized["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    drafts = load_repair_drafts()
    replaced = False
    for idx, item in enumerate(drafts):
        if item.get("draftId") == normalized.get("draftId"):
            drafts[idx] = normalized
            replaced = True
            break
    if not replaced:
        drafts.insert(0, normalized)
    save_repair_drafts(drafts[:500])
    return normalized


def repair_drafts_for_job(job_id):
    job_id = str(job_id or "").strip()
    if not job_id:
        return []
    return [draft for draft in load_repair_drafts() if draft.get("jobId") == job_id or draft.get("job_id") == job_id]


def active_repair_draft_for_job(job_id):
    for draft in repair_drafts_for_job(job_id):
        if draft.get("status") in ("DRAFTED", "WAIT_CONFIRM"):
            return draft
    return None


def repair_draft_by_id(draft_id):
    draft_id = str(draft_id or "").strip()
    if not draft_id:
        return None
    for draft in load_repair_drafts():
        if draft.get("draftId") == draft_id or draft.get("draft_id") == draft_id:
            return draft
    return None


def normalize_job_record(job):
    job = dict(job or {})
    job_id = str(job.get("job_id") or job.get("jobId") or job.get("id") or "").strip()
    report_url = job.get("report_url") or job.get("reportUrl") or job.get("sonic_report_url") or ""
    failure_review = job.get("failure_review") or job.get("failureReview") or {}
    repair_draft = job.get("repair_draft") or job.get("repairDraft") or active_repair_draft_for_job(job_id) or {}
    job.update({
        "job_id": job_id,
        "jobId": job_id,
        "runId": job.get("run_id") or job.get("runId") or job_id,
        "traceId": job.get("trace_id") or job.get("traceId") or "",
        "standardStatus": job.get("status") or job.get("state") or "",
        "currentStep": job.get("step") or job.get("current_step") or job.get("currentStep") or "",
        "taskName": job.get("target_task_name") or job.get("current_task_name") or job.get("task_name") or job.get("file") or "",
        "report_url": report_url,
        "reportUrl": report_url,
        "failure_review": failure_review,
        "failureReview": failure_review,
        "repair_draft": repair_draft,
        "repairDraft": repair_draft,
        "recentError": job.get("error") or job.get("message") or job.get("error_message") or "",
    })
    return job


def load_runners():
    data = read_json_file(RUNNERS_FILE, default={})
    return data if isinstance(data, dict) else {}


def save_runners(runners):
    write_json_file(RUNNERS_FILE, runners)


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


def load_task_meta():
    data = read_json_file(TASK_META_FILE, default={})
    return data if isinstance(data, dict) else {}


def save_task_meta(data):
    write_json_file(TASK_META_FILE, data)


def load_sonic_sync():
    data = read_json_file(SONIC_SYNC_FILE, default={"cases": {}})
    if not isinstance(data, dict):
        data = {"cases": {}}
    cases = data.get("cases") if isinstance(data.get("cases"), dict) else {}
    data["cases"] = cases
    return data


def save_sonic_sync(data):
    write_json_file(SONIC_SYNC_FILE, data)


def load_sonic_suite_results():
    data = read_json_file(SONIC_SUITE_RESULTS_FILE, default={"suites": {}, "active": {}})
    if not isinstance(data, dict):
        data = {"suites": {}, "active": {}}
    if not isinstance(data.get("suites"), dict):
        data["suites"] = {}
    if not isinstance(data.get("active"), dict):
        data["active"] = {}
    return data


def save_sonic_suite_results(data):
    write_json_file(SONIC_SUITE_RESULTS_FILE, data)


def task_key(module, file):
    return f"{module}::{clean_filename(file)}"


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


def sonic_base_url():
    return (os.getenv("SONIC_BASE_URL") or os.getenv("SONIC_URL") or "http://101.34.197.12:3000").rstrip("/")


def sonic_api_prefix():
    return os.getenv("SONIC_API_PREFIX", "/server/api/controller").rstrip("/")


SONIC_LOGIN_STATE = {
    "attempted_at": "",
    "ok": None,
    "error": "",
}


def jwt_expire_ts(token):
    try:
        parts = str(token or "").split(".")
        if len(parts) < 2:
            return 0
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload.encode("utf-8")).decode("utf-8", errors="replace"))
        return safe_int(data.get("exp"), 0)
    except Exception:
        return 0


def sonic_env_token():
    for key in ("SONIC_TOKEN", "SONIC_TOKEN_2_7_2", "SONICTOKEN", "SONIC_JWT"):
        value = os.getenv(key)
        if value:
            return value.strip().strip("\"'")
    return ""


def sonic_cached_token(expected_username=""):
    try:
        data = read_json_file(SONIC_TOKEN_CACHE_FILE, default={}) or {}
        token = str(data.get("token") or "").strip()
        if not token:
            return ""
        cached_username = str(data.get("username") or "").strip()
        if expected_username and cached_username and cached_username != expected_username:
            return ""
        exp = safe_int(data.get("exp") or jwt_expire_ts(token), 0)
        if exp and exp <= int(time.time()) + 120:
            return ""
        return token
    except Exception:
        return ""


def sonic_login_credentials():
    username = (
        os.getenv("SONIC_USERNAME")
        or os.getenv("SONIC_USER")
        or os.getenv("SONIC_LOGIN_USER")
        or ""
    ).strip().strip("\"'")
    password = (
        os.getenv("SONIC_PASSWORD")
        or os.getenv("SONIC_PASS")
        or os.getenv("SONIC_LOGIN_PASSWORD")
        or ""
    ).strip().strip("\"'")
    return username, password


def sonic_login_token():
    username, password = sonic_login_credentials()
    if not username or not password:
        return ""
    body = json.dumps({"userName": username, "password": password}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        sonic_url("/users/login"),
        data=body,
        headers={
            "Accept": "*/*",
            "Accept-Language": "zh_CN",
            "Content-Type": "application/json;charset=UTF-8",
            "User-Agent": "Mozilla/5.0 (MidsceneTaskManager Sonic integration)",
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    parsed = json.loads(raw) if raw else {}
    token = ""
    if isinstance(parsed, dict):
        token = str(parsed.get("data") or "").strip()
        if parsed.get("code") not in (None, 2000) or not token:
            raise RuntimeError(sonic_response_error_message(parsed) or "Sonic 登录失败")
    exp = jwt_expire_ts(token)
    try:
        os.makedirs(LEARNING_DIR, exist_ok=True)
        write_text_file(SONIC_TOKEN_CACHE_FILE, json.dumps({
            "token": token,
            "username": username,
            "exp": exp,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, ensure_ascii=False, indent=2))
    except Exception:
        pass
    SONIC_LOGIN_STATE.update({
        "attempted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "ok": True,
        "error": "",
    })
    return token


def sonic_token(force_refresh=False):
    username, password = sonic_login_credentials()
    login_configured = bool(username and password)
    if login_configured:
        if not force_refresh:
            token = sonic_cached_token(expected_username=username)
            if token:
                return token
        try:
            return sonic_login_token()
        except Exception as e:
            SONIC_LOGIN_STATE.update({
                "attempted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "ok": False,
                "error": str(e),
            })
            try:
                append_sonic_notify_log("sonic_login_failed", {"token_source": "login"}, error=str(e))
            except Exception:
                pass
            # Compatibility fallback: retain a manually supplied token when automatic login is temporarily unavailable.
            if not force_refresh:
                token = sonic_env_token()
                if token:
                    exp = jwt_expire_ts(token)
                    if not exp or exp > int(time.time()) + 120:
                        return token
            return ""
    if not force_refresh:
        token = sonic_env_token()
        if token:
            exp = jwt_expire_ts(token)
            if not exp or exp > int(time.time()) + 120:
                return token
        token = sonic_cached_token()
        if token:
            return token
    try:
        return sonic_login_token()
    except Exception as e:
        SONIC_LOGIN_STATE.update({
            "attempted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "ok": False,
            "error": str(e),
        })
        try:
            append_sonic_notify_log("sonic_login_failed", {"token_source": sonic_token_source(include_login=False)}, error=str(e))
        except Exception:
            pass
        return ""


def sonic_token_source(include_login=True):
    username, password = sonic_login_credentials()
    if include_login and username and password:
        if SONIC_LOGIN_STATE.get("ok") is False and sonic_env_token():
            return "static_token_fallback"
        if sonic_cached_token(expected_username=username):
            return "login_cache"
        return "login"
    for key in ("SONIC_TOKEN", "SONIC_TOKEN_2_7_2", "SONICTOKEN", "SONIC_JWT"):
        value = os.getenv(key)
        if value and value.strip().strip("\"'"):
            return key
    if include_login and sonic_cached_token():
        return "cache"
    return ""


def sonic_auth_preview():
    username, password = sonic_login_credentials()
    login_configured = bool(username and password)
    return {
        "login_configured": login_configured,
        "static_token_configured": bool(sonic_env_token()),
        "preferred_source": "login" if login_configured else ("static_token" if sonic_env_token() else ""),
        "active_source": sonic_token_source(),
        "login_attempted_at": SONIC_LOGIN_STATE.get("attempted_at", ""),
        "login_ok": SONIC_LOGIN_STATE.get("ok"),
        "login_error": SONIC_LOGIN_STATE.get("error", ""),
    }


def sonic_token_fingerprint():
    token = sonic_token()
    if not token:
        return ""
    return f"{token[:8]}...{token[-8:]} len={len(token)}"


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


def runtime_env_preview(env):
    preview = dict(env or {})
    for key in ("DASHSCOPE_API_KEY", "OPENAI_API_KEY"):
        if preview.get(key):
            value = str(preview[key])
            preview[key] = f"{value[:6]}...{value[-4:]} len={len(value)}"
    return preview


def dashscope_api_key(required=True):
    value = (os.getenv("DASHSCOPE_API_KEY") or os.getenv("OPENAI_API_KEY") or FALLBACK_DASHSCOPE_API_KEY or "").strip().strip("\"'")
    if required and not value:
        raise ValueError("未配置 DASHSCOPE_API_KEY/OPENAI_API_KEY")
    return value


def dashscope_base_url():
    return (os.getenv("DASHSCOPE_BASE_URL") or os.getenv("OPENAI_BASE_URL") or DEFAULT_DASHSCOPE_BASE_URL).rstrip("/")


def dashscope_text_model():
    return (os.getenv("DASHSCOPE_MODEL") or DEFAULT_TEXT_MODEL).strip()


def dashscope_vl_model():
    return (os.getenv("DASHSCOPE_VL_MODEL") or os.getenv("MIDSCENE_MODEL_NAME") or DEFAULT_VL_MODEL).strip()


def dashscope_model_for_images(image_assets=None):
    return dashscope_vl_model() if image_assets else dashscope_text_model()


def sonic_referer_for_request(path, params=None):
    params = params or {}
    project_id = safe_int(params.get("projectId") or params.get("project_id"), 0)
    if not project_id:
        return ""
    path = "/" + str(path or "").lstrip("/")
    if path.startswith("/results/"):
        page = "Results"
    elif path.startswith("/testCases/") or path.startswith("/steps/"):
        page = "TestCase"
    elif path.startswith("/modules/"):
        page = "TestCase"
    else:
        page = "Results"
    return f"{sonic_base_url()}/Home/{project_id}/{page}"


def sonic_headers(extra=None, has_body=False, path="", params=None):
    headers = {
        "Accept": "*/*",
        "Accept-Language": "zh_CN",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
        "User-Agent": "Mozilla/5.0 (MidsceneTaskManager Sonic integration)",
    }
    token = sonic_token()
    if token:
        # Sonic 2.7.2 源码中 Gateway/AuthFilter 与 Controller/PermissionFilter 均读取 SonicToken。
        headers["SonicToken"] = token
    referer = sonic_referer_for_request(path, params)
    if referer:
        headers["Referer"] = referer
    if has_body:
        headers["Content-Type"] = "application/json;charset=UTF-8"
    if extra:
        headers.update(extra)
    return headers


def sonic_url(path, params=None):
    path = "/" + str(path or "").lstrip("/")
    url = sonic_base_url() + sonic_api_prefix() + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    return url


def sonic_request(method, path, params=None, body=None, timeout=20):
    if not sonic_base_url():
        raise ValueError("未配置 SONIC_BASE_URL")
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    header_params = dict(params or {})
    if isinstance(body, dict):
        for key in ("projectId", "project_id"):
            if key in body and key not in header_params:
                header_params[key] = body.get(key)
    headers = sonic_headers(has_body=body is not None, path=path, params=header_params)
    req = urllib.request.Request(
        sonic_url(path, params=params),
        data=data,
        headers=headers,
        method=method.upper()
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            parsed = json.loads(raw) if raw else {}
            if sonic_response_auth_status(parsed) == "token_invalid":
                refresh_token = sonic_token(force_refresh=True)
                if refresh_token:
                    retry_headers = sonic_headers(has_body=body is not None, path=path, params=header_params)
                    retry_headers["SonicToken"] = refresh_token
                    retry_req = urllib.request.Request(
                        sonic_url(path, params=params),
                        data=data,
                        headers=retry_headers,
                        method=method.upper()
                    )
                    with urllib.request.urlopen(retry_req, timeout=timeout) as retry_resp:
                        retry_raw = retry_resp.read().decode("utf-8", errors="replace")
                        return json.loads(retry_raw) if retry_raw else {}
            return parsed
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Sonic HTTP {e.code} {path}: {raw[:1000]}")
    except Exception as e:
        raise RuntimeError(f"Sonic 请求失败 {path}：{e}")


def sonic_response_data(resp):
    if not isinstance(resp, dict):
        return None
    if resp.get("code") not in (None, 2000):
        raise RuntimeError(sonic_response_error_message(resp))
    return resp.get("data")


def sonic_auth_failure_message():
    auth = sonic_auth_preview()
    if auth.get("login_configured") and auth.get("login_error"):
        detail = re.sub(r"\s+", " ", str(auth.get("login_error") or "")).strip()
        return f"Sonic 自动登录失败：{detail[:240]}；请确认服务进程已加载 SONIC_USERNAME/SONIC_PASSWORD 且 Sonic 登录网关可访问"
    if auth.get("login_configured"):
        return "Sonic 鉴权失败：已配置账号密码自动登录，但生成的 Token 未通过校验，请运行 Sonic 诊断查看登录结果"
    return "Sonic 鉴权失败：未检测到可用自动登录配置，请在服务进程配置 SONIC_USERNAME/SONIC_PASSWORD"


def sonic_response_error_message(resp):
    if not isinstance(resp, dict):
        return str(resp)
    code = resp.get("code")
    message = resp.get("message") or resp.get("msg") or ""
    if code == 1001 or str(message).lower() == "unauthorized":
        return sonic_auth_failure_message()
    if code == 1003 or "暂无权限" in str(message) or "permission" in str(message).lower():
        return "Sonic token 有效，但当前账号角色没有该接口资源权限"
    if code == 1004 or "resource" in str(message).lower() or "uri" in str(message).lower():
        return "Sonic 资源表未找到该接口，请在 Sonic 资源管理中同步资源"
    return message or f"Sonic 返回异常：{resp}"


def sonic_response_auth_status(resp):
    if not isinstance(resp, dict):
        return "unknown"
    code = resp.get("code")
    message = str(resp.get("message") or resp.get("msg") or "").lower()
    if code in (None, 2000):
        return "ok"
    if code == 1001 or message == "unauthorized":
        return "token_invalid"
    if code == 1003 or "permission" in message or "暂无权限" in message:
        return "permission_denied"
    if code == 1004 or "resource" in message or "uri" in message:
        return "resource_not_found"
    return "error"


def sonic_safe_request_shape(path, params=None, body=None):
    headers = sonic_headers(has_body=body is not None, path=path, params=params or {})
    return {
        "url": sonic_url(path, params=params),
        "header_names": sorted(headers.keys()),
        "has_token": bool(headers.get("SonicToken")),
        "token_source": sonic_token_source(),
        "token_fingerprint": sonic_token_fingerprint(),
    }


def sonic_probe_token():
    path = "/users"
    try:
        resp = sonic_request("GET", path, timeout=10)
        status = sonic_response_auth_status(resp)
        data = resp.get("data") if isinstance(resp, dict) else None
        user = {}
        if isinstance(data, dict):
            user = {
                "id": data.get("id"),
                "userName": data.get("userName"),
                "role": data.get("role"),
                "roleName": data.get("roleName"),
            }
        return {
            "path": path,
            "ok": status == "ok",
            "code": resp.get("code") if isinstance(resp, dict) else None,
            "message": resp.get("message") if isinstance(resp, dict) else "",
            "auth_status": status,
            "error": "" if status == "ok" else sonic_response_error_message(resp),
            "user": user,
            "request": sonic_safe_request_shape(path),
        }
    except Exception as e:
        return {
            "path": path,
            "ok": False,
            "auth_status": "request_error",
            "error": str(e),
            "request": sonic_safe_request_shape(path),
        }


def sonic_probe_endpoint(path, params=None):
    params = params or {}
    try:
        resp = sonic_request("GET", path, params=params, timeout=10)
        status = sonic_response_auth_status(resp)
        data = resp.get("data") if isinstance(resp, dict) else None
        if status != "ok":
            return {
                "path": path,
                "params": params,
                "ok": False,
                "code": resp.get("code") if isinstance(resp, dict) else None,
                "message": resp.get("message") if isinstance(resp, dict) else "",
                "auth_status": status,
                "error": sonic_response_error_message(resp),
                "request": sonic_safe_request_shape(path, params=params),
            }
        count = 0
        if isinstance(data, list):
            count = len(data)
        elif isinstance(data, dict):
            count = safe_int(data.get("total") or data.get("totalElements") or data.get("totalCount"), 0)
            if not count:
                count = len(extract_page_items(data))
        return {
            "path": path,
            "params": params,
            "ok": True,
            "code": resp.get("code"),
            "message": resp.get("message") or resp.get("msg") or "",
            "auth_status": status,
            "data_type": type(data).__name__,
            "count": count,
            "request": sonic_safe_request_shape(path, params=params),
        }
    except Exception as e:
        return {
            "path": path,
            "params": params,
            "ok": False,
            "auth_status": "request_error",
            "error": str(e),
            "request": sonic_safe_request_shape(path, params=params),
        }


def sonic_list_projects():
    return sonic_response_data(sonic_request("GET", "/projects/list")) or []


def sonic_list_modules(project_id):
    return sonic_response_data(sonic_request("GET", "/modules/list", params={"projectId": project_id})) or []


def sonic_list_cases(project_id, platform=1, name=""):
    result = []
    page_size = 200
    for page in range(1, 21):
        params = {
            "projectId": project_id,
            "platform": platform,
            "name": name or "",
            "page": page,
            "pageSize": page_size,
            "editTimeSort": "desc"
        }
        data = sonic_response_data(sonic_request("GET", "/testCases/list", params=params)) or {}
        if isinstance(data, list):
            return data
        items = extract_page_items(data)
        result.extend(items)
        total = 0
        if isinstance(data, dict):
            total = safe_int(data.get("total") or data.get("totalElements") or data.get("totalCount"), 0)
        if not items:
            break
        if total:
            if len(result) >= total:
                break
        elif len(items) < page_size:
            break
    return result


def sonic_list_steps(case_id):
    return sonic_response_data(sonic_request("GET", "/steps/listAll", params={"caseId": case_id})) or []


def sonic_project_id_for_app(app):
    value = app.get("sonic_project_id") or app.get("sonicProjectId") or app.get("project_id")
    try:
        return int(value)
    except Exception:
        return 0


def sonic_project_name_for_app(app):
    return (app.get("sonic_project_name") or app.get("sonicProjectName") or app.get("name") or "").strip()


def sonic_find_project_id(app):
    configured = sonic_project_id_for_app(app)
    if configured:
        return configured
    target = sonic_project_name_for_app(app)
    if not target:
        return 0
    for item in sonic_list_projects():
        name = item.get("projectName") or item.get("name") or ""
        if name == target:
            return safe_int(item.get("id"), 0)
    return 0


def sonic_suite_id_for_app(app):
    return safe_int((app or {}).get("sonic_suite_id") or (app or {}).get("sonicSuiteId"), 0)


def sonic_suite_name_for_app(app):
    return str((app or {}).get("sonic_suite_name") or (app or {}).get("sonicSuiteName") or "").strip()


def sonic_ensure_module(project_id, module_name):
    module_name = (module_name or "默认模块").strip()
    modules = sonic_list_modules(project_id)
    for item in modules:
        if item.get("name") == module_name:
            return safe_int(item.get("id"), 0)
    sonic_request("PUT", "/modules", body={"projectId": project_id, "name": module_name})
    modules = sonic_list_modules(project_id)
    for item in modules:
        if item.get("name") == module_name:
            return safe_int(item.get("id"), 0)
    raise RuntimeError(f"Sonic 模块创建后未找到：{module_name}")


def sonic_sync_case_to_configured_suite(app, project_id, saved_case):
    """Add a published managed case to the app's explicitly bound Sonic suite."""
    suite_id = sonic_suite_id_for_app(app)
    if not suite_id:
        return {
            "state": "not_configured",
            "label": "应用未绑定 Sonic 测试套，已仅同步用例",
            "suite_id": 0,
            "suite_name": sonic_suite_name_for_app(app),
            "case_count": 0,
        }
    detail = sonic_response_data(sonic_request("GET", "/testSuites", params={"id": suite_id}, timeout=15)) or {}
    if not isinstance(detail, dict) or not detail:
        raise RuntimeError(f"Sonic 测试套不存在：{suite_id}")
    suite_project_id = safe_int(detail.get("projectId"), 0)
    if suite_project_id and suite_project_id != safe_int(project_id, 0):
        raise RuntimeError(f"Sonic 测试套 {suite_id} 不属于当前应用项目，请重新绑定")
    cases = [item for item in (detail.get("testCases") or []) if isinstance(item, dict)]
    sonic_case_id = safe_int((saved_case or {}).get("id"), 0)
    already_linked = any(safe_int(item.get("id"), 0) == sonic_case_id for item in cases)
    if not already_linked:
        updated = dict(detail)
        updated["testCases"] = cases + [saved_case]
        updated["devices"] = detail.get("devices") or []
        sonic_response_data(sonic_request("PUT", "/testSuites", body=updated, timeout=20))
        cases = updated["testCases"]
    return {
        "state": "linked" if not already_linked else "already_linked",
        "label": "已加入 Sonic 测试套" if not already_linked else "已在 Sonic 测试套内",
        "suite_id": suite_id,
        "suite_name": detail.get("name") or sonic_suite_name_for_app(app),
        "case_count": len(cases),
    }


def sonic_case_marker(case_id, module, file, task_name):
    return "\n".join([
        "[MidsceneSync]",
        f"case_id={case_id}",
        f"module={module}",
        f"file={clean_filename(file)}",
        f"task={task_name}",
    ])


def sonic_managed_case(case_obj, case_id=""):
    des = str((case_obj or {}).get("des") or "")
    return "[MidsceneSync]" in des and (not case_id or f"case_id={case_id}" in des)


def sonic_case_marker_info(case_obj):
    des = str((case_obj or {}).get("des") or "")
    info = {}
    if "[MidsceneSync]" not in des:
        return info
    for line in des.splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key:
            info[key] = value
    return info


def sonic_midscene_step(step, case_id=""):
    if (step or {}).get("stepType") != "runScript":
        return False
    content = str((step or {}).get("content") or "")
    lower = content.lower()
    markers = (
        "midscene sonic bridge",
        "taskserver",
        "taskmodule",
        "taskname",
        "midscenecaseid",
        "/api/sonic/case",
        "/api/sonic/bridge-groovy",
        "midscene \"",
        "midscene '",
        "midscene \\\"",
    )
    legacy_feishu_markers = (
        "midscene 自动化测试报告",
        "sendfeishu",
        "feishu_payload",
        "open.feishu.cn/open-apis/bot",
    )
    if case_id and f"case_id: {case_id}" in content:
        return True
    return any(marker in lower for marker in markers) or any(marker in lower for marker in legacy_feishu_markers)


def sonic_case_has_midscene_step(sonic_case_id):
    try:
        return any(sonic_midscene_step(step) for step in sonic_list_steps(sonic_case_id))
    except Exception:
        return False


def sonic_bridge_step(step):
    content = str((step or {}).get("content") or "")
    return "Midscene Sonic Bridge" in content or "/api/sonic/bridge-groovy" in content


def sonic_step_state(steps, case_id=""):
    midscene_steps = [step for step in (steps or []) if sonic_midscene_step(step, case_id)]
    if not midscene_steps:
        return {
            "state": "missing",
            "label": "未发现 Midscene 脚本步骤",
            "step_id": 0,
            "sort": 0,
            "step_ids": [],
            "step_count": 0,
            "bridge_count": 0,
            "legacy_count": 0,
        }
    bridge_steps = [step for step in midscene_steps if sonic_bridge_step(step)]
    legacy_steps = [step for step in midscene_steps if not sonic_bridge_step(step)]
    step = bridge_steps[0] if bridge_steps else midscene_steps[0]
    step_ids = [safe_int(item.get("id"), 0) for item in midscene_steps if safe_int(item.get("id"), 0)]
    if len(midscene_steps) > 1:
        if bridge_steps and legacy_steps:
            label = f"新桥接与旧模板并存（{len(midscene_steps)} 个步骤），需重新同步清理"
        else:
            label = f"重复 Midscene 步骤（{len(midscene_steps)} 个），需重新同步清理"
        state = "mixed"
    else:
        state = "bridge" if bridge_steps else "legacy"
        label = "新桥接脚本" if bridge_steps else "旧模板脚本"
    return {
        "state": state,
        "label": label,
        "step_id": safe_int(step.get("id"), 0),
        "sort": safe_int(step.get("sort"), 0),
        "step_ids": step_ids,
        "step_count": len(midscene_steps),
        "bridge_count": len(bridge_steps),
        "legacy_count": len(legacy_steps),
    }


def sonic_find_case(project_id, platform, case_name, case_id=""):
    if case_id:
        # The YAML task name can be renamed after it has been published to Sonic.
        # Always prefer the stable marker so existing Sonic suites keep pointing
        # to the same case id instead of creating a duplicate case with the new name.
        for item in sonic_list_cases(project_id, platform=platform, name=""):
            if sonic_managed_case(item, case_id):
                return item
    cases = sonic_list_cases(project_id, platform=platform, name=case_name)
    exact = [item for item in cases if item.get("name") == case_name]
    return exact[0] if exact else None


def sonic_bridge_step_script(case_id):
    task_server = os.getenv("MIDSCENE_PUBLIC_BASE_URL") or os.getenv("TASK_PUBLIC_BASE_URL") or "http://101.34.197.12:8088"
    task_server = task_server.rstrip("/")
    escaped_case_id = str(case_id or "").replace("\\", "\\\\").replace('"', '\\"')
    escaped_server = task_server.replace("\\", "\\\\").replace('"', '\\"')
    return f'''// Midscene Sonic Bridge - managed by Task Platform
// case_id: {escaped_case_id}
def midsceneCaseId = "{escaped_case_id}"
def taskServer = "{escaped_server}"
def runnerToken = "midscene2026"
def bridgeUrl = taskServer + "/api/sonic/bridge-groovy?case_id=" + java.net.URLEncoder.encode(midsceneCaseId, "UTF-8")
def conn = new URL(bridgeUrl).openConnection()
conn.setRequestProperty("x-token", runnerToken)
conn.setConnectTimeout(15000)
conn.setReadTimeout(30000)
def bridgeCode = conn.inputStream.getText("UTF-8")
binding.setVariable("midsceneCaseId", midsceneCaseId)
binding.setVariable("taskServer", taskServer)
binding.setVariable("runnerToken", runnerToken)
evaluate(bridgeCode)
'''


def sonic_upsert_bridge_step(project_id, platform, sonic_case_id, case_id):
    steps = sonic_list_steps(sonic_case_id)
    bridge_content = sonic_bridge_step_script(case_id)
    midscene_steps = [item for item in steps if sonic_midscene_step(item, case_id)]
    bridge_steps = [item for item in midscene_steps if sonic_bridge_step(item)]
    target = bridge_steps[0] if bridge_steps else (midscene_steps[0] if midscene_steps else None)
    max_sort = 0
    for item in steps:
        max_sort = max(max_sort, safe_int(item.get("sort"), 0))
    payload = {
        "id": target.get("id") if target else None,
        "caseId": sonic_case_id,
        "parentId": 0,
        "projectId": project_id,
        "platform": platform,
        "stepType": "runScript",
        "text": "Groovy",
        "content": bridge_content,
        "sort": safe_int(target.get("sort"), 1) if target else max_sort + 1,
        "error": 3,
        "conditionType": 0,
        "disabled": 0,
        "elements": []
    }
    sonic_response_data(sonic_request("PUT", "/steps", body=payload))
    kept_step_id = safe_int(target.get("id"), 0) if target else 0
    removed_step_ids = []
    for item in midscene_steps:
        step_id = safe_int(item.get("id"), 0)
        if not step_id or step_id == kept_step_id:
            continue
        sonic_response_data(sonic_request("DELETE", "/steps", params={"id": step_id}))
        removed_step_ids.append(step_id)
    verified_state = {}
    for attempt in range(3):
        verified_state = sonic_step_state(sonic_list_steps(sonic_case_id), case_id)
        if verified_state.get("state") == "bridge" and verified_state.get("step_count") == 1:
            break
        if attempt < 2:
            time.sleep(0.2)
    if verified_state.get("state") != "bridge" or verified_state.get("step_count") != 1:
        raise RuntimeError(
            "Sonic 步骤同步后复检未通过：仍存在旧模板或重复 Midscene 步骤，"
            "已停止标记为同步成功，请重新执行清理检查"
        )
    payload["removed_step_ids"] = removed_step_ids
    payload["cleaned_duplicate_steps"] = len(removed_step_ids)
    payload["verified_state"] = verified_state.get("state")
    payload["verified_step_count"] = verified_state.get("step_count", 0)
    return payload


def sonic_upsert_case(case_info, force=False):
    app = task_app_map_by_package().get(case_info.get("app_package") or "") or {}
    project_id = sonic_find_project_id(app)
    if not project_id:
        raise ValueError(f"应用「{app.get('name') or case_info.get('app_package') or '未绑定'}」未绑定 Sonic 项目 ID/名称")
    platform = 1 if case_info.get("platform", "android") == "android" else 2
    module_id = sonic_ensure_module(project_id, case_info.get("module") or "默认模块")
    case_name = case_info.get("task_name") or re.sub(r"\.(yaml|yml)$", "", case_info.get("file", ""), flags=re.I)
    existing = sonic_find_case(project_id, platform, case_name, case_info.get("case_id"))
    existing_id = safe_int(existing.get("id"), 0) if existing else 0
    legacy_midscene = bool(existing_id and sonic_case_has_midscene_step(existing_id))
    if existing and not sonic_managed_case(existing, case_info.get("case_id")) and not legacy_midscene and not force:
        raise RuntimeError(f"Sonic 已存在同名非 Midscene 托管用例「{case_name}」，为避免覆盖请先改名或勾选 force")
    body = {
        "id": existing_id if existing else None,
        "name": case_name,
        "platform": platform,
        "projectId": project_id,
        "moduleId": module_id,
        "version": case_info.get("version") or "Midscene",
        "des": sonic_case_marker(case_info.get("case_id"), case_info.get("module"), case_info.get("file"), case_info.get("task_name")),
    }
    sonic_request("PUT", "/testCases", body=body)
    saved = sonic_find_case(project_id, platform, case_name, case_info.get("case_id"))
    if not saved:
        raise RuntimeError("Sonic 用例保存后未能查回，请检查 Sonic 接口返回")
    sonic_case_id = safe_int(saved.get("id"), 0)
    step_payload = sonic_upsert_bridge_step(project_id, platform, sonic_case_id, case_info.get("case_id"))
    suite_sync = sonic_sync_case_to_configured_suite(app, project_id, saved)
    return {
        "project_id": project_id,
        "project_name": sonic_project_name_for_app(app),
        "module_id": module_id,
        "sonic_case_id": sonic_case_id,
        "sonic_case_name": case_name,
        "sonic_suite_id": app.get("sonic_suite_id") or app.get("sonicSuiteId") or "",
        "sonic_suite_name": app.get("sonic_suite_name") or app.get("sonicSuiteName") or "",
        "app_package": app.get("package") or case_info.get("app_package") or "",
        "app_name": app.get("name") or case_info.get("app_name") or "",
        "suite_sync": suite_sync,
        "step_sort": step_payload.get("sort"),
        "removed_step_ids": step_payload.get("removed_step_ids", []),
        "cleaned_duplicate_steps": step_payload.get("cleaned_duplicate_steps", 0),
        "verified_state": step_payload.get("verified_state", ""),
        "verified_step_count": step_payload.get("verified_step_count", 0),
        "legacy_midscene_migrated": legacy_midscene,
    }


def sonic_case_indexes(module_filter="", file_filter=""):
    cases = list_task_case_assets(module_filter, file_filter)
    by_id = {}
    by_app_name = {}
    for case in cases:
        if case.get("error"):
            continue
        by_id[case.get("case_id")] = case
        key = (case.get("app_package") or "", case.get("task_name") or "")
        by_app_name.setdefault(key, []).append(case)
    return cases, by_id, by_app_name


def sonic_match_task_case(sonic_case, app_package, by_id, by_app_name):
    marker = sonic_case_marker_info(sonic_case)
    marker_case_id = marker.get("case_id") or marker.get("caseId")
    if marker_case_id and marker_case_id in by_id:
        return by_id[marker_case_id], "case_id"
    name = sonic_case.get("name") or ""
    candidates = by_app_name.get((app_package or "", name), [])
    if len(candidates) == 1:
        return candidates[0], "name"
    if len(candidates) > 1:
        return None, "ambiguous"
    return None, "none"


def sonic_project_apps(app_package_filter=""):
    apps = []
    for app in sonic_notify_known_apps():
        if app_package_filter and app.get("package") != app_package_filter:
            continue
        project_id = sonic_find_project_id(app)
        row = dict(app)
        row["sonic_project_id_resolved"] = project_id
        apps.append(row)
    return apps


def sonic_live_case_status(case_info):
    app = task_app_map_by_package().get(case_info.get("app_package") or "") or {}
    project_id = sonic_find_project_id(app)
    row = {
        **case_info,
        "sonic_project_id": project_id,
        "sonic_project_name": sonic_project_name_for_app(app),
        "sonic_found": False,
        "sonic_case_id": 0,
        "sonic_case_name": "",
        "step_state": "project_missing" if not project_id else "missing",
        "step_label": "应用未绑定 Sonic 项目" if not project_id else "未同步",
        "sync": load_sonic_sync().get("cases", {}).get(case_info.get("case_id"), {}),
    }
    if not project_id:
        return row
    case_name = case_info.get("task_name") or ""
    existing = sonic_find_case(project_id, 1 if case_info.get("platform") == "android" else 2, case_name, case_info.get("case_id"))
    if not existing:
        row["step_state"] = "not_published"
        row["step_label"] = "Sonic 未找到用例"
        return row
    steps = sonic_list_steps(safe_int(existing.get("id"), 0))
    state = sonic_step_state(steps, case_info.get("case_id"))
    row.update({
        "sonic_found": True,
        "sonic_case_id": safe_int(existing.get("id"), 0),
        "sonic_case_name": existing.get("name") or "",
        "step_state": state["state"],
        "step_label": state["label"],
        "step_id": state["step_id"],
        "step_sort": state["sort"],
        "step_count": state.get("step_count", 0),
        "bridge_count": state.get("bridge_count", 0),
        "legacy_count": state.get("legacy_count", 0),
    })
    return row


def sonic_scan_midscene_cases(app_package="", module="", file="", include_current=False):
    cases, by_id, by_app_name = sonic_case_indexes(module, file)
    app_packages = {case.get("app_package") for case in cases if case.get("app_package")}
    app_filter = app_package or ""
    if module and not app_filter:
        app_filter = app_package_for_module(module)
    rows = []
    for app in sonic_project_apps(app_filter):
        app_package_row = app.get("package") or ""
        if app_packages and app_package_row not in app_packages and (module or file):
            continue
        project_id = app.get("sonic_project_id_resolved") or 0
        if not project_id:
            rows.append({
                "app_package": app_package_row,
                "app_name": app.get("name") or app_package_row,
                "project_id": 0,
                "project_name": sonic_project_name_for_app(app),
                "status": "project_missing",
                "reason": "应用未绑定 Sonic 项目",
                "matched_case": None,
            })
            continue
        for sonic_case in sonic_list_cases(project_id, platform=1, name=""):
            sonic_case_id = safe_int(sonic_case.get("id"), 0)
            steps = sonic_list_steps(sonic_case_id)
            state = sonic_step_state(steps)
            if state["state"] == "missing":
                continue
            if state["state"] == "bridge" and not include_current:
                continue
            matched, match_type = sonic_match_task_case(sonic_case, app_package_row, by_id, by_app_name)
            action = "skip"
            reason = "新桥接脚本，无需迁移" if state["state"] == "bridge" else "旧模板脚本"
            if state["state"] in ("legacy", "mixed"):
                if matched:
                    action = "migrate"
                    reason = f"可按 {match_type} 匹配到 Task 用例并清理旧步骤" if state["state"] == "mixed" else f"可按 {match_type} 匹配到 Task 用例"
                elif match_type == "ambiguous":
                    action = "manual"
                    reason = "同名 Task 用例不唯一，需要人工确认"
                else:
                    action = "manual"
                    reason = "未匹配到 Task 平台用例，请先对齐名称或重新同步"
            rows.append({
                "app_package": app_package_row,
                "app_name": app.get("name") or app_package_row,
                "project_id": project_id,
                "project_name": sonic_project_name_for_app(app),
                "sonic_case_id": sonic_case_id,
                "sonic_case_name": sonic_case.get("name") or "",
                "step_id": state.get("step_id", 0),
                "step_sort": state.get("sort", 0),
                "step_count": state.get("step_count", 0),
                "bridge_count": state.get("bridge_count", 0),
                "legacy_count": state.get("legacy_count", 0),
                "step_state": state["state"],
                "step_label": state["label"],
                "action": action,
                "reason": reason,
                "match_type": match_type,
                "matched_case": matched,
            })
    return rows


def sonic_migrate_midscene_cases(data):
    app_package = data.get("app_package") or data.get("appPackage") or ""
    module = data.get("module", "")
    file = clean_filename(data.get("file", "")) if data.get("file") else ""
    dry_run = safe_bool(data.get("dryRun", data.get("dry_run")))
    rows = sonic_scan_midscene_cases(app_package=app_package, module=module, file=file, include_current=False)
    results = []
    with SONIC_LOCK:
        sync = load_sonic_sync()
        sync_cases = sync.setdefault("cases", {})
        for row in rows:
            matched = row.get("matched_case") or {}
            if row.get("action") != "migrate" or not matched:
                results.append({**row, "migrated": False})
                continue
            record = {
                **matched,
                "sync_requested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "dry_run": dry_run,
                "legacy_sonic_case_id": row.get("sonic_case_id"),
                "legacy_step_id": row.get("step_id"),
            }
            if dry_run:
                record.update({
                    "status": "ready",
                    "message": "dry-run：将收敛为唯一桥接步骤并清理旧/重复 Midscene 步骤",
                    "bridge_step_preview": sonic_bridge_step_script(matched["case_id"])[:1200],
                })
                results.append({**row, "migrated": False, "publish": record})
            else:
                publish_result = sonic_upsert_case(matched, force=False)
                record.update({
                    "status": "published",
                    "published_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    **publish_result,
                })
                sync_cases[matched["case_id"]] = record
                results.append({**row, "migrated": True, "publish": record})
        if not dry_run:
            save_sonic_sync(sync)
    return {
        "ok": True,
        "dryRun": dry_run,
        "total": len(rows),
        "migratable": len([row for row in rows if row.get("action") == "migrate"]),
        "migrated": len([row for row in results if row.get("migrated")]),
        "manual": len([row for row in rows if row.get("action") == "manual"]),
        "results": results,
    }


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


def runner_summary():
    runners = load_runners()
    now = time.time()
    online = 0
    devices = []
    for runner_id, runner in runners.items():
        is_online = now - runner.get("last_seen_ts", 0) <= 45
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
        "online_devices": len([d for d in devices if d.get("runner_online") and d.get("status") in ("online", "device")]),
    }


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
    bridge_exists = bool(read_text(bridge_path, "") or read_text(os.path.join(os.getcwd(), "sonic-midscene-task-runner.groovy"), ""))
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


def sonic_publish_precheck(data):
    mod = data.get("module", "")
    raw_file = data.get("file", "")
    file = clean_filename(raw_file) if raw_file else ""
    task_name = data.get("taskName") or data.get("task_name") or ""
    blockers = []
    warnings = []
    fixes = []
    cases = []
    if not mod or not file:
        blockers.append("module 和 file 不能为空")
        return {"ok": False, "canPublish": False, "blockers": blockers, "warnings": warnings, "fixes": fixes, "cases": cases}
    try:
        fpath = safe_join(TASK_DIR, mod, file)
    except ValueError:
        blockers.append("非法路径")
        return {"ok": False, "canPublish": False, "blockers": blockers, "warnings": warnings, "fixes": fixes, "cases": cases}
    if not os.path.exists(fpath):
        blockers.append("YAML 文件不存在")
        return {"ok": False, "canPublish": False, "blockers": blockers, "warnings": warnings, "fixes": fixes, "cases": cases}
    yaml_text_value = read_text_file(fpath)
    if not yaml_text_value.strip():
        blockers.append("YAML 内容为空")
    if "tasks:" not in yaml_text_value:
        blockers.append("YAML 缺少 tasks")
    yaml_check = validate_midscene_yaml(yaml_text_value)
    if not yaml_check.get("ok"):
        blockers.extend(yaml_check.get("warnings", [])[:5])
    else:
        warnings.extend(yaml_check.get("warnings", [])[:3])
    meta = load_task_meta().get(task_key(mod, file), {}) or {}
    status = meta.get("status") or "draft"
    if status not in ("active", "baseline"):
        blockers.append(f"当前状态是「{status}」，请先标记为已入库或基线")
    app_package = resolve_app_package(mod, file, yaml_text_value, allow_default=False)
    if not app_package:
        blockers.append("未识别到 APP 包名，请先绑定模块应用或在 YAML 中包含 launch/force-stop 包名")
    app = task_app_map_by_package().get(app_package or "") or {}
    project_id = sonic_find_project_id(app) if app else 0
    suite_binding = {}
    if not project_id:
        blockers.append(f"应用「{app.get('name') or app_package or mod}」未绑定 Sonic 项目")
    elif sonic_suite_id_for_app(app):
        try:
            suite_id = sonic_suite_id_for_app(app)
            suite = sonic_response_data(sonic_request("GET", "/testSuites", params={"id": suite_id}, timeout=15)) or {}
            if not isinstance(suite, dict) or not suite:
                blockers.append(f"绑定的 Sonic 测试套不存在：{suite_id}")
            elif safe_int(suite.get("projectId"), 0) not in (0, project_id):
                blockers.append(f"绑定的 Sonic 测试套 {suite_id} 不属于应用项目")
            else:
                suite_binding = sonic_suite_definition_meta_from_dto(suite, "/testSuites?id")
                suite_binding["project_id"] = project_id
        except Exception as e:
            blockers.append(f"Sonic 测试套校验失败：{e}")
    else:
        warnings.append("当前应用未绑定 Sonic 测试套；同步后需要在 Sonic 中手动加入测试套，或先在配置页绑定测试套")
    try:
        all_cases = list_task_case_assets(mod, file)
        if task_name:
            all_cases = [item for item in all_cases if item.get("task_name") == task_name]
        if not all_cases:
            blockers.append("没有解析到可同步的 tasks[].name")
        for case in all_cases:
            if not case.get("case_id"):
                warnings.append(f"用例「{case.get('task_name')}」缺少 case_id，同步时会自动固化")
                fixes.append("自动写入 baseline.case_id")
        cases = all_cases
    except Exception as e:
        blockers.append(str(e))
    sonic_rows = []
    if project_id and cases:
        try:
            sonic_rows = [sonic_live_case_status(case) for case in cases if not case.get("error")]
            legacy = [row for row in sonic_rows if row.get("step_state") == "legacy"]
            if legacy:
                warnings.append(f"Sonic 中有 {len(legacy)} 条旧模板，同步会自动替换为桥接脚本")
            mixed = [row for row in sonic_rows if row.get("step_state") == "mixed"]
            if mixed:
                warnings.append(f"Sonic 中有 {len(mixed)} 条新旧脚本并存，同步会自动保留桥接并清理重复旧步骤")
        except Exception as e:
            warnings.append(f"Sonic 状态读取失败：{e}")
    return {
        "ok": True,
        "canPublish": not blockers,
        "blockers": blockers,
        "warnings": dedupe_keep_order(warnings),
        "fixes": dedupe_keep_order(fixes),
        "status": status,
        "app_package": app_package,
        "app_name": app.get("name") or app_package,
        "project_id": project_id,
        "suite": suite_binding,
        "cases": cases,
        "sonic": sonic_rows,
        "yamlCheck": yaml_check,
    }


def sonic_publish_yaml(data):
    mod = data.get("module", "")
    raw_file = data.get("file", "")
    file = clean_filename(raw_file) if raw_file else ""
    task_name = data.get("taskName") or data.get("task_name") or ""
    case_id = data.get("case_id") or data.get("caseId") or ""
    dry_run = safe_bool(data.get("dryRun", data.get("dry_run")))
    force = safe_bool(data.get("force"))
    if not mod or not file:
        return {"ok": False, "error": "module 和 file 不能为空", "results": []}

    precheck = sonic_publish_precheck({"module": mod, "file": file, "taskName": task_name})
    if not precheck.get("canPublish") and not force:
        return {
            "ok": False,
            "error": "同步前检查未通过",
            "precheck": precheck,
            "results": []
        }
    if not precheck.get("canPublish") and force:
        hard_blockers = [
            item for item in (precheck.get("blockers") or [])
            if not str(item).startswith("当前状态是")
        ]
        if hard_blockers:
            return {
                "ok": False,
                "error": "同步前检查存在硬阻断项",
                "precheck": precheck,
                "results": []
            }

    _, case_id_changes = ensure_yaml_case_ids(mod, file)
    cases = list_task_case_assets(mod, file)
    if task_name:
        cases = [item for item in cases if item.get("task_name") == task_name]
    if case_id:
        cases = [item for item in cases if item.get("case_id") == case_id]
    if not cases:
        return {"ok": False, "error": "未找到可同步的 YAML 用例", "results": []}

    results = []
    with SONIC_LOCK:
        sync = load_sonic_sync()
        sync_cases = sync.setdefault("cases", {})
        for case in cases:
            record = {
                **case,
                "sync_requested_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "dry_run": dry_run,
            }
            if dry_run:
                record.update({
                    "status": "ready",
                    "message": "dry-run：已生成 Sonic 桥接脚本，未调用 Sonic 接口",
                    "bridge_step_preview": sonic_bridge_step_script(case["case_id"])[:1200]
                })
            else:
                publish_result = sonic_upsert_case(case, force=force)
                record.update({
                    "status": "published",
                    "published_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    **publish_result
                })
            sync_cases[case["case_id"]] = record
            results.append(record)
        save_sonic_sync(sync)
    return {"ok": True, "results": results, "caseIdChanges": case_id_changes, "precheck": precheck}


def sonic_publish_batch(data):
    mod = data.get("module", "")
    files = data.get("files") or []
    force = safe_bool(data.get("force"))
    dry_run = safe_bool(data.get("dryRun", data.get("dry_run")))
    if not mod:
        return {"ok": False, "error": "module 不能为空", "results": []}
    if not files:
        files = [
            f for f in sorted(os.listdir(safe_join(TASK_DIR, mod)))
            if f.endswith((".yaml", ".yml"))
        ] if os.path.isdir(safe_join(TASK_DIR, mod)) else []
    results = []
    total_cases = 0
    for file in files:
        row = {"module": mod, "file": file, "status": "pending", "message": ""}
        try:
            result = sonic_publish_yaml({"module": mod, "file": file, "force": force, "dryRun": dry_run})
            row["status"] = "success" if result.get("ok") else "failed"
            row["message"] = result.get("error") or f"同步 {len(result.get('results') or [])} 条"
            row["case_count"] = len(result.get("results") or [])
            row["precheck"] = result.get("precheck")
            total_cases += row["case_count"]
        except Exception as e:
            row["status"] = "failed"
            row["message"] = str(e)
        results.append(row)
    failed = [item for item in results if item.get("status") == "failed"]
    return {
        "ok": True,
        "module": mod,
        "total_files": len(results),
        "total_cases": total_cases,
        "failed": len(failed),
        "results": results,
    }


def load_baseline_refs():
    data = read_json_file(BASELINE_REFS_FILE, default={})
    return data if isinstance(data, dict) else {}


def save_baseline_refs(data):
    write_json_file(BASELINE_REFS_FILE, data)


def baseline_ref_key(app_package, module, file, task_name=""):
    return "::".join([
        app_package or app_package_for_module(module) or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE),
        module or "",
        clean_filename(file or ""),
        task_name or ""
    ])


def get_baseline_ref_page_ids(app_package, module, file, task_name=""):
    refs = load_baseline_refs()
    app_package = app_package or app_package_for_module(module) or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    page_ids = []
    for key in (
        baseline_ref_key(app_package, module, file, ""),
        baseline_ref_key(app_package, module, file, task_name)
    ):
        row = refs.get(key) or {}
        for page_id in row.get("page_ids") or []:
            if page_id and page_id not in page_ids:
                page_ids.append(page_id)
    return page_ids


def set_baseline_ref_page_ids(app_package, module, file, task_name, page_ids):
    refs = load_baseline_refs()
    app_package = app_package or app_package_for_module(module) or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    key = baseline_ref_key(app_package, module, file, task_name)
    refs[key] = {
        "app_package": app_package or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE),
        "module": module,
        "file": clean_filename(file),
        "task_name": task_name or "",
        "page_ids": [str(item) for item in (page_ids or []) if str(item).strip()],
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    save_baseline_refs(refs)
    return refs[key]


def normalize_job_status(status):
    status = (status or "failed").strip().lower()
    if status in ("passed", "pass", "ok"):
        return "success"
    if status in ("cancel", "canceled"):
        return "cancelled"
    if status in ("pending", "running", "success", "failed", "cancelled"):
        return status
    return "failed"


def parse_time(value):
    if not value:
        return 0
    try:
        return time.mktime(time.strptime(value, "%Y-%m-%d %H:%M:%S"))
    except Exception:
        return 0


def safe_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


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


def safe_bool(value, default=False):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on", "是", "开启"):
        return True
    if text in ("0", "false", "no", "n", "off", "否", "关闭"):
        return False
    return default


def automatic_baseline_repair_enabled(requested):
    return bool(ENABLE_AUTOMATIC_BASELINE_REPAIR and safe_bool(requested))


def recover_timed_out_jobs():
    now = time.time()
    changed = False
    with JOB_LOCK:
        jobs = load_jobs()
        for job in jobs:
            if job.get("status") != "running":
                continue
            started = parse_time(job.get("started_at")) or parse_time(job.get("created_at"))
            if started and now - started > JOB_TIMEOUT_SECONDS:
                job["status"] = "failed"
                job["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                job["timeout_recovered"] = True
                job["stderr_tail"] = f"任务执行超过 {JOB_TIMEOUT_SECONDS} 秒未回传结果，已自动回收"
                changed = True
        if changed:
            save_jobs(jobs)


def find_job(job_id):
    jobs = load_jobs()
    for job in jobs:
        if job.get("job_id") == job_id:
            return job, jobs
    return None, jobs


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


def resolve_task_app_sonic_binding(app):
    """Resolve user-friendly Sonic names to stable ids and reject cross-project suite binding."""
    app = dict(app or {})
    if not (app.get("sonic_project_id") or app.get("sonic_project_name")):
        return app
    project_id = sonic_find_project_id(app)
    if not project_id:
        raise ValueError(f"未在 Sonic 找到项目「{app.get('sonic_project_name') or app.get('sonic_project_id')}」")
    app["sonic_project_id"] = str(project_id)
    for project in sonic_list_projects():
        if safe_int(project.get("id"), 0) == project_id:
            app["sonic_project_name"] = project.get("projectName") or project.get("name") or app.get("sonic_project_name", "")
            break
    suite_id = sonic_suite_id_for_app(app)
    suite_name = sonic_suite_name_for_app(app)
    suites = []
    if suite_id:
        detail = sonic_response_data(sonic_request("GET", "/testSuites", params={"id": suite_id}, timeout=15)) or {}
        if not isinstance(detail, dict) or not detail:
            raise ValueError(f"未在 Sonic 找到测试套 ID：{suite_id}")
        suites = [detail]
    elif suite_name:
        data = sonic_response_data(sonic_request("GET", "/testSuites/listAll", params={"projectId": project_id}, timeout=15)) or []
        rows = data if isinstance(data, list) else extract_page_items(data)
        exact_name = re.sub(r"\s+", "", suite_name)
        suites = [
            row for row in rows if isinstance(row, dict)
            and re.sub(r"\s+", "", str(row.get("name") or "")) == exact_name
        ]
        if not suites:
            raise ValueError(f"项目「{app.get('sonic_project_name')}」下未找到测试套「{suite_name}」")
        if len(suites) > 1:
            raise ValueError(f"项目内存在多个同名测试套「{suite_name}」，请填写测试套 ID")
    if suites:
        suite = suites[0]
        if safe_int(suite.get("projectId"), 0) not in (0, project_id):
            raise ValueError("Sonic 测试套不属于当前应用绑定的项目")
        app["sonic_suite_id"] = str(safe_int(suite.get("id"), 0))
        app["sonic_suite_name"] = suite.get("name") or suite_name
        app["sonic_suite_case_count"] = sonic_count_suite_cases(suite)
    return app


def env_key_for_package(prefix, package):
    return prefix + re.sub(r"[^A-Z0-9]", "_", (package or "").upper())


def validate_feishu_webhook(webhook):
    value = str(webhook or "").strip()
    if not value:
        return ""
    if any(marker in value for marker in ("\r", "\n", "\t", "export ", "export\t")):
        raise ValueError("飞书 Webhook 配置异常：只能填写单行机器人地址，不能包含换行或 export 配置")
    if value[:1] in "\"'“”‘’" or value[-1:] in "\"'“”‘’":
        raise ValueError("飞书 Webhook 配置异常：请去掉地址外层引号，尤其不要使用中文引号")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("飞书 Webhook 配置异常：请填写完整的 http/https 机器人地址")
    return value


def default_feishu_webhook_for_package(package):
    return (
        os.getenv(env_key_for_package("FEISHU_WEBHOOK_", package))
        or os.getenv("FEISHU_WEBHOOK_DEFAULT", "")
        or ""
    )


def task_app_feishu_webhook(app):
    if not app:
        return validate_feishu_webhook(os.getenv("FEISHU_WEBHOOK_DEFAULT", ""))
    return validate_feishu_webhook(
        app.get("feishu_webhook")
        or app.get("feishuWebhook")
        or default_feishu_webhook_for_package(app.get("package", ""))
        or ""
    )


def sonic_notify_compact(text, limit=220):
    text = sonic_notify_clean_text(text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "..."


def sonic_notify_display_value(value, default=""):
    text = re.sub(r"\s+", " ", sonic_notify_clean_text(value)).strip()
    if not text or re.fullmatch(r"[#$]\{[^{}]+\}", text):
        return default
    return text


def sonic_notify_pretty_title_text(value):
    text = sonic_notify_display_value(value)
    text = re.sub(r"(?i)3D\s*UI", "3D UI", text)
    text = re.sub(r"(?i)UI\s*3D", "UI 3D", text)
    return text


MOJIBAKE_MARKERS = (
    "锛", "涓", "褰", "妗", "鐣", "杈", "閬", "鎵", "鏍", "淇", "缃", "绔",
    "锟", "斤拷", "烫烫", "屯屯", "�",
)


def sonic_text_score(text):
    text = str(text or "")
    if not text:
        return 0
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    ascii_letters = len(re.findall(r"[A-Za-z0-9]", text))
    common = len(re.findall(r"[的一是在有和不为页面按钮点击任务提示根据内容出现失败成功执行用例报告模块]", text))
    bad = text.count("�") * 8
    bad += sum(text.count(marker) * 10 for marker in MOJIBAKE_MARKERS if marker != "�")
    return cjk * 2 + common * 4 + ascii_letters - bad


def sonic_text_looks_mojibake(text):
    text = str(text or "")
    if not text:
        return False
    if any(marker in text for marker in MOJIBAKE_MARKERS):
        return True
    cjk = len(re.findall(r"[\u4e00-\u9fff]", text))
    if cjk > 20 and len(re.findall(r"[的一是在有和不为页面按钮点击]", text)) < max(2, cjk // 18):
        return True
    return False


def sonic_recover_text_encoding(text):
    text = str(text or "")
    if not text or not sonic_text_looks_mojibake(text):
        return text
    candidates = [text]
    for source_encoding in ("gb18030", "gbk", "cp936", "latin1"):
        try:
            candidates.append(text.encode(source_encoding, errors="ignore").decode("utf-8", errors="replace"))
        except Exception:
            pass
    best = max(candidates, key=sonic_text_score)
    return best if sonic_text_score(best) > sonic_text_score(text) else text


def sonic_notify_clean_text(text, fallback="日志编码异常，请查看报告"):
    raw_text = str(text or "")
    if any(marker in raw_text for marker in ("锟", "斤拷", "烫烫", "屯屯")):
        return fallback
    text = sonic_recover_text_encoding(text)
    text = str(text or "")
    text = text.replace("\x00", " ")
    text = re.sub(r"[\u0001-\u0008\u000b\u000c\u000e-\u001f]+", " ", text)
    text = re.sub(r"[\u4e00-\u9fff]?\d�\d", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    if any(marker in text for marker in ("锟", "斤拷", "烫烫", "屯屯")):
        return fallback
    replacement_count = text.count("�")
    # Replacement characters indicate bytes were already lost before reaching
    # the Task service; presenting the partial recovery as a real failure
    # reason is misleading and produces unreadable Feishu cards.
    if replacement_count:
        return fallback
    if sonic_text_looks_mojibake(text) and sonic_text_score(text) < 20:
        return fallback
    return text


def builtin_task_apps():
    return [
        {
            "package": "com.kfb.model",
            "name": "3D 打印",
            "sonic_project_name": "3D 打印",
            "sonic_project_id": os.getenv("SONIC_PROJECT_ID_COM_KFB_MODEL", "3"),
            "aliases": ["3D测试自动", "3D打印基线", "3D打印基线回归"]
        },
        {
            "package": "com.xbxxhz.box",
            "name": "小白学习打印",
            "sonic_project_name": "小白学习打印",
            "aliases": ["小白学习", "小白学习基线", "小白学习打印基线"]
        },
    ]


def merge_task_app_defaults(app, defaults):
    merged = dict(defaults or {})
    merged.update(app or {})
    for key, value in (defaults or {}).items():
        if merged.get(key) in (None, ""):
            merged[key] = value
    if (app or {}).get("modules") is not None:
        merged["modules"] = app.get("modules") or []
    aliases = []
    for source in ((defaults or {}).get("aliases") or [], (app or {}).get("aliases") or []):
        for item in source:
            if item and item not in aliases:
                aliases.append(item)
    if aliases:
        merged["aliases"] = aliases
    return merged


def sonic_notify_known_apps():
    builtin_apps = builtin_task_apps()
    builtin_by_package = {
        (app.get("package") or "").strip(): app
        for app in builtin_apps
        if (app.get("package") or "").strip()
    }
    configured = load_task_apps().get("apps") or []
    apps = []
    seen = set()
    for app in configured:
        package = app.get("package", "")
        key = package or app.get("name", "")
        if key in seen:
            continue
        seen.add(key)
        apps.append(merge_task_app_defaults(app, builtin_by_package.get(package)))
    for app in builtin_apps:
        package = app.get("package", "")
        key = package or app.get("name", "")
        if key in seen:
            continue
        seen.add(key)
        apps.append(app)
    return apps


def post_feishu_card(webhook, card):
    webhook = validate_feishu_webhook(webhook)
    if not webhook:
        raise ValueError("未配置应用对应的飞书机器人 Webhook")
    data = json.dumps(card, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        webhook,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        return json.loads(raw) if raw else {"ok": True}


def sonic_suite_quiet_seconds():
    return max(20, env_int("SONIC_SUITE_SUMMARY_QUIET_SECONDS", 45))


def sonic_suite_max_wait_seconds():
    return max(60, env_int("SONIC_SUITE_MAX_WAIT_SECONDS", 600))


def sonic_suite_running_check_delay_seconds():
    return max(30, env_int("SONIC_SUITE_RUNNING_CHECK_DELAY_SECONDS", 30))


def sonic_suite_reopen_seconds():
    return max(300, env_int("SONIC_SUITE_REOPEN_SECONDS", 1800))


def sonic_suite_waits_for_completion_event(suite_or_job):
    return bool(
        SONIC_NOTIFY_ON_SUITE_COMPLETION_ONLY
        and (suite_or_job or {}).get("source", "sonic") == "sonic"
        and (suite_or_job or {}).get("run_mode", "baseline") == "baseline"
    )


def sonic_report_lookup_retries():
    return max(1, env_int("SONIC_REPORT_LOOKUP_RETRIES", 6))


def sonic_report_lookup_interval():
    return max(2, env_int("SONIC_REPORT_LOOKUP_INTERVAL_SECONDS", 5))


def sonic_midscene_report_grace_seconds():
    return max(0, env_int("MIDSCENE_REPORT_UPLOAD_GRACE_SECONDS", 30))


def sonic_midscene_report_check_delay_seconds():
    return max(1, env_int("MIDSCENE_REPORT_UPLOAD_CHECK_DELAY_SECONDS", 3))


def sonic_suite_pending_midscene_reports(suite):
    return len([
        item for item in ((suite or {}).get("results") or [])
        if safe_bool(item.get("report_upload_pending"))
    ])


def sonic_suite_can_wait_for_pending_midscene_reports(suite, now_ts=None):
    if not sonic_suite_pending_midscene_reports(suite):
        return False
    grace_seconds = sonic_midscene_report_grace_seconds()
    if grace_seconds <= 0:
        return False
    now_ts = now_ts or int(time.time())
    reference_ts = (
        safe_int((suite or {}).get("completion_ts"), 0)
        or safe_int((suite or {}).get("last_update_ts"), 0)
        or now_ts
    )
    return now_ts - reference_ts < grace_seconds


def sonic_report_window_before_seconds():
    return max(60, env_int("SONIC_REPORT_WINDOW_BEFORE_SECONDS", 900))


def sonic_report_window_after_seconds():
    return max(120, env_int("SONIC_REPORT_WINDOW_AFTER_SECONDS", 1800))


def sonic_suite_app_info(package="", module=""):
    package = (package or "").strip()
    try:
        for app in sonic_notify_known_apps():
            app_package = (app.get("package") or "").strip()
            if package and app_package == package:
                return app
            if module and module in (app.get("modules") or []):
                return app
    except Exception:
        pass
    for app in sonic_notify_known_apps():
        if package and app.get("package") == package:
            return app
    return {"package": package, "name": package or "Sonic"}


def sonic_suite_app_for_completion(event):
    package = str((event or {}).get("app_package") or "").strip()
    if package:
        return sonic_suite_app_info(package, "")
    project_id = safe_int((event or {}).get("project_id"), 0)
    suite_id = safe_int((event or {}).get("suite_id"), 0)
    suite_name = re.sub(r"\s+", "", str((event or {}).get("suite_name") or ""))
    for app in sonic_notify_known_apps():
        if project_id and sonic_project_id_for_app(app) == project_id:
            return app
        if suite_id and sonic_suite_id_for_app(app) == suite_id:
            return app
        configured_name = re.sub(r"\s+", "", sonic_suite_name_for_app(app))
        if suite_name and configured_name and (suite_name == configured_name or suite_name in configured_name or configured_name in suite_name):
            return app
    return sonic_suite_app_info(package, "")


def decode_sonic_callback_body(raw):
    if isinstance(raw, str):
        return raw
    raw = raw or b""
    for encoding in ("utf-8", "utf-8-sig", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin1", errors="replace")


def normalize_sonic_suite_status(raw_status="", passed=0, failed=0, warning=0, text=""):
    status_text = str(raw_status or "").strip().lower()
    full_text = f"{raw_status or ''}\n{text or ''}".lower()
    interrupted_markers = (
        "interrupted", "interrupt", "aborted", "abort", "cancelled", "canceled",
        "cancel", "stopped", "stop", "terminated", "terminate",
        "中断", "终止", "取消", "已停止", "停止", "手动停止", "强制停止",
    )
    if any(marker in full_text for marker in interrupted_markers):
        return "interrupted"
    if failed or status_text in ("failed", "fail", "失败"):
        return "failed"
    if warning or status_text in ("warning", "warn", "异常", "告警"):
        return "warning"
    if passed or status_text in ("success", "passed", "pass", "成功", "通过"):
        return "success"
    return "warning"


def sonic_suite_status_meta(status):
    if status == "failed":
        return {"text": "失败", "class": "fail", "color": "red", "icon": "❌"}
    if status == "interrupted":
        return {"text": "中断", "class": "warn", "color": "orange", "icon": "⏸️"}
    if status == "warning":
        return {"text": "告警", "class": "warn", "color": "orange", "icon": "⚠️"}
    return {"text": "通过", "class": "pass", "color": "green", "icon": "✅"}


def parse_sonic_suite_completion_payload(raw, content_type=""):
    text = decode_sonic_callback_body(raw).strip()
    data = {}
    if text.startswith("{"):
        try:
            loaded = json.loads(text)
            data = loaded if isinstance(loaded, dict) else {}
        except Exception:
            data = {}

    def value(*names):
        for name in names:
            if data.get(name) not in (None, ""):
                return data.get(name)
        return ""

    def match_text(*labels):
        for label in labels:
            match = re.search(rf"{re.escape(label)}\s*[：:]\s*([^\r\n]+)", text)
            if match:
                return match.group(1).strip()
        return ""

    report_url = str(value("sonicReportUrl", "sonic_report_url", "reportUrl", "report_url", "url") or "").strip()
    if not report_url:
        url_match = re.search(r"https?://[^\s\"'<>]+", text)
        report_url = url_match.group(0).rstrip("，,。)") if url_match else ""
    detail_match = re.search(r"/Home/(\d+)/ResultDetail/(\d+)", report_url)
    project_id = safe_int(value("projectId", "project_id"), 0)
    result_id = safe_int(value("resultId", "result_id"), 0)
    if detail_match:
        project_id = project_id or safe_int(detail_match.group(1), 0)
        result_id = result_id or safe_int(detail_match.group(2), 0)
    suite_name = sonic_notify_clean_text(
        value("suiteName", "suite_name", "name") or match_text("测试套件", "套件"),
        fallback=""
    )
    suite_name = re.sub(r"\s*(运行完毕|执行完毕|执行完成|运行完成)[！!。.]?\s*$", "", suite_name).strip()
    passed = safe_int(value("passed", "pass") or match_text("通过数", "通过"), 0)
    failed = safe_int(value("failed", "fail") or match_text("失败数", "失败"), 0)
    warning = safe_int(value("abnormal", "warn", "warning") or match_text("异常数", "告警数", "告警"), 0)
    total = safe_int(value("total", "totalCount", "caseCount", "case_count") or match_text("总数", "用例数"), 0)
    if not total:
        total = passed + failed + warning
    raw_status = str(value("status", "result") or match_text("运行状态", "状态")).strip()
    status = normalize_sonic_suite_status(raw_status, passed, failed, warning, text)
    return {
        "app_name": sonic_notify_clean_text(value("appName", "app_name"), fallback=""),
        "app_package": str(value("appPackage", "app_package") or "").strip(),
        "project_id": project_id,
        "result_id": result_id,
        "suite_id": safe_int(value("suiteId", "suite_id"), 0),
        "suite_name": suite_name,
        "status": status,
        "passed": passed,
        "failed": failed,
        "warning": warning,
        "total": total,
        "duration": sonic_notify_clean_text(value("duration") or match_text("耗时"), fallback=""),
        "createTime": sonic_notify_clean_text(value("createTime", "create_time", "startTime", "start_time") or match_text("创建时间", "开始时间"), fallback=""),
        "endTime": sonic_notify_clean_text(value("endTime", "end_time", "finishTime", "finish_time") or match_text("结束时间", "完成时间"), fallback=""),
        "report_url": report_url,
        "received_text": sonic_notify_clean_text(text, fallback="Sonic 已回传测试套结束事件"),
        "content_type": content_type or "",
    }


def sonic_result_suite_key(project_id, result_id):
    project_id = safe_int(project_id, 0)
    result_id = safe_int(result_id, 0)
    return f"sonic_result_{project_id}_{result_id}" if project_id and result_id else ""


def sonic_suite_bound_result_id(suite):
    suite = suite or {}
    return safe_int(suite.get("sonic_result_id") or (suite.get("sonic_result_meta") or {}).get("result_id"), 0)


def sonic_suite_is_legacy_mixed_completion(suite_key, suite):
    suite = suite or {}
    expected_key = sonic_result_suite_key(suite.get("sonic_project_id"), sonic_suite_bound_result_id(suite))
    return bool(expected_key and suite.get("completion_received") and suite_key != expected_key)


def sonic_suite_matches_completion(suite, event):
    result_id = safe_int(event.get("result_id"), 0)
    # A name/package is not an execution identity: successive scheduled runs
    # have the same values. Only an already bound Sonic result can be reused.
    if not result_id or result_id != sonic_suite_bound_result_id(suite):
        return False
    event_project_id = safe_int(event.get("project_id"), 0)
    suite_project_id = safe_int((suite or {}).get("sonic_project_id"), 0)
    return not (event_project_id and suite_project_id and event_project_id != suite_project_id)


def sonic_suite_key_for_completion_event(event, app, state, now_ts):
    result_key = sonic_result_suite_key(event.get("project_id"), event.get("result_id"))
    if result_key:
        return result_key
    event_suite = re.sub(r"\s+", "", str(event.get("suite_name") or ""))
    event_package = str((app or {}).get("package") or event.get("app_package") or "").strip()
    candidates = []
    for suite_key in set((state.get("active") or {}).values()):
        suite = (state.get("suites") or {}).get(suite_key) or {}
        if suite.get("completion_received") or (suite.get("sent_at") and not suite.get("send_error")):
            continue
        last_ts = safe_int(suite.get("last_update_ts") or suite.get("created_ts"), 0)
        if last_ts and now_ts - last_ts > sonic_suite_reopen_seconds():
            continue
        suite_package = str(suite.get("app_package") or ((suite.get("app") or {}).get("package")) or "").strip()
        if event_package and suite_package and event_package != suite_package:
            continue
        suite_name = re.sub(r"\s+", "", str(suite.get("sonic_suite_name") or ""))
        if event_suite and suite_name and not (
            event_suite == suite_name or event_suite in suite_name or suite_name in event_suite
        ):
            continue
        if not (suite.get("results") or suite.get("last_running_job_id")):
            continue
        candidates.append((last_ts, suite_key))
    if candidates:
        return sorted(candidates, reverse=True)[0][1]
    return unique_millis_id("sonic_suite")


def merge_sonic_suite_result_items(*groups):
    merged = []
    index = {}
    for items in groups:
        for item in items or []:
            row = dict(item or {})
            identity = (
                str(row.get("job_id") or "").strip()
                or "|".join(str(row.get(key) or "").strip() for key in (
                    "case_id", "module", "file", "target_task_name", "started_at"
                ))
            )
            if identity and identity in index:
                current = merged[index[identity]]
                current.update({
                    key: value for key, value in row.items()
                    if value not in ("", None)
                })
            else:
                if identity:
                    index[identity] = len(merged)
                merged.append(row)
    return merged


def register_sonic_suite_completion(event):
    now_ts = int(time.time())
    app = sonic_suite_app_for_completion(event)
    with SONIC_SUITE_LOCK:
        state = load_sonic_suite_results()
        suites = state.setdefault("suites", {})
        result_id = safe_int(event.get("result_id"), 0)
        project_id = safe_int(event.get("project_id"), 0)
        # A completed Sonic result is a single immutable run. If Sonic does
        # not send resultId in its custom robot payload, attach the completion
        # to the active suite that is currently collecting case callbacks.
        matched_key = sonic_suite_key_for_completion_event(event, app, state, now_ts)
        suite = suites.get(matched_key) or {
            "suite_key": matched_key,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "created_ts": now_ts,
            "results": [],
        }
        same_finished_event = bool(
            suite.get("sent_at")
            and not suite.get("send_error")
            and safe_int(suite.get("sonic_result_id"), 0)
            and safe_int(suite.get("sonic_result_id"), 0) == safe_int(event.get("result_id"), 0)
        )
        fixed_report_url = sonic_suite_fixed_report_url(
            suite,
            project_id=project_id,
            result_id=result_id,
            suite_key=matched_key
        )
        suite.update({
            "last_update_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_update_ts": now_ts,
            "completion_received": True,
            "completion_source": "sonic_callback",
            "completion_ts": now_ts,
            "app": app,
            "app_package": app.get("package") or event.get("app_package") or suite.get("app_package", ""),
            "app_name": app.get("name") or event.get("app_name") or suite.get("app_name", ""),
            "sonic_suite_id": str(event.get("suite_id") or suite.get("sonic_suite_id", "")),
            "sonic_suite_name": event.get("suite_name") or suite.get("sonic_suite_name", ""),
            "sonic_result_id": safe_int(event.get("result_id"), 0) or suite.get("sonic_result_id", 0),
            "sonic_project_id": safe_int(event.get("project_id"), 0) or suite.get("sonic_project_id", 0),
            "sonic_report_url": event.get("report_url") or suite.get("sonic_report_url", "") or fixed_report_url,
            "expected_total_count": max(safe_int(suite.get("expected_total_count"), 0), safe_int(event.get("total"), 0)),
            "run_mode": suite.get("run_mode") or "baseline",
            "sonic_completion": {
                "finished": True,
                "status": event.get("status") or "warning",
                "total": safe_int(event.get("total"), 0),
                "passed": safe_int(event.get("passed"), 0),
                "failed": safe_int(event.get("failed"), 0),
                "warning": safe_int(event.get("warning"), 0),
                "interrupted": event.get("status") == "interrupted",
                "duration": event.get("duration") or "",
                "createTime": event.get("createTime") or "",
                "endTime": event.get("endTime") or "",
            },
            "notification_mode": "suite_completion",
        })
        suites[matched_key] = suite
        save_sonic_suite_results(state)
    append_sonic_notify_log("sonic_suite_completion_received", {
        "suite_key": matched_key,
        "project_id": event.get("project_id"),
        "result_id": event.get("result_id"),
        "suite_name": event.get("suite_name"),
        "total": event.get("total"),
        "status": event.get("status"),
        "duplicate": same_finished_event,
    })
    if not same_finished_event:
        schedule_sonic_suite_summary(matched_key, delay=5)
    return {"suite_key": matched_key, "duplicate": same_finished_event, "suite": suite}


def sonic_suite_summary_status(results):
    if not results:
        return "warning"
    failed = len([item for item in results if item.get("status") == "failed"])
    warning = len([item for item in results if item.get("status") not in ("success", "failed")])
    if failed:
        return "failed"
    if warning:
        return "warning"
    return "success"


def sonic_suite_completion_stats(suite):
    completion = (suite or {}).get("sonic_completion") or {}
    if not completion or not completion.get("finished"):
        meta = (suite or {}).get("sonic_result_meta") or {}
        if not meta or not meta.get("finished"):
            return None
        total = safe_int(
            meta.get("expected_total_count")
            or meta.get("send_msg_count")
            or meta.get("sendMsgCount"),
            0,
        )
        if not total:
            return None
        actual = sonic_suite_case_stats((suite or {}).get("results") or [])
        status = safe_int(meta.get("status"), 0)
        status_text = str(meta.get("status_text") or meta.get("statusText") or "").lower()
        if status == 1 or any(word in status_text for word in ("success", "pass", "通过", "成功")):
            passed, failed, warning = total, 0, 0
        elif status == 3 or any(word in status_text for word in ("fail", "失败")):
            passed = min(actual.get("passed", 0), total)
            failed = max(actual.get("failed", 0), 1)
            warning = max(0, total - passed - failed)
        elif status == 2 or any(word in status_text for word in ("warn", "warning", "异常", "告警")):
            passed = min(actual.get("passed", 0), total)
            failed = min(actual.get("failed", 0), max(0, total - passed))
            warning = max(0, total - passed - failed)
        else:
            return None
        return {
            "total": total,
            "passed": passed,
            "failed": failed,
            "warning": warning,
            "actual_total": actual.get("total", 0),
            "expected_total": total,
            "pending": 0,
            "missing_task_callbacks": max(0, total - actual.get("total", 0)),
        }
    total = safe_int(completion.get("total"), 0)
    passed = safe_int(completion.get("passed"), 0)
    failed = safe_int(completion.get("failed"), 0)
    warning = safe_int(completion.get("warning") or completion.get("abnormal"), 0)
    if not total:
        total = passed + failed + warning
    if total and not (passed or failed or warning):
        status = completion.get("status") or ""
        if status == "success":
            passed = total
        elif status == "failed":
            failed = total
        elif status == "interrupted":
            warning = total
        else:
            warning = total
    if total > passed + failed + warning:
        warning += total - passed - failed - warning
    return {
        "total": total,
        "passed": passed,
        "failed": failed,
        "warning": warning,
        "actual_total": total,
        "expected_total": total,
        "pending": 0,
        "missing_task_callbacks": 0,
    }


def sonic_suite_expected_total(suite):
    if not suite:
        return 0
    values = [
        suite.get("expected_total_count"),
        suite.get("suite_expected_total"),
        suite.get("expected_case_count"),
        suite.get("suite_total"),
        suite.get("total_count"),
    ]
    detail = suite.get("sonic_report_lookup") or {}
    values.extend([
        detail.get("send_msg_count"),
        detail.get("sendMsgCount"),
        detail.get("case_count"),
        detail.get("total_count"),
    ])
    meta = suite.get("sonic_result_meta") or suite.get("sonic_suite_definition") or {}
    values.extend([
        meta.get("send_msg_count"),
        meta.get("sendMsgCount"),
        meta.get("case_count"),
        meta.get("total_count"),
        meta.get("expected_total_count"),
    ])
    definition = suite.get("sonic_suite_definition") or {}
    values.extend([
        definition.get("case_count"),
        definition.get("expected_total_count"),
    ])
    return max([safe_int(value, 0) for value in values] or [0])


def sonic_suite_case_units(item):
    total = safe_int(item.get("total_task_count") or item.get("totalTaskCount"), 0)
    return max(1, total)


def sonic_suite_result_identity(item):
    item = item or {}
    for key in ("case_id", "file", "target_task_name", "job_id"):
        value = str(item.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    return ""


def sonic_suite_job_identity(job):
    job = job or {}
    for key in ("case_id", "file", "target_task_name", "job_id"):
        value = str(job.get(key) or "").strip()
        if value:
            return f"{key}:{value}"
    return ""


def sonic_suite_unique_result_count(suite):
    identities = set()
    fallback_count = 0
    for item in (suite or {}).get("results") or []:
        identity = sonic_suite_result_identity(item)
        if identity:
            identities.add(identity)
        else:
            fallback_count += 1
    return len(identities) + fallback_count


def sonic_suite_contains_job_identity(suite, job):
    identity = sonic_suite_job_identity(job)
    if not identity:
        return False
    return any(sonic_suite_result_identity(item) == identity for item in (suite or {}).get("results") or [])


def sonic_suite_has_complete_result_cycle(suite):
    expected = sonic_suite_expected_total(suite)
    return bool(expected and sonic_suite_unique_result_count(suite) >= expected)


def sonic_suite_case_stats(results):
    stats = {"total": 0, "passed": 0, "failed": 0, "warning": 0}
    for item in results or []:
        total = sonic_suite_case_units(item)
        completed = safe_int(item.get("completed_task_count") or item.get("completedTaskCount"), 0)
        status = item.get("status") or ""
        stats["total"] += total
        if status == "success":
            stats["passed"] += total
        elif status == "failed":
            passed = max(0, min(total - 1, completed))
            stats["passed"] += passed
            stats["failed"] += max(1, total - passed)
        else:
            stats["warning"] += total
    return stats


def sonic_suite_display_stats(suite):
    completion_stats = sonic_suite_completion_stats(suite)
    if completion_stats:
        return completion_stats
    stats = sonic_suite_case_stats((suite or {}).get("results") or [])
    actual_total = stats["total"]
    expected_total = max(actual_total, sonic_suite_expected_total(suite))
    pending = max(0, expected_total - actual_total)
    stats["warning"] += pending
    stats["actual_total"] = actual_total
    stats["expected_total"] = expected_total
    stats["pending"] = pending
    stats["missing_task_callbacks"] = pending
    stats["total"] = expected_total
    return stats


def sonic_suite_effective_status(suite):
    completion = (suite or {}).get("sonic_completion") or {}
    if completion.get("finished"):
        if completion.get("status") == "interrupted":
            return "interrupted"
        stats = sonic_suite_display_stats(suite)
        if stats.get("failed"):
            return "failed"
        if stats.get("warning"):
            return "warning"
        return "success"
    results = list((suite or {}).get("results") or [])
    status = sonic_suite_summary_status(results)
    if status == "success" and sonic_suite_display_stats(suite).get("pending"):
        return "warning"
    return status


def sonic_suite_finished_in_sonic(suite):
    return bool(
        ((suite or {}).get("sonic_completion") or {}).get("finished")
        or ((suite or {}).get("sonic_result_meta") or {}).get("finished")
    )


def sonic_suite_result_line(job):
    title = sonic_notify_display_value(job.get("target_task_name") or job.get("current_task_name") or job.get("file") or job.get("case_id") or "-", "-")
    module = sonic_notify_display_value(job.get("module") or "-", "-")
    return f"{module} / {title}"


def sonic_suite_report_lookup_message(suite):
    if ensure_sonic_suite_report_url(suite):
        return ""
    detail = (suite or {}).get("sonic_report_lookup") or {}
    error = (suite or {}).get("sonic_report_lookup_error") or detail.get("error") or ""
    attempt = safe_int(detail.get("attempt"), 0)
    max_attempt = safe_int(detail.get("max_attempt"), 0)
    waited = f"已查询 {attempt}/{max_attempt} 次" if attempt and max_attempt else "已查询"
    if error:
        return f"Sonic 报告未附加：{waited}，{sonic_notify_compact(error, 80)}"
    if detail:
        return f"Sonic 报告未附加：{waited}，未匹配到时间窗口内的已完成结果"
    return ""


def write_sonic_suite_summary_report(suite):
    results = list((suite or {}).get("results") or [])
    app = suite.get("app") or sonic_suite_app_info(suite.get("app_package", ""), "")
    app_name = sonic_notify_pretty_title_text(app.get("name") or suite.get("app_name") or app.get("package") or "Sonic")
    run_mode = suite.get("run_mode") or "baseline"
    mode_label = "基线回归" if run_mode == "baseline" else "测试执行"
    status = sonic_suite_effective_status(suite)
    status_meta = sonic_suite_status_meta(status)
    status_text = status_meta["text"]
    status_class = status_meta["class"]
    stats = sonic_suite_display_stats(suite)
    total = stats["total"]
    passed = stats["passed"]
    failed = stats["failed"]
    warning = stats["warning"]
    pending = stats.get("pending", 0)
    missing_task_callbacks = stats.get("missing_task_callbacks", pending)
    suite_key = clean_id(suite.get("suite_key") or unique_millis_id("sonic_suite"), "sonic_suite")
    sonic_report_url = ensure_sonic_suite_report_url(suite) or suite.get("report_url") or ""
    sonic_lookup_message = sonic_suite_report_lookup_message(suite)
    result_modules = sorted({item.get("module") for item in results if item.get("module")})
    pending_module = result_modules[0] if result_modules else suite.get("module")
    duration = sonic_suite_duration_text(suite)
    started_ts, finished_ts, time_source = sonic_suite_time_range(suite)
    started_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started_ts)) if started_ts else ""
    finished_text = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(finished_ts)) if finished_ts else ""

    def h(value):
        return html_lib.escape(str(value or ""), quote=True)

    rows = []
    for idx, item in enumerate(results, start=1):
        item_status = item.get("status") or "-"
        cls = "pass" if item_status == "success" else ("fail" if item_status == "failed" else "warn")
        label = "通过" if item_status == "success" else ("失败" if item_status == "failed" else item_status)
        midscene_url = item.get("report_url") or ""
        report_pending = safe_bool(item.get("report_upload_pending"))
        report_error = item.get("report_upload_error")
        if str(midscene_url).startswith("http") and report_pending:
            midscene_link = f'<a href="{h(midscene_url)}" target="_blank">Midscene 报告（上传中）</a>'
        elif str(midscene_url).startswith("http") and report_error:
            midscene_link = f'<a href="{h(midscene_url)}" target="_blank">Midscene 报告（上传失败）</a>'
        elif str(midscene_url).startswith("http"):
            midscene_link = f'<a href="{h(midscene_url)}" target="_blank">Midscene 报告</a>'
        elif report_pending:
            midscene_link = '<span class="muted">后台上传中</span>'
        elif report_error:
            midscene_link = '<span class="muted">上传失败</span>'
        else:
            midscene_link = '<span class="muted">无</span>'
        reason = sonic_notify_compact(item.get("error") or item.get("stderr_tail") or item.get("progress_message") or "", 300)
        rows.append(
            "<tr>"
            f"<td>{idx}</td>"
            f"<td><span class='badge {cls}'>{h(label)}</span></td>"
            f"<td>{h(sonic_notify_display_value(item.get('module')))}</td>"
            f"<td>{h(sonic_notify_display_value(item.get('target_task_name') or item.get('current_task_name') or item.get('file')))}</td>"
            f"<td>{h(item.get('device_id'))}</td>"
            f"<td>{midscene_link}</td>"
            f"<td class='reason'>{h(reason)}</td>"
            "</tr>"
        )
    if missing_task_callbacks:
        missing_reason = (
            "Sonic 原始报告已结束，但 Task 平台未收到该用例的桥接回传；请以 Sonic 原始报告为准，或检查 Groovy 桥接脚本/接口权限。"
            if sonic_suite_finished_in_sonic(suite)
            else "仍在等待 Sonic Agent 回传结果。"
        )
        for offset in range(1, missing_task_callbacks + 1):
            rows.append(
                "<tr>"
                f"<td>{len(rows) + 1}</td>"
                "<td><span class='badge warn'>未回传</span></td>"
                f"<td>{h(sonic_notify_display_value(pending_module, '-'))}</td>"
                f"<td>未回传用例 {offset}</td>"
                f"<td>{h(sonic_notify_display_value(suite.get('device_id'), '-'))}</td>"
                "<td><span class='muted'>请查看 Sonic 原始报告</span></td>"
                f"<td class='reason'>{h(missing_reason)}</td>"
                "</tr>"
            )
    if not rows:
        rows.append("<tr><td colspan='7' class='empty'>暂无用例结果</td></tr>")

    sonic_link = (
        f'<a class="button" href="{h(sonic_report_url)}" target="_blank">查看 Sonic 原始报告</a>'
        if str(sonic_report_url).startswith("http")
        else ""
    )
    html = f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{h(app_name)} {h(mode_label)}{h(status_text)}</title>
  <style>
    :root {{ color-scheme: light; --bg:#f6f8fb; --card:#fff; --text:#162033; --muted:#667085; --line:#e4e7ec; --pass:#12b76a; --fail:#f04438; --warn:#f79009; }}
    body {{ margin:0; padding:28px; background:var(--bg); color:var(--text); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif; }}
    .wrap {{ max-width:1180px; margin:0 auto; }}
    .hero {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:24px; box-shadow:0 8px 24px rgba(16,24,40,.06); }}
    h1 {{ margin:0 0 14px; font-size:24px; }}
    .meta {{ display:flex; flex-wrap:wrap; gap:10px; color:var(--muted); font-size:14px; }}
    .pill {{ border:1px solid var(--line); border-radius:999px; padding:6px 10px; background:#fff; }}
    .badge {{ display:inline-flex; min-width:44px; justify-content:center; border-radius:999px; padding:4px 10px; color:#fff; font-weight:700; font-size:12px; }}
    .pass {{ background:var(--pass); }} .fail {{ background:var(--fail); }} .warn {{ background:var(--warn); }}
    .actions {{ margin-top:18px; display:flex; gap:10px; flex-wrap:wrap; }}
    .button {{ display:inline-block; border-radius:8px; padding:9px 14px; background:#155eef; color:#fff; text-decoration:none; font-weight:700; }}
    table {{ width:100%; border-collapse:separate; border-spacing:0; margin-top:18px; background:var(--card); border:1px solid var(--line); border-radius:12px; overflow:hidden; }}
    th, td {{ padding:12px 14px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; font-size:14px; }}
    th {{ background:#f2f4f7; color:#475467; font-weight:700; }}
    tr:last-child td {{ border-bottom:0; }}
    a {{ color:#155eef; font-weight:700; }}
    .reason {{ max-width:420px; color:#475467; line-height:1.55; }}
    .muted, .empty {{ color:var(--muted); }}
    @media (max-width:760px) {{ body {{ padding:14px; }} table {{ display:block; overflow-x:auto; }} .hero {{ padding:18px; }} }}
  </style>
</head>
<body>
  <main class="wrap">
    <section class="hero">
      <h1>{h(app_name)}｜{h(mode_label)}{h(status_text)}</h1>
      <div class="meta">
        <span class="pill">结论：<b class="{status_class}" style="background:transparent;color:var(--{status_class})">{h(status_text)}</b></span>
        <span class="pill">总数：{total}</span>
        <span class="pill">通过：{passed}</span>
        <span class="pill">失败：{failed}</span>
        <span class="pill">告警：{warning}</span>
        {f'<span class="pill">待回传：{pending}</span>' if pending else ''}
        {f'<span class="pill">开始：{h(started_text)}</span>' if started_text else ''}
        {f'<span class="pill">结束：{h(finished_text)}</span>' if finished_text else ''}
        {f'<span class="pill">耗时：{h(duration)}</span>' if duration else ''}
        <span class="pill">生成时间：{h(time.strftime("%Y-%m-%d %H:%M:%S"))}</span>
        {f'<span class="pill">时间来源：{h(time_source)}</span>' if time_source else ''}
        {f'<span class="pill">{h(sonic_lookup_message)}</span>' if sonic_lookup_message else ''}
      </div>
      <div class="actions">{sonic_link}</div>
    </section>
    <table>
      <thead><tr><th>#</th><th>状态</th><th>模块</th><th>用例</th><th>设备</th><th>报告</th><th>失败/备注</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
  </main>
</body>
</html>
"""
    filename = f"{suite_key}-summary.html"
    write_text_file(safe_join(REPORT_DIR, filename), html)
    return public_report_url(filename)


def build_sonic_suite_summary_card(suite):
    results = list((suite or {}).get("results") or [])
    app = suite.get("app") or sonic_suite_app_info(suite.get("app_package", ""), "")
    app_name = sonic_notify_pretty_title_text(app.get("name") or suite.get("app_name") or app.get("package") or "Sonic")
    run_mode = suite.get("run_mode") or "baseline"
    mode_label = "基线回归" if run_mode == "baseline" else "测试执行"
    status = sonic_suite_effective_status(suite)
    status_meta = sonic_suite_status_meta(status)
    color = status_meta["color"]
    icon = status_meta["icon"]
    status_label = status_meta["text"]
    stats = sonic_suite_display_stats(suite)
    total = stats["total"]
    passed = stats["passed"]
    failed = stats["failed"]
    warning = stats["warning"]
    duration = sonic_suite_duration_text(suite)
    devices = sorted({item.get("device_id") for item in results if item.get("device_id")})
    modules = sorted({item.get("module") for item in results if item.get("module")})
    elements = [
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**结论：** <font color='{color}'>{icon} {status_label}</font>"}},
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**应用：** {app_name}"}},
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**范围：** {mode_label} · {total} 条用例"}},
        {"tag": "div", "text": {"tag": "lark_md", "content": f"**统计：** 通过 {passed} / 失败 {failed} / 告警 {warning}"}},
    ]
    if stats.get("pending"):
        pending_text = (
            f"{stats.get('pending')} 条用例在 Sonic 已结束后仍未回传 Task 平台，请检查桥接脚本或接口权限"
            if sonic_suite_finished_in_sonic(suite)
            else f"{stats.get('pending')} 条用例仍未收到结果，已按等待上限生成当前汇总"
        )
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**待回传：** {pending_text}"}})
    extra = []
    if modules:
        extra.append("模块：" + "、".join(modules[:4]) + (" 等" if len(modules) > 4 else ""))
    if devices:
        extra.append("设备：" + "、".join(devices[:3]) + (" 等" if len(devices) > 3 else ""))
    if duration:
        extra.append("耗时：" + duration)
    if extra:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "**补充：** " + sonic_notify_clean_text(" · ".join(extra))}})
    sonic_report_url = ensure_sonic_suite_report_url(suite)
    lookup_message = sonic_suite_report_lookup_message(suite)
    if lookup_message:
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": f"**Sonic 报告：** {lookup_message}"}})
    pending_reports = sonic_suite_pending_midscene_reports(suite)
    if pending_reports:
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**Midscene 报告：** {pending_reports} 份仍在后台上传，汇总报告会自动补充链接"
            }
        })
    failed_items = [item for item in results if item.get("status") == "failed"]
    if failed_items:
        lines = []
        for item in failed_items[:5]:
            reason = sonic_notify_compact(item.get("error") or item.get("stderr_tail") or item.get("progress_message") or "请查看报告", 80)
            lines.append(f"- {sonic_suite_result_line(item)}：{reason}")
        if len(failed_items) > 5:
            lines.append(f"- 还有 {len(failed_items) - 5} 条失败，请在 Task 平台执行中心查看")
        elements.append({"tag": "div", "text": {"tag": "lark_md", "content": "**失败明细：**\n" + "\n".join(lines)}})
    sonic_report_urls = [
        sonic_report_url,
        suite.get("sonic_report_url"),
        suite.get("report_url"),
    ] + [
        item.get("sonic_report_url")
        for item in results
        if str(item.get("sonic_report_url") or "").startswith("http")
    ]
    sonic_report_urls = [url for url in sonic_report_urls if str(url or "").startswith("http")]
    report_urls = [
        item.get("report_url")
        for item in results
        if str(item.get("report_url") or "").startswith("http")
    ]
    suite_report_url = suite.get("suite_report_url") or suite.get("summary_report_url") or ""
    actions = []
    if str(suite_report_url).startswith("http"):
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看汇总报告"},
            "url": suite_report_url,
            "type": "primary"
        })
    if sonic_report_urls:
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看 Sonic 报告"},
            "url": sonic_report_urls[0],
            "type": "default" if actions else "primary"
        })
    if report_urls and not actions:
        actions.append({
            "tag": "button",
            "text": {"tag": "plain_text", "content": "查看 Midscene 报告"},
            "url": report_urls[0],
            "type": "default" if sonic_report_urls else "primary"
        })
    if actions:
        elements.append({"tag": "hr"})
        elements.append({"tag": "action", "actions": actions})
    return {
        "msg_type": "interactive",
        "card": {
            "config": {"wide_screen_mode": True},
            "header": {
                "template": color,
                "title": {"tag": "plain_text", "content": f"{icon} {app_name}｜{mode_label}{status_label}"}
            },
            "elements": elements,
        }
    }


def sonic_suite_natural_key(job):
    return "|".join([
        job.get("app_package") or resolve_app_package(job.get("module", ""), job.get("file", ""), "", allow_default=False) or "",
        job.get("sonic_suite_id") or job.get("sonicSuiteId") or "",
        job.get("sonic_suite_name") or "",
        job.get("suite_started_at") or "",
        job.get("runner_id") or "sonic",
        job.get("device_id") or "",
        job.get("run_mode") or "baseline",
    ])


def sonic_job_matches_suite(job, suite):
    if not job or job.get("source") != "sonic":
        return False
    suite_run_id = suite.get("suite_run_id") or suite.get("suiteRunId")
    job_suite_run_id = job.get("suite_run_id") or job.get("suiteRunId")
    if suite_run_id and job_suite_run_id:
        return str(suite_run_id) == str(job_suite_run_id)
    suite_key = suite.get("natural_key") or ""
    return bool(suite_key and sonic_suite_natural_key(job) == suite_key)


def sonic_suite_has_running_jobs(suite):
    for job in load_jobs():
        if job.get("status") not in ("pending", "running"):
            continue
        if sonic_job_matches_suite(job, suite):
            return True
    return False


def sonic_suite_can_wait_for_running_jobs(suite, now_ts=None):
    now_ts = now_ts or int(time.time())
    created_ts = safe_int(suite.get("created_ts"), 0) or now_ts
    return now_ts - created_ts < sonic_suite_max_wait_seconds()


def sonic_suite_key_for_job(job, state, now_ts):
    explicit = job.get("suite_run_id") or job.get("suiteRunId")
    if explicit:
        return str(explicit)
    natural = sonic_suite_natural_key(job)
    active_key = (state.get("active") or {}).get(natural)
    if active_key:
        suite = (state.get("suites") or {}).get(active_key) or {}
        last_ts = safe_int(suite.get("last_update_ts") or suite.get("created_ts"), 0)
        # Sonic may start the same timed suite again before its previous
        # webhook completion is available. Once a suite has a full case cycle,
        # the next callback for an existing case belongs to a new run.
        if sonic_suite_has_complete_result_cycle(suite) and sonic_suite_contains_job_identity(suite, job):
            suite["closed_at"] = suite.get("closed_at") or time.strftime("%Y-%m-%d %H:%M:%S")
            suite["closed_reason"] = suite.get("closed_reason") or "下一轮 Sonic 测试套已开始，停止追加上一轮结果"
            state.setdefault("suites", {})[active_key] = suite
            state.setdefault("active", {}).pop(natural, None)
        elif suite.get("sent_at") and not suite.get("send_error"):
            state.setdefault("active", {}).pop(natural, None)
        elif (not last_ts or now_ts - last_ts <= sonic_suite_reopen_seconds()):
            return active_key
    active_key = (state.get("active") or {}).get(natural)
    if active_key:
        suite = (state.get("suites") or {}).get(active_key) or {}
        last_ts = safe_int(suite.get("last_update_ts") or suite.get("created_ts"), 0)
        if (
            not suite.get("sent_at")
            and (not last_ts or now_ts - last_ts <= sonic_suite_reopen_seconds())
        ):
            return active_key
    suite_key = unique_millis_id("sonic_suite")
    state.setdefault("active", {})[natural] = suite_key
    return suite_key


def schedule_sonic_suite_summary(suite_key, delay=None):
    quiet = max(1, int(delay)) if delay is not None else sonic_suite_quiet_seconds()
    with SONIC_SUITE_LOCK:
        old = SONIC_SUITE_TIMERS.get(suite_key)
        if old:
            try:
                old.cancel()
            except Exception:
                pass
        timer = threading.Timer(quiet, send_sonic_suite_summary_if_quiet, args=(suite_key,))
        timer.daemon = True
        SONIC_SUITE_TIMERS[suite_key] = timer
        timer.start()


def restore_pending_sonic_suite_summary_timers():
    state = load_sonic_suite_results()
    pending_keys = []
    suppressed_keys = []
    state_changed = False
    now_ts = int(time.time())
    for suite_key, suite in (state.get("suites") or {}).items():
        # A send claim belongs to an in-memory worker. After a service restart
        # that worker no longer exists, so retaining the claim would strand the suite.
        if suite.get("send_in_progress"):
            suite["send_in_progress"] = False
            suite["send_started_ts"] = 0
            state_changed = True
        if sonic_suite_is_legacy_mixed_completion(suite_key, suite) and not suite.get("sent_at"):
            suite["notification_suppressed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            suite["notification_suppressed_reason"] = "历史套件结果与 Sonic resultId 混绑，已停止发送；等待按 resultId 生成的最终汇总"
            state_changed = True
            suppressed_keys.append(suite_key)
            continue
        if suite.get("sent_at") and not suite.get("send_error"):
            continue
        if not suite.get("results") and not suite.get("completion_received"):
            continue
        if sonic_suite_waits_for_completion_event(suite) and not suite.get("completion_received"):
            last_ts = safe_int(suite.get("last_update_ts") or suite.get("created_ts"), 0)
            # Restore only recent Sonic suites. Very old records are historical
            # data and must not suddenly notify Feishu after a service restart.
            if last_ts and now_ts - last_ts > max(sonic_suite_max_wait_seconds() * 2, 3600):
                continue
        pending_keys.append(suite_key)
    if state_changed:
        save_sonic_suite_results(state)
    for suite_key in pending_keys:
        schedule_sonic_suite_summary(suite_key, delay=5)
    if pending_keys:
        append_sonic_notify_log("suite_summary_timers_restored", {
            "count": len(pending_keys),
            "suite_keys": pending_keys[:20],
        })
    if suppressed_keys:
        append_sonic_notify_log("suite_summary_legacy_mixed_completion_suppressed", {
            "count": len(suppressed_keys),
            "suite_keys": suppressed_keys[:20],
        })


def register_sonic_suite_result(job):
    if not job or job.get("source") != "sonic" or job.get("status") in ("pending", "running"):
        return ""
    now_ts = int(time.time())
    updated_suite_for_summary_refresh = None
    already_notified_final = False
    with SONIC_SUITE_LOCK:
        state = load_sonic_suite_results()
        suite_key = sonic_suite_key_for_job(job, state, now_ts)
        suite = (state.get("suites") or {}).get(suite_key) or {
            "suite_key": suite_key,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "created_ts": now_ts,
            "results": [],
        }
        app = sonic_suite_app_info(job.get("app_package", ""), job.get("module", ""))
        suite_started_at = job.get("suite_started_at") or job.get("suiteStartedAt") or suite.get("suite_started_at", "")
        suite_start_ts = parse_time(suite_started_at)
        expected_total = max(
            safe_int(suite.get("expected_total_count"), 0),
            safe_int(job.get("suite_expected_total") or job.get("suiteExpectedTotal"), 0),
        )
        suite.update({
            "last_update_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_update_ts": now_ts,
            "natural_key": sonic_suite_natural_key(job),
            "suite_run_id": job.get("suite_run_id") or job.get("suiteRunId") or suite.get("suite_run_id", ""),
            "sonic_suite_id": job.get("sonic_suite_id") or job.get("sonicSuiteId") or app.get("sonic_suite_id") or app.get("sonicSuiteId") or suite.get("sonic_suite_id", ""),
            "app_package": app.get("package") or job.get("app_package", ""),
            "app_name": app.get("name") or job.get("app_name", ""),
            "app": app,
            "sonic_suite_name": job.get("sonic_suite_name") or app.get("sonic_suite_name") or app.get("sonicSuiteName") or suite.get("sonic_suite_name", ""),
            "suite_started_at": suite_started_at,
            "expected_total_count": expected_total,
            "run_mode": job.get("run_mode") or "baseline",
            "runner_id": job.get("runner_id") or "sonic",
            "device_id": job.get("device_id") or "",
        })
        if suite_start_ts:
            existing_created_ts = safe_int(suite.get("created_ts"), 0)
            suite["created_ts"] = min(existing_created_ts, suite_start_ts) if existing_created_ts else suite_start_ts
            suite["created_at"] = suite_started_at
        result = {
            "job_id": job.get("job_id", ""),
            "case_id": job.get("case_id", ""),
            "module": sonic_notify_clean_text(job.get("module", "")),
            "file": sonic_notify_clean_text(job.get("file", "")),
            "target_task_name": sonic_notify_clean_text(job.get("target_task_name", "")),
            "current_task_name": sonic_notify_clean_text(job.get("current_task_name", "")),
            "status": job.get("status", ""),
            "run_mode": job.get("run_mode", ""),
            "runner_id": job.get("runner_id", ""),
            "device_id": job.get("device_id", ""),
            "report_url": job.get("report_url", ""),
            "report_upload_pending": safe_bool(job.get("report_upload_pending")),
            "report_upload_error": job.get("report_upload_error", ""),
            "sonic_report_url": job.get("sonic_report_url", ""),
            "sonic_suite_id": job.get("sonic_suite_id", ""),
            "sonic_suite_name": job.get("sonic_suite_name", ""),
            "suite_started_at": job.get("suite_started_at", ""),
            "suite_expected_total": safe_int(job.get("suite_expected_total") or job.get("suiteExpectedTotal"), 0),
            "error": sonic_notify_clean_text(job.get("error", "") or job.get("stderr_tail", "")),
            "stderr_tail": sonic_notify_clean_text(job.get("stderr_tail", "")),
            "progress_message": sonic_notify_clean_text(job.get("progress_message", "")),
            "completed_task_count": safe_int(job.get("completed_task_count") or job.get("completedTaskCount"), 0),
            "total_task_count": safe_int(job.get("total_task_count") or job.get("totalTaskCount"), 0),
            "created_at": job.get("created_at", ""),
            "started_at": job.get("started_at", ""),
            "finished_at": job.get("finished_at", ""),
        }
        results = [item for item in (suite.get("results") or []) if item.get("job_id") != result["job_id"]]
        results.append(result)
        suite["results"] = results
        completion_only = sonic_suite_waits_for_completion_event(job)
        suite["notification_mode"] = "suite_completion" if completion_only else "case_quiet_period"
        if not completion_only:
            suite["sent_at"] = ""
        already_notified_final = bool(
            completion_only
            and suite.get("sent_at")
            and not suite.get("send_error")
        )
        state.setdefault("suites", {})[suite_key] = suite
        save_sonic_suite_results(state)
        if suite.get("suite_report_url"):
            updated_suite_for_summary_refresh = dict(suite)
    if updated_suite_for_summary_refresh:
        try:
            write_sonic_suite_summary_report(updated_suite_for_summary_refresh)
        except Exception as e:
            append_sonic_notify_log("suite_result_summary_refresh_error", {
                "suite_key": suite_key,
                "job_id": job.get("job_id", ""),
            }, error=str(e))
    if sonic_suite_waits_for_completion_event(job):
        if not already_notified_final:
            schedule_sonic_suite_summary(suite_key, delay=sonic_suite_running_check_delay_seconds())
        append_sonic_notify_log("sonic_case_result_recorded_waiting_suite_complete", {
            "suite_key": suite_key,
            "job_id": job.get("job_id", ""),
            "case_name": job.get("target_task_name") or job.get("current_task_name") or job.get("file", ""),
            "already_notified_final": already_notified_final,
            "summary_scheduled": not already_notified_final,
        })
    else:
        schedule_sonic_suite_summary(suite_key)
    return suite_key


def attach_sonic_background_report(job_id, report_url="", local_report_path="", report_upload_error=""):
    job_id = str(job_id or "").strip()
    report_url = str(report_url or "").strip()
    local_report_path = str(local_report_path or "").strip()
    report_upload_error = sonic_notify_clean_text(report_upload_error or "", fallback="报告后台上传失败")
    if not job_id:
        raise ValueError("job_id 不能为空")
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    updated_job = None
    suite_key = ""
    with JOB_LOCK:
        target, jobs = find_job(job_id)
        if not target:
            raise ValueError("执行记录不存在")
        if report_url:
            target["report_url"] = report_url
            target["report_upload_error"] = ""
        else:
            target["report_upload_error"] = report_upload_error or "后台上传未返回报告地址"
        if local_report_path:
            target["local_report_path"] = local_report_path
        target["report_upload_pending"] = False
        target["report_uploaded_at"] = now if report_url else ""
        suite_key = target.get("sonic_suite_key") or ""
        updated_job = dict(target)
        save_jobs(jobs)
    if updated_job.get("module") and updated_job.get("file") and report_url:
        update_task_meta(updated_job["module"], updated_job["file"], {
            "last_report_url": report_url
        })
    updated_suite = None
    if suite_key:
        with SONIC_SUITE_LOCK:
            state = load_sonic_suite_results()
            suite = (state.get("suites") or {}).get(suite_key)
            if suite:
                for item in suite.get("results") or []:
                    if item.get("job_id") == job_id:
                        if report_url:
                            item["report_url"] = report_url
                            item["report_upload_error"] = ""
                        else:
                            item["report_upload_error"] = report_upload_error or "后台上传未返回报告地址"
                        item["report_upload_pending"] = False
                        break
                state.setdefault("suites", {})[suite_key] = suite
                save_sonic_suite_results(state)
                updated_suite = dict(suite)
    if updated_suite and updated_suite.get("suite_report_url"):
        try:
            write_sonic_suite_summary_report(updated_suite)
        except Exception as e:
            append_sonic_notify_log("background_report_summary_refresh_error", {
                "suite_key": suite_key,
                "job_id": job_id,
            }, error=str(e))
    append_sonic_notify_log("background_midscene_report_attached" if report_url else "background_midscene_report_failed", {
        "suite_key": suite_key,
        "job_id": job_id,
        "report_url": report_url,
    }, error="" if report_url else report_upload_error)
    return updated_job


def process_sonic_result_post_actions(job, stdout, stderr):
    if not job or job.get("status") == "success":
        return
    job_id = job.get("job_id", "")
    failure_review = None
    optimize_result = None
    try:
        failure_review = call_dashscope_failure_review(job, stdout, stderr, None)
    except Exception as e:
        failure_review = {
            "category": "unknown",
            "confidence": 0,
            "reason": f"复检失败：{e}",
            "evidence": [],
            "suggested_action": "人工查看日志",
            "can_auto_repair": False
        }
    with JOB_LOCK:
        target, jobs = find_job(job_id)
        if target:
            target["failure_review"] = failure_review
            save_jobs(jobs)

    should_auto_repair = (
        ENABLE_AUTOMATIC_BASELINE_REPAIR
        and job.get("run_mode") == "baseline"
        and safe_bool(job.get("auto_optimize"))
        and failure_review
        and failure_review.get("category") == "script_issue"
        and safe_int(job.get("attempt"), 1) < safe_int(job.get("max_attempt"), 2)
    )
    if should_auto_repair:
        try:
            repaired, repair_dir = optimize_job_yaml_by_scope(job, stdout, stderr, None)
            next_job = create_pending_job(
                job["module"],
                job["file"],
                auto_optimize=True,
                max_attempt=safe_int(job.get("max_attempt"), 2),
                attempt=safe_int(job.get("attempt"), 1) + 1,
                parent_job_id=job_id,
                device_id=job.get("device_id", ""),
                runner_id=job.get("runner_id", ""),
                run_mode=job.get("run_mode", "baseline"),
                target_task_name=job.get("target_task_name", "")
            )
            optimize_result = {
                "ok": True,
                "analysis": repaired.get("analysis", ""),
                "changes": repaired.get("changes", []),
                "repair_dir": repair_dir,
                "updated_file": f"{job.get('module', '')}/{job.get('file', '')}",
                "next_job": next_job
            }
        except Exception as e:
            optimize_result = {"ok": False, "error": str(e)}
        with JOB_LOCK:
            target, jobs = find_job(job_id)
            if target:
                target["optimize_result"] = optimize_result
                save_jobs(jobs)


def start_sonic_result_post_actions(job, stdout, stderr):
    if not job or job.get("status") == "success":
        return False
    worker = threading.Thread(
        target=process_sonic_result_post_actions,
        args=(dict(job), stdout or "", stderr or ""),
        daemon=True
    )
    worker.start()
    return True


def touch_sonic_suite_activity(job):
    if not job or job.get("source") != "sonic":
        return ""
    now_ts = int(time.time())
    should_schedule = False
    with SONIC_SUITE_LOCK:
        state = load_sonic_suite_results()
        suite_key = sonic_suite_key_for_job(job, state, now_ts)
        suite = (state.get("suites") or {}).get(suite_key) or {
            "suite_key": suite_key,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "created_ts": now_ts,
            "results": [],
        }
        app = sonic_suite_app_info(job.get("app_package", ""), job.get("module", ""))
        suite_started_at = job.get("suite_started_at") or job.get("suiteStartedAt") or suite.get("suite_started_at", "")
        suite_start_ts = parse_time(suite_started_at)
        expected_total = max(
            safe_int(suite.get("expected_total_count"), 0),
            safe_int(job.get("suite_expected_total") or job.get("suiteExpectedTotal"), 0),
        )
        suite.update({
            "last_update_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "last_update_ts": now_ts,
            "natural_key": sonic_suite_natural_key(job),
            "suite_run_id": job.get("suite_run_id") or job.get("suiteRunId") or suite.get("suite_run_id", ""),
            "sonic_suite_id": job.get("sonic_suite_id") or job.get("sonicSuiteId") or app.get("sonic_suite_id") or app.get("sonicSuiteId") or suite.get("sonic_suite_id", ""),
            "app_package": app.get("package") or job.get("app_package", ""),
            "app_name": app.get("name") or job.get("app_name", ""),
            "app": app,
            "sonic_suite_name": job.get("sonic_suite_name") or app.get("sonic_suite_name") or app.get("sonicSuiteName") or suite.get("sonic_suite_name", ""),
            "suite_started_at": suite_started_at,
            "expected_total_count": expected_total,
            "run_mode": job.get("run_mode") or "baseline",
            "runner_id": job.get("runner_id") or "sonic",
            "device_id": job.get("device_id") or "",
            "last_running_job_id": job.get("job_id", ""),
            "last_running_case": job.get("target_task_name") or job.get("current_task_name") or job.get("file", ""),
        })
        if suite_start_ts:
            existing_created_ts = safe_int(suite.get("created_ts"), 0)
            suite["created_ts"] = min(existing_created_ts, suite_start_ts) if existing_created_ts else suite_start_ts
            suite["created_at"] = suite_started_at
        should_schedule = bool(suite.get("results")) and not sonic_suite_waits_for_completion_event(job)
        if sonic_suite_waits_for_completion_event(job):
            suite["notification_mode"] = "suite_completion"
        state.setdefault("suites", {})[suite_key] = suite
        save_sonic_suite_results(state)
    if should_schedule:
        schedule_sonic_suite_summary(suite_key)
    return suite_key


def sonic_project_id_for_package(package):
    package = (package or "").strip()
    if not package:
        return 0
    env_key = "SONIC_PROJECT_ID_" + re.sub(r"[^A-Za-z0-9]+", "_", package).upper().strip("_")
    env_project_id = safe_int(os.getenv(env_key), 0)
    if env_project_id:
        return env_project_id
    for app in sonic_notify_known_apps():
        if app.get("package") == package:
            project_id = sonic_project_id_for_app(app)
            if project_id:
                return project_id
    try:
        return sonic_find_project_id({"package": package, "name": package})
    except Exception:
        return 0


def sonic_result_detail_url(project_id, result_id):
    project_id = safe_int(project_id, 0)
    result_id = safe_int(result_id, 0)
    if not project_id or not result_id:
        return ""
    return f"{sonic_base_url()}/Home/{project_id}/ResultDetail/{result_id}"


def sonic_suite_fixed_report_url(suite=None, project_id=0, result_id=0, suite_key=""):
    suite = suite or {}
    project_id = safe_int(
        project_id
        or suite.get("sonic_project_id")
        or suite.get("project_id")
        or suite.get("projectId")
        or (suite.get("sonic_result_meta") or {}).get("project_id")
        or (suite.get("sonic_result_meta") or {}).get("projectId"),
        0
    )
    result_id = safe_int(
        result_id
        or suite.get("sonic_result_id")
        or suite.get("result_id")
        or suite.get("resultId")
        or (suite.get("sonic_result_meta") or {}).get("result_id")
        or (suite.get("sonic_result_meta") or {}).get("resultId"),
        0
    )
    if (not project_id or not result_id):
        key = str(suite_key or suite.get("suite_key") or "").strip()
        m = re.search(r"sonic_result_(\d+)_(\d+)", key)
        if m:
            project_id = project_id or safe_int(m.group(1), 0)
            result_id = result_id or safe_int(m.group(2), 0)
    return sonic_result_detail_url(project_id, result_id)


def ensure_sonic_suite_report_url(suite):
    if not isinstance(suite, dict):
        return ""
    existing = str(suite.get("sonic_report_url") or suite.get("report_url") or "").strip()
    if existing.startswith("http"):
        suite["sonic_report_url"] = existing
        return existing
    fixed = sonic_suite_fixed_report_url(suite)
    if fixed:
        suite["sonic_report_url"] = fixed
        suite["sonic_report_lookup_error"] = ""
        return fixed
    return ""


def sonic_list_results(project_id, page=1, page_size=15):
    data = sonic_response_data(sonic_request(
        "GET",
        "/results/list",
        params={"projectId": project_id, "page": page, "pageSize": page_size},
        timeout=10
    )) or {}
    return extract_page_items(data)


def sonic_suite_config_id(suite):
    app = (suite or {}).get("app") or sonic_suite_app_info((suite or {}).get("app_package", ""), "")
    return safe_int(
        (suite or {}).get("sonic_suite_id")
        or (suite or {}).get("sonicSuiteId")
        or app.get("sonic_suite_id")
        or app.get("sonicSuiteId"),
        0
    )


def sonic_suite_config_name(suite):
    app = (suite or {}).get("app") or sonic_suite_app_info((suite or {}).get("app_package", ""), "")
    return str(
        (suite or {}).get("sonic_suite_name")
        or (suite or {}).get("suite_name")
        or app.get("sonic_suite_name")
        or app.get("sonicSuiteName")
        or ""
    ).strip()


def sonic_count_suite_cases(dto):
    if not isinstance(dto, dict):
        return 0
    for key in ("testCases", "test_cases", "cases", "caseList", "case_list"):
        value = dto.get(key)
        if isinstance(value, list):
            return len(value)
    for key in ("caseIds", "case_ids", "testCaseIds", "test_case_ids"):
        value = dto.get(key)
        if isinstance(value, list):
            return len(value)
        if isinstance(value, str):
            return len([item for item in re.split(r"[,;\s]+", value) if item.strip()])
    return safe_int(dto.get("caseCount") or dto.get("case_count") or dto.get("totalCase") or dto.get("total_case"), 0)


def sonic_suite_definition_meta_from_dto(dto, source=""):
    count = sonic_count_suite_cases(dto)
    return {
        "source": source,
        "suite_id": safe_int(dto.get("id"), 0) if isinstance(dto, dict) else 0,
        "suite_name": dto.get("name", "") if isinstance(dto, dict) else "",
        "expected_total_count": count,
        "case_count": count,
    }


def lookup_sonic_suite_definition_for_suite(suite):
    app = suite.get("app") or sonic_suite_app_info(suite.get("app_package", ""), "")
    package = suite.get("app_package") or app.get("package", "")
    project_id = sonic_project_id_for_app(app) or sonic_project_id_for_package(package)
    if not project_id:
        return {"error": "未找到 Sonic 项目 ID"}
    suite_id = sonic_suite_config_id(suite)
    suite_name = sonic_suite_config_name(suite)

    if suite_id:
        try:
            data = sonic_response_data(sonic_request("GET", "/testSuites", params={"id": suite_id}, timeout=10)) or {}
            if isinstance(data, dict):
                meta = sonic_suite_definition_meta_from_dto(data, "/testSuites?id")
                if meta.get("expected_total_count"):
                    meta["project_id"] = project_id
                    return meta
        except Exception as e:
            return {"project_id": project_id, "suite_id": suite_id, "error": str(e), "source": "/testSuites?id"}

    try:
        data = sonic_response_data(sonic_request("GET", "/testSuites/listAll", params={"projectId": project_id}, timeout=10)) or []
        suites = data if isinstance(data, list) else extract_page_items(data)
    except Exception as e:
        return {"project_id": project_id, "error": str(e), "source": "/testSuites/listAll"}

    normalized_name = re.sub(r"\s+", "", suite_name)
    best = None
    for item in suites:
        if not isinstance(item, dict):
            continue
        if suite_id and safe_int(item.get("id"), 0) == suite_id:
            best = item
            break
        item_name = re.sub(r"\s+", "", str(item.get("name") or ""))
        if normalized_name and (item_name == normalized_name or normalized_name in item_name or item_name in normalized_name):
            best = item
            break

    if not best and suite_name:
        try:
            data = sonic_response_data(sonic_request(
                "GET",
                "/testSuites/list",
                params={"projectId": project_id, "name": suite_name, "page": 1, "pageSize": 20},
                timeout=10
            )) or {}
            items = data if isinstance(data, list) else extract_page_items(data)
            for item in items:
                if isinstance(item, dict):
                    best = item
                    break
        except Exception:
            pass

    if best:
        meta = sonic_suite_definition_meta_from_dto(best, "/testSuites/listAll")
        meta["project_id"] = project_id
        if not meta.get("expected_total_count") and meta.get("suite_id"):
            try:
                detail = sonic_response_data(sonic_request("GET", "/testSuites", params={"id": meta["suite_id"]}, timeout=10)) or {}
                if isinstance(detail, dict):
                    detail_meta = sonic_suite_definition_meta_from_dto(detail, "/testSuites?id")
                    if detail_meta.get("expected_total_count"):
                        detail_meta["project_id"] = project_id
                        return detail_meta
            except Exception as e:
                meta["detail_error"] = str(e)
        return meta

    return {
        "project_id": project_id,
        "suite_id": suite_id,
        "suite_name": suite_name,
        "error": "未在 Sonic 测试套列表中匹配到当前测试套",
        "source": "/testSuites/listAll",
    }


def attach_sonic_suite_definition_from_api(suite_key, suite):
    try:
        detail = lookup_sonic_suite_definition_for_suite(suite) or {}
    except Exception as e:
        detail = {"error": str(e)}
    if detail.get("error"):
        suite["sonic_suite_definition_error"] = detail.get("error", "")
        append_sonic_notify_log("sonic_suite_definition_lookup_missed", {"suite_key": suite_key, **detail})
        return suite
    suite["sonic_suite_definition"] = detail
    expected = safe_int(detail.get("expected_total_count") or detail.get("case_count"), 0)
    if expected:
        suite["expected_total_count"] = max(safe_int(suite.get("expected_total_count"), 0), expected)
    if detail.get("suite_id") and not suite.get("sonic_suite_id"):
        suite["sonic_suite_id"] = str(detail.get("suite_id"))
    if detail.get("suite_name") and not suite.get("sonic_suite_name"):
        suite["sonic_suite_name"] = detail.get("suite_name")
    suite["sonic_suite_definition_error"] = ""
    append_sonic_notify_log("sonic_suite_definition_attached", {"suite_key": suite_key, **detail})
    return suite


def sonic_error_is_unauthorized(error):
    text = str(error or "").lower()
    return "unauthorized" in text or "401" in text or "403" in text or "无权限" in text or "未授权" in text


def sonic_results_permission_error(project_id, error):
    token_probe = sonic_probe_token()
    if token_probe.get("auth_status") != "ok":
        return {
            "project_id": project_id,
            "error": token_probe.get("error") or token_probe.get("message") or "Sonic token 未通过鉴权",
            "raw_error": str(error),
            "token_probe": token_probe,
        }
    try:
        sonic_list_projects()
        return {
            "project_id": project_id,
            "error": "Sonic token 有效，但当前账号角色没有 /results/list（查询测试结果列表）资源权限；请在 Sonic 权限配置里给该角色增加该资源权限",
            "raw_error": str(error),
            "token_probe": token_probe,
        }
    except Exception as project_error:
        return {
            "project_id": project_id,
            "error": sonic_auth_failure_message(),
            "raw_error": str(error),
            "project_check_error": str(project_error),
        }


def sonic_suite_expected_name(suite):
    values = []
    values.append(suite.get("sonic_suite_name") or suite.get("suite_name") or "")
    for item in suite.get("results") or []:
        values.extend([
            item.get("sonic_suite_name") or "",
            item.get("module") or "",
        ])
    seen = []
    for value in values:
        value = re.sub(r"\s+", "", str(value or ""))
        if value and value not in seen:
            seen.append(value)
    return seen


def sonic_result_timestamp(result):
    return (
        parse_time(result.get("endTime") or result.get("end_time"))
        or parse_time(result.get("createTime") or result.get("create_time"))
        or 0
    )


def format_duration_seconds(seconds):
    seconds = safe_int(seconds, 0)
    if seconds <= 0:
        return ""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remain = seconds % 60
    if hours:
        return f"{hours}小时{minutes}分{remain}秒"
    if minutes:
        return f"{minutes}分{remain}秒"
    return f"{remain}秒"


def sonic_suite_time_range(suite):
    suite = suite or {}
    meta = suite.get("sonic_result_meta") or {}
    start = parse_time(meta.get("createTime") or meta.get("create_time") or meta.get("started_at"))
    end = parse_time(meta.get("endTime") or meta.get("end_time") or meta.get("finished_at"))
    if start and end and end >= start:
        return start, end, "sonic_result_meta"

    completion = suite.get("sonic_completion") or {}
    start = parse_time(completion.get("createTime") or completion.get("create_time") or completion.get("started_at"))
    end = parse_time(completion.get("endTime") or completion.get("end_time") or completion.get("finished_at"))
    if start and end and end >= start:
        return start, end, "sonic_completion"

    results = list(suite.get("results") or [])
    starts = [
        parse_time(item.get("started_at") or item.get("created_at"))
        for item in results
        if parse_time(item.get("started_at") or item.get("created_at"))
    ]
    ends = [
        parse_time(item.get("finished_at") or item.get("created_at"))
        for item in results
        if parse_time(item.get("finished_at") or item.get("created_at"))
    ]
    if starts and ends and max(ends) >= min(starts):
        return min(starts), max(ends), "task_callbacks"
    return 0, 0, ""


def sonic_suite_duration_text(suite):
    start, end, _ = sonic_suite_time_range(suite)
    if start and end and end >= start:
        return format_duration_seconds(int(end - start))
    duration = str(((suite or {}).get("sonic_completion") or {}).get("duration") or "").strip()
    return sonic_notify_clean_text(duration, fallback="") if duration else ""


def sonic_result_status_text(result):
    if not isinstance(result, dict):
        return ""
    parts = []
    for key in (
        "statusName", "status_name", "statusText", "status_text",
        "stateName", "state_name", "stateText", "state_text",
        "runStatus", "run_status", "result", "message", "msg", "remark",
    ):
        value = result.get(key)
        if value not in (None, ""):
            parts.append(str(value))
    status = result.get("status")
    if isinstance(status, str):
        parts.append(status)
    return "\n".join(parts).strip()


def sonic_result_is_finished(result):
    send_count = safe_int(result.get("sendMsgCount") or result.get("send_msg_count"), 0)
    receive_count = safe_int(result.get("receiveMsgCount") or result.get("receive_msg_count"), 0)
    # Sonic 2.7.2 may mark a suite FAIL as soon as one detail fails. Its
    # ResultsService only sets endTime when every dispatched result returns.
    return bool(send_count and receive_count >= send_count)


def sonic_result_time_score(result, suite):
    result_ts = sonic_result_timestamp(result)
    suite_start = safe_int(suite.get("created_ts"), 0)
    suite_end = safe_int(suite.get("last_update_ts"), 0) or int(time.time())
    if not result_ts:
        return -1
    if suite_start and suite_end:
        window_start = suite_start - sonic_report_window_before_seconds()
        window_end = suite_end + sonic_report_window_after_seconds()
        if not (window_start <= result_ts <= window_end):
            return -1
        return 5000 - min(abs(result_ts - suite_end), 5000)
    if suite_end:
        return max(0, 1200 - abs(result_ts - suite_end))
    return -1


def sonic_score_result_for_suite(result, suite, project_id):
    result_id = safe_int(result.get("id"), 0)
    if not result_id:
        return -1
    expected_suite_id = sonic_suite_config_id(suite)
    result_suite_id = safe_int(result.get("suiteId") or result.get("suite_id"), 0)
    if expected_suite_id and result_suite_id and expected_suite_id != result_suite_id:
        return -1
    if not sonic_result_is_finished(result):
        return -1
    time_score = sonic_result_time_score(result, suite)
    if time_score < 0:
        return -1
    score = 0
    if safe_int(result.get("projectId") or result.get("project_id"), project_id) == project_id:
        score += 100
    if expected_suite_id and result_suite_id == expected_suite_id:
        score += 3000
    suite_names = sonic_suite_expected_name(suite)
    result_suite_name = re.sub(r"\s+", "", str(result.get("suiteName") or result.get("suite_name") or ""))
    if result_suite_name:
        if result_suite_name in suite_names:
            score += 2000
        elif any(name and (name in result_suite_name or result_suite_name in name) for name in suite_names):
            score += 600
    status = safe_int(result.get("status"), 0)
    if status:
        score += 50
    else:
        score -= 1000
    send_count = safe_int(result.get("sendMsgCount") or result.get("send_msg_count"), 0)
    receive_count = safe_int(result.get("receiveMsgCount") or result.get("receive_msg_count"), 0)
    if send_count and receive_count >= send_count:
        score += 120
    score += time_score
    return score


def sonic_score_result_meta_for_suite(result, suite, project_id):
    result_id = safe_int(result.get("id"), 0)
    if not result_id:
        return -1
    expected_suite_id = sonic_suite_config_id(suite)
    result_suite_id = safe_int(result.get("suiteId") or result.get("suite_id"), 0)
    if expected_suite_id and result_suite_id and expected_suite_id != result_suite_id:
        return -1
    time_score = sonic_result_time_score(result, suite)
    if time_score < 0:
        return -1
    score = time_score
    if safe_int(result.get("projectId") or result.get("project_id"), project_id) == project_id:
        score += 100
    if expected_suite_id and result_suite_id == expected_suite_id:
        score += 3000
    suite_names = sonic_suite_expected_name(suite)
    result_suite_name = re.sub(r"\s+", "", str(result.get("suiteName") or result.get("suite_name") or ""))
    if result_suite_name:
        if result_suite_name in suite_names:
            score += 2000
        elif any(name and (name in result_suite_name or result_suite_name in name) for name in suite_names):
            score += 600
    elif suite_names:
        score -= 120
    send_count = safe_int(result.get("sendMsgCount") or result.get("send_msg_count"), 0)
    receive_count = safe_int(result.get("receiveMsgCount") or result.get("receive_msg_count"), 0)
    if send_count:
        score += 260
    if receive_count:
        score += min(receive_count, 50)
    if sonic_result_is_finished(result):
        score += 80
    return score


def lookup_sonic_result_meta_for_suite(suite):
    app = suite.get("app") or sonic_suite_app_info(suite.get("app_package", ""), "")
    package = suite.get("app_package") or app.get("package", "")
    project_id = sonic_project_id_for_app(app) or sonic_project_id_for_package(package)
    if not project_id:
        return {"error": "未找到 Sonic 项目 ID"}
    best = None
    best_score = -1
    candidates = []
    for page in range(1, 4):
        try:
            items = sonic_list_results(project_id, page=page, page_size=15)
        except Exception as e:
            if sonic_error_is_unauthorized(e):
                return sonic_results_permission_error(project_id, e)
            return {"project_id": project_id, "error": str(e)}
        if not items:
            break
        for item in items:
            score = sonic_score_result_meta_for_suite(item, suite, project_id)
            item_id = safe_int(item.get("id"), 0)
            if item_id:
                candidates.append({
                    "id": item_id,
                    "suiteId": item.get("suiteId") or item.get("suite_id"),
                    "suiteName": item.get("suiteName") or item.get("suite_name") or "",
                    "status": item.get("status"),
                    "statusText": sonic_result_status_text(item),
                    "sendMsgCount": item.get("sendMsgCount") or item.get("send_msg_count"),
                    "receiveMsgCount": item.get("receiveMsgCount") or item.get("receive_msg_count"),
                    "createTime": item.get("createTime") or item.get("create_time") or "",
                    "endTime": item.get("endTime") or item.get("end_time") or "",
                    "finished": sonic_result_is_finished(item),
                    "score": score,
                })
            if score > best_score:
                best = item
                best_score = score
    if best and best_score > 0:
        result_id = safe_int(best.get("id"), 0)
        send_count = safe_int(best.get("sendMsgCount") or best.get("send_msg_count"), 0)
        receive_count = safe_int(best.get("receiveMsgCount") or best.get("receive_msg_count"), 0)
        return {
            "project_id": project_id,
            "result_id": result_id,
            "suite_id": safe_int(best.get("suiteId") or best.get("suite_id"), 0),
            "score": best_score,
            "suite_name": best.get("suiteName") or best.get("suite_name") or "",
            "send_msg_count": send_count,
            "receive_msg_count": receive_count,
            "expected_total_count": send_count,
            "sonic_report_url": sonic_result_detail_url(project_id, result_id),
            "status": best.get("status"),
            "status_text": sonic_result_status_text(best),
            "finished": sonic_result_is_finished(best),
            "createTime": best.get("createTime") or best.get("create_time") or "",
            "endTime": best.get("endTime") or best.get("end_time") or "",
            "candidates": candidates[:8],
        }
    return {"project_id": project_id, "error": "未匹配到 Sonic 测试结果", "candidates": candidates[:8]}


def attach_sonic_result_meta_from_api(suite_key, suite):
    try:
        detail = lookup_sonic_result_meta_for_suite(suite) or {}
    except Exception as e:
        detail = {"error": str(e)}
    if detail.get("error"):
        suite["sonic_result_meta_error"] = detail.get("error", "")
        append_sonic_notify_log("sonic_result_meta_lookup_missed", {"suite_key": suite_key, **detail})
        return suite
    suite["sonic_result_meta"] = detail
    expected = safe_int(detail.get("expected_total_count") or detail.get("send_msg_count"), 0)
    if expected:
        suite["expected_total_count"] = max(safe_int(suite.get("expected_total_count"), 0), expected)
    if detail.get("suite_id") and not suite.get("sonic_suite_id"):
        suite["sonic_suite_id"] = str(detail.get("suite_id"))
    if detail.get("finished") and detail.get("sonic_report_url") and not suite.get("sonic_report_url"):
        suite["sonic_report_url"] = detail.get("sonic_report_url")
    suite["sonic_result_meta_error"] = ""
    append_sonic_notify_log("sonic_result_meta_attached", {"suite_key": suite_key, **detail})
    return suite


def sonic_suite_result_key_from_meta(meta):
    meta = meta or {}
    project_id = safe_int(meta.get("project_id") or meta.get("projectId"), 0)
    result_id = safe_int(meta.get("result_id") or meta.get("resultId") or meta.get("id"), 0)
    if project_id and result_id:
        return f"sonic_result_{project_id}_{result_id}"
    return ""


def mark_sonic_suite_completed_from_result_meta(suite):
    meta = (suite or {}).get("sonic_result_meta") or {}
    if not meta.get("finished"):
        return suite
    suite["completion_received"] = True
    suite["completion_source"] = "sonic_results_api"
    suite["completion_ts"] = suite.get("completion_ts") or time.strftime("%Y-%m-%d %H:%M:%S")
    project_id = safe_int(meta.get("project_id"), 0)
    result_id = safe_int(meta.get("result_id"), 0)
    if project_id:
        suite["sonic_project_id"] = project_id
    if result_id:
        suite["sonic_result_id"] = result_id
    if meta.get("suite_id") and not suite.get("sonic_suite_id"):
        suite["sonic_suite_id"] = str(meta.get("suite_id"))
    if meta.get("suite_name") and not suite.get("sonic_suite_name"):
        suite["sonic_suite_name"] = meta.get("suite_name")
    if meta.get("sonic_report_url"):
        suite["sonic_report_url"] = meta.get("sonic_report_url")
    expected = safe_int(meta.get("expected_total_count") or meta.get("send_msg_count"), 0)
    if expected:
        suite["expected_total_count"] = max(safe_int(suite.get("expected_total_count"), 0), expected)
    return suite


def merge_sonic_suite_results(left, right):
    merged = []
    seen = set()
    for item in list((left or {}).get("results") or []) + list((right or {}).get("results") or []):
        identity = sonic_suite_result_identity(item) or f"anon:{len(merged)}"
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(item)
    return merged


def migrate_sonic_suite_to_result_key(state, suite_key, suite):
    canonical_key = sonic_suite_result_key_from_meta((suite or {}).get("sonic_result_meta") or {})
    if not canonical_key or canonical_key == suite_key:
        return suite_key, suite
    suites = state.setdefault("suites", {})
    existing = suites.get(canonical_key)
    if existing and existing is not suite:
        merged = dict(existing)
        for key, value in suite.items():
            if key == "results":
                continue
            if value not in ("", None, [], {}):
                if key in ("sent_at", "send_error", "completion_final_sent", "feishu") and existing.get(key):
                    continue
                merged[key] = value
        merged["suite_key"] = canonical_key
        merged["results"] = merge_sonic_suite_results(existing, suite)
        suites[canonical_key] = merged
        suite = merged
    else:
        suite["suite_key"] = canonical_key
        suites[canonical_key] = suite
    if suite_key in suites and suite_key != canonical_key:
        suites.pop(suite_key, None)
        try:
            old_timer = SONIC_SUITE_TIMERS.pop(suite_key, None)
            if old_timer:
                old_timer.cancel()
        except Exception:
            pass
        append_sonic_notify_log("suite_summary_migrated_to_result_key", {
            "old_suite_key": suite_key,
            "suite_key": canonical_key,
        })
    return canonical_key, suite


def lookup_sonic_report_for_suite(suite):
    app = suite.get("app") or sonic_suite_app_info(suite.get("app_package", ""), "")
    package = suite.get("app_package") or app.get("package", "")
    project_id = sonic_project_id_for_app(app) or sonic_project_id_for_package(package)
    if not project_id:
        return "", {"error": "未找到 Sonic 项目 ID"}
    best = None
    best_score = -1
    candidates = []
    for page in range(1, 4):
        try:
            items = sonic_list_results(project_id, page=page, page_size=15)
        except Exception as e:
            if sonic_error_is_unauthorized(e):
                return "", sonic_results_permission_error(project_id, e)
            raise
        if not items:
            break
        for item in items:
            score = sonic_score_result_for_suite(item, suite, project_id)
            item_id = safe_int(item.get("id"), 0)
            if item_id:
                candidates.append({
                    "id": item_id,
                    "suiteId": item.get("suiteId") or item.get("suite_id"),
                    "suiteName": item.get("suiteName") or item.get("suite_name") or "",
                    "status": item.get("status"),
                    "sendMsgCount": item.get("sendMsgCount") or item.get("send_msg_count"),
                    "receiveMsgCount": item.get("receiveMsgCount") or item.get("receive_msg_count"),
                    "createTime": item.get("createTime") or item.get("create_time") or "",
                    "endTime": item.get("endTime") or item.get("end_time") or "",
                    "finished": sonic_result_is_finished(item),
                    "score": score,
                })
            if score > best_score:
                best = item
                best_score = score
    if best and best_score > 0:
        result_id = safe_int(best.get("id"), 0)
        return sonic_result_detail_url(project_id, result_id), {
            "project_id": project_id,
            "result_id": result_id,
            "suite_id": safe_int(best.get("suiteId") or best.get("suite_id"), 0),
                "score": best_score,
                "suite_name": best.get("suiteName") or best.get("suite_name") or "",
                "send_msg_count": safe_int(best.get("sendMsgCount") or best.get("send_msg_count"), 0),
                "receive_msg_count": safe_int(best.get("receiveMsgCount") or best.get("receive_msg_count"), 0),
                "createTime": best.get("createTime") or best.get("create_time") or "",
                "endTime": best.get("endTime") or best.get("end_time") or "",
                "candidates": candidates[:8],
            }
    return "", {"project_id": project_id, "error": "未匹配到 Sonic 测试结果", "candidates": candidates[:8]}


def attach_sonic_report_from_api(suite_key, suite):
    fixed_report_url = ensure_sonic_suite_report_url(suite)
    if fixed_report_url:
        append_sonic_notify_log("sonic_report_fixed_url_attached", {
            "suite_key": suite_key,
            "sonic_report_url": fixed_report_url,
        })
        return suite
    if suite.get("sonic_report_url"):
        return suite
    attempts = sonic_report_lookup_retries()
    interval = sonic_report_lookup_interval()
    last_detail = {}
    for attempt in range(1, attempts + 1):
        try:
            report_url, detail = lookup_sonic_report_for_suite(suite)
            detail = detail or {}
            detail["attempt"] = attempt
            detail["max_attempt"] = attempts
            last_detail = detail
            if report_url:
                suite["sonic_report_url"] = report_url
                suite["sonic_report_lookup"] = detail
                append_sonic_notify_log("sonic_report_lookup_attached", {"suite_key": suite_key, **detail})
                return suite
            append_sonic_notify_log("sonic_report_lookup_pending", {"suite_key": suite_key, **detail})
        except Exception as e:
            last_detail = {"attempt": attempt, "max_attempt": attempts, "error": str(e)}
            append_sonic_notify_log("sonic_report_lookup_error", {"suite_key": suite_key, "attempt": attempt, "max_attempt": attempts}, error=str(e))
            if "401" in str(e) or "403" in str(e) or "Unauthorized" in str(e):
                break
        if attempt < attempts:
            time.sleep(interval)
    suite["sonic_report_lookup"] = last_detail
    if last_detail.get("error"):
        suite["sonic_report_lookup_error"] = last_detail.get("error", "")
    append_sonic_notify_log("sonic_report_lookup_missed", {"suite_key": suite_key, **last_detail})
    return suite


def send_sonic_suite_summary_if_quiet(suite_key):
    quiet = sonic_suite_quiet_seconds()
    with SONIC_SUITE_LOCK:
        state = load_sonic_suite_results()
        suite = (state.get("suites") or {}).get(suite_key)
        if not suite:
            SONIC_SUITE_TIMERS.pop(suite_key, None)
            return
        now_ts = int(time.time())
        if suite.get("superseded_by"):
            SONIC_SUITE_TIMERS.pop(suite_key, None)
            append_sonic_notify_log("suite_summary_superseded", {
                "suite_key": suite_key,
                "superseded_by": suite.get("superseded_by"),
            })
            return
        if sonic_suite_is_legacy_mixed_completion(suite_key, suite) and not suite.get("sent_at"):
            suite["notification_suppressed_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            suite["notification_suppressed_reason"] = "历史套件结果与 Sonic resultId 混绑，已停止发送；等待按 resultId 生成的最终汇总"
            state.setdefault("suites", {})[suite_key] = suite
            save_sonic_suite_results(state)
            SONIC_SUITE_TIMERS.pop(suite_key, None)
            append_sonic_notify_log("suite_summary_legacy_mixed_completion_suppressed", {
                "suite_key": suite_key,
                "count": len(suite.get("results") or []),
                "sonic_result_id": suite.get("sonic_result_id") or "",
            })
            return
        if suite.get("completion_final_sent") and suite.get("sent_at") and not suite.get("send_error"):
            SONIC_SUITE_TIMERS.pop(suite_key, None)
            append_sonic_notify_log("suite_summary_already_sent_final_refresh_only", {
                "suite_key": suite_key,
                "count": len(suite.get("results") or []),
                "sent_count": safe_int(suite.get("sent_count"), 0),
            })
            return
        last_ts = safe_int(suite.get("last_update_ts"), 0)
        if not suite.get("completion_received") and last_ts and now_ts - last_ts < quiet:
            delay = max(10, quiet - (now_ts - last_ts))
            timer = threading.Timer(delay, send_sonic_suite_summary_if_quiet, args=(suite_key,))
            timer.daemon = True
            SONIC_SUITE_TIMERS[suite_key] = timer
            timer.start()
            return
        if not suite.get("completion_received") and sonic_suite_has_running_jobs(suite):
            delay = sonic_suite_running_check_delay_seconds()
            timer = threading.Timer(delay, send_sonic_suite_summary_if_quiet, args=(suite_key,))
            timer.daemon = True
            SONIC_SUITE_TIMERS[suite_key] = timer
            timer.start()
            return
        pending_reports = sonic_suite_pending_midscene_reports(suite)
        if pending_reports and sonic_suite_can_wait_for_pending_midscene_reports(suite, now_ts):
            delay = sonic_midscene_report_check_delay_seconds()
            timer = threading.Timer(delay, send_sonic_suite_summary_if_quiet, args=(suite_key,))
            timer.daemon = True
            SONIC_SUITE_TIMERS[suite_key] = timer
            timer.start()
            append_sonic_notify_log("suite_summary_wait_midscene_reports", {
                "suite_key": suite_key,
                "pending_reports": pending_reports,
                "grace_seconds": sonic_midscene_report_grace_seconds(),
            })
            return
        if suite.get("sent_at") and not suite.get("send_error") and not sonic_suite_waits_for_completion_event(suite):
            SONIC_SUITE_TIMERS.pop(suite_key, None)
            return
        send_started_ts = safe_int(suite.get("send_started_ts"), 0)
        if suite.get("send_in_progress") and now_ts - send_started_ts < 120:
            SONIC_SUITE_TIMERS.pop(suite_key, None)
            append_sonic_notify_log("suite_summary_send_already_in_progress", {
                "suite_key": suite_key,
                "count": len(suite.get("results") or []),
            })
            return
        app = suite.get("app") or sonic_suite_app_info(suite.get("app_package", ""), "")
        try:
            webhook = task_app_feishu_webhook(app)
        except ValueError as e:
            webhook = ""
            suite["send_error"] = str(e)
            state["suites"][suite_key] = suite
            save_sonic_suite_results(state)
            SONIC_SUITE_TIMERS.pop(suite_key, None)
            append_sonic_notify_log(
                "suite_summary_error",
                {
                    "suite_key": suite_key,
                    "app_package": suite.get("app_package", ""),
                    "app_name": suite.get("app_name", ""),
                    "count": len(suite.get("results") or []),
                },
                error=suite["send_error"]
            )
            return
        if not webhook:
            suite["send_error"] = "未配置应用飞书机器人 Webhook"
            state["suites"][suite_key] = suite
            save_sonic_suite_results(state)
            SONIC_SUITE_TIMERS.pop(suite_key, None)
            append_sonic_notify_log(
                "suite_summary_error",
                {
                    "suite_key": suite_key,
                    "app_package": suite.get("app_package", ""),
                    "app_name": suite.get("app_name", ""),
                    "count": len(suite.get("results") or []),
                },
                error=suite["send_error"]
            )
            return
        suite["send_in_progress"] = True
        suite["send_started_ts"] = now_ts
        state.setdefault("suites", {})[suite_key] = suite
        save_sonic_suite_results(state)
    suite_report_url = ""
    suite_report_error = ""
    sonic_report_url = ""
    sonic_report_lookup = {}
    sonic_report_lookup_error = ""
    sonic_result_meta = {}
    sonic_result_meta_error = ""
    sonic_suite_definition = {}
    sonic_suite_definition_error = ""
    expected_total_count = 0
    try:
        suite = attach_sonic_suite_definition_from_api(suite_key, suite)
        sonic_suite_definition = suite.get("sonic_suite_definition") or {}
        sonic_suite_definition_error = suite.get("sonic_suite_definition_error") or ""
        expected_total_count = max(expected_total_count, safe_int(suite.get("expected_total_count"), 0))
        suite = attach_sonic_result_meta_from_api(suite_key, suite)
        suite = mark_sonic_suite_completed_from_result_meta(suite)
        sonic_result_meta = suite.get("sonic_result_meta") or {}
        sonic_result_meta_error = suite.get("sonic_result_meta_error") or ""
        expected_total_count = max(expected_total_count, safe_int(suite.get("expected_total_count"), 0))
        with SONIC_SUITE_LOCK:
            state = load_sonic_suite_results()
            latest = (state.get("suites") or {}).get(suite_key) or suite
            latest.update({
                "sonic_result_meta": sonic_result_meta or latest.get("sonic_result_meta", {}),
                "sonic_result_meta_error": sonic_result_meta_error,
                "expected_total_count": max(
                    safe_int(latest.get("expected_total_count"), 0),
                    safe_int(suite.get("expected_total_count"), 0),
                ),
                "completion_received": bool(suite.get("completion_received") or latest.get("completion_received")),
                "completion_source": suite.get("completion_source") or latest.get("completion_source", ""),
                "completion_ts": suite.get("completion_ts") or latest.get("completion_ts", ""),
                "sonic_project_id": suite.get("sonic_project_id") or latest.get("sonic_project_id", ""),
                "sonic_result_id": suite.get("sonic_result_id") or latest.get("sonic_result_id", ""),
                "sonic_report_url": suite.get("sonic_report_url") or latest.get("sonic_report_url", ""),
            })
            state.setdefault("suites", {})[suite_key] = latest
            suite_key, latest = migrate_sonic_suite_to_result_key(state, suite_key, latest)
            suite = latest
            save_sonic_suite_results(state)
        if (
            sonic_suite_waits_for_completion_event(suite)
            and not suite.get("completion_received")
            and not sonic_suite_finished_in_sonic(suite)
        ):
            with SONIC_SUITE_LOCK:
                state = load_sonic_suite_results()
                latest = (state.get("suites") or {}).get(suite_key) or suite
                latest["send_in_progress"] = False
                latest["send_started_ts"] = 0
                state.setdefault("suites", {})[suite_key] = latest
                save_sonic_suite_results(state)
            schedule_sonic_suite_summary(suite_key, delay=sonic_suite_running_check_delay_seconds())
            append_sonic_notify_log("suite_summary_wait_sonic_result_finished", {
                "suite_key": suite_key,
                "count": len(suite.get("results") or []),
                "result_id": (sonic_result_meta or {}).get("result_id") or "",
                "send_msg_count": (sonic_result_meta or {}).get("send_msg_count") or "",
                "receive_msg_count": (sonic_result_meta or {}).get("receive_msg_count") or "",
                "lookup_error": sonic_result_meta_error,
            })
            return
        pre_report_stats = sonic_suite_display_stats(suite)
        if pre_report_stats.get("pending") and not sonic_suite_finished_in_sonic(suite) and sonic_suite_can_wait_for_running_jobs(suite, int(time.time())):
            with SONIC_SUITE_LOCK:
                state = load_sonic_suite_results()
                latest = (state.get("suites") or {}).get(suite_key) or suite
                latest.update({
                    "sonic_result_meta": suite.get("sonic_result_meta") or latest.get("sonic_result_meta", {}),
                    "sonic_result_meta_error": suite.get("sonic_result_meta_error", ""),
                    "expected_total_count": max(
                        safe_int(latest.get("expected_total_count"), 0),
                        safe_int(suite.get("expected_total_count"), 0),
                    ),
                    "send_in_progress": False,
                    "send_started_ts": 0,
                })
                state.setdefault("suites", {})[suite_key] = latest
                save_sonic_suite_results(state)
            schedule_sonic_suite_summary(suite_key)
            append_sonic_notify_log("suite_summary_wait_expected_from_results_api", {
                "suite_key": suite_key,
                "expected": pre_report_stats.get("expected_total"),
                "received": pre_report_stats.get("actual_total"),
                "pending": pre_report_stats.get("pending")
            })
            return
        suite = attach_sonic_report_from_api(suite_key, suite)
        sonic_report_url = suite.get("sonic_report_url") or ""
        sonic_report_lookup = suite.get("sonic_report_lookup") or {}
        sonic_report_lookup_error = suite.get("sonic_report_lookup_error") or ""
        sonic_result_meta = suite.get("sonic_result_meta") or sonic_result_meta
        sonic_result_meta_error = suite.get("sonic_result_meta_error") or sonic_result_meta_error
        sonic_suite_definition = suite.get("sonic_suite_definition") or sonic_suite_definition
        sonic_suite_definition_error = suite.get("sonic_suite_definition_error") or sonic_suite_definition_error
        expected_total_count = max(expected_total_count, safe_int(suite.get("expected_total_count"), 0))
        post_lookup_stats = sonic_suite_display_stats(suite)
        if post_lookup_stats.get("pending") and not sonic_suite_finished_in_sonic(suite) and sonic_suite_can_wait_for_running_jobs(suite, int(time.time())):
            with SONIC_SUITE_LOCK:
                state = load_sonic_suite_results()
                latest = (state.get("suites") or {}).get(suite_key) or suite
                latest.update({
                    "sonic_report_url": sonic_report_url or latest.get("sonic_report_url", ""),
                    "sonic_report_lookup": sonic_report_lookup or latest.get("sonic_report_lookup", {}),
                    "sonic_report_lookup_error": sonic_report_lookup_error or latest.get("sonic_report_lookup_error", ""),
                    "sonic_result_meta": sonic_result_meta or latest.get("sonic_result_meta", {}),
                    "sonic_result_meta_error": sonic_result_meta_error or latest.get("sonic_result_meta_error", ""),
                    "sonic_suite_definition": sonic_suite_definition or latest.get("sonic_suite_definition", {}),
                    "sonic_suite_definition_error": sonic_suite_definition_error or latest.get("sonic_suite_definition_error", ""),
                    "expected_total_count": max(
                        safe_int(latest.get("expected_total_count"), 0),
                        expected_total_count,
                    ),
                    "send_in_progress": False,
                    "send_started_ts": 0,
                })
                state.setdefault("suites", {})[suite_key] = latest
                save_sonic_suite_results(state)
            schedule_sonic_suite_summary(suite_key)
            append_sonic_notify_log("suite_summary_wait_expected_after_lookup", {
                "suite_key": suite_key,
                "expected": post_lookup_stats.get("expected_total"),
                "received": post_lookup_stats.get("actual_total"),
                "pending": post_lookup_stats.get("pending")
            })
            return
        try:
            suite_report_url = write_sonic_suite_summary_report(suite)
            suite["suite_report_url"] = suite_report_url
            suite["suite_report_error"] = ""
        except Exception as e:
            suite_report_error = str(e)
            suite["suite_report_error"] = suite_report_error
        card = build_sonic_suite_summary_card(suite)
        resp = post_feishu_card(webhook, card)
        send_error = ""
    except Exception as e:
        resp = {}
        send_error = str(e)
    with SONIC_SUITE_LOCK:
        state = load_sonic_suite_results()
        suite = (state.get("suites") or {}).get(suite_key) or suite
        if not send_error:
            suite["sent_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            suite["sent_count"] = len(suite.get("results") or [])
        suite["send_error"] = send_error
        suite["send_in_progress"] = False
        suite["send_started_ts"] = 0
        if suite_report_url:
            suite["suite_report_url"] = suite_report_url
        if suite_report_error:
            suite["suite_report_error"] = suite_report_error
        if sonic_report_url:
            suite["sonic_report_url"] = sonic_report_url
        if sonic_report_lookup:
            suite["sonic_report_lookup"] = sonic_report_lookup
        if sonic_report_lookup_error:
            suite["sonic_report_lookup_error"] = sonic_report_lookup_error
        if sonic_result_meta:
            suite["sonic_result_meta"] = sonic_result_meta
        if sonic_result_meta_error:
            suite["sonic_result_meta_error"] = sonic_result_meta_error
        if sonic_suite_definition:
            suite["sonic_suite_definition"] = sonic_suite_definition
        if sonic_suite_definition_error:
            suite["sonic_suite_definition_error"] = sonic_suite_definition_error
        if expected_total_count:
            suite["expected_total_count"] = max(safe_int(suite.get("expected_total_count"), 0), expected_total_count)
        if (
            not send_error
            and sonic_suite_waits_for_completion_event(suite)
            and (suite.get("completion_received") or sonic_suite_finished_in_sonic(suite))
        ):
            suite["completion_final_sent"] = True
        suite["feishu"] = resp
        state.setdefault("suites", {})[suite_key] = suite
        save_sonic_suite_results(state)
        SONIC_SUITE_TIMERS.pop(suite_key, None)
    append_sonic_notify_log(
        "suite_summary_sent" if not send_error else "suite_summary_error",
        {"suite_key": suite_key, "count": len(suite.get("results") or [])},
        result=resp,
        error=send_error
    )


def append_sonic_notify_log(event, payload=None, result=None, error=""):
    try:
        os.makedirs(LEARNING_DIR, exist_ok=True)
        safe_payload = payload if isinstance(payload, dict) else {"payload": payload}
        safe_payload = {
            k: v for k, v in safe_payload.items()
            if str(k).lower() not in ("token", "x-token", "secret", "sign", "signature", "password")
        }
        row = {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "event": event,
            "payload": safe_payload,
            "result": result if result is not None else {},
            "error": error or ""
        }
        with open(SONIC_NOTIFY_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"append_sonic_notify_log failed: {e}", flush=True)


def copy_or_move_task_file(src_module, src_file, dst_module, dst_file, move=False, overwrite=False):
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


def version_dir_for(module, file):
    return safe_join(VERSION_DIR, clean_id(module, "module"), clean_id(file, "file"))


def save_file_version(module, file, content=None, reason="manual"):
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


def list_file_versions(module, file, limit=30):
    try:
        vdir = version_dir_for(module, clean_filename(file))
    except ValueError:
        return []
    if not os.path.exists(vdir):
        return []
    result = []
    for name in os.listdir(vdir):
        if not name.endswith(".json"):
            continue
        meta = read_json_file(safe_join(vdir, name), default=None)
        if meta:
            result.append(meta)
    result.sort(key=lambda item: item.get("id", ""), reverse=True)
    return result[:limit]


def read_file_version(module, file, version_id):
    version_id = clean_id(version_id, "version")
    vdir = version_dir_for(module, clean_filename(file))
    meta = read_json_file(safe_join(vdir, f"{version_id}.json"), default=None)
    if not meta:
        raise FileNotFoundError("版本不存在")
    yaml_path = safe_join(vdir, meta.get("yaml") or f"{version_id}.yaml")
    with open(yaml_path, encoding="utf-8") as f:
        content = f.read()
    return meta, content


def safe_repair_artifact_dir(value):
    root = safe_join(LEARNING_DIR, "repairs")
    path = os.path.abspath(value or "")
    if not path:
        raise ValueError("repair_dir 不能为空")
    if path != root and not path.startswith(root + os.sep):
        raise ValueError("非法修复目录")
    return path


def normalize_device_list(devices):
    result = []
    for item in devices or []:
        if isinstance(item, str):
            device_id = item
            status = "online"
            meta = {}
        else:
            device_id = item.get("device_id") or item.get("deviceId") or item.get("id")
            status = item.get("status", "online")
            meta = item
        if not device_id:
            continue
        row = {
            "device_id": str(device_id),
            "status": status,
            "label": meta.get("label") or meta.get("model") or str(device_id),
            "brand": meta.get("brand", ""),
            "model": meta.get("model", "")
        }
        result.append(row)
    return result


def runner_device_ids(runner):
    return {
        dev.get("device_id")
        for dev in runner.get("devices", [])
        if dev.get("status") == "online" and dev.get("device_id")
    }


def all_online_devices():
    with RUNNER_LOCK:
        runners = load_runners()
    devices = []
    now = time.time()
    for runner_id, runner in runners.items():
        last_seen_ts = runner.get("last_seen_ts", 0)
        online = now - last_seen_ts <= 45
        for dev in runner.get("devices", []):
            row = dict(dev)
            row["runner_id"] = runner_id
            row["runner_online"] = online
            row["last_seen"] = runner.get("last_seen", "")
            devices.append(row)
    return devices


def annotate_job_queue_state(job, runners=None):
    row = dict(job)
    if row.get("status") != "pending":
        return row
    runners = runners if runners is not None else load_runners()
    now = time.time()
    target_runner = row.get("target_runner_id") or ""
    target_device = row.get("device_id") or ""
    online_runners = {
        runner_id: runner
        for runner_id, runner in runners.items()
        if now - runner.get("last_seen_ts", 0) <= 45
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
    if not online_runners:
        row["queue_message"] = "等待 Runner 在线"
    else:
        row["queue_message"] = "等待任一在线 Runner 拉取任务"
    return row


def new_job_id():
    return unique_millis_id("job")


def public_report_url(filename):
    return f"http://101.34.197.12:8088/reports/{urllib.parse.quote(filename)}"


def new_case_set_id():
    return unique_millis_id("cs")


def create_pending_job(module, file, auto_optimize=False, max_attempt=2, attempt=1, parent_job_id="", device_id="", runner_id="", run_mode="test", target_task_name=""):
    task_names = []
    try:
        with open(safe_join(TASK_DIR, module, file), encoding="utf-8") as f:
            task_names = yaml_task_names(f.read())
    except Exception:
        task_names = []
    if target_task_name:
        task_names = [target_task_name]
    job = {
        "job_id": new_job_id(),
        "module": module,
        "file": file,
        "status": "pending",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "attempt": attempt,
        "auto_optimize": safe_bool(auto_optimize),
        "run_mode": run_mode or "test",
        "max_attempt": max_attempt,
        "parent_job_id": parent_job_id,
        "device_id": device_id,
        "target_runner_id": runner_id,
        "target_task_name": target_task_name or "",
        "progress": 0,
        "current_task_name": task_names[0] if task_names else "",
        "current_task_index": 0,
        "completed_task_count": 0,
        "total_task_count": len(task_names),
        "task_names": task_names[:100]
    }
    with JOB_LOCK:
        jobs = load_jobs()
        jobs.append(job)
        save_jobs(jobs)
    update_task_meta(module, file, {
        "last_job_id": job["job_id"],
        "last_status": "pending",
        "last_target_task_name": target_task_name or "",
        "last_run_at": job["created_at"]
    })
    return job


def asset_meta_path(case_set_id):
    return safe_join(ASSET_DIR, case_set_id, "meta.json")


def cases_path(case_set_id):
    return safe_join(CASE_DIR, case_set_id, "cases.json")


def generation_summary_path(case_set_id):
    return safe_join(CASE_DIR, case_set_id, "summary.json")


def generation_summary_md_path(case_set_id):
    return safe_join(CASE_DIR, case_set_id, "summary.md")


def generation_mindmap_path(case_set_id):
    return safe_join(CASE_DIR, case_set_id, "cases.mm")


def generation_mindmap_deleted_path(case_set_id):
    return safe_join(CASE_DIR, case_set_id, ".mindmap_deleted")


def case_ui_design_dir(case_set_id):
    return safe_join(ASSET_DIR, case_set_id, "ui_designs")


def case_ui_design_meta_path(case_set_id):
    return safe_join(case_ui_design_dir(case_set_id), "meta.json")


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


def send_attachment(handler, body, filename, content_type):
    if isinstance(body, str):
        body = body.encode("utf-8")
    ascii_name = re.sub(r"[^A-Za-z0-9._-]+", "_", filename) or "download"
    quoted_name = urllib.parse.quote(filename)
    handler.send_response(200)
    handler._cors()
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Disposition", f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quoted_name}')
    handler.end_headers()
    try:
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        pass


def read_json_file(path, default=None):
    if not os.path.exists(path):
        return default
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        bad = f"{path}.bad.{int(time.time())}"
        try:
            shutil.copyfile(path, bad)
        except Exception:
            bad = ""
        print(f"read_json_file failed: {path}: {e}" + (f"; backup={bad}" if bad else ""))
        return default


def read_text(path, default=""):
    try:
        path = Path(path)
        if not path.exists():
            return default
        return path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return default


def read_text_file(path, default=""):
    return read_text(path, default=default)


def read_json(path, default=None):
    try:
        path = Path(path)
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return default


def html_text_to_plain(text, max_chars=12000):
    raw = str(text or "")
    if not raw:
        return ""
    raw = re.sub(r"(?is)<script\b.*?</script>", " ", raw)
    raw = re.sub(r"(?is)<style\b.*?</style>", " ", raw)
    raw = re.sub(r"(?is)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?is)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?is)</(div|p|li|tr|section|article|h[1-6])\s*>", "\n", raw)
    raw = re.sub(r"(?is)<[^>]+>", " ", raw)
    raw = html_lib.unescape(raw)
    lines = []
    for line in raw.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if line and line not in lines[-3:]:
            lines.append(line)
    plain = "\n".join(lines)
    if max_chars and len(plain) > max_chars:
        return plain[-max_chars:]
    return plain


def report_html_candidates_for_job(job):
    candidates = []
    run_dir = job.get("run_dir") or ""
    if run_dir:
        run_path = Path(run_dir)
        candidates.extend([
            run_path / "report.html",
            run_path / f"{job.get('job_id', '')}.html",
        ])
        try:
            candidates.extend(sorted(run_path.glob("**/*.html"), key=lambda item: item.stat().st_mtime, reverse=True)[:4])
        except Exception:
            pass
    job_id = job.get("job_id") or ""
    if job_id:
        candidates.append(Path(REPORT_DIR) / f"{job_id}.html")
    report_url = job.get("report_url") or ""
    if report_url:
        try:
            name = os.path.basename(urllib.parse.urlparse(report_url).path)
            if name:
                candidates.append(Path(REPORT_DIR) / urllib.parse.unquote(name))
        except Exception:
            pass
    local_report = job.get("local_report_path") or ""
    if local_report:
        try:
            candidates.append(Path(local_report))
        except Exception:
            pass
    unique = []
    seen = set()
    for path in candidates:
        try:
            key = str(path)
        except Exception:
            continue
        if key and key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def report_text_context(job, max_chars=12000):
    parts = []
    for path in report_html_candidates_for_job(job or {}):
        text = read_text(path, "")
        if not text:
            continue
        plain = html_text_to_plain(text, max_chars=max_chars)
        if plain:
            parts.append(f"[REPORT_TEXT:{Path(path).name}]\n{plain}")
    joined = "\n\n".join(parts)
    if len(joined) > max_chars:
        return joined[-max_chars:]
    return joined


def report_image_context(job, limit=4):
    collected = []
    seen = set()
    data_url_re = re.compile(
        r"data:(image/(?:png|jpe?g|webp));base64,([A-Za-z0-9+/=\\r\\n]+)",
        flags=re.I
    )
    for path in report_html_candidates_for_job(job or {}):
        text = read_text(path, "")
        if not text:
            continue
        for idx, match in enumerate(data_url_re.finditer(text), start=1):
            mime = match.group(1).lower().replace("image/jpg", "image/jpeg")
            b64 = re.sub(r"\s+", "", match.group(2))
            if len(b64) < 1000:
                continue
            key = b64[:80]
            if key in seen:
                continue
            try:
                data = base64.b64decode(b64, validate=False)
            except Exception:
                continue
            if not data or len(data) > 2 * 1024 * 1024:
                continue
            seen.add(key)
            collected.append({
                "name": f"{Path(path).name}-report-{idx}.png",
                "mime": mime,
                "base64": base64.b64encode(data).decode("ascii")
            })
    return collected[-limit:] if limit else collected


def write_json_file(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(target.suffix + f".tmp.{os.getpid()}.{threading.get_ident()}")
    bad = target.with_suffix(target.suffix + ".bad")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception:
        try:
            if tmp.exists():
                os.replace(tmp, bad)
        except Exception:
            pass
        raise


def write_text_file(path, text):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, f".{os.path.basename(path)}.tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text or "")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise


def write_bytes_file(path, data):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    tmp = os.path.join(directory, f".{os.path.basename(path)}.tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        with open(tmp, "wb") as f:
            f.write(data or b"")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        raise


def runtime_path_status(path):
    exists = os.path.exists(path)
    check_path = path if exists else os.path.dirname(path) or "."
    return {
        "path": path,
        "exists": exists,
        "is_dir": os.path.isdir(path),
        "writable": os.access(check_path, os.W_OK),
    }


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


def figma_proxy_url():
    return (
        os.getenv("FIGMA_PROXY")
        or os.getenv("FIGMA_HTTPS_PROXY")
        or os.getenv("HTTPS_PROXY")
        or os.getenv("https_proxy")
        or ""
    ).strip()


def urlopen_with_retry(url, headers=None, timeout=30, retries=0, binary=False, max_bytes=None):
    headers = headers or {}
    last_error = None
    opener = None
    proxy = figma_proxy_url() if "figma.com" in url or "figma" in url else ""
    if proxy:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    for attempt in range(max(1, retries + 1)):
        try:
            req = urllib.request.Request(url, headers=headers)
            open_fn = opener.open if opener else urllib.request.urlopen
            with open_fn(req, timeout=timeout) as resp:
                if binary:
                    if max_bytes:
                        data = resp.read(max_bytes + 1)
                    else:
                        data = resp.read()
                    if max_bytes and len(data) > max_bytes:
                        raise ValueError("图片过大，请选择更小的 Frame 或降低导出范围")
                    return data
                return resp.read().decode("utf-8")
        except (TimeoutError, socket.timeout, urllib.error.URLError) as e:
            last_error = e
            if attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise
    raise last_error


def urlopen_json(url, headers=None, timeout=30, retries=0):
    return json.loads(urlopen_with_retry(url, headers=headers, timeout=timeout, retries=retries))


def urlopen_bytes(url, headers=None, timeout=30, max_bytes=8 * 1024 * 1024, retries=0):
    data = urlopen_with_retry(url, headers=headers, timeout=timeout, retries=retries, binary=True, max_bytes=max_bytes)
    if len(data) > max_bytes:
        raise ValueError("图片过大，请选择更小的 Frame 或降低导出范围")
    return data


def markdown_cell(value):
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return text.replace("|", "｜") or "-"


def analysis_list(analysis, *keys):
    if not isinstance(analysis, dict):
        return []
    for key in keys:
        if key in analysis:
            return normalize_text_list(analysis.get(key))
    return []


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


def build_report_checkpoints(summary):
    analysis = summary.get("requirement_analysis") or {}
    goals = normalize_text_list(analysis.get("business_goals"))
    requirement_points = normalize_text_list(analysis.get("requirement_points"))
    risks = normalize_text_list(analysis.get("risks"))
    missing_inputs = normalize_text_list(analysis.get("missing_inputs"))
    questions = normalize_text_list(analysis.get("questions"))
    blockers = normalize_text_list(analysis.get("blockers"))
    visible_outcomes = normalize_text_list(analysis.get("visible_outcomes"))
    cases = [item for item in (summary.get("cases") or []) if isinstance(item, dict)]
    manual_cases = [item for item in (summary.get("manual_cases") or []) if isinstance(item, dict)]
    scenarios = [item for item in (summary.get("scenarios") or []) if isinstance(item, dict)]

    def tail_note(items, fallback):
        items = normalize_text_list(items)
        if not items:
            return fallback
        return "；".join(items[:3])

    def case_weight(item):
        priority = str(item.get("priority") or "").upper()
        return (
            2 if item.get("smoke") else 0,
            {"P0": 4, "P1": 3, "P2": 2, "P3": 1}.get(priority, 0),
            len(normalize_text_list(item.get("assertions"))),
        )

    def case_brief(item):
        title = item.get("title") or item.get("case_id") or "未命名用例"
        target = first_non_empty(
            item.get("coverage"),
            item.get("expected_result"),
            item.get("scenario"),
            item.get("business_path"),
            ""
        )
        return f"{title}（{target}）" if target else str(title)

    key_cases = sorted(cases, key=case_weight, reverse=True)
    positive_cases = [item for item in key_cases if not re.search(r"异常|失败|错误|弱网|超时|空态|边界|取消|返回|无|未", case_brief(item))]
    negative_cases = [item for item in key_cases if item not in positive_cases]
    risk_bits = risks + missing_inputs + questions + blockers
    manual_bits = [
        first_non_empty(item.get("title"), item.get("reason"), item.get("suggested_setup"), "")
        for item in manual_cases
    ]
    scenario_titles = [
        first_non_empty(item.get("name"), item.get("title"), item.get("scenario"), item.get("feature"), "")
        for item in scenarios
    ]

    checkpoints = [
        f"主流程验证：围绕「{summary.get('title') or '本次需求'}」确认核心业务目标是否达成，重点覆盖 {tail_note(requirement_points or goals, '需求主路径和验收目标')}。",
        f"页面与交互验证：确认关键 UI 可见结果、入口、按钮、弹窗、文案和状态流转符合预期，重点检查 {tail_note(visible_outcomes or scenario_titles, '页面可见结果、操作入口和状态提示')}。",
        f"关键用例验证：优先执行并记录 {tail_note([case_brief(item) for item in positive_cases[:3]] or [case_brief(item) for item in key_cases[:3]], 'P0/P1 和冒烟用例结果')}。",
        f"异常与边界验证：覆盖失败提示、空态/弱网/超时、返回/取消、重复操作和边界输入等风险路径，重点关注 {tail_note([case_brief(item) for item in negative_cases[:3]], tail_note(risk_bits, '需求中未明确的异常与边界场景'))}。",
        f"人工确认项：对自动化不稳定或需要造数/环境/后台/真实设备状态的内容单独记录结论，重点跟进 {tail_note(manual_bits or risk_bits, '当前暂无明确人工项，执行前仍需确认测试数据和环境稳定性')}。",
    ]
    return [re.sub(r"\s+", " ", item).strip() for item in checkpoints[:5]]


def mm_text(value):
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    text = re.sub(r"\n{3,}", "\n\n", text)
    return html_lib.escape(text, quote=True)


def mm_node(text, children=None, indent=0):
    pad = "  " * indent
    children = children or []
    if children:
        inner = "\n".join(children)
        return f'{pad}<node TEXT="{mm_text(text)}">\n{inner}\n{pad}</node>'
    return f'{pad}<node TEXT="{mm_text(text)}" />'


def scenario_key(value):
    return re.sub(r"\s+", "", str(value or "").strip().lower())


def scenario_method_text(scenario):
    methods = scenario.get("design_method") or scenario.get("designMethod") or scenario.get("method") or []
    methods = normalize_text_list(methods)
    if not methods:
        scenario_type = scenario.get("type") or scenario.get("scenario_type") or ""
        if "边界" in str(scenario_type):
            methods = ["边界值"]
        elif "异常" in str(scenario_type):
            methods = ["等价类", "错误推测"]
        else:
            methods = ["等价类"]
    return " / ".join(methods)


def case_mm_title(case):
    flags = []
    if case.get("priority"):
        flags.append(str(case.get("priority")))
    if case.get("smoke"):
        flags.append("flag=冒烟")
    suffix = f"（{'，'.join(flags)}）" if flags else ""
    return f"{case.get('case_id') or ''} {case.get('title') or '未命名用例'}{suffix}".strip()


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


def write_generation_mindmap(case_set_id, summary):
    mm_path = generation_mindmap_path(case_set_id)
    os.makedirs(os.path.dirname(mm_path), exist_ok=True)
    deleted_path = generation_mindmap_deleted_path(case_set_id)
    if os.path.exists(deleted_path):
        os.remove(deleted_path)
    write_text_file(mm_path, build_generation_mindmap(summary))
    return mm_path


def generation_mindmap_record(case_set_id):
    summary = read_json_file(generation_summary_path(case_set_id), default=None)
    if not isinstance(summary, dict):
        return None
    counts = summary.get("counts") or {}
    mm_path = generation_mindmap_path(case_set_id)
    deleted_path = generation_mindmap_deleted_path(case_set_id)
    exists = os.path.exists(mm_path)
    deleted = os.path.exists(deleted_path)
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
        "mindmap_downloadable": exists and not deleted,
        "mindmap_size": size,
        "mindmap_updated_at": updated_at,
    }


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
        record = generation_mindmap_record(name)
        if record:
            records.append(record)
    records.sort(key=lambda item: item.get("generated_at") or item.get("mindmap_updated_at") or "", reverse=True)
    return records[:max(1, min(500, limit))]


def report_cleanup_policy():
    return {
        "retention_days": REPORT_RETENTION_DAYS,
        "min_keep": REPORT_RETENTION_MIN_KEEP,
        "interval_seconds": REPORT_CLEANUP_INTERVAL_SECONDS,
        "cleanup_on_startup": REPORT_CLEANUP_ON_STARTUP,
        "report_dir": REPORT_DIR,
    }


def report_cleanup_candidates(retention_days=None, min_keep=None):
    retention_days = max(1, safe_int(retention_days, REPORT_RETENTION_DAYS))
    min_keep = max(0, safe_int(min_keep, REPORT_RETENTION_MIN_KEEP))
    cutoff = time.time() - retention_days * 86400
    html_files = []
    chunk_files = []
    if not os.path.isdir(REPORT_DIR):
        return [], [], {"total_html": 0, "kept_by_min_keep": 0, "cutoff": cutoff}
    try:
        for item in Path(REPORT_DIR).iterdir():
            try:
                if item.is_file() and item.suffix.lower() in (".html", ".htm"):
                    stat = item.stat()
                    html_files.append({"path": item, "mtime": stat.st_mtime, "size": stat.st_size})
            except Exception:
                continue
    except Exception:
        return [], [], {"total_html": 0, "kept_by_min_keep": 0, "cutoff": cutoff}
    html_files.sort(key=lambda item: item["mtime"], reverse=True)
    protected = {str(item["path"]) for item in html_files[:min_keep]}
    stale_html = [
        item for item in html_files
        if item["mtime"] < cutoff and str(item["path"]) not in protected
    ]
    chunk_root = Path(REPORT_DIR) / ".chunks"
    if chunk_root.exists():
        try:
            for item in chunk_root.iterdir():
                try:
                    stat = item.stat()
                    if stat.st_mtime < time.time() - 86400:
                        chunk_files.append({"path": item, "mtime": stat.st_mtime, "size": stat.st_size if item.is_file() else 0})
                except Exception:
                    continue
        except Exception:
            pass
    return stale_html, chunk_files, {
        "total_html": len(html_files),
        "kept_by_min_keep": min(len(html_files), min_keep),
        "cutoff": cutoff,
    }


def cleanup_midscene_reports(retention_days=None, min_keep=None, dry_run=False):
    stale_html, chunk_files, stats = report_cleanup_candidates(retention_days, min_keep)
    deleted = []
    errors = []
    reclaimed = 0
    for item in stale_html + chunk_files:
        path = item["path"]
        size = safe_int(item.get("size"), 0)
        record = {
            "path": str(path),
            "name": path.name,
            "size": size,
            "mtime": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(item.get("mtime") or 0)),
        }
        if dry_run:
            deleted.append(record)
            reclaimed += size
            continue
        try:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            elif path.exists():
                path.unlink()
            deleted.append(record)
            reclaimed += size
        except Exception as e:
            record["error"] = str(e)
            errors.append(record)
    return {
        "ok": not errors,
        "dry_run": safe_bool(dry_run),
        "policy": {
            **report_cleanup_policy(),
            "retention_days": max(1, safe_int(retention_days, REPORT_RETENTION_DAYS)),
            "min_keep": max(0, safe_int(min_keep, REPORT_RETENTION_MIN_KEEP)),
        },
        "stats": stats,
        "deleted_count": len(deleted) if not dry_run else 0,
        "candidate_count": len(deleted),
        "reclaimed_bytes": reclaimed,
        "items": deleted[:200],
        "errors": errors[:50],
        "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def report_cleanup_scheduler():
    if REPORT_CLEANUP_ON_STARTUP:
        try:
            cleanup_midscene_reports()
        except Exception as e:
            print(f"report cleanup startup failed: {e}")
    while True:
        time.sleep(REPORT_CLEANUP_INTERVAL_SECONDS)
        try:
            result = cleanup_midscene_reports()
            if result.get("deleted_count"):
                print(f"report cleanup deleted {result.get('deleted_count')} files, reclaimed {result.get('reclaimed_bytes')} bytes")
        except Exception as e:
            print(f"report cleanup failed: {e}")


def start_report_cleanup_scheduler():
    thread = threading.Thread(target=report_cleanup_scheduler, name="report-cleanup", daemon=True)
    thread.start()


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
        f"- 生成时间：{summary.get('generated_at')}",
        "",
    ]
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


def generate_job_id():
    return unique_millis_id("gen")


def generate_job_path(job_id):
    return safe_join(GENERATE_JOB_DIR, f"{job_id}.json")


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
        return read_json_file(generate_job_path(job_id), default=None)
    except Exception:
        return None


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


def sanitize_generate_job_for_client(job):
    if not isinstance(job, dict):
        return job
    safe = dict(job)
    request = safe.pop("request_data", None) or safe.pop("requestData", None)
    safe["can_retry"] = job.get("type") == "generate" and bool(generate_retry_request_from_job(job))
    if request:
        safe["request_summary"] = summarize_generate_request(request)
    if safe.get("error_trace"):
        safe["error_trace"] = str(safe.get("error_trace"))[-1200:]
    return safe


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
            rows.append(sanitize_generate_job_for_client(job))
    rows.sort(key=lambda item: item.get("updated_at") or item.get("created_at") or "", reverse=True)
    return rows[:limit]


def update_generate_job(job_id, **changes):
    with GENERATE_LOCK:
        job = read_json_file(generate_job_path(job_id), default={}) or {}
        job.setdefault("job_id", job_id)
        job.setdefault("ok", True)
        job.setdefault("created_at", time.strftime("%Y-%m-%d %H:%M:%S"))
        now_text = time.strftime("%Y-%m-%d %H:%M:%S")
        if changes.get("status") == "running" and not job.get("started_at"):
            changes.setdefault("started_at", now_text)
        if changes.get("status") in ("success", "failed", "cancelled"):
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


def generate_job_cancelled(job_id):
    job = load_generate_job(job_id) or {}
    return job.get("status") == "cancelled"


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


def guess_mime(filename):
    lower = filename.lower()
    if lower.endswith(".png"):
        return "image/png"
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".json"):
        return "application/json"
    if lower.endswith(".pdf"):
        return "application/pdf"
    if lower.endswith(".docx"):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if lower.endswith(".doc"):
        return "application/msword"
    if lower.endswith(".mm"):
        return "application/x-freemind"
    return "text/plain"


class BodyTooLarge(Exception):
    pass


REVOKED_SESSION_TOKENS = set()


def task_password_valid(password):
    # TODO: production should only use TASK_ADMIN_PASSWORD_HASH and remove plain TASK_ADMIN_PASSWORD fallback.
    raw = password or ""
    if TASK_ADMIN_PASSWORD_HASH:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return secrets.compare_digest(digest, TASK_ADMIN_PASSWORD_HASH)
    if TASK_ADMIN_PASSWORD:
        return secrets.compare_digest(raw, TASK_ADMIN_PASSWORD)
    return False


def sign_session_payload(payload):
    body = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")
    sig = hashlib.sha256((body + TASK_SESSION_SECRET).encode("utf-8")).hexdigest()
    return f"{body}.{sig}"


def verify_session_token(token):
    token = (token or "").strip()
    if not token or token in REVOKED_SESSION_TOKENS or "." not in token:
        return None
    body, sig = token.rsplit(".", 1)
    expected = hashlib.sha256((body + TASK_SESSION_SECRET).encode("utf-8")).hexdigest()
    if not secrets.compare_digest(sig, expected):
        return None
    try:
        padded = body + "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
    except Exception:
        return None
    if safe_int(payload.get("exp"), 0) < int(time.time()):
        return None
    if payload.get("user") != TASK_ADMIN_USER:
        return None
    return payload


def issue_session_token(username):
    now = int(time.time())
    return sign_session_payload({
        "user": username,
        "iat": now,
        "exp": now + max(300, TASK_SESSION_TTL_SECONDS),
        "nonce": secrets.token_hex(12),
    })


def bearer_token(headers):
    value = headers.get("Authorization", "")
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return ""


def supported_asset_file(filename):
    return filename.lower().endswith((
        ".txt", ".md", ".json", ".pdf", ".doc", ".docx", ".mm",
        ".png", ".jpg", ".jpeg"
    ))


def knowledge_app_dir(app_package):
    return safe_join(KNOWLEDGE_DIR, clean_id(app_package, DEFAULT_APP_PACKAGE))


def knowledge_page_dir(app_package, page_id):
    return safe_join(knowledge_app_dir(app_package), clean_id(page_id, "page"))


def knowledge_meta_path(app_package, page_id):
    return safe_join(knowledge_page_dir(app_package, page_id), "meta.json")


def normalize_lines(value):
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [line.strip(" -\t") for line in value.splitlines() if line.strip(" -\t")]
    return []


def normalize_knowledge_tier(value, default="test"):
    tier = str(value or default or "test").strip().lower()
    mapping = {
        "baseline": "baseline",
        "base": "baseline",
        "stable": "baseline",
        "基线": "baseline",
        "基线库": "baseline",
        "test": "test",
        "testing": "test",
        "draft": "test",
        "测试": "test",
        "测试库": "test",
        "草稿": "test"
    }
    return mapping.get(tier, "test")


def save_knowledge_page(data):
    app_package = data.get("app_package") or data.get("appPackage") or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    page_name = (data.get("page_name") or data.get("pageName") or "未命名页面").strip()
    page_id = clean_id(data.get("page_id") or data.get("pageId") or page_name)
    page_dir = knowledge_page_dir(app_package, page_id)
    os.makedirs(page_dir, exist_ok=True)

    screenshot = data.get("screenshot") or {}
    screenshot_name = ""
    if screenshot.get("contentBase64"):
        raw_name = clean_asset_filename(screenshot.get("name") or f"{page_id}.png")
        if not is_image_file(raw_name):
            raise ValueError("页面截图只支持 png / jpg / jpeg")
        screenshot_name = raw_name
        write_bytes_file(safe_join(page_dir, screenshot_name), base64.b64decode(screenshot["contentBase64"]))

    existed = read_json_file(knowledge_meta_path(app_package, page_id), default={}) or {}
    if not screenshot_name:
        screenshot_name = existed.get("screenshot", "")

    meta = {
        "app_package": app_package,
        "page_id": page_id,
        "page_name": page_name,
        "route": data.get("route", ""),
        "description": data.get("description", ""),
        "key_elements": normalize_lines(data.get("key_elements") or data.get("keyElements")),
        "common_assertions": normalize_lines(data.get("common_assertions") or data.get("commonAssertions")),
        "tags": normalize_lines(data.get("tags")),
        "tier": normalize_knowledge_tier(data.get("tier") or data.get("library") or existed.get("tier"), "test"),
        "screenshot": screenshot_name,
        "source": data.get("source") or ("figma" if data.get("figma") else existed.get("source", "manual")),
        "figma": data.get("figma") or existed.get("figma") or {},
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "created_at": existed.get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S")
    }
    write_json_file(knowledge_meta_path(app_package, page_id), meta)
    return meta


def list_knowledge_apps():
    if not os.path.exists(KNOWLEDGE_DIR):
        return []
    return sorted([
        name for name in os.listdir(KNOWLEDGE_DIR)
        if os.path.isdir(safe_join(KNOWLEDGE_DIR, name))
    ])


def task_app_map_by_package():
    result = {}
    try:
        for app in sonic_notify_known_apps():
            package = (app.get("package") or "").strip()
            if package:
                result[package] = app
    except Exception:
        pass
    return result


def list_knowledge_app_details():
    app_map = task_app_map_by_package()
    packages = set(list_knowledge_apps()) | set(app_map.keys())
    details = []
    for package in sorted(packages):
        app = app_map.get(package) or {}
        pages = list_knowledge_pages(package)
        details.append({
            "package": package,
            "name": app.get("name") or package,
            "modules": app.get("modules") or [],
            "page_count": len(pages),
            "test_count": len([page for page in pages if page.get("tier") != "baseline"]),
            "baseline_count": len([page for page in pages if page.get("tier") == "baseline"]),
            "has_knowledge": bool(pages),
            "source": "task-apps+knowledge" if app and pages else ("task-apps" if app else "knowledge")
        })
    return details


def list_knowledge_pages(app_package, tier="all"):
    app_dir = knowledge_app_dir(app_package)
    if not os.path.exists(app_dir):
        return []
    tier = normalize_knowledge_tier(tier, "") if tier and tier != "all" else "all"
    pages = []
    for page_id in sorted(os.listdir(app_dir)):
        meta = read_json_file(knowledge_meta_path(app_package, page_id), default=None)
        if meta:
            meta["tier"] = normalize_knowledge_tier(meta.get("tier"), "test")
            if tier != "all" and meta["tier"] != tier:
                continue
            pages.append(meta)
    return pages


def knowledge_page_text(meta):
    parts = [
        f"页面名称：{meta.get('page_name', '')}",
        f"知识库类型：{'基线库' if meta.get('tier') == 'baseline' else '测试库'}",
        f"到达路径：{meta.get('route', '')}",
        f"页面说明：{meta.get('description', '')}",
    ]
    if meta.get("key_elements"):
        parts.append("关键元素：\n" + "\n".join(f"- {item}" for item in meta["key_elements"]))
    if meta.get("common_assertions"):
        parts.append("常用断言：\n" + "\n".join(f"- {item}" for item in meta["common_assertions"]))
    if meta.get("tags"):
        parts.append("标签：" + "、".join(meta["tags"]))
    return "\n".join(part for part in parts if part.strip())


def load_knowledge_context(app_package, query_text, limit=5, selected_page_ids=None, tier="all"):
    pages = list_knowledge_pages(app_package, tier=tier)
    if not pages:
        return [], [], []
    query = (query_text or "").lower()
    selected_page_ids = [str(item) for item in (selected_page_ids or []) if str(item).strip()]

    def score(meta):
        source = " ".join([
            meta.get("page_name", ""),
            meta.get("route", ""),
            meta.get("description", ""),
            " ".join(meta.get("key_elements") or []),
            " ".join(meta.get("common_assertions") or []),
            " ".join(meta.get("tags") or []),
        ]).lower()
        points = 0
        for token in re.findall(r"[\w\u4e00-\u9fff]{2,}", source):
            if token and token in query:
                points += 1
        return points

    if selected_page_ids:
        manual = [page for page in pages if page.get("page_id") in selected_page_ids]
        remaining = [page for page in pages if page.get("page_id") not in selected_page_ids]
        ranked = sorted(remaining, key=score, reverse=True)
        selected = (manual + [page for page in ranked if score(page) > 0])[:limit]
    else:
        ranked = sorted(pages, key=score, reverse=True)
        selected = [page for page in ranked if score(page) > 0][:limit] or ranked[:min(3, limit)]
    text_assets = []
    image_assets = []
    for page in selected:
        text_assets.append("[APP页面知识]\n" + knowledge_page_text(page))
        screenshot = page.get("screenshot")
        if screenshot and len(image_assets) < 4:
            path = safe_join(knowledge_page_dir(app_package, page["page_id"]), screenshot)
            if os.path.exists(path):
                with open(path, "rb") as f:
                    image_assets.append({
                        "name": screenshot,
                        "mime": guess_mime(screenshot),
                        "base64": base64.b64encode(f.read()).decode("ascii")
                    })
    used_pages = [
        {
            "app_package": page.get("app_package"),
            "page_id": page.get("page_id"),
            "page_name": page.get("page_name"),
            "route": page.get("route", ""),
            "tier": page.get("tier", "test"),
            "screenshot": page.get("screenshot", "")
        }
        for page in selected
    ]
    return text_assets, image_assets, used_pages


def figma_token():
    return (os.getenv("FIGMA_TOKEN") or os.getenv("FIGMA_ACCESS_TOKEN") or "").strip()


def parse_figma_url(figma_url):
    raw = (figma_url or "").strip()
    if not raw:
        raise ValueError("Figma 链接不能为空")
    parsed = urllib.parse.urlparse(raw)
    parts = [part for part in parsed.path.split("/") if part]
    file_key = ""
    for key in ("file", "design", "proto"):
        if key in parts:
            idx = parts.index(key)
            if idx + 1 < len(parts):
                file_key = parts[idx + 1]
                break
    qs = urllib.parse.parse_qs(parsed.query)
    node_id = (qs.get("node-id") or qs.get("node_id") or [""])[0].replace("-", ":")
    if not file_key:
        raise ValueError("无法从 Figma 链接中解析 file key，请复制 design/file 链接")
    return file_key, node_id


def figma_api_json(path, query=None):
    token = figma_token()
    if not token:
        raise ValueError("未配置 FIGMA_TOKEN")
    base = os.getenv("FIGMA_API_BASE", DEFAULT_FIGMA_API_BASE).rstrip("/")
    url = base + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    try:
        return urlopen_json(url, headers={"X-Figma-Token": token}, timeout=FIGMA_TIMEOUT_SECONDS, retries=FIGMA_RETRY_COUNT)
    except Exception as e:
        proxy_hint = "当前未配置 FIGMA_PROXY/HTTPS_PROXY。" if not figma_proxy_url() else f"当前代理：{figma_proxy_url()}"
        raise ValueError(f"连接 Figma API 失败：{e}。腾讯云大陆服务器访问 Figma 可能不稳定，请配置海外代理或海外中转服务。{proxy_hint}")


def figma_node_texts(node, limit=60):
    texts = []

    def walk(item):
        if len(texts) >= limit or not isinstance(item, dict):
            return
        if item.get("type") == "TEXT":
            text = (item.get("characters") or item.get("name") or "").strip()
            if text and text not in texts:
                texts.append(text)
        for child in item.get("children") or []:
            walk(child)

    walk(node)
    return texts


def figma_node_size(node):
    box = node.get("absoluteBoundingBox") or {}
    width = float(box.get("width") or node.get("width") or 0)
    height = float(box.get("height") or node.get("height") or 0)
    return width, height


def figma_node_area(node):
    width, height = figma_node_size(node)
    return width * height


def figma_child_count(node):
    count = 0

    def walk(item):
        nonlocal count
        if not isinstance(item, dict):
            return
        for child in item.get("children") or []:
            count += 1
            walk(child)

    walk(node)
    return count


def figma_find_node_path(root, node_id):
    target = str(node_id or "")
    if not target:
        return []
    path = []

    def walk(node):
        if not isinstance(node, dict):
            return False
        path.append(node)
        if node.get("id") == target:
            return True
        for child in node.get("children") or []:
            if walk(child):
                return True
        path.pop()
        return False

    return path if walk(root) else []


def figma_nearest_page_root(path):
    if not path:
        return None
    for node in reversed(path):
        if node.get("type") == "FRAME" and figma_page_score(node, int(node.get("_figma_depth") or 0)) >= 3:
            return node
    for node in reversed(path):
        if node.get("type") == "CANVAS":
            return node
    return path[-1]


def figma_direct_node_needs_parent_lookup(root):
    if not isinstance(root, dict):
        return False
    node_type = root.get("type") or ""
    if node_type in {"CANVAS", "FRAME", "SECTION", "COMPONENT", "INSTANCE"} and (root.get("children") or []):
        return False
    return True


def figma_canvas_name(path):
    for node in path or []:
        if node.get("type") == "CANVAS":
            return node.get("name") or ""
    return ""


def normalize_figma_name(value):
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s+copy\s*\d*$", "", text, flags=re.I)
    text = re.sub(r"^(frame|group|section|screen|page|页面|画板)\s*[-_:#]?\s*", "", text, flags=re.I).strip()
    return text[:60]


def is_generic_figma_name(value):
    text = normalize_figma_name(value).lower()
    if not text:
        return True
    generic = (
        "frame", "group", "section", "screen", "page", "untitled", "copy",
        "iphone", "android", "mobile", "desktop", "标题", "title", "header", "navbar",
        "button", "按钮", "组件", "component", "编组"
    )
    if any(text == key or text.startswith(key + " ") for key in generic):
        return True
    if re.fullmatch(r"[\d\s._#:-]+", text):
        return True
    return False


def figma_likely_title_text(texts):
    bad = ("确定", "取消", "返回", "更多", "保存", "关闭", "编辑", "删除", "完成", "下一步")
    for text in texts or []:
        text = str(text or "").strip()
        if not text or len(text) > 24 or text in bad:
            continue
        if re.fullmatch(r"[\d\s._#:-]+", text):
            continue
        return text
    return ""


def figma_page_name(frame, canvas_name=""):
    raw_name = normalize_figma_name(frame.get("name") or "")
    texts = figma_node_texts(frame, limit=30)
    title = figma_likely_title_text(texts)
    if is_generic_figma_name(raw_name):
        return normalize_figma_name(title or canvas_name or raw_name or "Figma页面") or "Figma页面"
    if canvas_name and raw_name.lower() in ("home", "profile", "mine", "list", "detail"):
        return normalize_figma_name(f"{canvas_name}-{raw_name}")
    return raw_name


def figma_device_profile(width, height):
    width = float(width or 0)
    height = float(height or 0)
    long_edge = max(width, height)
    short_edge = min(width, height)
    if long_edge >= 900 and short_edge >= 600:
        return "tablet"
    if short_edge <= 520 and long_edge <= 980:
        return "phone"
    if long_edge >= 900:
        return "wide"
    return "unknown"


def figma_device_label(profile):
    return {
        "tablet": "平板",
        "phone": "手机",
        "wide": "宽屏",
        "unknown": "未知端"
    }.get(profile or "unknown", "未知端")


def figma_variant_signature(node):
    texts = figma_node_texts(node, limit=80)
    blob = " ".join(texts)
    variants = []
    color_words = ("红色", "蓝色", "绿色", "黄色", "紫色", "白色", "黑色", "灰色", "橙色", "粉色", "透明")
    for color in color_words:
        if color in blob and color not in variants:
            variants.append(color)
    # Keep exact material labels when present, e.g. 官方耗材-红色.
    color_pattern = "|".join(color_words)
    for match in re.findall(rf"(?:官方|默认|当前|选择)?耗材[-：: ]?(?:{color_pattern})", blob):
        match = match.strip(" ，,。；;")
        if match and match not in variants:
            variants.append(match[:24])
    if variants:
        return "+".join(variants[:4])
    title = figma_likely_title_text(texts)
    return normalize_figma_name(title or node.get("name") or "")[:30]


def figma_page_score(node, depth=0):
    name = (node.get("name") or "").lower()
    width, height = figma_node_size(node)
    area = width * height
    texts = figma_node_texts(node, limit=80)
    children = figma_child_count(node)
    score = 0
    if width >= 280 and height >= 480:
        score += 4
    elif width >= 240 and height >= 360:
        score += 2
    if area >= 180000:
        score += 2
    if 0.28 <= (width / height if height else 0) <= 2.4:
        score += 1
    if len(texts) >= 3:
        score += 2
    if len(texts) >= 8:
        score += 1
    if children >= 8:
        score += 1
    if any(key in name for key in ("首页", "我的", "登录", "详情", "列表", "页面", "画板", "screen", "page", "home", "profile", "detail", "list")):
        score += 2
    if any(key in name for key in ("title", "标题", "header", "navbar", "tab", "button", "按钮", "icon", "组件", "component", "编组")):
        score -= 4
    if height < 180 or width < 180:
        score -= 6
    if depth > 8:
        score -= 1
    return score


def figma_collect_visual_nodes(root, limit=500, canvas_name=""):
    visual_types = {"FRAME", "COMPONENT", "INSTANCE", "SECTION"}
    candidates = []
    direct_root = bool(root.get("_figma_direct_link"))
    direct_context_text = " ".join([
        str(root.get("name") or ""),
        " ".join(figma_node_texts(root, limit=40))
    ]).strip()

    def walk_children(parent, depth=0, parent_name="", parent_id="", current_canvas="", in_direct_group=False):
        if parent.get("type") == "CANVAS":
            current_canvas = parent.get("name") or current_canvas
        next_direct_group = bool(in_direct_group or parent.get("_figma_direct_link") or parent.get("_figma_direct_group"))
        for child in parent.get("children") or []:
            if len(candidates) >= limit:
                return
            child["_figma_depth"] = depth + 1
            child["_figma_parent_name"] = parent_name
            child["_figma_parent_id"] = parent_id
            child["_figma_canvas_name"] = current_canvas or canvas_name
            if next_direct_group:
                child["_figma_direct_group"] = True
                child["_figma_direct_context"] = direct_context_text
            if child.get("type") in visual_types:
                candidates.append(child)
            walk_children(child, depth + 1, child.get("name") or parent_name, child.get("id") or parent_id, current_canvas, next_direct_group)

    if root.get("type") in visual_types:
        root["_figma_depth"] = 0
        root["_figma_parent_name"] = ""
        root["_figma_parent_id"] = ""
        root["_figma_canvas_name"] = canvas_name
        if direct_root:
            root["_figma_direct_group"] = True
            root["_figma_direct_context"] = direct_context_text
        candidates.append(root)
    walk_children(root, 0, root.get("name") or "", root.get("id") or "", canvas_name, direct_root)
    return candidates[:limit]


def figma_frame_candidates(root, limit=12, mode="smart", min_width=240, min_height=360, pinned_node_ids=None):
    mode = (mode or "smart").strip().lower()
    pinned_node_ids = {str(item) for item in (pinned_node_ids or []) if str(item or "").strip()}
    root_canvas_name = root.get("_figma_canvas_name") or (root.get("name") if root.get("type") == "CANVAS" else "")
    visual_nodes = figma_collect_visual_nodes(root, canvas_name=root_canvas_name)
    if not visual_nodes:
        return []

    enriched = []
    for node in visual_nodes:
        width, height = figma_node_size(node)
        depth = int(node.get("_figma_depth") or 0)
        score = figma_page_score(node, depth)
        enriched.append({
            "node": node,
            "width": width,
            "height": height,
            "area": width * height,
            "depth": depth,
            "score": score,
            "text_count": len(figma_node_texts(node, limit=100)),
            "child_count": figma_child_count(node)
        })
    for item in enriched:
        node_id = str(item["node"].get("id") or "")
        item["pinned"] = bool(node_id and node_id in pinned_node_ids) or bool(item["node"].get("_figma_direct_link"))

    if mode in ("all", "loose"):
        selected = [
            item for item in enriched
            if item["width"] >= max(120, min_width * 0.5) and item["height"] >= max(120, min_height * 0.35)
        ]
    else:
        selected = [
            item for item in enriched
            if item["node"].get("type") == "FRAME"
            and item["width"] >= min_width
            and item["height"] >= min_height
            and item["score"] >= 3
        ]
        if not selected:
            selected = [item for item in enriched if item["node"].get("type") == "FRAME" and item["score"] >= 3]
    pinned_selected = [item for item in enriched if item.get("pinned")]
    if pinned_selected:
        pinned_ids = {item["node"].get("id") for item in pinned_selected}
        selected = pinned_selected + [item for item in selected if item["node"].get("id") not in pinned_ids]

    # If a wrapper contains many page-sized children, importing the children is more useful than importing the wrapper.
    selected_ids = {item["node"].get("id") for item in selected}
    child_like_ids = set()
    for item in selected:
        node = item["node"]
        for other in selected:
            if other is item:
                continue
            parent_id = other["node"].get("_figma_parent_id") or ""
            if item.get("pinned"):
                continue
            if parent_id and parent_id == node.get("id") and other["area"] >= item["area"] * 0.15:
                child_like_ids.add(node.get("id"))
    deduped = [item for item in selected if item["node"].get("id") not in child_like_ids or len(selected_ids) == 1]

    deduped.sort(key=lambda item: (1 if item.get("pinned") else 0, item["score"], item["area"], -item["depth"]), reverse=True)
    result = []
    seen_names = set()
    for item in deduped:
        node = item["node"]
        page_name = figma_page_name(node, node.get("_figma_canvas_name") or "")
        device_profile = figma_device_profile(item["width"], item["height"])
        variant_signature = figma_variant_signature(node)
        key = (page_name, device_profile, round(item["width"]), round(item["height"]), variant_signature)
        if key in seen_names:
            continue
        seen_names.add(key)
        node["_figma_score"] = item["score"]
        node["_figma_width"] = item["width"]
        node["_figma_height"] = item["height"]
        node["_figma_device_profile"] = device_profile
        node["_figma_variant_signature"] = variant_signature
        node["_figma_text_count"] = item["text_count"]
        node["_figma_pinned"] = bool(item.get("pinned"))
        result.append(node)
        if len(result) >= limit and not any(
            other.get("pinned") and other["node"].get("id") not in {row.get("id") for row in result}
            for other in deduped
        ):
            break
    return result


def figma_image_map(file_key, node_ids):
    node_ids = [node_id for node_id in node_ids if node_id]
    if not node_ids or not FIGMA_IMAGE_EXPORT:
        return {}
    data = figma_api_json(f"/images/{urllib.parse.quote(file_key)}", {
        "ids": ",".join(node_ids),
        "format": "png",
        "scale": "1"
    })
    return data.get("images") or {}


def download_figma_screenshots(drafts, images, max_workers=4):
    if not drafts or not images:
        return
    jobs = []
    for index, draft in enumerate(drafts):
        node_id = (draft.get("figma") or {}).get("node_id") or ""
        image_url = images.get(node_id) or ""
        if image_url:
            jobs.append((index, draft, image_url))
    if not jobs:
        return

    def fetch(job):
        index, draft, image_url = job
        try:
            image_bytes = urlopen_bytes(
                image_url,
                timeout=FIGMA_TIMEOUT_SECONDS,
                max_bytes=3 * 1024 * 1024,
                retries=FIGMA_RETRY_COUNT
            )
            name_bits = [
                draft.get("page_name") or "page",
                (draft.get("figma") or {}).get("device_label") or "",
                (draft.get("figma") or {}).get("variant") or "",
            ]
            image_name = "-".join([str(item) for item in name_bits if item])
            return index, {
                "name": clean_asset_filename(f"figma-{clean_id(image_name)}.png"),
                "mime": "image/png",
                "contentBase64": base64.b64encode(image_bytes).decode("ascii")
            }
        except Exception as exc:
            return index, {"_error": str(exc)}

    worker_count = max(1, min(int(max_workers or 4), 6, len(jobs)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        for index, screenshot in executor.map(fetch, jobs):
            if screenshot.get("_error"):
                drafts[index]["screenshot"] = {}
                drafts[index].setdefault("figma", {})["screenshot_error"] = screenshot.get("_error")
            else:
                drafts[index]["screenshot"] = screenshot


def figma_frame_to_draft(app_package, figma_url, file_key, frame, image_url="", canvas_name=""):
    node_id = frame.get("id") or ""
    canvas_name = canvas_name or frame.get("_figma_canvas_name") or ""
    name = figma_page_name(frame, canvas_name)
    texts = figma_node_texts(frame)
    width, height = figma_node_size(frame)
    device_profile = frame.get("_figma_device_profile") or figma_device_profile(width, height)
    variant_signature = frame.get("_figma_variant_signature") or figma_variant_signature(frame)
    key_elements = texts[:30]
    assertions = [f"页面展示「{text}」" for text in texts[:8]]
    if not assertions:
        assertions = [f"页面展示「{name}」相关内容"]
    screenshot = {}
    if image_url:
        try:
            image_bytes = urlopen_bytes(image_url, timeout=FIGMA_TIMEOUT_SECONDS, max_bytes=3 * 1024 * 1024, retries=FIGMA_RETRY_COUNT)
            screenshot = {
                "name": clean_asset_filename(f"figma-{clean_id(name)}.png"),
                "mime": "image/png",
                "contentBase64": base64.b64encode(image_bytes).decode("ascii")
            }
        except Exception:
            screenshot = {}
    return {
        "app_package": app_package,
        "page_id": clean_id(name),
        "page_name": name,
        "tier": "test",
        "route": f"Figma 设计稿：{canvas_name + ' / ' if canvas_name else ''}{name}",
        "description": "从 Figma 设计稿导入的页面知识。" + (f" 可见文案：{'、'.join(texts[:12])}" if texts else ""),
        "key_elements": key_elements,
        "common_assertions": assertions,
        "tags": ["Figma", "设计稿"],
        "figma": {
            "file_key": file_key,
            "node_id": node_id,
            "url": figma_url,
            "type": frame.get("type") or "",
            "name": frame.get("name") or name,
            "page_name": name,
            "canvas_name": canvas_name,
            "width": round(width),
            "height": round(height),
            "device_profile": device_profile,
            "device_label": figma_device_label(device_profile),
            "variant": variant_signature,
            "score": frame.get("_figma_score", 0),
            "text_count": frame.get("_figma_text_count", len(texts)),
            "pinned": bool(frame.get("_figma_pinned") or frame.get("_figma_direct_link")),
            "direct_group": bool(frame.get("_figma_direct_group")),
            "direct_context": frame.get("_figma_direct_context") or ""
        },
        "screenshot": screenshot
    }


def figma_draft_search_blob(draft):
    figma = draft.get("figma") or {}
    parts = [
        draft.get("page_name", ""),
        draft.get("description", ""),
        " ".join(normalize_lines(draft.get("key_elements") or draft.get("keyElements"))),
        " ".join(normalize_lines(draft.get("common_assertions") or draft.get("commonAssertions"))),
        " ".join(normalize_lines(draft.get("tags"))),
        figma.get("name", ""),
        figma.get("page_name", ""),
        figma.get("canvas_name", ""),
        figma.get("direct_context", ""),
    ]
    return " ".join(str(part or "") for part in parts)


def figma_requirement_terms(query_text):
    raw_text = str(query_text or "").lower()
    terms = []
    non_chinese_tokens = re.findall(r"[a-z0-9_./-]{2,}", raw_text)
    for token in non_chinese_tokens:
        if not re.fullmatch(r"\d+", token):
            terms.append(token)
    stop = {
        "页面", "功能", "用户", "展示", "进入", "点击", "验证", "正常", "流程", "场景",
        "可以", "进行", "是否", "相关", "测试", "按钮", "入口", "列表", "内容",
        "查看", "打开", "支持", "完成", "实现", "需要", "能够", "应该", "对应",
        "增加", "新增", "修改", "优化"
    }
    useful_short_terms = {
        "登录", "注册", "搜索", "筛选", "排序", "支付", "退款", "下单", "订单", "购物",
        "发票", "地址", "上传", "下载", "分享", "收藏", "点赞", "评论", "审核", "审批",
        "权限", "角色", "配置", "报表", "统计", "导入", "导出", "同步", "回调", "通知",
        "弹窗", "提示", "确认", "取消", "颜色", "耗材", "打印", "模型", "图片", "语音",
        "识别", "生成", "预览", "提交", "编辑", "删除", "保存", "失败", "成功", "异常",
        "边界", "弱网", "缓存", "分页", "刷新", "排队", "并发", "超时"
    }
    chinese_parts = re.findall(r"[\u4e00-\u9fff]{2,}", raw_text)
    for part in chinese_parts:
        part = part.strip()
        if part in stop:
            continue
        if 2 <= len(part) <= 8:
            terms.append(part)
        hits = []
        for term in useful_short_terms:
            idx = part.find(term)
            if idx >= 0:
                hits.append((idx, term))
        hits.sort()
        for _idx, term in hits:
            terms.append(term)
        for (_idx_a, term_a), (idx_b, term_b) in zip(hits, hits[1:]):
            if term_a in stop or term_b in stop:
                continue
            joined = part[_idx_a:idx_b + len(term_b)]
            if 2 <= len(joined) <= 8:
                terms.append(joined)
    return list(dict.fromkeys(terms))[:120]


def score_figma_draft_for_requirement(draft, query_text):
    terms = figma_requirement_terms(query_text)
    if not terms:
        return 0, []
    blob = figma_draft_search_blob(draft).lower()
    if not blob:
        return 0, []
    matched = []
    score = 0
    generic_terms = {
        "打印", "确认", "页面", "按钮", "入口", "弹窗", "状态", "提示", "结果", "流程", "任务",
        "模型", "颜色", "上传", "生成", "查看", "点击", "首页", "列表", "详情", "设置", "验证",
        "ai", "ui", "app", "android", "ios"
    }
    important_terms = [
        term for term in terms
        if len(term) >= 2 and term not in generic_terms and not term.isdigit()
    ]
    for term in terms:
        if term in blob:
            matched.append(term)
            score += 1
            if len(term) >= 4:
                score += 1
    page_name = str(draft.get("page_name") or "").lower()
    query = str(query_text or "").lower()
    for term in terms:
        if term and term in page_name:
            score += 4
    if page_name and page_name in query:
        score += 5
    figma = draft.get("figma") or {}
    canvas_name = str(figma.get("canvas_name") or "").lower()
    if canvas_name and canvas_name in query:
        score += 1
    matched_important = [term for term in matched if term in important_terms]
    if important_terms and not matched_important:
        # Do not let generic words such as "打印/确认/页面" pull unrelated
        # frames into visual grounding when the requirement contains stronger
        # business terms like "耗材/余额/会员/语音识别".
        score = min(score, 2)
        matched = matched[:6] + ["缺少核心需求词"]
    return score, matched[:12]


def filter_figma_drafts_for_requirement(drafts, query_text, limit=12, min_score=1, fallback_on_no_match=False, pinned_node_ids=None, max_limit=24):
    if not drafts:
        return [], []
    limit = max(1, int(limit or 12))
    max_limit = max(limit, int(max_limit or limit))
    pinned_node_ids = {str(item) for item in (pinned_node_ids or []) if str(item or "").strip()}
    terms = figma_requirement_terms(query_text)
    if not terms:
        selected = drafts[:max(1, min(limit, len(drafts)))]
        for draft in selected:
            figma = draft.setdefault("figma", {})
            figma["relevance_score"] = figma.get("relevance_score", 0)
            figma["relevance_reason"] = "未提供明确需求关键词，保留前几个页面作为弱参考"
        ignored = drafts[len(selected):]
        return selected, ignored

    scored = []
    for draft in drafts:
        score, matched = score_figma_draft_for_requirement(draft, query_text)
        figma = draft.setdefault("figma", {})
        node_id = str(figma.get("node_id") or draft.get("page_id") or "")
        pinned = bool(node_id and node_id in pinned_node_ids) or bool(figma.get("pinned"))
        if pinned:
            score = max(score, 99)
            if "链接直指节点" not in matched:
                matched = ["链接直指节点"] + matched
        elif figma.get("direct_group"):
            score = max(score, min_score)
            if "来自直链设计范围" not in matched:
                matched = matched[:8] + ["来自直链设计范围"]
        figma["relevance_score"] = score
        figma["relevance_terms"] = matched
        figma["pinned"] = pinned
        figma["relevance_reason"] = (
            "Figma 链接直接指定该节点，已强制保留为主要 UI 参考"
            if pinned else (
                "该页面位于用户粘贴的 Figma 直链设计范围内，作为本次需求 UI 参考保留"
                if figma.get("direct_group") else (f"匹配需求关键词：{'、'.join(matched[:8])}" if matched else "未匹配到需求关键词，生成时不作为主要 UI 参考")
            )
        )
        scored.append((score, draft))

    pinned_drafts = [draft for _score, draft in scored if (draft.get("figma") or {}).get("pinned")]
    top_score = max([score for score, _draft in scored] or [0])
    dynamic_min_score = min_score
    sorted_scored = sorted(scored, key=lambda item: item[0], reverse=True)
    matched_pairs = [(score, draft) for score, draft in sorted_scored if score >= dynamic_min_score and draft not in pinned_drafts]
    matched = [draft for _score, draft in matched_pairs]
    strong_variant_count = 0
    if top_score >= 8:
        strong_threshold = max(dynamic_min_score, int(top_score * 0.75))
        strong_variant_count = len([draft for score, draft in matched_pairs if score >= strong_threshold])
    if pinned_drafts or matched:
        selected_limit = min(max_limit, max(limit, strong_variant_count + len(pinned_drafts)))
        remaining_limit = max(0, selected_limit - len(pinned_drafts))
        selected = pinned_drafts + matched[:min(remaining_limit, len(matched))]
    elif fallback_on_no_match:
        selected = [draft for _score, draft in sorted(scored, key=lambda item: item[0], reverse=True)[:max(1, min(2, limit, len(scored)))]]
        for draft in selected:
            figma = draft.setdefault("figma", {})
            figma["relevance_reason"] = "未命中需求关键词，仅作为低置信度兜底参考；建议复制具体 Frame 链接或补充需求说明"
    else:
        selected = []
    selected_ids = {((draft.get("figma") or {}).get("node_id") or draft.get("page_id")) for draft in selected}
    ignored = [draft for _score, draft in scored if (((draft.get("figma") or {}).get("node_id") or draft.get("page_id")) not in selected_ids)]
    return selected, ignored


def parse_figma_design(data):
    started_at = time.time()
    stage_started_at = started_at
    stage_timing_ms = {}

    def mark_stage(name):
        nonlocal stage_started_at
        now = time.time()
        stage_timing_ms[name] = int((now - stage_started_at) * 1000)
        stage_started_at = now

    figma_url = data.get("figma_url") or data.get("figmaUrl") or data.get("url") or ""
    app_package = data.get("app_package") or data.get("appPackage") or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    limit = max(1, min(int(data.get("limit") or FIGMA_PARSE_LIMIT), 120))
    mode = data.get("mode") or data.get("parse_mode") or data.get("parseMode") or "smart"
    min_width = max(1, int(data.get("min_width") or data.get("minWidth") or 240))
    min_height = max(1, int(data.get("min_height") or data.get("minHeight") or 360))
    requirement_query = data.get("requirement_query") or data.get("requirementQuery") or ""
    filter_by_requirement = safe_bool(data.get("filter_by_requirement", data.get("filterByRequirement", True)))
    reference_limit = max(1, min(int(data.get("reference_limit") or data.get("referenceLimit") or FIGMA_REFERENCE_LIMIT), limit, FIGMA_MAX_REFERENCE_LIMIT))
    max_reference_limit = max(reference_limit, min(int(data.get("max_reference_limit") or data.get("maxReferenceLimit") or FIGMA_MAX_REFERENCE_LIMIT), limit, 120))
    file_key, node_id = parse_figma_url(figma_url)
    canvas_name = ""
    selected_node_id = node_id
    file_name = ""
    root = None
    if node_id:
        nodes_payload = figma_api_json(f"/files/{urllib.parse.quote(file_key)}/nodes", {"ids": node_id})
        mark_stage("nodes_api")
        node_wrap = (nodes_payload.get("nodes") or {}).get(node_id) or {}
        root = node_wrap.get("document")
        file_name = nodes_payload.get("name") or ""
        # Full-file parent lookup is useful for links copied from a tiny title/text node.
        # Keep it automatic only for non-container nodes; normal canvas/frame links stay fast.
        if FIGMA_PARENT_LOOKUP or figma_direct_node_needs_parent_lookup(root):
            try:
                payload = figma_api_json(f"/files/{urllib.parse.quote(file_key)}")
                mark_stage("parent_lookup_api")
                document = payload.get("document")
                file_name = payload.get("name") or file_name
                path = figma_find_node_path(document, node_id) if document else []
                if path:
                    canvas_name = figma_canvas_name(path)
                    parent_root = figma_nearest_page_root(path)
                    if parent_root:
                        root = parent_root
                        if root.get("id") != node_id:
                            selected_node_id = root.get("id") or node_id
            except Exception:
                pass
    else:
        payload = figma_api_json(f"/files/{urllib.parse.quote(file_key)}")
        mark_stage("file_api")
        root = payload.get("document")
        file_name = payload.get("name") or ""
    if not root:
        raise ValueError("没有读取到 Figma 节点，请确认链接权限和 node-id 是否正确")
    pinned_node_ids = {node_id, selected_node_id, root.get("id") if isinstance(root, dict) else ""} if node_id else set()
    pinned_node_ids = {str(item) for item in pinned_node_ids if str(item or "").strip()}
    if node_id and isinstance(root, dict):
        root["_figma_direct_link"] = True
    if canvas_name:
        root["_figma_canvas_name"] = canvas_name
    candidate_limit = limit
    if requirement_query and filter_by_requirement:
        # Do not let early geometric ranking discard later but highly relevant
        # frames in a large canvas. Screenshots are exported only after filtering,
        # so collecting more metadata here is cheap enough and much more accurate.
        candidate_limit = max(limit, min(120, max(max_reference_limit * 6, reference_limit * 10, 40)))
    frames = figma_frame_candidates(root, limit=candidate_limit, mode=mode, min_width=min_width, min_height=min_height, pinned_node_ids=pinned_node_ids)
    mark_stage("frame_candidates")
    if not frames:
        raise ValueError("没有找到可导入的页面级 Frame。可以尝试选择更上层节点，或把解析模式改为“宽松”。")
    drafts = []
    for frame in frames:
        drafts.append(figma_frame_to_draft(
            app_package,
            figma_url,
            file_key,
            frame,
            "",
            frame.get("_figma_canvas_name") or canvas_name
        ))
    mark_stage("draft_metadata")
    ignored_drafts = []
    if requirement_query and filter_by_requirement:
        filter_limit = reference_limit
        min_score = max(0, int(data.get("min_relevance_score") or data.get("minRelevanceScore") or 1))
        fallback_on_no_match = bool(node_id and len(drafts) <= 3)
        drafts, ignored_drafts = filter_figma_drafts_for_requirement(
            drafts,
            requirement_query,
            limit=filter_limit,
            min_score=min_score,
            fallback_on_no_match=fallback_on_no_match,
            pinned_node_ids=pinned_node_ids,
            max_limit=max_reference_limit
        )
        mark_stage("requirement_filter")
    images = figma_image_map(file_key, [((draft.get("figma") or {}).get("node_id") or "") for draft in drafts])
    mark_stage("image_export")
    download_figma_screenshots(
        drafts,
        images,
        max_workers=int(data.get("figma_image_workers") or data.get("figmaImageWorkers") or os.getenv("FIGMA_IMAGE_WORKERS", "4") or 4)
    )
    mark_stage("image_download")
    elapsed_ms = int((time.time() - started_at) * 1000)
    return {
        "file_key": file_key,
        "node_id": selected_node_id,
        "original_node_id": node_id,
        "canvas_name": canvas_name,
        "file_name": file_name,
        "app_package": app_package,
        "drafts": drafts,
        "ignored_drafts": ignored_drafts,
        "timing": {
            "elapsed_ms": elapsed_ms,
            "stages_ms": stage_timing_ms,
            "draft_count": len(drafts),
            "ignored_count": len(ignored_drafts),
            "image_count": len([draft for draft in drafts if draft.get("screenshot")])
        }
    }


def import_figma_design(data):
    provided_drafts = data.get("drafts") or []
    selected_ids = set(str(item) for item in (data.get("selected_node_ids") or data.get("selectedNodeIds") or []) if str(item).strip())
    app_package = data.get("app_package") or data.get("appPackage") or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    if provided_drafts:
        imported = []
        for draft in provided_drafts:
            node_id = str((draft.get("figma") or {}).get("node_id") or draft.get("page_id") or "")
            if selected_ids and node_id not in selected_ids:
                continue
            draft["app_package"] = app_package
            imported.append(save_knowledge_page(draft))
        return {
            "file_key": "",
            "node_id": "",
            "file_name": "",
            "app_package": app_package,
            "imported": imported
        }

    parsed = parse_figma_design(data)
    imported = []
    for draft in parsed["drafts"]:
        if selected_ids and draft.get("figma", {}).get("node_id") not in selected_ids:
            continue
        imported.append(save_knowledge_page(draft))
    parsed["imported"] = imported
    parsed["drafts"] = []
    return parsed


def figma_generation_min_relevance():
    return max(0, env_int("FIGMA_MIN_RELEVANCE_SCORE", 5))


def figma_draft_generation_allowed(draft, min_score=None):
    figma = (draft or {}).get("figma") or {}
    if figma.get("pinned"):
        return True
    if min_score is None:
        min_score = figma_generation_min_relevance()
    return safe_int(figma.get("relevance_score"), 0) >= min_score


def split_generation_figma_drafts(drafts, min_score=None):
    if min_score is None:
        min_score = figma_generation_min_relevance()
    allowed = []
    ignored = []
    for draft in drafts or []:
        if figma_draft_generation_allowed(draft, min_score=min_score):
            allowed.append(draft)
        else:
            figma = draft.setdefault("figma", {})
            figma["relevance_reason"] = (
                figma.get("relevance_reason")
                or f"匹配度低于 {min_score}，不进入本次模型视觉校准"
            )
            ignored.append(draft)
    return allowed, ignored


def figma_drafts_to_generation_assets(drafts, limit_images=None):
    if limit_images is None:
        limit_images = FIGMA_VISUAL_IMAGE_LIMIT
    min_score = figma_generation_min_relevance()
    text_assets = []
    image_assets = []
    used_pages = []
    for draft in drafts or []:
        if not figma_draft_generation_allowed(draft, min_score=min_score):
            continue
        figma = draft.get("figma") or {}
        page = {
            "app_package": draft.get("app_package", ""),
            "page_id": draft.get("page_id", ""),
            "page_name": draft.get("page_name", ""),
            "route": draft.get("route", ""),
            "description": draft.get("description", ""),
            "key_elements": normalize_lines(draft.get("key_elements") or draft.get("keyElements")),
            "common_assertions": normalize_lines(draft.get("common_assertions") or draft.get("commonAssertions")),
            "tags": normalize_lines(draft.get("tags")),
            "screenshot": ""
        }
        figma_context = [
            f"节点：{figma.get('node_id', '')}",
            f"设备形态：{figma.get('device_label') or figma.get('device_profile') or '未知'}",
            f"画布尺寸：{figma.get('width', '')}x{figma.get('height', '')}",
            f"状态/变体：{figma.get('variant') or '未标注'}",
            f"相关性：{figma.get('relevance_score', 0)}；{figma.get('relevance_reason', '')}",
            "设计稿要求：生成场景和用例时需要区分不同设备形态、颜色、弹窗状态和可见文案；同一业务点下的多端/多颜色变体不能当作重复用例忽略。"
        ]
        text_assets.append("[Figma设计稿页面]\n" + "\n".join(figma_context) + "\n" + knowledge_page_text(page))
        screenshot = draft.get("screenshot") or {}
        if screenshot.get("contentBase64") and len(image_assets) < limit_images:
            name = clean_asset_filename(screenshot.get("name") or f"figma-{clean_id(page['page_name'])}.png")
            image_assets.append({
                "name": name,
                "mime": guess_mime(name),
                "base64": screenshot["contentBase64"]
            })
            page["screenshot"] = name
        used_pages.append({
            "source": "figma",
            "app_package": page["app_package"],
            "page_id": page["page_id"],
            "page_name": page["page_name"],
            "route": page["route"],
            "figma": figma,
            "relevance_score": figma.get("relevance_score", 0),
            "relevance_reason": figma.get("relevance_reason", "")
        })
    return text_assets, image_assets, used_pages


def figma_ignored_draft_summaries(drafts, limit=12):
    rows = []
    for draft in drafts or []:
        figma = draft.get("figma") or {}
        rows.append({
            "source": "figma_ignored",
            "page_id": draft.get("page_id", ""),
            "page_name": draft.get("page_name", ""),
            "route": draft.get("route", ""),
            "figma": {
                "node_id": figma.get("node_id", ""),
                "canvas_name": figma.get("canvas_name", ""),
                "relevance_score": figma.get("relevance_score", 0),
                "relevance_terms": figma.get("relevance_terms", []),
                "relevance_reason": figma.get("relevance_reason", "")
            }
        })
        if len(rows) >= limit:
            break
    return rows


def load_figma_generation_context(data, app_package, job_id=None, requirement_query="", case_set_id="", title="", module=""):
    figma_url = (data.get("figma_url") or data.get("figmaUrl") or "").strip()
    if not figma_url:
        return [], [], [], [], []
    excluded_node_ids = {
        str(item or "").strip()
        for item in (data.get("excluded_figma_node_ids") or data.get("excludedFigmaNodeIds") or [])
        if str(item or "").strip()
    }
    if case_set_id:
        ui_meta = load_case_ui_design_meta(case_set_id)
        excluded_node_ids |= {
            str(item or "").strip()
            for item in (ui_meta.get("excluded_figma_node_ids") or [])
            if str(item or "").strip()
        }
        excluded_node_ids |= {
            str(item.get("node_id") or item.get("nodeId") or "").strip()
            for item in (ui_meta.get("excluded_figma_nodes") or [])
            if isinstance(item, dict) and str(item.get("node_id") or item.get("nodeId") or "").strip()
        }
    if job_id:
        update_generate_job(job_id, progress=38, step="解析 Figma", message="正在解析 Figma，并按需求筛选相关 UI 页面")
    min_relevance_score = safe_int(
        data.get("figma_min_relevance_score")
        or data.get("figmaMinRelevanceScore")
        or os.getenv("FIGMA_MIN_RELEVANCE_SCORE")
        or 5,
        5
    )
    parsed = parse_figma_design({
        "figma_url": figma_url,
        "app_package": app_package,
        "mode": data.get("figma_mode") or data.get("figmaMode") or "smart",
        "limit": data.get("figma_limit") or data.get("figmaLimit") or FIGMA_PARSE_LIMIT,
        "min_width": data.get("figma_min_width") or data.get("figmaMinWidth") or 240,
        "min_height": data.get("figma_min_height") or data.get("figmaMinHeight") or 360,
        "requirement_query": requirement_query,
        "filter_by_requirement": True,
        "reference_limit": data.get("figma_reference_limit") or data.get("figmaReferenceLimit") or FIGMA_REFERENCE_LIMIT,
        "max_reference_limit": data.get("figma_max_reference_limit") or data.get("figmaMaxReferenceLimit") or FIGMA_MAX_REFERENCE_LIMIT,
        "min_relevance_score": min_relevance_score
    })
    if excluded_node_ids:
        kept_drafts = []
        excluded_drafts = []
        for draft in parsed.get("drafts") or []:
            node_id = str((draft.get("figma") or {}).get("node_id") or draft.get("page_id") or "").strip()
            if node_id and node_id in excluded_node_ids:
                excluded_drafts.append(draft)
            else:
                kept_drafts.append(draft)
        if excluded_drafts:
            for draft in excluded_drafts:
                figma = draft.setdefault("figma", {})
                figma["relevance_reason"] = "该 Figma 页面已被用户删除并加入排除列表，本次不作为参考"
            parsed["drafts"] = kept_drafts
            parsed["ignored_drafts"] = (parsed.get("ignored_drafts") or []) + excluded_drafts
    generation_drafts, low_score_drafts = split_generation_figma_drafts(parsed.get("drafts") or [], min_score=min_relevance_score)
    if low_score_drafts:
        parsed["ignored_drafts"] = (parsed.get("ignored_drafts") or []) + low_score_drafts
        parsed["drafts"] = generation_drafts
    text_assets, image_assets, used_pages = figma_drafts_to_generation_assets(generation_drafts)
    saved_designs = save_figma_design_assets_for_case(case_set_id, generation_drafts, title=title, module=module) if case_set_id else []
    ignored_pages = figma_ignored_draft_summaries(parsed.get("ignored_drafts") or [])
    if job_id:
        ignored_count = len(parsed.get("ignored_drafts") or [])
        timing = parsed.get("timing") or {}
        elapsed_ms = int(timing.get("elapsed_ms") or 0)
        image_count = len(image_assets)
        elapsed_text = f"，耗时 {elapsed_ms / 1000:.1f}s" if elapsed_ms else ""
        update_generate_job(
            job_id,
            progress=40,
            step="Figma 相关性筛选",
            message=(
                f"实际参考 Figma {len(used_pages)} 页/{image_count} 图；"
                f"忽略低匹配或已排除 {ignored_count} 页；"
                f"保存 {len(saved_designs)} 份达标 UI 稿{elapsed_text}"
            )
        )
    return text_assets, image_assets, used_pages, ignored_pages, saved_designs


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


def validate_midscene_yaml(yaml_text):
    warnings = []
    text = yaml_text or ""
    warnings.extend(validate_midscene_yaml_text_structure(text))
    if pyyaml is not None:
        try:
            parsed = pyyaml.safe_load(text)
            if not isinstance(parsed, dict):
                warnings.append("YAML 顶层必须是对象")
            else:
                tasks = parsed.get("tasks")
                if not isinstance(tasks, list) or not tasks:
                    warnings.append("YAML 必须包含非空 tasks 数组")
                else:
                    for idx, task in enumerate(tasks, 1):
                        if not isinstance(task, dict):
                            warnings.append(f"第 {idx} 条 task 必须是对象")
                            continue
                        misplaced = [
                            key for key in task.keys()
                            if key in SUPPORTED_FLOW_ITEMS or key not in TASK_LEVEL_ALLOWED_KEYS
                        ]
                        misplaced = [key for key in misplaced if key not in ("name", "flow")]
                        if misplaced:
                            warnings.append(
                                f"第 {idx} 条 task 存在疑似放错层级的字段："
                                + "、".join(str(key) for key in misplaced[:6])
                                + "；flowItem 必须放在 flow 数组内"
                            )
                        if not task.get("name"):
                            warnings.append(f"第 {idx} 条 task 缺少 name")
                        flow = task.get("flow")
                        if not isinstance(flow, list) or not flow:
                            warnings.append(f"第 {idx} 条 task 缺少非空 flow")
                        else:
                            for flow_idx, item in enumerate(flow, 1):
                                if not isinstance(item, dict):
                                    warnings.append(f"第 {idx} 条 task 的第 {flow_idx} 个 flowItem 必须是对象")
                                    continue
                                action, action_keys = parsed_flow_item_action(item)
                                if len(action_keys) != 1:
                                    if not action_keys:
                                        warnings.append(
                                            f"第 {idx} 条 task 的第 {flow_idx} 个 flowItem 缺少合法动作名："
                                            + "、".join(str(key) for key in list(item.keys())[:6])
                                        )
                                    else:
                                        warnings.append(
                                            f"第 {idx} 条 task 的第 {flow_idx} 个 flowItem 同时包含多个动作："
                                            + "、".join(action_keys)
                                        )
                                    continue
                                child_keys = [key for key in item.keys() if key != action]
                                bad_child_keys = [key for key in child_keys if key not in FLOW_CHILD_KEYS]
                                if bad_child_keys:
                                    warnings.append(
                                        f"第 {idx} 条 task 的第 {flow_idx} 个 flowItem「{action}」存在不支持的子字段："
                                        + "、".join(str(key) for key in bad_child_keys[:6])
                                    )
                                value = item.get(action)
                                if action == "sleep":
                                    if not isinstance(value, (int, float)) or value < 0:
                                        warnings.append(f"第 {idx} 条 task 的第 {flow_idx} 个 sleep 必须是非负数字毫秒")
                                elif action in ("launch", "terminate", "runAdbShell"):
                                    if not isinstance(value, str) or not value.strip():
                                        warnings.append(f"第 {idx} 条 task 的第 {flow_idx} 个 {action} 必须是非空字符串")
                                elif action == "aiInput":
                                    locate_ok = isinstance(value, str) and value.strip()
                                    input_value = item.get("value")
                                    if not locate_ok:
                                        warnings.append(f"第 {idx} 条 task 的第 {flow_idx} 个 aiInput 必须写定位文本，例如 aiInput: 搜索框")
                                    if input_value is None or not isinstance(input_value, (str, int, float)):
                                        warnings.append(f"第 {idx} 条 task 的第 {flow_idx} 个 aiInput 必须包含同级 value")
                                    if "autoDismissKeyboard" in item and not isinstance(item.get("autoDismissKeyboard"), bool):
                                        warnings.append(f"第 {idx} 条 task 的第 {flow_idx} 个 aiInput.autoDismissKeyboard 必须是布尔值 true/false")
                                    if "mode" in item and item.get("mode") not in ("replace", "typeOnly", "clear", "append"):
                                        warnings.append(f"第 {idx} 条 task 的第 {flow_idx} 个 aiInput.mode 不支持：{item.get('mode')}")
                                elif action in PROMPT_STYLE_FLOW_ITEMS:
                                    if not isinstance(value, (str, int, float)) or not str(value).strip():
                                        warnings.append(f"第 {idx} 条 task 的第 {flow_idx} 个 {action} 必须是非空提示文本")
                                if "timeout" in item and (not isinstance(item.get("timeout"), (int, float)) or item.get("timeout") < 0):
                                    warnings.append(f"第 {idx} 条 task 的第 {flow_idx} 个 timeout 必须是非负数字毫秒")
        except Exception as e:
            warnings.append(f"YAML 解析失败：{e}")
    else:
        if re.search(r"^\s*[}\]]\s*$", text, flags=re.M):
            warnings.append("YAML 中存在孤立的 } 或 ]，疑似模型输出混入 JSON 片段")
        if not re.search(r"^\s*tasks\s*:\s*$", text, flags=re.M):
            warnings.append("缺少 tasks 节点")
        if not re.search(r"^\s*-\s+name\s*:", text, flags=re.M):
            warnings.append("缺少 task name")
        if not re.search(r"^\s*flow\s*:\s*$", text, flags=re.M):
            warnings.append("缺少 flow 节点")
        if re.search(r"^\s*-\s+[A-Za-z][\w]*\s*:\S", text, flags=re.M):
            warnings.append("存在冒号后缺少空格的 flowItem，可能导致 YAML 解析失败")
        if re.search(r"^\s*-\s+aiInput\s*:\s*(?![\"']).+\n(?!\s+value\s*:)", text, flags=re.M):
            warnings.append("aiInput 缺少同级 value，Midscene 1.7 推荐使用 aiInput + value")
        lines = text.splitlines()
        for idx, line in enumerate(lines[:-1], 1):
            m = re.match(r"^(\s*)-\s+recordToReport\s*:", line)
            if not m:
                continue
            nxt = lines[idx]
            cm = re.match(r"^(\s*)(title|content)\s*:", nxt)
            if cm:
                warnings.append(f"第 {idx + 1} 行 recordToReport 后不能直接挂 {cm.group(2)}，修复阶段应移除 recordToReport")
    deduped = []
    for item in warnings:
        if item not in deduped:
            deduped.append(item)
    return {
        "ok": len(deduped) == 0,
        "warnings": deduped
    }


def validate_business_assertions(yaml_text):
    warnings = []
    vague_patterns = (
        "页面正常展示", "页面展示正常", "结果符合预期", "页面结果符合预期",
        "操作成功", "功能正常", "页面无异常", "验证成功", "进入相关页面"
    )
    for line_no, line in enumerate((yaml_text or "").splitlines(), 1):
        stripped = line.strip()
        if not re.match(r"^-\s+(ai|aiAct|aiAction|aiAssert|aiWaitFor)\s*:", stripped):
            continue
        text = strip_yaml_quotes(stripped.split(":", 1)[1])
        if any(pattern in text for pattern in vague_patterns):
            warnings.append(f"第 {line_no} 行断言/等待过于泛化：{text}")
    return warnings


SHORT_PROMPT_CONTEXT_WORDS = (
    "弹窗", "页面", "按钮", "入口", "底部", "顶部", "右上角", "左上角", "Tab",
    "列表", "区域", "卡片", "搜索框", "输入框", "详情页", "配置页", "结果页",
    "首页", "弹窗中", "对话框", "确认页", "预览页", "打印页"
)

GENERIC_PROMPT_TEXTS = {
    "确认", "确定", "取消", "返回", "下一步", "完成", "提交", "保存", "关闭",
    "继续", "开始", "进入", "查看", "点击", "搜索", "打开", "选择", "页面",
    "结果符合预期", "页面正常", "操作成功", "功能正常", "跳转成功"
}


def prompt_is_too_ambiguous(text):
    text = strip_yaml_quotes(text or "").strip()
    if not text:
        return True
    compact = re.sub(r"\s+", "", text)
    if compact in GENERIC_PROMPT_TEXTS:
        return True
    if len(compact) <= 3 and not any(word in text for word in SHORT_PROMPT_CONTEXT_WORDS):
        return True
    if any(pattern in text for pattern in ("结果符合预期", "页面正常", "功能正常", "操作成功")):
        return True
    return False


def validate_midscene_yaml_executability(yaml_text):
    """Return non-blocking suggestions for generated YAML runtime stability."""
    suggestions = []
    stats = {
        "task_count": 0,
        "tasks_without_assertion": 0,
        "ambiguous_prompt_count": 0,
        "long_sleep_count": 0,
        "missing_wait_after_action_count": 0,
    }
    if pyyaml is None:
        return validate_midscene_yaml_executability_text(yaml_text, stats)
    try:
        parsed = pyyaml.safe_load(yaml_text or "")
    except Exception as e:
        return {
            "ok": False,
            "level": "blocked",
            "score": 0,
            "suggestions": [f"YAML 解析失败，暂无法评估可执行性：{e}"],
            "stats": stats
        }
    tasks = parsed.get("tasks") if isinstance(parsed, dict) else []
    if not isinstance(tasks, list):
        return {"ok": False, "level": "blocked", "score": 0, "suggestions": ["缺少 tasks 数组，暂无法评估可执行性"], "stats": stats}

    for task_idx, task in enumerate(tasks, 1):
        if not isinstance(task, dict):
            continue
        stats["task_count"] += 1
        name = str(task.get("name") or f"第 {task_idx} 条 task")
        flow = task.get("flow") or []
        if not isinstance(flow, list):
            suggestions.append(f"{name}：flow 不是数组，无法稳定执行")
            continue
        actions = []
        sleep_values = []
        has_launch = False
        has_cleanup = False
        has_assertion = False
        last_interactive_index = -1
        wait_after_interactive = False
        for flow_idx, item in enumerate(flow, 1):
            if not isinstance(item, dict):
                continue
            action, action_keys = parsed_flow_item_action(item)
            if not action or len(action_keys) != 1:
                continue
            actions.append(action)
            value = item.get(action)
            if action == "launch":
                has_launch = True
            if action == "terminate" or (action == "runAdbShell" and "force-stop" in str(value or "")):
                has_cleanup = True
            if action in ("aiAssert", "aiWaitFor"):
                has_assertion = True
            if action == "sleep" and isinstance(value, (int, float)):
                sleep_values.append(value)
                if value >= 5000:
                    stats["long_sleep_count"] += 1
                    suggestions.append(f"{name}：第 {flow_idx} 步 sleep {int(value)}ms 偏长，建议改为 aiWaitFor 等待真实 UI 信号")
            if action in ("aiTap", "aiInput", "aiAssert", "aiWaitFor", "ai"):
                prompt_value = str(value or "")
                if prompt_is_too_ambiguous(prompt_value):
                    stats["ambiguous_prompt_count"] += 1
                    suggestions.append(f"{name}：第 {flow_idx} 步 {action} 提示词「{prompt_value}」过泛，建议补页面/弹窗/区域上下文")
            if action in ("aiTap", "aiInput", "aiKeyboardPress", "aiScroll"):
                last_interactive_index = flow_idx
                wait_after_interactive = False
            if last_interactive_index >= 0 and flow_idx > last_interactive_index and action in ("aiWaitFor", "aiAssert"):
                wait_after_interactive = True
        if not has_assertion:
            stats["tasks_without_assertion"] += 1
            suggestions.append(f"{name}：缺少 aiAssert 或业务目标型 aiWaitFor，执行报告难以判断是否真正通过")
        if detect_yaml_platform(yaml_text) == "android" and not has_launch:
            suggestions.append(f"{name}：缺少 launch，建议从稳定 App 起点独立执行")
        if detect_yaml_platform(yaml_text) == "android" and not has_cleanup:
            suggestions.append(f"{name}：缺少收尾关闭 App，可能影响下一条用例状态")
        if last_interactive_index >= 0 and not wait_after_interactive:
            stats["missing_wait_after_action_count"] += 1
            suggestions.append(f"{name}：最后一次交互后缺少可见等待/断言，建议补结果页、按钮、列表或空态检查")
        if len([v for v in sleep_values if v >= 3000]) >= 3:
            suggestions.append(f"{name}：存在多段固定等待，建议合并为更明确的 aiWaitFor 目标以提升速度")

    suggestions = dedupe_keep_order(suggestions)[:20]
    penalty = (
        stats["tasks_without_assertion"] * 18
        + stats["ambiguous_prompt_count"] * 6
        + stats["long_sleep_count"] * 4
        + stats["missing_wait_after_action_count"] * 8
    )
    score = max(0, min(100, 100 - penalty))
    level = "good" if score >= 80 else "needs_review" if score >= 55 else "risky"
    return {
        "ok": score >= 55,
        "level": level,
        "score": score,
        "suggestions": suggestions,
        "stats": stats
    }


def validate_midscene_yaml_executability_text(yaml_text, stats=None):
    stats = stats or {
        "task_count": 0,
        "tasks_without_assertion": 0,
        "ambiguous_prompt_count": 0,
        "long_sleep_count": 0,
        "missing_wait_after_action_count": 0,
    }
    suggestions = []
    text = yaml_text or ""
    task_blocks = []
    current = []
    for line in text.splitlines():
        if re.match(r"^\s{2}-\s+name\s*:", line):
            if current:
                task_blocks.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        task_blocks.append("\n".join(current))
    for task_idx, block in enumerate(task_blocks, 1):
        name = f"第 {task_idx} 条 task"
        name_m = re.search(r"^\s{2}-\s+name\s*:\s*(.+?)\s*$", block, flags=re.M)
        if name_m:
            name = strip_yaml_quotes(name_m.group(1)) or name
        stats["task_count"] += 1
        has_launch = re.search(r"^\s*-\s+launch\s*:", block, flags=re.M) is not None
        has_cleanup = re.search(r"^\s*-\s+terminate\s*:", block, flags=re.M) is not None or (
            re.search(r"^\s*-\s+runAdbShell\s*:\s*.*force-stop", block, flags=re.M) is not None
        )
        has_assertion = re.search(r"^\s*-\s+(aiAssert|aiWaitFor)\s*:", block, flags=re.M) is not None
        last_interactive_line = 0
        wait_after_interactive = False
        long_sleeps = 0
        for line_no, line in enumerate(block.splitlines(), 1):
            m = re.match(r"^\s*-\s+([A-Za-z][\w]*)\s*:\s*(.*)$", line)
            if not m:
                continue
            action, raw_value = m.groups()
            value = strip_yaml_quotes(raw_value)
            if action == "sleep":
                try:
                    sleep_ms = float(value)
                except Exception:
                    sleep_ms = 0
                if sleep_ms >= 5000:
                    long_sleeps += 1
                    stats["long_sleep_count"] += 1
                    suggestions.append(f"{name}：第 {line_no} 步 sleep {int(sleep_ms)}ms 偏长，建议改为 aiWaitFor 等待真实 UI 信号")
            if action in ("aiTap", "aiInput", "aiAssert", "aiWaitFor", "ai"):
                if prompt_is_too_ambiguous(value):
                    stats["ambiguous_prompt_count"] += 1
                    suggestions.append(f"{name}：第 {line_no} 步 {action} 提示词「{value}」过泛，建议补页面/弹窗/区域上下文")
            if action in ("aiTap", "aiInput", "aiKeyboardPress", "aiScroll"):
                last_interactive_line = line_no
                wait_after_interactive = False
            if last_interactive_line and line_no > last_interactive_line and action in ("aiWaitFor", "aiAssert"):
                wait_after_interactive = True
        if not has_assertion:
            stats["tasks_without_assertion"] += 1
            suggestions.append(f"{name}：缺少 aiAssert 或业务目标型 aiWaitFor，执行报告难以判断是否真正通过")
        if detect_yaml_platform(text) == "android" and not has_launch:
            suggestions.append(f"{name}：缺少 launch，建议从稳定 App 起点独立执行")
        if detect_yaml_platform(text) == "android" and not has_cleanup:
            suggestions.append(f"{name}：缺少收尾关闭 App，可能影响下一条用例状态")
        if last_interactive_line and not wait_after_interactive:
            stats["missing_wait_after_action_count"] += 1
            suggestions.append(f"{name}：最后一次交互后缺少可见等待/断言，建议补结果页、按钮、列表或空态检查")
        if long_sleeps >= 3:
            suggestions.append(f"{name}：存在多段固定等待，建议合并为更明确的 aiWaitFor 目标以提升速度")
    suggestions = dedupe_keep_order(suggestions)[:20]
    penalty = (
        stats["tasks_without_assertion"] * 18
        + stats["ambiguous_prompt_count"] * 6
        + stats["long_sleep_count"] * 4
        + stats["missing_wait_after_action_count"] * 8
    )
    score = max(0, min(100, 100 - penalty))
    level = "good" if score >= 80 else "needs_review" if score >= 55 else "risky"
    return {
        "ok": score >= 55,
        "level": level,
        "score": score,
        "suggestions": suggestions,
        "stats": stats,
        "mode": "text"
    }


def business_assertion_warning_text(warning):
    return str(warning or "").split("：", 1)[-1].strip()


BUSINESS_ANCHOR_STOPWORDS = {
    "点击", "按钮", "页面", "进入", "验证", "等待", "加载", "完成", "当前", "相关",
    "继续", "执行", "下一步", "返回", "操作", "显示", "出现", "可点击", "状态",
    "目标", "入口", "流程", "结果", "符合预期", "页面加载完成", "可以继续执行下一步"
}


def business_anchor_terms(text):
    text = strip_yaml_quotes(text or "")
    if not text:
        return []
    raw_parts = []
    raw_parts.extend(re.findall(r"[「『“\"]([^」』”\"]{2,30})[」』”\"]", text))
    raw_parts.extend(re.split(r"->|→|；|;|，|,|、|/|\s+", text))
    terms = []
    for part in raw_parts:
        part = re.sub(r"^(点击|进入|验证|等待|选择|输入|打开|查看|确认|检查)", "", str(part).strip())
        part = re.sub(r"(按钮|入口|页面|链接|图标|区域|状态)$", "", part).strip()
        if len(part) < 2:
            continue
        if part in BUSINESS_ANCHOR_STOPWORDS:
            continue
        if re.fullmatch(r"\d+%|\d+\.\d+%", part):
            continue
        if part not in terms:
            terms.append(part)
    return terms[:24]


def task_business_anchors(task_block):
    meta = extract_baseline_meta_from_block(task_block)
    source_texts = [
        meta.get("goal", ""),
        meta.get("path", ""),
        meta.get("expected", ""),
        meta.get("scenario", ""),
    ]
    for key, text in flow_texts_from_task_block(task_block, {"aiTap", "ai", "aiAct", "aiAction", "aiAssert", "aiWaitFor"}):
        if key in ("aiTap", "aiAssert"):
            source_texts.append(text)
        elif key in ("ai", "aiAct", "aiAction") and not text.startswith("确认前置条件："):
            source_texts.append(text)
    anchors = []
    for text in source_texts:
        for term in business_anchor_terms(text):
            if term not in anchors:
                anchors.append(term)
    return anchors[:24]


def business_alignment_report(old_block, new_block):
    anchors = task_business_anchors(old_block)
    if not anchors:
        return {"ok": True, "anchors": [], "kept": [], "missing": [], "ratio": 1.0}
    new_text = new_block or ""
    kept = [term for term in anchors if term in new_text]
    missing = [term for term in anchors if term not in new_text]
    ratio = len(kept) / max(len(anchors), 1)
    # If only a few anchors exist, require at least one real business anchor to survive.
    ok = (len(anchors) < 3 and bool(kept)) or ratio >= 0.45
    return {"ok": ok, "anchors": anchors, "kept": kept, "missing": missing, "ratio": round(ratio, 2)}


def task_action_anchor_sequence(task_block):
    sequence = []
    guard_words = (
        "弹窗", "权限", "引导", "广告", "登录提示", "未登录", "关闭", "force-stop",
        "launch", "首页", "回到首页", "keyevent", "加载完成", "可以继续执行"
    )
    for key, text in flow_texts_from_task_block(task_block, {"aiTap", "ai", "aiAct", "aiAction", "aiInput"}):
        if key == "aiAction" and text.startswith("确认前置条件："):
            continue
        if any(word in text for word in guard_words):
            continue
        terms = business_anchor_terms(text)
        if not terms:
            continue
        term = terms[0]
        if term not in sequence:
            sequence.append(term)
    return sequence[:16]


def is_runtime_guard_text(text):
    text = strip_yaml_quotes(text or "")
    if not text:
        return True
    generic_locators = (
        "当前页面的搜索输入框或文本输入框",
        "当前页面的输入框",
        "底部输入消息框",
    )
    if text in generic_locators:
        return True
    guard_words = (
        "弹窗", "权限", "引导", "广告", "登录提示", "未登录", "关闭", "force-stop",
        "launch", "启动", "首页", "回到首页", "手机主页", "外部页面", "被测 App",
        "App 内", "keyevent", "加载完成", "可以继续执行", "确认前置条件",
        "当前停留", "稳定状态", "知道了", "稍后", "跳过"
    )
    return any(word in text for word in guard_words)


def flow_entries_from_task_block(block, keys=None):
    keys = set(keys or [])
    lines = (block or "").splitlines()
    entries = []
    idx = 0
    while idx < len(lines):
        m = re.match(r"^\s*-\s+([A-Za-z][\w]*)\s*:\s*(.*)$", lines[idx])
        if not m:
            idx += 1
            continue
        key = m.group(1)
        if keys and key not in keys:
            idx += 1
            continue
        text = strip_yaml_quotes(m.group(2))
        child_map = {}
        j = idx + 1
        while j < len(lines):
            if re.match(r"^\s*-\s+[A-Za-z][\w]*\s*:", lines[j]):
                break
            cm = re.match(r"^\s*([A-Za-z][\w]*)\s*:\s*(.*)$", lines[j])
            if cm:
                child_map[cm.group(1)] = strip_yaml_quotes(cm.group(2))
            j += 1
        entries.append((key, text, child_map))
        idx = j
    return entries


def normalize_business_action_text(key, text, child_map=None):
    child_map = child_map or {}
    text = strip_yaml_quotes(text or "")
    if key == "aiInput":
        input_value = child_map.get("value") or parse_input_value(text)
        if input_value:
            return "输入:" + re.sub(r"\s+", "", strip_yaml_quotes(input_value).strip("“”‘’\"'"))
    parsed_input = parse_input_value(text)
    if parsed_input:
        return "输入:" + re.sub(r"\s+", "", strip_yaml_quotes(parsed_input).strip("“”‘’\"'"))
    if is_runtime_guard_text(text):
        return ""
    normalized = re.sub(r"\s+", "", text)
    normalized = normalized.strip("“”‘’\"'")
    normalized = re.sub(r"^(点击|选择|进入|打开|查看|验证|确认|检查)", "", normalized)
    return normalized if len(normalized) >= 2 else ""


def core_business_action_signature(task_block):
    signature = []
    for key, text, child_map in flow_entries_from_task_block(task_block, {"aiTap", "ai", "aiAct", "aiAction", "aiInput", "aiAssert"}):
        if key == "aiAction" and text.startswith("确认前置条件："):
            continue
        normalized = normalize_business_action_text(key, text, child_map)
        if not normalized:
            continue
        signature.append(normalized)
    return signature


def is_subsequence_preserved(old_items, new_items):
    if not old_items:
        return True
    pos = 0
    for item in old_items:
        found = False
        while pos < len(new_items):
            candidate = new_items[pos]
            pos += 1
            if item == candidate or item in candidate or candidate in item:
                found = True
                break
        if not found:
            return False
    return True


def core_business_actions_preserved(old_block, new_block):
    old_sig = core_business_action_signature(old_block)
    if not old_sig:
        return False
    new_sig = core_business_action_signature(new_block)
    return is_subsequence_preserved(old_sig, new_sig)


def business_sequence_report(old_block, new_block):
    old_seq = task_action_anchor_sequence(old_block)
    new_seq = task_action_anchor_sequence(new_block)
    if len(old_seq) < 3:
        return {"ok": True, "old": old_seq, "new": new_seq, "missing": [], "inversions": []}
    missing = [term for term in old_seq if term not in new_seq]
    inversions = []
    new_index = {term: idx for idx, term in enumerate(new_seq)}
    for left, right in zip(old_seq, old_seq[1:]):
        if left in new_index and right in new_index and new_index[left] > new_index[right]:
            inversions.append(f"{left} -> {right}")
    kept_ratio = (len(old_seq) - len(missing)) / max(len(old_seq), 1)
    if len(old_seq) <= 8:
        ok = not missing and not inversions
    else:
        ok = kept_ratio >= 0.85 and not inversions
    return {
        "ok": ok,
        "old": old_seq,
        "new": new_seq,
        "missing": missing,
        "inversions": inversions,
        "ratio": round(kept_ratio, 2)
    }


def validate_task_business_flow_preserved(old_block, new_block):
    if core_business_actions_preserved(old_block, new_block):
        return []
    old_core = core_business_action_signature(old_block)
    new_core = core_business_action_signature(new_block)
    if len(old_core) >= 2 and not is_subsequence_preserved(old_core, new_core):
        return [
            "修复后疑似改坏原业务流程顺序：核心业务动作不再按原顺序保留；原顺序："
            + " -> ".join(old_core[:8])
        ]
    report = business_alignment_report(old_block, new_block)
    warnings = []
    if not report["ok"]:
        warnings.append(
            "修复后疑似偏离原业务链路，缺失核心业务锚点："
            + "、".join(report["missing"][:8])
        )
    seq_report = business_sequence_report(old_block, new_block)
    if not seq_report["ok"]:
        details = []
        if seq_report.get("missing"):
            details.append("缺失步骤：" + "、".join(seq_report["missing"][:6]))
        if seq_report.get("inversions"):
            details.append("顺序异常：" + "、".join(seq_report["inversions"][:4]))
        warnings.append("修复后疑似改坏原业务流程顺序：" + "；".join(details))
    return warnings


def validate_yaml_business_flow_preserved(old_yaml, new_yaml):
    warnings = []
    for name in yaml_task_names(old_yaml):
        try:
            old_info = find_yaml_task_block(old_yaml, name)
            new_info = find_yaml_task_block(new_yaml, name)
        except Exception:
            continue
        for item in validate_task_business_flow_preserved(old_info["block"], new_info["block"]):
            warnings.append(f"{name}：{item}")
    return warnings


def task_block_has_key(block, key):
    return re.search(r"^\s*-\s+" + re.escape(key) + r"\s*:", block or "", flags=re.M) is not None


SUPPORTED_FLOW_ITEMS = {
    "ai", "aiAct", "aiAction", "aiTap", "aiHover", "aiInput", "aiKeyboardPress",
    "aiScroll", "aiAssert", "aiWaitFor", "aiQuery", "aiAsk", "aiBoolean", "aiNumber",
    "aiString", "sleep", "launch", "terminate", "javascript", "recordToReport",
    "runAdbShell", "runWdaRequest"
}

TASK_LEVEL_ALLOWED_KEYS = {
    "name", "flow", "continueOnError", "description", "tags", "priority", "data",
    "timeout", "retry", "skip", "only", "env", "variables"
}

FLOW_CHILD_KEYS = {
    "locate", "prompt", "value", "timeout", "errorMessage", "name", "keyName",
    "direction", "scrollType", "distance", "deepThink", "xpath", "cacheable",
    "autoDismissKeyboard", "mode", "method", "endpoint", "data", "content",
    "title", "duration", "target", "query", "schema"
}

PROMPT_STYLE_FLOW_ITEMS = {
    "ai", "aiAct", "aiAction", "aiTap", "aiHover", "aiAssert", "aiWaitFor",
    "aiQuery", "aiAsk", "aiBoolean", "aiNumber", "aiString", "aiInput",
    "aiKeyboardPress", "aiScroll"
}


def parsed_flow_item_action(item):
    if not isinstance(item, dict):
        return None, []
    action_keys = [key for key in item.keys() if key in SUPPORTED_FLOW_ITEMS]
    return (action_keys[0] if action_keys else None), action_keys


def detect_yaml_platform(text):
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


def normalize_yaml_key_punctuation(text):
    lines = []
    changed = False
    for line in str(text or "").splitlines():
        new_line = re.sub(r"^(\s*-\s*[A-Za-z][\w]*)(\s*)：", r"\1:", line)
        new_line = re.sub(r"^(\s*[A-Za-z][\w]*)(\s*)：", r"\1:", new_line)
        if new_line != line:
            changed = True
        lines.append(new_line)
    normalized = "\n".join(lines)
    if str(text or "").endswith("\n"):
        normalized += "\n"
    return normalized


def has_unclosed_yaml_quote(value):
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


def normalize_unclosed_yaml_quotes(text):
    lines = []
    changed = False
    source_lines = str(text or "").splitlines()
    for idx, line in enumerate(source_lines):
        new_line = line.rstrip()
        stripped = new_line.strip()
        next_stripped = source_lines[idx + 1].strip() if idx + 1 < len(source_lines) else ""
        m = re.match(r"^(\s*(?:-\s*)?[A-Za-z][\w]*\s*:\s*)(['\"])(.*)$", new_line)
        if m and has_unclosed_yaml_quote(m.group(2) + m.group(3)):
            # If the next line starts a new YAML node, this is almost certainly a
            # model-generated missing trailing quote, not an intended multiline scalar.
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


def validate_midscene_yaml_text_structure(text):
    warnings = []
    platform = detect_yaml_platform(text)
    lines = (text or "").splitlines()
    in_tasks = False
    in_task = False
    in_flow = False
    ai_operation_seen = False
    launch_seen = False
    cleanup_seen = False
    task_seen = False
    flow_seen = False
    allowed = SUPPORTED_FLOW_ITEMS
    ai_ops = {"ai", "aiAct", "aiAction", "aiTap", "aiHover", "aiInput", "aiKeyboardPress", "aiScroll", "aiAssert", "aiWaitFor", "aiQuery", "aiAsk", "aiBoolean", "aiNumber", "aiString"}

    idx = 0
    while idx < len(lines):
        line_no = idx + 1
        line = lines[idx]
        stripped = line.strip()
        indent_len = len(line) - len(line.lstrip(" "))
        if not stripped or stripped.startswith("#"):
            idx += 1
            continue
        if re.match(r"^-\s*[A-Za-z][\w]*\s*：", stripped) or re.match(r"^[A-Za-z][\w]*\s*：", stripped):
            warnings.append(f"第 {line_no} 行使用了全角冒号「：」，YAML key 必须使用英文冒号「:」")
            idx += 1
            continue
        scalar_m = re.match(r"^(?:-\s*)?[A-Za-z][\w]*\s*:\s*(.+)$", stripped)
        if scalar_m and has_unclosed_yaml_quote(scalar_m.group(1)):
            warnings.append(f"第 {line_no} 行疑似存在未闭合引号，请补齐字符串结尾引号")
        if re.match(r"^tasks\s*:\s*$", stripped):
            in_tasks = True
            in_task = False
            in_flow = False
            idx += 1
            continue
        if in_tasks and indent_len == 0 and re.match(r"^[A-Za-z_][\w-]*\s*:", stripped) and not stripped.startswith("tasks:"):
            in_tasks = False
            in_task = False
            in_flow = False
        if not in_tasks:
            idx += 1
            continue

        task_m = re.match(r"^-\s+name\s*:", stripped)
        if task_m:
            task_seen = True
            in_task = True
            in_flow = False
            if indent_len != 2:
                warnings.append(f"第 {line_no} 行 task name 缩进必须是 2 个空格")
            idx += 1
            continue

        if re.match(r"^flow\s*:\s*$", stripped):
            flow_seen = True
            in_flow = True
            if not in_task:
                warnings.append(f"第 {line_no} 行 flow 不在 task 内")
            if indent_len != 4:
                warnings.append(f"第 {line_no} 行 flow 缩进必须是 4 个空格")
            idx += 1
            continue

        flow_m = re.match(r"^-\s+([A-Za-z][\w]*)\s*:\s*(.*)$", stripped)
        if flow_m:
            key, value = flow_m.groups()
            if key == "name":
                idx += 1
                continue
            if indent_len < 6 or not in_flow:
                warnings.append(f"第 {line_no} 行 flowItem「{key}」疑似不在 flow 内，请检查缩进")
            if key not in allowed:
                warnings.append(f"第 {line_no} 行存在可能不支持的 flowItem：{key}")
            if key in ai_ops:
                ai_operation_seen = True
            if key == "launch":
                launch_seen = True
            if key == "terminate" or (key == "runAdbShell" and "force-stop" in value):
                cleanup_seen = True
            if re.match(r"^[A-Za-z][\w]*\s*:\S", stripped[2:]):
                warnings.append(f"第 {line_no} 行 flowItem「{key}」冒号后必须有空格")
            child_lines = []
            j = idx + 1
            while j < len(lines):
                child_line = lines[j]
                child_stripped = child_line.strip()
                child_indent = len(child_line) - len(child_line.lstrip(" "))
                if not child_stripped or child_stripped.startswith("#"):
                    child_lines.append((j + 1, child_indent, child_stripped))
                    j += 1
                    continue
                if (
                    (
                        re.match(r"^-\s+[A-Za-z][\w]*\s*:", child_stripped)
                        or re.match(r"^-\s*[A-Za-z][\w]*\s*：", child_stripped)
                    )
                    and child_indent <= indent_len
                ):
                    break
                if child_indent <= indent_len and (
                    re.match(r"^[A-Za-z][\w]*\s*:", child_stripped)
                    or re.match(r"^[A-Za-z][\w]*\s*：", child_stripped)
                ):
                    break
                child_lines.append((j + 1, child_indent, child_stripped))
                j += 1
            child_map = {}
            for child_no, child_indent, child_stripped in child_lines:
                cm = re.match(r"^([A-Za-z][\w]*)\s*:\s*(.*)$", child_stripped)
                if not cm:
                    continue
                child_key, child_value = cm.groups()
                child_map[child_key] = child_value
                if child_indent < indent_len + 2:
                    warnings.append(f"第 {child_no} 行子字段「{child_key}」缩进不足，必须挂在上一条 flowItem 下")
                if child_key not in FLOW_CHILD_KEYS:
                    warnings.append(f"第 {child_no} 行存在不支持的 flowItem 子字段：{child_key}")
            if key == "sleep":
                if not re.match(r"^\d+(\.\d+)?$", strip_yaml_quotes(value)):
                    warnings.append(f"第 {line_no} 行 sleep 必须是数字毫秒")
            if key == "aiInput":
                if "value" not in child_map or not strip_yaml_quotes(child_map.get("value", "")):
                    warnings.append(f"第 {line_no} 行 aiInput 必须包含同级 value")
                if "autoDismissKeyboard" in child_map:
                    raw_bool = child_map["autoDismissKeyboard"].strip()
                    if raw_bool not in ("true", "false", "True", "False"):
                        warnings.append(f"第 {line_no} 行 aiInput.autoDismissKeyboard 必须是布尔值 true/false")
                    if raw_bool.startswith(("'", '"')) or raw_bool.endswith(("'", '"')):
                        warnings.append(f"第 {line_no} 行 aiInput.autoDismissKeyboard 不能写成字符串，必须是 true/false")
            idx = j
            continue

        kv_m = re.match(r"^([A-Za-z][\w]*)\s*:", stripped)
        if in_task and kv_m and indent_len == 4 and kv_m.group(1) not in TASK_LEVEL_ALLOWED_KEYS:
            warnings.append(f"第 {line_no} 行 task 层存在不支持字段：{kv_m.group(1)}")
        idx += 1

    if not task_seen:
        warnings.append("缺少 task name")
    if not flow_seen:
        warnings.append("缺少 flow 节点")
    if platform == "android" and not launch_seen:
        warnings.append("缺少 launch 前置启动步骤")
    if platform == "android" and not cleanup_seen:
        warnings.append("缺少收尾关闭 App 步骤")
    if not ai_operation_seen:
        warnings.append("未发现 AI 操作步骤")
    return warnings

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


def normalize_yaml_scalar_value(value):
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


def strip_record_to_report_items(block):
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
    if not block:
        return block, []
    block = normalize_yaml_key_punctuation(block)
    block, record_changes = strip_record_to_report_items(block)
    lines = block.splitlines()
    changes = list(record_changes)
    normalized = []
    idx = 0
    prompt_style_items = {"ai", "aiAct", "aiAction", "aiTap", "aiHover", "aiAssert", "aiWaitFor", "aiQuery", "aiAsk", "aiBoolean", "aiNumber", "aiString"}
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


def normalize_input_actions_in_task_block(block):
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


def normalize_search_input_submit_in_task_block(block, evidence_text=""):
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


def normalize_horizontal_icon_scrolls_in_task_block(block, evidence_text=""):
    if not block:
        return block, []
    lines = block.splitlines()
    result = []
    changes = []
    idx = 0
    evidence = str(evidence_text or "")
    needs_stronger_scroll = any(word in evidence for word in ("实际页面截图中未出现", "仍未出现", "未找到", "找不到", "not found", "failed to locate"))
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
        child_text = "\n".join(children)
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


def normalize_terminate_to_force_stop(block, app_package=None):
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


def task_block_has_popup_guard(block):
    return any(word in (block or "") for word in ("弹窗", "浮层", "关闭", "跳过", "允许"))


def task_block_ends_with_key(block, key):
    flow_keys = []
    for line in (block or "").splitlines():
        m = re.match(r"^\s*-\s+([A-Za-z][\w]*)\s*:", line)
        if m:
            item_key = m.group(1)
            if item_key != "sleep":
                flow_keys.append(item_key)
    return bool(flow_keys) and flow_keys[-1] == key


def task_block_ends_with_force_stop(block):
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
    for prev in range(idx - 1, -1, -1):
        m = re.match(r"^\s*-\s+([A-Za-z][\w]*)\s*:", lines[prev])
        if m:
            return m.group(1)
    return ""


def next_flow_item(lines, idx):
    for nxt in range(idx + 1, len(lines)):
        m = re.match(r"^\s*-\s+([A-Za-z][\w]*)\s*:\s*(.*)$", lines[nxt])
        if m:
            return m.group(1), strip_yaml_quotes(m.group(2))
    return "", ""


def previous_flow_item(lines, idx):
    for prev in range(idx - 1, -1, -1):
        m = re.match(r"^\s*-\s+([A-Za-z][\w]*)\s*:\s*(.*)$", lines[prev])
        if m:
            return m.group(1), strip_yaml_quotes(m.group(2))
    return "", ""


def normalize_redundant_short_sleeps_in_task_block(block):
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


def wait_condition_from_context(prev_key, next_key, next_text):
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


def normalize_waitfor_timeouts_in_task_block(block):
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


def loading_wait_timeout_for_context(text):
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
    text_parts = []
    for line in (block or "").splitlines():
        stripped = line.strip()
        if re.match(r"^-\s+aiWaitFor\s*:", stripped) and "模型处理进度" in stripped:
            continue
        if stripped.startswith("timeout:"):
            continue
        text_parts.append(stripped)
    text = "\n".join(text_parts)
    model_terms = (
        "3D", "3d", "模型", "建模", "切片", "stl", ".stl", "obj", ".obj",
        "关节龙", "模型导入", "模型库"
    )
    non_model_terms = (
        "错题", "错题本", "文档", "PDF", "Word", "照片", "相册", "扫描", "复印",
        "证件", "格式转换", "基础打印", "试卷", "题目", "数学", "学习", "2D", "2d"
    )
    model_score = sum(1 for word in model_terms if word in text)
    non_model_score = sum(1 for word in non_model_terms if word in text)
    if any(word in text for word in ("模型处理", "切片完成", "模型加载", "3D打印", "模型导入", ".stl", ".obj", "stl", "obj")):
        model_score += 2
    return model_score > 0 and model_score >= non_model_score


def normalize_inappropriate_model_processing_waits_in_task_block(block):
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
        changes.append("将非模型/2D打印链路中的“模型处理进度”等待改为按钮或确认弹窗等待")
    if not changes:
        return block, []
    return "\n".join(result).rstrip(), list(dict.fromkeys(changes))


def normalize_business_loading_waits_in_task_block(block):
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
            changes.append("将“等待进度并点击确认打印”的混合 ai 动作拆成等待后的明确 aiTap")
            continue
        result.append(line)
    if not changes:
        return block, []
    return "\n".join(result).rstrip(), list(dict.fromkeys(changes))


def evidence_needs_midflow_render_wait(evidence_text=""):
    text = str(evidence_text or "")
    return any(word in text for word in ("尚未渲染", "未渲染", "未完成加载", "还没加载", "未加载完成", "还没出现", "按钮未出现", "未找到")) and any(
        word in text for word in ("PNG", "JPG", "PDF", "Word", "格式", "导出", "确认", "下一步", "完成")
    )


def render_wait_target_hints(evidence_text=""):
    text = str(evidence_text or "")
    hints = []
    for word in ("PNG", "JPG", "JPEG", "PDF", "Word", "导出", "确认", "下一步", "完成"):
        if word.lower() in text.lower() and word not in hints:
            hints.append(word)
    return hints


def normalize_midflow_render_waits_in_task_block(block, evidence_text=""):
    if not block or not evidence_needs_midflow_render_wait(evidence_text):
        return block, []
    target_hints = render_wait_target_hints(evidence_text)
    lines = block.splitlines()
    result = []
    changes = []
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        result.append(line)
        m = re.match(r"^(\s*)-\s+(aiTap|ai|aiAction|aiAct)\s*:\s*(.+?)\s*$", line)
        if not m:
            idx += 1
            continue
        indent, key, raw_text = m.groups()
        text = strip_yaml_quotes(raw_text)
        if text not in ("完成", "确认", "下一步") and not any(word in text for word in ("点击完成", "点击确认", "点击下一步")):
            idx += 1
            continue
        next_idx = idx + 1
        while next_idx < len(lines) and re.match(r"^\s*-\s+sleep\s*:\s*\d+\s*$", lines[next_idx]):
            result.append(lines[next_idx])
            next_idx += 1
        if next_idx >= len(lines):
            idx = next_idx
            continue
        next_m = re.match(r"^(\s*)-\s+(aiTap|ai|aiAction|aiAct)\s*:\s*(.+?)\s*$", lines[next_idx])
        if not next_m:
            idx += 1
            continue
        next_text = strip_yaml_quotes(next_m.group(3))
        target_words = ("PNG", "JPG", "JPEG", "PDF", "Word", "确认", "导出", "保存", "下一步")
        if not any(word.lower() in next_text.lower() for word in target_words):
            idx += 1
            continue
        if target_hints and not any(word.lower() in next_text.lower() for word in target_hints):
            idx += 1
            continue
        already_wait = any("aiWaitFor" in item for item in result[-3:]) or any("aiWaitFor" in lines[k] for k in range(idx + 1, next_idx))
        if not already_wait:
            condition = f"页面已完成处理并渲染出可点击的「{next_text}」选项或按钮"
            result.append(indent + "- aiWaitFor: " + yaml_text(condition))
            result.append(indent + "  timeout: 30000")
            changes.append(f"在「{text}」后补充等待，确保「{next_text}」渲染完成后再点击")
        idx = next_idx
    if not changes:
        return block, []
    return "\n".join(result).rstrip(), list(dict.fromkeys(changes))


def normalize_toast_assertions_in_task_block(block, evidence_text=""):
    if not block or not evidence_is_toast_assertion_issue("\n".join([block, evidence_text or ""])):
        return block, []
    result_wait_text = "页面出现保存成功、已保存、保存完成、导出成功、下载完成、生成成功或转换完成等结果提示；如果短暂提示已消失，则结果流程已结束，页面没有加载中、保存中、导出中、权限失败、网络错误或结果失败提示"
    result_assert_text = "当前没有保存失败、导出失败、下载失败、生成失败、转换失败、权限失败、网络错误或异常弹窗"
    lines = block.splitlines()
    result = []
    changes = []
    last_result_action_seen = False
    result_validation_emitted = False
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        flow_m = re.match(r"^(\s*)-\s+([A-Za-z][\w]*)\s*:\s*(.+?)\s*$", line)
        if flow_m:
            _, flow_key, flow_raw_text = flow_m.groups()
            flow_text = strip_yaml_quotes(flow_raw_text)
            if flow_key in ("aiTap", "ai", "aiAction", "aiAct") and any(word in flow_text for word in ("保存到相册", "保存", "导出", "下载")):
                last_result_action_seen = True
            elif flow_key not in ("aiWaitFor", "aiAssert"):
                last_result_action_seen = False
        m = re.match(r"^(\s*)-\s+(aiWaitFor|aiAssert)\s*:\s*(.+?)\s*$", line)
        if not m:
            result.append(line)
            idx += 1
            continue
        indent, key, raw_text = m.groups()
        text = strip_yaml_quotes(raw_text)
        result_validation_text = (
            text == result_wait_text
            or text == result_assert_text
            or ("结果流程已结束" in text and "权限失败" in text)
            or ("当前没有保存失败" in text and "异常弹窗" in text)
        )
        strict_result_check = any(word in text for word in ("保存", "下载", "导出", "转换", "生成", "相册")) and any(
            word in text for word in ("成功", "已保存", "完成", "提示", "toast", "保持静止", "按钮仍可见", "导出按钮", "保存按钮", "未出现预期")
        )
        if result_validation_text or strict_result_check:
            if last_result_action_seen and not result_validation_emitted:
                result.append(indent + "- aiWaitFor: " + yaml_text(result_wait_text))
                result.append(indent + "  timeout: 30000")
                result.append(indent + "- aiAssert: " + yaml_text(result_assert_text))
                result_validation_emitted = True
                changes.append("将保存/导出/下载类结果断言改为最终结果动作后的单次通用校验")
            else:
                changes.append("移除流程中间或重复的保存/导出/下载结果校验，避免打断业务链路")
            idx += 1
            while idx < len(lines):
                child = lines[idx]
                if re.match(r"^\s*-\s+[A-Za-z][\w]*\s*:", child):
                    break
                if re.match(r"^\s*(timeout|errorMessage|name)\s*:", child):
                    idx += 1
                    continue
                break
            continue
        result.append(line)
        idx += 1
    if not changes:
        return block, []
    return "\n".join(result).rstrip(), list(dict.fromkeys(changes))


def task_block_has_baseline_meta(block):
    return re.search(r"^\s*#\s*baseline\.", block or "", flags=re.M) is not None


def strip_yaml_quotes(value):
    value = str(value or "").strip()
    if len(value) >= 2 and value[0] in ("'", '"') and value[-1] == value[0]:
        value = value[1:-1]
    return value.replace('\\"', '"').replace("\\'", "'").strip()


def flow_texts_from_task_block(block, keys=None):
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
    m = re.search(r"^\s*-\s+name:\s*(.+?)\s*$", block or "", flags=re.M)
    return strip_yaml_quotes(m.group(1)) if m else "未命名用例"


def derive_task_baseline_meta(block):
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


def normalize_task_block_runtime_guards(block, app_package=None, evidence_text="", platform="android"):
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


def normalize_yaml_runtime_guards(yaml_text, app_package=None, evidence_text=""):
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


def should_use_rule_only_repair(old_text, guard_changes, stdout="", stderr="", summary=None):
    if not guard_changes:
        return False
    evidence = "\n".join([stdout or "", stderr or "", json.dumps(summary, ensure_ascii=False)[:3000] if summary is not None else ""]).lower()
    failure_brief = extract_failure_brief(stdout, stderr, summary)
    if failure_brief.get("failure_type") in ("model_config", "device_env"):
        return False
    if failure_brief.get("failure_type") == "yaml_syntax":
        return True
    structural_signals = (
        "unknown flowitem",
        "failed to load",
        "property",
        "yaml",
        "cannot use 'in' operator",
        "model configuration",
        "base url",
        "midscene_model_name",
        "no yaml files",
        "no script path",
    )
    not_in_app_signals = (
        "不在",
        "首页",
        "launch",
        "activate",
        "current page",
        "not in app",
        "failed to get base url",
    )
    missing_runtime_guard = "launch:" not in old_text or not ("terminate:" in old_text or "am force-stop" in old_text)
    if missing_runtime_guard:
        return True
    if any(signal in evidence for signal in structural_signals):
        return True
    if any(signal in evidence for signal in not_in_app_signals) and not any(signal in evidence for signal in ("task failed:", "assertion failed", "failed to locate")):
        return True
    return False


def normalize_full_yaml_structure(text):
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


def normalize_yaml_from_model(text):
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


def _clean_yaml_name(value):
    value = (value or "").strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        value = value[1:-1]
    return value.replace('\\"', '"').strip()


def find_yaml_task_block(yaml_text, task_name):
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
    lines = yaml_text.splitlines()
    block_lines = normalize_task_block_indent(new_block, task_info["indent"]).splitlines()
    if not block_lines or not re.match(r"^\s*-\s+name:\s*", block_lines[0]):
        raise ValueError("修复后的内容必须是一条以 - name: 开头的 YAML task")
    new_text = "\n".join(lines[:task_info["start"]] + block_lines + lines[task_info["end"]:])
    return new_text.rstrip() + "\n"


def normalize_task_block_indent(block, target_indent):
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


def stable_case_id(app_package, module, file, task_name):
    source = "||".join([app_package or "", module or "", clean_filename(file or ""), task_name or ""])
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]
    prefix = clean_id(app_package or module or "midscene", "midscene").replace(".", "_").upper()[:18]
    return f"{prefix}_{digest}"


def task_case_info(module, file, yaml_text, task_info, app_package=None):
    meta = extract_baseline_meta_from_block(task_info.get("block", ""))
    resolved_app = resolve_app_package(module, file, yaml_text, explicit=app_package or "", allow_default=False)
    case_id = meta.get("case_id") or meta.get("caseId") or stable_case_id(resolved_app, module, file, task_info.get("name"))
    return {
        "case_id": case_id,
        "module": module,
        "file": clean_filename(file),
        "task_name": task_info.get("name") or "",
        "line": safe_int(task_info.get("start"), 0) + 1,
        "app_package": resolved_app,
        "app_name": (task_app_map_by_package().get(resolved_app) or {}).get("name") or resolved_app,
        "platform": "android" if "android:" in (yaml_text or "") else ("ios" if "ios:" in (yaml_text or "") else "android"),
        "goal": meta.get("goal") or "",
        "path": meta.get("path") or "",
        "expected": meta.get("expected") or "",
        "status": (load_task_meta().get(task_key(module, file), {}) or {}).get("status", "draft")
    }


def list_task_case_assets(module_filter="", file_filter=""):
    rows = []
    if not os.path.exists(TASK_DIR):
        return rows
    for module in sorted(os.listdir(TASK_DIR)):
        if module_filter and module != module_filter:
            continue
        module_dir = safe_join(TASK_DIR, module)
        if not os.path.isdir(module_dir):
            continue
        for file in sorted(os.listdir(module_dir)):
            if not file.endswith((".yaml", ".yml")):
                continue
            if file_filter and file != clean_filename(file_filter):
                continue
            try:
                yaml_text_value = read_text_file(safe_join(module_dir, file))
                app_package = resolve_app_package(module, file, yaml_text_value, allow_default=False)
                for task_info in list_yaml_task_blocks(yaml_text_value):
                    rows.append(task_case_info(module, file, yaml_text_value, task_info, app_package=app_package))
            except Exception as e:
                rows.append({
                    "case_id": "",
                    "module": module,
                    "file": file,
                    "task_name": "",
                    "error": str(e)
                })
    sync = load_sonic_sync().get("cases", {})
    for row in rows:
        row["sonic"] = sync.get(row.get("case_id"), {})
    return rows


def find_task_case_asset(case_id):
    case_id = (case_id or "").strip()
    if not case_id:
        raise ValueError("case_id 不能为空")
    for row in list_task_case_assets():
        if row.get("case_id") == case_id:
            return row
    raise FileNotFoundError(f"未找到 case_id：{case_id}")


def task_case_sonic_context(case_info):
    """Execution context inherited from the app binding; bridge runners should not need manual suite params."""
    app = task_app_map_by_package().get(case_info.get("app_package") or "") or {}
    context = {
        "app_package": app.get("package") or case_info.get("app_package") or "",
        "app_name": app.get("name") or case_info.get("app_name") or "",
        "sonic_project_id": sonic_project_id_for_app(app),
        "sonic_project_name": sonic_project_name_for_app(app),
        "sonic_suite_id": str(sonic_suite_id_for_app(app) or ""),
        "sonic_suite_name": sonic_suite_name_for_app(app),
        "suite_expected_total": 0,
    }
    if context["sonic_suite_id"]:
        try:
            detail = sonic_response_data(sonic_request("GET", "/testSuites", params={"id": int(context["sonic_suite_id"])}, timeout=10)) or {}
            if isinstance(detail, dict):
                context["sonic_suite_name"] = detail.get("name") or context["sonic_suite_name"]
                context["suite_expected_total"] = sonic_count_suite_cases(detail)
        except Exception as e:
            context["suite_lookup_error"] = str(e)
    return context


def task_case_yaml(case_info):
    yaml_path = safe_join(TASK_DIR, case_info["module"], case_info["file"])
    yaml_text_value = read_text_file(yaml_path)
    app_package = resolve_app_package(case_info["module"], case_info["file"], yaml_text_value, allow_default=False)
    return yaml_with_single_task(yaml_text_value, case_info["task_name"], app_package=app_package)


def ensure_yaml_case_ids(module, file):
    file = clean_filename(file)
    yaml_path = safe_join(TASK_DIR, module, file)
    if not os.path.exists(yaml_path):
        raise FileNotFoundError("YAML 文件不存在")
    yaml_text_value = read_text_file(yaml_path)
    app_package = resolve_app_package(module, file, yaml_text_value, allow_default=False)
    tasks = list_yaml_task_blocks(yaml_text_value)
    if not tasks:
        return yaml_text_value, []
    lines = yaml_text_value.splitlines()
    changes = []
    for task in reversed(tasks):
        meta = extract_baseline_meta_from_block(task.get("block", ""))
        if meta.get("case_id") or meta.get("caseId"):
            continue
        case_id = stable_case_id(app_package, module, file, task.get("name"))
        insert_at = safe_int(task.get("start"), 0) + 1
        indent = task.get("indent", "") + "  "
        lines.insert(insert_at, f"{indent}# baseline.case_id: {case_id}")
        changes.append({"task_name": task.get("name"), "case_id": case_id})
    if changes:
        save_file_version(module, file, reason="before_sonic_case_id")
        yaml_text_value = "\n".join(lines).rstrip() + "\n"
        write_text_file(yaml_path, yaml_text_value)
    return yaml_text_value, list(reversed(changes))


def build_dashscope_chat_body(prompt, image_assets=None, temperature=0.1, json_response=True, image_limit=None):
    image_assets = image_assets or []
    image_limit = max(1, int(image_limit or AI_VISION_IMAGE_LIMIT))
    model = dashscope_model_for_images(image_assets)
    if image_assets:
        user_content = [{"type": "text", "text": prompt}]
        for asset in image_assets[:image_limit]:
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{asset['mime']};base64,{asset['base64']}"
                }
            })
    else:
        user_content = prompt
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你只输出合法 JSON。"},
            {"role": "user", "content": user_content}
        ],
        "temperature": temperature
    }
    if json_response:
        body["response_format"] = {"type": "json_object"}
    return body


def dashscope_chat_content(prompt, image_assets=None, temperature=0.1, timeout=180, json_response=True, image_limit=None):
    api_key = dashscope_api_key()
    base_url = dashscope_base_url()
    model = dashscope_model_for_images(image_assets)
    timeout = max(safe_int(timeout, 180), AI_CHAT_TIMEOUT_SECONDS)
    body = json.dumps(build_dashscope_chat_body(
        prompt,
        image_assets=image_assets,
        temperature=temperature,
        json_response=json_response,
        image_limit=image_limit
    ), ensure_ascii=False).encode("utf-8")
    last_error = None
    for attempt in range(AI_CHAT_RETRY_COUNT + 1):
        req = urllib.request.Request(
            f"{base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp_data = json.loads(resp.read().decode("utf-8"))
            return resp_data["choices"][0]["message"]["content"]
        except (TimeoutError, socket.timeout, urllib.error.URLError) as e:
            last_error = e
            if attempt < AI_CHAT_RETRY_COUNT:
                time.sleep(2 * (attempt + 1))
                continue
            raise TimeoutError(
                f"千问模型响应超时：{model} 在 {timeout}s 内未返回，已重试 {AI_CHAT_RETRY_COUNT} 次；"
                "建议减少本次上传的大图/长文档，补充关键截图即可，或稍后重新生成"
            ) from e
        except Exception:
            raise
    raise last_error


def repair_knowledge_context(module, file, yaml_text, log_text, task_name=""):
    app_package = resolve_app_package(module, file, yaml_text)
    selected_page_ids = get_baseline_ref_page_ids(app_package, module, file, task_name)
    query_text = "\n".join([
        module or "",
        file or "",
        task_name or "",
        (yaml_text or "")[-6000:],
        (log_text or "")[-3000:],
    ])
    knowledge_texts, knowledge_images, used_pages = load_knowledge_context(app_package, query_text, limit=6, selected_page_ids=selected_page_ids, tier="baseline")
    if not knowledge_texts:
        return "", [], []
    text = "\n\n".join(knowledge_texts)
    return text, knowledge_images, used_pages


def execution_screenshot_context(job, limit=4):
    run_dir = job.get("run_dir") or ""
    if not run_dir:
        return []
    screenshot_dir = Path(run_dir) / "screenshots"
    if not screenshot_dir.exists():
        return []
    assets = []
    for path in sorted(screenshot_dir.iterdir(), key=lambda item: item.stat().st_mtime if item.exists() else 0, reverse=True):
        if len(assets) >= limit:
            break
        if not path.is_file() or path.suffix.lower() not in (".png", ".jpg", ".jpeg"):
            continue
        try:
            data = path.read_bytes()
            if not data or len(data) > 2 * 1024 * 1024:
                continue
            assets.append({
                "name": path.name,
                "mime": guess_mime(path.name),
                "base64": base64.b64encode(data).decode("ascii")
            })
        except Exception:
            continue
    return assets


def flow_items_with_index(task_block):
    items = []
    lines = (task_block or "").splitlines()
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        m = re.match(r"^(\s*)-\s+([A-Za-z][\w]*)\s*:\s*(.*)$", line)
        if not m:
            idx += 1
            continue
        indent, key, raw_value = m.groups()
        if key == "name":
            idx += 1
            continue
        children = []
        j = idx + 1
        while j < len(lines):
            child = lines[j]
            if re.match(r"^\s*-\s+[A-Za-z][\w]*\s*:", child):
                break
            children.append(child)
            j += 1
        items.append({
            "index": len(items),
            "line": idx + 1,
            "key": key,
            "value": strip_yaml_quotes(raw_value),
            "text": "\n".join([line] + children),
            "children": children,
            "indent": indent
        })
        idx = j
    return items


def failure_target_terms(text):
    terms = []
    raw = str(text or "")
    quoted = re.findall(r"[「“\"']([^」”\"']{1,30})[」”\"']", raw)
    for item in quoted:
        item = item.strip()
        if item and item not in terms:
            terms.append(item)
    for word in ("试卷夹", "确认打印", "立即打印", "下一步", "完成", "搜索", "保存", "导出", "登录", "首页"):
        if word in raw and word not in terms:
            terms.append(word)
    return terms[:8]


def locate_failure_window(task_block, evidence_text="", radius=5):
    items = flow_items_with_index(task_block)
    if not items:
        return {"failed_index": -1, "before": [], "after": [], "items": []}
    evidence = str(evidence_text or "")
    terms = failure_target_terms(evidence)
    failed_index = -1
    for idx, item in enumerate(items):
        blob = "\n".join([item.get("value", ""), item.get("text", "")])
        if terms and any(term and term in blob for term in terms):
            failed_index = idx
            break
    if failed_index < 0:
        for idx, item in enumerate(items):
            if item.get("key") in ("aiAssert", "aiWaitFor") and any(word in item.get("value", "") for word in ("未出现", "找不到", "可见", "出现")):
                failed_index = idx
                break
    if failed_index < 0:
        failed_index = min(len(items) - 1, max(0, len(items) - 2))
    start = max(0, failed_index - radius)
    end = min(len(items), failed_index + radius + 1)
    return {
        "failed_index": failed_index,
        "before": items[start:failed_index],
        "failed": items[failed_index] if 0 <= failed_index < len(items) else None,
        "after": items[failed_index + 1:end],
        "items": items
    }


def build_failure_context(job, yaml_text, stdout="", stderr="", summary=None, task_name=""):
    module = job.get("module", "")
    file = job.get("file", "")
    report_text = report_text_context(job)
    evidence_text = "\n".join([
        stdout or "",
        stderr or "",
        json.dumps(summary, ensure_ascii=False)[:3000] if summary is not None else "",
        report_text or "",
    ])
    platform = detect_yaml_platform(yaml_text)
    app_package = resolve_app_package(module, file, yaml_text)
    target_task = task_name or job.get("target_task_name") or ""
    task_block = ""
    if target_task:
        try:
            task_block = find_yaml_task_block(yaml_text, target_task)["block"]
        except Exception:
            task_block = ""
    if not task_block:
        names = yaml_task_names(yaml_text)
        for name in names:
            if name and (name in evidence_text or not task_block):
                try:
                    task_block = find_yaml_task_block(yaml_text, name)["block"]
                    target_task = name
                    if name in evidence_text:
                        break
                except Exception:
                    continue
    business_context = task_business_context(task_block, "") if task_block else {}
    failure_window = locate_failure_window(task_block, evidence_text)
    return {
        "module": module,
        "file": file,
        "task_name": target_task,
        "platform": platform,
        "app_package": app_package,
        "run_mode": job.get("run_mode", "test"),
        "evidence_text": evidence_text,
        "report_text": report_text,
        "failure_brief": extract_failure_brief(stdout, stderr, summary),
        "business_context": business_context,
        "failure_window": failure_window,
        "task_block": task_block,
        "yaml_text": yaml_text
    }


def classify_failure_by_context(ctx):
    yaml_text = ctx.get("yaml_text", "")
    task_block = ctx.get("task_block", "")
    evidence = ctx.get("evidence_text", "")
    lower = evidence.lower()
    runtime_toast = runtime_toast_error_from_text(evidence)
    if runtime_toast:
        return {
            "category": "product_bug",
            "failure_type": "runtime_toast_error",
            "confidence": 0.93,
            "reason": f"报告或执行证据中出现运行时错误 toast/浮层：{runtime_toast}，这不是普通元素定位失败，不能通过放宽 YAML 掩盖",
            "evidence": [runtime_toast],
            "suggested_action": "保留失败并提交产品/运行时问题；若确认是测试数据或环境导致，再由人工调整数据后重跑",
            "can_auto_repair": False
        }
    brief = ctx.get("failure_brief") or {}
    if brief.get("failure_type") in ("model_config", "model_service", "device_env"):
        return {
            "category": "env_issue",
            "failure_type": brief.get("failure_type"),
            "confidence": 0.96,
            "reason": "失败属于模型配置、模型服务或设备环境问题，不应修改 YAML",
            "evidence": brief.get("signals", [])[:8],
            "suggested_action": "先修复环境/设备/模型服务后重跑",
            "can_auto_repair": False
        }
    if any(word in lower for word in ("ai call error", "failed to call ai model service", "request was aborted", "model-provider.html")):
        return {
            "category": "env_issue",
            "failure_type": "model_service",
            "confidence": 0.96,
            "reason": "Midscene 调用视觉模型服务时被中断或超时，不是 YAML 业务链路错误",
            "evidence": [line for line in evidence.splitlines() if "AI call error" in line or "Request was aborted" in line or "model-provider" in line][:8],
            "suggested_action": "先检查 Runner 模型环境、DashScope 网络连通性和重试执行；确认模型服务稳定后再判断是否需要修脚本",
            "can_auto_repair": False
        }
    yaml_check = validate_midscene_yaml(yaml_text)
    if not yaml_check.get("ok"):
        return {
            "category": "script_issue",
            "failure_type": "yaml_syntax",
            "confidence": 0.98,
            "reason": "YAML 基础结构或 Midscene flowItem 校验未通过",
            "evidence": yaml_check.get("warnings", [])[:8],
            "suggested_action": "只执行规则级 YAML 结构/flowItem 修复，不改业务链路",
            "can_auto_repair": True
        }
    if brief.get("failure_type") in ("model_config", "model_service", "device_env"):
        return {
            "category": "env_issue",
            "failure_type": brief.get("failure_type"),
            "confidence": 0.96,
            "reason": "失败属于模型配置或设备环境问题，不应修改 YAML",
            "evidence": brief.get("signals", [])[:8],
            "suggested_action": "先修复环境/设备/模型配置后重跑",
            "can_auto_repair": False
        }
    if "http error" in lower or "request entity too large" in lower or re.search(r"\b50[234]\b", lower):
        return {
            "category": "env_issue",
            "failure_type": "server_or_upload",
            "confidence": 0.9,
            "reason": "失败包含服务端或报告上传错误，不应修改业务 YAML",
            "evidence": [line for line in evidence.splitlines() if "HTTP" in line or "Error" in line][:6],
            "suggested_action": "先处理服务端上传/代理限制，再重跑",
            "can_auto_repair": False
        }
    if any(word in lower for word in ("ai call error", "failed to call ai model service", "request was aborted", "model-provider.html")):
        return {
            "category": "env_issue",
            "failure_type": "model_service",
            "confidence": 0.96,
            "reason": "Midscene 调用视觉模型服务时被中断或超时，不是 YAML 业务链路错误",
            "evidence": [line for line in evidence.splitlines() if "AI call error" in line or "Request was aborted" in line or "model-provider" in line][:8],
            "suggested_action": "先检查 Runner 模型环境、DashScope 网络连通性和重试执行；确认模型服务稳定后再判断是否需要修脚本",
            "can_auto_repair": False
        }
    horizontal = detect_horizontal_scroll_script_issue(task_block or yaml_text, evidence)
    if horizontal:
        horizontal["failure_type"] = "scroll_not_effective"
        return horizontal
    wait_issue = detect_wait_strategy_issue(task_block or yaml_text, evidence)
    if wait_issue:
        wait_issue["failure_type"] = "wait_strategy"
        return wait_issue
    if evidence_needs_adb_input_fallback(evidence):
        return {
            "category": "script_issue",
            "failure_type": "input_failed",
            "confidence": 0.9,
            "reason": "日志显示输入框未实际输入或输入失败，应修复输入动作",
            "evidence": brief.get("signals", [])[:8],
            "suggested_action": "修复 aiInput + value，必要时仅对安全文本加 ADB input 兜底",
            "can_auto_repair": True
        }
    if any(word in lower for word in ("unknown flowitem", "failed to load", "property \"tasks\" is required", "yaml格式", "yaml语法")):
        return {
            "category": "script_issue",
            "failure_type": "yaml_syntax",
            "confidence": 0.94,
            "reason": "执行日志显示 YAML 语法或 flowItem 不兼容",
            "evidence": brief.get("signals", [])[:8],
            "suggested_action": "优先规则修复 YAML 语法、flowItem 名称和缩进结构",
            "can_auto_repair": True
        }
    if any(word in evidence for word in ("弹窗", "权限", "遮挡", "浮层", "引导")):
        return {
            "category": "script_issue",
            "failure_type": "popup_overlay",
            "confidence": 0.82,
            "reason": "失败上下文出现弹窗/权限/浮层遮挡信号",
            "evidence": brief.get("signals", [])[:8],
            "suggested_action": "只在关键路径前增加弹窗/权限处理，然后继续原业务目标",
            "can_auto_repair": True
        }
    if any(word in lower for word in ("failed to locate", "not found", "cannot find")) or any(word in evidence for word in ("找不到", "未找到")):
        return {
            "category": "script_issue",
            "failure_type": "element_not_found",
            "confidence": 0.74,
            "reason": "目标元素未定位到，优先按脚本定位/导航问题处理一次；若修复后仍失败再转人工判断产品问题",
            "evidence": brief.get("signals", [])[:8],
            "suggested_action": "结合失败步骤前后上下文、页面知识和截图修正定位描述或导航",
            "can_auto_repair": True
        }
    if "assert" in lower or "断言" in evidence or "验证" in evidence:
        return {
            "category": "script_issue",
            "failure_type": "assertion_too_strict",
            "confidence": 0.68,
            "reason": "断言失败，先检查是否断言过严或不贴近业务可见状态",
            "evidence": brief.get("signals", [])[:8],
            "suggested_action": "把过严断言改成业务意图 + UI 可见信号，不删除关键断言",
            "can_auto_repair": True
        }
    return None


def repair_by_failure_type(yaml_text, ctx):
    failure_type = ((ctx.get("classification") or {}).get("failure_type") or "").strip()
    app_package = ctx.get("app_package", "")
    evidence_text = ctx.get("evidence_text", "")
    if failure_type in ("model_config", "device_env", "server_or_upload"):
        return yaml_text, []
    repaired, changes = normalize_yaml_runtime_guards(yaml_text, app_package=app_package, evidence_text=evidence_text)
    return repaired, changes


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


def attach_repair_result_metadata(result, old_yaml, new_yaml, repair_dir="", before_version=None, yaml_check=None, safety_warnings=None, business_check=None):
    result["changed_line_count"] = changed_line_count(old_yaml, new_yaml)
    result["diff_summary"] = yaml_diff_summary(old_yaml, new_yaml)
    if repair_dir:
        result["repair_dir"] = repair_dir
    if before_version:
        result["before_version"] = before_version
    if yaml_check is not None:
        result["yamlCheck"] = yaml_check
    if safety_warnings is not None:
        result["safetyCheck"] = {"ok": not safety_warnings, "warnings": safety_warnings}
    if business_check is not None:
        result["businessFlowCheck"] = business_check
    return result


def validate_repair_safety(old_yaml, new_yaml, ctx=None, task_name=""):
    warnings = []
    yaml_check = validate_midscene_yaml(new_yaml)
    if not yaml_check.get("ok"):
        warnings.extend(yaml_check.get("warnings", []))
    old_pkg = extract_app_package_from_yaml(old_yaml)
    new_pkg = extract_app_package_from_yaml(new_yaml)
    ctx_pkg = (ctx or {}).get("app_package", "")
    if old_pkg and new_pkg and old_pkg != new_pkg:
        warnings.append(f"修复后包名发生变化：{old_pkg} -> {new_pkg}")
    if ctx_pkg and new_pkg and ctx_pkg != new_pkg:
        warnings.append(f"修复后包名与当前模块/文件绑定不一致：{new_pkg} != {ctx_pkg}")
    if not old_pkg and not ctx_pkg and new_pkg:
        warnings.append(f"当前模块未绑定 App，禁止自动注入包名：{new_pkg}")
    diff_count = changed_line_count(old_yaml, new_yaml)
    task_count = max(1, len(yaml_task_names(old_yaml)))
    if diff_count > max(80, task_count * 45):
        warnings.append(f"修复改动过大：{diff_count} 行，疑似重写 YAML")
    if task_name:
        try:
            old_block = find_yaml_task_block(old_yaml, task_name)["block"]
            new_block = find_yaml_task_block(new_yaml, task_name)["block"]
            warnings.extend(validate_task_business_flow_preserved(old_block, new_block))
        except Exception as e:
            warnings.append(f"无法校验单条业务链路：{e}")
    else:
        warnings.extend(validate_yaml_business_flow_preserved(old_yaml, new_yaml))
    old_vague_texts = {business_assertion_warning_text(item) for item in validate_business_assertions(old_yaml)}
    vague_assertions = [
        item for item in validate_business_assertions(new_yaml)
        if business_assertion_warning_text(item) not in old_vague_texts
    ]
    warnings.extend(vague_assertions[:5])
    return warnings


def extract_failure_brief(stdout="", stderr="", summary=None):
    text = "\n".join([stdout or "", stderr or ""])
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    signal_patterns = (
        "error:", "Task failed:", "Assertion failed", "failed to locate", "Failed to continue",
        "unknown flowItem", "Model configuration", "No such file", "timeout", "Timed out",
        "Replanned", "exceeding the limit", "I can see", "Reason:", "toast",
        "mapper function returned", "returned a null value", "null value", "系统异常", "操作失败"
    )
    signals = []
    for idx, line in enumerate(lines):
        if any(pattern.lower() in line.lower() for pattern in signal_patterns):
            start = max(0, idx - 1)
            end = min(len(lines), idx + 3)
            for item in lines[start:end]:
                if item not in signals:
                    signals.append(item)
        if len(signals) >= 12:
            break

    failed_tasks = []
    for line in lines:
        m = re.search(r"[✘x]\s+(.+?)\s+\(task\s+\d+/\d+\)", line)
        if m and m.group(1).strip() not in failed_tasks:
            failed_tasks.append(m.group(1).strip())
    if isinstance(summary, dict):
        for key in ("failed", "failedTasks", "errors"):
            value = summary.get(key)
            if isinstance(value, list):
                for item in value[:6]:
                    if isinstance(item, dict):
                        name = item.get("name") or item.get("task") or item.get("title")
                        err = item.get("error") or item.get("message")
                        if name and name not in failed_tasks:
                            failed_tasks.append(str(name))
                        if err and str(err) not in signals:
                            signals.append(str(err)[:500])
                    elif item and str(item) not in signals:
                        signals.append(str(item)[:500])

    lower = text.lower()
    repair_plan = {
        "priority": "manual_review",
        "can_repair_yaml": False,
        "focus": [],
        "avoid": []
    }
    if any(word in lower for word in ("ai call error", "failed to call ai model service", "request was aborted", "model-provider.html")):
        failure_type = "model_service"
        repair_plan = {
            "priority": "environment_first",
            "can_repair_yaml": False,
            "focus": [
                "检查 Runner 侧模型环境变量是否包含 OPENAI_API_KEY、OPENAI_BASE_URL、MIDSCENE_MODEL_NAME、MIDSCENE_USE_QWEN_VL=1",
                "检查 Windows Runner 到 DashScope compatible-mode 接口的网络连通性和超时",
                "确认最新部署包已下发 runtime-env，必要时重启 Runner 清理旧环境缓存"
            ],
            "avoid": ["不要把模型服务中断误判成元素定位问题", "不要自动修 YAML", "不要删除或放宽业务断言"]
        }
    elif any(word in lower for word in ("unknown flowitem", "property", "yaml", "failed to load")):
        failure_type = "yaml_syntax"
        repair_plan = {
            "priority": "rule_first",
            "can_repair_yaml": True,
            "focus": ["修复 flowItem 名称大小写、冒号空格、缩进、aiAssert/aiInput 子字段结构", "不改变业务路径和断言含义"],
            "avoid": ["不要重写整条业务链路", "不要新增无关点击"]
        }
    elif any(word in lower for word in ("model configuration", "api_key", "base url", "midscene_model_name")):
        failure_type = "model_config"
        repair_plan = {
            "priority": "environment_first",
            "can_repair_yaml": False,
            "focus": ["检查 Midscene 模型环境变量和 API 配置"],
            "avoid": ["不要修改 YAML 业务步骤"]
        }
    elif any(word in lower for word in ("adb", "device offline", "no device", "device not found")):
        failure_type = "device_env"
        repair_plan = {
            "priority": "environment_first",
            "can_repair_yaml": False,
            "focus": ["检查设备连接、adb devices、Sonic runner 设备占用"],
            "avoid": ["不要修改 YAML 业务步骤"]
        }
    elif runtime_toast_error_from_text(text):
        failure_type = "runtime_toast_error"
        repair_plan = {
            "priority": "product_or_data_first",
            "can_repair_yaml": False,
            "focus": ["报告或截图出现运行时 toast/错误浮层，优先按产品/数据/环境问题处理"],
            "avoid": ["不要删除断言", "不要通过加等待或放宽断言掩盖运行时错误"]
        }
    elif evidence_needs_adb_input_fallback(text):
        failure_type = "input_failed"
        repair_plan = {
            "priority": "targeted_yaml_repair",
            "can_repair_yaml": True,
            "focus": ["修复输入步骤：先 aiTap 输入框，再 aiInput + value", "只有确认 aiInput 没有实际输入时才允许 ADB input text 兜底", "避免重复输入"],
            "avoid": ["不要同时默认保留 aiInput 和 adb input text", "不要把中文输入改成 adb input text"]
        }
    elif evidence_is_toast_assertion_issue(text):
        failure_type = "toast_assertion"
        repair_plan = {
            "priority": "targeted_yaml_repair",
            "can_repair_yaml": True,
            "focus": [
                "保存/导出/下载/生成/转换这类结果型操作后，立即等待多个同义成功提示",
                "如果短暂提示消失，改用结果流程结束且无失败态作为兜底",
                "只校验没有保存失败、导出失败、下载失败、生成失败、转换失败、权限失败、网络错误或异常弹窗；不要要求页面保持静止或某个按钮仍可见"
            ],
            "avoid": ["不要把断言放宽成页面正常", "不要删除结果校验", "不要要求页面保持静止", "不要要求导出/保存按钮仍可见", "不要无限加长等待 toast"]
        }
    elif any(word in lower for word in ("failed to locate", "找不到", "not found", "cannot find")):
        failure_type = "element_not_found"
        repair_plan = {
            "priority": "targeted_yaml_repair",
            "can_repair_yaml": True,
            "focus": ["参考页面知识和失败截图修正入口文案/目标描述", "必要时补充从首页到目标页面的稳定导航", "点击动作使用明确 aiTap"],
            "avoid": ["不要坐标点击", "不要点击随机相似元素", "不要删除业务关键步骤"]
        }
    elif any(word in lower for word in ("assertion failed", "task failed:", "验证", "assert")):
        failure_type = "assertion_failed"
        repair_plan = {
            "priority": "review_then_repair",
            "can_repair_yaml": True,
            "focus": ["判断是否断言过严", "把断言改为真实可见、符合业务意图的 UI 状态", "如果页面确实不符合需求，保留为产品 Bug"],
            "avoid": ["不要为了通过删除关键断言", "不要把真实失败改成泛化的页面正常"]
        }
    elif any(word in lower for word in ("timeout", "timed out", "超时")):
        failure_type = "timeout"
        repair_plan = {
            "priority": "review_then_repair",
            "can_repair_yaml": True,
            "focus": ["区分环境超时和业务加载等待短", "短等待改成 aiWaitFor + 目标 UI 条件 + 合理 timeout", "只做一次等待策略修复"],
            "avoid": ["不要无限加长 timeout", "不要用固定长 sleep 代替条件等待"]
        }
    elif any(word in lower for word in ("弹窗", "dialog", "popup", "permission", "overlay", "遮挡")):
        failure_type = "popup_overlay"
        repair_plan = {
            "priority": "targeted_yaml_repair",
            "can_repair_yaml": True,
            "focus": ["只在关键路径前增加弹窗/权限/浮层处理", "处理后继续回到业务目标"],
            "avoid": ["不要每一步都加弹窗处理", "不要坐标关闭"]
        }
    else:
        failure_type = "unknown"

    return {
        "failure_type": failure_type,
        "failed_tasks": failed_tasks[:8],
        "signals": signals[:16],
        "repair_plan": repair_plan
    }


def repair_strategy_guide():
    return """
修复决策策略：
1. 先判断是否真的应该修脚本。模型配置、设备离线、网络断连、服务端 5xx、ADB 异常不应改 YAML，只在 analysis 里说明环境问题。
2. 修复优先级：YAML 语法/flowItem 名称/冒号空格/空 flow > App 启动和关闭 > 页面稳定起点 > 弹窗遮挡 > 加载等待 > 导航路径 > 断言表达。
3. 如果是 YAML 语法问题，只修语法，不改业务路径。常见问题包括 terminate:com.xxx 缺空格、tap/click/action 这类非标准 key、sleep 写成字符串、flow 为空。
4. 如果是启动/页面起点问题，优先补 HOME、force-stop、launch、首页稳定导航、底部首页 Tab、必要的 aiWaitFor。
5. 如果脚本还没跑到业务步骤，不要改业务步骤和断言；只补运行时守卫后让下一轮重跑。
6. 如果是找不到入口，优先参考页面知识和截图中的真实文案，增加从稳定页面到目标入口的导航路径；不要臆造按钮，不要坐标。
7. 如果是弹窗/权限/升级/广告/引导遮挡，只在关键路径前补自然语言弹窗处理，不要每一步都加，避免拖慢。
8. 如果是加载慢，使用 aiWaitFor + timeout 等目标 UI 条件，不要用固定长 sleep。Midscene 自身会重试/重规划，不要无限加长等待；任何新增或修改的 aiWaitFor timeout 都不得超过 300000ms。只有 3D/模型/建模/切片/STL/OBJ/模型导入这类链路才允许写“模型处理进度到 100%”和 180000~240000ms；2D/文档/错题/基础打印/相册/扫描/格式转换链路禁止套用“模型处理进度”，应等待“打印前准备完成、立即打印按钮、确认打印弹窗/按钮”等真实 UI 条件，通常 30000~60000ms。只能在“原等待明显偏短或条件过泛”时修一次，不要反复加长等待掩盖真实产品/环境问题。
8.1 如果失败发生在中间流程，例如点击“完成/确认/下一步”后目标格式按钮、PNG/PDF/Word、导出或确认按钮尚未渲染，应该在这两个业务动作之间补 aiWaitFor 等待目标按钮/选项出现，不要把它误修成最终保存成功校验。
8.2 横向 icon 列表/分页功能区不要用 ai 自然语言描述“向左滑/向右滑”。必须使用官方 aiScroll：目标写清楚具体横向区域，露出右侧隐藏入口时使用两次 `scrollType: "singleAction"` + `direction: "right"` + `distance: 400`。禁止生成 `distance: 200` 这类过短距离，也不要超过单次距离上限。注意 Midscene 的 direction 表示“哪个方向的内容进入屏幕”，不是手指滑动方向。Android 上横向 icon 区域默认在 aiScroll 后增加 `runAdbShell: "input swipe 950 1080 150 1080 500"` 作为兜底。
9. 如果是断言失败，先判断是否断言过严。可把“完全一致”改为“页面标题、关键入口、列表或空态可见”等视觉可验证断言；不要把真实产品缺陷改没。
9.1 如果失败是保存/导出/下载/生成/转换这类结果型操作的短暂提示没捕捉到，先结合原 YAML 的业务链路和失败截图判断。可以优化为更合理的成功提示或失败态校验，但只能改失败相关步骤，不要批量插入重复校验，不要改变中间业务流程。
10. 修复业务链路时，必须先对齐 goal、start_page、business_path、expected_result：入口路径可以修，等待条件可以修，断言表达可以修，但不能绕开核心业务目标。
11. 每个 aiTap 都必须有业务目的：进入目标页面、触发目标功能、选择目标条件、提交目标操作。不要为了“能点”而点击无关卡片、返回键、广告、推荐内容或随机入口。
12. 每个断言都必须验证业务结果：页面标题、目标入口状态、列表/空态、弹窗文案、按钮状态、结果区域。不要用“页面正常展示”“操作成功”这类无法对应业务目标的泛化断言替代真实预期。
13. 不要为了让用例通过而删除关键步骤或关键断言；只能把过严/不稳定的表述改成更贴近真实 UI 的可见断言。
14. 如果当前步骤和业务链路冲突，优先修正为页面知识/截图支持的真实链路；如果页面知识不足，不要大幅改写，只补稳定导航和更清晰断言。
15. 如果页面知识/截图与原 YAML 冲突，优先页面知识/截图；如果仍不确定，做最小改动并保留 baseline 注释。
16. 每次修复要最小化：只改失败相关 task 或相关步骤，不要重写大量用例，不要改变包名。
17. 输出 changes 要具体说明“为什么改、改了哪里”，便于人工审查。
18. 必须服从失败摘要里的 repair_plan：can_repair_yaml=false 时不要实质改业务 YAML；priority=rule_first 时只做确定性语法/结构修复；priority=targeted_yaml_repair 时只改对应失败点；priority=review_then_repair 时先判断是否可能是真产品问题，再做最小脚本修复。
19. 修复前必须阅读业务链路上下文里的 goal、start_page、business_path、expected_result、current_actions、current_assertions。修复后必须保留原业务目标和核心路径锚点；可以改入口描述、等待条件、断言表达，但不能把“测试什么功能”改成另一个功能。
""".strip()


def extract_baseline_meta_from_block(block):
    meta = {}
    for line in (block or "").splitlines():
        m = re.match(r"^\s*#\s*baseline\.([A-Za-z_]+)\s*:\s*(.*)$", line)
        if m:
            meta[m.group(1)] = m.group(2).strip()
    return meta


def task_business_context(task_block, knowledge_text=""):
    meta = extract_baseline_meta_from_block(task_block)
    actions = []
    assertions = []
    waits = []
    for key, text in flow_texts_from_task_block(task_block, {"aiTap", "aiAction", "aiAssert", "aiWaitFor"}):
        if key == "aiTap":
            actions.append(text)
        elif key in ("aiAssert", "aiWaitFor"):
            (waits if key == "aiWaitFor" else assertions).append(text)
        elif key == "aiAction":
            if text.startswith("验证："):
                assertions.append(text.replace("验证：", "", 1).strip())
            elif not text.startswith("确认前置条件："):
                actions.append(text)

    knowledge_lines = []
    for line in (knowledge_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if any(key in line for key in ("页面", "路径", "入口", "关键元素", "常用断言", "route", "page")):
            knowledge_lines.append(line)
        if len(knowledge_lines) >= 18:
            break

    return {
        "goal": meta.get("goal") or f"验证{task_name_from_block(task_block)}",
        "scenario": meta.get("scenario") or "",
        "start_page": meta.get("start_page") or "",
        "business_path": meta.get("path") or " -> ".join(actions[:10]),
        "expected_result": meta.get("expected") or "；".join(assertions[:8]),
        "repair_hint": meta.get("repair_hint") or "",
        "risk": meta.get("risk") or "",
        "coverage": meta.get("coverage") or "",
        "data_requirements": meta.get("data") or "",
        "automation_reason": meta.get("automation") or "",
        "current_actions": actions[:20],
        "current_assertions": assertions[:12],
        "current_waits": waits[:8],
        "matched_page_knowledge": knowledge_lines
    }


def normalize_patch_lines(lines, flow_indent):
    normalized = []
    for raw in lines or []:
        for part in str(raw).splitlines():
            stripped = part.strip()
            if not stripped:
                continue
            if stripped.startswith("- "):
                normalized.append(flow_indent + stripped)
            elif re.match(r"^[A-Za-z][\w]*\s*:", stripped):
                key = stripped.split(":", 1)[0].strip()
                if key in SUPPORTED_FLOW_ITEMS or key in FLOW_ITEM_ALIASES:
                    normalized.append(flow_indent + "- " + stripped)
                else:
                    normalized.append(flow_indent + "  " + stripped)
            else:
                normalized.append(flow_indent + "  " + stripped)
    return normalized


def line_matches_anchor(line, anchor):
    anchor = strip_yaml_quotes(anchor or "")
    if not anchor:
        return False
    plain = str(line or "").strip()
    normalized_plain = plain.replace("- ", "", 1).replace('"', "").replace("'", "")
    normalized_anchor = anchor.replace("- ", "", 1).replace('"', "").replace("'", "")
    return normalized_anchor in normalized_plain or normalized_anchor in plain or normalized_plain in normalized_anchor


def flow_item_end(lines, idx):
    end = idx + 1
    while end < len(lines):
        if re.match(r"^\s*-\s+[A-Za-z][\w]*\s*:", lines[end]):
            break
        end += 1
    return end


def apply_task_repair_patches(task_block, patches):
    if not patches:
        raise ValueError("模型未返回可应用的修复补丁")
    lines = normalize_task_block_indent(task_block, "").splitlines()
    flow_idx = next((idx for idx, line in enumerate(lines) if re.match(r"^\s*flow\s*:\s*$", line)), None)
    if flow_idx is None:
        raise ValueError("当前 task 缺少 flow，无法应用补丁")
    flow_indent = re.match(r"^(\s*)flow\s*:", lines[flow_idx]).group(1) + "  "
    applied = []
    for patch in patches[:2]:
        if not isinstance(patch, dict):
            continue
        op = (patch.get("op") or patch.get("type") or "").strip()
        anchor = patch.get("anchor") or patch.get("after") or patch.get("before") or ""
        patch_lines = normalize_patch_lines(patch.get("lines") or patch.get("content") or [], flow_indent)
        if op not in ("insert_after", "insert_before", "replace_step", "remove_step"):
            continue
        target = None
        for idx in range(flow_idx + 1, len(lines)):
            if re.match(r"^\s*-\s+[A-Za-z][\w]*\s*:", lines[idx]) and line_matches_anchor(lines[idx], anchor):
                target = idx
                break
        if target is None:
            raise ValueError(f"修复补丁锚点未找到：{anchor}")
        end = flow_item_end(lines, target)
        existing_window = "\n".join(lines[max(flow_idx, target - 2):min(len(lines), end + len(patch_lines) + 2)])
        if patch_lines and all(line.strip() in existing_window for line in patch_lines):
            continue
        if op == "insert_after":
            lines = lines[:end] + patch_lines + lines[end:]
        elif op == "insert_before":
            lines = lines[:target] + patch_lines + lines[target:]
        elif op == "replace_step":
            if not patch_lines:
                raise ValueError("replace_step 补丁缺少 lines")
            lines = lines[:target] + patch_lines + lines[end:]
        elif op == "remove_step":
            lines = lines[:target] + lines[end:]
        applied.append({
            "op": op,
            "anchor": anchor,
            "lines": [line.strip() for line in patch_lines],
            "reason": patch.get("reason", "")
        })
    if not applied:
        raise ValueError("没有补丁被应用，已拒绝覆盖 YAML")
    return "\n".join(lines).rstrip(), applied


def call_dashscope_repair_yaml_task_patch(module, file, task_name, yaml_text, task_block, stdout, stderr, summary, execution_images=None):
    log_text = "\n".join([
        "STDOUT:",
        (stdout or "")[-6000:],
        "STDERR:",
        (stderr or "")[-3000:],
        "SUMMARY:",
        json.dumps(summary, ensure_ascii=False)[:4000] if summary is not None else ""
    ])
    failure_brief = extract_failure_brief(stdout, stderr, summary)
    knowledge_text, knowledge_images, used_pages = repair_knowledge_context(module, file, yaml_text, log_text, task_name)
    execution_images = execution_images or []
    repair_images = (execution_images + knowledge_images)[:6]
    business_context = task_business_context(task_block, knowledge_text)
    skill_payload = {
        "module": module,
        "file": file,
        "task_name": task_name,
        "business_context": business_context,
        "failure_brief": failure_brief,
        "page_knowledge": knowledge_text or "",
        "task_block": task_block,
        "log_text": log_text,
        "repair_strategy": repair_strategy_guide(),
        "framework": {
            "task": "保存 YAML、应用补丁、校验语法和业务链路",
            "qwen": "失败分析和补丁规划",
            "midscene": "执行自然语言 UI intent",
            "sonic": "基线回归稳定性"
        }
    }
    try:
        parsed = run_ai_skill("repair_patch_planner", skill_payload, image_assets=repair_images, timeout=240)
        patches = parsed.get("patches") or []
        if not isinstance(patches, list):
            raise ValueError("模型返回的 patches 必须是数组")
        return {
            "analysis": parsed.get("analysis") or "",
            "changes": parsed.get("changes") or [],
            "patches": patches,
            "used_knowledge_pages": used_pages,
            "used_execution_screenshots": [item.get("name", "") for item in execution_images],
            "repair_patch_skill": "repair_patch_planner.v1"
        }
    except Exception as exc:
        legacy_error = str(exc)
    prompt = f"""
你是 Midscene Android UI 自动化 YAML 单用例修复助手。
你不能直接重写 YAML，只能输出补丁。服务端会应用补丁并校验语法与业务链路。

严格要求：
1. 只输出合法 JSON，不要 Markdown。
2. JSON 必须包含 analysis、changes、patches。
3. patches 最多 2 条，只允许修改当前失败点附近，不要批量重写。
4. 每条 patch 格式：
   {{"op":"insert_after|insert_before|replace_step|remove_step","anchor":"原 YAML 中某个完整步骤的关键文本","lines":["aiWaitFor: ...","timeout: 30000"],"reason":"为什么改"}}
5. lines 不要写 android/tasks/name/flow 外层，只写 flow 里的步骤或子字段；缩进由服务端处理。
6. 必须保留原业务主线：{business_context.get("business_path", "")}
7. 如果 failure_brief.repair_plan.can_repair_yaml=false，patches 必须为空，并在 analysis 说明应先处理环境/产品问题。
8. 不要输出完整 YAML，不要输出 task 字符串。
9. 必须按业务域修复等待条件：只有 3D/模型/建模/切片/STL/OBJ/模型导入链路才允许出现“模型处理进度/100%”等待；2D/文档/错题/基础打印/相册/扫描/格式转换链路禁止写“模型处理进度”，应等待目标按钮、打印前准备完成、确认弹窗或真实业务页面状态。
10. 不要把“确认打印”单独当成模型处理；它可能属于 2D 打印确认。是否需要长等待必须结合当前 task 的 goal/path/actions 判断。
11. 不能瞎改原流程：补丁只能围绕失败点附近做最小改动，禁止删除、重排、替换失败点之前已经成功执行的核心业务步骤；禁止把原本测试 A 功能的链路改成 B 功能链路。
12. 如果要替换步骤，replace_step 只能替换当前失败步骤或紧邻失败步骤；不得整段替换从入口到结果的主链路。服务端会校验业务锚点和步骤顺序，不通过会拒绝保存。

模块：{module}
文件：{file}
目标用例：{task_name}

业务链路上下文：
{json.dumps(business_context, ensure_ascii=False, indent=2)}

失败摘要：
{json.dumps(failure_brief, ensure_ascii=False, indent=2)}

页面知识：
{knowledge_text or "无"}

当前 task：
{task_block}

执行日志：
{log_text}

输出示例：
{{
  "analysis": "点击完成后 PNG 尚未渲染，需在完成后等待 PNG 出现",
  "changes": ["在 aiTap: 完成 后插入等待 PNG 出现"],
  "patches": [
    {{"op":"insert_after","anchor":"aiTap: 完成","lines":["aiWaitFor: 页面已完成处理并出现 PNG 选项","timeout: 30000"],"reason":"PNG 未渲染"}}
  ]
}}
"""
    parsed = normalize_model_json(dashscope_chat_content(prompt, repair_images, temperature=0.1))
    patches = parsed.get("patches") or parsed.get("patch") or []
    if not isinstance(patches, list):
        raise ValueError("模型返回的 patches 必须是数组")
    return {
        "analysis": parsed.get("analysis") or parsed.get("reason") or "",
        "changes": parsed.get("changes") or [],
        "patches": patches,
        "used_knowledge_pages": used_pages,
        "used_execution_screenshots": [item.get("name", "") for item in execution_images],
        "repair_patch_skill": "fallback_legacy_repair_prompt",
        "repair_patch_skill_error": legacy_error
    }


def call_dashscope_repair_yaml_task(module, file, task_name, yaml_text, task_block, stdout, stderr, summary, execution_images=None):
    dashscope_api_key()

    log_text = "\n".join([
        "STDOUT:",
        (stdout or "")[-6000:],
        "STDERR:",
        (stderr or "")[-3000:],
        "SUMMARY:",
        json.dumps(summary, ensure_ascii=False)[:4000] if summary is not None else ""
    ])
    failure_brief = extract_failure_brief(stdout, stderr, summary)
    knowledge_text, knowledge_images, used_pages = repair_knowledge_context(module, file, yaml_text, log_text, task_name)
    execution_images = execution_images or []
    repair_images = (execution_images + knowledge_images)[:6]
    business_context = task_business_context(task_block, knowledge_text)
    prompt = f"""
你是 Midscene Android UI 自动化 YAML 单用例修复助手。
请根据执行日志，只修复指定的这一条 task，让它更有机会在下一轮执行通过。

严格要求：
1. 只输出合法 JSON，不要 Markdown，不要解释。
2. JSON 必须包含 analysis、changes、task。
3. task 必须是字符串类型，内容是一条 YAML task block，必须从 "- name:" 开始；不要输出 JSON 对象、数组或 android/tasks 外层。
4. 只修复名为「{task_name}」的用例，不要新增其他用例。
5. 按 Midscene 1.7.20 YAML 语法生成：优先使用 ai、aiTap、aiInput + value、aiWaitFor、aiAssert、sleep、launch、runAdbShell；aiAction 仅作为旧脚本兼容，不要在新增步骤里优先使用；修复时不要新增 recordToReport。
5.1 flowItem 名称大小写必须严格正确，例如 aiTap、aiInput、aiWaitFor、aiAssert、runAdbShell；禁止输出 aitap、aiinput、aiwaitfor、runadbshell 这类小写变体。
6. 不要生成坐标点击。
7. 如果是断言过严，优先改成更贴近页面可见状态的 aiAssert 或 aiWaitFor 验证。
8. 如果是找不到入口，优先增加更明确的导航步骤、等待或先回到首页的步骤。
9. 保留前置 HOME + force-stop + launch 和后置 force-stop，包名沿用原内容。
10. 不要写 deviceId。
11. 如果日志表现为弹窗、权限框、升级框、广告、活动浮层、引导浮层遮挡，应在关键步骤前增加自然语言弹窗处理，不要用坐标。
12. 如果失败原因是当前页面不在预期页面，应增加回到首页、点击底部首页 Tab、重新进入目标入口等稳定导航步骤。
13. 如果提供了 APP 页面知识和辅助截图，必须优先参考真实页面标题、入口文案、按钮文案、Tab、常用断言来修复步骤和断言。
14. 辅助截图只用于理解真实页面，不要写坐标；如果截图和 YAML 文案冲突，优先使用截图/页面知识中的真实可见文案。
15. 保留并更新 task 内的 # baseline.goal / # baseline.start_page / # baseline.path / # baseline.expected / # baseline.repair_hint 注释；这些注释是基线链路说明，不是执行步骤。
16. 如果当前 task 缺少 baseline 注释，请根据用例名、步骤、页面知识和截图补齐；不要把这些说明改成执行步骤。
17. 不要滥用固定长等待。普通页面切换用短 sleep；如果网络、接口、资源加载可能较慢，要用 aiWaitFor + timeout 等待“页面标题/按钮/列表/空态/目标入口可见”，不要用 sleep: 5000 这类无条件等待；timeout 上限 300000ms，不能越修越长。
18. 如果失败更像产品 Bug、环境问题、模型配置问题、设备问题，不要为了通过而篡改业务断言；analysis 中说明原因，task 尽量只做安全的稳定性补充。
19. 华为/系统文件管理器、相册、文件选择器里的搜索框输入必须优先使用 Midscene 1.7.20 标准写法：先 aiTap 搜索输入框，再 aiInput: "当前页面的搜索输入框或文本输入框" 并在同级写 value: "实际输入内容"、autoDismissKeyboard: false、mode: "replace"；不要默认再补 runAdbShell: "input text xxx"，只有失败日志明确证明 aiInput 没有实际输入、输入框为空或无法输入时，才允许增加 adb input text 兜底，避免重复输入。
20. 必须先按业务链路上下文理解原 YAML：goal 是测试目的，business_path 是核心路径，expected_result 是业务预期，current_actions/current_assertions 是现有执行链路。修复只能围绕这些内容做最小改动，不能替换成另一个业务流程。
21. 保存、下载、导出、生成、转换类结果操作如果失败原因是“没看到成功/已保存/完成提示”，要先看原业务链路，不要模板化批量插入校验。只允许围绕失败点做最小改动，例如调整一个等待条件或补一个失败态断言；不要把中间“完成/确认/PNG”等步骤误判成最终保存结果。
22. 如果报错说明点击“完成/确认/下一步”后下一个目标按钮或格式选项尚未渲染，例如 PNG/PDF/Word/导出/确认按钮未出现，修复应只在该失败点附近补等待，不要顺手改其它步骤。
23. 业务域不能串台：只有 3D/模型/建模/切片/STL/OBJ/模型导入链路才允许写“模型处理进度/100%”等待；2D/文档/错题/基础打印/相册/扫描/格式转换链路禁止套用“模型处理进度”，要等待目标按钮、打印前准备完成、确认弹窗/按钮或真实业务页面状态。
24. 横向 icon 列表/分页区域必须用官方 aiScroll 结构，不要用 ai 自然语言滑动。目标入口在右侧隐藏时使用两次 `aiScroll: "具体横向区域"` + `scrollType: "singleAction"` + `direction: "right"` + `distance: 400`；禁止 `distance: 200`，也不要超过 Midscene 单次滚动距离上限。Android 横向 icon 区域默认补一条 `runAdbShell: "input swipe 950 1080 150 1080 500"` 兜底；不要同时写矛盾的“向右滑动/手指左划”。
25. 不能瞎改原流程：原 YAML 中已经成功到达的核心业务步骤、入口顺序和目标断言必须保留。修复只能调整失败点附近的定位描述、等待条件、输入参数、弹窗处理或断言表达；不得删除核心业务动作，不得把链路改成另一个功能。
26. 如果当前失败是产品 toast、业务错误、数据不满足、环境问题或模型配置问题，analysis 中说明原因，task 保持原流程，不要为了通过删断言、改目标、绕开失败页面。
27. 字符串必须完整闭合：任何 `ai/aiTap/aiAssert/aiWaitFor/runAdbShell` 等带引号的值，行尾必须有对应的结束引号；禁止输出 `- ai: "xxx` 这种未闭合字符串。无法确定时不要加外层引号，由服务端统一转义。

{repair_strategy_guide()}

模块：{module}
文件：{file}
目标用例：{task_name}

业务链路上下文：
{json.dumps(business_context, ensure_ascii=False, indent=2)}

失败摘要：
{json.dumps(failure_brief, ensure_ascii=False, indent=2)}

自动匹配到的 APP 页面知识：
{knowledge_text or "无"}

已附加 Midscene 失败现场截图数量：{len(execution_images)}
已附加页面辅助截图数量：{len(knowledge_images)}

完整 YAML 仅供上下文参考：
{yaml_text[-10000:]}

当前要修复的 task：
{task_block}

执行日志：
{log_text}

输出格式：
{{
  "analysis": "失败原因简述",
  "changes": ["修改点1", "修改点2"],
  "task": "- name: \\"{task_name}\\"\\n  flow:\\n    - ..."
}}
"""
    result = normalize_yaml_task_block_from_model(dashscope_chat_content(prompt, repair_images, temperature=0.1))
    result["used_knowledge_pages"] = used_pages
    result["used_execution_screenshots"] = [item.get("name", "") for item in execution_images]
    return result


def call_dashscope_repair_yaml(module, file, yaml_text, stdout, stderr, summary, execution_images=None):
    dashscope_api_key()

    log_text = "\n".join([
        "STDOUT:",
        (stdout or "")[-6000:],
        "STDERR:",
        (stderr or "")[-3000:],
        "SUMMARY:",
        json.dumps(summary, ensure_ascii=False)[:4000] if summary is not None else ""
    ])
    failure_brief = extract_failure_brief(stdout, stderr, summary)
    knowledge_text, knowledge_images, used_pages = repair_knowledge_context(module, file, yaml_text, log_text)
    execution_images = execution_images or []
    repair_images = (execution_images + knowledge_images)[:6]
    names = yaml_task_names(yaml_text)
    business_contexts = []
    for name in names[:12]:
        try:
            info = find_yaml_task_block(yaml_text, name)
            business_contexts.append({"task": name, **task_business_context(info["block"], knowledge_text)})
        except Exception:
            continue
    prompt = f"""
你是 Midscene Android UI 自动化 YAML 修复助手。
请根据执行日志修复 YAML，让它更有机会在下一轮执行通过。

严格要求：
1. 只输出合法 JSON，不要 Markdown，不要解释。
2. JSON 必须包含 analysis、changes、content。
3. content 必须是字符串类型，内容是完整 YAML，不是片段；不要输出 JSON 对象或数组。
4. 只修改和失败原因相关的步骤，不要重写无关用例。
5. 按 Midscene 1.7.20 YAML 语法生成：优先使用 ai、aiTap、aiInput + value、aiWaitFor、aiAssert、sleep、launch、runAdbShell；aiAction 仅作为旧脚本兼容，不要在新增步骤里优先使用；修复时不要新增 recordToReport。
5.1 flowItem 名称大小写必须严格正确，例如 aiTap、aiInput、aiWaitFor、aiAssert、runAdbShell；禁止输出 aitap、aiinput、aiwaitfor、runadbshell 这类小写变体。
6. 不要生成坐标点击。
7. 如果是断言过严，优先把断言改成更贴近页面可见状态的 aiAssert 或 aiWaitFor 验证。
8. 如果是找不到入口，优先增加更明确的导航步骤或等待。
9. 保留 android 节点，不要写 deviceId。
10. 每条 task 都必须能独立执行：包含启动 App、处理弹窗/浮层、进入稳定起点、执行步骤、验证结果、关闭 App。
11. 如果日志表现为弹窗、权限框、升级框、广告、活动浮层、引导浮层遮挡，应在关键步骤前增加自然语言弹窗处理，不要用坐标。
12. 如果失败原因是当前页面不在预期页面，应增加回到首页、点击底部首页 Tab、重新进入目标入口等稳定导航步骤。
13. 如果提供了 APP 页面知识和辅助截图，必须优先参考真实页面标题、入口文案、按钮文案、Tab、常用断言来修复步骤和断言。
14. 辅助截图只用于理解真实页面，不要写坐标；如果截图和 YAML 文案冲突，优先使用截图/页面知识中的真实可见文案。
15. 保留并更新每条 task 内的 # baseline.goal / # baseline.start_page / # baseline.path / # baseline.expected / # baseline.repair_hint 注释；这些注释是基线链路说明，不是执行步骤。
16. 如果某条 task 缺少 baseline 注释，请根据用例名、步骤、页面知识和截图补齐；不要把这些说明改成执行步骤。
17. 不要滥用固定长等待。普通页面切换用短 sleep；如果网络、接口、资源加载可能较慢，要用 aiWaitFor + timeout 等待“页面标题/按钮/列表/空态/目标入口可见”，不要用 sleep: 5000 这类无条件等待；timeout 上限 300000ms，不能越修越长。
18. 如果失败更像产品 Bug、环境问题、模型配置问题、设备问题，不要为了通过而篡改业务断言；analysis 中说明原因，content 尽量只做安全的稳定性补充。
19. 华为/系统文件管理器、相册、文件选择器里的搜索框输入必须优先使用 Midscene 1.7.20 标准写法：先 aiTap 搜索输入框，再 aiInput: "当前页面的搜索输入框或文本输入框" 并在同级写 value: "实际输入内容"、autoDismissKeyboard: false、mode: "replace"；不要默认再补 runAdbShell: "input text xxx"，只有失败日志明确证明 aiInput 没有实际输入、输入框为空或无法输入时，才允许增加 adb input text 兜底，避免重复输入。
20. 必须先按业务链路上下文理解原 YAML：goal 是测试目的，business_path 是核心路径，expected_result 是业务预期，current_actions/current_assertions 是现有执行链路。修复只能围绕这些内容做最小改动，不能替换成另一个业务流程。
21. 保存、下载、导出、生成、转换类结果操作如果失败原因是“没看到成功/已保存/完成提示”，要先看原业务链路，不要模板化批量插入校验。只允许围绕失败点做最小改动，例如调整一个等待条件或补一个失败态断言；不要把中间“完成/确认/PNG”等步骤误判成最终保存结果。
22. 如果报错说明点击“完成/确认/下一步”后下一个目标按钮或格式选项尚未渲染，例如 PNG/PDF/Word/导出/确认按钮未出现，修复应只在该失败点附近补等待，不要顺手改其它步骤。

{repair_strategy_guide()}

模块：{module}
文件：{file}

业务链路上下文：
{json.dumps(business_contexts, ensure_ascii=False, indent=2)}

失败摘要：
{json.dumps(failure_brief, ensure_ascii=False, indent=2)}

自动匹配到的 APP 页面知识：
{knowledge_text or "无"}

已附加 Midscene 失败现场截图数量：{len(execution_images)}
已附加页面辅助截图数量：{len(knowledge_images)}

当前 YAML：
{yaml_text}

执行日志：
{log_text}

输出格式：
{{
  "analysis": "失败原因简述",
  "changes": ["修改点1", "修改点2"],
  "content": "完整 YAML 内容"
}}
"""
    result = normalize_yaml_from_model(dashscope_chat_content(prompt, repair_images, temperature=0.1))
    result["used_knowledge_pages"] = used_pages
    result["used_execution_screenshots"] = [item.get("name", "") for item in execution_images]
    return result


def call_dashscope_failure_review(job, stdout, stderr, summary):
    dashscope_api_key()
    module = job.get("module", "")
    file = job.get("file", "")
    yaml_path = safe_join(TASK_DIR, module, file)
    yaml_text = ""
    if os.path.exists(yaml_path):
        with open(yaml_path, encoding="utf-8") as f:
            yaml_text = f.read()
    deterministic_issues = []
    if "launch:" not in yaml_text:
        deterministic_issues.append("缺少 launch 前置启动 App")
    if "terminate:" not in yaml_text and "am force-stop" not in yaml_text:
        deterministic_issues.append("缺少后置关闭 App")
    if deterministic_issues:
        return {
            "category": "script_issue",
            "confidence": 0.95,
            "reason": "；".join(deterministic_issues),
            "evidence": deterministic_issues,
            "suggested_action": "优先执行规则修复，补齐启动/关闭等运行时守卫后再重跑",
            "can_auto_repair": True
        }
    ctx = build_failure_context(job, yaml_text, stdout, stderr, summary)
    review_images = (execution_screenshot_context(job, limit=4) + report_image_context(job, limit=4))[:6]
    log_text = "\n".join([
        "STDOUT:",
        (stdout or "")[-6000:],
        "STDERR:",
        (stderr or "")[-3000:],
        "SUMMARY:",
        json.dumps(summary, ensure_ascii=False)[:4000] if summary is not None else "",
        "REPORT_TEXT:",
        (ctx.get("report_text") or "")[-6000:]
    ])
    deterministic_review = classify_failure_by_context(ctx)
    if deterministic_review:
        low_confidence_visual_types = {"assertion_too_strict", "element_not_found", "wait_strategy", "popup_overlay"}
        if not (review_images and deterministic_review.get("failure_type") in low_confidence_visual_types):
            return sanitize_failure_review_against_sources(deterministic_review, yaml_text, stdout, stderr, summary, ctx)
    wait_issue = detect_wait_strategy_issue(yaml_text, log_text)
    if wait_issue:
        return sanitize_failure_review_against_sources(wait_issue, yaml_text, stdout, stderr, summary, ctx)
    horizontal_scroll_issue = detect_horizontal_scroll_script_issue(yaml_text, log_text)
    if horizontal_scroll_issue:
        return sanitize_failure_review_against_sources(horizontal_scroll_issue, yaml_text, stdout, stderr, summary, ctx)
    yaml_syntax_signals = ("unknown flowitem", "failed to load", "property \"tasks\" is required", "cannot use 'in' operator", "yaml格式", "yaml语法")
    if any(signal in log_text.lower() for signal in yaml_syntax_signals):
        return {
            "category": "script_issue",
            "confidence": 0.92,
            "reason": "执行日志显示 YAML 语法或 flowItem 不兼容",
            "evidence": [line for line in log_text.splitlines() if any(signal in line.lower() for signal in yaml_syntax_signals)][:6],
            "suggested_action": "优先规则修复 YAML 语法、flowItem 名称和缩进结构，不改业务断言",
            "can_auto_repair": True
        }
    prompt = f"""
你是移动 App 测试失败复检助手。
请根据 YAML、执行日志和 summary 判断失败更可能属于哪一类。

分类只能选择：
- product_bug：疑似产品缺陷
- script_issue：疑似脚本问题
- env_issue：疑似环境/设备/模型/网络问题
- data_issue：疑似测试数据或账号状态问题
- unknown：无法判断

要求：
1. 只输出合法 JSON，不要 Markdown。
2. 不要因为脚本失败就默认是脚本问题；测试模式下要优先保护真实缺陷。
3. 如果页面行为和需求预期不一致，归为 product_bug。
4. 如果 YAML 动作明显不合理、入口文案臆造、断言过严，归为 script_issue。
4.1 如果业务本身需要长时间处理（模型加载、切片、上传、生成、进度条到 100%、确认打印按钮出现），并且 YAML 只等待 20~30 秒或等待条件过于泛化，可以先归为 script_issue，建议只做一次等待策略修复；如果 YAML 已经有足够长的条件等待后仍失败，不要继续放宽脚本，应回到 product_bug/env_issue/unknown 复核。
4.2 如果失败目标是横向 icon 列表中隐藏入口，例如“试卷夹”，且 YAML 失败点前存在 aiScroll 横向列表操作，但当前截图仍只显示前几个入口，应归为 script_issue：滑动没有真实生效或距离/方向/兜底不足，不要判 product_bug。
5. 如果截图或报告中出现 toast/浮层/运行时错误文案，例如“The mapper function returned a null value.”、“系统异常”、“操作失败”，并且页面没有达到业务预期，应优先归为 product_bug 或 data_issue，can_auto_repair=false；不要把它当成普通“按钮没找到”去放宽断言。
6. 如果是设备断连、模型配置、adb、超时、网络，归为 env_issue。
7. 严格禁止引用当前 YAML、日志、summary、报告文本中没有出现过的按钮、控件或步骤；如果无法确认，就归为 unknown，can_auto_repair=false。比如当前 YAML 没有“确认打印”，日志也没有“确认打印”，就不能说脚本等待“确认打印”。
8. 区分“YAML 当前内容”和“产品知识/历史经验”：只能把当前 YAML flow 中真实存在的步骤称为“脚本步骤”。

任务：{module}/{file}
执行模式：{job.get("run_mode", "test")}

YAML：
{yaml_text[-8000:]}

日志：
{log_text}

如果本次任务有执行截图，下面会随请求一起发送。请重点观察截图中间或底部是否出现 toast、半透明浮层、错误文案、加载失败、系统异常等短暂提示。

输出格式：
{{
  "category": "product_bug",
  "confidence": 0.8,
  "reason": "失败原因简述",
  "evidence": ["证据1", "证据2"],
  "suggested_action": "建议动作",
  "can_auto_repair": false
}}
"""
    review = normalize_model_json(dashscope_chat_content(prompt, image_assets=review_images, temperature=0.1, timeout=240))
    category = review.get("category") or "unknown"
    if category not in ("product_bug", "script_issue", "env_issue", "data_issue", "unknown"):
        category = "unknown"
    normalized_review = {
        "category": category,
        "confidence": float(review.get("confidence") or 0),
        "reason": review.get("reason", ""),
        "evidence": review.get("evidence") or [],
        "suggested_action": review.get("suggested_action", ""),
        "can_auto_repair": safe_bool(review.get("can_auto_repair")) and category == "script_issue"
    }
    return sanitize_failure_review_against_sources(normalized_review, yaml_text, stdout, stderr, summary, ctx)


def optimize_yaml_after_failure(job, stdout, stderr, summary):
    module = job.get("module", "")
    file = job.get("file", "")
    yaml_path = safe_join(TASK_DIR, module, file)
    with open(yaml_path, encoding="utf-8") as f:
        old_yaml = f.read()

    evidence_text = "\n".join([stdout or "", stderr or "", json.dumps(summary, ensure_ascii=False)[:3000] if summary is not None else ""])
    app_package = resolve_app_package(module, file, old_yaml)
    ctx = build_failure_context(job, old_yaml, stdout, stderr, summary)
    classification = classify_failure_by_context(ctx) or {}
    ctx["classification"] = classification
    guarded_yaml, guard_changes = repair_by_failure_type(old_yaml, ctx)
    execution_images = execution_screenshot_context(job)
    ai_repaired = None
    if classification.get("can_auto_repair") and classification.get("failure_type") in ("yaml_syntax", "scroll_not_effective", "wait_strategy", "input_failed", "popup_overlay"):
        repaired = {
            "analysis": classification.get("reason", "确定性规则修复"),
            "changes": guard_changes,
            "content": guarded_yaml
        }
    elif should_use_rule_only_repair(old_yaml, guard_changes, stdout, stderr, summary):
        repaired = {
            "analysis": "规则优先修复：脚本缺少运行时前置/后置、未进入 App、或尚未可靠跑到业务步骤，先只补齐启动/关闭/等待等确定性问题，不改业务步骤和断言。",
            "changes": guard_changes,
            "content": guarded_yaml
        }
    else:
        ai_repaired = call_dashscope_repair_yaml(module, file, guarded_yaml, stdout, stderr, summary, execution_images=execution_images)
        normalized_content, normalized_changes = normalize_yaml_runtime_guards(ai_repaired["content"], app_package=app_package, evidence_text=evidence_text)
        repaired = {
            "analysis": ai_repaired.get("analysis", ""),
            "changes": guard_changes + ai_repaired.get("changes", []) + normalized_changes,
            "content": normalized_content
        }
    repaired["content"] = normalize_full_yaml_structure(repaired["content"])
    safety_warnings = validate_repair_safety(old_yaml, repaired["content"], ctx)
    if safety_warnings:
        raise ValueError("修复后的 YAML 安全检查未通过：" + "；".join(safety_warnings[:8]))
    yaml_check = validate_midscene_yaml(repaired["content"])

    repair_dir = safe_join(LEARNING_DIR, "repairs", job.get("job_id", new_job_id()))
    os.makedirs(repair_dir, exist_ok=True)
    write_text_file(safe_join(repair_dir, "before.yaml"), old_yaml)
    write_text_file(safe_join(repair_dir, "after.yaml"), repaired["content"])
    business_check = validate_yaml_business_flow_preserved(old_yaml, repaired["content"])
    write_json_file(safe_join(repair_dir, "repair.json"), {
        "job": job,
        "analysis": repaired.get("analysis", ""),
        "changes": repaired.get("changes", []),
        "rule_changes": guard_changes,
        "ai_repaired": bool(ai_repaired),
        "used_knowledge_pages": repaired.get("used_knowledge_pages", []),
        "used_execution_screenshots": repaired.get("used_execution_screenshots", []),
        "yamlCheck": yaml_check,
        "classification": classification,
        "safetyCheck": {"ok": not safety_warnings, "warnings": safety_warnings},
        "businessFlowCheck": business_check,
        "changed_line_count": changed_line_count(old_yaml, repaired["content"]),
        "diff_summary": yaml_diff_summary(old_yaml, repaired["content"]),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    })

    before_version = save_file_version(module, file, content=old_yaml, reason="before_ai_repair")
    write_text_file(yaml_path, repaired["content"])
    update_task_meta(module, file, {
        "last_repair_job_id": job.get("job_id", ""),
        "last_repair_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_repair_status": "updated",
        "last_repair_changes": repaired.get("changes", [])[:20]
    })
    attach_repair_result_metadata(
        repaired,
        old_yaml,
        repaired["content"],
        repair_dir=repair_dir,
        before_version=before_version,
        yaml_check=yaml_check,
        safety_warnings=safety_warnings,
        business_check=business_check
    )
    return repaired, repair_dir


def optimize_yaml_task_after_failure(job, task_name, stdout, stderr, summary):
    module = job.get("module", "")
    file = job.get("file", "")
    yaml_path = safe_join(TASK_DIR, module, file)
    with open(yaml_path, encoding="utf-8") as f:
        old_yaml = f.read()

    task_info = find_yaml_task_block(old_yaml, task_name)
    evidence_text = "\n".join([stdout or "", stderr or "", json.dumps(summary, ensure_ascii=False)[:3000] if summary is not None else ""])
    app_package = resolve_app_package(module, file, old_yaml)
    platform = detect_yaml_platform(old_yaml)
    ctx = build_failure_context(job, old_yaml, stdout, stderr, summary, task_name=task_info["name"])
    classification = classify_failure_by_context(ctx) or {}
    ctx["classification"] = classification
    guarded_yaml_for_ctx, whole_guard_changes = repair_by_failure_type(old_yaml, ctx)
    try:
        guarded_block = find_yaml_task_block(guarded_yaml_for_ctx, task_info["name"])["block"]
        guard_changes = whole_guard_changes
    except Exception:
        guarded_block, guard_changes = normalize_task_block_runtime_guards(task_info["block"], app_package=app_package, evidence_text=evidence_text, platform=platform)
    execution_images = execution_screenshot_context(job)
    ai_repaired = None
    if classification.get("can_auto_repair") and classification.get("failure_type") in ("yaml_syntax", "scroll_not_effective", "wait_strategy", "input_failed", "popup_overlay"):
        repaired = {
            "analysis": classification.get("reason", "确定性规则修复"),
            "changes": guard_changes,
            "content": guarded_block
        }
    elif should_use_rule_only_repair(task_info["block"], guard_changes, stdout, stderr, summary):
        repaired = {
            "analysis": "规则优先修复：该用例缺少运行时前置/后置、未进入 App、或尚未可靠跑到业务步骤，先只补齐启动/关闭/等待等确定性问题，不改业务步骤和断言。",
            "changes": guard_changes,
            "content": guarded_block
        }
    else:
        ai_repaired = call_dashscope_repair_yaml_task_patch(module, file, task_info["name"], old_yaml, guarded_block, stdout, stderr, summary, execution_images=execution_images)
        patched_block, applied_patches = apply_task_repair_patches(guarded_block, ai_repaired.get("patches") or [])
        normalized_block, normalized_changes = normalize_task_block_runtime_guards(patched_block, app_package=app_package, evidence_text=evidence_text, platform=platform)
        repaired = {
            "analysis": ai_repaired.get("analysis", ""),
            "changes": guard_changes + ai_repaired.get("changes", []) + [f"应用补丁：{item.get('op')} {item.get('anchor')}" for item in applied_patches] + normalized_changes,
            "content": normalized_block,
            "patches": applied_patches
        }
    new_yaml = normalize_full_yaml_structure(replace_yaml_task_block(old_yaml, task_info, repaired["content"]))
    safety_warnings = validate_repair_safety(old_yaml, new_yaml, ctx, task_name=task_info["name"])
    if safety_warnings:
        raise ValueError("修复后的 YAML 安全检查未通过：" + "；".join(safety_warnings[:8]))
    yaml_check = validate_midscene_yaml(new_yaml)

    repair_dir = safe_join(LEARNING_DIR, "repairs", f"{job.get('job_id', new_job_id())}_{clean_id(task_info['name'], 'task')}")
    os.makedirs(repair_dir, exist_ok=True)
    write_text_file(safe_join(repair_dir, "before.yaml"), old_yaml)
    write_text_file(safe_join(repair_dir, "before-task.yaml"), task_info["block"] + "\n")
    write_text_file(safe_join(repair_dir, "after-task.yaml"), repaired["content"].strip("\n") + "\n")
    write_text_file(safe_join(repair_dir, "after.yaml"), new_yaml)
    business_check = business_alignment_report(task_info["block"], repaired["content"])
    write_json_file(safe_join(repair_dir, "repair.json"), {
        "job": job,
        "taskName": task_info["name"],
        "analysis": repaired.get("analysis", ""),
        "changes": repaired.get("changes", []),
        "patches": repaired.get("patches", []),
        "rule_changes": guard_changes,
        "ai_repaired": bool(ai_repaired),
        "used_knowledge_pages": repaired.get("used_knowledge_pages", []),
        "used_execution_screenshots": repaired.get("used_execution_screenshots", []),
        "yamlCheck": yaml_check,
        "classification": classification,
        "safetyCheck": {"ok": not safety_warnings, "warnings": safety_warnings},
        "businessFlowCheck": business_check,
        "changed_line_count": changed_line_count(old_yaml, new_yaml),
        "diff_summary": yaml_diff_summary(old_yaml, new_yaml),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    })

    before_version = save_file_version(module, file, content=old_yaml, reason="before_ai_task_repair")
    write_text_file(yaml_path, new_yaml)
    update_task_meta(module, file, {
        "last_repair_job_id": job.get("job_id", ""),
        "last_repair_task_name": task_info["name"],
        "last_repair_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_repair_status": "updated",
        "last_repair_changes": repaired.get("changes", [])[:20]
    })
    attach_repair_result_metadata(
        repaired,
        old_yaml,
        new_yaml,
        repair_dir=repair_dir,
        before_version=before_version,
        yaml_check=yaml_check,
        safety_warnings=safety_warnings,
        business_check=business_check
    )
    return repaired, repair_dir


def latest_failed_job_for_file(module, file):
    with JOB_LOCK:
        jobs = load_jobs()
    for job in reversed(jobs):
        if job.get("module") == module and job.get("file") == file and job.get("status") != "success" and job.get("run_dir"):
            return job
    return None


def latest_failed_job_for_file_task(module, file, task_name):
    with JOB_LOCK:
        jobs = load_jobs()
    target = (task_name or "").strip()
    for job in reversed(jobs):
        if job.get("module") != module or job.get("file") != file or job.get("status") == "success" or not job.get("run_dir"):
            continue
        if target and job.get("target_task_name") == target:
            return job
        stdout = read_text(Path(job.get("run_dir", "")) / "stdout.log")
        stderr = read_text(Path(job.get("run_dir", "")) / "stderr.log")
        if not target or target in stdout or target in stderr:
            return job
    return None


def should_block_manual_repair(job, stdout, stderr, summary):
    run_mode = job.get("run_mode", "test")
    if run_mode == "baseline":
        return None
    try:
        review = call_dashscope_failure_review(job, stdout, stderr, summary)
    except Exception:
        return None
    if review.get("category") in ("product_bug", "env_issue", "data_issue"):
        return review
    return None


def repair_job_and_create_next(job, create_next=True, force=False):
    run_dir = job.get("run_dir", "")
    stdout = read_text(Path(run_dir) / "stdout.log") if run_dir else job.get("stdout_tail", "")
    stderr = read_text(Path(run_dir) / "stderr.log") if run_dir else job.get("stderr_tail", "")
    summary = read_json(Path(run_dir) / "summary.json") if run_dir else None
    failure_brief = extract_failure_brief(stdout, stderr, summary)
    if failure_brief.get("failure_type") in ("model_config", "device_env"):
        raise ValueError("当前失败属于环境/配置问题，不应修改 YAML：" + "；".join(failure_brief.get("signals", [])[:3]))
    blocked = None if force else should_block_manual_repair(job, stdout, stderr, summary)
    if blocked:
        raise ValueError(f"当前失败更像 {blocked.get('category')}，不建议自动修复脚本：{blocked.get('reason') or '请人工确认'}")
    repaired, repair_dir = optimize_yaml_after_failure(job, stdout, stderr, summary)
    next_job = None
    if create_next:
        next_job = create_pending_job(
            job["module"],
            job["file"],
            auto_optimize=False,
            max_attempt=safe_int(job.get("max_attempt"), 2),
            attempt=safe_int(job.get("attempt"), 1) + 1,
            parent_job_id=job.get("job_id", ""),
            device_id=job.get("device_id", ""),
            runner_id=job.get("target_runner_id") or job.get("runner_id", ""),
            run_mode=job.get("run_mode", "test"),
            target_task_name=job.get("target_task_name", "")
        )
    return {
        "ok": True,
        "analysis": repaired.get("analysis", ""),
        "changes": repaired.get("changes", []),
        "repair_dir": repair_dir,
        "updated_file": f"{job.get('module', '')}/{job.get('file', '')}",
        "before_version": repaired.get("before_version"),
        "changed_line_count": repaired.get("changed_line_count", 0),
        "diff_summary": repaired.get("diff_summary", ""),
        "yamlCheck": repaired.get("yamlCheck"),
        "safetyCheck": repaired.get("safetyCheck"),
        "businessFlowCheck": repaired.get("businessFlowCheck"),
        "next_job": next_job
    }


def repair_job_task_and_create_next(job, task_name, create_next=True, force=False):
    run_dir = job.get("run_dir", "")
    stdout = read_text(Path(run_dir) / "stdout.log") if run_dir else job.get("stdout_tail", "")
    stderr = read_text(Path(run_dir) / "stderr.log") if run_dir else job.get("stderr_tail", "")
    summary = read_json(Path(run_dir) / "summary.json") if run_dir else None
    failure_brief = extract_failure_brief(stdout, stderr, summary)
    if failure_brief.get("failure_type") in ("model_config", "device_env"):
        raise ValueError("当前失败属于环境/配置问题，不应修改 YAML：" + "；".join(failure_brief.get("signals", [])[:3]))
    blocked = None if force else should_block_manual_repair(job, stdout, stderr, summary)
    if blocked:
        raise ValueError(f"当前失败更像 {blocked.get('category')}，不建议自动修复脚本：{blocked.get('reason') or '请人工确认'}")
    repaired, repair_dir = optimize_yaml_task_after_failure(job, task_name, stdout, stderr, summary)
    next_job = None
    if create_next:
        next_job = create_pending_job(
            job["module"],
            job["file"],
            auto_optimize=False,
            max_attempt=safe_int(job.get("max_attempt"), 2),
            attempt=safe_int(job.get("attempt"), 1) + 1,
            parent_job_id=job.get("job_id", ""),
            device_id=job.get("device_id", ""),
            runner_id=job.get("target_runner_id") or job.get("runner_id", ""),
            run_mode=job.get("run_mode", "test"),
            target_task_name=task_name
        )
    return {
        "ok": True,
        "taskName": task_name,
        "source_job_id": job.get("job_id", ""),
        "analysis": repaired.get("analysis", ""),
        "changes": repaired.get("changes", []),
        "repair_dir": repair_dir,
        "updated_file": f"{job.get('module', '')}/{job.get('file', '')}",
        "before_version": repaired.get("before_version"),
        "changed_line_count": repaired.get("changed_line_count", 0),
        "diff_summary": repaired.get("diff_summary", ""),
        "yamlCheck": repaired.get("yamlCheck"),
        "safetyCheck": repaired.get("safetyCheck"),
        "businessFlowCheck": repaired.get("businessFlowCheck"),
        "next_job": next_job
    }


def optimize_job_yaml_by_scope(job, stdout, stderr, summary):
    task_name = (job.get("target_task_name") or "").strip()
    if task_name:
        return optimize_yaml_task_after_failure(job, task_name, stdout, stderr, summary)
    return optimize_yaml_after_failure(job, stdout, stderr, summary)


def static_repair_yaml_file(module, file, create_next=False):
    yaml_path = safe_join(TASK_DIR, module, file)
    with open(yaml_path, encoding="utf-8") as f:
        old_yaml = f.read()
    app_package = resolve_app_package(module, file, old_yaml)
    repaired_yaml, changes = normalize_yaml_runtime_guards(old_yaml, app_package=app_package, evidence_text="")
    repaired_yaml = normalize_full_yaml_structure(repaired_yaml)
    yaml_check = validate_midscene_yaml(repaired_yaml)
    if not yaml_check["ok"]:
        raise ValueError("静态修复后的 YAML 基础检查未通过：" + "；".join(yaml_check["warnings"]))
    repair_dir = safe_join(LEARNING_DIR, "repairs", f"{generate_job_id()}_static_{clean_id(file, 'file')}")
    os.makedirs(repair_dir, exist_ok=True)
    write_text_file(safe_join(repair_dir, "before.yaml"), old_yaml)
    write_text_file(safe_join(repair_dir, "after.yaml"), repaired_yaml)
    business_check = validate_yaml_business_flow_preserved(old_yaml, repaired_yaml)
    before_version = None
    if repaired_yaml.strip() != old_yaml.strip():
        before_version = save_file_version(module, file, content=old_yaml, reason="before_static_repair")
        write_text_file(yaml_path, repaired_yaml)
    update_task_meta(module, file, {
        "last_repair_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_repair_status": "static_updated" if changes else "static_noop",
        "last_repair_changes": changes[:20]
    })
    next_job = create_pending_job(module, file, auto_optimize=False, run_mode="baseline") if create_next else None
    result = {
        "ok": True,
        "mode": "static",
        "analysis": "未找到失败执行记录，已执行静态规则体检：修复 YAML 语法、flowItem、前置启动、后置关闭、长等待和基线注释。",
        "changes": changes,
        "updated_file": f"{module}/{file}",
        "yamlCheck": yaml_check,
        "next_job": next_job
    }
    attach_repair_result_metadata(
        result,
        old_yaml,
        repaired_yaml,
        repair_dir=repair_dir,
        before_version=before_version,
        yaml_check=yaml_check,
        safety_warnings=[],
        business_check=business_check
    )
    write_json_file(safe_join(repair_dir, "repair.json"), {
        "module": module,
        "file": file,
        "mode": "static",
        "analysis": result["analysis"],
        "changes": changes,
        "yamlCheck": yaml_check,
        "safetyCheck": result["safetyCheck"],
        "businessFlowCheck": business_check,
        "changed_line_count": result["changed_line_count"],
        "diff_summary": result["diff_summary"],
        "before_version": before_version,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    })
    return result


def static_repair_yaml_task(module, file, task_name, create_next=False):
    yaml_path = safe_join(TASK_DIR, module, file)
    with open(yaml_path, encoding="utf-8") as f:
        old_yaml = f.read()
    task_info = find_yaml_task_block(old_yaml, task_name)
    app_package = resolve_app_package(module, file, old_yaml)
    platform = detect_yaml_platform(old_yaml)
    repaired_block, changes = normalize_task_block_runtime_guards(task_info["block"], app_package=app_package, evidence_text="", platform=platform)
    new_yaml = normalize_full_yaml_structure(replace_yaml_task_block(old_yaml, task_info, repaired_block))
    yaml_check = validate_midscene_yaml(new_yaml)
    if not yaml_check["ok"]:
        raise ValueError("静态修复后的 YAML 基础检查未通过：" + "；".join(yaml_check["warnings"]))
    repair_dir = safe_join(LEARNING_DIR, "repairs", f"{generate_job_id()}_static_{clean_id(task_info['name'], 'task')}")
    os.makedirs(repair_dir, exist_ok=True)
    write_text_file(safe_join(repair_dir, "before.yaml"), old_yaml)
    write_text_file(safe_join(repair_dir, "before-task.yaml"), task_info["block"] + "\n")
    write_text_file(safe_join(repair_dir, "after-task.yaml"), repaired_block.strip("\n") + "\n")
    write_text_file(safe_join(repair_dir, "after.yaml"), new_yaml)
    business_check = business_alignment_report(task_info["block"], repaired_block)
    before_version = None
    if new_yaml.strip() != old_yaml.strip():
        before_version = save_file_version(module, file, content=old_yaml, reason="before_static_task_repair")
        write_text_file(yaml_path, new_yaml)
    update_task_meta(module, file, {
        "last_repair_task_name": task_info["name"],
        "last_repair_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "last_repair_status": "static_updated" if changes else "static_noop",
        "last_repair_changes": changes[:20]
    })
    next_job = create_pending_job(module, file, auto_optimize=False, run_mode="baseline", target_task_name=task_info["name"]) if create_next else None
    result = {
        "ok": True,
        "mode": "static",
        "taskName": task_info["name"],
        "analysis": "未找到该用例失败执行记录，已执行单条静态规则体检：修复 YAML 语法、flowItem、前置启动、后置关闭、长等待和基线注释。",
        "changes": changes,
        "updated_file": f"{module}/{file}",
        "yamlCheck": yaml_check,
        "next_job": next_job
    }
    attach_repair_result_metadata(
        result,
        old_yaml,
        new_yaml,
        repair_dir=repair_dir,
        before_version=before_version,
        yaml_check=yaml_check,
        safety_warnings=[],
        business_check=business_check
    )
    write_json_file(safe_join(repair_dir, "repair.json"), {
        "module": module,
        "file": file,
        "taskName": task_info["name"],
        "mode": "static",
        "analysis": result["analysis"],
        "changes": changes,
        "yamlCheck": yaml_check,
        "safetyCheck": result["safetyCheck"],
        "businessFlowCheck": business_check,
        "changed_line_count": result["changed_line_count"],
        "diff_summary": result["diff_summary"],
        "before_version": before_version,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    })
    return result


def extract_docx_text(path):
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml")
    root = ET.fromstring(xml)
    texts = []
    for node in root.iter():
        if node.tag.endswith("}t") and node.text:
            texts.append(node.text)
    return "\n".join(texts)


def extract_pdf_text(path):
    if shutil.which("pdftotext"):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as tmp:
            out = tmp.name
        try:
            subprocess.run(["pdftotext", path, out], check=True)
            with open(out, encoding="utf-8", errors="ignore") as f:
                return f.read()
        finally:
            try:
                os.remove(out)
            except OSError:
                pass

    try:
        from pypdf import PdfReader
        reader = PdfReader(path)
        return "\n".join((page.extract_text() or "") for page in reader.pages)
    except Exception:
        raise ValueError("PDF 解析需要安装 poppler-utils 或 pypdf")


def extract_doc_text(path):
    if shutil.which("antiword"):
        return subprocess.check_output(["antiword", path], text=True, errors="ignore")
    if shutil.which("catdoc"):
        return subprocess.check_output(["catdoc", path], text=True, errors="ignore")
    raise ValueError("旧版 .doc 解析需要安装 antiword 或 catdoc，建议上传 .docx")


def extract_mm_text(path):
    tree = ET.parse(path)
    root = tree.getroot()
    lines = []

    def walk(node, depth=0):
        text = node.attrib.get("TEXT") or node.attrib.get("text")
        if text:
            lines.append(f"{'  ' * depth}- {text}")
        for child in node:
            if child.tag.lower().endswith("node"):
                walk(child, depth + 1)

    for node in root.iter():
        if node.tag.lower().endswith("node"):
            walk(node, 0)
            break
    return "\n".join(lines)


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


def save_asset_files(case_set_id, title, module, files):
    if not isinstance(files, list) or not files:
        raise ValueError("files 不能为空")

    asset_root = safe_join(ASSET_DIR, case_set_id)
    os.makedirs(asset_root, exist_ok=True)
    saved = []

    for item in files:
        name = clean_asset_filename(item.get("name", "asset.txt"))
        if not supported_asset_file(name):
            raise ValueError(f"不支持的资产格式：{name}")

        content_base64 = item.get("contentBase64")
        content = item.get("content")
        if content_base64:
            data = base64.b64decode(content_base64)
        else:
            data = (content or "").encode("utf-8")

        path_to_save = safe_join(asset_root, name)
        write_bytes_file(path_to_save, data)

        extract_error = ""
        extracted_size = 0
        if not is_image_file(name):
            try:
                extracted = extract_asset_text(path_to_save, name)
                extracted_size = len(extracted)
                if extracted:
                    extracted_path = safe_join(asset_root, f"{name}.extracted.txt")
                    write_text_file(extracted_path, extracted)
            except Exception as e:
                extract_error = str(e)

        saved.append({
            "name": name,
            "mime": guess_mime(name),
            "size": len(data),
            "type": "image" if is_image_file(name) else "text",
            "extracted_size": extracted_size,
            "extract_error": extract_error
        })

    meta = {
        "case_set_id": case_set_id,
        "title": title,
        "module": module,
        "files": saved,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    write_json_file(asset_meta_path(case_set_id), meta)
    return meta


def update_asset_request_context(case_set_id, context=None):
    context = context or {}
    try:
        meta = read_json_file(asset_meta_path(case_set_id), default=None) or {
            "case_set_id": case_set_id,
            "files": []
        }
    except Exception:
        meta = {"case_set_id": case_set_id, "files": []}
    figma_url = (context.get("figma_url") or context.get("figmaUrl") or "").strip()
    if figma_url:
        meta["figma_url"] = figma_url
    for key in ("figma_mode", "figmaMode", "figma_limit", "figmaLimit", "figma_reference_limit", "figmaReferenceLimit"):
        if context.get(key) not in (None, ""):
            meta[key] = context.get(key)
    for key in ("app_package", "appPackage", "knowledge_tier", "knowledgeTier"):
        if context.get(key) not in (None, ""):
            meta[key] = context.get(key)
    page_ids = context.get("knowledge_page_ids") or context.get("knowledgePageIds")
    if page_ids:
        meta["knowledge_page_ids"] = page_ids
    meta["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_json_file(asset_meta_path(case_set_id), meta)
    return meta


def append_asset_files(case_set_id, title, module, files):
    if not isinstance(files, list) or not files:
        raise ValueError("files 不能为空")
    existing = read_json_file(asset_meta_path(case_set_id), default=None) or {
        "case_set_id": case_set_id,
        "title": title,
        "module": module,
        "files": [],
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
    }
    asset_root = safe_join(ASSET_DIR, case_set_id)
    os.makedirs(asset_root, exist_ok=True)
    by_name = {item.get("name"): item for item in existing.get("files", []) if item.get("name")}

    for item in files:
        name = clean_asset_filename(item.get("name", "asset.txt"))
        if not supported_asset_file(name):
            raise ValueError(f"不支持的资产格式：{name}")
        content_base64 = item.get("contentBase64")
        content = item.get("content")
        data = base64.b64decode(content_base64) if content_base64 else (content or "").encode("utf-8")
        path_to_save = safe_join(asset_root, name)
        write_bytes_file(path_to_save, data)
        extract_error = ""
        extracted_size = 0
        if not is_image_file(name):
            try:
                extracted = extract_asset_text(path_to_save, name)
                extracted_size = len(extracted)
                if extracted:
                    write_text_file(safe_join(asset_root, f"{name}.extracted.txt"), extracted)
            except Exception as e:
                extract_error = str(e)
        by_name[name] = {
            "name": name,
            "mime": guess_mime(name),
            "size": len(data),
            "type": "image" if is_image_file(name) else "text",
            "extracted_size": extracted_size,
            "extract_error": extract_error,
            "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }

    existing["title"] = title or existing.get("title")
    existing["module"] = module or existing.get("module")
    existing["files"] = list(by_name.values())
    existing["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    write_json_file(asset_meta_path(case_set_id), existing)
    return existing


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


def normalize_design_asset_id(value):
    text = clean_id(value or "")
    return text[:80] or unique_millis_id("ui")


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


def save_figma_design_assets_for_case(case_set_id, drafts, title="", module=""):
    files = []
    min_save_score = max(0, env_int("FIGMA_AUTO_SAVE_MIN_RELEVANCE", 5))
    for draft in drafts or []:
        screenshot = draft.get("screenshot") or {}
        content = screenshot.get("contentBase64")
        if not content:
            continue
        figma = draft.get("figma") or {}
        relevance_score = safe_int(figma.get("relevance_score"), 0)
        if not figma.get("pinned") and relevance_score < min_save_score:
            continue
        node_id = figma.get("node_id") or draft.get("page_id") or draft.get("page_name") or ""
        files.append({
            "asset_id": normalize_design_asset_id(f"figma-{node_id}"),
            "name": screenshot.get("name") or clean_asset_filename(f"figma-{clean_id(draft.get('page_name') or node_id)}.png"),
            "contentBase64": content,
            "page_name": draft.get("page_name") or figma.get("page_name") or "",
            "route": draft.get("route") or "",
            "description": draft.get("description") or "",
            "figma": {
                **figma,
                "relevance_score": relevance_score,
                "relevance_reason": figma.get("relevance_reason", ""),
                "auto_save_min_relevance": min_save_score
            }
        })
    if not files:
        return []
    saved, _meta = save_case_ui_design_files(case_set_id, files, source="figma", title=title, module=module)
    return saved


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


def load_asset_contents(case_set_id, meta):
    asset_root = safe_join(ASSET_DIR, case_set_id)
    text_assets = []
    image_assets = []

    for item in meta.get("files", []):
        name = item.get("name", "")
        path_to_read = safe_join(asset_root, name)
        if is_text_asset(name):
            extracted_path = safe_join(asset_root, f"{name}.extracted.txt")
            read_path = extracted_path if os.path.exists(extracted_path) else path_to_read
            with open(read_path, encoding="utf-8", errors="ignore") as f:
                text_assets.append(f"文件：{name}\n{f.read()[:30000]}")
        elif is_image_file(name):
            with open(path_to_read, "rb") as f:
                image_assets.append({
                    "name": name,
                    "mime": guess_mime(name),
                    "base64": base64.b64encode(f.read()).decode("ascii")
                })

    return text_assets, image_assets


def is_image_file(filename):
    return filename.lower().endswith((".png", ".jpg", ".jpeg"))


def is_text_asset(filename):
    return filename.lower().endswith((".txt", ".md", ".json", ".pdf", ".doc", ".docx", ".mm"))


def normalize_case_json_from_model(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.I).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    payload = json.loads(text)
    return normalize_cases_payload(payload)


def normalize_model_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text, flags=re.I).strip()
        text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    return json.loads(text)


def ai_skill_path(*parts):
    return safe_join(AI_SKILLS_DIR, *parts)


def load_ai_skill_prompt(skill_name, version="v1"):
    name = clean_id(skill_name, "skill")
    ver = clean_id(version, "v1")
    path = ai_skill_path("prompts", f"{name}.{ver}.md")
    return read_text(path, "")


def load_ai_skill_schema(skill_name):
    name = clean_id(skill_name, "skill")
    path = ai_skill_path("schemas", f"{name}.schema.json")
    schema = read_json_file(path, default=None)
    if not schema:
        raise ValueError(f"AI skill schema 不存在：{name}")
    return schema


def validate_json_schema_minimal(value, schema, path="$"):
    expected = schema.get("type")
    if expected == "object":
        if not isinstance(value, dict):
            raise ValueError(f"{path} 必须是 object")
        for key in schema.get("required") or []:
            if key not in value:
                raise ValueError(f"{path}.{key} 为必填字段")
        properties = schema.get("properties") or {}
        for key, child_schema in properties.items():
            if key in value:
                validate_json_schema_minimal(value[key], child_schema, f"{path}.{key}")
    elif expected == "array":
        if not isinstance(value, list):
            raise ValueError(f"{path} 必须是 array")
    elif expected == "string":
        if not isinstance(value, str):
            raise ValueError(f"{path} 必须是 string")
    elif expected == "boolean":
        if not isinstance(value, bool):
            raise ValueError(f"{path} 必须是 boolean")
    elif expected in ("number", "integer"):
        if not isinstance(value, (int, float)):
            raise ValueError(f"{path} 必须是 number")
    return True


def validate_ai_skill_output(skill_name, value):
    schema = load_ai_skill_schema(skill_name)
    validate_json_schema_minimal(value, schema)
    return value


def render_ai_skill_prompt(skill_name, payload=None, version="v1", fallback_prompt=""):
    template = load_ai_skill_prompt(skill_name, version)
    if not template:
        return fallback_prompt
    payload_text = json.dumps(payload or {}, ensure_ascii=False, indent=2)
    return template.replace("{{payload}}", payload_text)


def run_ai_skill(skill_name, payload=None, image_assets=None, version="v1", temperature=0.1, timeout=180, fallback_prompt=""):
    prompt = render_ai_skill_prompt(skill_name, payload, version=version, fallback_prompt=fallback_prompt)
    if not prompt:
        raise ValueError(f"AI skill prompt 不存在：{skill_name}.{version}")
    raw = dashscope_chat_content(prompt, image_assets=image_assets, temperature=temperature, timeout=timeout, json_response=True)
    result = normalize_model_json(raw)
    return validate_ai_skill_output(skill_name, result)


def compact_text_assets(text_assets, max_chars=24000):
    text = "\n\n".join(str(item or "").strip() for item in (text_assets or []) if str(item or "").strip())
    return text[:max_chars]


def normalize_source_quality(value):
    source = value if isinstance(value, dict) else {}
    normalized = {}
    for key in ("requirement", "ui", "knowledge"):
        text = str(source.get(key) or "").strip().lower()
        normalized[key] = text if text in ("sufficient", "partial", "missing") else "missing"
    return normalized


def normalize_readiness_level(score, blockers=None, missing_inputs=None, questions=None, explicit=""):
    explicit = str(explicit or "").strip().lower()
    if explicit in ("ready", "review", "blocked"):
        return explicit
    if blockers:
        return "blocked"
    if score < 50:
        return "blocked"
    if score < 75 or missing_inputs or questions:
        return "review"
    return "ready"


def normalize_requirement_analysis_result(result):
    result = result if isinstance(result, dict) else {}
    for key in (
        "business_goals", "roles", "entry_points", "state_assumptions",
        "data_assumptions", "visible_outcomes", "risks", "requirement_points",
        "questions", "missing_inputs", "blockers", "assumptions"
    ):
        result[key] = normalize_text_list(result.get(key))
    confidence = str(result.get("confidence") or "medium").strip().lower()
    if confidence not in ("high", "medium", "low"):
        confidence = "medium"
    result["confidence"] = confidence
    source_quality = normalize_source_quality(result.get("source_quality"))
    if source_quality.get("requirement") == "missing" and (result["requirement_points"] or result["business_goals"]):
        source_quality["requirement"] = "partial"
    if source_quality.get("ui") == "missing" and (result["entry_points"] or result["visible_outcomes"]):
        source_quality["ui"] = "partial"
    result["source_quality"] = source_quality
    score = safe_int(result.get("readiness_score") or result.get("readinessScore"), 0)
    if score <= 0:
        score = {"high": 86, "medium": 70, "low": 48}.get(confidence, 70)
        score -= min(25, len(result["questions"]) * 5)
        score -= min(25, len(result["missing_inputs"]) * 5)
        score -= min(30, len(result["blockers"]) * 12)
        if source_quality.get("requirement") == "missing":
            score -= 12
        if source_quality.get("ui") == "missing":
            score -= 8
        if not result["requirement_points"]:
            score -= 20
    score = max(0, min(100, score))
    result["readiness_score"] = score
    result["readiness_level"] = normalize_readiness_level(
        score,
        blockers=result["blockers"],
        missing_inputs=result["missing_inputs"],
        questions=result["questions"],
        explicit=result.get("readiness_level") or result.get("readinessLevel")
    )
    return result


def scenario_requirement_point(scenario):
    if not isinstance(scenario, dict):
        return ""
    return first_non_empty(scenario.get("requirement_point"), scenario.get("requirementPoint"), scenario.get("coverage"), scenario.get("point"))


def case_matches_requirement(case, requirement_point):
    text = " ".join(normalize_text_list([
        (case or {}).get("coverage"),
        (case or {}).get("requirement_point"),
        (case or {}).get("requirementPoint"),
        (case or {}).get("title"),
        (case or {}).get("scenario"),
    ]))
    point = str(requirement_point or "").strip()
    if not point:
        return False
    point_core = re.sub(r"^REQ[-_ ]?\d+\s*[:：.-]?\s*", "", point, flags=re.I).strip()
    return point in text or (point_core and point_core in text)


def build_skill_coverage_matrix(analysis, scenarios, cases, manual_cases):
    analysis = analysis if isinstance(analysis, dict) else {}
    existing = analysis.get("coverage_matrix") or analysis.get("coverageMatrix") or []
    if isinstance(existing, list) and existing:
        return existing
    points = normalize_text_list(analysis.get("requirement_points"))
    rows = []
    for point in points:
        related_scenarios = [
            item for item in (scenarios or [])
            if isinstance(item, dict) and (
                scenario_requirement_point(item) == point
                or case_matches_requirement({"coverage": scenario_requirement_point(item), "title": item.get("scenario")}, point)
            )
        ]
        auto = [
            first_non_empty(case.get("case_id"), case.get("caseId"), case.get("title"))
            for case in (cases or [])
            if isinstance(case, dict) and case_matches_requirement(case, point)
        ]
        manual = [
            first_non_empty(case.get("case_id"), case.get("caseId"), case.get("title"), case.get("reason"))
            for case in (manual_cases or [])
            if isinstance(case, dict) and case_matches_requirement(case, point)
        ]
        normal = [s.get("scenario") for s in related_scenarios if "正常" in str(s.get("type") or "")]
        negative = [s.get("scenario") for s in related_scenarios if "异常" in str(s.get("type") or "")]
        boundary = [s.get("scenario") for s in related_scenarios if "边界" in str(s.get("type") or "") or "状态" in str(s.get("type") or "")]
        rows.append({
            "feature": first_non_empty((related_scenarios[0] or {}).get("feature") if related_scenarios else "", "需求覆盖"),
            "requirement_point": point,
            "normal_scenarios": normalize_text_list(normal),
            "negative_scenarios": normalize_text_list(negative),
            "boundary_scenarios": normalize_text_list(boundary),
            "auto_cases": normalize_text_list(auto),
            "manual_cases": normalize_text_list(manual),
            "uncovered_reason": "" if auto or manual else "已识别需求点，但尚未生成可追溯用例，需人工补充或重新生成"
        })
    return rows


def call_skill_requirement_analyzer(title, module, text_assets):
    payload = {
        "title": title,
        "module": module,
        "text_assets": compact_text_assets(text_assets)
    }
    result = run_ai_skill("requirement_analyzer", payload, timeout=240)
    return normalize_requirement_analysis_result(result)


def generation_volume_targets(analysis):
    points = normalize_text_list((analysis or {}).get("requirement_points"))
    risks = normalize_text_list((analysis or {}).get("risks"))
    visible = normalize_text_list((analysis or {}).get("visible_outcomes"))
    blockers = normalize_text_list((analysis or {}).get("blockers"))
    missing = normalize_text_list((analysis or {}).get("missing_inputs"))
    point_count = len(points)
    complexity = point_count + min(len(risks), 4) + min(len(visible), 3)
    if point_count <= 1 and complexity <= 3:
        min_cases, target_cases, max_cases = 6, 8, 12
        min_scenarios, target_scenarios = 4, 6
    elif point_count <= 2:
        min_cases, target_cases, max_cases = 8, 12, 16
        min_scenarios, target_scenarios = 6, 10
    elif point_count <= 5:
        min_cases, target_cases, max_cases = 12, 18, 28
        min_scenarios, target_scenarios = 10, 18
    else:
        min_cases, target_cases, max_cases = 18, 30, 45
        min_scenarios, target_scenarios = 16, 30
    if blockers:
        min_cases = max(4, min_cases - 4)
        target_cases = max(min_cases, target_cases - 6)
    return {
        "requirement_point_count": point_count,
        "min_automation_cases": min_cases,
        "target_automation_cases": target_cases,
        "max_automation_cases": max_cases,
        "min_scenarios": min_scenarios,
        "target_scenarios": target_scenarios,
        "manual_cases_not_counted": True,
        "guidance": (
            "按需求点、正常/异常/边界/状态/空态覆盖扩容；不要为了数量重复同一路径。"
            "无法稳定自动化的场景进入 manual_cases，但不计入自动化 cases 数。"
        ),
        "missing_inputs": missing,
        "blockers": blockers
    }


def call_skill_scenario_designer(title, module, analysis):
    targets = generation_volume_targets(analysis)
    payload = {
        "title": title,
        "module": module,
        "analysis": analysis,
        "generation_targets": targets
    }
    result = run_ai_skill("scenario_designer", payload, timeout=240)
    scenarios = result.get("scenarios") or []
    if not isinstance(scenarios, list) or not scenarios:
        raise ValueError("scenario_designer 未产出场景")
    return scenarios


def call_skill_automation_filter(title, module, analysis, scenarios):
    targets = generation_volume_targets(analysis)
    payload = {
        "title": title,
        "module": module,
        "analysis": analysis,
        "scenarios": scenarios,
        "generation_targets": targets,
        "automation_rules": {
            "allowed_actions": ["点击", "输入", "等待", "断言", "返回", "滚动", "处理弹窗", "回到首页"],
            "manual_by_default": ["真实支付", "删除", "切账号", "清数据", "后台造数", "真实外设", "破坏网络"],
            "assertion_required": True
        }
    }
    result = run_ai_skill("automation_filter", payload, timeout=300)
    cases = result.get("cases") or []
    if not isinstance(cases, list) or not cases:
        raise ValueError("automation_filter 未产出自动化用例")
    review = result.get("review") or {}
    review["generation_targets"] = targets
    review["actual_case_count"] = len(cases)
    return {
        "cases": cases,
        "manual_cases": result.get("manual_cases") or [],
        "review": review
    }


def build_cases_payload_from_skills(title, module, text_assets):
    analysis = call_skill_requirement_analyzer(title, module, text_assets)
    scenarios = call_skill_scenario_designer(title, module, analysis)
    filtered = call_skill_automation_filter(title, module, analysis, scenarios)
    cases = filtered.get("cases") or []
    manual_cases = filtered.get("manual_cases") or []
    analysis["coverage_matrix"] = build_skill_coverage_matrix(analysis, scenarios, cases, manual_cases)
    payload = {
        "title": title,
        "module": module,
        "analysis": analysis,
        "scenarios": scenarios,
        "cases": cases,
        "manual_cases": manual_cases,
        "review": filtered.get("review") or {}
    }
    review = payload.setdefault("review", {})
    review["skill_pipeline"] = "requirement_analyzer.v1 -> scenario_designer.v1 -> automation_filter.v1"
    review["requirement_readiness"] = {
        "score": analysis.get("readiness_score"),
        "level": analysis.get("readiness_level"),
        "confidence": analysis.get("confidence"),
        "missing_inputs": analysis.get("missing_inputs") or [],
        "blockers": analysis.get("blockers") or [],
        "questions": analysis.get("questions") or [],
    }
    normalized = normalize_cases_payload(payload)
    validate_ai_skill_output("cases_payload", normalized)
    return normalized


def analyze_knowledge_screenshot(data):
    api_key = dashscope_api_key()

    screenshot = data.get("screenshot") or {}
    if not screenshot.get("contentBase64"):
        raise ValueError("请先上传页面截图")

    name = clean_asset_filename(screenshot.get("name") or "page.png")
    if not is_image_file(name):
        raise ValueError("页面截图只支持 png / jpg / jpeg")

    app_package = data.get("app_package") or data.get("appPackage") or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
    hint = data.get("hint") or ""
    existing_page_name = data.get("page_name") or data.get("pageName") or ""
    prompt = f"""
你是移动 App UI 自动化测试知识库维护助手。
请根据截图识别这个页面，生成可维护的页面知识草稿。

要求：
1. 只输出合法 JSON，不要 Markdown，不要解释。
2. 不要编造截图里看不到的按钮、入口、Tab 或文案。
3. key_elements 用真实可见文案或稳定入口描述，适合给 Midscene 的 aiTap/aiAction 使用。
4. common_assertions 必须是页面上可以视觉验证的内容。
5. route 如果截图无法判断，可给出空字符串或“待补充”。
6. page_name 尽量用页面标题、Tab 名、核心业务名。

APP 包名：{app_package}
人工提示：{hint}
已有页面名称：{existing_page_name}

输出格式：
{{
  "page_name": "我的页",
  "route": "点击底部 Tab「我的」",
  "description": "用户个人中心页面，包含我的收藏、打印记录等入口。",
  "key_elements": ["底部 Tab「我的」", "入口「我的收藏」", "入口「打印记录」"],
  "common_assertions": ["页面展示「我的收藏」入口", "页面展示「打印记录」入口"],
  "tags": ["我的", "个人中心"]
}}
"""

    base_url = dashscope_base_url()
    body = json.dumps({
        "model": dashscope_vl_model(),
        "messages": [
            {"role": "system", "content": "你只输出合法 JSON。"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{guess_mime(name)};base64,{screenshot['contentBase64']}"
                        }
                    }
                ]
            }
        ],
        "temperature": 0.1
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        resp_data = json.loads(resp.read().decode("utf-8"))

    draft = normalize_model_json(resp_data["choices"][0]["message"]["content"])
    return {
        "page_name": draft.get("page_name") or draft.get("pageName") or existing_page_name or "未命名页面",
        "route": draft.get("route") or "",
        "description": draft.get("description") or "",
        "key_elements": normalize_lines(draft.get("key_elements") or draft.get("keyElements")),
        "common_assertions": normalize_lines(draft.get("common_assertions") or draft.get("commonAssertions")),
        "tags": normalize_lines(draft.get("tags"))
    }


def build_case_generation_prompt(title, module, text_assets):
    text_block = "\n\n".join(text_assets).strip()
    return f"""
你是资深移动 App UI 自动化测试工程师。
请根据需求文档、原型图或设计稿截图，生成标准测试用例 JSON。需求文档是业务范围和测试意图的主来源，页面知识库和设计稿用于校准真实入口、页面结构和 UI 可见断言。

要求：
1. 只输出合法 JSON，不要 Markdown，不要解释。
2. JSON 根节点必须包含 title、module、analysis、scenarios、cases。
3. JSON 根节点还可以包含 manual_cases、review，用于放置当前环境不可稳定自动执行的场景和自评审结果。
4. cases 是数组，每条用例包含 case_id、title、priority、smoke、preconditions、steps、assertions、tags；建议额外包含 goal、start_page、business_path、expected_result、repair_hints、risk、coverage、data_requirements、automation_reason，便于后续 AI 修复和人工评审理解业务链路。
5. cases 里放“当前默认测试环境可直接执行或可弱数据依赖执行”的 UI 自动化用例。只要能通过页面标题、入口、列表/空态、按钮状态、弹窗等 UI 信号验证，就应优先进入 cases。
6. 不要因为数据结果可能为空就放弃自动化：列表类、记录类、收藏类、资源类页面必须兼容“有数据或空态”两种可见结果。只有依赖切换登录态、清空账号数据、特殊后台造数、特定付费账号、系统权限预置、真实支付/删除等高风险场景才放入 manual_cases。
7. 如果需求没有明确说明测试账号状态，默认认为当前账号已登录。
8. steps 必须是用户可执行的 UI 操作，尽量使用页面真实文案、按钮名、Tab 名、入口名。
9. assertions 必须表达“业务意图 + UI 可见信号”，避免抽象断言，也避免过严断言。除非需求明确要求完全一致，否则不要断言动态列表第几条、动态推荐内容、数量、时间、百分比、随机资源名。
10. 覆盖主流程和当前状态下能自然到达的分支。不要只生成 1 条主流程；每个需求功能点通常至少生成 2-4 条自动化用例：入口可达、页面展示、关键交互、状态/空态/异常提示中可稳定执行的部分。
11. 不要输出 YAML。
12. 如果文本资产里包含 [APP页面知识]，必须优先参考里面的真实页面名称、入口文案、关键元素和常用断言；不要臆造不存在的按钮。
13. 如果有 UI 设计稿/原型图截图，必须从截图中提取可见的预期结果作为 assertions，例如页面标题、Tab 选中态、入口卡片、按钮文案、列表区域、空态文案、弹窗标题、关键状态文案。
14. assertions 只写视觉可验证内容，不要写接口成功、数据库变化、算法正确、业务一定成功这类仅凭 UI 看不到的结论。
15. 页面截图仅用于理解页面结构和预期结果，Midscene YAML 不写坐标，仍使用自然语言动作。
16. goal/start_page/business_path/expected_result/repair_hints 是给平台做基线维护和 AI 修复用的描述元数据，不要写成执行步骤；内容要具体到页面、入口和可见预期。
17. 不要在 steps 里写“等待 5 秒/等待 10 秒”这类固定长等待。遇到网络加载、资源加载、列表刷新等慢场景，要在 assertions/expected_result 里描述等待目标，例如“列表加载完成并展示内容或空态”“详情页标题可见”，平台转换 YAML 时会优先生成 aiWaitFor 条件等待。
18. 生成前先做测试分析：必须逐条提取需求文档中的功能点、业务规则、用户角色、入口路径、状态前置、关键数据、核心风险、可见结果、不可自动化依赖，再决定生成哪些自动化用例。
19. 不要把需求里的每个按钮都机械拆成用例。每条用例必须对应一个业务目标或风险点，点击只是实现业务链路的手段。
20. priority 必须使用 P0/P1/P2/P3：P0 只放阻断发布或基线必跑的核心链路，例如核心入口不可达、主流程完全不可用、打印/生成主链路阻断；P1 放核心功能主流程、核心页面展示、关键业务风险；P2 放常见分支、空态、异常提示、权限/状态变化中可稳定执行的部分；P3 放低频边界或体验类检查。不要滥造 P0/P1。
21. 对新增/编辑/删除/支付/提交类操作，只有在当前环境具备稳定测试数据和可回滚能力时才放入 cases；否则放入 manual_cases，并写清楚数据准备和人工检查点。
22. 如果需求明确提出某个业务功能，但设计稿/页面知识缺少入口，不要忽略该需求；可以根据需求生成用例，并在 repair_hints 标注“不确定入口，需人工确认真实页面入口”。只有完全无法形成 UI 路径时才放入 manual_cases。
23. 必须先设计测试场景，再从场景筛选自动化用例；不要直接从需求句子生成步骤。
24. scenarios 必须按业务域/功能点分组，每个功能点尽量包含正常流程、异常流程、边界/临界场景，并标注 design_method：等价类、边界值、状态迁移、权限矩阵、错误推测、业务规则校验中的一种或多种。
25. 等价类要求至少考虑有效等价类和无效等价类；边界值要求在适用时考虑下界、上界、下界外一点、上界外一点，但只有 UI 可稳定执行的才进入 cases。
26. 自评审必须输出 review，检查覆盖完整性、自动化可执行性、断言质量、重复用例、数据依赖和不可自动化场景归类。
27. 每条 cases 必须有稳定标识 case_id，格式 TC-001、TC-002；smoke 为布尔值，只有核心冒烟主链路标记 true，同时 tags 中加入“冒烟”。不要把所有 P1 都标成冒烟。
28. analysis 必须包含 requirement_points 数组，逐条列出从需求文档中提取的测试点；scenarios 和 cases 必须能追溯到这些 requirement_points。
29. 生成数量要求：如果需求包含多个功能点，自动化 cases 数量应随功能点增长。除非需求极少或存在大量不可自动化依赖，否则不要少于 8 条；1-2 个需求点通常生成 8-16 条；3-5 个需求点通常生成 12-28 条；超过 5 个需求点通常生成 18-45 条。manual_cases 不计入自动化 cases 数量。只生成 8 条通常只适合极小需求；中等需求必须覆盖更多状态、空态、异常和边界。
30. 采用“需求提取测试点 → 业务场景设计 → 测试用例设计 → 自动化可执行性筛选 → 自评审”的流程，不可跳过场景直接生成用例。
31. 以资深软件测试业务专家视角设计用例：先理解业务规则和用户目标，再设计覆盖策略；不要只按页面控件或按钮清单生成用例。
32. 每个功能点都要尝试设计三类场景：正常流程、异常流程、边界/临界流程；并在 scenarios.type 中标注。若某类场景不适合自动化，也必须在 scenarios 或 manual_cases 中说明原因。
33. scenarios.design_method 必须标注所用方法，优先从“等价类、边界值、状态迁移、权限矩阵、错误推测、业务规则校验”中选择；有输入范围、数量、长度、次数、时间、进度、状态阈值时必须考虑边界值。
34. 用例标题必须业务化且可区分，不能出现“测试1/功能验证/页面验证”这类泛化命名。
35. 每条 case 的 steps 和 assertions 必须一一服务于同一个业务目标：步骤可执行，预期可检查；不要把多个无关目标塞进一条用例。
36. 冒烟用例只标记核心主链路，tags 中必须包含“冒烟”；非核心入口、边界、异常、体验检查不要随意标冒烟。
37. 不得在输出中写入 token、API key、真实账号密码、手机号、身份证、邮箱等敏感信息；如需求需要账号或测试数据，使用占位符或 data_requirements 描述。
38. 自动化步骤只允许使用平台后续可转换的自然语言动作意图：点击、输入、等待、断言、返回、滚动、处理弹窗、回到首页。不要输出坐标、XPath、CSS selector、控件层级，也不要输出当前平台未确认支持的动作名。
39. 对“串行执行”的理解：每条自动化 case 都要从稳定起点开始，preconditions/start_page/business_path/repair_hints 要描述如何回到首页、处理弹窗、确认登录态；但 cases JSON 中不要生成单独的 00-BOOTSTRAP 用例，平台会在 YAML 转换阶段统一注入启动和收尾守卫。
40. 对需要长时间加载的业务（上传、生成、打印、搜索、模型加载、进度条），不要用固定长 sleep 作为步骤；在 expected_result/assertions 中写清楚等待目标，例如“进度达到 100% 且出现确认按钮”“列表区域展示结果或空态”“提交按钮恢复可点击”。
41. 场景必须考虑全，但要分层：scenarios 负责完整覆盖正常、异常、边界、权限/状态、数据状态、错误提示、兼容性风险；cases 只承载当前环境能稳定自动执行的部分；manual_cases 承载需要造数、切账号、破坏网络、支付、删除、真实外设或高风险操作的部分。不要因为不能自动化就漏掉场景。
42. 生成前必须先产出覆盖矩阵：analysis.coverage_matrix 数组中逐项列出 feature、requirement_point、normal_scenarios、negative_scenarios、boundary_scenarios、auto_cases、manual_cases、uncovered_reason。review.coverage_check 必须说明是否有未覆盖需求点；review.quantity_check 必须说明当前自动化用例数量是否匹配需求复杂度。
43. 不允许为了数量把同一业务链路拆成大量重复点击用例；覆盖“风险和业务规则”，不是覆盖“按钮个数”。每条 case 必须能追溯到 requirement_points 和 scenarios。
44. 如果需求和设计稿只给了局部页面，也要根据业务常识补全关键风险：入口不可达、页面展示、数据为空/有数据、权限/登录态、加载失败/错误提示、重复操作/返回中断；但不能编造需要后台或真实支付的数据结果，无法稳定执行的放入 manual_cases。

当前平台生成策略：
1. 这是“测试资产管理 + AI 用例生成 + YAML 转换 + Runner 执行 + 反馈学习”的自动化平台，生成结果要优先保证能在 Midscene Runner 中稳定执行。
2. 需求文档用于判断业务范围、测试目标和覆盖点；UI 设计稿/原型图用于匹配真实页面结构、入口文案、预期展示和视觉断言；页面知识库用于校准线上 APP 的真实页面名称、入口路径、常用断言。
3. 同时存在需求、设计稿、页面知识时：业务覆盖范围以需求文档为准；入口路径和页面名称优先参考页面知识库；视觉断言优先参考设计稿和页面知识。若三者冲突，不要忽略需求，应在 repair_hints 或 manual_cases 中说明冲突和人工确认点。
4. 生成 steps 时必须使用 Midscene 容易理解的自然语言，不写坐标，不写 XPath，不写控件层级，不写“点击左上角第三个图标”这类脆弱描述；优先写真实文案，例如“点击底部 Tab「我的」”“点击「打印记录」入口”。
5. 生成 assertions 时必须从需求意图推导 UI 可见检查点，并结合设计稿或页面知识校准真实文案。优先验证：页面标题、Tab 选中态、按钮/入口是否展示、弹窗标题和文案、空态文案、列表区域、结果页关键文案、状态标签。
6. 如果需求只描述“进入某功能”，但设计稿显示了明确页面标题或关键入口，assertions 必须补充这些可见断言，避免只写“页面跳转成功”。如果列表内容动态，断言应写成“展示列表区域或空态提示”，不要写“列表展示正常”。
7. 如果需求涉及新增、删除、支付、登录态切换、清缓存、特殊账号、空数据、后台造数、系统权限弹窗等不稳定前置，默认不要放进 cases，放入 manual_cases，并说明需要的测试准备。
8. cases 的数量要贴近需求，不要为了凑数量拆出大量重复用例；但必须充分覆盖需求中的功能点、业务规则和风险。优先生成主路径、关键入口、关键页面展示、当前账号状态下能自然执行的分支。
9. 每条用例应尽量短而稳定：一个用例只验证一个核心目标；步骤不要跨太多页面；断言不要过严，避免依赖具体列表第几条、动态时间、动态推荐内容。
10. 如果上传的是已生成的用例 JSON，优先按 JSON 转换和补强，不要重新发散需求。
11. 对慢加载页面，expected_result 要写成可等待的 UI 条件，不要让 YAML 依赖固定长 sleep；例如“页面标题「模型库」展示”“列表区域出现内容或空态”“提交按钮恢复可点击”。
12. 用例组合要覆盖“业务链路”而不是“控件清单”：优先覆盖入口可达性、关键路径完成、页面状态展示、空态/无数据、错误提示、权限/登录态边界中当前环境可稳定执行的部分。
13. 自动化用例必须可独立执行，不能依赖上一条用例留下的页面状态、数据状态或排序状态；如果需要特殊数据，写入 data_requirements，且不稳定时放入 manual_cases。
14. 每条 case 的 steps 应该从稳定起点开始描述业务路径；不要跨多个不相关业务目标；不要在一个用例里验证过多页面。
15. assertions 至少包含一个“业务成功信号”，例如目标页面标题、目标功能入口状态、目标结果区域、目标列表/空态、目标弹窗文案、目标按钮状态。断言要允许合理 UI 变化，例如“展示列表内容或空态提示”“页面包含功能标题或核心入口”，不要要求动态内容完全一致。
16. 对设计稿截图，优先提取可见元素作为断言；对需求文档中的抽象结果，要转成 UI 可见结果；无法从 UI 看见的结果不要写入 assertions，但可以写入 expected_result 或 manual_cases 的人工检查点。
17. 输出前自检：删除重复用例、删除不可执行用例、删除只点击不验证的用例、删除断言泛化的用例。
18. 场景到用例必须可追溯：每条 case 需要填写 scenario、coverage、risk，并能对应 scenarios 中的某个场景。
19. 异常/边界场景不是一定要自动化；如果需要构造数据、切账号、破坏网络、修改权限、触发支付或删除数据，放入 manual_cases，但 scenarios 中仍要体现这些风险。

输出格式：
{{
  "title": "{title}",
  "module": "{module}",
  "analysis": {{
    "business_goals": ["用户可以查看我的收藏内容"],
    "roles": ["已登录普通用户"],
    "entry_points": ["首页", "底部 Tab「我的」"],
    "state_assumptions": ["默认账号已登录"],
    "data_assumptions": ["收藏有无均可"],
    "risks": ["核心入口不可达", "空态展示异常"],
    "requirement_points": ["用户可以从我的页进入我的收藏", "收藏列表有数据或无数据时都应有可理解展示"],
    "coverage_matrix": [
      {{
        "feature": "我的收藏",
        "requirement_point": "用户可以从我的页进入我的收藏",
        "normal_scenarios": ["已登录用户进入我的收藏"],
        "negative_scenarios": ["未登录用户点击我的收藏"],
        "boundary_scenarios": ["收藏为空时展示空态"],
        "auto_cases": ["TC-001"],
        "manual_cases": ["未登录时点击我的收藏提示登录"],
        "uncovered_reason": ""
      }}
    ]
  }},
  "scenarios": [
    {{
      "feature": "我的收藏",
      "scenario": "已登录用户进入我的收藏",
      "type": "正常流程",
      "design_method": ["等价类"],
      "business_path": "首页 -> 底部 Tab「我的」 -> 我的收藏",
      "expected": "进入我的收藏页面，展示列表或空态",
      "automation_suitable": true,
      "reason": "路径短、结果 UI 可见、数据依赖弱"
    }},
    {{
      "feature": "我的收藏",
      "scenario": "收藏为空时展示空态",
      "type": "边界/临界流程",
      "design_method": ["等价类", "边界值"],
      "business_path": "首页 -> 底部 Tab「我的」 -> 我的收藏",
      "expected": "进入我的收藏页面，无收藏数据时展示空态提示或引导入口",
      "automation_suitable": true,
      "reason": "空态是列表类功能的关键边界，断言可兼容有数据/无数据或在数据可控时单独执行"
    }},
    {{
      "feature": "我的收藏",
      "scenario": "未登录用户点击我的收藏",
      "type": "异常流程",
      "design_method": ["状态迁移", "权限矩阵"],
      "business_path": "首页 -> 我的 -> 我的收藏",
      "expected": "出现登录提示或跳转登录页",
      "automation_suitable": false,
      "reason": "需要稳定切换未登录态"
    }}
  ],
  "cases": [
    {{
      "title": "进入我的收藏列表",
      "case_id": "TC-001",
      "priority": "P1",
      "smoke": true,
      "scenario": "已登录用户进入我的收藏",
      "goal": "验证已登录用户可以从我的页进入我的收藏列表",
      "start_page": "App 首页",
      "business_path": "首页 -> 底部 Tab「我的」 -> 我的收藏",
      "expected_result": "进入「我的收藏」页面，页面展示收藏列表或空态区域",
      "repair_hints": "如果找不到入口，先点击底部 Tab「我的」；断言优先使用页面标题、收藏列表、空态文案等可见内容。",
      "risk": "我的页核心个人资产入口不可达会影响用户查看收藏内容",
      "coverage": "核心入口可达性 + 页面展示",
      "data_requirements": "默认已登录账号；收藏有无均可，断言兼容列表和空态",
      "automation_reason": "路径短、结果 UI 可见、数据依赖弱，适合稳定回归",
      "preconditions": ["用户已登录"],
      "steps": ["点击底部 Tab「我的」", "点击「我的收藏」入口"],
      "assertions": ["页面展示「我的收藏」标题或收藏相关核心区域", "展示收藏列表内容或空态提示"],
      "tags": ["我的", "收藏", "冒烟"],
      "flag": ["冒烟"]
    }}
  ],
  "manual_cases": [
    {{
      "title": "未登录时点击我的收藏提示登录",
      "reason": "需要切换到未登录态，当前默认执行环境无法保证",
      "suggested_setup": "使用未登录设备或执行清除登录态脚本后单独运行"
    }}
  ],
  "review": {{
    "coverage_check": "已覆盖主流程和未登录异常场景，未登录场景归入 manual_cases",
    "automation_check": "自动化用例路径短且 UI 断言明确",
    "assertion_check": "断言包含页面标题、列表或空态区域",
    "dedupe_check": "无重复用例",
    "remaining_risks": ["未覆盖真实无收藏账号的数据构造"]
  }}
}}

文本资产：
{text_block}
"""


def call_dashscope_cases_legacy(title, module, text_assets, image_assets):
    api_key = dashscope_api_key()
    base_url = dashscope_base_url()
    prompt = build_case_generation_prompt(title, module, text_assets)
    body = json.dumps(build_dashscope_chat_body(
        prompt,
        image_assets=image_assets,
        temperature=0.2,
        json_response=True,
        image_limit=8
    ), ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        },
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=360) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    content = data["choices"][0]["message"]["content"]
    payload = normalize_case_json_from_model(content)
    payload["title"] = payload.get("title") or title
    payload["module"] = payload.get("module") or module
    validate_ai_skill_output("cases_payload", payload)
    return payload


def call_dashscope_cases(title, module, text_assets, image_assets):
    if image_assets:
        return call_dashscope_cases_legacy(title, module, text_assets, image_assets)
    try:
        return build_cases_payload_from_skills(title, module, text_assets)
    except Exception as exc:
        payload = call_dashscope_cases_legacy(title, module, text_assets, image_assets)
        review = payload.setdefault("review", {})
        review["skill_pipeline"] = "fallback_legacy_prompt"
        review["skill_pipeline_error"] = str(exc)
        return payload


def build_case_visual_refine_prompt(title, module, base_payload, visual_text_assets):
    visual_block = "\n\n".join(visual_text_assets).strip() or "无额外页面知识或设计稿文本。"
    base_json = json.dumps(base_payload, ensure_ascii=False, indent=2)
    return f"""
你是资深移动 App UI 自动化测试工程师，现在执行第二阶段：把“需求理解生成的测试用例 JSON”结合 Figma、截图和页面知识，校准为更可执行的 UI 自动化用例 JSON。

重要原则：
1. 第一阶段 JSON 来自需求文档，是业务覆盖范围的主依据。不要因为截图里没看到某个功能，就删除对应需求用例。
2. Figma、截图、页面知识只用于校准：真实页面名称、入口文案、按钮/Tab 名称、导航路径、可见断言、空态/列表/弹窗文案。
3. 只允许参考与当前需求点相关的 Figma 页面；如果 Figma 文件里混有其他页面，不要把无关页面的入口、按钮、文案带入当前需求用例。
4. 可以优化 steps、assertions、expected_result、repair_hints、start_page、business_path、data_requirements，但不要减少需求覆盖点。
5. 如果视觉资料和需求冲突，在 repair_hints 或 manual_cases 里说明冲突；不要静默丢弃需求。
6. 断言要贴近业务意图，不要过严。动态内容使用兼容表达，例如“展示列表内容或空态提示”“页面展示标题或核心区域”“按钮处于可点击状态”。
7. 每条自动化 case 仍必须可独立执行，步骤短而稳定，不写坐标、XPath、控件层级和固定长等待。
8. 如果第一阶段某条用例只有泛化断言，例如“页面正常展示/跳转成功/结果符合预期”，必须结合视觉资料或业务目标改成 UI 可见业务信号。
9. 如果视觉资料能证明更多当前环境可稳定执行的分支，可以补充 cases，但不得生成和需求无关的控件清单。
10. 输出必须仍是合法 JSON，保留 title、module、analysis、scenarios、cases、manual_cases、review。
11. analysis.requirement_points 必须保留；review 中说明本次视觉校准做了哪些修正。
12. 不允许因为视觉资料缺页就删掉需求场景；只能把入口不确定、数据不稳定、无法自动化的内容转入 manual_cases，并保留 scenarios 覆盖。
13. 保留并补强 analysis.coverage_matrix；视觉校准后，每个 requirement_point 仍必须能追溯到 scenarios、cases 或 manual_cases。

当前标题：{title}
当前模块：{module}

第一阶段需求用例 JSON：
{base_json}

Figma / 截图 / 页面知识文本：
{visual_block}
"""


def call_visual_grounder_skill(title, module, base_payload, visual_text_assets, image_assets):
    payload = {
        "title": title,
        "module": module,
        "base_payload": base_payload,
        "visual_text_assets": compact_text_assets(visual_text_assets),
        "image_count": len(image_assets or []),
        "rules": {
            "do_not_delete_requirements": True,
            "no_coordinates_or_selectors": True,
            "assertions_must_be_ui_visible": True
        }
    }
    grounded = run_ai_skill(
        "visual_grounder",
        payload,
        image_assets=image_assets,
        timeout=360,
        temperature=0.1
    )
    grounded["title"] = grounded.get("title") or title
    grounded["module"] = grounded.get("module") or module
    base_points = ((base_payload.get("analysis") or {}).get("requirement_points") or [])
    if base_points:
        analysis = grounded.setdefault("analysis", {})
        if not analysis.get("requirement_points"):
            analysis["requirement_points"] = base_points
    review = grounded.setdefault("review", {})
    review["visual_grounder_skill"] = "visual_grounder.v1"
    validate_ai_skill_output("cases_payload", grounded)
    return grounded


def call_dashscope_refine_cases_legacy(title, module, base_payload, visual_text_assets, image_assets):
    if not visual_text_assets and not image_assets:
        return base_payload
    prompt = build_case_visual_refine_prompt(title, module, base_payload, visual_text_assets)
    content = dashscope_chat_content(prompt, image_assets=image_assets, temperature=0.1, timeout=360)
    payload = normalize_case_json_from_model(content)
    payload["title"] = payload.get("title") or title
    payload["module"] = payload.get("module") or module
    base_points = ((base_payload.get("analysis") or {}).get("requirement_points") or [])
    if base_points:
        analysis = payload.setdefault("analysis", {})
        if not analysis.get("requirement_points"):
            analysis["requirement_points"] = base_points
    validate_ai_skill_output("cases_payload", payload)
    return payload


def call_dashscope_refine_cases(title, module, base_payload, visual_text_assets, image_assets):
    if not visual_text_assets and not image_assets:
        return base_payload
    try:
        return call_visual_grounder_skill(title, module, base_payload, visual_text_assets, image_assets)
    except Exception as exc:
        payload = call_dashscope_refine_cases_legacy(title, module, base_payload, visual_text_assets, image_assets)
        review = payload.setdefault("review", {})
        review["visual_grounder_skill"] = "fallback_legacy_refine_prompt"
        review["visual_grounder_error"] = str(exc)
        return payload


def build_case_coverage_repair_prompt(title, module, payload, audit):
    payload_json = json.dumps(payload, ensure_ascii=False, indent=2)
    audit_json = json.dumps(audit, ensure_ascii=False, indent=2)
    return f"""
你是资深测试架构师，现在执行第三阶段：覆盖率审查与补全。

目标：保证需求点都被场景和用例覆盖，并且自动化用例的断言贴合业务意图。

硬性要求：
1. 不要重新发散整个需求，只基于已有 JSON 和覆盖率审查结果进行补全/修正。
2. 对 audit.missing_case_points 中的每个需求点，必须补充至少 1 条可执行 cases，或放入 manual_cases 并写清楚为什么不能自动化。
3. 对 audit.missing_scenario_points 中的每个需求点，必须补充 scenarios。
4. 对 audit.generic_assertion_cases 中的用例，必须把断言改成业务意图 + UI 可见信号，不要使用“展示正常/跳转成功/结果符合预期”。
5. 不能删除已有有效 cases；可以去重和合并明显重复用例。
6. 每条新增 case 必须包含 case_id、title、priority、smoke、scenario、goal、coverage、risk、preconditions、steps、assertions、tags、repair_hints。
7. steps 要能在 Midscene 中用自然语言执行；assertions 要允许动态内容，例如“展示列表内容或空态提示”。
8. 输出只允许合法 JSON，结构仍为 title、module、analysis、scenarios、cases、manual_cases、review。
9. 必须补齐 analysis.coverage_matrix：每个 requirement_point 都要说明正常/异常/边界场景，以及进入 cases 还是 manual_cases；不能只补 cases 不补场景。
10. 不得删除已有有效业务链路；如果合并重复用例，要在 review 中说明合并原因，并保留覆盖点。

当前标题：{title}
当前模块：{module}

覆盖率审查结果：
{audit_json}

待修正 JSON：
{payload_json}
"""


def call_coverage_auditor_skill(title, module, payload, local_audit=None):
    normalized = normalize_cases_payload(payload)
    targets = generation_volume_targets(normalized.get("analysis") or {})
    request = {
        "title": title,
        "module": module,
        "payload": normalized,
        "local_audit": local_audit or {},
        "generation_targets": targets,
        "rules": {
            "requirement_points_must_map_to_scenarios": True,
            "requirement_points_must_map_to_cases_or_manual_cases": True,
            "generic_assertions_are_not_allowed": True,
            "min_automation_cases": targets.get("min_automation_cases"),
            "target_automation_cases": targets.get("target_automation_cases")
        }
    }
    result = run_ai_skill("coverage_auditor", request, timeout=240, temperature=0.1)
    result.setdefault("missing_case_points", result.get("missing_requirement_points") or [])
    result.setdefault("missing_scenario_points", [])
    result.setdefault("generic_assertion_cases", [])
    result.setdefault("duplicate_cases", [])
    result.setdefault("questions", [])
    result["coverage_auditor_skill"] = "coverage_auditor.v1"
    result["ok"] = bool(result.get("ok")) or not (
        result.get("missing_requirement_points")
        or result.get("missing_case_points")
        or result.get("missing_scenario_points")
        or result.get("generic_assertion_cases")
        or result.get("duplicate_cases")
    )
    return result


def improve_case_coverage(title, module, payload, max_rounds=1):
    current = normalize_cases_payload(payload)
    for _ in range(max_rounds):
        current, local_audit = audit_case_coverage(current)
        targets = generation_volume_targets(current.get("analysis") or {})
        enough_cases = safe_int(local_audit.get("case_count"), 0) >= safe_int(targets.get("min_automation_cases"), 0)
        if local_audit.get("ok") and enough_cases and not AI_COVERAGE_MODEL_WHEN_LOCAL_OK:
            review = current.setdefault("review", {})
            local_audit["coverage_auditor_skill"] = "skipped_local_audit_ok"
            local_audit["generation_targets"] = targets
            review["coverage_audit"] = local_audit
            review["coverage_auditor_skipped"] = "本地覆盖审查已通过且用例数达到下限，跳过额外模型审查以降低超时风险"
            return current, local_audit
        try:
            audit = call_coverage_auditor_skill(title, module, current, local_audit)
            review = current.setdefault("review", {})
            review["coverage_audit"] = audit
        except Exception as exc:
            audit = local_audit
            review = current.setdefault("review", {})
            review["coverage_auditor_skill"] = "fallback_local_audit"
            review["coverage_auditor_error"] = str(exc)
        if audit.get("ok"):
            return current, audit
        prompt = build_case_coverage_repair_prompt(title, module, current, audit)
        content = dashscope_chat_content(prompt, image_assets=None, temperature=0.1, timeout=360)
        current = normalize_case_json_from_model(content)
        current["title"] = current.get("title") or title
        current["module"] = current.get("module") or module
        validate_ai_skill_output("cases_payload", current)
    current, audit = audit_case_coverage(current)
    return current, audit


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
    files = d.get("files") or []
    reuse_assets = safe_bool(d.get("reuse_assets") or d.get("reuseAssets") or d.get("regenerate"))

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

    has_figma = bool((d.get("figma_url") or d.get("figmaUrl") or meta.get("figma_url") or "").strip())
    if case_set_id and (has_figma or reuse_assets):
        removed = clear_auto_figma_ui_design_assets(case_set_id)
        if job_id and removed:
            suffix = "，将重新按需求筛选" if has_figma else "，本次没有可用 Figma 链接，不再沿用旧误选页面"
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
        figma_future = executor.submit(load_figma_generation_context, d, app_package, job_id, query_text, case_set_id, title, module)
        try:
            knowledge_texts, knowledge_images, used_knowledge_pages = knowledge_future.result()
        except Exception as e:
            knowledge_texts, knowledge_images, used_knowledge_pages = [], [], []
            if job_id:
                update_generate_job(job_id, progress=36, step="读取页面知识", message=f"页面知识读取失败，已跳过：{str(e)[:80]}")
        try:
            figma_texts, figma_images, used_figma_pages, ignored_figma_pages, saved_figma_designs = figma_future.result()
        except Exception as e:
            figma_texts, figma_images, used_figma_pages, ignored_figma_pages, saved_figma_designs = [], [], [], [], []
            if job_id:
                update_generate_job(job_id, progress=38, step="解析 Figma", message=f"Figma 解析失败，已跳过：{str(e)[:80]}")
    used_reference_pages = used_figma_pages + used_knowledge_pages
    visual_text_assets = figma_texts + knowledge_texts
    visual_image_assets = figma_images + knowledge_images + uploaded_image_assets

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
                    knowledge_images,
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
        payload, coverage_audit = improve_case_coverage(title, module, payload, max_rounds=1)
    except Exception as e:
        payload, coverage_audit = audit_case_coverage(payload)
        review = payload.setdefault("review", {})
        review["coverage_repair_error"] = str(e)
        review["remaining_risks"] = normalize_text_list(review.get("remaining_risks") or []) + [
            "覆盖率补全模型调用失败，已保留当前用例并记录覆盖审查结果"
        ]
    payload["id"] = case_set_id
    payload["module"] = module

    if job_id:
        update_generate_job(job_id, progress=75, step="保存用例 JSON", message="正在保存模型生成的用例 JSON")
    write_json_file(cases_path(case_set_id), payload)

    if job_id:
        update_generate_job(job_id, progress=85, step="转换 YAML", message="正在转换 Midscene YAML")
    converted_payload = split_automation_ready_cases(payload)
    _, yaml = cases_to_midscene_yaml(converted_payload, app_package=app_package)
    yaml_check = validate_midscene_yaml(yaml)
    yaml_executability = validate_midscene_yaml_executability(yaml)
    module_dir = safe_join(TASK_DIR, module)
    os.makedirs(module_dir, exist_ok=True)
    write_text_file(safe_join(module_dir, yaml_file), yaml)

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
    update_task_meta(module, yaml_file, {
        "last_case_set_id": case_set_id,
        "last_case_set_title": title,
        "last_generated_at": summary.get("generated_at"),
        "last_case_count": len(converted_payload.get("cases", [])),
        "last_manual_case_count": len(converted_payload.get("manual_cases", [])),
    })
    job = create_pending_job(module, yaml_file, auto_optimize=auto_optimize, device_id=device_id, runner_id=runner_id, run_mode=run_mode) if create_job else None
    return {
        "ok": True,
        "case_set_id": case_set_id,
        "asset": meta,
        "cases": converted_payload,
        "manual_cases": converted_payload.get("manual_cases", []),
        "module": module,
        "file": yaml_file,
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
        "job": job
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

    if job_id:
        update_generate_job(job_id, progress=35, step="匹配上下文", message="正在匹配页面知识和相关 Figma 页面")
    with ThreadPoolExecutor(max_workers=2) as executor:
        knowledge_future = executor.submit(load_knowledge_context, app_package, query_text, 6, selected_page_ids, knowledge_tier)
        figma_future = executor.submit(load_figma_generation_context, d, app_package, job_id, query_text, case_set_id, title, module)
        try:
            knowledge_texts, knowledge_images, used_knowledge_pages = knowledge_future.result()
        except Exception:
            knowledge_texts, knowledge_images, used_knowledge_pages = [], [], []
        try:
            figma_texts, figma_images, used_figma_pages, ignored_figma_pages, saved_figma_designs = figma_future.result()
        except Exception:
            figma_texts, figma_images, used_figma_pages, ignored_figma_pages, saved_figma_designs = [], [], [], [], []

    visual_text_assets = figma_texts + knowledge_texts
    visual_image_assets = figma_images + knowledge_images + uploaded_image_assets
    stage1_text_assets = (requirement_text_assets + visual_text_assets) or [
        "未提供独立需求文档，请根据标题、模块、Figma/截图和页面知识先归纳业务范围，再生成测试场景和用例脑图。"
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
    if visual_text_assets or visual_image_assets:
        if job_id:
            update_generate_job(
                job_id,
                progress=65,
                step="视觉校准",
                message=visual_reference_message(
                    "正在校准脑图场景，实际送入模型",
                    figma_texts,
                    figma_images,
                    ignored_figma_pages,
                    knowledge_texts,
                    knowledge_images,
                    uploaded_image_assets
                )
            )
        try:
            payload = call_dashscope_refine_cases(title, module, payload, visual_text_assets, visual_image_assets)
            payload.setdefault("review", {})["mindmap_visual_grounded"] = True
        except Exception as e:
            review = payload.setdefault("review", {})
            review["visual_refine_error"] = str(e)
            review["visual_refine_fallback"] = "脑图视觉校准失败，已保留需求和 Figma 文本摘要生成脑图；建议重新生成或补充更聚焦的 Frame 链接"
            if job_id:
                update_generate_job(job_id, progress=67, step="视觉校准跳过", message=f"脑图视觉校准失败但不阻塞：{str(e)[:100]}")

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


def generation_failure_detail(error, job=None):
    job = job or {}
    raw = str(error or "").strip() or "生成失败"
    lower = raw.lower()
    stage = job.get("step") or "AI生成"
    progress = safe_int(job.get("progress"), 0)
    error_type = "generation_error"
    suggestion = "查看上传资料是否完整；如需求或 UI 信息不足，可在生成分析中补充确认项、截图或 UI 稿后重新生成。"
    message = raw
    if "timeout" in lower or "timed out" in lower or "超时" in raw:
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


def run_generate_job(job_id, request_data):
    try:
        update_generate_job(job_id, status="running", progress=5, step="开始生成", message="生成任务已启动")
        result = generate_ui_yaml_from_request(request_data, job_id=job_id)
        if generate_job_cancelled(job_id):
            return
        summary = {
            "module": result["module"],
            "file": result["file"],
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
        if generate_job_cancelled(job_id):
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
        update_generate_job(job_id, status="running", progress=5, step="开始生成脑图", message="只生成脑图任务已启动")
        result = generate_mindmap_from_request(request_data, job_id=job_id)
        if generate_job_cancelled(job_id):
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
        if generate_job_cancelled(job_id):
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


def repair_file_latest_result(d, job_id=None):
    mod = d.get("module", "")
    file = d.get("file", "")
    create_next = safe_bool(d.get("createJob"), True)
    force = safe_bool(d.get("forceRepair") or d.get("force_repair") or d.get("force"))
    if not mod or not file:
        raise ValueError("module 和 file 不能为空")
    if job_id:
        update_generate_job(job_id, progress=15, step="查找失败记录", message="正在查找该 YAML 最近一次失败执行记录")
    job = latest_failed_job_for_file(mod, file)
    if not job:
        if job_id:
            update_generate_job(job_id, progress=35, step="静态体检", message="没有失败记录，执行静态规则体检")
        result = static_repair_yaml_file(mod, file, create_next=create_next)
        if job_id:
            update_generate_job(job_id, progress=85, step="校验保存", message="静态体检完成，正在校验并保存结果")
        result["mode"] = "static"
        return result
    if job_id:
        update_generate_job(job_id, progress=35, step="分析日志", message="已找到失败记录，正在结合日志和页面知识修复")
        update_generate_job(job_id, progress=55, step="生成修复方案", message="正在生成修复方案并保护原业务链路")
    result = repair_job_and_create_next(job, create_next=create_next, force=force)
    if job_id:
        update_generate_job(job_id, progress=85, step="校验保存", message="修复方案已生成，正在校验 YAML 和保存版本")
    with JOB_LOCK:
        jobs = load_jobs()
        for item in jobs:
            if item.get("job_id") == job.get("job_id"):
                item["manual_repair_result"] = result
                break
        save_jobs(jobs)
    result["mode"] = "ai"
    return result


def repair_task_latest_result(d, job_id=None):
    mod = d.get("module", "")
    file = d.get("file", "")
    task_name = d.get("taskName") or d.get("task_name") or ""
    create_next = safe_bool(d.get("createJob"), True)
    force = safe_bool(d.get("forceRepair") or d.get("force_repair") or d.get("force"))
    if not mod or not file or not task_name:
        raise ValueError("module、file 和 taskName 不能为空")
    if job_id:
        update_generate_job(job_id, progress=15, step="查找失败记录", message="正在查找该用例最近一次失败执行记录")
    job = latest_failed_job_for_file_task(mod, file, task_name)
    if not job:
        if job_id:
            update_generate_job(job_id, progress=35, step="静态体检", message="没有失败记录，执行单条静态规则体检")
        result = static_repair_yaml_task(mod, file, task_name, create_next=create_next)
        if job_id:
            update_generate_job(job_id, progress=85, step="校验保存", message="单条静态体检完成，正在校验并保存结果")
        result["mode"] = "static"
        return result
    if job_id:
        update_generate_job(job_id, progress=35, step="分析日志", message="已找到失败记录，正在结合日志和页面知识修复单条用例")
        update_generate_job(job_id, progress=55, step="生成修复方案", message="正在生成单条用例修复方案并保护原业务链路")
    result = repair_job_task_and_create_next(job, task_name, create_next=create_next, force=force)
    if job_id:
        update_generate_job(job_id, progress=85, step="校验保存", message="单条修复方案已生成，正在校验 YAML 和保存版本")
    with JOB_LOCK:
        jobs = load_jobs()
        for item in jobs:
            if item.get("job_id") == job.get("job_id"):
                item["manual_task_repair_result"] = result
                break
        save_jobs(jobs)
    result["mode"] = "ai"
    return result


def run_repair_job(job_id, request_data):
    try:
        scope = request_data.get("scope") or "file"
        update_generate_job(job_id, status="running", progress=5, step="开始修复", message="修复任务已启动")
        result = repair_task_latest_result(request_data, job_id=job_id) if scope == "task" else repair_file_latest_result(request_data, job_id=job_id)
        update_generate_job(
            job_id,
            status="success",
            progress=100,
            step="修复完成",
            message="修复完成，已保存 YAML" + ("，并创建重跑任务" if result.get("next_job") else ""),
            result=result
        )
    except Exception as e:
        update_generate_job(job_id, status="failed", progress=90, step="修复失败", message=str(e), error=str(e))


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_HEAD(self):
        return self._safe_call(self._do_HEAD)

    def _safe_call(self, fn):
        try:
            return fn()
        except (BrokenPipeError, ConnectionResetError):
            return
        except BodyTooLarge as e:
            try:
                self._json({"ok": False, "error": str(e) or "请求体过大"}, 413)
            except Exception:
                pass
        except Exception as e:
            print(f"{fn.__name__} failed: {e}\n{traceback.format_exc()}", flush=True)
            try:
                self._json({"ok": False, "error": f"服务端异常：{e}"}, 500)
            except Exception:
                pass

    def _cors(self):
        origin = self.headers.get("Origin", "")
        if origin and origin in TASK_ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        elif not origin:
            self.send_header("Access-Control-Allow-Origin", "null")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type,x-token,x-filename,Authorization")

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _text(self, text, code=200):
        body = text.encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _html(self, text, code=200):
        body = text.encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def _raw_body(self):
        length = int(self.headers.get("Content-Length", 0))
        qs, path = self._qs()
        limit = MAX_UPLOAD_BODY_SIZE if path in ("/report", "/api/report/chunk", "/api/report/chunk-finish") else MAX_BODY_SIZE
        if length > limit:
            raise BodyTooLarge("请求体过大")
        return self.rfile.read(length) if length else b""

    def _body_size_allowed(self, path):
        length = int(self.headers.get("Content-Length", 0))
        limit = MAX_UPLOAD_BODY_SIZE if path in ("/report", "/api/report/chunk", "/api/report/chunk-finish") else MAX_BODY_SIZE
        if length > limit:
            self._json({"ok": False, "error": "请求体过大"}, 413)
            return False
        return True

    def _body(self):
        raw = self._raw_body()
        if not raw:
            return {}
        last_error = None
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin1"):
            try:
                return json.loads(raw.decode(encoding))
            except Exception as e:
                last_error = e
        raise last_error

    def _qs(self):
        parsed = urllib.parse.urlparse(self.path)
        return dict(urllib.parse.parse_qsl(parsed.query)), parsed.path

    def _authorized(self):
        if self.headers.get("x-token", "") == TOKEN:
            return True
        return bool(verify_session_token(bearer_token(self.headers)))

    def _authorized_runner(self):
        return self.headers.get("x-token", "") == TOKEN

    def _authorized_sonic_callback(self):
        return self.headers.get("x-token", "") == SONIC_CALLBACK_TOKEN

    def _authorized_with_qs(self, qs):
        if self._authorized_sonic_callback() or self._authorized():
            return True
        if not ALLOW_QUERY_TOKEN:
            return False
        if (qs or {}).get("token", "") in (TOKEN, SONIC_CALLBACK_TOKEN):
            print("WARNING: query token auth is deprecated; use x-token or Authorization header", flush=True)
            return True
        return False

    def do_GET(self):
        return self._safe_call(self._do_GET)

    def do_POST(self):
        return self._safe_call(self._do_POST)

    def do_DELETE(self):
        return self._safe_call(self._do_DELETE)

    def _do_HEAD(self):
        qs, path = self._qs()
        if path in ("/", "/task-manager.html"):
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            return
        if path.startswith("/api/"):
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            return
        self.send_response(404)
        self._cors()
        self.end_headers()

    def _do_GET(self):
        qs, path = self._qs()

        if path in ("/", "/task-manager.html"):
            html_path = Path(__file__).resolve().with_name("task-manager.html")
            if html_path.exists():
                self._html(read_text_file(html_path))
            else:
                self._text("task-manager.html not found", 404)
            return

        if path.startswith("/assets/"):
            root = Path(__file__).resolve().with_name("assets").resolve()
            rel = path[len("/assets/"):].lstrip("/")
            asset_path = (root / rel).resolve()
            if not str(asset_path).startswith(str(root)) or not asset_path.is_file():
                self._text("asset not found", 404)
                return
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", guess_mime(str(asset_path)))
            self.send_header("Cache-Control", "public, max-age=3600")
            self.end_headers()
            with open(asset_path, "rb") as f:
                self.wfile.write(f.read())
            return

        if path == "/api/health":
            self._json({
                "ok": True,
                "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                "port": PORT,
                "paths": {
                    "tasks": runtime_path_status(TASK_DIR),
                    "reports": runtime_path_status(REPORT_DIR),
                    "learning": runtime_path_status(LEARNING_DIR),
                    "generate_jobs": runtime_path_status(GENERATE_JOB_DIR),
                    "knowledge": runtime_path_status(KNOWLEDGE_DIR),
                    "ai_skills": ai_skills_status(),
                },
                "models": {
                    "dashscope_key": bool(dashscope_api_key(required=False)),
                    "text_model": dashscope_text_model(),
                    "vl_model": dashscope_vl_model(),
                    "base_url": dashscope_base_url(),
                },
                "figma": {
                    "token": bool(os.getenv("FIGMA_TOKEN")),
                    "api_base": os.getenv("FIGMA_API_BASE", DEFAULT_FIGMA_API_BASE),
                },
                "dependencies": {
                    "pyyaml": bool(pyyaml),
                    "yaml_parser": "PyYAML" if pyyaml else "text_fallback",
                }
            })
            return

        if path == "/api/auth/me":
            payload = verify_session_token(bearer_token(self.headers))
            if not payload:
                self._json({"ok": False, "error": "Unauthorized"}, 401)
                return
            self._json({"ok": True, "user": payload.get("user"), "expires_at": payload.get("exp")})
            return

        sensitive_get_paths = {
            "/api/task-apps",
            "/api/task-meta",
            "/api/sonic/config",
            "/api/sonic/runtime-env",
            "/api/preflight/dashboard",
            "/api/jobs",
            "/api/repair-drafts",
            "/api/runners",
            "/api/reports/cleanup",
        }
        if path in sensitive_get_paths and not self._authorized():
            self._json({"ok": False, "error": "Unauthorized"}, 401)
            return

        if path == "/api/reports/cleanup":
            try:
                dry_run = str(qs.get("dry_run") or qs.get("dryRun") or "1").lower() not in ("0", "false", "no")
                days = safe_int(qs.get("days") or qs.get("retention_days") or qs.get("retentionDays"), REPORT_RETENTION_DAYS)
                min_keep = safe_int(qs.get("min_keep") or qs.get("minKeep"), REPORT_RETENTION_MIN_KEEP)
                self._json(cleanup_midscene_reports(days, min_keep, dry_run=dry_run))
            except Exception as e:
                self._json({"ok": False, "error": str(e), "policy": report_cleanup_policy()}, 500)
            return

        if path == "/api/apps":
            self._json(get_available_apps())
            return

        if path == "/api/modules":
            result = {}
            if os.path.exists(TASK_DIR):
                for mod in sorted(os.listdir(TASK_DIR)):
                    mp = safe_join(TASK_DIR, mod)
                    if os.path.isdir(mp):
                        result[mod] = sorted([
                            f for f in os.listdir(mp)
                            if is_visible_yaml_filename(f)
                        ])
            self._json(result)
            return

        if path == "/api/yaml-stats":
            module_filter = (qs.get("module") or "").strip()
            result = {}
            if os.path.exists(TASK_DIR):
                module_names = [module_filter] if module_filter else sorted(os.listdir(TASK_DIR))
                for mod in module_names:
                    mp = safe_join(TASK_DIR, mod)
                    if not os.path.isdir(mp):
                        continue
                    module_stats = {}
                    for file in sorted(os.listdir(mp)):
                        if not is_visible_yaml_filename(file):
                            continue
                        fpath = safe_join(mp, file)
                        try:
                            text = read_text_file(fpath)
                            module_stats[file] = yaml_priority_stats(text)
                        except Exception as e:
                            module_stats[file] = {
                                "total": 0, "p0": 0, "p1": 0, "p2": 0, "p3": 0, "smoke": 0,
                                "loaded": True, "error": str(e)
                            }
                    result[mod] = module_stats
            self._json({"ok": True, "stats": result})
            return

        if path == "/api/task-meta":
            self._json({"ok": True, "meta": load_task_meta()})
            return

        if path == "/api/task-apps":
            self._json({"ok": True, "apps": sonic_notify_known_apps()})
            return

        if path == "/api/sonic/config":
            self._json({
                "ok": True,
                "base_url": sonic_base_url(),
                "api_prefix": sonic_api_prefix(),
                "token_configured": bool(sonic_token()),
                "token_source": sonic_token_source(),
                "token_fingerprint": sonic_token_fingerprint(),
                "public_task_url": os.getenv("MIDSCENE_PUBLIC_BASE_URL") or os.getenv("TASK_PUBLIC_BASE_URL") or "http://101.34.197.12:8088"
            })
            return

        if path == "/api/sonic/runtime-env":
            if not self._authorized():
                self._json({"ok": False, "error": "Unauthorized"}, 401)
                return
            env = midscene_runtime_env()
            self._json({
                "ok": True,
                "env": env,
                "preview": runtime_env_preview(env),
                "env_file": ENV_FILE_LOAD_STATUS,
            })
            return

        if path == "/api/preflight/dashboard":
            live = safe_bool(qs.get("live") or qs.get("sonic") or qs.get("includeSonic"))
            try:
                self._json({"ok": True, **platform_preflight_dashboard(include_sonic_scan=live)})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
            return

        if path == "/api/repair-drafts":
            drafts = load_repair_drafts()
            job_id = qs.get("job_id") or qs.get("jobId")
            include_all = safe_bool(qs.get("include_all") or qs.get("includeAll"))
            if job_id:
                drafts = [draft for draft in drafts if draft.get("jobId") == job_id or draft.get("job_id") == job_id]
            if not include_all:
                drafts = [draft for draft in drafts if draft.get("status") in ("DRAFTED", "WAIT_CONFIRM")]
            self._json({"ok": True, "drafts": drafts})
            return

        if path == "/api/sonic/cases":
            rows = list_task_case_assets(qs.get("module", ""), qs.get("file", ""))
            self._json({"ok": True, "cases": rows, "sync": load_sonic_sync().get("cases", {})})
            return

        if path == "/api/sonic/status":
            try:
                rows = list_task_case_assets(qs.get("module", ""), qs.get("file", ""))
                status_rows = [sonic_live_case_status(row) for row in rows if not row.get("error")]
                summary = {
                    "total": len(status_rows),
                    "bridge": len([row for row in status_rows if row.get("step_state") == "bridge"]),
                    "legacy": len([row for row in status_rows if row.get("step_state") == "legacy"]),
                    "mixed": len([row for row in status_rows if row.get("step_state") == "mixed"]),
                    "missing": len([row for row in status_rows if row.get("step_state") in ("missing", "not_published")]),
                    "project_missing": len([row for row in status_rows if row.get("step_state") == "project_missing"]),
                }
                self._json({"ok": True, "summary": summary, "cases": status_rows})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
            return

        if path == "/api/sonic/suite-results":
            data = load_sonic_suite_results()
            suites = list((data.get("suites") or {}).values())
            suites.sort(key=lambda item: safe_int(item.get("last_update_ts") or item.get("created_ts"), 0), reverse=True)
            limit = max(1, min(100, safe_int(qs.get("limit"), 30)))
            self._json({"ok": True, "suites": suites[:limit], "active": data.get("active") or {}})
            return

        if path == "/api/sonic/case":
            try:
                case = find_task_case_asset(qs.get("case_id") or qs.get("caseId"))
                self._json({
                    "ok": True,
                    "case": case,
                    "context": task_case_sonic_context(case),
                    "yaml": task_case_yaml(case)
                })
            except FileNotFoundError as e:
                self._json({"ok": False, "error": str(e)}, 404)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 400)
            return

        if path == "/api/sonic/case-yaml":
            try:
                case = find_task_case_asset(qs.get("case_id") or qs.get("caseId"))
                self._text(task_case_yaml(case))
            except FileNotFoundError as e:
                self._text(str(e), 404)
            except Exception as e:
                self._text(str(e), 400)
            return

        if path == "/api/sonic/bridge-groovy":
            # Accept runner token (from Sonic step scripts) and callback token
            x_token = self.headers.get("x-token", "")
            if not (x_token and x_token in (TOKEN, SONIC_CALLBACK_TOKEN)) and not self._authorized():
                self._text("Unauthorized", 401)
                return
            bridge_path = os.getenv("SONIC_BRIDGE_GROOVY_PATH", "/opt/sonic-midscene-task-runner.groovy")
            bridge = read_text(bridge_path, "")
            if not bridge:
                # Local development fallback: this path exists in the Codex workspace.
                bridge = read_text(os.path.join(os.getcwd(), "sonic-midscene-task-runner.groovy"), "")
            if not bridge:
                self._text("sonic bridge groovy not found; set SONIC_BRIDGE_GROOVY_PATH", 500)
                return
            self._text(bridge)
            return

        if path == "/api/file":
            try:
                fpath = safe_join(TASK_DIR, qs.get("module", ""), qs.get("file", ""))
            except ValueError:
                self._text("非法路径", 400)
                return
            if os.path.exists(fpath):
                with open(fpath, encoding="utf-8") as f:
                    self._text(f.read())
            else:
                self._text("不存在", 404)
            return

        if path == "/api/file/history":
            mod = qs.get("module", "")
            file = qs.get("file", "")
            if not mod or not file:
                self._json({"ok": False, "error": "module 和 file 不能为空"}, 400)
                return
            self._json({"ok": True, "versions": list_file_versions(mod, file)})
            return

        if path == "/api/file/version":
            mod = qs.get("module", "")
            file = qs.get("file", "")
            version_id = qs.get("version") or qs.get("id")
            if not mod or not file or not version_id:
                self._json({"ok": False, "error": "module、file 和 version 不能为空"}, 400)
                return
            try:
                meta, content = read_file_version(mod, file, version_id)
            except FileNotFoundError:
                self._json({"ok": False, "error": "版本不存在"}, 404)
                return
            except ValueError:
                self._json({"ok": False, "error": "非法路径"}, 400)
                return
            self._json({"ok": True, "version": meta, "content": content})
            return

        if path == "/api/repair/result":
            repair_dir = qs.get("repair_dir") or qs.get("dir") or ""
            try:
                rdir = safe_repair_artifact_dir(repair_dir)
                result = read_json_file(safe_join(rdir, "repair.json"), default=None)
                if not result:
                    self._json({"ok": False, "error": "修复结果不存在"}, 404)
                    return
                before = read_text(safe_join(rdir, "before.yaml"))
                after = read_text(safe_join(rdir, "after.yaml"))
                if "diff_summary" not in result:
                    result["diff_summary"] = yaml_diff_summary(before, after)
                if "changed_line_count" not in result:
                    result["changed_line_count"] = changed_line_count(before, after)
                self._json({"ok": True, "result": result})
            except ValueError as e:
                self._json({"ok": False, "error": str(e)}, 400)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
            return

        if path == "/api/ui/generate-status":
            job_id = qs.get("job_id") or qs.get("id")
            if not job_id:
                self._json({"ok": False, "error": "job_id 不能为空"}, 400)
                return
            job = load_generate_job(job_id)
            if not job:
                self._json({"ok": False, "error": "生成任务不存在"}, 404)
                return
            self._json({"ok": True, "job": sanitize_generate_job_for_client(job)})
            return

        if path == "/api/cases/summary":
            case_set_id = qs.get("case_set_id") or qs.get("id")
            if not case_set_id:
                self._json({"ok": False, "error": "case_set_id 不能为空"}, 400)
                return
            summary = read_json_file(generation_summary_path(case_set_id), default=None)
            if not summary:
                self._json({"ok": False, "error": "生成汇总不存在"}, 404)
                return
            ui_design_meta = filtered_case_ui_design_assets_for_summary(case_set_id, summary)
            if ui_design_meta.get("designs"):
                summary["ui_design_assets"] = ui_design_meta.get("designs") or []
            elif summary.get("ui_design_assets"):
                summary["ui_design_assets"] = []
            if ui_design_meta.get("hidden_designs"):
                summary["hidden_ui_design_assets"] = ui_design_meta.get("hidden_designs") or []
            if ui_design_meta.get("excluded_figma_nodes"):
                summary["excluded_figma_nodes"] = ui_design_meta.get("excluded_figma_nodes") or []
            mindmap_deleted = os.path.exists(generation_mindmap_deleted_path(case_set_id))
            mindmap_exists = os.path.exists(generation_mindmap_path(case_set_id))
            self._json({
                "ok": True,
                "summary": summary,
                "artifacts": {
                    "mindmap_exists": mindmap_exists,
                    "mindmap_deleted": mindmap_deleted,
                    "mindmap_downloadable": mindmap_exists and not mindmap_deleted
                }
            })
            return

        if path == "/api/cases/mindmaps":
            limit = safe_int(qs.get("limit"), 100)
            self._json({"ok": True, "mindmaps": list_generation_mindmaps(limit)})
            return

        if path == "/api/cases/mindmap":
            case_set_id = qs.get("case_set_id") or qs.get("id")
            if not case_set_id:
                self._text("case_set_id 不能为空", 400)
                return
            summary = read_json_file(generation_summary_path(case_set_id), default=None)
            if not summary:
                self._text("生成汇总不存在", 404)
                return
            mm_path = generation_mindmap_path(case_set_id)
            if os.path.exists(generation_mindmap_deleted_path(case_set_id)):
                self._text("脑图文件已删除；请点击刷新脑图文件", 410)
                return
            if not os.path.exists(mm_path):
                write_generation_mindmap(case_set_id, summary)
            try:
                body = read_text_file(mm_path).encode("utf-8")
            except Exception:
                self._text("思维导图不存在", 404)
                return
            filename = generation_artifact_filename(summary, case_set_id, "测试用例.mm")
            send_attachment(self, body, filename, "application/x-freemind; charset=utf-8")
            return

        if path == "/api/cases/ui-designs":
            case_set_id = qs.get("case_set_id") or qs.get("id")
            if not case_set_id:
                self._json({"ok": False, "error": "case_set_id 不能为空"}, 400)
                return
            self._json({"ok": True, **list_case_ui_design_assets(case_set_id)})
            return

        if path == "/api/cases/ui-design-image":
            case_set_id = qs.get("case_set_id") or qs.get("id")
            asset_id = qs.get("asset_id") or qs.get("assetId") or ""
            filename = clean_asset_filename(qs.get("filename") or "")
            if not case_set_id or not (asset_id or filename):
                self._text("case_set_id 和 asset_id 不能为空", 400)
                return
            meta = list_case_ui_design_assets(case_set_id)
            match = None
            for item in meta.get("designs") or []:
                if (asset_id and item.get("asset_id") == asset_id) or (filename and item.get("filename") == filename):
                    match = item
                    break
            if not match:
                self._text("UI 设计稿不存在", 404)
                return
            image_path = safe_join(case_ui_design_dir(case_set_id), match.get("filename") or "")
            if not os.path.exists(image_path):
                self._text("UI 设计稿文件不存在", 404)
                return
            with open(image_path, "rb") as f:
                body = f.read()
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", match.get("mime") or guess_mime(match.get("filename") or "image.png"))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if path == "/api/knowledge/apps":
            details = list_knowledge_app_details()
            self._json({
                "ok": True,
                "apps": [item["package"] for item in details],
                "appDetails": details
            })
            return

        if path == "/api/knowledge/pages":
            app_package = qs.get("app_package") or qs.get("appPackage") or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
            tier = qs.get("tier") or qs.get("library") or "all"
            app_info = task_app_map_by_package().get(app_package) or {}
            self._json({
                "ok": True,
                "app_package": app_package,
                "app_name": app_info.get("name") or app_package,
                "modules": app_info.get("modules") or [],
                "tier": tier,
                "pages": list_knowledge_pages(app_package, tier=tier)
            })
            return

        if path == "/api/knowledge/screenshot":
            app_package = qs.get("app_package") or qs.get("appPackage") or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
            page_id = qs.get("page_id") or qs.get("pageId")
            if not page_id:
                self._text("page_id 不能为空", 400)
                return
            meta = read_json_file(knowledge_meta_path(app_package, page_id), default=None)
            if not meta or not meta.get("screenshot"):
                self._text("截图不存在", 404)
                return
            try:
                image_path = safe_join(knowledge_page_dir(app_package, page_id), meta["screenshot"])
                with open(image_path, "rb") as f:
                    body = f.read()
            except Exception:
                self._text("截图不存在", 404)
                return
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", guess_mime(meta["screenshot"]))
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass
            return

        if path == "/api/baseline/page-refs":
            mod = qs.get("module", "")
            file = clean_filename(qs.get("file", ""))
            app_package = qs.get("app_package") or qs.get("appPackage") or app_package_for_module(mod) or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
            task_name = qs.get("taskName") or qs.get("task_name") or ""
            if not mod or not file:
                self._json({"ok": False, "error": "module 和 file 不能为空"}, 400)
                return
            refs = load_baseline_refs()
            file_ref = refs.get(baseline_ref_key(app_package, mod, file, "")) or {}
            task_ref = refs.get(baseline_ref_key(app_package, mod, file, task_name)) or {}
            file_page_ids = file_ref.get("page_ids") or []
            task_page_ids = task_ref.get("page_ids") or []
            merged = get_baseline_ref_page_ids(app_package, mod, file, task_name)
            pages = list_knowledge_pages(app_package, tier="baseline")
            selected_pages = []
            for page in pages:
                page_id = page.get("page_id")
                if page_id not in merged:
                    continue
                item = dict(page)
                in_file = page_id in file_page_ids
                in_task = page_id in task_page_ids
                item["ref_source"] = "both" if in_file and in_task else ("task" if in_task else "file")
                selected_pages.append(item)
            self._json({
                "ok": True,
                "app_package": app_package,
                "module": mod,
                "file": file,
                "task_name": task_name,
                "file_page_ids": file_page_ids,
                "task_page_ids": task_page_ids,
                "merged_page_ids": merged,
                "selected_pages": selected_pages,
                "pages": pages
            })
            return

        if path == "/api/jobs":
            recover_timed_out_jobs()
            with JOB_LOCK:
                jobs = load_jobs()
            with RUNNER_LOCK:
                runners = load_runners()
            active_jobs = [job for job in jobs if job.get("status") in ("pending", "running")]
            active_ids = {job.get("job_id") for job in active_jobs}
            recent_done = [job for job in jobs if job.get("job_id") not in active_ids][-100:]
            result_jobs = [normalize_job_record(annotate_job_queue_state(job, runners)) for job in (active_jobs + recent_done)]
            background_jobs = [normalize_job_record(job) for job in list_generate_jobs(80)]
            self._json({"ok": True, "jobs": result_jobs, "background_jobs": background_jobs})
            return

        if path.startswith("/api/assets/"):
            case_set_id = path.split("/")[-1]
            try:
                meta = read_json_file(asset_meta_path(case_set_id), default=None)
            except ValueError:
                self._json({"ok": False, "error": "非法路径"}, 400)
                return
            if not meta:
                self._json({"ok": False, "error": "资产不存在"}, 404)
                return
            self._json({"ok": True, "asset": meta})
            return

        if path.startswith("/api/cases/"):
            case_set_id = path.split("/")[-1]
            try:
                payload = read_json_file(cases_path(case_set_id), default=None)
            except ValueError:
                self._json({"ok": False, "error": "非法路径"}, 400)
                return
            if not payload:
                self._json({"ok": False, "error": "用例集不存在"}, 404)
                return
            self._json({"ok": True, "case_set_id": case_set_id, "cases": payload})
            return

        if path == "/api/runner/jobs/next":
            recover_timed_out_jobs()
            if not self._authorized_runner():
                self._json({"ok": False, "error": "Unauthorized"}, 401)
                return
            runner_id = qs.get("runner_id", "runner")
            runner_device_ids_qs = set(filter(None, (qs.get("devices") or "").split(",")))
            with RUNNER_LOCK:
                runners = load_runners()
                runner_info = runners.get(runner_id, {})
            available_devices = runner_device_ids(runner_info) | runner_device_ids_qs
            with JOB_LOCK:
                jobs = load_jobs()
                selected = None
                for job in jobs:
                    if job.get("status") != "pending":
                        continue
                    target_runner = job.get("target_runner_id") or ""
                    target_device = job.get("device_id") or ""
                    if target_runner and target_runner != runner_id:
                        continue
                    if target_device and target_device not in available_devices:
                        continue
                    if not target_device and not available_devices:
                        continue
                    if job.get("status") == "pending":
                        selected = job
                        break
                if selected:
                    selected["status"] = "running"
                    selected["runner_id"] = runner_id
                    if not selected.get("device_id") and available_devices:
                        selected["device_id"] = sorted(available_devices)[0]
                    selected["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    save_jobs(jobs)
                    update_task_meta(selected["module"], selected["file"], {
                        "last_job_id": selected["job_id"],
                        "last_status": "running",
                        "last_target_task_name": selected.get("target_task_name", ""),
                        "last_run_at": selected["started_at"]
                    })

            if not selected:
                self._json({"ok": True, "job": None})
                return

            try:
                yaml_path = safe_join(TASK_DIR, selected["module"], selected["file"])
                with open(yaml_path, encoding="utf-8") as f:
                    yaml_content = f.read()
                target_task_name = selected.get("target_task_name", "")
                if target_task_name:
                    app_package = resolve_app_package(selected["module"], selected["file"], yaml_content)
                    yaml_content = yaml_with_single_task(yaml_content, target_task_name, app_package=app_package)
            except Exception as e:
                with JOB_LOCK:
                    jobs = load_jobs()
                    for job in jobs:
                        if job.get("job_id") == selected.get("job_id"):
                            job["status"] = "failed"
                            job["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                            job["stderr_tail"] = f"任务下发失败：{e}"
                            job["progress"] = job.get("progress") or 0
                            break
                    save_jobs(jobs)
                self._json({"ok": False, "error": str(e)}, 500)
                return

            self._json({
                "ok": True,
                "job": {
                    "job_id": selected["job_id"],
                    "module": selected["module"],
                    "file": selected["file"],
                    "target_task_name": selected.get("target_task_name", ""),
                    "device_id": selected.get("device_id", ""),
                    "yaml_content": yaml_content
                }
            })
            return

        if path == "/api/runners":
            devices = all_online_devices()
            with RUNNER_LOCK:
                runners = load_runners()
            self._json({"ok": True, "runners": runners, "devices": devices})
            return

        # ===== Agent Run GET endpoints =====
        if path == "/api/agent-runs":
            with AGENT_RUN_LOCK:
                runs = load_agent_runs()
            self._json({"ok": True, "runs": runs[:20]})
            return

        m = re.match(r"^/api/agent-runs/([^/]+)$", path)
        if m:
            run_id = urllib.parse.unquote(m.group(1))
            with AGENT_RUN_LOCK:
                runs = load_agent_runs()
            run = next((r for r in runs if r.get("runId") == run_id), None)
            if not run:
                self._json({"ok": False, "error": "Agent Run 不存在"}, 404)
                return
            self._json({"ok": True, "run": run})
            return

        self._text("Not Found", 404)

    def _do_POST(self):
        qs, path = self._qs()
        if not self._body_size_allowed(path):
            return

        if path == "/api/auth/login":
            try:
                d = self._body()
            except Exception:
                d = {}
            username = str(d.get("username") or "").strip()
            password = str(d.get("password") or "")
            if username != TASK_ADMIN_USER or not task_password_valid(password):
                self._json({"ok": False, "error": "账号或密码错误"}, 401)
                return
            token = issue_session_token(username)
            self._json({"ok": True, "user": username, "token": token, "expires_in": max(300, TASK_SESSION_TTL_SECONDS)})
            return

        if path == "/api/auth/logout":
            token = bearer_token(self.headers)
            if token:
                REVOKED_SESSION_TOKENS.add(token)
            self._json({"ok": True})
            return

        if path.startswith("/api/") and path not in SONIC_SUITE_COMPLETION_PATHS and not self._authorized():
            self._json({"ok": False, "error": "Unauthorized"}, 401)
            return

        if path == "/api/repair-drafts":
            try:
                d = self._body()
            except Exception:
                d = {}
            try:
                draft = upsert_repair_draft(d)
                self._json({"ok": True, "draft": draft})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 400)
            return

        if path == "/api/repair-drafts/reject":
            try:
                d = self._body()
            except Exception:
                d = {}
            draft_id = d.get("draftId") or d.get("draft_id")
            draft = repair_draft_by_id(draft_id)
            if not draft:
                self._json({"ok": False, "error": "修复草稿不存在"}, 404)
                return
            draft["status"] = "REJECTED"
            draft["rejectReason"] = d.get("reason") or d.get("rejectReason") or ""
            draft["reject_reason"] = draft["rejectReason"]
            draft["rejectedAt"] = draft["rejected_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            draft = upsert_repair_draft(draft)
            self._json({"ok": True, "draft": draft})
            return

        if path == "/api/repair-drafts/apply":
            try:
                d = self._body()
            except Exception:
                d = {}
            draft_id = d.get("draftId") or d.get("draft_id")
            draft = repair_draft_by_id(draft_id)
            if not draft:
                self._json({"ok": False, "error": "修复草稿不存在"}, 404)
                return
            if draft.get("status") not in ("DRAFTED", "WAIT_CONFIRM"):
                self._json({"ok": False, "error": f"当前草稿状态不可应用：{draft.get('status')}"}, 400)
                return
            if not safe_bool(d.get("confirmApply") or d.get("confirm_apply")):
                self._json({"ok": False, "error": "必须人工确认 confirmApply=true 后才能应用修复草稿"}, 400)
                return
            risk_hits = draft.get("riskHits") or draft.get("risk_hits") or []
            if risk_hits and not safe_bool(d.get("confirmRisk") or d.get("confirm_risk")):
                self._json({"ok": False, "error": "修复草稿包含高风险动作，必须 confirmRisk=true"}, 400)
                return
            module = draft.get("module") or d.get("module") or ""
            file = clean_filename(draft.get("file") or d.get("file") or "")
            fixed_yaml = draft.get("fixedYaml") or draft.get("fixed_yaml") or ""
            if not module or not file:
                self._json({"ok": False, "error": "草稿缺少 module/file，不能应用"}, 400)
                return
            if not str(fixed_yaml or "").strip():
                self._json({"ok": False, "error": "草稿缺少 fixedYaml，不能应用"}, 400)
                return
            yaml_check = validate_midscene_yaml(fixed_yaml)
            yaml_executability = validate_midscene_yaml_executability(fixed_yaml)
            if not yaml_check.get("ok"):
                self._json({"ok": False, "error": "YAML 校验未通过，不能应用", "yaml_check": yaml_check, "yaml_executability": yaml_executability}, 400)
                return
            try:
                fpath = safe_join(TASK_DIR, module, file)
                backup = save_file_version(module, file, reason="before_repair_draft_apply")
                write_text_file(fpath, fixed_yaml)
            except ValueError:
                self._json({"ok": False, "error": "非法路径"}, 400)
                return
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
                return
            draft["status"] = "APPLIED"
            draft["appliedAt"] = draft["applied_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            draft["backup"] = backup or {}
            draft["yaml_check"] = yaml_check
            draft["yaml_executability"] = yaml_executability
            draft = upsert_repair_draft(draft)
            self._json({"ok": True, "applied": True, "draft": draft, "backup": backup, "yaml_check": yaml_check, "yaml_executability": yaml_executability})
            return

        if path == "/api/cases/mindmap":
            case_set_id = qs.get("case_set_id") or qs.get("id")
            if not case_set_id:
                try:
                    d = self._body()
                    case_set_id = d.get("case_set_id") or d.get("id")
                except Exception:
                    case_set_id = ""
            if not case_set_id:
                self._json({"ok": False, "error": "case_set_id 不能为空"}, 400)
                return
            summary = read_json_file(generation_summary_path(case_set_id), default=None)
            if not summary:
                self._json({"ok": False, "error": "生成汇总不存在"}, 404)
                return
            try:
                deleted_path = generation_mindmap_deleted_path(case_set_id)
                if os.path.exists(deleted_path):
                    os.remove(deleted_path)
                mm_path = write_generation_mindmap(case_set_id, summary)
                stat = os.stat(mm_path)
            except ValueError:
                self._json({"ok": False, "error": "非法路径"}, 400)
                return
            self._json({
                "ok": True,
                "case_set_id": case_set_id,
                "mindmap": mm_path,
                "mindmap_exists": True,
                "mindmap_deleted": False,
                "mindmap_size": stat.st_size,
                "mindmap_updated_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stat.st_mtime)),
                "message": "已按现有生成分析刷新脑图文件；不会重新调用 AI，不会改 YAML 或用例"
            })
            return

        if path == "/api/cases/ui-designs":
            try:
                d = self._body()
            except Exception:
                d = {}
            case_set_id = d.get("case_set_id") or d.get("caseSetId") or qs.get("case_set_id") or qs.get("id")
            if not case_set_id:
                self._json({"ok": False, "error": "case_set_id 不能为空"}, 400)
                return
            files = d.get("files") or []
            try:
                summary = read_json_file(generation_summary_path(case_set_id), default={}) or {}
                saved, meta = save_case_ui_design_files(
                    case_set_id,
                    files,
                    source=d.get("source") or "manual",
                    title=d.get("title") or summary.get("title") or "",
                    module=d.get("module") or summary.get("module") or "",
                    extra={
                        "description": d.get("description") or "人工补充的 UI 设计稿/截图",
                        "route": d.get("route") or "",
                        "page_name": d.get("page_name") or d.get("pageName") or ""
                    }
                )
                if summary:
                    summary["ui_design_assets"] = meta.get("designs") or []
                    write_generation_summary(case_set_id, summary)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 400)
                return
            self._json({"ok": True, "case_set_id": case_set_id, "saved": saved, "ui_designs": meta})
            return

        if path == "/api/cases/ui-design-exclusion":
            try:
                d = self._body()
            except Exception:
                d = {}
            case_set_id = d.get("case_set_id") or d.get("caseSetId") or qs.get("case_set_id") or qs.get("id")
            node_id = d.get("node_id") or d.get("nodeId") or qs.get("node_id") or qs.get("nodeId")
            if not case_set_id or not node_id:
                self._json({"ok": False, "error": "case_set_id 和 node_id 不能为空"}, 400)
                return
            try:
                restored, meta = restore_excluded_figma_node(case_set_id, node_id=node_id)
                summary = read_json_file(generation_summary_path(case_set_id), default=None)
                if summary:
                    ui_design_meta = filtered_case_ui_design_assets_for_summary(case_set_id, summary)
                    summary["ui_design_assets"] = ui_design_meta.get("designs") or []
                    summary["hidden_ui_design_assets"] = ui_design_meta.get("hidden_designs") or []
                    summary["excluded_figma_nodes"] = ui_design_meta.get("excluded_figma_nodes") or []
                    write_generation_summary(case_set_id, summary)
            except ValueError as e:
                self._json({"ok": False, "error": str(e)}, 400)
                return
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
                return
            self._json({"ok": True, "restored": restored, "ui_designs": meta})
            return

        if path == "/api/reports/cleanup":
            try:
                d = self._body()
            except Exception:
                d = {}
            try:
                dry_run = safe_bool(d.get("dry_run") if "dry_run" in d else d.get("dryRun"), False)
                days = safe_int(d.get("days") or d.get("retention_days") or d.get("retentionDays"), REPORT_RETENTION_DAYS)
                min_keep = safe_int(d.get("min_keep") or d.get("minKeep"), REPORT_RETENTION_MIN_KEEP)
                self._json(cleanup_midscene_reports(days, min_keep, dry_run=dry_run))
            except Exception as e:
                self._json({"ok": False, "error": str(e), "policy": report_cleanup_policy()}, 500)
            return

        if path == "/report":
            if self.headers.get("x-token", "") != TOKEN:
                self._text("Unauthorized", 401)
                return
            filename = urllib.parse.unquote(self.headers.get("x-filename", "report.html"))
            filename = filename.replace("/", "_").replace("\\", "_")
            os.makedirs(REPORT_DIR, exist_ok=True)
            write_bytes_file(safe_join(REPORT_DIR, filename), self._raw_body())
            self._text(public_report_url(filename))
            return

        if path == "/api/report/chunk":
            if not self._authorized_runner():
                self._json({"ok": False, "error": "Unauthorized"}, 401)
                return
            try:
                d = self._body()
                upload_id = clean_id(d.get("upload_id") or d.get("uploadId") or "", "report")
                filename = urllib.parse.unquote(d.get("filename") or "report.html").replace("/", "_").replace("\\", "_")
                index = safe_int(d.get("index"), -1)
                total = safe_int(d.get("total"), 0)
                content = d.get("contentBase64") or ""
                if not upload_id or index < 0 or total <= 0 or not content:
                    self._json({"ok": False, "error": "分片参数不完整"}, 400)
                    return
                chunk_dir = safe_join(REPORT_DIR, ".chunks", upload_id)
                os.makedirs(chunk_dir, exist_ok=True)
                write_bytes_file(safe_join(chunk_dir, f"{index:05d}.part"), base64.b64decode(content))
                write_text_file(safe_join(chunk_dir, "filename.txt"), filename)
                self._json({"ok": True, "upload_id": upload_id, "index": index, "total": total})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 400)
            return

        if path == "/api/report/chunk-finish":
            if not self._authorized_runner():
                self._json({"ok": False, "error": "Unauthorized"}, 401)
                return
            try:
                d = self._body()
                upload_id = clean_id(d.get("upload_id") or d.get("uploadId") or "", "report")
                total = safe_int(d.get("total"), 0)
                chunk_dir = safe_join(REPORT_DIR, ".chunks", upload_id)
                filename_path = safe_join(chunk_dir, "filename.txt")
                if not upload_id or total <= 0 or not os.path.exists(filename_path):
                    self._json({"ok": False, "error": "分片上传不存在"}, 404)
                    return
                filename = open(filename_path, encoding="utf-8").read().strip() or "report.html"
                final_path = safe_join(REPORT_DIR, filename)
                parts = [safe_join(chunk_dir, f"{index:05d}.part") for index in range(total)]
                for index, part in enumerate(parts):
                    if not os.path.exists(part):
                        self._json({"ok": False, "error": f"缺少分片 {index}"}, 400)
                        return
                tmp_final = final_path + f".tmp.{os.getpid()}.{threading.get_ident()}"
                try:
                    with open(tmp_final, "wb") as out:
                        for part in parts:
                            with open(part, "rb") as f:
                                shutil.copyfileobj(f, out)
                        out.flush()
                        os.fsync(out.fileno())
                    os.replace(tmp_final, final_path)
                finally:
                    if os.path.exists(tmp_final):
                        try:
                            os.remove(tmp_final)
                        except Exception:
                            pass
                shutil.rmtree(chunk_dir, ignore_errors=True)
                self._json({"ok": True, "url": public_report_url(filename)})
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 400)
            return

        if path in SONIC_SUITE_COMPLETION_PATHS:
            if not self._authorized_with_qs(qs):
                self._json({"ok": False, "error": "Unauthorized"}, 401)
                return
            try:
                event = parse_sonic_suite_completion_payload(
                    self._raw_body(),
                    self.headers.get("Content-Type", "")
                )
                if not event.get("suite_name") and not event.get("result_id") and not event.get("total"):
                    self._json({"ok": False, "error": "未识别到 Sonic 测试套结束信息"}, 400)
                    return
                result = register_sonic_suite_completion(event)
                self._json({
                    "ok": True,
                    "suite_key": result.get("suite_key"),
                    "duplicate": result.get("duplicate", False),
                    "status": event.get("status"),
                    "total": event.get("total"),
                    "message": "已接收 Sonic 测试套结束事件，Task 平台将发送整套汇总通知"
                })
            except Exception as e:
                append_sonic_notify_log("sonic_suite_completion_error", {}, error=str(e))
                self._json({"ok": False, "error": str(e)}, 500)
            return

        try:
            d = self._body()
        except Exception as e:
            self._json({"ok": False, "error": f"JSON 解析失败：{e}"}, 400)
            return

        if path in ("/api/convert-cases-json", "/api/generate-yaml"):
            mod = d.get("module", "AI测试")
            raw_content = d.get("content") or d.get("casesJson") or ""
            if not raw_content:
                self._json({"ok": False, "error": "测试用例 JSON 不能为空"}, 400)
                return
            try:
                payload = normalize_cases_payload(raw_content)
                converted_payload = split_automation_ready_cases(payload)
                app_package = d.get("app_package") or d.get("appPackage") or app_package_for_module(mod) or ""
                title, yaml = cases_to_midscene_yaml(converted_payload, app_package=app_package)
                filename = clean_filename(d.get("file") or f"task-{slug_for_file(title)}.yaml")
                module_dir = safe_join(TASK_DIR, mod)
                os.makedirs(module_dir, exist_ok=True)
                write_text_file(safe_join(module_dir, filename), yaml)
                case_set_id = d.get("case_set_id") or d.get("caseSetId") or new_case_set_id()
                converted_payload["id"] = case_set_id
                converted_payload["module"] = mod
                write_json_file(cases_path(case_set_id), converted_payload)
                yaml_check = validate_midscene_yaml(yaml)
                yaml_executability = validate_midscene_yaml_executability(yaml)
                summary = build_generation_summary(
                    case_set_id,
                    title,
                    mod,
                    filename,
                    converted_payload,
                    yaml_check=yaml_check,
                    yaml_executability=yaml_executability
                )
                summary_files = write_generation_summary(case_set_id, summary)
                update_task_meta(mod, filename, {
                    "last_case_set_id": case_set_id,
                    "last_case_set_title": title,
                    "last_generated_at": summary.get("generated_at"),
                    "last_case_count": len(converted_payload.get("cases", [])),
                    "last_manual_case_count": len(converted_payload.get("manual_cases", [])),
                })
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 400)
                return
            self._json({
                "ok": True,
                "case_set_id": case_set_id,
                "module": mod,
                "file": filename,
                "content": yaml,
                "files": [{"file": filename, "content": yaml, "title": title}],
                "caseCount": len(converted_payload["cases"]),
                "manualCaseCount": len(converted_payload.get("manual_cases", [])),
                "scenarioCount": len(converted_payload.get("scenarios", [])),
                "manual_cases": converted_payload.get("manual_cases", []),
                "summary": summary,
                "summaryFiles": summary_files,
                "yamlCheck": yaml_check,
                "yamlExecutability": yaml_executability
            })
            return

        if path == "/api/assets/upload":
            title = d.get("title") or "测试资产"
            module = d.get("module") or "AI测试"
            case_set_id = d.get("case_set_id") or new_case_set_id()
            files = d.get("files") or []
            try:
                meta = save_asset_files(case_set_id, title, module, files)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 400)
                return
            self._json({"ok": True, "asset": meta})
            return

        if path == "/api/knowledge/page":
            try:
                meta = save_knowledge_page(d)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 400)
                return
            self._json({"ok": True, "page": meta})
            return

        if path == "/api/knowledge/analyze":
            try:
                draft = analyze_knowledge_screenshot(d)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
                return
            self._json({"ok": True, "draft": draft})
            return

        if path == "/api/figma/parse":
            try:
                result = parse_figma_design(d)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
                return
            self._json({"ok": True, **result})
            return

        if path == "/api/figma/parse-async":
            job_id = generate_job_id()
            job = {
                "ok": True,
                "job_id": job_id,
                "type": "figma_parse",
                "status": "pending",
                "progress": 0,
                "step": "排队中",
                "message": "Figma 解析任务已创建",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            save_generate_job(job)
            worker = threading.Thread(target=run_figma_parse_job, args=(job_id, d), daemon=True)
            worker.start()
            self._json({"ok": True, "job_id": job_id, "job": job})
            return

        if path == "/api/figma/import":
            try:
                result = import_figma_design(d)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
                return
            self._json({"ok": True, **result})
            return

        if path in ("/api/file/repair-latest-async", "/api/file/repair-task-latest-async"):
            job_id = generate_job_id()
            scope = "task" if path.endswith("repair-task-latest-async") else "file"
            request_data = dict(d)
            request_data["scope"] = scope
            job = {
                "ok": True,
                "job_id": job_id,
                "type": "repair",
                "scope": scope,
                "status": "pending",
                "progress": 0,
                "step": "排队中",
                "message": "修复任务已创建",
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            save_generate_job(job)
            worker = threading.Thread(target=run_repair_job, args=(job_id, request_data), daemon=True)
            worker.start()
            self._json({"ok": True, "job_id": job_id, "job": job})
            return

        if path == "/api/cases/generate":
            case_set_id = d.get("case_set_id")
            if not case_set_id:
                self._json({"ok": False, "error": "case_set_id 不能为空"}, 400)
                return
            meta = read_json_file(asset_meta_path(case_set_id), default=None)
            if not meta:
                self._json({"ok": False, "error": "资产不存在"}, 404)
                return

            title = d.get("title") or meta.get("title") or "测试用例"
            module = d.get("module") or meta.get("module") or "AI测试"
            text_assets, image_assets = load_asset_contents(case_set_id, meta)

            if not text_assets and not image_assets:
                self._json({"ok": False, "error": "没有可用于生成的文本或图片资产"}, 400)
                return

            try:
                payload = call_dashscope_cases(title, module, text_assets, image_assets)
                payload["id"] = case_set_id
                payload["module"] = module
                write_json_file(cases_path(case_set_id), payload)
            except Exception as e:
                self._json({"ok": False, "error": f"生成用例失败：{e}"}, 500)
                return

            self._json({"ok": True, "case_set_id": case_set_id, "cases": payload})
            return

        if path == "/api/ui/generate-yaml":
            try:
                result = generate_ui_yaml_from_request(d)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
                return

            self._json(result)
            return

        if path == "/api/ui/generate-yaml-async":
            job_id = generate_job_id()
            job = {
                "ok": True,
                "job_id": job_id,
                "type": "generate",
                "status": "pending",
                "progress": 0,
                "step": "排队中",
                "message": "生成任务已创建",
                "request_data": d,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            save_generate_job(job)
            worker = threading.Thread(target=run_generate_job, args=(job_id, d), daemon=True)
            worker.start()
            self._json({"ok": True, "job_id": job_id, "job": sanitize_generate_job_for_client(job)})
            return

        if path == "/api/cases/mindmap-only-async":
            job_id = generate_job_id()
            job = {
                "ok": True,
                "job_id": job_id,
                "type": "mindmap_only",
                "status": "pending",
                "progress": 0,
                "step": "排队中",
                "message": "只生成脑图任务已创建",
                "request_data": d,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            save_generate_job(job)
            worker = threading.Thread(target=run_mindmap_only_job, args=(job_id, d), daemon=True)
            worker.start()
            self._json({"ok": True, "job_id": job_id, "job": sanitize_generate_job_for_client(job)})
            return

        if path == "/api/ui/regenerate-yaml-async":
            case_set_id = d.get("case_set_id") or d.get("caseSetId") or d.get("id")
            if not case_set_id:
                self._json({"ok": False, "error": "case_set_id 不能为空"}, 400)
                return
            summary = read_json_file(generation_summary_path(case_set_id), default=None)
            meta = read_json_file(asset_meta_path(case_set_id), default=None)
            if not summary:
                self._json({"ok": False, "error": "生成汇总不存在，无法重新生成"}, 404)
                return
            if not meta or not meta.get("files"):
                self._json({"ok": False, "error": "这个生成批次没有可复用的需求资料，请重新上传需求后生成"}, 400)
                return
            supplement_files = d.get("files") or []
            supplement_text = (d.get("supplement") or d.get("supplement_text") or d.get("confirmation") or "").strip()
            if supplement_text:
                supplement_files = [{
                    "name": f"manual-confirmation-{time.strftime('%Y%m%d-%H%M%S')}.txt",
                    "content": supplement_text
                }] + list(supplement_files)
            if supplement_files:
                meta = append_asset_files(
                    case_set_id,
                    d.get("title") or summary.get("title") or meta.get("title") or "UI自动化用例",
                    d.get("module") or summary.get("module") or meta.get("module") or "AI测试",
                    supplement_files
                )
            figma_url = (
                (d.get("figma_url") or d.get("figmaUrl") or "").strip()
                or find_figma_url_for_case_set(case_set_id, summary=summary, meta=meta)
            )
            figma_mode = d.get("figma_mode") or d.get("figmaMode") or meta.get("figma_mode") or meta.get("figmaMode") or "smart"
            figma_limit = d.get("figma_limit") or d.get("figmaLimit") or meta.get("figma_limit") or meta.get("figmaLimit") or FIGMA_PARSE_LIMIT
            knowledge_page_ids = (
                d.get("knowledge_page_ids")
                or d.get("knowledgePageIds")
                or meta.get("knowledge_page_ids")
                or meta.get("knowledgePageIds")
                or []
            )
            knowledge_tier = d.get("knowledge_tier") or d.get("knowledgeTier") or meta.get("knowledge_tier") or meta.get("knowledgeTier") or "all"
            request_data = {
                "case_set_id": case_set_id,
                "title": d.get("title") or summary.get("title") or meta.get("title") or "UI自动化用例",
                "module": d.get("module") or summary.get("module") or meta.get("module") or "AI测试",
                "file": d.get("file") or summary.get("yaml_file") or f"task-{slug_for_file(summary.get('title') or meta.get('title') or 'UI自动化用例')}.yaml",
                "app_package": d.get("app_package") or d.get("appPackage") or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE),
                "knowledge_page_ids": knowledge_page_ids,
                "knowledge_tier": knowledge_tier,
                "figma_url": figma_url,
                "figma_mode": figma_mode,
                "figma_limit": figma_limit,
                "createJob": safe_bool(d.get("createJob") or d.get("create_job")),
                "autoOptimize": safe_bool(d.get("autoOptimize") or d.get("auto_optimize")),
                "run_mode": d.get("run_mode") or d.get("runMode") or "test",
                "reuse_assets": True,
                "regenerate": True
            }
            update_asset_request_context(case_set_id, request_data)
            job_id = generate_job_id()
            job = {
                "ok": True,
                "job_id": job_id,
                "type": "generate",
                "status": "pending",
                "progress": 0,
                "step": "排队中",
                "message": "重新生成用例任务已创建，将按最新策略覆盖生成 YAML 和脑图文件",
                "case_set_id": case_set_id,
                "request_data": request_data,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            save_generate_job(job)
            worker = threading.Thread(target=run_generate_job, args=(job_id, request_data), daemon=True)
            worker.start()
            self._json({"ok": True, "job_id": job_id, "job": sanitize_generate_job_for_client(job)})
            return

        if path.startswith("/api/ui/generate-jobs/") and path.endswith("/retry"):
            job_id = path.split("/")[4]
            old_job = load_generate_job(job_id)
            if not old_job:
                self._json({"ok": False, "error": "生成任务不存在"}, 404)
                return
            if old_job.get("type") != "generate":
                self._json({"ok": False, "error": "只有 AI 生成任务支持重试"}, 400)
                return
            request_data = generate_retry_request_from_job(old_job)
            if not request_data:
                self._json({"ok": False, "error": "这个生成任务没有可复用的原始请求，请回到生成分析或重新上传需求后生成"}, 400)
                return
            next_job_id = generate_job_id()
            next_job = {
                "ok": True,
                "job_id": next_job_id,
                "type": "generate",
                "status": "pending",
                "progress": 0,
                "step": "排队中",
                "message": f"已从失败任务 {job_id} 创建重试",
                "case_set_id": request_data.get("case_set_id") or old_job.get("case_set_id") or "",
                "retry_from_job_id": job_id,
                "request_data": request_data,
                "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            save_generate_job(next_job)
            worker = threading.Thread(target=run_generate_job, args=(next_job_id, request_data), daemon=True)
            worker.start()
            self._json({"ok": True, "job_id": next_job_id, "job": sanitize_generate_job_for_client(next_job)})
            return

        if path.startswith("/api/ui/generate-jobs/") and path.endswith("/cancel"):
            job_id = path.split("/")[4]
            job = load_generate_job(job_id)
            if not job:
                self._json({"ok": False, "error": "生成任务不存在"}, 404)
                return
            if job.get("status") not in ("pending", "running"):
                self._json({"ok": False, "error": "只有排队中或执行中的生成任务可以取消"}, 400)
                return
            job = update_generate_job(
                job_id,
                status="cancelled",
                progress=safe_int(job.get("progress"), 0),
                step="已取消",
                message="用户已取消后台生成任务",
                cancel_reason=d.get("reason") or "manual",
                finished_at=time.strftime("%Y-%m-%d %H:%M:%S")
            )
            self._json({"ok": True, "job": sanitize_generate_job_for_client(job)})
            return

        if path == "/api/runner/heartbeat":
            if not self._authorized():
                self._json({"ok": False, "error": "Unauthorized"}, 401)
                return
            runner_id = d.get("runner_id") or d.get("runnerId") or "runner"
            devices = normalize_device_list(d.get("devices") or [])
            with RUNNER_LOCK:
                runners = load_runners()
                runners[runner_id] = {
                    "runner_id": runner_id,
                    "devices": devices,
                    "workspace": d.get("workspace", ""),
                    "hostname": d.get("hostname", ""),
                    "last_seen": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "last_seen_ts": time.time()
                }
                save_runners(runners)
            self._json({"ok": True, "runner_id": runner_id, "devices": devices})
            return

        if path.startswith("/api/cases/"):
            case_set_id = path.split("/")[-1]
            payload = d.get("cases") or d.get("content") or d
            try:
                normalized = normalize_cases_payload(payload)
                normalized["id"] = case_set_id
                normalized["module"] = d.get("module") or normalized.get("module") or "AI测试"
                write_json_file(cases_path(case_set_id), normalized)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 400)
                return
            self._json({"ok": True, "case_set_id": case_set_id, "cases": normalized})
            return

        if path == "/api/baseline/page-refs":
            mod = d.get("module", "")
            file = d.get("file", "")
            app_package = d.get("app_package") or d.get("appPackage") or app_package_for_module(mod) or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
            task_name = d.get("taskName") or d.get("task_name") or ""
            page_ids = d.get("page_ids") or d.get("pageIds") or []
            if not mod or not file:
                self._json({"ok": False, "error": "module 和 file 不能为空"}, 400)
                return
            row = set_baseline_ref_page_ids(app_package, mod, file, task_name, page_ids)
            self._json({"ok": True, "ref": row})
            return

        if path == "/api/run-request":
            mod = d.get("module", "")
            file = d.get("file", "")
            auto_optimize = automatic_baseline_repair_enabled(d.get("autoOptimize", d.get("auto_optimize")))
            run_mode = d.get("run_mode") or d.get("runMode") or ("baseline" if auto_optimize else "test")
            device_id = d.get("device_id") or d.get("deviceId") or ""
            runner_id = d.get("runner_id") or d.get("runnerId") or ""
            target_task_name = d.get("target_task_name") or d.get("targetTaskName") or ""
            if not mod or not file:
                self._json({"ok": False, "error": "module 和 file 不能为空"}, 400)
                return
            try:
                yaml_path = safe_join(TASK_DIR, mod, file)
                if not os.path.exists(yaml_path):
                    self._json({"ok": False, "error": "YAML 文件不存在"}, 404)
                    return
                if target_task_name:
                    with open(yaml_path, encoding="utf-8") as f:
                        yaml_content = f.read()
                    app_package = resolve_app_package(mod, file, yaml_content)
                    yaml_with_single_task(yaml_content, target_task_name, app_package=app_package)
            except ValueError:
                self._json({"ok": False, "error": "非法路径"}, 400)
                return
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 400)
                return

            job = create_pending_job(mod, file, auto_optimize=auto_optimize, device_id=device_id, runner_id=runner_id, run_mode=run_mode, target_task_name=target_task_name)
            self._json({"ok": True, "job": job})
            return

        if path == "/api/sonic/report-ready":
            if not self._authorized():
                self._json({"ok": False, "error": "Unauthorized"}, 401)
                return
            try:
                job = attach_sonic_background_report(
                    d.get("job_id") or d.get("jobId") or "",
                    d.get("report_url") or d.get("reportUrl") or "",
                    d.get("local_report_path") or d.get("localReportPath") or "",
                    d.get("report_upload_error") or d.get("reportUploadError") or ""
                )
            except ValueError as e:
                self._json({"ok": False, "error": str(e)}, 404)
                return
            self._json({"ok": True, "job_id": job.get("job_id"), "report_url": job.get("report_url", "")})
            return

        if path == "/api/sonic/result":
            if not self._authorized():
                self._json({"ok": False, "error": "Unauthorized"}, 401)
                return
            mod = d.get("module") or d.get("taskModule") or ""
            file = clean_filename(d.get("file") or d.get("taskName") or "")
            job_id = d.get("job_id") or d.get("jobId") or new_job_id()
            status = normalize_job_status(d.get("status") or ("success" if safe_int(d.get("exitCode"), 1) == 0 else "failed"))
            target_task_name = d.get("target_task_name") or d.get("targetTaskName") or ""
            stdout = sonic_notify_clean_text(d.get("stdout") or d.get("output") or "", fallback="")
            stderr = sonic_notify_clean_text(d.get("stderr") or d.get("error") or "", fallback="日志编码异常，请查看报告")
            report_url = d.get("report_url") or d.get("reportUrl") or ""
            sonic_report_url = d.get("sonic_report_url") or d.get("sonicReportUrl") or d.get("sonic_url") or d.get("sonicUrl") or ""
            local_report_path = sonic_notify_clean_text(d.get("local_report_path") or d.get("localReportPath") or "", fallback="")
            report_upload_error = sonic_notify_clean_text(d.get("report_upload_error") or d.get("reportUploadError") or "", fallback="报告上传错误信息编码异常")
            report_upload_pending = safe_bool(d.get("report_upload_pending") or d.get("reportUploadPending"))
            report_missing_reason = sonic_notify_clean_text(d.get("report_missing_reason") or d.get("reportMissingReason") or "", fallback="")
            upload_warning = sonic_notify_clean_text(d.get("upload_warning") or d.get("uploadWarning") or "", fallback="")
            app_package = (d.get("app_package") or d.get("appPackage") or "").strip()
            app_name = (d.get("app_name") or d.get("appName") or "").strip()
            suite_run_id = (d.get("suite_run_id") or d.get("suiteRunId") or "").strip()
            sonic_suite_id = (d.get("sonic_suite_id") or d.get("sonicSuiteId") or d.get("suite_id") or d.get("suiteId") or "").strip()
            sonic_suite_name = (d.get("sonic_suite_name") or d.get("sonicSuiteName") or d.get("suite_name") or d.get("suiteName") or "").strip()
            suite_started_at = sonic_notify_clean_text(d.get("suite_started_at") or d.get("suiteStartedAt") or d.get("suite_start_time") or d.get("suiteStartTime") or "", fallback="")
            suite_expected_total = safe_int(d.get("suite_expected_total") or d.get("suiteExpectedTotal") or d.get("suite_total") or d.get("suiteTotal"), 0)
            screenshots = d.get("screenshots") if isinstance(d.get("screenshots"), list) else []
            if not mod or not file:
                self._json({"ok": False, "error": "module 和 file 不能为空"}, 400)
                return
            if not app_package:
                try:
                    yaml_text_for_app = read_text_file(safe_join(TASK_DIR, mod, file), "")
                    app_package = resolve_app_package(mod, file, yaml_text_for_app, allow_default=False)
                except Exception:
                    app_package = ""
            app_info = sonic_suite_app_info(app_package, mod)
            if not app_name:
                app_name = app_info.get("name") or app_package
            if not sonic_suite_id:
                sonic_suite_id = app_info.get("sonic_suite_id") or app_info.get("sonicSuiteId") or ""
            if not sonic_suite_name:
                sonic_suite_name = app_info.get("sonic_suite_name") or app_info.get("sonicSuiteName") or ""
            run_dir = safe_join(LEARNING_DIR, "runs", job_id)
            os.makedirs(run_dir, exist_ok=True)
            write_text_file(safe_join(run_dir, "stdout.log"), stdout)
            write_text_file(safe_join(run_dir, "stderr.log"), stderr)
            saved_screenshots = []
            if screenshots:
                screenshot_dir = safe_join(run_dir, "screenshots")
                os.makedirs(screenshot_dir, exist_ok=True)
                for idx, shot in enumerate(screenshots[:4], start=1):
                    if not isinstance(shot, dict) or not shot.get("contentBase64"):
                        continue
                    raw_name = clean_asset_filename(shot.get("name") or f"midscene-{idx}.png")
                    try:
                        data = base64.b64decode(shot["contentBase64"])
                    except Exception:
                        continue
                    if not data or len(data) > 2 * 1024 * 1024:
                        continue
                    name = f"{idx:02d}-{raw_name}"
                    write_bytes_file(safe_join(screenshot_dir, name), data)
                    saved_screenshots.append({
                        "name": name,
                        "mime": shot.get("mime") or guess_mime(name),
                        "local_path": shot.get("local_path") or shot.get("localPath") or ""
                    })
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            job = {
                "job_id": job_id,
                "case_id": d.get("case_id") or d.get("caseId") or "",
                "module": mod,
                "file": file,
                "target_task_name": target_task_name,
                "status": status,
                "run_mode": d.get("run_mode") or d.get("runMode") or "test",
                "auto_optimize": automatic_baseline_repair_enabled(d.get("autoOptimize", d.get("auto_optimize"))),
                "attempt": safe_int(d.get("attempt"), 1),
                "max_attempt": safe_int(d.get("max_attempt") or d.get("maxAttempt"), 2),
                "parent_job_id": d.get("parent_job_id") or d.get("parentJobId") or "",
                "runner_id": d.get("runner_id") or d.get("runnerId") or "sonic",
                "device_id": d.get("device_id") or d.get("deviceId") or "",
                "created_at": d.get("created_at") or now,
                "started_at": d.get("started_at") or now,
                "report_url": report_url,
                "sonic_report_url": sonic_report_url,
                "local_report_path": local_report_path,
                "report_upload_error": report_upload_error,
                "report_upload_pending": report_upload_pending,
                "report_missing_reason": report_missing_reason,
                "upload_warning": upload_warning,
                "app_package": app_package,
                "app_name": app_name,
                "suite_run_id": suite_run_id,
                "sonic_suite_id": sonic_suite_id,
                "sonic_suite_name": sonic_suite_name,
                "suite_started_at": suite_started_at,
                "suite_expected_total": suite_expected_total,
                "execution_screenshots": saved_screenshots,
                "run_dir": run_dir,
                "stdout_tail": stdout[-2000:],
                "stderr_tail": stderr[-2000:],
                "error": stderr,
                "progress": safe_int(d.get("progress"), 100 if status == "success" else 0),
                "current_task_name": d.get("current_task_name") or d.get("currentTaskName") or d.get("caseName") or target_task_name,
                "current_task_index": safe_int(d.get("current_task_index") or d.get("currentTaskIndex"), 0),
                "completed_task_count": safe_int(d.get("completed_task_count") or d.get("completedTaskCount"), 0),
                "total_task_count": safe_int(d.get("total_task_count") or d.get("totalTaskCount"), 0),
                "progress_message": sonic_notify_clean_text(d.get("message") or "", fallback=""),
                "source": "sonic"
            }
            if status not in ("pending", "running"):
                job["finished_at"] = now
            with JOB_LOCK:
                jobs = load_jobs()
                replaced = False
                for idx, item in enumerate(jobs):
                    if item.get("job_id") == job_id:
                        jobs[idx].update(job)
                        replaced = True
                        break
                if not replaced:
                    jobs.append(job)
                save_jobs(jobs)
            update_task_meta(mod, file, {
                "last_job_id": job_id,
                "last_status": status,
                "last_target_task_name": target_task_name,
                "last_run_at": now,
                "last_report_url": report_url,
                "last_sonic_report_url": sonic_report_url
            })
            if status in ("pending", "running"):
                suite_key = touch_sonic_suite_activity(job)
                if suite_key:
                    job["sonic_suite_key"] = suite_key
                    with JOB_LOCK:
                        target, jobs = find_job(job_id)
                        if target:
                            target["sonic_suite_key"] = suite_key
                            save_jobs(jobs)
                self._json({"ok": True, "job": job, "failure_review": None, "optimize": None})
                return
            suite_key = register_sonic_suite_result(job)
            if suite_key:
                job["sonic_suite_key"] = suite_key
                with JOB_LOCK:
                    target, jobs = find_job(job_id)
                    if target:
                        target["sonic_suite_key"] = suite_key
                        save_jobs(jobs)
            post_processing = start_sonic_result_post_actions(job, stdout, stderr)
            self._json({
                "ok": True,
                "job": job,
                "failure_review": None,
                "optimize": None,
                "post_processing": post_processing
            })
            return

        if path.startswith("/api/runner/jobs/") and path.endswith("/progress"):
            if not self._authorized():
                self._json({"ok": False, "error": "Unauthorized"}, 401)
                return
            job_id = path.split("/")[4]
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            with JOB_LOCK:
                target, jobs = find_job(job_id)
                if not target:
                    self._json({"ok": False, "error": "任务不存在"}, 404)
                    return
                if target.get("status") in ("pending", "running"):
                    target["status"] = "running"
                if not target.get("started_at"):
                    target["started_at"] = now
                progress = safe_int(d.get("progress"), safe_int(target.get("progress"), 0))
                progress = max(0, min(99, progress))
                target["progress"] = progress
                target["current_task_name"] = d.get("current_task_name") or d.get("currentTaskName") or target.get("current_task_name", "")
                target["current_task_index"] = safe_int(d.get("current_task_index") or d.get("currentTaskIndex"), safe_int(target.get("current_task_index"), 0))
                target["completed_task_count"] = safe_int(d.get("completed_task_count") or d.get("completedTaskCount"), safe_int(target.get("completed_task_count"), 0))
                target["total_task_count"] = safe_int(d.get("total_task_count") or d.get("totalTaskCount"), safe_int(target.get("total_task_count"), 0))
                target["progress_message"] = d.get("message") or target.get("progress_message", "")
                target["stdout_tail"] = (d.get("stdout_tail") or d.get("stdoutTail") or target.get("stdout_tail", ""))[-2000:]
                target["updated_at"] = now
                save_jobs(jobs)
            self._json({"ok": True, "job": target})
            return

        if path.startswith("/api/runner/jobs/") and path.endswith("/report-ready"):
            if not self._authorized():
                self._json({"ok": False, "error": "Unauthorized"}, 401)
                return
            job_id = path.split("/")[4]
            try:
                job = attach_sonic_background_report(
                    job_id,
                    d.get("report_url") or d.get("reportUrl") or "",
                    d.get("local_report_path") or d.get("localReportPath") or "",
                    d.get("report_upload_error") or d.get("reportUploadError") or ""
                )
            except ValueError as e:
                self._json({"ok": False, "error": str(e)}, 404)
                return
            self._json({"ok": True, "job_id": job_id, "report_url": job.get("report_url", "")})
            return

        if path.startswith("/api/runner/jobs/") and path.endswith("/result"):
            if not self._authorized():
                self._json({"ok": False, "error": "Unauthorized"}, 401)
                return
            job_id = path.split("/")[4]
            status = normalize_job_status(d.get("status", "failed"))
            run_dir = safe_join(LEARNING_DIR, "runs", job_id)
            os.makedirs(run_dir, exist_ok=True)

            stdout = d.get("stdout", "")
            stderr = d.get("stderr", "")
            summary = d.get("summary")
            report_html = d.get("report_html", "")
            incoming_report_url = d.get("report_url") or d.get("reportUrl") or ""
            local_report_path = d.get("local_report_path") or d.get("localReportPath") or ""
            report_upload_error = d.get("report_upload_error") or d.get("reportUploadError") or ""
            report_upload_pending = safe_bool(d.get("report_upload_pending") or d.get("reportUploadPending"))
            report_missing_reason = d.get("report_missing_reason") or d.get("reportMissingReason") or ""
            upload_warning = d.get("upload_warning") or d.get("uploadWarning") or ""
            attempts = d.get("attempts") if isinstance(d.get("attempts"), list) else []
            screenshots = d.get("screenshots") if isinstance(d.get("screenshots"), list) else []

            write_text_file(safe_join(run_dir, "stdout.log"), stdout)
            write_text_file(safe_join(run_dir, "stderr.log"), stderr)
            if summary is not None:
                write_json_file(safe_join(run_dir, "summary.json"), summary)
            if attempts:
                write_json_file(safe_join(run_dir, "attempts.json"), attempts)
            saved_screenshots = []
            if screenshots:
                screenshot_dir = safe_join(run_dir, "screenshots")
                os.makedirs(screenshot_dir, exist_ok=True)
                for idx, shot in enumerate(screenshots[:4], start=1):
                    if not isinstance(shot, dict) or not shot.get("contentBase64"):
                        continue
                    raw_name = clean_asset_filename(shot.get("name") or f"midscene-{idx}.png")
                    try:
                        data = base64.b64decode(shot["contentBase64"])
                    except Exception:
                        continue
                    if not data or len(data) > 2 * 1024 * 1024:
                        continue
                    name = f"{idx:02d}-{raw_name}"
                    write_bytes_file(safe_join(screenshot_dir, name), data)
                    saved_screenshots.append({
                        "name": name,
                        "mime": shot.get("mime") or guess_mime(name),
                        "local_path": shot.get("local_path") or shot.get("localPath") or ""
                    })

            report_url = incoming_report_url
            if report_html:
                report_name = f"{job_id}.html"
                write_text_file(safe_join(REPORT_DIR, report_name), report_html)
                report_url = public_report_url(report_name)

            with JOB_LOCK:
                jobs = load_jobs()
                found = None
                for job in jobs:
                    if job.get("job_id") == job_id:
                        found = job
                        break
                if found:
                    found["status"] = status
                    found["progress"] = 100 if status == "success" else max(safe_int(found.get("progress"), 0), 0)
                    if status == "success":
                        found["completed_task_count"] = found.get("total_task_count") or found.get("completed_task_count") or 0
                    found["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    found["report_url"] = report_url
                    found["local_report_path"] = local_report_path
                    found["report_upload_error"] = report_upload_error
                    found["report_upload_pending"] = report_upload_pending
                    found["report_missing_reason"] = report_missing_reason
                    found["upload_warning"] = upload_warning
                    found["attempts"] = attempts[-5:] if attempts else found.get("attempts", [])
                    found["execution_screenshots"] = saved_screenshots
                    found["run_dir"] = run_dir
                    found["device_id"] = d.get("device_id") or d.get("deviceId") or found.get("device_id", "")
                    found["stdout_tail"] = stdout[-2000:]
                    found["stderr_tail"] = stderr[-2000:]
                    save_jobs(jobs)
                    update_task_meta(found["module"], found["file"], {
                        "last_job_id": job_id,
                        "last_status": status,
                        "last_target_task_name": found.get("target_task_name", ""),
                        "last_run_at": found["finished_at"],
                        "last_report_url": report_url
                    })

            failure_review = None
            if found and status != "success":
                try:
                    failure_review = call_dashscope_failure_review(found, stdout, stderr, summary)
                except Exception as e:
                    failure_review = {"category": "unknown", "confidence": 0, "reason": f"复检失败：{e}", "evidence": [], "suggested_action": "人工查看日志", "can_auto_repair": False}
                with JOB_LOCK:
                    jobs = load_jobs()
                    for job in jobs:
                        if job.get("job_id") == job_id:
                            job["failure_review"] = failure_review
                            break
                    save_jobs(jobs)

            optimize_result = None
            should_auto_repair = (
                ENABLE_AUTOMATIC_BASELINE_REPAIR
                and found and status != "success"
                and found.get("run_mode") == "baseline"
                and safe_bool(found.get("auto_optimize"))
                and failure_review
                and failure_review.get("category") == "script_issue"
                and safe_int(found.get("attempt"), 1) < safe_int(found.get("max_attempt"), 2)
            )
            if should_auto_repair:
                try:
                    repaired, repair_dir = optimize_job_yaml_by_scope(found, stdout, stderr, summary)
                    next_job = create_pending_job(
                        found["module"],
                        found["file"],
                        auto_optimize=True,
                        max_attempt=safe_int(found.get("max_attempt"), 2),
                        attempt=safe_int(found.get("attempt"), 1) + 1,
                        parent_job_id=job_id,
                        device_id=found.get("device_id", ""),
                        runner_id=found.get("target_runner_id") or found.get("runner_id", ""),
                        run_mode=found.get("run_mode", "baseline"),
                        target_task_name=found.get("target_task_name", "")
                    )
                    optimize_result = {
                        "ok": True,
                        "analysis": repaired.get("analysis", ""),
                        "changes": repaired.get("changes", []),
                        "repair_dir": repair_dir,
                        "next_job": next_job
                    }
                    with JOB_LOCK:
                        jobs = load_jobs()
                        for job in jobs:
                            if job.get("job_id") == job_id:
                                job["optimize_result"] = optimize_result
                                break
                        save_jobs(jobs)
                except Exception as e:
                    optimize_result = {"ok": False, "error": str(e)}
                    with JOB_LOCK:
                        jobs = load_jobs()
                        for job in jobs:
                            if job.get("job_id") == job_id:
                                job["optimize_result"] = optimize_result
                                break
                        save_jobs(jobs)

            self._json({"ok": True, "job_id": job_id, "status": status, "report_url": report_url, "failure_review": failure_review, "optimize": optimize_result})
            return

        if path.startswith("/api/jobs/") and path.endswith("/repair"):
            job_id = path.split("/")[3]
            with JOB_LOCK:
                jobs = load_jobs()
                target = None
                for job in jobs:
                    if job.get("job_id") == job_id:
                        target = job
                        break
            if not target:
                self._json({"ok": False, "error": "任务不存在"}, 404)
                return
            try:
                result = repair_job_and_create_next(target, create_next=True, force=safe_bool(d.get("forceRepair") or d.get("force_repair") or d.get("force")))
                with JOB_LOCK:
                    jobs = load_jobs()
                    for job in jobs:
                        if job.get("job_id") == job_id:
                            job["manual_repair_result"] = result
                            break
                    save_jobs(jobs)
                self._json(result)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
            return

        if path.startswith("/api/jobs/") and path.endswith("/cancel"):
            job_id = path.split("/")[3]
            with JOB_LOCK:
                target, jobs = find_job(job_id)
                if not target:
                    self._json({"ok": False, "error": "任务不存在"}, 404)
                    return
                if target.get("status") not in ("pending", "running"):
                    self._json({"ok": False, "error": "只有排队中或执行中的任务可以取消"}, 400)
                    return
                target["status"] = "cancelled"
                target["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                target["cancel_reason"] = d.get("reason") or "manual"
                save_jobs(jobs)
            update_task_meta(target["module"], target["file"], {
                "last_job_id": job_id,
                "last_status": "cancelled",
                "last_target_task_name": target.get("target_task_name", ""),
                "last_run_at": target.get("finished_at")
            })
            self._json({"ok": True, "job": target})
            return

        if path.startswith("/api/jobs/") and path.endswith("/retry"):
            job_id = path.split("/")[3]
            with JOB_LOCK:
                target, _ = find_job(job_id)
            if not target:
                self._json({"ok": False, "error": "任务不存在"}, 404)
                return
            next_job = create_pending_job(
                target["module"],
                target["file"],
                auto_optimize=automatic_baseline_repair_enabled(target.get("auto_optimize")),
                max_attempt=safe_int(target.get("max_attempt"), 2),
                attempt=safe_int(target.get("attempt"), 1) + 1,
                parent_job_id=job_id,
                device_id=d.get("device_id") or d.get("deviceId") or target.get("device_id", ""),
                runner_id=d.get("runner_id") or d.get("runnerId") or target.get("target_runner_id") or target.get("runner_id", ""),
                run_mode=d.get("run_mode") or d.get("runMode") or target.get("run_mode", "test"),
                target_task_name=d.get("target_task_name") or d.get("targetTaskName") or target.get("target_task_name", "")
            )
            self._json({"ok": True, "job": next_job})
            return

        if path.startswith("/api/jobs/") and path.endswith("/review"):
            job_id = path.split("/")[3]
            category = d.get("category") or "unknown"
            allowed = ("product_bug", "script_issue", "env_issue", "data_issue", "model_issue", "unknown")
            if category not in allowed:
                self._json({"ok": False, "error": "非法归因分类"}, 400)
                return
            with JOB_LOCK:
                target, jobs = find_job(job_id)
                if not target:
                    self._json({"ok": False, "error": "任务不存在"}, 404)
                    return
                review = target.get("failure_review") or {}
                review.update({
                    "category": category,
                    "reason": d.get("reason") or review.get("reason", ""),
                    "suggested_action": d.get("suggested_action") or d.get("suggestedAction") or review.get("suggested_action", ""),
                    "manual_confirmed": True,
                    "confirmed_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "confirmed_by": d.get("user") or "manual",
                    "can_auto_repair": category == "script_issue"
                })
                target["failure_review"] = review
                save_jobs(jobs)
            self._json({"ok": True, "job": target, "failure_review": target["failure_review"]})
            return

        if path == "/api/file/status":
            mod = d.get("module", "")
            file = d.get("file", "")
            status = d.get("status", "draft")
            allowed = ("draft", "review", "active", "baseline", "maintenance", "blocked", "deprecated")
            if status not in allowed:
                self._json({"ok": False, "error": "非法用例状态"}, 400)
                return
            if not mod or not file:
                self._json({"ok": False, "error": "module 和 file 不能为空"}, 400)
                return
            row = update_task_meta(mod, file, {
                "status": status,
                "status_note": d.get("note", ""),
                "status_updated_by": d.get("user") or "manual"
            })
            self._json({"ok": True, "meta": row})
            return

        if path == "/api/file/repair-latest":
            mod = d.get("module", "")
            file = d.get("file", "")
            if not mod or not file:
                self._json({"ok": False, "error": "module 和 file 不能为空"}, 400)
                return
            try:
                result = repair_file_latest_result(d)
                self._json(result)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
            return

        if path == "/api/file/repair-task-latest":
            mod = d.get("module", "")
            file = d.get("file", "")
            task_name = d.get("taskName", "")
            if not mod or not file or not task_name:
                self._json({"ok": False, "error": "module、file 和 taskName 不能为空"}, 400)
                return
            try:
                result = repair_task_latest_result(d)
                self._json(result)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
            return

        if path == "/api/module":
            name = d.get("name", "")
            if not name:
                self._json({"ok": False, "error": "模块名称不能为空"}, 400)
                return
            try:
                os.makedirs(safe_join(TASK_DIR, name), exist_ok=True)
            except ValueError:
                self._json({"ok": False, "error": "非法路径"}, 400)
                return
            self._json({"ok": True})
            return

        if path == "/api/task-app":
            try:
                app = resolve_task_app_sonic_binding(normalize_task_app(d))
                data = load_task_apps()
                apps = [item for item in data.get("apps", []) if item.get("package") != app["package"]]
                apps.append(app)
                data["apps"] = sorted(apps, key=lambda item: item.get("name") or item.get("package") or "")
                save_task_apps(data)
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 400)
                return
            self._json({"ok": True, "app": app})
            return

        if path == "/api/sonic/diagnose":
            try:
                projects = sonic_list_projects()
                apps = sonic_notify_known_apps()
                matched = []
                probe_project_id = safe_int(d.get("project_id") or d.get("projectId"), 0)
                for app in apps:
                    project_id = sonic_project_id_for_app(app)
                    project_name = sonic_project_name_for_app(app)
                    found = None
                    for project in projects:
                        if project_id and safe_int(project.get("id"), 0) == project_id:
                            found = project
                            break
                        name = project.get("projectName") or project.get("name") or ""
                        if project_name and name == project_name:
                            found = project
                            break
                    suite_binding = {
                        "configured": bool(sonic_suite_id_for_app(app) or sonic_suite_name_for_app(app)),
                        "matched": False,
                        "id": sonic_suite_id_for_app(app),
                        "name": sonic_suite_name_for_app(app),
                        "case_count": 0,
                        "error": "",
                    }
                    if found and suite_binding["configured"]:
                        try:
                            bound = resolve_task_app_sonic_binding(app)
                            suite_binding.update({
                                "matched": bool(sonic_suite_id_for_app(bound)),
                                "id": sonic_suite_id_for_app(bound),
                                "name": sonic_suite_name_for_app(bound),
                                "case_count": safe_int(bound.get("sonic_suite_case_count"), 0),
                            })
                        except Exception as e:
                            suite_binding["error"] = str(e)
                    matched.append({
                        "package": app.get("package"),
                        "name": app.get("name"),
                        "sonic_project_id": project_id,
                        "sonic_project_name": project_name,
                        "matched": bool(found),
                        "project": found or None,
                        "suite": suite_binding,
                    })
                    if not probe_project_id and found:
                        probe_project_id = safe_int(found.get("id"), 0)
                probes = [
                    sonic_probe_endpoint("/projects/list"),
                    sonic_probe_token(),
                ]
                if probe_project_id:
                    probes.extend([
                        sonic_probe_endpoint("/modules/list", {"projectId": probe_project_id}),
                        sonic_probe_endpoint("/testCases/list", {"projectId": probe_project_id, "platform": 1, "name": "", "page": 1, "pageSize": 5, "editTimeSort": "desc"}),
                        sonic_probe_endpoint("/testSuites/listAll", {"projectId": probe_project_id}),
                        sonic_probe_endpoint("/testSuites/list", {"projectId": probe_project_id, "name": "", "page": 1, "pageSize": 5}),
                        sonic_probe_endpoint("/results/list", {"projectId": probe_project_id, "page": 1, "pageSize": 15}),
                    ])
                recommendations = []
                token_probe = next((item for item in probes if item.get("path") == "/users"), {})
                if not token_probe.get("ok"):
                    auth = sonic_auth_preview()
                    if auth.get("login_configured") and auth.get("login_error"):
                        recommendations.append("账号密码已被服务进程读取，但自动登录失败：" + re.sub(r"\s+", " ", auth.get("login_error", ""))[:180] + "；请检查 Sonic 登录网关状态后重试。")
                    elif auth.get("login_configured"):
                        recommendations.append("账号密码自动登录已配置，但 Token 未通过 Sonic 校验；请检查登录账号状态并重新诊断。")
                    else:
                        recommendations.append("服务进程未读取到 SONIC_USERNAME/SONIC_PASSWORD；配置后重启 Python 服务再重新诊断。")
                permission_paths = [item.get("path") for item in probes if item.get("auth_status") == "permission_denied"]
                if permission_paths:
                    recommendations.append("当前 Sonic 账号缺少资源权限：" + "、".join(permission_paths) + "；请在 Sonic 角色资源中授权这些接口。")
                unmatched_apps = [item.get("name") or item.get("package") for item in matched if not item.get("matched")]
                if unmatched_apps:
                    recommendations.append("这些应用尚未匹配 Sonic 项目：" + "、".join(unmatched_apps) + "；填写项目名称后保存。")
                unbound_suites = [item.get("name") or item.get("package") for item in matched if item.get("matched") and not (item.get("suite") or {}).get("configured")]
                if unbound_suites:
                    recommendations.append("这些应用未绑定测试套：" + "、".join(unbound_suites) + "；填写测试套名称后保存，平台会自动回填 ID 并同步用例。")
                invalid_suites = [item.get("name") or item.get("package") for item in matched if (item.get("suite") or {}).get("error")]
                if invalid_suites:
                    recommendations.append("这些应用的测试套绑定无效：" + "、".join(invalid_suites) + "；请重新选择对应项目内的测试套。")
                missing_webhooks = []
                invalid_webhooks = []
                for item in matched:
                    app_name = item.get("name") or item.get("package")
                    try:
                        if not task_app_feishu_webhook(sonic_suite_app_info(item.get("package") or "", "")):
                            missing_webhooks.append(app_name)
                    except ValueError:
                        invalid_webhooks.append(app_name)
                if missing_webhooks:
                    recommendations.append("这些应用未配置飞书汇总群：" + "、".join(missing_webhooks) + "；请在应用分组中填写 Webhook，避免跨应用误发。")
                if invalid_webhooks:
                    recommendations.append("这些应用的飞书 Webhook 格式无效：" + "、".join(invalid_webhooks) + "；只填写单行机器人地址，不要粘贴 export 命令或中文引号。")
                if not recommendations:
                    recommendations.append("Sonic 接入检查通过；同步到 Sonic 后，新用例会自动加入绑定测试套并按整套汇总通知。")
                self._json({
                    "ok": True,
                    "base_url": sonic_base_url(),
                    "token_configured": bool(sonic_token()),
                    "token_source": sonic_token_source(),
                    "token_fingerprint": sonic_token_fingerprint(),
                    "auth": sonic_auth_preview(),
                    "login_configured": sonic_auth_preview()["login_configured"],
                    "source_findings": [
                        "Sonic 2.7.2 Gateway 读取请求头 SonicToken；/projects/list 是网关白名单，成功不代表 token 已通过鉴权。",
                        "Controller PermissionFilter 会按 servletPath + HTTP method 校验角色资源权限，例如 GET /results/list。",
                        "Sonic 2.7.2 登录接口为 POST /users/login，Task 平台可通过 SONIC_USERNAME/SONIC_PASSWORD 自动刷新 SonicToken。",
                        "测试套执行总数来自 sendMsgCount，只有 receiveMsgCount 达到 sendMsgCount 才按执行完成汇总。",
                    ],
                    "projects": projects,
                    "apps": matched,
                    "probe_project_id": probe_project_id,
                    "probes": probes,
                    "recommendations": recommendations,
                })
            except Exception as e:
                self._json({
                    "ok": False,
                    "error": str(e),
                    "base_url": sonic_base_url(),
                    "token_configured": bool(sonic_token()),
                    "token_source": sonic_token_source(),
                    "token_fingerprint": sonic_token_fingerprint(),
                    "auth": sonic_auth_preview(),
                    "login_configured": sonic_auth_preview()["login_configured"],
                    "probes": [sonic_probe_endpoint("/projects/list")] if sonic_token() else []
                }, 500)
            return

        if path == "/api/sonic/scan-legacy":
            try:
                rows = sonic_scan_midscene_cases(
                    app_package=d.get("app_package") or d.get("appPackage") or "",
                    module=d.get("module", ""),
                    file=clean_filename(d.get("file", "")) if d.get("file") else "",
                    include_current=safe_bool(d.get("includeCurrent") or d.get("include_current"))
                )
                self._json({
                    "ok": True,
                    "total": len(rows),
                    "migratable": len([row for row in rows if row.get("action") == "migrate"]),
                    "manual": len([row for row in rows if row.get("action") == "manual"]),
                    "current": len([row for row in rows if row.get("step_state") == "bridge"]),
                    "legacy": len([row for row in rows if row.get("step_state") == "legacy"]),
                    "mixed": len([row for row in rows if row.get("step_state") == "mixed"]),
                    "rows": rows
                })
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
            return

        if path == "/api/sonic/migrate-legacy":
            try:
                self._json(sonic_migrate_midscene_cases(d))
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
            return

        if path == "/api/sonic/publish-check":
            try:
                result = sonic_publish_precheck(d)
                self._json(result)
            except Exception as e:
                self._json({"ok": False, "error": str(e), "canPublish": False, "blockers": [str(e)]}, 500)
            return

        if path == "/api/sonic/publish-batch":
            try:
                self._json(sonic_publish_batch(d))
            except Exception as e:
                self._json({"ok": False, "error": str(e), "results": []}, 500)
            return

        if path == "/api/sonic/publish":
            case_id = d.get("case_id") or d.get("caseId") or ""
            try:
                result = sonic_publish_yaml(d)
                self._json(result, 200 if result.get("ok") else 400)
            except Exception as e:
                if case_id:
                    with SONIC_LOCK:
                        sync = load_sonic_sync()
                        row = sync.setdefault("cases", {}).get(case_id, {"case_id": case_id, "module": d.get("module", ""), "file": d.get("file", ""), "task_name": d.get("taskName") or d.get("task_name") or ""})
                        row.update({"status": "failed", "last_error": str(e), "updated_at": time.strftime("%Y-%m-%d %H:%M:%S")})
                        sync["cases"][case_id] = row
                        save_sonic_sync(sync)
                self._json({"ok": False, "error": str(e)}, 500)
            return

        if path == "/api/file/op":
            op = d.get("op") or "copy"
            move = op in ("move", "rename")
            src_module = d.get("module", "")
            src_file = d.get("file", "")
            dst_module = d.get("targetModule") or d.get("target_module") or src_module
            dst_file = d.get("targetFile") or d.get("target_file") or src_file
            overwrite = safe_bool(d.get("overwrite"))
            if not src_module or not src_file or not dst_module or not dst_file:
                self._json({"ok": False, "error": "源模块、源文件、目标模块、目标文件不能为空"}, 400)
                return
            try:
                final_file = copy_or_move_task_file(src_module, src_file, dst_module, dst_file, move=move, overwrite=overwrite)
            except FileNotFoundError as e:
                self._json({"ok": False, "error": str(e)}, 404)
                return
            except FileExistsError as e:
                self._json({"ok": False, "error": str(e)}, 409)
                return
            except ValueError:
                self._json({"ok": False, "error": "非法路径"}, 400)
                return
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
                return
            self._json({
                "ok": True,
                "op": op,
                "module": dst_module,
                "file": final_file
            })
            return

        if path == "/api/files/op":
            op = d.get("op") or "move"
            if op not in ("move", "copy"):
                self._json({"ok": False, "error": "批量操作只支持 move/copy"}, 400)
                return
            items = d.get("items") or []
            dst_module = d.get("targetModule") or d.get("target_module") or ""
            overwrite = safe_bool(d.get("overwrite"))
            if not items or not dst_module:
                self._json({"ok": False, "error": "请选择文件和目标模块"}, 400)
                return
            results = []
            errors = []
            for item in items:
                src_module = item.get("module", "")
                src_file = item.get("file", "")
                try:
                    final_file = copy_or_move_task_file(src_module, src_file, dst_module, src_file, move=(op == "move"), overwrite=overwrite)
                    results.append({"module": src_module, "file": src_file, "targetModule": dst_module, "targetFile": final_file})
                except Exception as e:
                    errors.append({"module": src_module, "file": src_file, "error": str(e)})
            self._json({
                "ok": len(errors) == 0,
                "error": f"{len(errors)} 个文件操作失败" if errors else "",
                "results": results,
                "errors": errors
            }, 207 if errors else 200)
            return

        if path == "/api/file/restore":
            mod = d.get("module", "")
            file = d.get("file", "")
            version_id = d.get("version") or d.get("id")
            if not mod or not file or not version_id:
                self._json({"ok": False, "error": "module、file 和 version 不能为空"}, 400)
                return
            try:
                meta, content = read_file_version(mod, file, version_id)
                fpath = safe_join(TASK_DIR, mod, clean_filename(file))
                if os.path.exists(fpath):
                    save_file_version(mod, file, reason="before_restore")
                write_text_file(fpath, content)
            except FileNotFoundError:
                self._json({"ok": False, "error": "版本不存在"}, 404)
                return
            except ValueError:
                self._json({"ok": False, "error": "非法路径"}, 400)
                return
            self._json({"ok": True, "version": meta})
            return

        if path == "/api/file":
            mod = d.get("module", "")
            file = clean_filename(d.get("file", ""))
            content = d.get("content", "")
            try:
                module_dir = safe_join(TASK_DIR, mod)
                os.makedirs(module_dir, exist_ok=True)
                fpath = safe_join(module_dir, file)
                if os.path.exists(fpath):
                    save_file_version(mod, file, reason=d.get("reason") or "save")
                write_text_file(fpath, content)
            except ValueError:
                self._json({"ok": False, "error": "非法路径"}, 400)
                return
            self._json({"ok": True})
            return

        # ===== Agent Run POST endpoints =====
        if path == "/api/agent-runs/start":
            with AGENT_RUN_LOCK:
                run = create_agent_run(d)
                run = advance_agent_run(run)
                runs = load_agent_runs()
                runs.insert(0, run)
                runs = runs[:50]
                save_agent_runs(runs)
            self._json({"ok": True, "run": run})
            return

        if path == "/api/agent-runs/preview":
            goal = str(d.get("target") or d.get("goal") or "").strip()
            app_name = str(d.get("appName") or "").strip() or "智小白3D APP"
            platform = str(d.get("platform") or "android").strip()
            scope = str(d.get("scope") or "smoke").strip()
            mode = str(d.get("mode") or "AUTO_SAFE").upper()
            risk_hits = [kw for kw in AGENT_RISK_KEYWORDS if kw in goal]
            self._json({
                "ok": True,
                "plan": {
                    "mode": mode,
                    "appName": app_name,
                    "platform": platform,
                    "scope": scope,
                    "riskHits": risk_hits,
                    "steps": [
                        "1. 分析测试目标",
                        "2. 匹配已有用例或生成新用例",
                        "3. 生成并校验 Midscene YAML",
                        "4. 同步 Sonic 并执行测试",
                        "5. 收集报告并分析失败",
                        "6. SCRIPT_ISSUE 生成修复草稿；PRODUCT_BUG 生成缺陷草稿",
                        "7. 高风险动作进入 WAIT_CONFIRM",
                        "8. 生成总结报告"
                    ]
                }
            })
            return

        m_confirm = re.match(r"^/api/agent-runs/([^/]+)/confirm$", path)
        if m_confirm:
            run_id = urllib.parse.unquote(m_confirm.group(1))
            with AGENT_RUN_LOCK:
                runs = load_agent_runs()
                run = next((r for r in runs if r.get("runId") == run_id), None)
                if not run:
                    self._json({"ok": False, "error": "Agent Run 不存在"}, 404)
                    return
                now = time.strftime("%Y-%m-%dT%H:%M:%S")
                confirm_id = d.get("confirmationId") or d.get("id") or ""
                action = d.get("action") or d.get("decision") or "confirmed"
                # Mark pending confirmations as resolved
                for pc in run.get("pendingConfirmations", []):
                    if not confirm_id or pc.get("id") == confirm_id:
                        pc["decision"] = action
                        pc["decidedAt"] = now
                run["pendingConfirmations"] = []
                run["status"] = "RUNNING"
                run["updatedAt"] = now
                save_agent_runs(runs)
            worker = threading.Thread(target=_execute_agent_steps, args=(run_id,), daemon=True)
            worker.start()
            self._json({"ok": True, "run": run})
            return

        m_cancel = re.match(r"^/api/agent-runs/([^/]+)/cancel$", path)
        if m_cancel:
            run_id = urllib.parse.unquote(m_cancel.group(1))
            with AGENT_RUN_LOCK:
                runs = load_agent_runs()
                run = next((r for r in runs if r.get("runId") == run_id), None)
                if not run:
                    self._json({"ok": False, "error": "Agent Run 不存在"}, 404)
                    return
                now = time.strftime("%Y-%m-%dT%H:%M:%S")
                run["status"] = "CANCELLED"
                run["currentStep"] = "FAILED"
                run["pendingConfirmations"] = []
                reason = d.get("reason") or "用户取消"
                run["error"] = reason
                run["updatedAt"] = now
                save_agent_runs(runs)
            self._json({"ok": True, "run": run})
            return

        self._text("Not Found", 404)

    def _do_DELETE(self):
        qs, path = self._qs()

        if path.startswith("/api/") and not self._authorized():
            self._json({"ok": False, "error": "Unauthorized"}, 401)
            return

        if path == "/api/file":
            try:
                fpath = safe_join(TASK_DIR, qs.get("module", ""), qs.get("file", ""))
                if os.path.exists(fpath):
                    os.remove(fpath)
            except ValueError:
                self._json({"ok": False, "error": "非法路径"}, 400)
                return
            self._json({"ok": True})
            return

        if path == "/api/module":
            try:
                mp = safe_join(TASK_DIR, qs.get("module", ""))
                if os.path.exists(mp):
                    shutil.rmtree(mp)
            except ValueError:
                self._json({"ok": False, "error": "非法路径"}, 400)
                return
            self._json({"ok": True})
            return

        if path == "/api/task-app":
            package = qs.get("package") or qs.get("app_package") or qs.get("appPackage")
            if not package:
                self._json({"ok": False, "error": "包名不能为空"}, 400)
                return
            data = load_task_apps()
            data["apps"] = [item for item in data.get("apps", []) if item.get("package") != package]
            save_task_apps(data)
            self._json({"ok": True})
            return

        if path == "/api/knowledge/page":
            app_package = qs.get("app_package") or qs.get("appPackage") or os.getenv("APP_PACKAGE", DEFAULT_APP_PACKAGE)
            page_id = qs.get("page_id") or qs.get("pageId")
            if not page_id:
                self._json({"ok": False, "error": "page_id 不能为空"}, 400)
                return
            try:
                page_dir = knowledge_page_dir(app_package, page_id)
                if os.path.exists(page_dir):
                    shutil.rmtree(page_dir)
            except ValueError:
                self._json({"ok": False, "error": "非法路径"}, 400)
                return
            self._json({"ok": True})
            return

        if path == "/api/cases/mindmap":
            case_set_id = qs.get("case_set_id") or qs.get("id")
            if not case_set_id:
                self._json({"ok": False, "error": "case_set_id 不能为空"}, 400)
                return
            try:
                mm_path = generation_mindmap_path(case_set_id)
                existed = os.path.exists(mm_path)
                if existed:
                    os.remove(mm_path)
                os.makedirs(os.path.dirname(mm_path), exist_ok=True)
                write_text_file(generation_mindmap_deleted_path(case_set_id), time.strftime("%Y-%m-%d %H:%M:%S"))
            except ValueError:
                self._json({"ok": False, "error": "非法路径"}, 400)
                return
            self._json({"ok": True, "deleted": existed, "mindmap_deleted": True})
            return

        if path == "/api/cases/ui-design":
            case_set_id = qs.get("case_set_id") or qs.get("id")
            asset_id = qs.get("asset_id") or qs.get("assetId") or ""
            filename = qs.get("filename") or ""
            if not case_set_id or not (asset_id or filename):
                self._json({"ok": False, "error": "case_set_id 和 asset_id 不能为空"}, 400)
                return
            try:
                deleted, meta = delete_case_ui_design_asset(case_set_id, asset_id=asset_id, filename=filename)
                summary = read_json_file(generation_summary_path(case_set_id), default=None)
                if summary:
                    summary["ui_design_assets"] = meta.get("designs") or []
                    write_generation_summary(case_set_id, summary)
            except ValueError:
                self._json({"ok": False, "error": "非法路径"}, 400)
                return
            except Exception as e:
                self._json({"ok": False, "error": str(e)}, 500)
                return
            self._json({"ok": True, "deleted": deleted, "ui_designs": meta})
            return

        if path == "/api/knowledge/app":
            app_package = qs.get("app_package") or qs.get("appPackage")
            if not app_package:
                self._json({"ok": False, "error": "app_package 不能为空"}, 400)
                return
            try:
                app_dir = knowledge_app_dir(app_package)
                if os.path.exists(app_dir):
                    shutil.rmtree(app_dir)
            except ValueError:
                self._json({"ok": False, "error": "非法路径"}, 400)
                return
            self._json({"ok": True})
            return

        self._text("Not Found", 404)


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


if __name__ == "__main__":
    validate_runtime_secrets()
    os.makedirs(TASK_DIR, exist_ok=True)
    os.makedirs(REPORT_DIR, exist_ok=True)
    os.makedirs(LEARNING_DIR, exist_ok=True)
    os.makedirs(ASSET_DIR, exist_ok=True)
    os.makedirs(CASE_DIR, exist_ok=True)
    os.makedirs(GENERATE_JOB_DIR, exist_ok=True)
    os.makedirs(KNOWLEDGE_DIR, exist_ok=True)
    restore_pending_sonic_suite_summary_timers()
    start_report_cleanup_scheduler()
    print(f"MidScene task server with queue running on port {PORT}")
    ThreadedHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
