import {addStep, createRun, getRun, listRuns, updateRun} from './agent-memory.js';
import {appendAgentLog, preview} from './agent-logger.js';
import {applyAgentPolicy, AGENT_MODES} from './agent-policy.js';
import {createAgentTools} from './agent-tools.js';
import {AGENT_STATES, assertRunnableOptions, normalizeRunOptions, TERMINAL_STATES} from './agent-state-machine.js';

async function recordToolStep(run, state, toolName, input, handler) {
  const startedAt = Date.now();
  let output = null;
  let error = '';
  try {
    output = await handler();
    const durationMs = Date.now() - startedAt;
    addStep(run.runId, {
      state,
      tool: toolName,
      success: true,
      durationMs,
      inputPreview: preview(input),
      outputPreview: preview(output),
    });
    await appendAgentLog({
      runId: run.runId,
      traceId: run.traceId,
      state,
      tool: toolName,
      success: true,
      durationMs,
      input,
      output,
    });
    return output;
  } catch (err) {
    const durationMs = Date.now() - startedAt;
    error = String(err?.message || err || '未知错误');
    addStep(run.runId, {
      state,
      tool: toolName,
      success: false,
      durationMs,
      inputPreview: preview(input),
      error,
    });
    await appendAgentLog({
      runId: run.runId,
      traceId: run.traceId,
      state,
      tool: toolName,
      success: false,
      durationMs,
      input,
      output,
      error,
    });
    throw err;
  }
}

function publicRun(run) {
  if (!run) return null;
  return {
    runId: run.runId,
    traceId: run.traceId,
    status: run.status,
    currentStep: run.currentStep,
    options: {
      goal: run.options.goal,
      mode: run.options.mode,
      requestedMode: run.policy?.requestedMode || run.options.mode,
      effectiveMode: run.policy?.effectiveMode || run.options.mode,
      policyReasons: run.policy?.reasons || [],
      appName: run.options.appName,
      platform: run.options.platform,
      autoRun: run.options.autoRun,
      autoRepair: run.options.autoRepair,
      autoCreateBug: run.options.autoCreateBug,
      maxRetries: run.options.maxRetries,
      maxAutoRepair: run.options.maxAutoRepair,
    },
    steps: run.steps,
    artifacts: run.artifacts,
    confirmations: run.confirmations,
    policy: run.policy,
    retryCount: run.retryCount,
    createdAt: run.createdAt,
    updatedAt: run.updatedAt,
  };
}

export async function startAgentRun(input = {}, deps = {}) {
  const whitelist = deps.agentWhitelist || {};
  const policy = applyAgentPolicy(input, whitelist);
  const options = normalizeRunOptions({
    ...input,
    mode: policy.effectiveMode,
    autoRun: policy.autoRun,
    autoRepair: policy.autoRepair,
    autoCreateBug: policy.autoCreateBug,
    autoOverwriteBaseline: policy.autoOverwriteBaseline,
    maxRetries: policy.maxRetries,
  });
  assertRunnableOptions(options);
  const run = createRun(options);
  run.policy = policy;
  const tools = createAgentTools(deps);

  try {
    if (policy.reasons.length) {
      addStep(run.runId, {
        state: AGENT_STATES.START,
        tool: 'applyAgentPolicy',
        success: true,
        message: policy.reasons.join('；'),
        outputPreview: preview(policy),
      });
      await appendAgentLog({
        runId: run.runId,
        traceId: run.traceId,
        state: AGENT_STATES.START,
        tool: 'applyAgentPolicy',
        success: true,
        input,
        output: policy,
      });
    }

    updateRun(run.runId, {status: AGENT_STATES.GENERATE_CASE, currentStep: AGENT_STATES.GENERATE_CASE});
    const caseResult = await recordToolStep(run, AGENT_STATES.GENERATE_CASE, 'generateCase', options, () => tools.generateCase(options));
    run.artifacts.caseDraft = caseResult.cases;

    updateRun(run.runId, {status: AGENT_STATES.GENERATE_YAML, currentStep: AGENT_STATES.GENERATE_YAML});
    const yamlResult = await recordToolStep(run, AGENT_STATES.GENERATE_YAML, 'generateYaml', options, () => tools.generateYaml(options));
    run.artifacts.yamlDraft = yamlResult.yaml;

    updateRun(run.runId, {status: AGENT_STATES.VALIDATE_YAML, currentStep: AGENT_STATES.VALIDATE_YAML});
    const validation = await recordToolStep(
      run,
      AGENT_STATES.VALIDATE_YAML,
      'validateYaml',
      {yaml: yamlResult.yaml},
      () => tools.validateYaml({yaml: yamlResult.yaml}),
    );
    run.artifacts.validation = validation;

    if (!validation.valid) {
      updateRun(run.runId, {
        status: AGENT_STATES.WAIT_CONFIRM,
        currentStep: AGENT_STATES.WAIT_CONFIRM,
        waitReason: 'YAML 校验未通过，需要人工确认后再修复。',
      });
      run.confirmations.push({
        type: 'yaml_validation_failed',
        message: 'YAML 校验未通过，需要人工确认是否进入修复。',
        errors: validation.errors || [],
        createdAt: new Date().toISOString(),
      });
      return publicRun(run);
    }

    updateRun(run.runId, {status: AGENT_STATES.SAVE_ASSET, currentStep: AGENT_STATES.SAVE_ASSET});
    const assetResult = await recordToolStep(
      run,
      AGENT_STATES.SAVE_ASSET,
      'saveYamlAsset',
      {goal: options.goal, yaml: yamlResult.yaml, autoOverwriteBaseline: options.autoOverwriteBaseline},
      () => tools.saveYamlAsset({
        goal: options.goal,
        appName: options.appName,
        yaml: yamlResult.yaml,
        autoOverwriteBaseline: options.autoOverwriteBaseline,
        lockedBaseline: Boolean(input.lockedBaseline),
      }),
    );
    run.artifacts.yamlAsset = assetResult;

    if (options.mode === AGENT_MODES.SEMI_AUTO) {
      updateRun(run.runId, {
        status: AGENT_STATES.WAIT_CONFIRM_RUN,
        currentStep: AGENT_STATES.WAIT_CONFIRM_RUN,
        waitReason: 'SEMI_AUTO 模式：执行前需要人工确认。',
      });
      run.confirmations.push({
        type: 'confirm_before_run',
        message: 'SEMI_AUTO 模式已生成并保存草稿，执行 Sonic 前需要人工确认。',
        createdAt: new Date().toISOString(),
      });
      return publicRun(run);
    }

    if (options.autoRun) {
      const runTask = await recordToolStep(run, AGENT_STATES.RUN_TASK, 'runSonicTask', options, () => tools.runSonicTask(options));
      run.confirmations.push({
        type: 'sonic_run_required',
        message: runTask.message,
        createdAt: new Date().toISOString(),
      });
      updateRun(run.runId, {status: AGENT_STATES.WAIT_CONFIRM, currentStep: AGENT_STATES.WAIT_CONFIRM});
      return publicRun(run);
    }

    updateRun(run.runId, {
      status: AGENT_STATES.WAIT_CONFIRM_RUN,
      currentStep: AGENT_STATES.WAIT_CONFIRM_RUN,
      waitReason: '已生成并校验 YAML，等待人工确认保存或执行。',
    });
    run.confirmations.push({
      type: 'review_generated_yaml',
      message: '已生成并校验 YAML，等待人工确认保存或执行。',
      createdAt: new Date().toISOString(),
    });
    return publicRun(run);
  } catch (error) {
    updateRun(run.runId, {
      status: AGENT_STATES.FAILED,
      currentStep: AGENT_STATES.FAILED,
      error: String(error?.message || error || '未知错误'),
    });
    return publicRun(run);
  }
}

export function getAgentRun(runId) {
  return publicRun(getRun(runId));
}

export function listAgentRuns(limit = 50) {
  return listRuns(limit).map(publicRun);
}

export function confirmAgentRun(runId, payload = {}) {
  const run = getRun(runId);
  if (!run) return null;
  if (TERMINAL_STATES.has(run.status)) return publicRun(run);
  run.confirmations.push({
    type: payload.type || 'manual_confirm',
    decision: payload.decision || 'confirmed',
    note: payload.note || '',
    createdAt: new Date().toISOString(),
  });
  updateRun(runId, {
    status: AGENT_STATES.WAIT_CONFIRM,
    currentStep: AGENT_STATES.WAIT_CONFIRM,
    waitReason: '人工确认已记录；下一阶段接入保存、执行或飞书工具后继续推进。',
  });
  return publicRun(run);
}

export function cancelAgentRun(runId, payload = {}) {
  const run = getRun(runId);
  if (!run) return null;
  updateRun(runId, {
    status: AGENT_STATES.CANCELLED,
    currentStep: AGENT_STATES.CANCELLED,
    cancelReason: payload.reason || '用户取消',
  });
  return publicRun(run);
}
