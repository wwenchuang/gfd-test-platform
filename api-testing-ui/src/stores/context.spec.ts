import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

import { useContextStore } from './context'

const SAVED_WORKSPACE = {
  project_id: 'project-1',
  source_revision_id: 'source-revision-1',
  environment_revision_id: 'environment-revision-1',
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
})
