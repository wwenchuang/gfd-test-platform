"""共享业务常量和枚举定义。

从 midscene-upload.py 迁移，供 task_server 各模块及前端统一引用。
本文件不引入任何外部依赖。
"""

# ----- Job 状态 -----
JOB_STATUS_PENDING = "pending"
JOB_STATUS_DISPATCHED = "dispatched"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_REPORT_UPLOADING = "report_uploading"
JOB_STATUS_ANALYZING = "analyzing"
JOB_STATUS_REPAIR_DRAFTED = "repair_drafted"
JOB_STATUS_WAIT_CONFIRM = "wait_confirm"
JOB_STATUS_SUCCESS = "success"
JOB_STATUS_FAILED = "failed"
JOB_STATUS_CANCELLED = "cancelled"
JOB_STATUS_TIMEOUT = "timeout"

ALL_JOB_STATUSES = {
    JOB_STATUS_PENDING, JOB_STATUS_DISPATCHED, JOB_STATUS_RUNNING,
    JOB_STATUS_REPORT_UPLOADING, JOB_STATUS_ANALYZING,
    JOB_STATUS_REPAIR_DRAFTED, JOB_STATUS_WAIT_CONFIRM,
    JOB_STATUS_SUCCESS, JOB_STATUS_FAILED, JOB_STATUS_CANCELLED,
    JOB_STATUS_TIMEOUT,
}

TERMINAL_JOB_STATUSES = {
    JOB_STATUS_SUCCESS, JOB_STATUS_FAILED,
    JOB_STATUS_CANCELLED, JOB_STATUS_TIMEOUT,
}

# ----- 故障类型 -----
FAILURE_TYPE_SCRIPT_ISSUE = "SCRIPT_ISSUE"
FAILURE_TYPE_PRODUCT_BUG = "PRODUCT_BUG"
FAILURE_TYPE_ENV_ISSUE = "ENV_ISSUE"
FAILURE_TYPE_UNKNOWN = "UNKNOWN"

REPAIRABLE_FAILURE_TYPES = {FAILURE_TYPE_SCRIPT_ISSUE}
NON_REPAIRABLE_FAILURE_TYPES = {
    FAILURE_TYPE_PRODUCT_BUG, FAILURE_TYPE_ENV_ISSUE, FAILURE_TYPE_UNKNOWN,
}

# ----- Midscene YAML flow 动作白名单 -----
# 源自 midscene-upload.py SUPPORTED_FLOW_ITEMS
MIDSCENE_FLOW_ACTIONS = {
    "ai", "aiAct", "aiAction", "aiTap", "aiHover", "aiInput", "aiKeyboardPress",
    "aiScroll", "aiAssert", "aiWaitFor", "aiQuery", "aiAsk", "aiBoolean",
    "aiNumber", "aiString", "sleep", "launch", "terminate", "javascript",
    "recordToReport", "runAdbShell", "runWdaRequest",
}

# AI-only 子集（不含 sleep/launch/terminate 等基础设施动作）
MIDSCENE_AI_ACTIONS = {
    "ai", "aiAct", "aiAction", "aiTap", "aiHover", "aiInput", "aiKeyboardPress",
    "aiScroll", "aiAssert", "aiWaitFor", "aiQuery", "aiAsk", "aiBoolean",
    "aiNumber", "aiString",
}

# ----- 高风险关键词 -----
# 源自 midscene-upload.py AGENT_RISK_KEYWORDS
HIGH_RISK_KEYWORDS = [
    "确认打印", "开始打印", "支付", "删除", "覆盖基线",
    "格式化", "清空", "解绑", "重置", "批量同步", "批量执行",
]

# ----- Agent 状态机步骤 -----
# 源自 midscene-upload.py AGENT_STATE_STEPS
AGENT_STATE_STEPS = [
    "IDLE", "PLAN", "PREPARE_SOURCE", "IMPACT_ANALYSIS", "CASE_RETRIEVAL", "MATCH_CASES", "GENERATE_YAML", "VALIDATE_YAML",
    "RISK_REVIEW", "EXECUTION_PRECHECK", "SYNC_SONIC", "RUN_SONIC", "COLLECT_REPORT", "ANALYZE_FAILURE",
    "DIAGNOSE_FAILURE", "GENERATE_REPAIR", "APPLY_SAFE_REPAIR", "RERUN", "LEARN_FROM_RESULT",
    "GENERATE_SUMMARY", "GENERATE_BUG_DRAFT", "DONE", "FAILED", "WAIT_CONFIRM",
]

# ----- YAML 结构校验辅助 -----
# 源自 midscene-upload.py TASK_LEVEL_ALLOWED_KEYS
TASK_LEVEL_ALLOWED_KEYS = {
    "name", "flow", "continueOnError", "description", "tags", "priority", "data",
    "timeout", "retry", "skip", "only", "env", "variables",
}

# 源自 midscene-upload.py FLOW_CHILD_KEYS
FLOW_CHILD_KEYS = {
    "locate", "prompt", "value", "timeout", "errorMessage", "name", "keyName",
    "direction", "scrollType", "distance", "deepThink", "xpath", "cacheable",
    "autoDismissKeyboard", "mode", "method", "endpoint", "data", "content",
    "title", "duration", "target", "query", "schema",
}

# 源自 midscene-upload.py PROMPT_STYLE_FLOW_ITEMS
PROMPT_STYLE_FLOW_ITEMS = {
    "ai", "aiAct", "aiAction", "aiTap", "aiHover", "aiAssert", "aiWaitFor",
    "aiQuery", "aiAsk", "aiBoolean", "aiNumber", "aiString", "aiInput",
    "aiKeyboardPress", "aiScroll",
}
