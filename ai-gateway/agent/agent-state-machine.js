export const AGENT_STATES = Object.freeze({
  START: 'START',
  ANALYZE_REQUIREMENT: 'ANALYZE_REQUIREMENT',
  GENERATE_CASE: 'GENERATE_CASE',
  GENERATE_YAML: 'GENERATE_YAML',
  VALIDATE_YAML: 'VALIDATE_YAML',
  SAVE_ASSET: 'SAVE_ASSET',
  WAIT_CONFIRM_RUN: 'WAIT_CONFIRM_RUN',
  RUN_TASK: 'RUN_TASK',
  WAIT_RESULT: 'WAIT_RESULT',
  ANALYZE_RESULT: 'ANALYZE_RESULT',
  ANALYZE_FAILURE: 'ANALYZE_FAILURE',
  OPTIMIZE_YAML: 'OPTIMIZE_YAML',
  VALIDATE_REPAIRED_YAML: 'VALIDATE_REPAIRED_YAML',
  RERUN_TASK: 'RERUN_TASK',
  GENERATE_BUG_DRAFT: 'GENERATE_BUG_DRAFT',
  WAIT_CONFIRM_BUG: 'WAIT_CONFIRM_BUG',
  GENERATE_BUG: 'GENERATE_BUG',
  WAIT_CONFIRM: 'WAIT_CONFIRM',
  CREATE_FEISHU_TICKET: 'CREATE_FEISHU_TICKET',
  GENERATE_REPORT: 'GENERATE_REPORT',
  NOTIFY_FEISHU: 'NOTIFY_FEISHU',
  FINISH: 'FINISH',
  FAILED: 'FAILED',
  CANCELLED: 'CANCELLED',
});

export const TERMINAL_STATES = new Set([
  AGENT_STATES.FINISH,
  AGENT_STATES.FAILED,
  AGENT_STATES.CANCELLED,
]);

export const DEFAULT_LIMITS = Object.freeze({
  maxRetries: 2,
  maxAutoRepair: 2,
  hardMaxRetries: 3,
});

// ── AUTO_AGENT 工作流步骤定义 ──────────────────────────────────────
export const AUTO_AGENT_STEPS = [
  'IDLE',
  'PLAN',
  'MATCH_CASES',
  'GENERATE_YAML',
  'VALIDATE_YAML',
  'RISK_REVIEW',
  'SYNC_SONIC',
  'RUN_TASK',
  'COLLECT_REPORT',
  'ANALYZE_FAILURE',
  'GENERATE_REPAIR',
  'WAIT_CONFIRM',
  'RERUN',
  'GENERATE_SUMMARY',
  'GENERATE_BUG_DRAFT',
  'DONE',
  'FAILED',
  'CANCELLED',
];

// 每步的标准数据结构
export function createStepRecord(stepName) {
  return {
    step: stepName,
    status: 'PENDING',  // PENDING | RUNNING | SUCCESS | FAILED | WAIT_CONFIRM | SKIPPED
    startedAt: null,
    endedAt: null,
    durationMs: 0,
    summary: '',
    toolCalls: [],
    artifactRefs: [],
    error: null,
  };
}

// 状态转移函数
export function getNextStep(currentStep, context) {
  // context: { success, hasFailures, needsRepair, needsConfirm, needsRerun, hasBugDraft }
  const idx = AUTO_AGENT_STEPS.indexOf(currentStep);
  if (idx === -1) return 'FAILED';

  // 跳过逻辑
  if (currentStep === 'ANALYZE_FAILURE' && !context.hasFailures) return 'GENERATE_SUMMARY';
  if (currentStep === 'GENERATE_REPAIR' && !context.needsRepair) return 'GENERATE_SUMMARY';
  if (currentStep === 'WAIT_CONFIRM' && !context.needsConfirm) return 'RERUN';
  if (currentStep === 'RERUN' && !context.needsRerun) return 'GENERATE_SUMMARY';
  if (currentStep === 'GENERATE_BUG_DRAFT' && !context.hasBugDraft) return 'DONE';

  // 正常推进
  const next = AUTO_AGENT_STEPS[idx + 1];
  return next || 'DONE';
}

// 判断是否为终态
export function isTerminalStep(step) {
  return ['DONE', 'FAILED', 'CANCELLED'].includes(step);
}

function clampNumber(value, fallback, min, max) {
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.max(min, Math.min(max, Math.floor(parsed)));
}

export function normalizeRunOptions(input = {}) {
  const maxRetries = clampNumber(
    input.maxRetries,
    DEFAULT_LIMITS.maxRetries,
    0,
    DEFAULT_LIMITS.hardMaxRetries,
  );
  return {
    mode: String(input.mode || 'SEMI_AUTO').trim().toUpperCase() || 'SEMI_AUTO',
    goal: String(input.goal || '').trim(),
    appName: String(input.appName || '').trim(),
    platform: String(input.platform || 'android').trim() || 'android',
    moduleName: String(input.moduleName || '').trim(),
    requirement: String(input.requirement || input.testCase || input.goal || '').trim(),
    testCase: String(input.testCase || input.requirement || input.goal || '').trim(),
    autoRun: Boolean(input.autoRun),
    autoRepair: Boolean(input.autoRepair),
    autoCreateBug: Boolean(input.autoCreateBug),
    autoOverwriteBaseline: Boolean(input.autoOverwriteBaseline),
    requireConfirmBeforePrint: Boolean(input.requireConfirmBeforePrint),
    maxRetries,
    maxAutoRepair: clampNumber(input.maxAutoRepair, DEFAULT_LIMITS.maxAutoRepair, 0, maxRetries),
    traceId: String(input.traceId || '').trim(),
  };
}

export function assertRunnableOptions(options) {
  if (!options.goal && !options.requirement && !options.testCase) {
    throw new Error('Agent 目标不能为空：请提供 goal、requirement 或 testCase');
  }
}
