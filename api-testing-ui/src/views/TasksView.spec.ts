// @vitest-environment jsdom

import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createMemoryHistory, createRouter } from 'vue-router'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { ApiEndpoint, ApiTestTask } from '../api/contracts'
import { useAssetsStore } from '../stores/assets'
import { useContextStore } from '../stores/context'
import { useTasksStore } from '../stores/tasks'
import TasksView from './TasksView.vue'

const ENDPOINT: ApiEndpoint = {
  id: 'endpoint-1',
  method: 'GET',
  path: '/pmc/api/v1/deviceCmd/status',
  summary: '获取设备状态',
  tags: ['本地测试', '启迪设备'],
}

const TASK: ApiTestTask = {
  id: 'task-1',
  project_id: 'project-1',
  source_revision_id: 'source-1',
  environment_revision_id: 'environment-1',
  name: '3D 家用基线回归',
  state: 'ready',
  selected_endpoint_ids: [ENDPOINT.id],
  runnable_baseline_count: 1,
  latest_ai_job_id: null,
  latest_execution_id: 'execution-latest',
  summary: { total: 1, passed: 1, failed: 0, broken: 0, skipped: 0, cancelled: 0 },
  created_at: '2026-08-25T08:00:00Z',
  updated_at: '2026-08-25T10:30:00Z',
}

describe('TasksView', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  it('shows task management as an independent page and renders selected task details', async () => {
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

    const tasks = useTasksStore()
    vi.spyOn(tasks, 'list').mockImplementation(async () => {
      tasks.tasks = [TASK]
      return tasks.tasks
    })
    vi.spyOn(tasks, 'restore').mockResolvedValue(null)

    const assets = useAssetsStore()
    const loadAssets = vi.spyOn(assets, 'load').mockImplementation(async () => {
      assets.endpoints = [ENDPOINT]
      assets.state = 'ready'
    })

    const router = createRouter({ history: createMemoryHistory(), routes: [
      { path: '/tasks', name: 'tasks', component: TasksView },
      { path: '/runs', name: 'runs', component: { template: '<div />' } },
    ] })
    await router.push('/tasks')
    await router.isReady()
    const wrapper = mount(TasksView, { global: { plugins: [router], stubs: { ContextBar: true } } })
    await flushPromises()

    expect(wrapper.get('[data-testid="tasks-page"]').text()).toContain('任务管理')
    expect(wrapper.text()).not.toContain('接口测试工作台')
    expect(loadAssets).toHaveBeenCalledWith('source-1')

    await wrapper.get('[data-testid="task-list-item-task-1"]').trigger('click')
    await flushPromises()

    expect(wrapper.get('[data-testid="selected-task-title"]').text()).toContain('3D 家用基线回归')
    expect(wrapper.get('[data-testid="selected-task-state"]').text()).toBe('可执行')
    expect(wrapper.text()).toContain('最近执行')
    expect(wrapper.text()).toContain('获取设备状态')
    expect(wrapper.get('[data-testid="task-management-shell"]').classes()).toContain('mobile-detail-open')

    await wrapper.get('[data-testid="task-latest-execution"]').trigger('click')
    await flushPromises()
    expect(router.currentRoute.value.query.executionId).toBe('execution-latest')

    await router.push('/tasks')
    await flushPromises()

    await wrapper.get('[data-testid="management-back-to-list"]').trigger('click')
    expect(wrapper.get('[data-testid="task-management-shell"]').classes()).not.toContain('mobile-detail-open')
  })

  it('runs and deletes tasks from the independent management page', async () => {
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

    const tasks = useTasksStore()
    vi.spyOn(tasks, 'list').mockImplementation(async () => {
      tasks.tasks = [TASK]
      return tasks.tasks
    })
    vi.spyOn(tasks, 'restore').mockResolvedValue(null)
    const run = vi.spyOn(tasks, 'runCurrent').mockResolvedValue({ id: 'execution-1' } as never)
    const remove = vi.spyOn(tasks, 'remove').mockImplementation(async taskId => {
      tasks.tasks = tasks.tasks.filter(item => item.id !== taskId)
      tasks.task = null
      return TASK
    })
    vi.spyOn(window, 'confirm').mockReturnValue(true)

    const assets = useAssetsStore()
    vi.spyOn(assets, 'load').mockImplementation(async () => {
      assets.endpoints = [ENDPOINT]
      assets.state = 'ready'
    })

    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/tasks', name: 'tasks', component: TasksView },
        { path: '/runs', name: 'runs', component: { template: '<div />' } },
      ],
    })
    await router.push('/tasks')
    await router.isReady()
    const wrapper = mount(TasksView, { global: { plugins: [router], stubs: { ContextBar: true } } })
    await flushPromises()

    await wrapper.get('[data-testid="task-list-item-task-1"]').trigger('click')
    await wrapper.get('[data-testid="task-detail-run"]').trigger('click')
    await flushPromises()

    expect(run).toHaveBeenCalledWith('environment-1')
    expect(router.currentRoute.value.name).toBe('runs')
    expect(router.currentRoute.value.query.executionId).toBe('execution-1')

    await router.push('/tasks')
    await flushPromises()
    await wrapper.get('[data-testid="task-list-item-task-1"]').trigger('click')
    await wrapper.get('[data-testid="task-detail-delete"]').trigger('click')
    await flushPromises()

    expect(window.confirm).toHaveBeenCalledWith('删除任务“3D 家用基线回归”？任务关联的用例、基线和历史执行记录会保留。')
    expect(remove).toHaveBeenCalledWith('task-1')
    expect(wrapper.get('[data-testid="task-management-shell"]').classes()).not.toContain('mobile-detail-open')
    expect(wrapper.text()).toContain('选择左侧任务')
  })
})
