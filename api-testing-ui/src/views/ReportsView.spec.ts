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

  it('uses compact history rows and opens a full diagnostic report in place', async () => {
    const executions = useExecutionsStore()
    const context = useContextStore()
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    executions.executions = [report]
    const wrapper = mount(ReportsView)

    expect(wrapper.text()).toContain('通过率 50%')
    expect(wrapper.find('.summary-grid').exists()).toBe(false)
    await wrapper.get('[data-testid="report-history-row"]').trigger('click')
    expect(wrapper.text()).toContain('返回报告列表')
    expect(wrapper.text()).toContain('诊断结论')
  })
})
