import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { apiClient } from '../api/client'
import { useTasksStore } from './tasks'

const TASK = {
  id: 'task-1',
  project_id: 'project-1',
  source_revision_id: 'source-1',
  environment_revision_id: 'environment-1',
  name: '我的收藏接口回归',
  state: 'draft',
  selected_endpoint_ids: ['endpoint-1', 'endpoint-2'],
  latest_ai_job_id: null,
  latest_execution_id: null,
  summary: {},
  created_at: '2026-08-10T00:00:00Z',
  updated_at: '2026-08-10T00:00:00Z',
} as const

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
})
