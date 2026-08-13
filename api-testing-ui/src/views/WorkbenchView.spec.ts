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
    vi.spyOn(tasks, 'list').mockResolvedValue([])
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

  it('reloads the editor when another saved case is selected', async () => {
    const context = useContextStore()
    Object.assign(context, {
      projectId: 'project-1', sourceRevisionId: 'source-1', environmentRevisionId: 'environment-1',
      projects: [{ id: 'project-1', name: '3D 家用' }],
      sourceRevisions: [{ id: 'source-1', project_id: 'project-1', name: '默认模块', revision: 1 }],
      environmentRevisions: [{ id: 'environment-1', project_id: 'project-1', name: '生产环境', revision: 7 }],
    })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()

    const assets = useAssetsStore()
    assets.endpoints = [ENDPOINT]
    assets.state = 'ready'
    vi.spyOn(assets, 'load').mockResolvedValue()

    const missingBiz = savedCase('version-missing-biz', 'case-missing-biz', '添加收藏 - 缺失 Biz', {})
    const normal = savedCase('version-normal', 'case-normal', '添加收藏 - 正常流程', { Biz: '{{Biz}}' })
    const cases = useCasesStore()
    cases.registerVersion(missingBiz)
    cases.registerVersion(normal, false)
    vi.spyOn(cases, 'loadSavedCases').mockResolvedValue()
    vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()

    const tasks = useTasksStore()
    vi.spyOn(tasks, 'list').mockResolvedValue([])
    vi.spyOn(tasks, 'restore').mockResolvedValue(null)

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
    expect((wrapper.get('[data-testid="case-name"]').element as HTMLInputElement).value).toBe('添加收藏 - 缺失 Biz')
    cases.debugResult = {
      status: 'PASSED', executionCaseId: 'old-evidence', resolvedRequest: {},
      sanitizedResponse: {}, assertions: [], failureCategory: '', logs: [],
    }

    await wrapper.get('.case-version-picker select').setValue('version-normal')
    await flushPromises()

    expect((wrapper.get('[data-testid="case-name"]').element as HTMLInputElement).value).toBe('添加收藏 - 正常流程')
    expect((wrapper.get('[data-testid="headers-name"]').element as HTMLInputElement).value).toBe('Biz')
    expect(cases.debugResult).toBeNull()
  })

  it('generates cases against the exact saved task scope', async () => {
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
    cases.aiError = '旧的失败'
    vi.spyOn(cases, 'loadSavedCases').mockResolvedValue()
    vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()
    const generate = vi.spyOn(cases, 'generate').mockResolvedValue()

    const tasks = useTasksStore()
    vi.spyOn(tasks, 'list').mockResolvedValue([])
    vi.spyOn(tasks, 'restore').mockResolvedValue(null)
    vi.spyOn(tasks, 'saveSelection').mockResolvedValue({
      id: 'task-1',
      name: '3D 家用接口测试',
      project_id: 'project-1',
      source_revision_id: 'source-1',
      environment_revision_id: 'environment-1',
      selected_endpoint_ids: [ENDPOINT.id],
      state: 'draft',
      runnable_baseline_count: 0,
      latest_ai_job_id: null,
      latest_execution_id: null,
      summary: {},
      created_at: '',
      updated_at: '',
    })

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
          DebugDrawer: true,
          EndpointTree: {
            props: ['endpoints'],
            emits: ['activate', 'selection-change'],
            template: '<button data-testid="select-endpoint" @click="$emit(\'selection-change\', [endpoints[0].id]); $emit(\'activate\', endpoints[0])">选择接口</button>',
          },
          AiAssistant: {
            emits: ['generate'],
            template: '<button data-testid="generate-cases" @click="$emit(\'generate\', \'覆盖正常流程\')">生成</button>',
          },
        },
      },
    })
    await flushPromises()

    await wrapper.get('[data-testid="select-endpoint"]').trigger('click')
    await wrapper.get('[data-testid="generate-cases"]').trigger('click')
    await flushPromises()

    expect(generate).toHaveBeenCalledWith([ENDPOINT.id], 'environment-1', '覆盖正常流程', 'task-1')
    expect(cases.aiError).toBe('')
  })

  it('uses project, source revision, and environment from the asset page route', async () => {
    const context = useContextStore()
    Object.assign(context, {
      projectId: 'old-project',
      sourceRevisionId: 'old-source',
      environmentRevisionId: 'old-env',
      projects: [
        { id: 'old-project', name: '旧项目' },
        { id: 'project-2', name: '打印后台' },
      ],
      sourceRevisions: [
        { id: 'old-source', project_id: 'old-project', name: '旧版本', revision: 1 },
        { id: 'source-2', project_id: 'project-2', name: '后台模块', revision: 2 },
      ],
      environmentRevisions: [
        { id: 'old-env', project_id: 'old-project', name: '旧环境', revision: 1 },
        { id: 'env-2', project_id: 'project-2', name: '后台环境', revision: 3 },
      ],
    })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()

    const assets = useAssetsStore()
    const loadAssets = vi.spyOn(assets, 'load').mockResolvedValue()
    const cases = useCasesStore()
    vi.spyOn(cases, 'loadSavedCases').mockResolvedValue()
    vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()

    const tasks = useTasksStore()
    const listTasks = vi.spyOn(tasks, 'list').mockResolvedValue([])
    vi.spyOn(tasks, 'restore').mockResolvedValue(null)

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/', name: 'workbench', component: WorkbenchView }],
    })
    await router.push('/?projectId=project-2&sourceRevisionId=source-2&environmentRevisionId=env-2')
    await router.isReady()
    mount(WorkbenchView, {
      global: {
        plugins: [router],
        stubs: {
          ContextBar: true,
          TaskStatusStrip: true,
          EndpointDetail: true,
          CaseEditor: true,
          AiAssistant: true,
          DebugDrawer: true,
          EndpointTree: true,
        },
      },
    })
    await flushPromises()

    expect(context.projectId).toBe('project-2')
    expect(context.sourceRevisionId).toBe('source-2')
    expect(context.environmentRevisionId).toBe('env-2')
    expect(loadAssets).toHaveBeenCalledWith('source-2')
    expect(listTasks).toHaveBeenCalledWith('project-2')
  })
})

function savedCase(id: string, caseId: string, name: string, headers: Record<string, unknown>): CaseVersion {
  return {
    id, case_id: caseId, endpoint_id: ENDPOINT.id, status: 'draft', origin: 'ai', version: 1,
    validation_summary: {}, name, purpose: name, priority: 'P1',
    request: { method: 'POST', path: '/collection/add', service: 'default', path_params: {}, query: {}, headers, cookies: {}, body: { modelSn: 'm001' } },
    data_rows: [], assertions: [], extractions: [], dependencies: [], processing: { pre: [], post: [] },
  }
}
