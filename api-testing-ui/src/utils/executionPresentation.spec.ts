import { describe, expect, it } from 'vitest'

import type { ExecutionCaseResult, ExecutionView } from '../api/contracts'
import {
  executionConclusion,
  executionFailureBuckets,
  executionMetrics,
  executionScopeLabel,
  executionSourceScope,
  formatDuration,
  formatPassRate,
  redactSensitiveEvidence,
  statusLabel,
} from './executionPresentation'

function result(status: string, failureCategory = '', durationMs = 100): ExecutionCaseResult {
  return {
    execution_case_id: `execution-case-${status}-${failureCategory}`,
    case_version_id: `case-version-${status}-${failureCategory}`,
    endpoint_id: `endpoint-${status}-${failureCategory}`,
    case_name: `${status} case`, endpoint_summary: '', method: 'POST', path: '/collection/page',
    status, failure_category: failureCategory, duration_ms: durationMs, sanitized_result: {},
  }
}

const execution: ExecutionView = {
  id: 'execution-1', project_id: 'project-1', state: 'DONE', execution_type: 'regression',
  source_revision_id: 'source-1', environment_revision_id: 'environment-1', environment_name: '生产环境 · v6',
  case_statuses: ['PASSED', 'PASSED', 'FAILED', 'BROKEN', 'SKIPPED'],
  case_results: [
    result('PASSED', '', 120), result('PASSED', '', 80), result('FAILED', 'product_assertion', 90),
    result('BROKEN', 'environment', 30), result('SKIPPED', 'dependency', 0),
  ],
  summary: { total: 5, passed: 2, failed: 1, broken: 1, skipped: 1 },
  cancellation_requested: false, created_at: '2026-08-12T07:09:00Z',
  started_at: '2026-08-12T07:09:01Z', finished_at: '2026-08-12T07:09:02Z',
}

describe('execution presentation', () => {
  it('keeps deterministic child states and pass rate truthful', () => {
    expect(executionMetrics(execution)).toEqual({
      total: 5, passed: 2, failed: 1, broken: 1, skipped: 1, cancelled: 0,
      running: 0, queued: 0, durationMs: 1000, passRate: 40,
    })
    expect(executionConclusion(execution)).toEqual({ label: '未通过', tone: 'failed' })
  })

  it('groups deterministic failures without asking AI to reclassify them', () => {
    expect(executionFailureBuckets(execution)).toEqual({
      product: 1, scriptData: 0, environment: 1, skipped: 1, cancelled: 0,
    })
  })

  it('keeps platform and transport failures out of the script/data bucket', () => {
    const classified = {
      ...execution,
      case_results: [result('BROKEN', 'worker'), result('BROKEN', 'transport'), result('BROKEN', 'host_policy'), result('BROKEN', 'assertion_definition')],
    }

    expect(executionFailureBuckets(classified)).toEqual({
      product: 0, scriptData: 1, environment: 3, skipped: 0, cancelled: 0,
    })
  })

  it('does not report skipped-only or partially skipped executions as passed', () => {
    const skippedOnly = { ...execution, case_results: [result('SKIPPED', 'dependency')], summary: { total: 1, skipped: 1 } }
    const partial = { ...execution, case_results: [result('PASSED'), result('SKIPPED', 'dependency')], summary: { total: 2, passed: 1, skipped: 1 } }

    expect(executionConclusion(skippedOnly)).toEqual({ label: '执行不完整', tone: 'neutral' })
    expect(executionConclusion(partial)).toEqual({ label: '执行不完整', tone: 'neutral' })
  })

  it('provides Chinese state labels and stable durations', () => {
    expect(statusLabel('BROKEN')).toBe('运行异常')
    expect(statusLabel('SKIPPED')).toBe('已跳过')
    expect(formatDuration(1280)).toBe('1.28 秒')
  })

  it('never rounds an imperfect execution up to a perfect pass rate', () => {
    expect(formatPassRate(716, 719)).toBe('99.6%')
    expect(formatPassRate(239, 240)).toBe('99.6%')
    expect(formatPassRate(9996, 10000)).toBe('99.9%')
    expect(formatPassRate(240, 240)).toBe('100%')
    expect(formatPassRate(241, 240)).toBe('99.9%')
    expect(formatPassRate(0, 0)).toBe('0%')
  })

  it('classifies only explicit debug executions as online debugging', () => {
    expect(executionSourceScope({ ...execution, execution_type: 'debug' })).toBe('debug')
    expect(executionSourceScope({ ...execution, execution_type: 'regression' })).toBe('formal')
    expect(executionSourceScope({ ...execution, execution_type: 'baseline_regression' })).toBe('formal')
    expect(executionSourceScope({ ...execution, execution_type: 'scheduled', execution_source: 'scheduled_job' })).toBe('formal')
  })

  it('presents the snapshotted application and business without internal identifiers', () => {
    expect(executionScopeLabel({
      ...execution,
      application_name: '校园应用、家庭应用',
      business_name: '校园业务、家庭业务',
    })).toBe('校园应用、家庭应用 · 校园业务、家庭业务')
    expect(executionScopeLabel(execution)).toBe('未标注应用 · 未标注业务')
  })

  it('masks nested authorization, token and cookie evidence before rendering', () => {
    const result = redactSensitiveEvidence({
      headers: { Authorization: 'Bearer secret-token', Cookie: 'session=secret' },
      body: { access_token: 'private', modelSn: 'm001' },
    }) as Record<string, any>

    expect(JSON.stringify(result)).not.toContain('secret-token')
    expect(JSON.stringify(result)).not.toContain('session=secret')
    expect(JSON.stringify(result)).not.toContain('private')
    expect(result.body.modelSn).toBe('m001')
  })
})
