// @vitest-environment jsdom

import { createPinia, setActivePinia } from 'pinia'
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ExecutionView } from '../api/contracts'
import { useContextStore } from '../stores/context'
import { useExecutionsStore } from '../stores/executions'
import RunsView from './RunsView.vue'

vi.mock('vue-router', () => ({
  useRoute: () => ({ query: {} }),
  useRouter: () => ({ push: vi.fn() }),
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
})
