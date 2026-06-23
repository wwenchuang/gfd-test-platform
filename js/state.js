// state.js
// Extracted from task-manager.html (no logic changes).

// === Unified front-end state object (new in refactor round 3) ===
const AppState = {
  activeSection: 'agent',
  activeSubPage: 'workbench',
  user: null,
  modules: {},
  jobs: [],
  agentRuns: [],
  currentAgentRun: null,
  repairDrafts: [],
  modelProviders: [],
  modelRouter: {},
  reports: [],
  runners: [],
  loading: {},
  errors: {},
  // round 4: lazy-load cache flags so each section only fetches on demand
  loaded: {
    modules: false,
    jobs: false,
    reports: false,
    modelConfig: false,
    sonicCases: false,
    runners: false,
    agentRuns: false
  },
  // round 4: polling timers driven by current section
  polling: {
    agentStatus: null,
    jobs: null
  }
};

// ===== STATE =====
let currentModule = null;
let currentFile = null;
let editorDirty = false;
let editorInitialContent = '';
let modules = {};
let uploadFileContent = '';
let uploadFileName = '';
let generateAssetFiles = [];
let mindmapAssetFiles = [];
let mindmapBusy = false;
let generateBusy = false;
let generateProgressTimer = null;
let generateProgressDelayTimer = null;
let generateAppInputTimer = null;
let jobsRefreshTimer = null;
let mindmapCenterRefreshTimer = null;
let mindmapCenterRecordCaseSetIds = new Set();
let mindmapCenterTaskJobs = [];
let latestJobs = [];
let taskApps = [];
let taskMeta = {};
let knowledgeScreenshot = null;
let knowledgePages = [];
let knowledgeApps = [];
let knowledgeAppDetails = [];
let figmaDrafts = [];
let figmaParsedUrl = '';
let managerKnowledgePages = [];
let managerKnowledgeAppsLoaded = false;
let recentAppPackages = JSON.parse(localStorage.getItem('midscene_recent_apps') || '[]');
let generateKnowledgePages = [];
let runnerDevices = [];
let selectedFiles = new Set();
let baselineRefPages = [];
let baselinePreviewTimer = null;
let baselinePreviewTaskName = '';
let baselinePreviewData = null;
let sonicStatusData = null;
let sonicCaseRows = [];
let lastSonicScanPayload = null;
let activeWorkflow = 'dashboard';
let activeWorkspaceMode = '';
let modulesLoaded = false;
let libraryView = 'module';
let expandedJobs = new Set();
let yamlStatsCache = {};
let yamlStatsWarmupStarted = false;
let modulePriorityFilter = 'all';
let assetListPage = 1;
let moduleDirectoryPage = 1;
let lastAssetFilterKey = '';
let agentCurrentRun = null;
let agentRuns = [];
let agentBusy = false;
let agentActiveTab = 'cases';
let agentSourceFiles = [];
let agentRefreshTimer = null;
let aiFailureDraft = null;
let selectedRepairJobId = '';
let repairDrafts = [];
let aiProviders = [];
let aiModelRouter = {};
const layoutPrefs = JSON.parse(localStorage.getItem('midscene_layout_prefs') || '{}');

const AGENT_RISK_KEYWORDS = ['确认打印', '开始打印', '支付', '删除', '覆盖基线', '格式化', '清空', '解绑', '重置'];
const ASSET_PAGE_SIZE = 20;
const MODULE_DIRECTORY_PAGE_SIZE = 24;
const AUTO_AGENT_STEPS = [
  'IDLE','PLAN','PREPARE_SOURCE','IMPACT_ANALYSIS','CASE_RETRIEVAL','MATCH_CASES',
  'GENERATE_YAML','VALIDATE_YAML','RISK_REVIEW','EXECUTION_PRECHECK',
  'SYNC_SONIC','RUN_SONIC','COLLECT_REPORT','ANALYZE_FAILURE','DIAGNOSE_FAILURE',
  'GENERATE_REPAIR','APPLY_SAFE_REPAIR','RERUN','LEARN_FROM_RESULT',
  'GENERATE_SUMMARY','GENERATE_BUG_DRAFT','DONE','FAILED','WAIT_CONFIRM'
];
const AUTO_AGENT_STEP_LABELS = {
  IDLE: '空闲',
  PLAN: '计划中',
  PREPARE_SOURCE: '整理输入',
  IMPACT_ANALYSIS: '影响分析',
  CASE_RETRIEVAL: '检索用例',
  MATCH_CASES: '匹配用例',
  GENERATE_YAML: '生成 YAML',
  VALIDATE_YAML: '校验 YAML',
  RISK_REVIEW: '风险判断',
  EXECUTION_PRECHECK: '执行前体检',
  SYNC_SONIC: '同步至 Sonic 平台',
  RUN_SONIC: '执行中',
  COLLECT_REPORT: '收集报告',
  ANALYZE_FAILURE: '失败分析',
  DIAGNOSE_FAILURE: '失败诊断',
  GENERATE_REPAIR: '生成修复',
  APPLY_SAFE_REPAIR: '自动重跑',
  RERUN: '安全重跑',
  LEARN_FROM_RESULT: '沉淀学习',
  GENERATE_SUMMARY: '生成总结',
  GENERATE_BUG_DRAFT: '缺陷草稿',
  DONE: '完成',
  FAILED: '失败',
  CANCELLED: '已取消',
  WAIT_CONFIRM: '待确认'
};
const AGENT_RISK_LEVELS = { LOW: 'low', MEDIUM: 'medium', HIGH: 'high' };
const AGENT_HIGH_RISK_KEYWORDS = ['支付', '删除', '覆盖基线', '格式化', '清空', '解绑', '重置'];
const AGENT_MEDIUM_RISK_KEYWORDS = ['确认打印', '开始打印', '打印流程', '设备连接', '文件上传'];

function classifyRiskLevel(goal) {
  const text = String(goal || '');
  if (AGENT_HIGH_RISK_KEYWORDS.some(kw => text.includes(kw))) return AGENT_RISK_LEVELS.HIGH;
  if (AGENT_MEDIUM_RISK_KEYWORDS.some(kw => text.includes(kw))) return AGENT_RISK_LEVELS.MEDIUM;
  return AGENT_RISK_LEVELS.LOW;
}

// Legacy aliases
const AGENT_STEPS = AUTO_AGENT_STEPS;
const AGENT_STEP_LABELS = AUTO_AGENT_STEP_LABELS;
const AGENT_MODE_LABELS = {
  AUTO_SAFE: '安全自动',
  FULL_AUTO: '全自动',
  SEMI_AUTO: '半自动',
  ANALYZE_ONLY: '只分析'
};
const AGENT_RISK_LABELS = {
  low: '低',
  medium: '中',
  high: '高',
  LOW: '低',
  MEDIUM: '中',
  HIGH: '高'
};
const AGENT_TOOL_LABELS = {
  analyze_goal: '理解测试目标',
  prepare_source: '整理输入资料',
  impact_analysis: '影响分析',
  case_retrieval: '检索已有用例',
  list_cases: '匹配用例',
  generate_yaml: '生成 YAML 草稿',
  validate_yaml: '校验 YAML',
  risk_review: '风险判断',
  execution_precheck: '执行前体检',
  sync_sonic: '同步至 Sonic 平台',
  create_runner_job: '创建 Runner 执行任务',
  read_report: '收集执行报告',
  analyze_failure: '分析失败原因',
  diagnose_failure: '失败诊断',
  generate_repair_draft: '生成修复草稿',
  generate_bug_draft: '生成缺陷草稿',
  generate_summary: '生成总结报告',
  query_page_knowledge: '查询页面知识',
  query_failure_knowledge: '查询失败经验',
  query_case_history: '查询用例历史'
};

function agentModeText(value) {
  return AGENT_MODE_LABELS[String(value || '').toUpperCase()] || value || '-';
}

function agentRiskText(value) {
  return AGENT_RISK_LABELS[String(value || '')] || value || '低';
}

function agentStepLabel(value) {
  const key = String(value || '').toUpperCase();
  return AUTO_AGENT_STEP_LABELS[key] || AGENT_STEP_LABELS_OLD?.[key] || value || '-';
}

function agentToolNameText(value) {
  return AGENT_TOOL_LABELS[String(value || '')] || AGENT_TOOL_LABELS[String(value || '').toLowerCase()] || value || '工具调用';
}

function agentToolStatusText(value) {
  const v = String(value || '').toUpperCase();
  const map = {
    SUCCESS: '成功',
    FAILED: '失败',
    PARTIAL_FAILED: '部分失败',
    SKIPPED: '已跳过',
    RUNNING: '执行中',
    PENDING: '等待中',
    WAIT_CONFIRM: '待确认',
    DONE: '完成',
    CANCELLED: '已取消'
  };
  return map[v] || value || '';
}

function agentJobStatusText(value) {
  const v = String(value || '').toLowerCase();
  const map = {
    pending: '等待中',
    running: '执行中',
    success: '成功',
    failed: '失败',
    timeout: '超时',
    cancelled: '已取消'
  };
  return map[v] || value || '未知';
}

function failureTypeText(value) {
  const v = String(value || '').toUpperCase();
  const map = {
    SCRIPT_ISSUE: '脚本问题',
    PRODUCT_BUG: '产品缺陷',
    ENV_ISSUE: '环境问题',
    UNKNOWN: '待确认',
    NONE: '无失败'
  };
  return map[v] || value || '已分析';
}
const AGENT_STEPS_OLD = [
  'START',
  'ANALYZE_REQUIREMENT',
  'GENERATE_CASE',
  'GENERATE_YAML',
  'VALIDATE_YAML',
  'WAIT_CONFIRM_RUN',
  'RUN_TASK',
  'ANALYZE_FAILURE',
  'OPTIMIZE_YAML',
  'GENERATE_BUG_DRAFT',
  'WAIT_CONFIRM_BUG',
  'FINISH'
];
const AGENT_STEP_LABELS_OLD = {
  START: '开始',
  ANALYZE_REQUIREMENT: '需求分析',
  GENERATE_CASE: '生成用例',
  GENERATE_YAML: '生成 YAML',
  VALIDATE_YAML: '校验 YAML',
  WAIT_CONFIRM_RUN: '等待确认执行',
  RUN_TASK: '执行 Sonic',
  ANALYZE_FAILURE: '失败分析',
  OPTIMIZE_YAML: '修复脚本',
  GENERATE_BUG_DRAFT: '缺陷草稿',
  WAIT_CONFIRM_BUG: '等待确认提单',
  FINISH: '完成'
};
const MODEL_ROUTER_FIELDS = [
  ['generate_case', '生成测试用例模型'],
  ['generate_yaml', '生成 YAML 模型'],
  ['analyze_failure', '失败分析模型'],
  ['optimize_yaml', 'YAML 修复模型'],
  ['agent_plan', 'Agent 判断模型'],
  ['generate_bug', '飞书缺陷草稿模型']
];
const AGENT_ARTIFACT_TABS = [
  ['plan', '执行计划'],
  ['cases', '测试用例'],
  ['quality', '质量检查'],
  ['yaml', 'Midscene YAML'],
  ['validation', 'YAML 校验'],
  ['logs', '执行日志'],
  ['failure', '失败分析'],
  ['repair', '修复草稿'],
  ['bug', '缺陷草稿'],
  ['summary', '总结报告'],
  ['report', '最终报告']
];
