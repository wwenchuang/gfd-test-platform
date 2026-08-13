import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { ApiTestTask } from '../api/contracts'
import { apiClient } from '../api/client'
import { useTasksStore } from './tasks'

const TASK: ApiTestTask = {
  id: 'task-1',
  project_id: 'project-1',
  source_revision_id: 'source-1',
  environment_revision_id: 'environment-1',
  name: '我的收藏接口回归',
  state: 'draft',
  selected_endpoint_ids: ['endpoint-1', 'endpoint-2'],
  runnable_baseline_count: 0,
  latest_ai_job_id: null,
  latest_execution_id: null,
  summary: {},
  created_at: '2026-08-10T00:00:00Z',
  updated_at: '2026-08-10T00:00:00Z',
}

describe('tasks store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.restoreAllMocks()
  })

  it('restores the active task and its saved endpoint selection', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { task: TASK } })
    const store = useTasksStore()

    await store.restore('project-1')

    expect(get).toHaveBeenCalledWith('/api/api-testing/v1/tasks/active?project_id=project-1')
    expect(store.task?.selected_endpoint_ids).toEqual(['endpoint-1', 'endpoint-2'])
  })

  it('lists saved tasks so users can reopen an earlier task', async () => {
    const get = vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { tasks: [TASK] } })
    const store = useTasksStore()

    await store.list('project-1')
    store.select(TASK.id)

    expect(get).toHaveBeenCalledWith('/api/api-testing/v1/tasks?project_id=project-1')
    expect(store.tasks).toHaveLength(1)
    expect(store.task?.name).toBe('我的收藏接口回归')
  })

  it('explicitly saves the current context and selection', async () => {
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { task: TASK } })
    const store = useTasksStore()

    await store.saveSelection({
      projectId: 'project-1',
      sourceRevisionId: 'source-1',
      environmentRevisionId: 'environment-1',
    }, ['endpoint-1', 'endpoint-2'], '我的收藏接口回归')

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/tasks', {
      project_id: 'project-1',
      source_revision_id: 'source-1',
      environment_revision_id: 'environment-1',
      name: '我的收藏接口回归',
      selected_endpoint_ids: ['endpoint-1', 'endpoint-2'],
    })
  })

  it('updates the selected task instead of creating a new one', async () => {
    const updated = { ...TASK, name: '收藏回归 V2' }
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue({ data: { task: updated } })
    const store = useTasksStore()
    store.task = TASK
    store.tasks = [TASK]

    await store.saveSelection({
      projectId: 'project-1',
      sourceRevisionId: 'source-1',
      environmentRevisionId: 'environment-1',
    }, ['endpoint-1'], '收藏回归 V2')

    expect(put).toHaveBeenCalledWith('/api/api-testing/v1/tasks/task-1', {
      project_id: 'project-1',
      source_revision_id: 'source-1',
      environment_revision_id: 'environment-1',
      name: '收藏回归 V2',
      selected_endpoint_ids: ['endpoint-1'],
    })
    expect(store.task?.name).toBe('收藏回归 V2')
    expect(store.tasks[0].name).toBe('收藏回归 V2')
  })

  it('updates the selected task when only the runtime environment changes', async () => {
    const updated = { ...TASK, environment_revision_id: 'environment-2', name: '收藏回归 V2' }
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue({ data: { task: updated } })
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { task: { ...updated, id: 'task-new' } } })
    const store = useTasksStore()
    store.task = TASK
    store.tasks = [TASK]

    await store.saveSelection({
      projectId: 'project-1',
      sourceRevisionId: 'source-1',
      environmentRevisionId: 'environment-2',
    }, ['endpoint-1'], '收藏回归 V2')

    expect(put).toHaveBeenCalledWith('/api/api-testing/v1/tasks/task-1', {
      project_id: 'project-1',
      source_revision_id: 'source-1',
      environment_revision_id: 'environment-2',
      name: '收藏回归 V2',
      selected_endpoint_ids: ['endpoint-1'],
    })
    expect(post).not.toHaveBeenCalled()
    expect(store.task?.environment_revision_id).toBe('environment-2')
  })

  it('renames the current task without changing its saved scope', async () => {
    const renamed = { ...TASK, name: '发版收藏基线' }
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue({ data: { task: renamed } })
    const store = useTasksStore()
    store.task = TASK
    store.tasks = [TASK]

    await store.rename(TASK.id, '发版收藏基线')

    expect(put).toHaveBeenCalledWith('/api/api-testing/v1/tasks/task-1/name', {
      name: '发版收藏基线',
    })
    expect(store.task?.name).toBe('发版收藏基线')
  })

  it('runs only the saved task and keeps its canonical execution', async () => {
    const execution = { id: 'execution-1', state: 'QUEUED' }
    const runningTask = { ...TASK, state: 'running', latest_execution_id: 'execution-1' }
    vi.spyOn(apiClient, 'get').mockResolvedValue({ data: { task: TASK } })
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({
      data: { task: runningTask, execution },
    })
    const store = useTasksStore()
    await store.restore('project-1')

    const started = await store.runCurrent()

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/tasks/task-1/run', {
      idempotency_key: expect.any(String),
    })
    expect(started.id).toBe('execution-1')
    expect(store.task?.state).toBe('running')
  })

  it('runs a saved task with the selected runtime environment', async () => {
    const execution = { id: 'execution-2', state: 'QUEUED', environment_revision_id: 'environment-2' }
    const runningTask = { ...TASK, state: 'running', latest_execution_id: 'execution-2' }
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({
      data: { task: runningTask, execution },
    })
    const store = useTasksStore()
    store.task = TASK
    store.tasks = [TASK]

    const started = await store.runCurrent('environment-2')

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/tasks/task-1/run', {
      idempotency_key: expect.any(String),
      environment_revision_id: 'environment-2',
    })
    expect(started.id).toBe('execution-2')
  })
})
