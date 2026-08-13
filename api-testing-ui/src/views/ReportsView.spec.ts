// @vitest-environment jsdom

import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'

import type { ExecutionView } from '../api/contracts'
import { useExecutionsStore } from '../stores/executions'
import { useContextStore } from '../stores/context'
import { useNotificationsStore } from '../stores/notifications'
import ReportsView from './ReportsView.vue'

const routeState = vi.hoisted(() => ({ query: {} as Record<string, string> }))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => routeState,
}))

const report: ExecutionView = {
  id: 'report-1', project_id: 'project-1', state: 'DONE', execution_type: 'regression', source_revision_id: 'source-1', environment_revision_id: 'environment-1', environment_name: '生产环境 V6', case_statuses: ['PASSED', 'FAILED'], summary: { total: 2, passed: 1, failed: 1 }, cancellation_requested: false, created_at: '2026-08-12T07:00:00Z', started_at: null, finished_at: null,
  case_results: [{ execution_case_id: 'case-1', case_version_id: 'version-1', endpoint_id: 'endpoint-1', case_name: '查询收藏', endpoint_summary: '', method: 'POST', path: '/favorites/page', status: 'PASSED', failure_category: '', duration_ms: 100, sanitized_result: {} }, { execution_case_id: 'case-2', case_version_id: 'version-2', endpoint_id: 'endpoint-2', case_name: '取消收藏', endpoint_summary: '', method: 'POST', path: '/favorites/cancel', status: 'FAILED', failure_category: 'product_assertion', duration_ms: 100, sanitized_result: {} }],
}

describe('ReportsView', () => {
  beforeEach(() => {
    routeState.query = {}
    setActivePinia(createPinia())
  })

  it('summarizes report health before opening the full diagnostic report', async () => {
    const executions = useExecutionsStore()
    const context = useContextStore()
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    executions.executions = [report]
    const wrapper = mount(ReportsView)

    expect(wrapper.text()).toContain('项目报告驾驶舱')
    expect(wrapper.text()).toContain('1 次执行')
    expect(wrapper.text()).toContain('1 个问题')
    expect(wrapper.text()).toContain('需要关注')
    expect(wrapper.text()).toContain('取消收藏')
    expect(wrapper.text()).toContain('断言失败')
    expect(wrapper.text()).toContain('50%')
    expect(wrapper.find('.report-dashboard').exists()).toBe(true)
    expect(wrapper.find('.summary-grid').exists()).toBe(false)
    await wrapper.get('[data-testid="report-history-row"]').trigger('click')
    await wrapper.get('[data-testid="report-open-diagnostic"]').trigger('click')
    expect(wrapper.text()).toContain('返回报告列表')
    expect(wrapper.text()).toContain('诊断结论')
  })

  it('loads and filters report results by the selected project', async () => {
    const executions = useExecutionsStore()
    const context = useContextStore()
    const notifications = useNotificationsStore()
    context.projectId = 'project-1'
    context.projects = [
      { id: 'project-1', name: '3D家用' },
      { id: 'project-2', name: '商城项目' },
    ] as typeof context.projects
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    vi.spyOn(notifications, 'loadFeishu').mockResolvedValue()
    const otherReport: ExecutionView = {
      ...report,
      id: 'report-2',
      project_id: 'project-2',
      summary: { total: 1, passed: 1, failed: 0 },
      case_results: [{ ...report.case_results[0], case_name: '商城项目用例' }],
    }
    const load = vi.spyOn(executions, 'load').mockImplementation(async projectId => {
      executions.executions = projectId === 'project-1' ? [report, otherReport] : [otherReport]
    })

    const wrapper = mount(ReportsView)
    await flushPromises()

    expect(load).toHaveBeenCalledWith('project-1')
    expect((wrapper.get('[data-testid="report-project-select"]').element as HTMLSelectElement).value).toBe('project-1')
    expect(wrapper.text()).toContain('项目报告驾驶舱')
    expect(wrapper.text()).toContain('3D家用')
    expect(wrapper.text()).toContain('取消收藏')
    expect(wrapper.text()).not.toContain('商城项目用例')

    await wrapper.get('[data-testid="report-project-select"]').setValue('project-2')
    await flushPromises()

    expect(load).toHaveBeenCalledWith('project-2')
    expect(wrapper.text()).toContain('商城项目')
    expect(wrapper.text()).toContain('商城项目用例')
  })

  it('archives selected reports in bulk from the report dashboard', async () => {
    const executions = useExecutionsStore()
    const context = useContextStore()
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const deleteExecutions = vi.spyOn(executions, 'deleteExecutions').mockImplementation(async ids => {
      executions.executions = executions.executions.filter(item => !ids.includes(item.id))
    })
    executions.executions = [
      report,
      { ...report, id: 'report-2', summary: { total: 1, passed: 1, failed: 0 }, case_results: [report.case_results[0]] },
    ]
    const wrapper = mount(ReportsView)

    await nextTick()
    await wrapper.findAll('input[aria-label="选择报告"]')[0].trigger('click')
    await wrapper.findAll('input[aria-label="选择报告"]')[1].trigger('click')
    await wrapper.get('.report-board-actions .danger-command').trigger('click')

    expect(deleteExecutions).toHaveBeenCalledWith(['report-1', 'report-2'])
    expect(wrapper.text()).toContain('0 / 0')
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

  it('shows the persisted Feishu sent state on the report card', () => {
    const executions = useExecutionsStore()
    const context = useContextStore()
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    executions.executions = [{
      ...report,
      notifications: {
        feishu: { sent: true, failed: false, message: '飞书通知已发' },
      },
    }]

    const wrapper = mount(ReportsView)

    expect(wrapper.get('[data-testid="report-feishu-status"]').text()).toContain('飞书通知已发')
  })

  it('opens the report requested by Feishu link query', async () => {
    routeState.query = { execution_id: 'report-2' }
    const executions = useExecutionsStore()
    const context = useContextStore()
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    executions.executions = [
      report,
      {
        ...report,
        id: 'report-2',
        execution_type: 'baseline_regression',
        environment_name: '生产环境 V9',
        summary: { total: 1, passed: 1, failed: 0 },
        case_results: [{ ...report.case_results[0], case_name: '目标报告用例' }],
      },
    ]

    const wrapper = mount(ReportsView)
    await nextTick()

    expect(wrapper.get('.report-detail-hero').text()).toContain('生产环境 V9')
    expect(wrapper.text()).toContain('目标报告用例')
  })
})
