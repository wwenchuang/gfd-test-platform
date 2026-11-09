import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useContextStore } from './context'

const SAVED_WORKSPACE = {
  project_id: 'project-1',
  source_revision_id: 'source-revision-1',
  environment_revision_id: 'environment-revision-1',
}

const SERVER_WORKSPACE = {
  project_id: 'project-2',
  source_revision_id: 'source-revision-2',
  environment_revision_id: 'environment-revision-2',
}

describe('context store', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  it('keeps read failures for workspace and options independently until the corresponding read succeeds', async () => {
    const store = useContextStore()
    await store.loadSavedContext({ get: async () => { throw new Error('工作区 502') } })
    await store.loadOptions({ get: async () => { throw new Error('项目列表 503') } })
    expect(store.readErrors).toEqual({ workspace: '工作区 502', options: '项目列表 503' })
    await store.loadOptions({ get: async () => ({ data: { projects: [], source_revisions: [], environment_revisions: [] } }) } as never)
    expect(store.readErrors).toEqual({ workspace: '工作区 502', options: '' })
    await store.loadSavedContext({ get: async () => ({ data: { workspace: SAVED_WORKSPACE } }) })
    expect(store.readErrors).toEqual({ workspace: '', options: '' })
  })

  it('preserves unsaved selection when retrying a workspace read', async () => {
    const store = useContextStore()
    store.restoreExecutionContext(SERVER_WORKSPACE)
    await store.loadSavedContext({ get: async () => ({ data: { workspace: SAVED_WORKSPACE } }) }, { preserveSelection: true })
    expect(store.projectId).toBe(SERVER_WORKSPACE.project_id)
    expect(store.sourceRevisionId).toBe(SERVER_WORKSPACE.source_revision_id)
    expect(store.environmentRevisionId).toBe(SERVER_WORKSPACE.environment_revision_id)
  })

  it('ignores an old workspace failure after a newer success', async () => {
    const store = useContextStore()
    let failOld!: (error: Error) => void
    const old = store.loadSavedContext({ get: () => new Promise((_, reject) => { failOld = reject }) })
    await store.loadSavedContext({ get: async () => ({ data: { workspace: SAVED_WORKSPACE } }) })
    failOld(new Error('过期 502')); await old
    expect(store.readErrors.workspace).toBe('')
    expect(store.error).toBe('')
    expect(store.projectId).toBe(SAVED_WORKSPACE.project_id)
  })

  it('records which operation failed even when the message text is the same', async () => {
    const store = useContextStore()
    await store.loadSavedContext({ get: async () => { throw new Error('服务 502') } })
    expect(store.errorSource).toBe('workspace')
    await store.loadOptions({ get: async () => { throw new Error('服务 502') } })
    expect(store.errorSource).toBe('options')
    await store.saveContext({ put: async () => { throw new Error('服务 502') } })
    expect(store.errorSource).toBe('save')
  })

  it('marks a preserved selection saved only when the server confirms the same scope', async () => {
    const store = useContextStore()
    store.restoreExecutionContext(SAVED_WORKSPACE)
    expect(store.isSaved).toBe(false)
    await store.loadSavedContext({ get: async () => ({ data: { workspace: SAVED_WORKSPACE } }) }, { preserveSelection: true })
    expect(store.isSaved).toBe(true)
    store.restoreExecutionContext(SERVER_WORKSPACE)
    await store.loadSavedContext({ get: async () => ({ data: { workspace: SAVED_WORKSPACE } }) }, { preserveSelection: true })
    expect(store.projectId).toBe(SERVER_WORKSPACE.project_id)
    expect(store.isSaved).toBe(false)
  })

  it('does not let an older workspace retry replace the result of a concurrent save', async () => {
    const store = useContextStore()
    store.restoreExecutionContext(SERVER_WORKSPACE)
    let resolve!: (value: { data: { workspace: typeof SAVED_WORKSPACE } }) => void
    const retry = store.loadSavedContext({ get: () => new Promise(done => { resolve = done }) }, { preserveSelection: true })
    await store.saveContext({ put: async () => ({ data: { workspace: SERVER_WORKSPACE } }) })
    expect(store.isSaved).toBe(true)
    resolve({ data: { workspace: SAVED_WORKSPACE } }); await retry
    expect(store.projectId).toBe(SERVER_WORKSPACE.project_id)
    expect(store.isSaved).toBe(true)
  })

  it('never overwrites a selection changed while a workspace retry is pending', async () => {
    const store = useContextStore()
    let resolve!: (value: { data: { workspace: typeof SAVED_WORKSPACE } }) => void
    const retry = store.loadSavedContext({ get: () => new Promise(done => { resolve = done }) }, { preserveSelection: true })
    store.restoreExecutionContext(SERVER_WORKSPACE)
    resolve({ data: { workspace: SAVED_WORKSPACE } }); await retry
    expect(store.projectId).toBe(SERVER_WORKSPACE.project_id)
    expect(store.readErrors.workspace).toBe('')
  })

  it('does not replace a changed-then-cleared selection with a late workspace response', async () => {
    const store = useContextStore()
    let resolve!: (value: { data: { workspace: typeof SAVED_WORKSPACE } }) => void
    const retry = store.loadSavedContext({ get: () => new Promise(done => { resolve = done }) }, { preserveSelection: true })
    store.selectProject('different')
    store.selectProject(null)
    resolve({ data: { workspace: SAVED_WORKSPACE } }); await retry
    expect(store.projectId).toBeNull()
  })

  it('keeps equal error messages for independent workspace and options requests', async () => {
    const store = useContextStore()
    await Promise.all([
      store.loadSavedContext({ get: async () => { throw new Error('服务 502') } }),
      store.loadOptions({ get: async () => { throw new Error('服务 502') } }),
    ])
    await store.loadOptions({ get: async () => ({ data: { projects: [], source_revisions: [], environment_revisions: [] } }) } as never)
    expect(store.readErrors).toEqual({ workspace: '服务 502', options: '' })
  })

  it('keeps changed selections when an older workspace save completes', async () => {
    const store = useContextStore()
    store.applyWorkspace(SAVED_WORKSPACE)
    let resolve!: (value: { data: { workspace: typeof SAVED_WORKSPACE } }) => void
    const save = store.saveContext({ put: () => new Promise(done => { resolve = done }) })
    store.restoreExecutionContext(SERVER_WORKSPACE)
    resolve({ data: { workspace: SAVED_WORKSPACE } }); await save
    expect(store.projectId).toBe(SERVER_WORKSPACE.project_id)
    expect(store.isSaved).toBe(false)
  })

  it('keeps a save error separate from later successful reads', async () => {
    const store = useContextStore()
    await store.saveContext({ put: async () => { throw new Error('保存失败') } })
    await store.loadOptions({ get: async () => ({ data: { projects: [], source_revisions: [], environment_revisions: [] } }) } as never)
    expect(store.saveError).toBe('保存失败')
    await store.saveContext({ put: async () => ({ data: { workspace: SAVED_WORKSPACE } }) })
    expect(store.saveError).toBe('')
  })

  it('loads the saved server context without starting a source refresh', async () => {
    const calls: string[] = []
    const api = {
      get: async (path: string) => {
        calls.push(path)
        return { data: { workspace: SAVED_WORKSPACE } }
      },
    }

    const store = useContextStore()
    await store.loadSavedContext(api)

    expect(store.projectId).toBe(SAVED_WORKSPACE.project_id)
    expect(store.sourceRevisionId).toBe(SAVED_WORKSPACE.source_revision_id)
    expect(store.environmentRevisionId).toBe(SAVED_WORKSPACE.environment_revision_id)
    expect(calls).toEqual(['/api/api-testing/v1/workspace'])
  })

  it('clears the context for a new user without a saved workspace', async () => {
    const store = useContextStore()
    store.applyWorkspace(SAVED_WORKSPACE)

    await store.loadSavedContext({
      get: async () => ({ data: { workspace: null } }),
    })

    expect(store.projectId).toBeNull()
    expect(store.sourceRevisionId).toBeNull()
    expect(store.environmentRevisionId).toBeNull()
    expect(store.error).toBe('')
  })

  it('exposes a workspace load failure without leaving the store loading', async () => {
    const store = useContextStore()

    await store.loadSavedContext({
      get: async () => { throw new Error('服务不可用') },
    })

    expect(store.error).toBe('服务不可用')
    expect(store.loading).toBe(false)
  })

  it('saves the three workspace IDs and applies the server response', async () => {
    const calls: Array<{ path: string; body: unknown }> = []
    const store = useContextStore()
    store.applyWorkspace(SAVED_WORKSPACE)

    await store.saveContext({
      put: async (path, body) => {
        calls.push({ path, body })
        return { data: { workspace: SERVER_WORKSPACE } }
      },
    })

    expect(calls).toEqual([{
      path: '/api/api-testing/v1/workspace',
      body: SAVED_WORKSPACE,
    }])
    expect(store.projectId).toBe(SERVER_WORKSPACE.project_id)
    expect(store.sourceRevisionId).toBe(SERVER_WORKSPACE.source_revision_id)
    expect(store.environmentRevisionId).toBe(SERVER_WORKSPACE.environment_revision_id)
    expect(store.error).toBe('')
    expect(store.isSaved).toBe(true)
  })

  it('marks the workspace dirty as soon as a saved selection changes', () => {
    const store = useContextStore()
    store.applyWorkspace(SAVED_WORKSPACE)

    store.selectEnvironmentRevision('environment-revision-2')

    expect(store.isSaved).toBe(false)
  })

  it('reports an invalid save response instead of accepting an empty workspace', async () => {
    const store = useContextStore()

    await store.saveContext({
      put: async () => ({ data: { workspace: null } }),
    })

    expect(store.error).toBe('工作区保存响应无效')
  })

  it('temporarily restores the exact context captured by a historical execution', () => {
    const store = useContextStore()
    store.applyWorkspace(SAVED_WORKSPACE)

    store.restoreExecutionContext(SERVER_WORKSPACE)

    expect(store.projectId).toBe(SERVER_WORKSPACE.project_id)
    expect(store.sourceRevisionId).toBe(SERVER_WORKSPACE.source_revision_id)
    expect(store.environmentRevisionId).toBe(SERVER_WORKSPACE.environment_revision_id)
    expect(store.isSaved).toBe(false)
  })

  it('loads only environment variable names and clears them when the environment changes', async () => {
    const store = useContextStore()
    store.selectEnvironmentRevision('environment-revision-1')

    await store.loadEnvironmentVariableNames('environment-revision-1', {
      get: async () => ({ data: { environment_revision: {
        revision_id: 'environment-revision-1',
        variables: { Biz: 'ZXB', Authorization: 'secret-token' },
        services: {},
      } } }),
    })

    expect(store.environmentVariableNames).toEqual(['Authorization', 'Biz'])
    expect(JSON.stringify(store.$state)).not.toContain('secret-token')
    store.selectEnvironmentRevision('environment-revision-2')
    expect(store.environmentVariableNames).toEqual([])
  })
})
