// @vitest-environment jsdom

import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import type { ExecutionView } from '../api/contracts'
import ExecutionConsole from './ExecutionConsole.vue'

const execution: ExecutionView = {
  id: 'execution-1', project_id: 'project-1', state: 'DONE', execution_type: 'regression',
  task_id: 'task-1', task_name: '收藏接口发版回归',
  application_name: '校园助手', business_name: '校园业务',
  source_revision_id: 'source-1', environment_revision_id: 'environment-1', environment_name: '生产环境 V6',
  case_statuses: ['PASSED', 'FAILED'], summary: { PASSED: 1, FAILED: 1 }, cancellation_requested: false,
  created_at: '2026-08-12T07:00:00Z', started_at: '2026-08-12T07:00:01Z', finished_at: '2026-08-12T07:00:03Z',
  case_results: [
    { execution_case_id: 'case-1', case_version_id: 'version-1', endpoint_id: 'endpoint-1', endpoint_stable_key: 'stable-favorites-list', case_name: '查询收藏', endpoint_summary: '', method: 'POST', path: '/favorites/page', status: 'PASSED', failure_category: '', duration_ms: 100, sanitized_result: {} },
    { execution_case_id: 'case-2', case_version_id: 'version-2', endpoint_id: 'endpoint-2', endpoint_stable_key: 'stable-favorites-cancel', case_name: '取消收藏', endpoint_summary: '', method: 'POST', path: '/favorites/cancel', status: 'FAILED', failure_category: 'product_assertion', duration_ms: 200, sanitized_result: { assertion_results: [{ passed: false, message: '业务码不匹配' }] } },
  ],
}

describe('ExecutionConsole', () => {
  it('labels a completed historical execution as ended without hiding active connection failures', async () => {
    const wrapper = mount(ExecutionConsole, { props: { executions: [execution], active: execution, events: [], connectionState: 'idle' } })
    expect(wrapper.text()).toContain('执行已结束')
    expect(wrapper.text()).not.toContain('未连接')
    await wrapper.setProps({ connectionState: 'failed' })
    expect(wrapper.text()).toContain('连接失败')
    await wrapper.setProps({ active: { ...execution, state: 'RUNNING' }, connectionState: 'connecting' })
    expect(wrapper.text()).toContain('正在连接')
  })

  it('renders the overview and the approved three-view structure', () => {
    const wrapper = mount(ExecutionConsole, {
      props: { executions: [execution], active: execution, events: [], connectionState: 'complete' },
    })

    expect(wrapper.text()).toContain('生产环境 V6')
    expect(wrapper.text()).toContain('收藏接口发版回归')
    expect(wrapper.get('[data-testid="execution-row-execution-1"]').text()).toContain('校园助手 · 校园业务')
    expect(wrapper.text()).toContain('实时轨迹')
    expect(wrapper.text()).toContain('用例明细')
    expect(wrapper.text()).toContain('测试报告')
    expect(wrapper.findAll('[data-testid="realtime-case-row"]')).toHaveLength(2)
    expect(wrapper.text()).toContain('实时日志')
  })

  it('does not present an empty execution count while a targeted record is still loading', () => {
    const wrapper = mount(ExecutionConsole, {
      props: { executions: [], active: null, events: [], connectionState: 'idle', loading: true },
    })

    expect(wrapper.text()).toContain('正在读取执行记录')
    expect(wrapper.text()).not.toContain('共 0 条')
    expect(wrapper.text()).not.toContain('还没有执行记录')
  })

  it('opens a failed report on the problem cases and allows returning to all results', async () => {
    const wrapper = mount(ExecutionConsole, {
      props: { executions: [execution], active: execution, events: [], connectionState: 'complete' },
    })

    await wrapper.get('[data-testid="execution-tab-report"]').trigger('click')
    expect(wrapper.text()).toContain('已优先定位 1 个问题')
    expect(wrapper.findAll('[data-testid="report-preview-case-row"]')).toHaveLength(1)
    expect(wrapper.get('[data-testid="report-preview-case-row"]').text()).toContain('取消收藏')
    expect(wrapper.get('[data-testid="execution-report-filter-problem"]').classes()).toContain('active')

    await wrapper.get('[data-testid="execution-report-filter-skipped"]').trigger('click')
    expect(wrapper.text()).toContain('当前筛选没有用例')
    await wrapper.get('[data-testid="execution-report-filter-cancelled"]').trigger('click')
    expect(wrapper.text()).toContain('当前筛选没有用例')
    await wrapper.get('[data-testid="execution-report-filter-all"]').trigger('click')
    expect(wrapper.findAll('[data-testid="report-preview-case-row"]')).toHaveLength(2)
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

  it('loads evidence only when the user opens or changes the case detail', async () => {
    const lightweight = {
      ...execution,
      case_results: execution.case_results.map(item => ({ ...item, sanitized_result: {}, evidence_loaded: false })),
    }
    const wrapper = mount(ExecutionConsole, {
      props: { executions: [lightweight], active: lightweight, events: [], connectionState: 'complete' },
    })

    expect(wrapper.emitted('loadEvidence')).toBeUndefined()
    await wrapper.get('[data-testid="execution-tab-cases"]').trigger('click')
    expect(wrapper.emitted('loadEvidence')?.[0]?.[0]).toMatchObject({ execution_case_id: 'case-1' })
    await wrapper.findAll('.embedded-evidence .case-result-list button')[1].trigger('click')
    expect(wrapper.emitted('loadEvidence')?.at(-1)?.[0]).toMatchObject({ execution_case_id: 'case-2' })
  })

  it('reloads selected evidence when an opened running case becomes terminal', async () => {
    const running = {
      ...execution,
      state: 'RUNNING',
      case_results: [{
        ...execution.case_results[0], status: 'RUNNING', sanitized_result: {}, evidence_loaded: false,
      }],
    }
    const wrapper = mount(ExecutionConsole, {
      props: { executions: [running], active: running, events: [], connectionState: 'open' },
    })

    await wrapper.get('[data-testid="execution-tab-cases"]').trigger('click')
    expect(wrapper.emitted('loadEvidence')).toHaveLength(1)

    await wrapper.setProps({
      active: {
        ...running,
        state: 'DONE',
        case_results: [{ ...running.case_results[0], status: 'PASSED', evidence_loaded: false }],
      },
      connectionState: 'complete',
    })

    expect(wrapper.emitted('loadEvidence')).toHaveLength(2)
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
    expect(wrapper.get('[data-testid="execution-row-execution-debug"]').text()).toContain('查询收藏 · 在线调试')
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
    expect(wrapper.get('[data-testid="execution-row-execution-running"]').text()).toContain('触发方式未记录')

    await wrapper.get('[data-testid="execution-filter-conclusion"]').setValue('all')
    await wrapper.get('[data-testid="execution-filter-search"]').setValue('测试环境')
    expect(wrapper.find('[data-testid="execution-row-execution-debug-passed"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="execution-row-execution-running"]').exists()).toBe(false)
  })

  it('paginates long execution histories while keeping bulk selection scoped to all filtered records', async () => {
    const executions = Array.from({ length: 21 }, (_, index) => ({
      ...execution,
      id: `execution-${index + 1}`,
      task_name: `回归任务 ${index + 1}`,
    }))
    const wrapper = mount(ExecutionConsole, {
      props: { executions, active: executions[0], events: [], connectionState: 'complete' },
    })

    expect(wrapper.findAll('.execution-row')).toHaveLength(20)
    expect(wrapper.text()).toContain('第 1 / 2 页')
    expect(wrapper.text()).toContain('第 1-20 条，共 21 条')

    await wrapper.find('.execution-list-tools .text-command').trigger('click')
    expect(wrapper.text()).toContain('归档 21')

    await wrapper.get('[data-testid="execution-page-next"]').trigger('click')
    expect(wrapper.findAll('.execution-row')).toHaveLength(1)
    expect(wrapper.text()).toContain('回归任务 21')
    expect(wrapper.text()).toContain('第 21-21 条，共 21 条')
  })

  it('filters execution history by endpoint and allows clearing the filter', async () => {
    const otherExecution = {
      ...execution,
      id: 'execution-other',
      case_results: [{ ...execution.case_results[0], endpoint_id: 'endpoint-other' }],
    }
    const wrapper = mount(ExecutionConsole, {
      props: { executions: [execution, otherExecution], active: null, events: [], connectionState: 'idle', endpointId: 'endpoint-2' },
    })

    expect(wrapper.text()).toContain('当前仅显示所选接口')
    expect(wrapper.find('[data-testid="execution-row-execution-1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="execution-row-execution-other"]').exists()).toBe(false)

    await wrapper.get('[data-testid="execution-clear-endpoint-filter"]').trigger('click')
    expect(wrapper.emitted('clearEndpointFilter')).toHaveLength(1)
  })

  it('keeps historical executions visible after an interface revision changes its endpoint id', () => {
    const oldRevisionExecution = {
      ...execution,
      id: 'execution-old-revision',
      case_results: [{
        ...execution.case_results[0],
        endpoint_id: 'endpoint-old-revision',
        endpoint_stable_key: 'stable-favorites-list',
      }],
    }
    const unrelatedExecution = {
      ...execution,
      id: 'execution-unrelated',
      case_results: [{
        ...execution.case_results[0],
        endpoint_id: 'endpoint-unrelated',
        endpoint_stable_key: 'stable-unrelated',
      }],
    }
    const wrapper = mount(ExecutionConsole, {
      props: {
        executions: [oldRevisionExecution, unrelatedExecution],
        active: oldRevisionExecution,
        events: [],
        connectionState: 'complete',
        endpointId: 'endpoint-current-revision',
        endpointStableKey: 'stable-favorites-list',
      },
    })

    expect(wrapper.find('[data-testid="execution-row-execution-old-revision"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="execution-row-execution-unrelated"]').exists()).toBe(false)
  })
})
