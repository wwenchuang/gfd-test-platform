// @vitest-environment jsdom

import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ExecutionView } from '../api/contracts'
import { useExecutionsStore } from '../stores/executions'
import { useContextStore } from '../stores/context'
import ReportsView from './ReportsView.vue'

vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))

const report: ExecutionView = {
  id: 'report-1', project_id: 'project-1', state: 'DONE', execution_type: 'regression', source_revision_id: 'source-1', environment_revision_id: 'environment-1', environment_name: '生产环境 V6', case_statuses: ['PASSED', 'FAILED'], summary: { total: 2, passed: 1, failed: 1 }, cancellation_requested: false, created_at: '2026-08-12T07:00:00Z', started_at: null, finished_at: null,
  case_results: [{ execution_case_id: 'case-1', case_version_id: 'version-1', endpoint_id: 'endpoint-1', case_name: '查询收藏', endpoint_summary: '', method: 'POST', path: '/favorites/page', status: 'PASSED', failure_category: '', duration_ms: 100, sanitized_result: {} }, { execution_case_id: 'case-2', case_version_id: 'version-2', endpoint_id: 'endpoint-2', case_name: '取消收藏', endpoint_summary: '', method: 'POST', path: '/favorites/cancel', status: 'FAILED', failure_category: 'product_assertion', duration_ms: 100, sanitized_result: {} }],
}

describe('ReportsView', () => {
  beforeEach(() => setActivePinia(createPinia()))

  it('summarizes report health before opening the full diagnostic report', async () => {
    const executions = useExecutionsStore()
    const context = useContextStore()
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    executions.executions = [report]
    const wrapper = mount(ReportsView)

    expect(wrapper.text()).toContain('报告概览')
    expect(wrapper.text()).toContain('1 次执行')
    expect(wrapper.text()).toContain('1 个问题')
    expect(wrapper.text()).toContain('需要关注')
    expect(wrapper.text()).toContain('取消收藏')
    expect(wrapper.text()).toContain('断言失败')
    expect(wrapper.text()).toContain('通过率 50%')
    expect(wrapper.find('.report-dashboard').exists()).toBe(true)
    expect(wrapper.find('.summary-grid').exists()).toBe(false)
    await wrapper.get('[data-testid="report-history-row"]').trigger('click')
    expect(wrapper.text()).toContain('返回报告列表')
    expect(wrapper.text()).toContain('诊断结论')
  })

  it('labels baseline regression reports separately from ad-hoc debug runs', () => {
    const executions = useExecutionsStore()
    const context = useContextStore()
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    executions.executions = [{
      ...report,
      id: 'baseline-report-1',
      execution_type: 'baseline_regression',
      case_statuses: ['PASSED'],
      case_results: [report.case_results[0]],
      summary: { total: 1, passed: 1, failed: 0 },
    }]

    const wrapper = mount(ReportsView)

    expect(wrapper.text()).toContain('基线回归')
    expect(wrapper.text()).not.toContain('自动回归')
  })
})
