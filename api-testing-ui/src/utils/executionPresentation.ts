import type { ExecutionCaseResult, ExecutionView } from '../api/contracts'

export type ExecutionTone = 'passed' | 'failed' | 'broken' | 'running' | 'cancelled' | 'neutral'
export type ExecutionSourceScope = 'formal' | 'debug'

export interface ExecutionMetrics {
  total: number
  passed: number
  failed: number
  broken: number
  skipped: number
  cancelled: number
  running: number
  queued: number
  durationMs: number
  passRate: number
}

const STATUS_LABELS: Record<string, string> = {
  PASSED: '通过', FAILED: '断言失败', BROKEN: '运行异常', SKIPPED: '已跳过',
  CANCELLED: '已取消', RUNNING: '运行中', QUEUED: '等待中', DONE: '已结束',
}

const EXECUTION_TYPE_LABELS: Record<string, string> = {
  debug: '在线调试',
  regression: '自动回归',
  baseline_regression: '基线回归',
  scheduled: '定时任务',
}

export function statusLabel(status: string): string {
  return STATUS_LABELS[String(status || '').toUpperCase()] || String(status || '未知')
}

export function executionTypeLabel(executionOrType: ExecutionView | string): string {
  const value = typeof executionOrType === 'string'
    ? executionOrType
    : executionOrType.execution_type
  if (typeof executionOrType !== 'string' && executionOrType.execution_source === 'scheduled_job') {
    return executionOrType.task_name || '定时任务'
  }
  return EXECUTION_TYPE_LABELS[value] || 'API 执行'
}

export function executionSourceScope(execution: ExecutionView): ExecutionSourceScope {
  return execution.execution_type === 'debug' ? 'debug' : 'formal'
}

export function formatDuration(durationMs: number): string {
  const safe = Number.isFinite(durationMs) ? Math.max(0, durationMs) : 0
  return safe >= 1000 ? `${(safe / 1000).toFixed(2)} 秒` : `${safe} ms`
}

export function executionMetrics(execution: ExecutionView): ExecutionMetrics {
  const counts = {
    passed: 0, failed: 0, broken: 0, skipped: 0, cancelled: 0, running: 0, queued: 0,
  }
  for (const result of execution.case_results) {
    const key = result.status.toLowerCase() as keyof typeof counts
    if (key in counts) counts[key] += 1
  }
  const total = execution.case_results.length || numberValue(execution.summary.total)
  if (!execution.case_results.length) {
    for (const key of Object.keys(counts) as Array<keyof typeof counts>) {
      counts[key] = numberValue(execution.summary[key])
    }
  }
  const startedAt = execution.started_at ? Date.parse(execution.started_at) : Number.NaN
  const finishedAt = execution.finished_at ? Date.parse(execution.finished_at) : Number.NaN
  const wallDuration = Number.isFinite(startedAt)
    ? Math.max(0, (Number.isFinite(finishedAt) ? finishedAt : Date.now()) - startedAt)
    : Number.NaN
  const durationMs = Number.isFinite(wallDuration)
    ? wallDuration
    : execution.case_results.reduce((sum, result) => sum + Math.max(0, result.duration_ms || 0), 0)
  return {
    total,
    ...counts,
    durationMs,
    passRate: total ? Math.round((counts.passed / total) * 100) : 0,
  }
}

export function executionConclusion(execution: ExecutionView): { label: string; tone: ExecutionTone } {
  const metrics = executionMetrics(execution)
  if (execution.state === 'CANCELLED' || metrics.cancelled > 0) return { label: '已取消', tone: 'cancelled' }
  if (metrics.failed > 0) return { label: '未通过', tone: 'failed' }
  if (metrics.broken > 0) return { label: '运行异常', tone: 'broken' }
  if (['RUNNING', 'QUEUED'].includes(execution.state) || metrics.running > 0 || metrics.queued > 0) {
    return { label: execution.state === 'QUEUED' ? '等待执行' : '执行中', tone: 'running' }
  }
  if (metrics.skipped > 0) return { label: '执行不完整', tone: 'neutral' }
  if (metrics.total > 0 && metrics.passed === metrics.total) return { label: '通过', tone: 'passed' }
  return { label: statusLabel(execution.state), tone: 'neutral' }
}

export function executionFailureBuckets(execution: ExecutionView): {
  product: number; scriptData: number; environment: number; skipped: number; cancelled: number
} {
  const buckets = { product: 0, scriptData: 0, environment: 0, skipped: 0, cancelled: 0 }
  for (const result of execution.case_results) {
    if (result.status === 'SKIPPED') { buckets.skipped += 1; continue }
    if (result.status === 'CANCELLED') { buckets.cancelled += 1; continue }
    if (!['FAILED', 'BROKEN'].includes(result.status)) continue
    if (['product_assertion', 'product_response'].includes(result.failure_category)) buckets.product += 1
    else if ([
      'environment', 'network', 'transport', 'timeout', 'auth', 'authentication',
      'infrastructure', 'worker', 'host_policy', 'redirect_limit', 'response_limit',
    ].includes(result.failure_category)) buckets.environment += 1
    else buckets.scriptData += 1
  }
  return buckets
}

export function caseResultSummary(result: ExecutionCaseResult): string {
  const detail = result.sanitized_result || {}
  const assertions = Array.isArray(detail.assertion_results) ? detail.assertion_results as Array<Record<string, unknown>> : []
  const failed = assertions.find(item => item.passed === false)
  if (failed) return String(failed.message || failed.error || '断言未满足')
  if (result.status === 'SKIPPED') return String(detail.error_message || detail.skip_reason || '前置依赖未满足')
  if (result.status === 'BROKEN') return String(detail.error_message || '执行过程中发生异常')
  if (result.status === 'PASSED') return '请求和断言均通过'
  return statusLabel(result.status)
}

export function redactSensitiveEvidence(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(redactSensitiveEvidence)
  if (!value || typeof value !== 'object') return value
  const record = value as Record<string, unknown>
  const sensitiveAssertion = Object.entries(record).some(([key, item]) => (
    /^(path|name|header|key)$/i.test(key)
      && typeof item === 'string'
      && /authorization|cookie|token|password|secret|api[-_]?key/i.test(item)
  ))
  return Object.fromEntries(Object.entries(record).map(([key, item]) => [
    key,
    /authorization|cookie|token|password|secret|api[-_]?key/i.test(key)
      || (sensitiveAssertion && /^(expected|actual|value)$/i.test(key))
      ? '已隐藏'
      : redactSensitiveEvidence(item),
  ]))
}

function numberValue(value: unknown): number {
  const number = Number(value || 0)
  return Number.isFinite(number) ? Math.max(0, number) : 0
}
