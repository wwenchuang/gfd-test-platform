// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ApiEndpoint, CaseVersion } from '../api/contracts'
import CaseListPanel from '../components/CaseListPanel.vue'
import { useAssetsStore } from '../stores/assets'
import { useCasesStore } from '../stores/cases'
import { useContextStore } from '../stores/context'
import { useTasksStore } from '../stores/tasks'
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
    const loadCases = vi.spyOn(cases, 'loadSavedCases').mockResolvedValue()
    const moveCases = vi.spyOn(cases, 'updateVersionGroups').mockResolvedValue([])
    const saveForDebug = vi.spyOn(cases, 'saveForDebug').mockResolvedValue(version)
    const debug = vi.spyOn(cases, 'debug').mockResolvedValue({} as never)

    const tasks = useTasksStore()
    vi.spyOn(tasks, 'list').mockResolvedValue([])
    vi.spyOn(tasks, 'restore').mockResolvedValue(null)
    vi.spyOn(context, 'saveContext').mockResolvedValue()
    vi.spyOn(tasks, 'saveSelection').mockResolvedValue({
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

    await wrapper.get('[data-testid="case-version-edit-version-1"]').trigger('click')
    await flushPromises()

    expect((wrapper.get('[data-testid="case-name"]').element as HTMLInputElement).value).toBe('我的收藏列表 - 基础正向流程')
    expect(wrapper.get('[data-testid="case-management-shell"]').classes()).toContain('mobile-detail-open')

    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false)
    await wrapper.get('[data-testid="save-and-debug"]').trigger('click')
    await flushPromises()
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
    priority: 'P1',
    request: { method: 'POST', path: '/collection/page', service: 'default', path_params: {}, query: {}, headers: {}, cookies: {}, body: { pageNum: 1 } },
    data_rows: [],
    assertions: [],
    extractions: [],
    dependencies: [],
    processing: { pre: [], post: [] },
  }
}
