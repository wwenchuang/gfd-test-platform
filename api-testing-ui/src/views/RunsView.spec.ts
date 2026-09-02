// @vitest-environment jsdom

import { createPinia, setActivePinia } from 'pinia'
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ExecutionView } from '../api/contracts'
import { useContextStore } from '../stores/context'
import { useExecutionsStore } from '../stores/executions'
import RunsView from './RunsView.vue'

const routeState = vi.hoisted(() => ({ query: {} as Record<string, string> }))
const routerState = vi.hoisted(() => ({ push: vi.fn(), replace: vi.fn() }))

vi.mock('vue-router', () => ({
  useRoute: () => routeState,
  useRouter: () => routerState,
}))

const debugExecution: ExecutionView = {
  id: 'debug-execution-1',
  project_id: 'project-1',
  state: 'DONE',
  execution_type: 'debug',
  source_revision_id: 'source-1',
  environment_revision_id: 'environment-1',
  environment_name: '生产环境（新）- 腾讯云',
  case_statuses: ['PASSED'],
  case_results: [],
  summary: { total: 1, passed: 1 },
  cancellation_requested: false,
  created_at: '2026-08-12T08:00:00Z',
  started_at: null,
  finished_at: null,
}

describe('RunsView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    routeState.query = {}
    routerState.push.mockReset()
    routerState.replace.mockReset()
  })

  it('does not show a global baseline run command for a selected debug execution', async () => {
    const context = useContextStore()
    const executions = useExecutionsStore()
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    vi.spyOn(executions, 'load').mockResolvedValue()
    executions.executions = [debugExecution]
    executions.active = debugExecution

    const wrapper = mount(RunsView, {
      global: {
        stubs: {
          ExecutionConsole: true,
          ExecutionDetailDrawer: true,
        },
      },
    })

    await Promise.resolve()

    expect(wrapper.find('[data-testid="run-baselines"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('执行当前基线')
  })

  it('clears the old detail before selecting the execution requested by the URL', async () => {
    routeState.query = { executionId: 'execution-new' }
    const context = useContextStore()
    const executions = useExecutionsStore()
    context.projectId = 'project-1'
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    vi.spyOn(executions, 'load').mockResolvedValue()
    const select = vi.spyOn(executions, 'select').mockResolvedValue()
    executions.active = debugExecution
    executions.events = [{ id: 1, type: 'response', level: 'info', caseId: '', message: '旧执行', payload: {} }]

    mount(RunsView, { global: { stubs: { ExecutionConsole: true, ExecutionDetailDrawer: true } } })

    expect(executions.active).toBeNull()
    expect(executions.events).toEqual([])
    await flushPromises()
    expect(select).toHaveBeenCalledWith('execution-new')
  })

  it('does not rerun a production execution until the user confirms the environment and scope', async () => {
    const context = useContextStore()
    const executions = useExecutionsStore()
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    vi.spyOn(executions, 'load').mockResolvedValue()
    const rerun = vi.spyOn(executions, 'rerunExecution').mockResolvedValue(null)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    executions.executions = [debugExecution]
    executions.active = debugExecution

    const wrapper = mount(RunsView, {
      global: {
        stubs: {
          ExecutionConsole: {
            props: ['active'],
            emits: ['rerun'],
            template: '<button data-testid="rerun" @click="$emit(\'rerun\', active)">重新执行</button>',
          },
          ExecutionDetailDrawer: true,
        },
      },
    })
    await flushPromises()

    await wrapper.get('[data-testid="rerun"]').trigger('click')
    await flushPromises()

    expect(rerun).not.toHaveBeenCalled()
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/生产环境.*重新执行.*真实发送/))
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('在线调试'))
    expect(confirm).not.toHaveBeenCalledWith(expect.stringContaining(debugExecution.id))
  })

  it('confirms before archiving execution history and offers an immediate restore', async () => {
    const context = useContextStore()
    const executions = useExecutionsStore()
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    vi.spyOn(executions, 'load').mockResolvedValue()
    const archive = vi.spyOn(executions, 'deleteExecutions').mockResolvedValue()
    const restore = vi.spyOn(executions, 'restoreExecutions').mockResolvedValue()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    executions.executions = [debugExecution]
    executions.active = debugExecution

    const wrapper = mount(RunsView, {
      global: {
        stubs: {
          ExecutionConsole: {
            emits: ['deleteMany'],
            template: '<button data-testid="archive" @click="$emit(\'deleteMany\', [\'debug-execution-1\'])">归档</button>',
          },
          ExecutionDetailDrawer: true,
        },
      },
    })
    await flushPromises()

    await wrapper.get('[data-testid="archive"]').trigger('click')
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/确认归档 1 条执行记录.*可以撤销/))
    expect(archive).not.toHaveBeenCalled()

    confirm.mockReturnValue(true)
    await wrapper.get('[data-testid="archive"]').trigger('click')
    await flushPromises()
    expect(archive).toHaveBeenCalledWith(['debug-execution-1'])
    expect(wrapper.text()).toContain('已归档 1 条执行记录')

    await wrapper.get('[data-testid="restore-archived-executions"]').trigger('click')
    await flushPromises()
    expect(restore).toHaveBeenCalledWith(['debug-execution-1'])
    expect(wrapper.text()).toContain('已恢复 1 条执行记录')
  })

  it('updates the URL to the new execution after rerunning a record', async () => {
    routeState.query = { executionId: debugExecution.id }
    const context = useContextStore()
    const executions = useExecutionsStore()
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    vi.spyOn(executions, 'load').mockResolvedValue()
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.spyOn(executions, 'rerunExecution').mockResolvedValue({
      ...debugExecution,
      id: 'debug-execution-rerun',
      state: 'QUEUED',
    })
    executions.executions = [debugExecution]
    executions.active = debugExecution

    const wrapper = mount(RunsView, {
      global: {
        stubs: {
          ExecutionConsole: {
            props: ['active'],
            emits: ['rerun'],
            template: '<button data-testid="rerun" @click="$emit(\'rerun\', active)">重新执行</button>',
          },
          ExecutionDetailDrawer: true,
        },
      },
    })
    await flushPromises()

    await wrapper.get('[data-testid="rerun"]').trigger('click')
    await flushPromises()

    expect(routerState.push).toHaveBeenLastCalledWith({
      name: 'runs',
      query: { executionId: 'debug-execution-rerun' },
    })
  })

  it('reruns only the selected failed case from case evidence', async () => {
    const failedCase = {
      execution_case_id: 'execution-case-failed', case_version_id: 'case-version-failed', endpoint_id: 'endpoint-1',
      case_name: '判断是否重新切片', endpoint_summary: '重新切片判断', method: 'GET', path: '/checkReslice',
      status: 'FAILED', failure_category: 'product_assertion', duration_ms: 719, sanitized_result: {},
    }
    const source = {
      ...debugExecution,
      id: 'baseline-execution-282',
      execution_type: 'baseline_regression',
      task_name: '家用业务基线回归（已复验282条）',
      case_statuses: ['FAILED'],
      case_results: [failedCase],
      summary: { total: 282, passed: 281, failed: 1 },
    } as ExecutionView
    const context = useContextStore()
    const executions = useExecutionsStore()
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    vi.spyOn(executions, 'load').mockResolvedValue()
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const createRerun = vi.spyOn(executions, 'createRerun').mockResolvedValue({
      ...source, id: 'partial-rerun-1', state: 'QUEUED',
    })
    executions.executions = [source]
    executions.active = source

    const wrapper = mount(RunsView, {
      global: {
        stubs: {
          ExecutionConsole: {
            props: ['active'],
            emits: ['rerunCase'],
            template: '<button data-testid="rerun-case" @click="$emit(\'rerunCase\', active.case_results[0], active)">重跑失败项</button>',
          },
          ExecutionDetailDrawer: true,
        },
      },
    })
    await flushPromises()

    await wrapper.get('[data-testid="rerun-case"]').trigger('click')
    await flushPromises()

    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/判断是否重新切片.*1 条用例/))
    expect(confirm).not.toHaveBeenCalledWith(expect.stringContaining('282 条用例'))
    expect(createRerun).toHaveBeenCalledWith(expect.objectContaining({ id: source.id }), ['case-version-failed'])
    expect(routerState.push).toHaveBeenLastCalledWith({
      name: 'runs', query: { executionId: 'partial-rerun-1' },
    })
  })

  it('opens the newest matching history record when filtering by a stable interface key', async () => {
    routeState.query = { endpointId: 'endpoint-current', endpointKey: 'stable-favorites-list' }
    const context = useContextStore()
    const executions = useExecutionsStore()
    context.projectId = 'project-1'
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    executions.executions = [{
      ...debugExecution,
      id: 'execution-old-revision',
      case_results: [{
        execution_case_id: 'case-1', case_version_id: 'version-1', endpoint_id: 'endpoint-old',
        endpoint_stable_key: 'stable-favorites-list', case_name: '查询收藏', endpoint_summary: '',
        method: 'POST', path: '/favorites/page', status: 'PASSED', failure_category: '', duration_ms: 10,
        sanitized_result: {},
      }],
    }]
    vi.spyOn(executions, 'load').mockResolvedValue()
    const select = vi.spyOn(executions, 'select').mockResolvedValue()

    mount(RunsView, { global: { stubs: { ExecutionConsole: true, ExecutionDetailDrawer: true } } })
    await flushPromises()

    expect(select).toHaveBeenCalledWith('execution-old-revision')
  })

  it('clears an unrelated detail before loading the first execution for an interface filter', async () => {
    routeState.query = { endpointId: 'endpoint-edit-ft' }
    const context = useContextStore()
    const executions = useExecutionsStore()
    context.projectId = 'project-1'
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    executions.executions = [{
      ...debugExecution,
      id: 'execution-matching-edit-ft',
      case_results: [{
        execution_case_id: 'case-edit-ft', case_version_id: 'version-edit-ft', endpoint_id: 'endpoint-edit-ft',
        endpoint_stable_key: 'stable-edit-ft', case_name: '编辑耗材', endpoint_summary: '',
        method: 'POST', path: '/devices/editFt', status: 'PASSED', failure_category: '', duration_ms: 10,
        sanitized_result: {},
      }],
    }]
    executions.active = { ...debugExecution, id: 'execution-unrelated-shared' }
    executions.events = [{ id: 1, type: 'response', level: 'info', caseId: '', message: '共享执行旧详情', payload: {} }]
    vi.spyOn(executions, 'load').mockResolvedValue()
    const select = vi.spyOn(executions, 'select').mockResolvedValue()

    mount(RunsView, { global: { stubs: { ExecutionConsole: true, ExecutionDetailDrawer: true } } })
    await flushPromises()

    expect(executions.active).toBeNull()
    expect(executions.events).toEqual([])
    expect(select).toHaveBeenCalledWith('execution-matching-edit-ft')
  })

  it('clears a restored detail immediately while an interface-scoped list is loading', async () => {
    routeState.query = { endpointId: 'endpoint-new-without-history' }
    const context = useContextStore()
    const executions = useExecutionsStore()
    context.projectId = 'project-1'
    executions.active = { ...debugExecution, id: 'execution-restored-from-previous-page' }
    executions.events = [{ id: 1, type: 'response', level: 'info', caseId: '', message: '旧执行详情', payload: {} }]
    vi.spyOn(context, 'loadSavedContext').mockImplementation(() => new Promise<void>(() => {}))
    vi.spyOn(context, 'loadOptions').mockImplementation(() => new Promise<void>(() => {}))

    mount(RunsView, { global: { stubs: { ExecutionConsole: true, ExecutionDetailDrawer: true } } })

    expect(executions.active).toBeNull()
    expect(executions.events).toEqual([])
    expect(executions.selectingExecutionId).toBe('')
  })
})
