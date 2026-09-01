// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiClient } from '../api/client'
import type { ApiEndpoint, ApiTestTask, CaseVersion, GeneratedCasePreview } from '../api/contracts'
import { useAssetsStore } from '../stores/assets'
import { useCasesStore } from '../stores/cases'
import { useContextStore } from '../stores/context'
import { useTasksStore } from '../stores/tasks'
import { replaceTestApplications } from '../utils/testApplications'
import WorkbenchView from './WorkbenchView.vue'

const ENDPOINT = {
  id: 'endpoint-1', method: 'POST', path: '/collection/page', summary: '我的收藏列表', tags: ['收藏'],
} as ApiEndpoint

describe('WorkbenchView debug workflow', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    replaceTestApplications([{
      package: 'com.kfb.model',
      name: '智小白3D',
      enabled: true,
      business_lines: [{ id: 'home', name: '家用', enabled: true }],
    }])
    vi.spyOn(useContextStore(), 'loadEnvironmentVariableNames').mockResolvedValue()
  })

  it('keeps restored task details hidden until the routed workspace context is ready', async () => {
    const context = useContextStore()
    let finishContext!: () => void
    vi.spyOn(context, 'loadSavedContext').mockImplementation(() => new Promise<void>(resolve => { finishContext = resolve }))
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const cases = useCasesStore()
    vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()

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
          EndpointTree: true,
        },
      },
    })

    expect(wrapper.get('[data-testid="workspace-restoring"]').text()).toContain('正在恢复上次工作区')
    expect(wrapper.findComponent({ name: 'TaskStatusStrip' }).exists()).toBe(false)

    finishContext()
    await flushPromises()

    expect(wrapper.find('[data-testid="workspace-restoring"]').exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'TaskStatusStrip' }).exists()).toBe(true)
  })

  it('stops workspace restoration after the initial service timeout and offers retry', async () => {
    const context = useContextStore()
    const loadSavedContext = vi.spyOn(context, 'loadSavedContext').mockImplementation(async () => {
      context.error = '服务响应超时（8 秒），请检查服务状态后重试'
    })
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const tasks = useTasksStore()
    const restoreTask = vi.spyOn(tasks, 'restore').mockResolvedValue(null)
    const assets = useAssetsStore()
    const loadAssets = vi.spyOn(assets, 'load').mockResolvedValue()

    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'workbench', component: WorkbenchView }] })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(WorkbenchView, {
      global: {
        plugins: [router],
        stubs: { ContextBar: true, TaskStatusStrip: true, EndpointDetail: true, CaseEditor: true, AiAssistant: true, DebugDrawer: true, EndpointTree: true },
      },
    })
    await flushPromises()

    expect(wrapper.find('[data-testid="workspace-restoring"]').exists()).toBe(false)
    expect(wrapper.get('[data-testid="workspace-restore-error"]').text()).toContain('服务响应超时')
    expect(wrapper.get('[data-testid="workspace-restore-retry"]').text()).toContain('重试恢复')
    expect(restoreTask).not.toHaveBeenCalled()
    expect(loadAssets).not.toHaveBeenCalled()
    expect(wrapper.findComponent({ name: 'TaskStatusStrip' }).exists()).toBe(false)

    await wrapper.get('[data-testid="workspace-restore-retry"]').trigger('click')
    await flushPromises()
    expect(loadSavedContext).toHaveBeenCalledTimes(2)
  })

  it('renders the workspace without waiting for prior AI job restoration', async () => {
    const context = useContextStore()
    Object.assign(context, {
      projectId: 'project-1', sourceRevisionId: 'source-1', environmentRevisionId: 'environment-1',
    })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const tasks = useTasksStore()
    vi.spyOn(tasks, 'restore').mockResolvedValue(null)
    const assets = useAssetsStore()
    vi.spyOn(assets, 'load').mockResolvedValue()
    const cases = useCasesStore()
    vi.spyOn(cases, 'loadSavedCases').mockResolvedValue()
    vi.spyOn(cases, 'restoreLatestAiJob').mockImplementation(() => new Promise<void>(() => {}))

    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'workbench', component: WorkbenchView }] })
    await router.push('/')
    await router.isReady()
    const wrapper = mount(WorkbenchView, {
      global: {
        plugins: [router],
        stubs: { ContextBar: true, TaskStatusStrip: true, EndpointDetail: true, CaseEditor: true, AiAssistant: true, DebugDrawer: true, EndpointTree: true },
      },
    })
    await flushPromises()

    expect(wrapper.find('[data-testid="workspace-restoring"]').exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'TaskStatusStrip' }).exists()).toBe(true)
  })

  it('shows the retry state when a routed case version cannot be restored', async () => {
    const context = useContextStore()
    Object.assign(context, {
      projectId: 'project-1', sourceRevisionId: 'source-1', environmentRevisionId: 'environment-1',
    })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const tasks = useTasksStore()
    vi.spyOn(tasks, 'restore').mockResolvedValue(null)
    const assets = useAssetsStore()
    vi.spyOn(assets, 'load').mockImplementation(async () => {
      assets.endpoints = [ENDPOINT]
      assets.state = 'ready'
    })
    const cases = useCasesStore()
    vi.spyOn(cases, 'loadSavedCases').mockResolvedValue()
    vi.spyOn(cases, 'loadVersion').mockRejectedValue(new Error('用例版本读取超时'))
    vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()

    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'workbench', component: WorkbenchView }] })
    await router.push('/?endpointId=endpoint-1&caseVersionId=version-missing')
    await router.isReady()
    const wrapper = mount(WorkbenchView, {
      global: {
        plugins: [router],
        stubs: { ContextBar: true, TaskStatusStrip: true, EndpointDetail: true, CaseEditor: true, AiAssistant: true, DebugDrawer: true, EndpointTree: true },
      },
    })
    await flushPromises()

    expect(wrapper.get('[data-testid="workspace-restore-error"]').text()).toContain('用例版本读取超时')
    expect(wrapper.findComponent({ name: 'TaskStatusStrip' }).exists()).toBe(false)
  })

  it('opens a direct new-task route without restoring the previous task', async () => {
    const context = useContextStore()
    Object.assign(context, {
      projectId: 'project-1', sourceRevisionId: 'source-1', environmentRevisionId: 'environment-1',
      projects: [{ id: 'project-1', name: '3D 家用' }],
    })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const tasks = useTasksStore()
    vi.spyOn(tasks, 'list').mockResolvedValue([])
    const restore = vi.spyOn(tasks, 'restore').mockResolvedValue(null)
    const assets = useAssetsStore()
    vi.spyOn(assets, 'load').mockResolvedValue()
    const cases = useCasesStore()
    vi.spyOn(cases, 'loadSavedCases').mockResolvedValue()
    vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()

    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'workbench', component: WorkbenchView }] })
    await router.push('/?newTask=1')
    await router.isReady()
    const wrapper = mount(WorkbenchView, {
      global: {
        plugins: [router],
        stubs: { ContextBar: true, EndpointDetail: true, CaseEditor: true, AiAssistant: true, DebugDrawer: true, EndpointTree: true },
      },
    })
    await flushPromises()

    expect(restore).not.toHaveBeenCalled()
    expect(tasks.task).toBeNull()
    expect(wrapper.find('[data-testid="task-name-input"]').exists()).toBe(false)
    expect(wrapper.get('.task-start-heading').text()).toContain('从一个接口开始')
    wrapper.findComponent({ name: 'EndpointTree' }).vm.$emit('selection-change', ['endpoint-1'])
    await flushPromises()
    expect(wrapper.get('[data-testid="task-name-input"]').element).toHaveProperty('value', '3D 家用新建任务')
  })

  it('opens the only endpoint in a restored task without another selection click', async () => {
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
    vi.spyOn(assets, 'load').mockImplementation(async () => {
      assets.endpoints = [ENDPOINT]
      assets.state = 'ready'
    })

    const cases = useCasesStore()
    vi.spyOn(cases, 'loadSavedCases').mockResolvedValue()
    vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()

    const restoredTask = {
      id: 'task-1', project_id: 'project-1', source_revision_id: 'source-1', environment_revision_id: 'environment-1',
      name: '单接口任务', state: 'draft', selected_endpoint_ids: [ENDPOINT.id], runnable_baseline_count: 0,
      latest_ai_job_id: null, latest_execution_id: null, summary: {}, created_at: '', updated_at: '',
    } as ApiTestTask
    const tasks = useTasksStore()
    vi.spyOn(tasks, 'list').mockResolvedValue([restoredTask])
    vi.spyOn(tasks, 'restore').mockResolvedValue(restoredTask)

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
          EndpointTree: {
            props: ['initialTab'],
            template: '<div data-testid="endpoint-tree-tab">{{ initialTab }}</div>',
          },
          CaseEditor: true,
          AiAssistant: true,
          DebugDrawer: true,
          EndpointDetail: {
            props: ['endpoint'],
            template: '<div data-testid="active-endpoint">{{ endpoint?.summary || "未选择" }}</div>',
          },
        },
      },
    })
    await flushPromises()

    expect(wrapper.get('[data-testid="active-endpoint"]').text()).toBe('我的收藏列表')
    expect(wrapper.get('[data-testid="endpoint-tree-tab"]').text()).toBe('selected')
  })

  it.each(['/', '/?projectId=project-1&sourceRevisionId=source-new&environmentRevisionId=environment-new'])('keeps the newly activated source and separates old tasks on %s', async (path) => {
    const context = useContextStore()
    Object.assign(context, {
      projectId: 'project-1', sourceRevisionId: 'source-new', environmentRevisionId: 'environment-new',
      projects: [{ id: 'project-1', name: '3D 家用' }],
      sourceRevisions: [
        { id: 'source-new', source_id: 'source-asset', project_id: 'project-1', name: '默认模块', revision_number: 9, endpoint_count: 1025 },
        { id: 'source-old', source_id: 'source-asset', project_id: 'project-1', name: '默认模块', revision_number: 8, endpoint_count: 1024 },
      ],
      environmentRevisions: [{ id: 'environment-new', project_id: 'project-1', name: '生产环境', revision: 28 }],
    })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const assets = useAssetsStore()
    const loadAssets = vi.spyOn(assets, 'load').mockImplementation(async revisionId => {
      assets.endpoints = revisionId === 'source-new' ? [{ ...ENDPOINT, id: 'endpoint-new' }] : [ENDPOINT]
      assets.state = 'ready'
    })
    const cases = useCasesStore()
    vi.spyOn(cases, 'loadSavedCases').mockResolvedValue()
    vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()
    const oldTask = {
      id: 'task-old', project_id: 'project-1', source_revision_id: 'source-old', environment_revision_id: 'environment-old',
      name: '旧版本任务', state: 'ready', selected_endpoint_ids: [ENDPOINT.id], runnable_baseline_count: 1,
      latest_ai_job_id: null, latest_execution_id: null, summary: {}, created_at: '', updated_at: '',
    } as ApiTestTask
    const tasks = useTasksStore()
    vi.spyOn(tasks, 'list').mockResolvedValue([oldTask])
    vi.spyOn(tasks, 'restore').mockResolvedValue(oldTask)

    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/', name: 'workbench', component: WorkbenchView },
      { path: '/tasks', name: 'tasks', component: { template: '<div />' } },
    ] })
    await router.push(path)
    await router.isReady()
    const wrapper = mount(WorkbenchView, {
      global: {
        plugins: [router],
        stubs: { ContextBar: true, TaskStatusStrip: true, EndpointDetail: true, CaseEditor: true, AiAssistant: true, DebugDrawer: true, EndpointTree: true },
      },
    })
    await flushPromises()

    expect(context.sourceRevisionId).toBe('source-new')
    expect(loadAssets).toHaveBeenCalledWith('source-new', expect.anything())
    expect(loadAssets).not.toHaveBeenCalledWith('source-old', expect.anything())
    expect(wrapper.get('[data-testid="source-version-mismatch"]').text()).toContain('旧版本任务')
    expect(wrapper.get('[data-testid="source-version-mismatch"]').text()).toContain('当前接口版本')
    await wrapper.get('[data-testid="source-version-mismatch"] a').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.name).toBe('tasks')
    expect(context.sourceRevisionId).toBe('source-new')
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
          CaseEditor: {
            name: 'CaseEditor',
            props: ['operationError'],
            emits: ['debug'],
            template: '<button data-testid="editor-debug" @click="$emit(\'debug\')">保存并调试</button>',
          },
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

    expect(wrapper.get('[data-testid="mobile-workbench-scope"]').attributes('aria-selected')).toBe('true')
    expect(wrapper.get('[data-testid="mobile-workbench-scope"]').attributes('aria-controls')).toBe('mobile-workbench-panel-scope')
    expect(wrapper.get('#mobile-workbench-panel-scope').attributes('role')).toBe('tabpanel')
    await wrapper.get('[data-testid="activate-endpoint"]').trigger('click')
    await flushPromises()
    expect(wrapper.get('[data-testid="mobile-workbench-editor"]').attributes('aria-selected')).toBe('true')
    await wrapper.get('[data-testid="mobile-workbench-ai"]').trigger('click')
    expect(wrapper.get('[data-testid="mobile-workbench-ai"]').attributes('aria-selected')).toBe('true')
    await wrapper.get('[data-testid="mobile-workbench-editor"]').trigger('click')
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await wrapper.get('[data-testid="editor-debug"]').trigger('click')
    await flushPromises()

    expect(prepare).not.toHaveBeenCalled()
    expect(debug).not.toHaveBeenCalled()
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/生产环境.*我的收藏列表.*真实发送/))

    confirm.mockReturnValue(true)
    await wrapper.get('[data-testid="editor-debug"]').trigger('click')
    await flushPromises()

    expect(prepare).toHaveBeenCalledWith(ENDPOINT.id, 'environment-1')
    expect(debug).toHaveBeenCalledWith(expect.objectContaining({
      caseVersionId: 'version-new', taskId: 'task-1', environmentRevisionId: 'environment-1',
    }))
    expect(prepare.mock.invocationCallOrder[0]).toBeLessThan(debug.mock.invocationCallOrder[0])

    vi.mocked(tasks.saveSelection).mockRejectedValueOnce(new Error('测试任务范围与当前请求不一致'))
    debug.mockClear()
    await wrapper.get('[data-testid="editor-debug"]').trigger('click')
    await flushPromises()
    expect(debug).not.toHaveBeenCalled()
    expect(wrapper.getComponent({ name: 'CaseEditor' }).props('operationError')).toContain('调试未开始')
    expect(wrapper.text()).toContain('测试任务范围与当前请求不一致')
  })

  it('debugs a deep-linked case without silently adding it to the restored saved task', async () => {
    const context = useContextStore()
    Object.assign(context, {
      projectId: 'project-1', sourceRevisionId: 'source-1', environmentRevisionId: 'environment-1',
      projects: [{ id: 'project-1', name: '3D 家用' }],
      sourceRevisions: [{ id: 'source-1', project_id: 'project-1', name: '默认模块', revision: 1 }],
      environmentRevisions: [{ id: 'environment-1', project_id: 'project-1', name: '生产环境', revision: 7 }],
    })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()

    const savedEndpoint = { ...ENDPOINT, id: 'endpoint-saved' }
    const editedEndpoint = { ...ENDPOINT, id: 'endpoint-edited', path: '/guidance/finishMark', summary: '新手必学是否完成' }
    const assets = useAssetsStore()
    vi.spyOn(assets, 'load').mockImplementation(async () => {
      assets.endpoints = [savedEndpoint, editedEndpoint]
      assets.state = 'ready'
    })
    vi.spyOn(assets, 'ensureEndpointDetail').mockImplementation(async endpointId => (
      assets.endpoints.find(item => item.id === endpointId) || null
    ))

    const cases = useCasesStore()
    vi.spyOn(cases, 'loadSavedCases').mockResolvedValue()
    vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()
    vi.spyOn(cases, 'loadVersion').mockResolvedValue()
    vi.spyOn(cases, 'saveForDebug').mockResolvedValue({ id: 'version-edited-next', endpoint_id: editedEndpoint.id } as CaseVersion)
    const debug = vi.spyOn(cases, 'debug').mockResolvedValue()

    const restoredTask = {
      id: 'task-saved', name: '3D家用接口测试', project_id: 'project-1', source_revision_id: 'source-1',
      environment_revision_id: 'environment-1', selected_endpoint_ids: [savedEndpoint.id], state: 'ready',
      runnable_baseline_count: 1, latest_ai_job_id: null, latest_execution_id: null,
      summary: {}, created_at: '', updated_at: '',
    } as ApiTestTask
    const tasks = useTasksStore()
    vi.spyOn(tasks, 'restore').mockImplementation(async () => {
      tasks.task = restoredTask
      tasks.tasks = [restoredTask]
      return restoredTask
    })
    const saveSelection = vi.spyOn(tasks, 'saveSelection')
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/', name: 'workbench', component: WorkbenchView }] })
    await router.push(`/?endpointId=${editedEndpoint.id}&caseVersionId=version-edited&projectId=project-1&sourceRevisionId=source-1&environmentRevisionId=environment-1`)
    await router.isReady()
    const wrapper = mount(WorkbenchView, {
      global: {
        plugins: [router],
        stubs: {
          ContextBar: true,
          TaskStatusStrip: true,
          EndpointDetail: true,
          CaseEditor: {
            name: 'CaseEditor', props: ['operationError'], emits: ['debug'],
            template: '<button data-testid="editor-debug" @click="$emit(\'debug\')">保存并调试</button>',
          },
          AiAssistant: true,
          DebugDrawer: true,
          EndpointTree: true,
        },
      },
    })
    await flushPromises()

    expect(wrapper.get('[data-testid="standalone-case-edit-note"]').text()).toContain('不会更改当前任务的接口范围')
    await wrapper.get('[data-testid="editor-debug"]').trigger('click')
    await flushPromises()

    expect(saveSelection).not.toHaveBeenCalled()
    expect(tasks.task?.selected_endpoint_ids).toEqual([savedEndpoint.id])
    expect(debug).toHaveBeenCalledWith(expect.objectContaining({
      caseVersionId: 'version-edited-next', taskId: undefined,
    }))
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

  it('previews basic positive cases against the exact saved task scope before saving', async () => {
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
    const basicPreview = generatedPreview('basic-positive-endpoint-1', ENDPOINT.id, '我的收藏列表 - 基础正向流程')
    const previewBasic = vi.spyOn(cases, 'previewBasicPositive').mockImplementation(async () => {
      cases.generatedPreviews = [basicPreview]
      return [basicPreview]
    })

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
            emits: ['generate-basic'],
            template: '<button data-testid="generate-basic" @click="$emit(\'generate-basic\')">基础生成</button>',
          },
        },
      },
    })
    await flushPromises()

    await wrapper.get('[data-testid="select-endpoint"]').trigger('click')
    await wrapper.get('[data-testid="generate-basic"]').trigger('click')
    await flushPromises()

    expect(previewBasic).toHaveBeenCalledWith([ENDPOINT.id], 'environment-1', 'task-1')
    expect(cases.activeGeneratedPreviewId).toBe('basic-positive-endpoint-1')
    expect(cases.activeVersionByEndpoint[ENDPOINT.id]).toBeUndefined()
    expect(cases.drafts[ENDPOINT.id].name).toBe('我的收藏列表 - 基础正向流程')
  })

  it('saves the active generated preview from the editor instead of creating a manual case', async () => {
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

    const cases = useCasesStore()
    const preview = generatedPreview('basic-positive-endpoint-1', ENDPOINT.id, '我的收藏列表 - 基础正向流程')
    cases.generatedPreviews = [preview]
    vi.spyOn(cases, 'loadSavedCases').mockResolvedValue()
    vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()
    const savePreview = vi.spyOn(cases, 'saveGeneratedPreview').mockResolvedValue(savedCase('version-basic', 'case-basic', '我的收藏列表 - 基础正向流程', {}))
    const saveManual = vi.spyOn(cases, 'save')

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
    cases.setDraftFromGeneratedPreview(preview.id)
    await flushPromises()
    await wrapper.get('[data-testid="case-name"]').setValue('我的收藏列表 - 编辑后')
    await wrapper.get('[data-testid="save-case-draft"]').trigger('click')
    await flushPromises()

    expect(savePreview).toHaveBeenCalledWith('basic-positive-endpoint-1', expect.objectContaining({ name: '我的收藏列表 - 编辑后' }))
    expect(saveManual).not.toHaveBeenCalled()
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
    expect(loadAssets).toHaveBeenCalledWith('source-2', expect.anything())
    expect(listTasks).not.toHaveBeenCalled()
    expect(context.loadEnvironmentVariableNames).toHaveBeenCalledWith('env-2', expect.any(ApiClient))
  })

  it('keeps full task and case management lists out of the workbench', async () => {
    const context = useContextStore()
    Object.assign(context, {
      projectId: null,
      sourceRevisionId: null,
      environmentRevisionId: null,
      projects: [],
      sourceRevisions: [],
      environmentRevisions: [],
    })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()

    const cases = useCasesStore()
    vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()
    const tasks = useTasksStore()
    vi.spyOn(tasks, 'restore').mockResolvedValue(null)

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'workbench', component: WorkbenchView },
      ],
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
          EndpointTree: true,
        },
      },
    })
    await flushPromises()

    expect(wrapper.find('.task-list-panel').exists()).toBe(false)
    expect(wrapper.find('.case-list-panel').exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'EndpointTree' }).exists()).toBe(true)
  })

  it('keeps a historical task scope while running it with the selected runtime environment', async () => {
    const context = useContextStore()
    Object.assign(context, {
      projectId: 'project-1',
      sourceRevisionId: null,
      environmentRevisionId: null,
      projects: [{ id: 'project-1', name: '3D 家用' }],
      sourceRevisions: [
        { id: 'source-current', source_id: 'source-current', project_id: 'project-1', name: '默认模块', revision_number: 9, endpoint_count: 999 },
      ],
      environmentRevisions: [
        { id: 'environment-current', environment_id: 'environment-current', project_id: 'project-1', name: '生产环境（新）', revision: 9 },
      ],
    })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    vi.spyOn(context, 'saveContext').mockResolvedValue()

    const assets = useAssetsStore()
    vi.spyOn(assets, 'load').mockImplementation(async () => {
      assets.endpoints = [ENDPOINT]
      assets.state = 'ready'
    })

    const cases = useCasesStore()
    vi.spyOn(cases, 'loadSavedCases').mockResolvedValue()
    vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()

    const historicalTask = {
      id: 'task-old',
      project_id: 'project-1',
      source_revision_id: 'source-old',
      environment_revision_id: 'environment-old',
      name: '3D 家用基线回归',
      state: 'ready',
      selected_endpoint_ids: [ENDPOINT.id],
      runnable_baseline_count: 1,
      latest_ai_job_id: null,
      latest_execution_id: null,
      summary: { task_type: 'baseline' },
      created_at: '',
      updated_at: '',
    } as ApiTestTask
    const tasks = useTasksStore()
    vi.spyOn(tasks, 'list').mockImplementation(async () => {
      tasks.tasks = [historicalTask]
      return tasks.tasks
    })
    vi.spyOn(tasks, 'restore').mockResolvedValue(null)
    const saveSelection = vi.spyOn(tasks, 'saveSelection').mockResolvedValue(historicalTask)
    const run = vi.spyOn(tasks, 'runCurrent').mockResolvedValue({ id: 'execution-1' } as never)

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', name: 'workbench', component: WorkbenchView },
        { path: '/runs', name: 'runs', component: { template: '<div />' } },
      ],
    })
    await router.push('/?taskId=task-old')
    await router.isReady()
    const wrapper = mount(WorkbenchView, {
      global: {
        plugins: [router],
        stubs: {
          EndpointDetail: true,
          CaseEditor: true,
          AiAssistant: true,
          DebugDrawer: true,
          EndpointTree: {
            props: ['selectedIds'],
            emits: ['selection-change'],
            template: '<div><button type="button" data-testid="endpoint-tree" @click="$emit(\'selection-change\', [\'endpoint-1\', \'endpoint-2\'])">{{ selectedIds.length }}</button><button type="button" data-testid="restore-selection" @click="$emit(\'selection-change\', [\'endpoint-1\'])">恢复范围</button></div>',
          },
        },
      },
    })
    await flushPromises()

    expect(context.sourceRevisionId).toBe('source-old')
    expect(context.environmentRevisionId).toBe('environment-old')
    expect((wrapper.get('[data-testid="context-source"]').element as HTMLSelectElement).value).toBe('source-old')
    expect((wrapper.get('[data-testid="context-environment"]').element as HTMLSelectElement).value).toBe('environment-old')
    expect(wrapper.get('.task-status-strip').text()).toContain('任务保存环境')

    await wrapper.get('[data-testid="context-environment"]').setValue('environment-current')
    await flushPromises()

    expect(context.environmentRevisionId).toBe('environment-current')
    expect(wrapper.get('.task-status-strip').text()).toContain('生产环境（新）')

    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await wrapper.get('[data-testid="endpoint-tree"]').trigger('click')
    await wrapper.get('[data-testid="run-task"]').trigger('click')
    await flushPromises()

    expect(saveSelection).not.toHaveBeenCalled()
    expect(run).not.toHaveBeenCalled()
    expect(confirm).not.toHaveBeenCalled()
    expect(wrapper.get('[data-testid="run-task"]').attributes('disabled')).toBeDefined()
    expect(wrapper.get('[data-testid="task-next-step"]').text()).toContain('更新任务接口')

    await wrapper.get('[data-testid="restore-selection"]').trigger('click')
    await flushPromises()
    confirm.mockReturnValue(true)
    await wrapper.get('[data-testid="run-task"]').trigger('click')
    await flushPromises()

    expect(run).toHaveBeenCalledWith('environment-current')
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/生产环境.*3D 家用基线回归.*真实发送/))
    expect(router.currentRoute.value.name).toBe('runs')
    expect(router.currentRoute.value.query.executionId).toBe('execution-1')
  })

})

function savedCase(id: string, caseId: string, name: string, headers: Record<string, unknown>): CaseVersion {
  return {
    id, case_id: caseId, endpoint_id: ENDPOINT.id, status: 'draft', origin: 'ai', version: 1, group_name: '',
    validation_summary: {}, name, purpose: name, priority: 'P1',
    app_package: 'com.kfb.model', app_name: '智小白3D', business: 'home',
    request: { method: 'POST', path: '/collection/add', service: 'default', path_params: {}, query: {}, headers, cookies: {}, body: { modelSn: 'm001' } },
    data_rows: [], assertions: [], extractions: [], dependencies: [], processing: { pre: [], post: [] },
  }
}

function generatedPreview(id: string, endpointId: string, name: string): GeneratedCasePreview {
  return {
    id,
    endpoint_id: endpointId,
    origin: 'imported',
    case: {
      name,
      purpose: name,
      app_package: 'com.kfb.model',
      app_name: '智小白3D',
      business: 'home',
      priority: 'P1',
      request: { method: 'POST', path: '/collection/page', service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: { pageNum: 1 } },
      data_rows: [],
      assertions: [],
      extractions: [],
      dependencies: [],
      processing: { pre: [], post: [] },
    },
  }
}
