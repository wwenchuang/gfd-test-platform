// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { apiClient } from '../api/client'
import type { ApiEndpoint, CaseVersion } from '../api/contracts'
import CaseListPanel from '../components/CaseListPanel.vue'
import { useAssetsStore } from '../stores/assets'
import { useCasesStore } from '../stores/cases'
import { useContextStore } from '../stores/context'
import { useTasksStore } from '../stores/tasks'
import { replaceTestApplications } from '../utils/testApplications'
import CasesView from './CasesView.vue'

const ENDPOINT: ApiEndpoint = {
  id: 'endpoint-1',
  method: 'POST',
  path: '/collection/page',
  summary: '我的收藏列表',
  tags: ['家用业务', 'app接口', '我的', '我的收藏'],
}

describe('CasesView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
    replaceTestApplications([{
      package: 'com.kfb.model',
      name: '智小白3D',
      enabled: true,
      business_lines: [{ id: 'home', name: '家用', enabled: true }],
    }])
  })

  it('does not open an old manual draft after switching projects during detail loading', async () => {
    const context = useContextStore()
    Object.assign(context, { projectId: 'old-project', sourceRevisionId: 'old-source' })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue(); vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const assets = useAssetsStore(); assets.endpoints = [ENDPOINT]; assets.state = 'ready'
    vi.spyOn(assets, 'load').mockResolvedValue()
    const lightweight = { ...ENDPOINT, operation: {} }
    assets.endpoints = [lightweight]
    let finishOld!: (result: any) => void
    vi.spyOn(apiClient, 'get').mockImplementationOnce(() => new Promise(resolve => { finishOld = resolve }) as never)
    const cases = useCasesStore(); vi.spyOn(cases, 'loadSavedCases').mockResolvedValue(); vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()
    const tasks = useTasksStore(); vi.spyOn(tasks, 'list').mockResolvedValue([]); vi.spyOn(tasks, 'restore').mockResolvedValue(null)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/cases', name: 'cases', component: CasesView }] })
    await router.push('/cases'); await router.isReady()
    const wrapper = mount(CasesView, { global: { plugins: [router], stubs: {
      ContextBar: { name: 'ContextBar', emits: ['update:projectId'], template: '<div />' },
      EndpointDetail: true, DebugDrawer: true,
      CaseEndpointPicker: { name: 'CaseEndpointPicker', emits: ['create-manual'], template: '<div />' },
    } } })
    await flushPromises()
    await wrapper.get('[data-testid="open-case-endpoint-picker"]').trigger('click')
    wrapper.findComponent({ name: 'CaseEndpointPicker' }).vm.$emit('create-manual', lightweight)
    wrapper.findComponent({ name: 'ContextBar' }).vm.$emit('update:projectId', 'new-project')
    await flushPromises()
    finishOld({ data: { endpoint: { ...lightweight, operation: { description: 'old' } } } }); await flushPromises()
    expect(cases.drafts[ENDPOINT.id]).toBeUndefined()
    expect(wrapper.findComponent({ name: 'EndpointDetail' }).props('endpoint')).toBeNull()
    wrapper.unmount()
  })

  it('does not open a manual draft after the endpoint picker is closed', async () => {
    const context = useContextStore()
    Object.assign(context, { projectId: 'old-project', sourceRevisionId: 'old-source' })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue(); vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const assets = useAssetsStore(); assets.endpoints = [ENDPOINT]; assets.state = 'ready'
    vi.spyOn(assets, 'load').mockResolvedValue()
    let finishOld!: (endpoint: ApiEndpoint) => void
    vi.spyOn(assets, 'ensureEndpointDetail').mockImplementation(() => new Promise(resolve => { finishOld = resolve }))
    const cases = useCasesStore(); vi.spyOn(cases, 'loadSavedCases').mockResolvedValue(); vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()
    const tasks = useTasksStore(); vi.spyOn(tasks, 'list').mockResolvedValue([]); vi.spyOn(tasks, 'restore').mockResolvedValue(null)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/cases', name: 'cases', component: CasesView }] })
    await router.push('/cases'); await router.isReady()
    const wrapper = mount(CasesView, { global: { plugins: [router], stubs: {
      ContextBar: { name: 'ContextBar', emits: ['update:projectId'], template: '<div />' },
      EndpointDetail: true, DebugDrawer: true,
      CaseEndpointPicker: { name: 'CaseEndpointPicker', emits: ['create-manual', 'close'], template: '<div />' },
    } } })
    await flushPromises()
    await wrapper.get('[data-testid="open-case-endpoint-picker"]').trigger('click')
    wrapper.findComponent({ name: 'CaseEndpointPicker' }).vm.$emit('create-manual', ENDPOINT)
    wrapper.findComponent({ name: 'CaseEndpointPicker' }).vm.$emit('close')
    await flushPromises()
    finishOld(ENDPOINT); await flushPromises()
    expect(cases.drafts[ENDPOINT.id]).toBeUndefined()
    expect(wrapper.findComponent({ name: 'EndpointDetail' }).props('endpoint')).toBeNull()
    wrapper.unmount()
  })

  it('does not restore AI history after the environment changes while loading a source', async () => {
    const context = useContextStore()
    Object.assign(context, { projectId: 'p', sourceRevisionId: 'initial', environmentRevisionId: 'e' })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue(); vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const assets = useAssetsStore()
    let finishOld!: (value: undefined) => void
    vi.spyOn(assets, 'load').mockImplementation(async source => {
      if (source === 'old') return new Promise<void>(resolve => { finishOld = resolve })
      assets.endpoints = [ENDPOINT]; assets.state = 'ready'
    })
    const cases = useCasesStore(); vi.spyOn(cases, 'loadSavedCases').mockResolvedValue()
    const restoreAi = vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()
    const tasks = useTasksStore(); vi.spyOn(tasks, 'list').mockResolvedValue([]); vi.spyOn(tasks, 'restore').mockResolvedValue(null)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/cases', name: 'cases', component: CasesView }] })
    await router.push('/cases'); await router.isReady()
    const wrapper = mount(CasesView, { global: { plugins: [router], stubs: {
      ContextBar: { emits: ['update:sourceRevisionId', 'update:environmentRevisionId'], template: `<div><button data-testid="old" @click="$emit('update:sourceRevisionId', 'old')">old</button><button data-testid="env" @click="$emit('update:environmentRevisionId', 'new-environment')">env</button></div>` },
      EndpointDetail: true, DebugDrawer: true,
    } } })
    await flushPromises()
    await wrapper.get('[data-testid="old"]').trigger('click')
    await wrapper.get('[data-testid="env"]').trigger('click')
    finishOld(undefined); await flushPromises()
    expect(restoreAi).not.toHaveBeenCalledWith('p', 'old')
    expect(context.environmentRevisionId).toBe('new-environment')
    wrapper.unmount()
  })

  it('shows an endpoint fetch failure instead of presenting an empty case list', async () => {
    const context = useContextStore()
    context.projectId = 'project-1'
    context.sourceRevisionId = 'source-1'
    context.projects = [{ id: 'project-1', name: '3D 家用' }]
    context.sourceRevisions = [{ id: 'source-1', source_id: 'source-1', project_id: 'project-1', name: '默认模块', revision_number: 1, endpoint_count: 1 }]
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const assets = useAssetsStore()
    vi.spyOn(assets, 'load').mockImplementation(async () => {
      assets.state = 'failed'
      assets.error = '接口服务返回 503'
      assets.endpoints = []
    })
    const cases = useCasesStore()
    vi.spyOn(cases, 'loadSavedCases').mockResolvedValue()
    vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()
    const tasks = useTasksStore()
    vi.spyOn(tasks, 'list').mockResolvedValue([])
    vi.spyOn(tasks, 'restore').mockResolvedValue(null)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/cases', name: 'cases', component: CasesView }] })
    await router.push('/cases')
    await router.isReady()
    const wrapper = mount(CasesView, { global: { plugins: [router], stubs: { ContextBar: true, EndpointDetail: true, DebugDrawer: true } } })
    await flushPromises()

    expect(wrapper.get('.inline-error').text()).toContain('接口服务返回 503')
  })

  it('keeps each load error until that resource recovers without hiding unrelated failures', async () => {
    const context = useContextStore()
    Object.assign(context, {
      projectId: 'project-1', sourceRevisionId: 'source-1', environmentRevisionId: 'environment-1',
      projects: [{ id: 'project-1', name: '3D 家用' }],
      sourceRevisions: [{ id: 'source-1', source_id: 'source-1', project_id: 'project-1', name: '默认模块', revision_number: 1, endpoint_count: 1 }],
    })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    const assets = useAssetsStore()
    vi.spyOn(assets, 'load').mockImplementation(async () => { assets.state = 'ready'; assets.error = ''; assets.endpoints = [ENDPOINT] })
    const cases = useCasesStore()
    let finishRetry!: () => void
    vi.spyOn(cases, 'loadSavedCases').mockRejectedValueOnce(new Error('用例服务 502')).mockImplementationOnce(() => new Promise<void>(resolve => { finishRetry = resolve }))
    vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()
    const tasks = useTasksStore()
    vi.spyOn(tasks, 'list').mockResolvedValue([])
    vi.spyOn(tasks, 'restore').mockResolvedValue(null)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/cases', name: 'cases', component: CasesView }] })
    await router.push('/cases'); await router.isReady()
    const wrapper = mount(CasesView, { global: { plugins: [router], stubs: { ContextBar: true, EndpointDetail: true, DebugDrawer: true } } })
    await flushPromises()
    expect(wrapper.text()).toContain('用例服务 502')
    tasks.error = '任务状态 503'
    context.error = '工作区 504'
    wrapper.findComponent(CaseListPanel).vm.$emit('run-version', {
      ...savedCase('missing-version', '不存在的用例'), endpoint_id: 'missing-endpoint',
    })
    await flushPromises()
    expect(wrapper.text()).toContain('该用例对应的接口不在当前接口版本中')
    await wrapper.get('button[title="重新读取用例"]').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('用例服务 502')
    finishRetry()
    await flushPromises()
    expect(wrapper.text()).not.toContain('用例服务 502')
    expect(wrapper.text()).toContain('任务状态 503')
    expect(wrapper.text()).toContain('工作区 504')
    expect(wrapper.text()).toContain('该用例对应的接口不在当前接口版本中')
    expect(wrapper.text()).toContain('任务信息')
    expect(wrapper.text()).toContain('项目与环境')
  })

  it('retries only the failed task read and keeps the other task error visible', async () => {
    const context = useContextStore()
    Object.assign(context, { projectId: 'project-1', sourceRevisionId: 'source-1' })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()
    vi.spyOn(useAssetsStore(), 'load').mockResolvedValue()
    const cases = useCasesStore(); vi.spyOn(cases, 'loadSavedCases').mockResolvedValue(); vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()
    const tasks = useTasksStore()
    const list = vi.spyOn(tasks, 'list').mockImplementation(async () => { tasks.readErrors.list = ''; return [] })
    const restore = vi.spyOn(tasks, 'restore').mockResolvedValue(null)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/cases', name: 'cases', component: CasesView }] })
    await router.push('/cases'); await router.isReady()
    const wrapper = mount(CasesView, { global: { plugins: [router], stubs: { ContextBar: true, EndpointDetail: true, DebugDrawer: true } } })
    await flushPromises(); list.mockClear(); restore.mockClear()
    tasks.readErrors.list = '列表 502'; tasks.readErrors.restore = '当前任务 503'; tasks.operationError = '任务保存失败'
    await flushPromises()
    await wrapper.get('[data-testid="retry-task-list"]').trigger('click'); await flushPromises()
    expect(list).toHaveBeenCalledOnce()
    expect(restore).not.toHaveBeenCalled()
    expect(wrapper.text()).not.toContain('列表 502')
    expect(wrapper.text()).toContain('当前任务 503')
    expect(wrapper.text()).toContain('任务保存失败')
  })

  it('retries project options without hiding an independent workspace failure', async () => {
    const context = useContextStore()
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    const loadOptions = vi.spyOn(context, 'loadOptions').mockImplementation(async () => { context.readErrors.options = '' })
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/cases', name: 'cases', component: CasesView }] })
    await router.push('/cases'); await router.isReady()
    const wrapper = mount(CasesView, { global: { plugins: [router], stubs: { ContextBar: true, EndpointDetail: true, DebugDrawer: true } } })
    await flushPromises(); loadOptions.mockClear()
    context.readErrors.workspace = '工作区 502'; context.readErrors.options = '项目选项 503'
    await flushPromises()
    await wrapper.get('[data-testid="retry-context-options"]').trigger('click'); await flushPromises()
    expect(loadOptions).toHaveBeenCalledOnce()
    expect(wrapper.text()).toContain('工作区 502')
    expect(wrapper.text()).not.toContain('项目选项 503')
  })

  it.each(['editor', 'saved-version'])('stops %s debugging when the confirmed production scope changes and changes back', async mode => {
    const context = useContextStore()
    Object.assign(context, { projectId: 'project-1', sourceRevisionId: 'source-1', environmentRevisionId: 'environment-1', environmentRevisions: [{ id: 'environment-1', environment_id: 'environment-1', project_id: 'project-1', name: '生产环境', revision: 1 }] })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue(); vi.spyOn(context, 'loadOptions').mockResolvedValue(); vi.spyOn(context, 'saveContext').mockResolvedValue()
    const assets = useAssetsStore(); assets.endpoints = [ENDPOINT]; assets.state = 'ready'; vi.spyOn(assets, 'load').mockResolvedValue()
    const cases = useCasesStore(); const version = savedCase('version-1', '收藏'); cases.registerVersion(version, false)
    vi.spyOn(cases, 'loadSavedCases').mockResolvedValue(); vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()
    let finish!: (version: CaseVersion) => void
    const prepare = vi.spyOn(cases, 'saveForDebug').mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const debug = vi.spyOn(cases, 'debug').mockResolvedValue()
    const tasks = useTasksStore(); vi.spyOn(tasks, 'list').mockResolvedValue([]); vi.spyOn(tasks, 'restore').mockResolvedValue(null)
    const saveTask = vi.spyOn(tasks, 'saveSelection').mockResolvedValue({ id: 'task-1' } as never)
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/cases', name: 'cases', component: CasesView }] })
    await router.push('/cases'); await router.isReady()
    const wrapper = mount(CasesView, { global: { plugins: [router], stubs: {
      ContextBar: true, EndpointDetail: true, DebugDrawer: true,
      CaseEditor: { emits: ['debug'], template: `<button data-testid="editor-debug" @click="$emit('debug')">debug</button>` },
    } } })
    await flushPromises()
    if (mode === 'editor') {
      wrapper.findComponent(CaseListPanel).vm.$emit('edit-version', version)
      await flushPromises()
      await wrapper.get('[data-testid="editor-debug"]').trigger('click')
    } else wrapper.findComponent(CaseListPanel).vm.$emit('run-version', version)
    expect(confirm).toHaveBeenCalledWith(expect.stringMatching(/生产环境.*真实发送/))
    if (mode === 'editor') await wrapper.get('[data-testid="editor-debug"]').trigger('click')
    else wrapper.findComponent(CaseListPanel).vm.$emit('run-version', version)
    expect(prepare).toHaveBeenCalledTimes(1)
    expect(confirm).toHaveBeenCalledTimes(1)
    context.selectEnvironmentRevision('environment-2'); context.selectEnvironmentRevision('environment-1')
    finish(version); await flushPromises()
    expect(saveTask).not.toHaveBeenCalled()
    expect(debug).not.toHaveBeenCalled()
    expect(wrapper.text()).toMatch(/草稿已保存.*调试未开始.*范围.*变化/)
    wrapper.unmount()
  })

  it.each(['workspace', 'task', 'workspace-task-switch'])('does not debug when production scope changes during %s save', async phase => {
    const context = useContextStore()
    Object.assign(context, { projectId: 'project-1', sourceRevisionId: 'source-1', environmentRevisionId: 'environment-1', environmentRevisions: [{ id: 'environment-1', environment_id: 'environment-1', project_id: 'project-1', name: '生产环境', revision: 1 }] })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue(); vi.spyOn(context, 'loadOptions').mockResolvedValue()
    let finishWorkspace!: () => void
    vi.spyOn(context, 'saveContext').mockImplementation(() => phase !== 'task' ? new Promise(resolve => { finishWorkspace = resolve }) : Promise.resolve())
    const assets = useAssetsStore(); assets.endpoints = [ENDPOINT]; assets.state = 'ready'; vi.spyOn(assets, 'load').mockResolvedValue()
    const cases = useCasesStore(); const version = savedCase('version-1', '收藏'); cases.registerVersion(version, false)
    vi.spyOn(cases, 'loadSavedCases').mockResolvedValue(); vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()
    const prepare = vi.spyOn(cases, 'saveForDebug').mockResolvedValue(version)
    const debug = vi.spyOn(cases, 'debug').mockResolvedValue()
    const tasks = useTasksStore(); vi.spyOn(tasks, 'list').mockResolvedValue([]); vi.spyOn(tasks, 'restore').mockResolvedValue(null)
    let finishTask!: (task: never) => void
    const saveTask = vi.spyOn(tasks, 'saveSelection').mockImplementation(() => phase === 'task' ? new Promise(resolve => { finishTask = resolve }) : Promise.resolve({ id: 'task-1' } as never))
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true)
    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/cases', name: 'cases', component: CasesView }] })
    await router.push('/cases'); await router.isReady()
    const wrapper = mount(CasesView, { global: { plugins: [router], stubs: { ContextBar: true, EndpointDetail: true, DebugDrawer: true } } })
    await flushPromises()
    wrapper.findComponent(CaseListPanel).vm.$emit('run-version', version)
    await flushPromises()
    wrapper.findComponent(CaseListPanel).vm.$emit('run-version', version)
    expect(prepare).toHaveBeenCalledTimes(1)
    expect(confirm).toHaveBeenCalledTimes(1)
    if (phase === 'workspace-task-switch') {
      tasks.task = { id: 'task-b', project_id: 'project-1', source_revision_id: 'source-1', environment_revision_id: 'environment-1', selected_endpoint_ids: [], name: 'B' } as never
      tasks.task = null
    } else { context.selectEnvironmentRevision('environment-2'); context.selectEnvironmentRevision('environment-1') }
    if (phase !== 'task') finishWorkspace()
    else finishTask({ id: 'task-1' } as never)
    await flushPromises()
    if (phase !== 'task') expect(saveTask).not.toHaveBeenCalled()
    expect(debug).not.toHaveBeenCalled()
    expect(wrapper.text()).toMatch(/草稿已保存.*调试未开始/)
    wrapper.unmount()
  })

  it('shows case management as an independent page and edits a saved case', async () => {
    const context = useContextStore()
    Object.assign(context, {
      projectId: 'project-1',
      sourceRevisionId: 'source-1',
      environmentRevisionId: 'environment-1',
      projects: [{ id: 'project-1', name: '3D 家用' }],
      sourceRevisions: [{ id: 'source-1', source_id: 'source-1', project_id: 'project-1', name: '默认模块', revision_number: 2, endpoint_count: 1 }],
      environmentRevisions: [{ id: 'environment-1', environment_id: 'environment-1', project_id: 'project-1', name: '生产环境', revision: 1 }],
    })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()

    const assets = useAssetsStore()
    assets.endpoints = [ENDPOINT]
    assets.state = 'ready'
    const loadAssets = vi.spyOn(assets, 'load').mockResolvedValue()

    const cases = useCasesStore()
    const version = savedCase('version-1', '我的收藏列表 - 基础正向流程')
    cases.registerVersion(version, false)
    cases.aiJob = {
      id: 'job-1', state: 'completed', endpoint_ids: [ENDPOINT.id], requested_model: 'qwen', actual_model: 'qwen', fallback_used: false, summary: {},
      batches: [{ id: 'batch-1', sequence: 1, state: 'completed', endpoint_ids: [ENDPOINT.id], requested_model: 'qwen', actual_model: 'qwen', fallback_used: false, fallback_reason: '', generated_draft_ids: [version.id], validation_errors: [] }],
    }
    const loadCases = vi.spyOn(cases, 'loadSavedCases').mockResolvedValue()
    vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()
    const moveCases = vi.spyOn(cases, 'updateVersionGroups').mockResolvedValue([])
    const saveForDebug = vi.spyOn(cases, 'saveForDebug').mockResolvedValue(version)
    const debug = vi.spyOn(cases, 'debug').mockResolvedValue({} as never)

    const tasks = useTasksStore()
    vi.spyOn(tasks, 'list').mockResolvedValue([])
    vi.spyOn(tasks, 'restore').mockResolvedValue(null)
    vi.spyOn(context, 'saveContext').mockResolvedValue()
    const saveSelection = vi.spyOn(tasks, 'saveSelection').mockResolvedValue({
      id: 'task-1', project_id: 'project-1', source_revision_id: 'source-1', environment_revision_id: 'environment-1',
      name: '3D 家用接口测试', state: 'ready', selected_endpoint_ids: ['endpoint-1'], runnable_baseline_count: 1,
      latest_ai_job_id: null, latest_execution_id: null, summary: {}, created_at: '', updated_at: '',
    })

    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/cases', name: 'cases', component: CasesView }] })
    await router.push('/cases')
    await router.isReady()
    const wrapper = mount(CasesView, {
      global: {
        plugins: [router],
        stubs: {
          ContextBar: true,
          EndpointDetail: true,
          DebugDrawer: true,
        },
      },
    })
    await flushPromises()

    expect(wrapper.get('[data-testid="cases-page"]').text()).toContain('用例管理')
    expect(wrapper.text()).not.toContain('接口测试工作台')
    expect(loadAssets).toHaveBeenCalledWith('source-1')
    expect(loadCases).toHaveBeenCalledWith('source-1')
    expect(wrapper.get('[data-testid="case-generation-status"]').text()).toContain('生成完成')
    expect(wrapper.get('[data-testid="case-generation-status"]').text()).toContain('1 条用例')
    expect(wrapper.get('[data-testid="case-generation-status"]').text()).toContain('结果已保存，可在用例列表继续调试')
    await wrapper.get('[data-testid="case-generation-results"]').trigger('click')
    expect((wrapper.get('[data-testid="case-name"]').element as HTMLInputElement).value).toBe('我的收藏列表 - 基础正向流程')

    expect(wrapper.find('[data-testid="open-case-endpoint-picker"]').exists()).toBe(true)
    await wrapper.get('[data-testid="open-case-endpoint-picker"]').trigger('click')
    await wrapper.get('[data-testid="case-endpoint-search"]').setValue('收藏')
    await wrapper.get('[data-testid="case-endpoint-endpoint-1"]').trigger('click')
    await wrapper.get('[data-testid="case-endpoint-create-manual"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="case-endpoint-search"]').exists()).toBe(false)
    expect((wrapper.get('[data-testid="case-name"]').element as HTMLInputElement).value).toBe('我的收藏列表')
    expect((wrapper.get('[data-testid="case-application"]').element as HTMLSelectElement).value).toBe('com.kfb.model')
    expect(wrapper.get('[data-testid="case-business-home"]').classes()).toContain('active')
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    wrapper.findComponent(CaseListPanel).vm.$emit('run-version', {
      ...version, id: 'missing-version', endpoint_id: 'missing-endpoint',
    })
    await flushPromises()
    expect(wrapper.text()).toContain('该用例对应的接口不在当前接口版本中')
    expect(confirm).not.toHaveBeenCalled()
    expect(saveForDebug).not.toHaveBeenCalled()
    expect(saveSelection).not.toHaveBeenCalled()
    expect(debug).not.toHaveBeenCalled()
    await wrapper.get('[data-testid="save-and-debug"]').trigger('click')
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('创建并保存新用例'))
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('更新当前任务范围'))

    await wrapper.get('[data-testid="case-version-edit-version-1"]').trigger('click')
    await flushPromises()

    expect((wrapper.get('[data-testid="case-name"]').element as HTMLInputElement).value).toBe('我的收藏列表 - 基础正向流程')
    expect(wrapper.get('[data-testid="case-management-shell"]').classes()).toContain('mobile-detail-open')
    expect(wrapper.get('[data-testid="case-editing-context"]').text()).toContain('基础正向流程 · v1')

    await wrapper.get('[data-testid="case-name"]').setValue('未保存的用例名称')
    await wrapper.get('[data-testid="case-version-run-version-1"]').trigger('click')
    expect(confirm).toHaveBeenCalledWith(expect.stringContaining('保存用例新版本并更新当前任务范围'))
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('未保存的用例名称'))
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('包含未保存修改'))
    cases.registerVersion(savedCase('version-2', '同接口另一用例'), false)
    await flushPromises()
    await wrapper.get('[data-testid="case-version-run-version-2"]').trigger('click')
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('未保存编辑'))
    expect((wrapper.get('[data-testid="case-name"]').element as HTMLInputElement).value).toBe('未保存的用例名称')
    const secondEndpoint = { ...ENDPOINT, id: 'endpoint-2', path: '/collection/other', summary: '另一接口' }
    assets.endpoints.push(secondEndpoint)
    cases.registerVersion({ ...savedCase('version-b', '接口 B 用例'), endpoint_id: secondEndpoint.id }, false)
    await flushPromises()
    await wrapper.get('[data-testid="case-version-edit-version-b"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-testid="case-name"]').setValue('接口 B 的未保存编辑')
    await wrapper.get('[data-testid="case-version-run-version-2"]').trigger('click')
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('替换该接口的未保存编辑'))
    expect(cases.drafts[ENDPOINT.id].name).toBe('未保存的用例名称')
    await wrapper.get('[data-testid="case-version-run-version-1"]').trigger('click')
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('未保存的用例名称'))
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('包含未保存修改'))
    let preparedName = ''
    saveForDebug.mockImplementationOnce(async endpointId => {
      preparedName = cases.drafts[endpointId].name
      return version
    })
    confirm.mockReturnValue(true)
    await wrapper.get('[data-testid="case-version-run-version-1"]').trigger('click')
    await flushPromises()
    expect(preparedName).toBe('未保存的用例名称')
    expect(debug).toHaveBeenCalledWith(expect.objectContaining({ caseVersionId: version.id }))
    expect(cases.drafts[secondEndpoint.id].name).toBe('接口 B 的未保存编辑')
    saveForDebug.mockClear()
    debug.mockClear()
    confirm.mockReturnValue(false)
    await wrapper.get('[data-testid="case-version-edit-version-1"]').trigger('click')
    await flushPromises()
    expect((wrapper.get('[data-testid="case-name"]').element as HTMLInputElement).value).toBe('未保存的用例名称')
    await wrapper.get('[data-testid="case-name"]').setValue('我的收藏列表 - 基础正向流程')
    await wrapper.get('[data-testid="save-and-debug"]').trigger('click')
    await flushPromises()
    expect(confirm).toHaveBeenLastCalledWith(expect.stringContaining('保存用例新版本并更新当前任务范围'))
    expect(saveForDebug).not.toHaveBeenCalled()
    expect(debug).not.toHaveBeenCalled()

    confirm.mockReturnValue(true)
    await wrapper.get('[data-testid="save-and-debug"]').trigger('click')
    await flushPromises()
    expect(saveForDebug).toHaveBeenCalledWith('endpoint-1', 'environment-1')
    expect(debug).toHaveBeenCalledWith(expect.objectContaining({ caseVersionId: 'version-1', taskId: 'task-1' }))
    expect(wrapper.find('.debug-command').exists()).toBe(false)

    await wrapper.get('[data-testid="management-back-to-list"]').trigger('click')
    expect(wrapper.get('[data-testid="case-management-shell"]').classes()).not.toContain('mobile-detail-open')

    wrapper.findComponent(CaseListPanel).vm.$emit('update-version-groups', ['version-1'], '发版回归')
    await flushPromises()
    expect(moveCases).toHaveBeenCalledWith(['version-1'], '发版回归')
  })

  it('deletes saved cases from the independent page after confirmation', async () => {
    const context = useContextStore()
    Object.assign(context, {
      projectId: 'project-1',
      sourceRevisionId: 'source-1',
      environmentRevisionId: 'environment-1',
      projects: [{ id: 'project-1', name: '3D 家用' }],
      sourceRevisions: [{ id: 'source-1', source_id: 'source-1', project_id: 'project-1', name: '默认模块', revision_number: 2, endpoint_count: 1 }],
      environmentRevisions: [{ id: 'environment-1', environment_id: 'environment-1', project_id: 'project-1', name: '生产环境', revision: 1 }],
    })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()

    const assets = useAssetsStore()
    assets.endpoints = [ENDPOINT]
    assets.state = 'ready'
    vi.spyOn(assets, 'load').mockResolvedValue()

    const cases = useCasesStore()
    const version = savedCase('version-1', '我的收藏列表 - 基础正向流程')
    cases.registerVersion(version, false)
    vi.spyOn(cases, 'loadSavedCases').mockResolvedValue()
    vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()
    const archive = vi.spyOn(cases, 'archiveCase').mockImplementation(async () => {
      cases.versions = {}
      cases.versionIdsByEndpoint = {}
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    const tasks = useTasksStore()
    vi.spyOn(tasks, 'list').mockResolvedValue([])
    vi.spyOn(tasks, 'restore').mockResolvedValue(null)

    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/cases', name: 'cases', component: CasesView }] })
    await router.push('/cases')
    await router.isReady()
    const wrapper = mount(CasesView, {
      global: {
        plugins: [router],
        stubs: {
          ContextBar: true,
          EndpointDetail: true,
          CaseEditor: true,
          DebugDrawer: true,
        },
      },
    })
    await flushPromises()

    await wrapper.get('[data-testid="case-version-delete-version-1"]').trigger('click')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalledWith('删除用例“我的收藏列表 - 基础正向流程”？历史执行记录和已采纳基线证据会保留。')
    expect(archive).toHaveBeenCalledWith(ENDPOINT.id, 'version-1')
  })

  it('opens the exact source and environment passed by an upstream generation flow', async () => {
    const context = useContextStore()
    Object.assign(context, {
      projectId: 'project-1',
      sourceRevisionId: 'source-old',
      environmentRevisionId: 'environment-old',
      projects: [{ id: 'project-1', name: '3D 家用' }],
      sourceRevisions: [
        { id: 'source-old', source_id: 'source-a', project_id: 'project-1', name: '默认模块', revision_number: 1, endpoint_count: 1 },
        { id: 'source-current', source_id: 'source-a', project_id: 'project-1', name: '默认模块', revision_number: 2, endpoint_count: 2 },
      ],
      environmentRevisions: [
        { id: 'environment-old', environment_id: 'environment-a', project_id: 'project-1', name: '测试环境', revision: 1 },
        { id: 'environment-current', environment_id: 'environment-a', project_id: 'project-1', name: '测试环境', revision: 2 },
      ],
    })
    vi.spyOn(context, 'loadSavedContext').mockResolvedValue()
    vi.spyOn(context, 'loadOptions').mockResolvedValue()

    const assets = useAssetsStore()
    const loadAssets = vi.spyOn(assets, 'load').mockResolvedValue()
    const cases = useCasesStore()
    const loadCases = vi.spyOn(cases, 'loadSavedCases').mockResolvedValue()
    vi.spyOn(cases, 'restoreLatestAiJob').mockResolvedValue()
    const tasks = useTasksStore()
    vi.spyOn(tasks, 'list').mockResolvedValue([])
    vi.spyOn(tasks, 'restore').mockResolvedValue(null)

    const router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/cases', name: 'cases', component: CasesView }] })
    await router.push({
      name: 'cases',
      query: {
        projectId: 'project-1',
        sourceRevisionId: 'source-current',
        environmentRevisionId: 'environment-current',
      },
    })
    await router.isReady()
    mount(CasesView, {
      global: {
        plugins: [router],
        stubs: { ContextBar: true, EndpointDetail: true, CaseEditor: true, DebugDrawer: true },
      },
    })
    await flushPromises()

    expect(context.sourceRevisionId).toBe('source-current')
    expect(context.environmentRevisionId).toBe('environment-current')
    expect(loadAssets).toHaveBeenCalledWith('source-current')
    expect(loadCases).toHaveBeenCalledWith('source-current')
  })
})

function savedCase(id: string, name: string): CaseVersion {
  return {
    id,
    case_id: `case-${id}`,
    endpoint_id: ENDPOINT.id,
    status: 'draft',
    origin: 'imported',
    version: 1,
    group_name: '',
    validation_summary: {},
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
  }
}
