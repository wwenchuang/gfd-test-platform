import { describe, expect, it } from 'vitest'

import type { ApiTestTask } from '../api/contracts'
import { taskLatestResult, taskStateLabel } from './taskPresentation'

function task(overrides: Partial<ApiTestTask>): ApiTestTask {
  return {
    id: 'task-1',
    project_id: 'project-1',
    source_revision_id: 'source-1',
    environment_revision_id: 'environment-1',
    name: '历史回归任务',
    state: 'draft',
    selected_endpoint_ids: [],
    runnable_baseline_count: 0,
    latest_ai_job_id: null,
    latest_execution_id: null,
    summary: {},
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
    ...overrides,
  }
}

describe('task presentation', () => {
  it('uses runnable baselines as the readiness source for a stale draft task', () => {
    expect(taskStateLabel('draft', 240)).toBe('可执行')
    expect(taskStateLabel('draft', 0)).toBe('待设计')
  })

  it('does not describe a zero-pass execution as passed', () => {
    expect(taskLatestResult(task({
      latest_execution_id: 'execution-1',
      latest_execution_state: 'DONE',
      latest_execution_summary: { total: 1, passed: 0, failed: 1 },
    }))).toBe('最近结果 未通过 · 0/1 通过 · 0%')
  })
})
