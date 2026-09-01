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
})
