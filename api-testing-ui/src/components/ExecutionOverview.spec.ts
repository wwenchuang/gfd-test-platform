// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ExecutionView } from '../api/contracts'
import ExecutionOverview from './ExecutionOverview.vue'

const execution: ExecutionView = {
  id: 'execution-123456789', project_id: 'project-1', state: 'DONE', execution_type: 'regression',
  application_name: '校园助手', business_name: '校园业务',
  source_revision_id: 'source-1', environment_revision_id: 'environment-1', environment_name: '生产环境（新）- 腾讯云 · v6',
  case_statuses: ['PASSED', 'FAILED', 'SKIPPED'],
  case_results: [
    { execution_case_id: 'ec-1', case_version_id: 'cv-1', endpoint_id: 'ep-1', case_name: '列表', endpoint_summary: '', method: 'POST', path: '/page', status: 'PASSED', failure_category: '', duration_ms: 120, sanitized_result: {} },
    { execution_case_id: 'ec-2', case_version_id: 'cv-2', endpoint_id: 'ep-2', case_name: '取消', endpoint_summary: '', method: 'POST', path: '/cancel', status: 'FAILED', failure_category: 'product_assertion', duration_ms: 80, sanitized_result: {} },
    { execution_case_id: 'ec-3', case_version_id: 'cv-3', endpoint_id: 'ep-3', case_name: '批量取消', endpoint_summary: '', method: 'POST', path: '/batch', status: 'SKIPPED', failure_category: 'dependency', duration_ms: 0, sanitized_result: {} },
  ],
  summary: { total: 3, passed: 1, failed: 1, skipped: 1 }, cancellation_requested: false,
  created_at: '2026-08-12T07:09:00Z', started_at: '2026-08-12T07:09:01Z', finished_at: '2026-08-12T07:09:02Z',
}

describe('ExecutionOverview', () => {
  it('shows environment, conclusion, truthful counts, rate and duration', () => {
    const wrapper = mount(ExecutionOverview, { props: { execution } })

    expect(wrapper.text()).toContain('生产环境（新）- 腾讯云 · v6')
    expect(wrapper.get('[data-testid="overview-application"]').text()).toContain('校园助手')
    expect(wrapper.get('[data-testid="overview-business"]').text()).toContain('校园业务')
    expect(wrapper.get('[data-testid="execution-conclusion"]').text()).toBe('未通过')
    expect(wrapper.get('[data-testid="overview-passed"]').text()).toContain('1')
    expect(wrapper.get('[data-testid="overview-failed"]').text()).toContain('1')
    expect(wrapper.get('[data-testid="overview-skipped"]').text()).toContain('1')
    expect(wrapper.get('[data-testid="overview-rate"]').text()).toContain('33.3%')
    expect(wrapper.get('[data-testid="overview-duration"]').text()).toContain('1.00 秒')
    expect(wrapper.text()).toContain('execution-123456789')
  })
})
