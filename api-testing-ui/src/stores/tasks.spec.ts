import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import type { ApiTestTask } from '../api/contracts'
import { apiClient, type ApiClient } from '../api/client'
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

  it('does not replace a newly selected task with an old save response', async () => {
    const store = useTasksStore(); store.task = TASK
    let current = true; let finish!: (value: unknown) => void
    vi.spyOn(apiClient, 'put').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
    const pending = store.saveSelection({ projectId: 'project-1', sourceRevisionId: 'source-1', environmentRevisionId: 'environment-1' }, ['endpoint-1'], 'A', { isCurrent: () => current })
    const newer = { ...TASK, id: 'task-2', name: 'B' }; store.task = newer; current = false
    finish({ data: { task: TASK } }); await expect(pending).rejects.toThrow('任务已保存')
    expect(store.task?.id).toBe('task-2')
    expect(store.tasks.some(item => item.id === TASK.id)).toBe(false)
  })

  it('does not replace a newly selected task with an old execution response', async () => {
    const store = useTasksStore(); store.task = TASK
    let current = true; let finish!: (value: unknown) => void
    vi.spyOn(apiClient, 'post').mockImplementationOnce(() => new Promise(resolve => { finish = resolve }) as never)
    const pending = store.runCurrent('environment-1', { isCurrent: () => current })
    const newer = { ...TASK, id: 'task-2', name: 'B' }; store.task = newer; current = false
    finish({ data: { task: TASK, execution: { id: 'execution-1' } } }); await expect(pending).rejects.toThrow('执行请求已提交')
    expect(store.task?.id).toBe('task-2')
    expect(store.tasks.some(item => item.id === TASK.id)).toBe(false)
  })

  it.each(['save', 'run'])('keeps the %s busy state until the newest overlapping request settles', async operation => {
    const store = useTasksStore(); store.task = TASK
    let finishFirst!: (value: unknown) => void; let finishSecond!: (value: unknown) => void
    const request = operation === 'save' ? vi.spyOn(apiClient, 'put') : vi.spyOn(apiClient, 'post')
    request.mockImplementationOnce(() => new Promise(resolve => { finishFirst = resolve }) as never)
      .mockImplementationOnce(() => new Promise(resolve => { finishSecond = resolve }) as never)
    const start = () => operation === 'save'
      ? store.saveSelection({ projectId: 'project-1', sourceRevisionId: 'source-1', environmentRevisionId: 'environment-1' }, ['endpoint-1'], 'A')
      : store.runCurrent('environment-1')
    const first = start(); const second = start()
    const payload = { data: { task: TASK, execution: { id: 'run-1' } } }
    finishFirst(payload); await expect(first).rejects.toThrow(operation === 'save' ? '任务已保存' : '执行请求已提交')
    expect(operation === 'save' ? store.saving : store.running).toBe(true)
    finishSecond(payload); await second
    expect(operation === 'save' ? store.saving : store.running).toBe(false)
  })

  it('keeps saving busy while a different task creation remains in flight', async () => {
    const store = useTasksStore(); store.task = TASK
    let finishSave!: (value: unknown) => void; let finishCreate!: (value: unknown) => void
    vi.spyOn(apiClient, 'put').mockImplementationOnce(() => new Promise(resolve => { finishSave = resolve }) as never)
    vi.spyOn(apiClient, 'post').mockImplementationOnce(() => new Promise(resolve => { finishCreate = resolve }) as never)
    const scope = { projectId: 'project-1', sourceRevisionId: 'source-1', environmentRevisionId: 'environment-1' }
    const saved = store.saveSelection(scope, ['endpoint-1'], 'A')
    const created = store.createSelection(scope, ['endpoint-2'], 'B')
    finishSave({ data: { task: TASK } }); await saved
    expect(store.saving).toBe(true)
    finishCreate({ data: { task: { ...TASK, id: 'task-b' } } }); await created
    expect(store.saving).toBe(false)
  })

  it('refuses to run when the current task no longer matches the confirmed task', async () => {
    const store = useTasksStore(); store.task = { ...TASK, id: 'task-b' }
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { task: TASK, execution: { id: 'unsafe' } } })
    await expect(store.runCurrent('environment-1', { expectedTaskId: 'task-a' })).rejects.toThrow('任务已变化')
    expect(post).not.toHaveBeenCalled()
  })

  it('keeps a failed task-list read until that same resource succeeds', async () => {
    const store = useTasksStore()
    const failure = { get: vi.fn().mockRejectedValue(new Error('任务列表 502')) } as unknown as Pick<ApiClient, 'get'>
    const restore = { get: vi.fn().mockResolvedValue({ data: { task: TASK } }) } as unknown as Pick<ApiClient, 'get'>
    await store.list('project-1', failure)
    await store.restore('project-1', restore)
    expect(store.readErrors.list).toBe('任务列表 502')
    expect(store.readErrors.restore).toBe('')
    await store.list('project-1', { get: vi.fn().mockResolvedValue({ data: { tasks: [TASK] } }) } as unknown as Pick<ApiClient, 'get'>)
    expect(store.readErrors.list).toBe('')
  })

  it('does not restore a task from the previous project after context changes', async () => {
    let finish!: (value: unknown) => void
    const get = vi.fn().mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const store = useTasksStore()
    const pending = store.restore('old-project', { get } as unknown as Pick<ApiClient, 'get'>)
    store.clear()
    finish({ data: { task: TASK } })
    await pending
    expect(store.task).toBeNull()
    expect(store.readErrors.restore).toBe('')
  })

  it('ends loading when a task selection supersedes an in-flight restore', async () => {
    let finish!: (value: unknown) => void
    const get = vi.fn().mockImplementation(() => new Promise(resolve => { finish = resolve }))
    const store = useTasksStore()
    store.tasks = [TASK]
    const pending = store.restore('project-1', { get } as unknown as Pick<ApiClient, 'get'>)
    store.select(TASK.id)
    finish({ data: { task: null } })
    await pending
    expect(store.task?.id).toBe(TASK.id)
    expect(store.loading).toBe(false)
  })

  it('stays loading while either independent read is in flight', async () => {
    let finishRestore!: (value: unknown) => void
    const client = { get: vi.fn().mockResolvedValueOnce({ data: { tasks: [] } }).mockImplementationOnce(() => new Promise(resolve => { finishRestore = resolve })) } as unknown as Pick<ApiClient, 'get'>
    const store = useTasksStore()
    const listing = store.list('project-1', client)
    const restoring = store.restore('project-1', client)
    await listing
    expect(store.loading).toBe(true)
    finishRestore({ data: { task: null } })
    await restoring
    expect(store.loading).toBe(false)
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

  it('creates an independent regression task instead of updating the current task', async () => {
    const regressionTask = { ...TASK, id: 'task-baseline', name: '3D 家用基线回归' }
    const post = vi.spyOn(apiClient, 'post').mockResolvedValue({ data: { task: regressionTask } })
    const put = vi.spyOn(apiClient, 'put').mockResolvedValue({ data: { task: { ...TASK, name: 'should-not-update' } } })
    const store = useTasksStore()
    store.task = TASK
    store.tasks = [TASK]

    await store.createSelection({
      projectId: 'project-1',
      sourceRevisionId: 'source-1',
      environmentRevisionId: 'environment-2',
    }, ['endpoint-1', 'endpoint-1'], '3D 家用基线回归')

    expect(post).toHaveBeenCalledWith('/api/api-testing/v1/tasks', {
      project_id: 'project-1',
      source_revision_id: 'source-1',
      environment_revision_id: 'environment-2',
      name: '3D 家用基线回归',
      selected_endpoint_ids: ['endpoint-1'],
    })
    expect(put).not.toHaveBeenCalled()
    expect(store.task?.id).toBe('task-baseline')
    expect(store.tasks.map(item => item.id)).toEqual(['task-baseline', 'task-1'])
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

  it('removes a saved task and clears it when it is currently selected', async () => {
    const remove = vi.spyOn(apiClient, 'delete').mockResolvedValue({ data: { task: TASK } })
    const store = useTasksStore()
    store.task = TASK
    store.tasks = [TASK, { ...TASK, id: 'task-2', name: '其它任务' }]

    await store.remove(TASK.id)

    expect(remove).toHaveBeenCalledWith('/api/api-testing/v1/tasks/task-1')
    expect(store.task).toBeNull()
    expect(store.tasks.map(item => item.id)).toEqual(['task-2'])
  })
})

describe('guarded task restore', () => {
  it('does not apply a delayed task response after the caller scope expires', async () => {
    setActivePinia(createPinia())
    const store = useTasksStore()
    let finish!: (response: unknown) => void
    const client = { get: vi.fn(() => new Promise(resolve => { finish = resolve })) }
    let current = true
    const restoring = store.restore('p', client as never, { isCurrent: () => current })
    current = false
    finish({ data: { task: { id: 'old' } } })
    await restoring
    expect(store.task).toBeNull()
  })
})
