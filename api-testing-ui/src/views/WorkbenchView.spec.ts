// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ApiEndpoint, CaseVersion } from '../api/contracts'
import { useAssetsStore } from '../stores/assets'
import { useCasesStore } from '../stores/cases'
import { useContextStore } from '../stores/context'
import { useTasksStore } from '../stores/tasks'
import WorkbenchView from './WorkbenchView.vue'

const ENDPOINT = {
  id: 'endpoint-1', method: 'POST', path: '/collection/page', summary: '我的收藏列表', tags: ['收藏'],
} as ApiEndpoint

describe('WorkbenchView debug workflow', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  it('saves the current draft and debugs the exact version returned by that save', async () => {
    const context = useContextStore()
    Object.assign(context, {
      projectId: 'project-1', sourceRevisionId: 'source-1', environmentRevisionId: 'environment-1',
      projects: [{ id: 'project-1', name: '3D 家用' }],
      sourceRevisions: [{ id: 'source-1', project_id: 'project-1', name: '默认模块', revision: 1 }],
      environmentRevisions: [{ id: 'environment-1', project_id: 'project-1', name: '生产环境', revision: 7 }],
    })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    vi.spyOn(context, 'saveContext').mockResolvedValue()

    const assets = useAssetsStore()
    assets.endpoints = [ENDPOINT]
    assets.state = 'ready'
    vi.spyOn(assets, 'load').mockResolvedValue()

    const cases = useCasesStore()
    vi.spyOn(cases, 'loadSavedCases').mockResolvedValue()
    vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()
    const savedVersion = { id: 'version-new', endpoint_id: ENDPOINT.id, version: 2 } as CaseVersion
    const prepare = vi.spyOn(cases, 'saveForDebug').mockResolvedValue(savedVersion)
    const debug = vi.spyOn(cases, 'debug').mockResolvedValue()

    const tasks = useTasksStore()
    vi.spyOn(tasks, 'restore').mockResolvedValue(null)
    vi.spyOn(tasks, 'saveSelection').mockResolvedValue({ id: 'task-1', name: '3D 家用接口测试' } as never)

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', name: 'workbench', component: WorkbenchView }],
    })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(WorkbenchView, {
      global: {
        plugins: [router],
        stubs: {
          ContextBar: true,
          TaskStatusStrip: true,
          EndpointDetail: true,
          CaseEditor: true,
          AiAssistant: true,
          DebugDrawer: true,
          EndpointTree: {
            props: ['endpoints'],
            emits: ['activate'],
            template: '<button data-testid="activate-endpoint" @click="$emit(\'activate\', endpoints[0])">选择接口</button>',
          },
        },
      },
    })
    await flushPromises()

    await wrapper.get('[data-testid="activate-endpoint"]').trigger('click')
    await wrapper.get('.debug-command').trigger('click')
    await flushPromises()

    expect(prepare).toHaveBeenCalledWith(ENDPOINT.id, 'environment-1')
    expect(debug).toHaveBeenCalledWith(expect.objectContaining({
      caseVersionId: 'version-new', taskId: 'task-1', environmentRevisionId: 'environment-1',
    }))
    expect(prepare.mock.invocationCallOrder[0]).toBeLessThan(debug.mock.invocationCallOrder[0])
  })
})
