export const AGENT_MODES = Object.freeze({
  SEMI_AUTO: 'SEMI_AUTO',
  AUTO_SAFE: 'AUTO_SAFE',
  FULL_AUTO: 'FULL_AUTO',
});

const MODE_ORDER = [AGENT_MODES.SEMI_AUTO, AGENT_MODES.AUTO_SAFE, AGENT_MODES.FULL_AUTO];

function normalizeMode(mode) {
  let value = String(mode || AGENT_MODES.AUTO_SAFE).trim().toUpperCase();
  if (value === 'AUTO_FULL') value = AGENT_MODES.FULL_AUTO;
  return MODE_ORDER.includes(value) ? value : AGENT_MODES.AUTO_SAFE;
}

function containsAny(text, keywords = []) {
  const haystack = String(text || '').toLowerCase();
  return keywords.filter(keyword => haystack.includes(String(keyword || '').toLowerCase()));
}

function taskAllowed(goal, allowedTasks = []) {
  const text = String(goal || '').trim();
  if (!text) return false;
  return allowedTasks.some(task => text.includes(String(task || '').trim()));
}

export function applyAgentPolicy(input = {}, whitelist = {}) {
  const requestedMode = normalizeMode(input.mode);
  let effectiveMode = requestedMode;
  const reasons = [];
  const searchable = [
    input.goal,
    input.requirement,
    input.testCase,
    input.yaml,
  ].filter(Boolean).join('\n');
  const blockedHits = containsAny(searchable, whitelist.blockedKeywords || []);

  if (Number(input.maxRetries ?? 2) > 3) {
    reasons.push('maxRetries 超过安全上限 3，已自动降为 3');
  }

  if (requestedMode === AGENT_MODES.FULL_AUTO) {
    if (!whitelist.fullAutoEnabled) {
      effectiveMode = AGENT_MODES.AUTO_SAFE;
      reasons.push('FULL_AUTO 未全局开启，已降级为 AUTO_SAFE');
    } else if (!taskAllowed(input.goal || input.testCase || input.requirement, whitelist.allowedTasks || [])) {
      effectiveMode = AGENT_MODES.AUTO_SAFE;
      reasons.push('FULL_AUTO 仅允许白名单任务，当前任务未命中白名单，已降级为 AUTO_SAFE');
    }
  }

  if (blockedHits.length) {
    effectiveMode = AGENT_MODES.SEMI_AUTO;
    reasons.push(`命中高风险关键词：${blockedHits.join('、')}，已强制降级为 SEMI_AUTO`);
  }

  return {
    requestedMode,
    effectiveMode,
    reasons,
    blockedHits,
    autoRun: effectiveMode !== AGENT_MODES.SEMI_AUTO && Boolean(input.autoRun ?? true),
    autoRepair: effectiveMode !== AGENT_MODES.SEMI_AUTO && Boolean(input.autoRepair),
    autoCreateBug: effectiveMode === AGENT_MODES.FULL_AUTO && Boolean(input.autoCreateBug),
    autoOverwriteBaseline: false,
    maxRetries: Math.max(0, Math.min(3, Math.floor(Number(input.maxRetries ?? 2) || 2))),
  };
}

// ── AUTO_AGENT 风险关键词 ──────────────────────────────────────────
export const AUTO_AGENT_RISK_KEYWORDS = [
  '确认打印',
  '开始打印',
  '支付',
  '删除',
  '覆盖基线',
  '格式化',
  '清空',
  '解绑',
  '重置',
  '批量同步',
  '批量执行',
];

// ── 风险等级定义 ───────────────────────────────────────────────────
export const RISK_LEVELS = {
  LOW: {
    label: 'LOW',
    description: '低风险：读取、查询、截图、文本校验、AI分析、生成草稿',
    autoSafe: true,    // AUTO_SAFE下可自动执行
    autoFull: true,    // AUTO_FULL下可自动执行
    requiresAudit: false,
  },
  MEDIUM: {
    label: 'MEDIUM',
    description: '中风险：同步单条Sonic、触发单条低风险执行、文件上传、设备连接',
    autoSafe: true,    // AUTO_SAFE下可自动执行但审计
    autoFull: true,    // AUTO_FULL下可自动执行
    requiresAudit: true,
  },
  HIGH: {
    label: 'HIGH',
    description: '高风险：支付、删除、覆盖基线、真实打印、硬件危险、批量操作',
    autoSafe: false,   // AUTO_SAFE下必须WAIT_CONFIRM
    autoFull: false,   // AUTO_FULL下仍必须人工确认
    requiresAudit: true,
  },
};

// ── 计算风险等级 ───────────────────────────────────────────────────
export function calculateRiskLevel(text, mode) {
  const hits = AUTO_AGENT_RISK_KEYWORDS.filter(kw => text.includes(kw));
  if (hits.length > 0) return { level: 'HIGH', hits };
  // 中风险判断
  const mediumKeywords = ['同步', '执行', '上传', '连接', '设备'];
  const mediumHits = mediumKeywords.filter(kw => text.includes(kw));
  if (mediumHits.length > 0) return { level: 'MEDIUM', hits: mediumHits };
  return { level: 'LOW', hits: [] };
}

// ── AUTO_SAFE 确认规则 ─────────────────────────────────────────────
export function shouldWaitConfirmAutoSafe(riskLevel, failureType) {
  if (riskLevel === 'HIGH') return true;
  if (failureType === 'PRODUCT_BUG') return true;  // 不自动修YAML
  if (failureType === 'ENV_ISSUE') return true;     // 不自动修YAML
  if (failureType === 'UNKNOWN') return true;       // 人工复核
  return false;
}

// ── AUTO_FULL 确认规则 ─────────────────────────────────────────────
export function shouldWaitConfirmAutoFull(riskLevel, failureType) {
  if (riskLevel === 'HIGH') return true;  // 高风险仍需人工
  if (failureType === 'PRODUCT_BUG') return true;
  if (failureType === 'ENV_ISSUE') return true;
  if (failureType === 'UNKNOWN') return true;
  return false;
}

// ── 判断是否允许生成YAML修复 ───────────────────────────────────────
export function canGenerateYamlRepair(failureType) {
  return failureType === 'SCRIPT_ISSUE';
}
