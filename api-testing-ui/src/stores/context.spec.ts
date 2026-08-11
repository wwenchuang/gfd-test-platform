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
})
