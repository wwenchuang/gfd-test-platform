import {v4 as uuidv4} from 'uuid';
import {AGENT_STATES} from './agent-state-machine.js';

const runs = new Map();

export function createRun(options) {
  const runId = `agent_${Date.now()}_${uuidv4().slice(0, 8)}`;
  const traceId = options.traceId || `trace_${Date.now()}_${uuidv4().slice(0, 8)}`;
  const run = {
    runId,
    traceId,
    status: AGENT_STATES.START,
    currentStep: AGENT_STATES.START,
    options,
    steps: [],
    artifacts: {},
    confirmations: [],
    retryCount: 0,
    createdAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
  };
  runs.set(runId, run);
  return run;
}

export function getRun(runId) {
  return runs.get(runId) || null;
}

export function listRuns(limit = 50) {
  return Array.from(runs.values())
    .sort((a, b) => String(b.createdAt).localeCompare(String(a.createdAt)))
    .slice(0, limit);
}

export function updateRun(runId, patch) {
  const run = getRun(runId);
  if (!run) return null;
  Object.assign(run, patch, {updatedAt: new Date().toISOString()});
  return run;
}

export function addStep(runId, step) {
  const run = getRun(runId);
  if (!run) return null;
  const normalized = {
    time: new Date().toISOString(),
    state: step.state,
    tool: step.tool || '',
    success: Boolean(step.success),
    durationMs: Number(step.durationMs || 0),
    message: step.message || '',
    inputPreview: step.inputPreview || '',
    outputPreview: step.outputPreview || '',
    error: step.error || '',
  };
  run.steps.push(normalized);
  run.currentStep = step.state || run.currentStep;
  run.updatedAt = new Date().toISOString();
  return normalized;
}
