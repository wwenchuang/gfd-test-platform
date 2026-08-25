// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ExecutionView } from '../api/contracts'
import ExecutionConsole from './ExecutionConsole.vue'

const execution: ExecutionView = {
  id: 'execution-1', project_id: 'project-1', state: 'DONE', execution_type: 'regression',
  task_id: 'task-1', task_name: '收藏接口发版回归',
  source_revision_id: 'source-1', environment_revision_id: 'environment-1', environment_name: '生产环境 V6',
  case_statuses: ['PASSED', 'FAILED'], summary: { PASSED: 1, FAILED: 1 }, cancellation_requested: false,
  created_at: '2026-08-12T07:00:00Z', started_at: '2026-08-12T07:00:01Z', finished_at: '2026-08-12T07:00:03Z',
  case_results: [
    { execution_case_id: 'case-1', case_version_id: 'version-1', endpoint_id: 'endpoint-1', case_name: '查询收藏', endpoint_summary: '', method: 'POST', path: '/favorites/page', status: 'PASSED', failure_category: '', duration_ms: 100, sanitized_result: {} },
    { execution_case_id: 'case-2', case_version_id: 'version-2', endpoint_id: 'endpoint-2', case_name: '取消收藏', endpoint_summary: '', method: 'POST', path: '/favorites/cancel', status: 'FAILED', failure_category: 'product_assertion', duration_ms: 200, sanitized_result: { assertion_results: [{ passed: false, message: '业务码不匹配' }] } },
  ],
}

describe('ExecutionConsole', () => {
  it('renders the overview and the approved three-view structure', () => {
    const wrapper = mount(ExecutionConsole, {
      props: { executions: [execution], active: execution, events: [], connectionState: 'complete' },
    })

    expect(wrapper.text()).toContain('生产环境 V6')
    expect(wrapper.text()).toContain('收藏接口发版回归')
    expect(wrapper.text()).toContain('实时轨迹')
    expect(wrapper.text()).toContain('用例明细')
    expect(wrapper.text()).toContain('测试报告')
    expect(wrapper.findAll('[data-testid="realtime-case-row"]')).toHaveLength(2)
    expect(wrapper.text()).toContain('实时日志')
  })

  it('selects a case for evidence and emits the exact result for inspection', async () => {
    const wrapper = mount(ExecutionConsole, {
      props: { executions: [execution], active: execution, events: [], connectionState: 'complete' },
    })

    await wrapper.findAll('[data-testid="realtime-case-row"]')[1].trigger('click')
    expect(wrapper.emitted('inspect')?.[0]?.[0]).toMatchObject({ endpoint_id: 'endpoint-2' })

    await wrapper.get('[data-testid="execution-tab-cases"]').trigger('click')
    expect(wrapper.text()).toContain('业务码不匹配')
  })

  it('keeps the selected case when background analysis refreshes the execution object', async () => {
    const wrapper = mount(ExecutionConsole, {
      props: { executions: [execution], active: execution, events: [], connectionState: 'complete' },
    })
    await wrapper.findAll('[data-testid="realtime-case-row"]')[1].trigger('click')

    await wrapper.setProps({ active: {
      ...execution,
      case_results: execution.case_results.map(item => item.execution_case_id === 'case-2'
        ? { ...item, failure_analysis: { analyzer: 'ai_gateway', model: 'qwen-plus', category: 'product_assertion', analysis: { summary: '后台分析完成' } } }
        : item),
    } })
    await wrapper.get('[data-testid="execution-tab-cases"]').trigger('click')

    expect(wrapper.text()).toContain('后台分析完成')
    expect(wrapper.find('.case-result-list button.active').text()).toContain('取消收藏')
  })

  it('resets log filters and visible lines when switching execution records', async () => {
    const wrapper = mount(ExecutionConsole, {
      props: {
        executions: [execution], active: execution,
        events: [{ id: 1, type: 'case_finished', level: 'error', caseId: 'case-2', message: '旧执行失败', payload: {} }],
        connectionState: 'complete',
      },
    })
    await wrapper.get('[data-testid="log-level"]').setValue('error')

    await wrapper.setProps({
      active: { ...execution, id: 'execution-2' },
      events: [{ id: 1, type: 'case_started', level: 'info', caseId: 'case-1', message: '新执行开始', payload: {} }],
    })

    expect((wrapper.get('[data-testid="log-level"]').element as HTMLSelectElement).value).toBe('all')
    expect(wrapper.text()).toContain('新执行开始')
  })

  it('keeps a rerun action for the selected execution and labels records by task type', async () => {
    const debugExecution: ExecutionView = {
      ...execution,
      id: 'execution-debug',
      execution_type: 'debug',
      task_id: null,
      task_name: null,
      case_results: [execution.case_results[0]],
      summary: { PASSED: 1 },
    }
    const wrapper = mount(ExecutionConsole, {
      props: {
        executions: [execution, debugExecution],
        active: debugExecution,
        events: [],
        connectionState: 'complete',
      },
    })

    expect(wrapper.text()).toContain('在线调试')
    expect(wrapper.text()).toContain('单条')
    expect(wrapper.text()).toContain('收藏接口发版回归')
    expect(wrapper.text()).toContain('多条')

    await wrapper.get('[data-testid="rerun-active-execution"]').trigger('click')
    expect(wrapper.emitted('rerun')?.[0]?.[0]).toMatchObject({ id: 'execution-debug' })
  })

  it('filters records by source, conclusion and search while showing business conclusions', async () => {
    const debugPassed: ExecutionView = {
      ...execution,
      id: 'execution-debug-passed',
      execution_type: 'debug',
      task_id: null,
      task_name: null,
      environment_name: '测试环境',
      case_results: [execution.case_results[0]],
      summary: { PASSED: 1 },
    }
    const scheduledRunning: ExecutionView = {
      ...execution,
      id: 'execution-running',
      execution_type: 'scheduled',
      execution_source: 'scheduled_job',
      task_name: '每日收藏回归',
      state: 'RUNNING',
      case_results: [{ ...execution.case_results[0], status: 'RUNNING' }],
      summary: { RUNNING: 1 },
      finished_at: null,
    }
    const wrapper = mount(ExecutionConsole, {
      props: {
        executions: [execution, debugPassed, scheduledRunning],
        active: execution,
        events: [],
        connectionState: 'complete',
      },
    })

    expect(wrapper.get('[data-testid="execution-row-execution-1"]').text()).toContain('未通过')
    expect(wrapper.get('[data-testid="execution-row-execution-1"]').text()).not.toContain('DONE')

    await wrapper.get('[data-testid="execution-filter-source"]').setValue('debug')
    expect(wrapper.find('[data-testid="execution-row-execution-debug-passed"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="execution-row-execution-1"]').exists()).toBe(false)
    expect(wrapper.emitted('select')).toBeUndefined()

    await wrapper.get('[data-testid="execution-filter-source"]').setValue('all')
    await wrapper.get('[data-testid="execution-filter-conclusion"]').setValue('running')
    expect(wrapper.find('[data-testid="execution-row-execution-running"]').exists()).toBe(true)
    expect(wrapper.get('[data-testid="execution-row-execution-running"]').text()).toContain('执行中')

    await wrapper.get('[data-testid="execution-filter-conclusion"]').setValue('all')
    await wrapper.get('[data-testid="execution-filter-search"]').setValue('测试环境')
    expect(wrapper.find('[data-testid="execution-row-execution-debug-passed"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="execution-row-execution-running"]').exists()).toBe(false)
  })
})
