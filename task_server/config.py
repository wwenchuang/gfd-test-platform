"""Runtime configuration for the Midscene Task Platform.

All directory paths, environment-derived constants, file paths, locks, and
startup env-loading logic are centralised here.  Business logic and HTTP
handling must live in other modules.

Migrated from midscene-upload.py — every global constant, environment-derived
value, and configuration function has been extracted into this single module.
"""

import os
import re
import threading

# ---------------------------------------------------------------------------
# Environment file loading
# ---------------------------------------------------------------------------
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
                if any(char in value for char in "\u201c\u201d\u2018\u2019"):
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

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def env_int(name, default):
    try:
        return int(os.getenv(name, str(default)))
    except Exception:
        return default


def safe_int(value, default=0):
    """Convert *value* to int safely; return *default* on failure."""
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        return default


def safe_bool(value, default=False):
    """Convert *value* to bool safely; return *default* on failure."""
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


WEAK_SECRET_VALUES = {
    "",
    "midscene2026",
    "admin",
    "password",
    "test123",
    "change-me",
    "change-this-long-random-secret",
}


def require_secret(name, value):
    if APP_ENV == "prod" and (not value or str(value).strip() in WEAK_SECRET_VALUES):
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


# ---------------------------------------------------------------------------
# Directory constants
# ---------------------------------------------------------------------------
TASK_DIR = os.getenv("TASK_DIR", "/opt/midscene-tasks")
REPORT_DIR = os.getenv("REPORT_DIR", "/opt/midscene-reports")
LEARNING_DIR = os.getenv("LEARNING_DIR", "/opt/midscene-learning")
ASSET_DIR = os.getenv("ASSET_DIR", "/opt/midscene-assets")
CASE_DIR = os.getenv("CASE_DIR", "/opt/midscene-cases")
GENERATE_JOB_DIR = os.getenv("GENERATE_JOB_DIR", "/opt/midscene-generate-jobs")
KNOWLEDGE_DIR = os.getenv("KNOWLEDGE_DIR", "/opt/midscene-knowledge")
AI_SKILLS_DIR = os.getenv(
    "AI_SKILLS_DIR",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ai_skills"),
)

# ---------------------------------------------------------------------------
# Runtime environment constants
# ---------------------------------------------------------------------------
APP_ENV = os.getenv("TASK_APP_ENV", "prod").strip().lower()
TASK_ENABLE_DEBUG_EXECUTION = env_int("TASK_ENABLE_DEBUG_EXECUTION", 0) != 0
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
MAX_BODY_SIZE = env_int("TASK_MAX_BODY_SIZE", 300 * 1024 * 1024)
MAX_UPLOAD_BODY_SIZE = env_int("TASK_MAX_UPLOAD_BODY_SIZE", 300 * 1024 * 1024)
PORT = env_int("PORT", 8091)
JOB_TIMEOUT_SECONDS = int(os.getenv("MIDSCENE_JOB_TIMEOUT_SECONDS", "1800"))

# ---------------------------------------------------------------------------
# Default app package & AI / Figma / Runtime configuration
# ---------------------------------------------------------------------------
DEFAULT_APP_PACKAGE = os.getenv("APP_PACKAGE", "com.kfb.model")
DEFAULT_DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_TEXT_MODEL = os.getenv("DASHSCOPE_MODEL", "qwen3.6-plus")
DEFAULT_VL_MODEL = os.getenv("DASHSCOPE_VL_MODEL", "qwen3.6-plus")
DEFAULT_REPLANNING_CYCLE_LIMIT = str(env_int("MIDSCENE_REPLANNING_CYCLE_LIMIT", 8))
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
MINDMAP_VISUAL_MAX_IMAGES = max(0, env_int("MIDSCENE_MINDMAP_VISUAL_MAX_IMAGES", AI_VISION_IMAGE_LIMIT))
MINDMAP_VISUAL_BATCH_SIZE = max(1, env_int("MIDSCENE_MINDMAP_VISUAL_BATCH_SIZE", 1))
MINDMAP_VISUAL_TIMEOUT_SECONDS = max(30, env_int("MIDSCENE_MINDMAP_VISUAL_TIMEOUT_SECONDS", 90))
MINDMAP_VISUAL_TOTAL_BUDGET_SECONDS = max(
    MINDMAP_VISUAL_TIMEOUT_SECONDS,
    env_int("MIDSCENE_MINDMAP_VISUAL_TOTAL_BUDGET_SECONDS", 360)
)
YAML_VISUAL_BATCH_SIZE = max(1, env_int("MIDSCENE_YAML_VISUAL_BATCH_SIZE", 4))
YAML_VISUAL_TIMEOUT_SECONDS = max(60, env_int("MIDSCENE_YAML_VISUAL_TIMEOUT_SECONDS", 900))
YAML_VISUAL_TOTAL_BUDGET_SECONDS = max(
    YAML_VISUAL_TIMEOUT_SECONDS,
    env_int("MIDSCENE_YAML_VISUAL_TOTAL_BUDGET_SECONDS", 3600)
)
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
AI_COVERAGE_AUDITOR_TIMEOUT_SECONDS = max(30, env_int("MIDSCENE_COVERAGE_AUDITOR_TIMEOUT_SECONDS", 180))
AI_COVERAGE_REPAIR_TIMEOUT_SECONDS = max(30, env_int("MIDSCENE_COVERAGE_REPAIR_TIMEOUT_SECONDS", 180))
AI_COVERAGE_TOTAL_BUDGET_SECONDS = max(
    AI_COVERAGE_AUDITOR_TIMEOUT_SECONDS,
    env_int("MIDSCENE_COVERAGE_TOTAL_BUDGET_SECONDS", 360)
)

# ---------------------------------------------------------------------------
# Report cleanup & business configuration constants
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# File path constants
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# Knowledge sub-directory paths (for failure / case / repair history)
# ---------------------------------------------------------------------------
KNOWLEDGE_DATA_DIR = os.path.join(LEARNING_DIR, "knowledge")
FAILURE_PATTERNS_FILE = os.path.join(KNOWLEDGE_DATA_DIR, "failure-patterns.json")
CASE_HISTORY_FILE = os.path.join(KNOWLEDGE_DATA_DIR, "case-history.json")
REPAIR_HISTORY_FILE = os.path.join(KNOWLEDGE_DATA_DIR, "repair-history.json")

# ---------------------------------------------------------------------------
# Concurrency locks & counters
# ---------------------------------------------------------------------------
JOB_LOCK = threading.Lock()
GENERATE_LOCK = threading.Lock()
RUNNER_LOCK = threading.Lock()
SONIC_LOCK = threading.RLock()
AGENT_RUN_LOCK = threading.Lock()
SONIC_SUITE_LOCK = threading.RLock()
SONIC_SUITE_TIMERS = {}
ID_LOCK = threading.Lock()
ID_COUNTER = 0

# ---------------------------------------------------------------------------
# AI Gateway URL
# ---------------------------------------------------------------------------
AI_GATEWAY_URL = os.getenv("AI_GATEWAY_URL", "http://127.0.0.1:8090").rstrip("/")

# ---------------------------------------------------------------------------
# DashScope configuration functions
# ---------------------------------------------------------------------------

def dashscope_api_key(required=True):
    """Resolve the DashScope API key from environment or fallback.

    Checks DASHSCOPE_API_KEY, OPENAI_API_KEY, MIDSCENE_API_KEY, then FALLBACK_DASHSCOPE_API_KEY.
    Raises ``ValueError`` when *required* is True and no key is found.
    """
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
    """Return the DashScope-compatible base URL."""
    return (os.getenv("DASHSCOPE_BASE_URL") or os.getenv("OPENAI_BASE_URL") or os.getenv("MIDSCENE_BASE_URL") or DEFAULT_DASHSCOPE_BASE_URL).rstrip("/")


def dashscope_text_model():
    """Return the text-only model name."""
    return (os.getenv("DASHSCOPE_MODEL") or DEFAULT_TEXT_MODEL).strip()


def dashscope_vl_model():
    """Return the vision-language model name."""
    return (os.getenv("DASHSCOPE_VL_MODEL") or os.getenv("MIDSCENE_MODEL_NAME") or DEFAULT_VL_MODEL).strip()


def dashscope_model_for_images(image_assets=None):
    """Select the appropriate model based on whether images are present."""
    return dashscope_vl_model() if image_assets else dashscope_text_model()

# ---------------------------------------------------------------------------
# Sonic configuration
# ---------------------------------------------------------------------------

def sonic_base_url():
    """Return the Sonic server base URL (no trailing slash)."""
    return (os.getenv("SONIC_BASE_URL") or os.getenv("SONIC_URL") or "http://101.34.197.12:3000").rstrip("/")


def sonic_api_prefix():
    """Return the Sonic API URL prefix (no trailing slash)."""
    return os.getenv("SONIC_API_PREFIX", "/server/api/controller").rstrip("/")


SONIC_LOGIN_STATE = {
    "attempted_at": "",
    "ok": None,
    "error": "",
}

# ---------------------------------------------------------------------------
# Agent Run Storage — steps, risk keywords, tool registry
# ---------------------------------------------------------------------------

AGENT_RUN_STEPS = [
    "IDLE", "PREPARE_SOURCE", "PLAN", "IMPACT_ANALYSIS", "CASE_RETRIEVAL",
    "MATCH_CASES", "GENERATE_YAML", "VALIDATE_YAML", "RISK_REVIEW",
    "EXECUTION_PRECHECK", "SYNC_SONIC", "RUN_SONIC", "COLLECT_REPORT",
    "ANALYZE_FAILURE", "DIAGNOSE_FAILURE", "GENERATE_REPAIR",
    "APPLY_SAFE_REPAIR", "RERUN", "LEARN_FROM_RESULT",
    "GENERATE_SUMMARY", "GENERATE_BUG_DRAFT", "DONE", "FAILED", "WAIT_CONFIRM"
]

AGENT_RISK_KEYWORDS = [
    "确认打印", "开始打印", "支付", "删除", "覆盖基线",
    "格式化", "清空", "解绑", "重置", "批量同步", "批量执行"
]

AUTO_AGENT_RISK_KEYWORDS = AGENT_RISK_KEYWORDS

# ----- Agent Tool Registry -----

AGENT_TOOL_CALLS_FILE = os.path.join(LEARNING_DIR, "agent-tool-calls.json")
AGENT_TOOL_CALL_LOCK = threading.Lock()

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
    # CONFIRM_TOOLS
    "confirm_high_risk_action": {"name": "confirm_high_risk_action", "title": "确认高风险动作", "category": "CONFIRM", "riskLevel": "high", "write": True, "requiresConfirm": True},
    "confirm_apply_yaml": {"name": "confirm_apply_yaml", "title": "确认应用 YAML", "category": "CONFIRM", "riskLevel": "high", "write": True, "requiresConfirm": True},
    "confirm_rerun": {"name": "confirm_rerun", "title": "确认重新执行", "category": "CONFIRM", "riskLevel": "medium", "write": True, "requiresConfirm": True},
    "confirm_baseline_update": {"name": "confirm_baseline_update", "title": "确认覆盖基线", "category": "CONFIRM", "riskLevel": "high", "write": True, "requiresConfirm": True},
    "confirm_bug_submit": {"name": "confirm_bug_submit", "title": "确认提交缺陷", "category": "CONFIRM", "riskLevel": "medium", "write": True, "requiresConfirm": True},
}

AGENT_PERMISSION_LEVELS = {
    "READ_ONLY": {"allowed_categories": {"READ"}, "max_auto_risk": "low"},
    "AUTO_SAFE": {"allowed_categories": {"READ", "AI", "SONIC", "TASK", "CONFIRM"}, "max_auto_risk": "medium"},
    "FULL_AUTO": {"allowed_categories": {"READ", "AI", "SONIC", "TASK", "CONFIRM"}, "max_auto_risk": "medium"},
}

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}

# ---------------------------------------------------------------------------
# APP name -> directory-keyword mapping (for filtering case dirs by app)
# ---------------------------------------------------------------------------

APP_DIR_KEYWORDS = {
    "智小白3D": ["3D打印基线", "3D打印"],
    "小白学习": ["小白学习"],
}
